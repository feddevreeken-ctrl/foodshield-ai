#!/usr/bin/env python3
"""End-to-end checks for the El Niño panel.

Written after a long session of driving a live browser over CDP, where every
probe that slept through a map repaint timed out and told me nothing. Headless
Playwright with an explicit networkidle wait is deterministic and repeatable,
which is what a check like this has to be.

Uses the installed Chrome (channel="chrome") rather than downloading a browser.

    pip install playwright
    python tests/test_elnino_panel.py            # serves the repo itself
    python tests/test_elnino_panel.py --port 8801
"""
from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []
CHECKS = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
        FAILURES.append(label)


def serve(port: int) -> socketserver.TCPServer:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))

    class Quiet(socketserver.TCPServer):
        allow_reuse_address = True

    httpd = Quiet(("127.0.0.1", port), handler)
    httpd.RequestHandlerClass.log_message = lambda *a, **k: None  # type: ignore[assignment]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def open_panel(page, base: str, tab: str = "elnino"):
    page.goto(f"{base}/index.html?tab={tab}", wait_until="networkidle")
    page.wait_for_selector("#enso-hero .enso-hero-k, #enso-hero", state="attached")
    page.wait_for_function(
        "() => document.getElementById('enso-indices')"
        "        && document.getElementById('enso-indices').innerHTML.length > 0",
        timeout=25_000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8809)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    httpd = serve(args.port)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        # Serving the repo over a plain file server is not production: Vercel's
        # analytics shim does not exist here, and third-party APIs rate-limit a
        # loop of test loads. Those are environment noise. Anything else is not.
        IGNORE = ("_vercel/insights", "api.reliefweb.int")

        def note(text: str, where: str = "") -> None:
            # "Failed to load resource: ... 404" carries the URL in the message's
            # location, not its text, so both have to be checked.
            if not any(k in (text + " " + where) for k in IGNORE):
                errors.append(f"{text} [{where}]" if where else text)

        def on_console(m) -> None:
            if m.type != "error":
                return
            loc = m.location or {}
            note(m.text, loc.get("url", "") if isinstance(loc, dict) else "")

        page.on("pageerror", lambda e: note(str(e)))
        page.on("console", on_console)
        page.on("requestfailed",
                lambda r: note(f"request failed: {r.failure or ''}", r.url))

        print("\nindex strip — comparability")
        open_panel(page, base)
        page.eval_on_selector_all(".enso-idx", "els => els.forEach(e => e.open = true)")
        kinds = page.eval_on_selector_all(
            ".enso-idx-grp-h b", "els => els.map(e => e.textContent)")
        check("rows are grouped by averaging window", len(kinds) >= 3, str(kinds))
        # no bar may be scaled against a bar in a different group
        widths = page.evaluate("""() => [...document.querySelectorAll('.enso-idx-grp')].map(g =>
            [...g.querySelectorAll('.enso-idx-bar span')].map(s => parseFloat(s.style.width)))""")
        check("each window group has its own 100% bar",
              all((not w) or abs(max(w) - 100) < 0.6 for w in widths), str(widths))
        frees = page.eval_on_selector_all(
            ".enso-idx-cmp:not(.enso-idx-cmp-bad) .enso-idx-cmp-v", "els => els.map(e => e.textContent)")
        check("every published pair names exactly one free variable",
              bool(frees) and all(t.count(":") == 1 for t in frees), str(frees))
        check("the incomparable pair is shown as incomparable",
              page.locator(".enso-idx-cmp-bad").count() >= 1)

        print("\nscenario disclosure")
        tag = page.locator(".enso-tag-snap")
        check("modelled layer discloses the scenario it is painted at", tag.count() == 1)
        hero = page.locator("#enso-hero").inner_text()
        check("hero prints the agency band, not the snapped one",
              "Moderate" in hero and "+1.39" in hero, hero[:90])

        print("\nphase follows the selected scenario")
        page.select_option("#enso-country", "USA")
        page.wait_for_timeout(250)
        head_nino = page.eval_on_selector_all("#enso-detail th", "e => e.map(x => x.textContent)")
        order_nino = page.eval_on_selector_all("#enso-detail tbody tr td.nm", "e => e.map(x => x.textContent)")
        page.select_option("#enso-level", "-1.5")
        page.wait_for_timeout(250)
        head_nina = page.eval_on_selector_all("#enso-detail th", "e => e.map(x => x.textContent)")
        order_nina = page.eval_on_selector_all("#enso-detail tbody tr td.nm", "e => e.map(x => x.textContent)")
        check("'selected' marker starts on the El Niño column",
              any("selected" in h and "El" in h for h in head_nino), str(head_nino))
        check("'selected' marker moves to the La Niña column",
              any("selected" in h and "La" in h for h in head_nina), str(head_nina))
        check("ranking changes with the phase", order_nino != order_nina,
              f"{order_nino} vs {order_nina}")
        check("snap badge hidden once a scenario is chosen by hand",
              page.locator(".enso-tag-snap").count() == 0)

        print("\nnon-ENSO-specific pairs are out of the aggregate")
        page.select_option("#enso-level", "1.5")
        page.select_option("#enso-country", "IDN")
        page.wait_for_timeout(250)
        # inner_text() returns RENDERED text, and these labels are uppercased by
        # CSS text-transform — compare case-insensitively or the assertion tests
        # the stylesheet rather than the content.
        idn = page.locator("#enso-detail").inner_text().lower()
        check("Indonesia reports no ENSO coverage", "coverage" in idn and "0%" in idn, idn[:110])
        check("the excluded IOD-shared value is still shown", "shared with the iod" in idn)

        print("\nagency bulletins in the news view")
        open_panel(page, base, "ensolive")
        page.wait_for_selector(".enso-bul", timeout=20_000)
        ags = page.eval_on_selector_all(".enso-bul-ag", "e => e.map(x => x.textContent)")
        check("BoM weekly appears in news", any("BoM" in a for a in ags), str(ags))
        check("more than one agency is represented", len(set(ags)) >= 2, str(set(ags)))
        check("the frozen climate.gov feed is named and excluded",
              "climate gov" in page.locator(".enso-bul-skip").inner_text().lower())
        hrefs = page.eval_on_selector_all(
            ".enso-news-t", "e => e.map(x => x.getAttribute('href') || '')")
        check("no feed link escapes the http(s) allow-list",
              all(h.startswith("http") for h in hrefs) if hrefs else True, str(hrefs[:3]))

        print("\nlayout")
        edges = page.evaluate("""() => {
            const host = document.querySelector('.enso-panel') || document.body;
            const col = host.getBoundingClientRect().left;
            const sel = '.enso-idx-cmp-b,.enso-idx-cmp-h,.enso-idx-grp-h b,.enso-bul-k,.enso-bul-sub';
            return [...new Set([...document.querySelectorAll(sel)].map(n => {
              const r = n.getBoundingClientRect(), cs = getComputedStyle(n);
              return Math.round(r.left + parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth) - col);
            }))];
        }""")
        check("boxed text shares one left edge", len(edges) == 1, str(edges))
        check("no horizontal overflow", page.evaluate(
            "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"))

        check("no console errors", not errors, "; ".join(errors[:2]))
        browser.close()

    httpd.shutdown()
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} passed")
    if FAILURES:
        print("failed: " + ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
