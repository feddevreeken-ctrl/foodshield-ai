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


STRIP_PROBE = """() => {
    const svg = document.querySelector('.enso-strip');
    if (!svg) return null;
    return {bars: svg.querySelectorAll('rect').length,
            now: (document.querySelector('.enso-strip-now') || {}).textContent || null,
            hasLabel: !!svg.getAttribute('aria-label'),
            textInSvg: svg.querySelectorAll('text').length};
}"""

PANAMA_PROBE = """() => {
    const cv = document.getElementById('enso-c-panama');
    const ch = cv && window.Chart && Chart.getChart ? Chart.getChart(cv) : null;
    if (!ch) return null;
    const cap = document.querySelector('.enso-chart-cap');
    return {labels: ch.data.labels, cap: cap ? cap.textContent : ''};
}"""

NOW_PROBE = """() => {
    const svg = document.querySelector('.enso-strip');
    const plot = document.querySelector('.enso-strip-plot');
    const lab = document.querySelector('.enso-strip-now');
    if (!svg || !plot || !lab) return null;
    const line = svg.querySelector('.enso-live-point');
    if (!line) return null;
    const pr = plot.getBoundingClientRect();
    const gr = line.getBoundingClientRect();
    const pointY = gr.top + gr.height / 2;
    const lr = lab.getBoundingClientRect();
    const ys = [...svg.querySelectorAll('rect')].map(r => +r.getAttribute('y'));
    return {offset: Math.round((lr.top + lr.height / 2) - pointY),
            ruleY: Math.round(pointY - pr.top),
            topBarY: Math.min.apply(null, ys),
            label: lab.textContent};
}"""

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
    page.wait_for_selector("#enso-hero", state="attached")
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
        # The Quick Start modal and the first-visit hint sit over the nav on a fresh
        # profile and swallow real clicks; the site stores both opt-outs in localStorage.
        page.add_init_script(
            "try { localStorage.setItem('foodshield_skip_intro', '1');"
            " localStorage.setItem('foodshield_hint_shown', '1'); } catch (e) {}")
        errors: list[str] = []
        # Serving the repo over a plain file server is not production: Vercel's
        # analytics shim does not exist here, and third-party APIs rate-limit a
        # loop of test loads. Those are environment noise. Anything else is not.
        IGNORE = ("_vercel/insights", "api.reliefweb.int", "tiles.openfreemap.org",
                  "basemaps.cartocdn.com")

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
        switcher = page.locator('#enso-view-nav')
        in_nav = switcher.evaluate("el => !!el.closest('#nav')")
        visible_here = switcher.is_visible()
        page.evaluate("showTab('global')")
        hidden_elsewhere = not switcher.is_visible()
        page.evaluate("showTab('elnino')")
        check("El Nino view switcher belongs to nav and only shows on its host tab",
              in_nav and visible_here and hidden_elsewhere and switcher.is_visible())
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
        # Read expected values from the feed rather than hardcoding them: CPC
        # publishes a new season every month, and this assertion is about the hero
        # agreeing with enso.json, not about any particular number.
        feed = page.evaluate("async () => (await (await fetch('data/enso.json')).json()).data.latest")
        hero = page.locator("#enso-hero").inner_text()
        band = feed["band"].replace("El Nino", "El Niño").replace("La Nina", "La Niña")
        val = ("%+.2f" % feed["anom"]).replace("-", "−")
        check("hero prints the agency band, not the snapped one",
              band.lower() in hero.lower() and val.lower() in hero.lower(), "want %s / %s in: %s" % (band, val, hero[:110]))

        print("\nthe record strip is the whole record")
        strip = page.evaluate(STRIP_PROBE)
        hist = page.evaluate("async () => (await (await fetch('data/enso.json')).json()).data.history.length")
        check("one bar per winter in the published record",
              bool(strip) and strip["bars"] == hist,
              "%s bars vs %s winters" % (strip and strip["bars"], hist))
        check("the current reading is marked and labelled",
              bool(strip) and strip["now"] and feed["season"].lower() in strip["now"].lower() and val in strip["now"],
              str(strip and strip["now"]))
        # preserveAspectRatio="none" stretches glyphs, so no text may live in the SVG
        check("no text inside the stretched chart",
              bool(strip) and strip["textInSvg"] == 0, str(strip and strip["textInSvg"]))
        check("the strip carries an accessible description", bool(strip and strip["hasLabel"]))

        page.wait_for_timeout(1100)  # opening plot animation has completed
        marker = page.evaluate(NOW_PROBE)
        # The HTML callout and SVG endpoint must use the same y coordinate.
        check("the live callout sits on its plotted point",
              bool(marker) and abs(marker["offset"]) <= 3, str(marker))

        axis = page.evaluate("""() => {
            const h = document.querySelector('#enso-hero');
            return !!h.querySelector('svg .enso-y-axis') &&
              [...h.querySelectorAll('.enso-threshold-label')].some(e => e.textContent.includes('+0.5 El Niño threshold')) &&
              [...h.querySelectorAll('.enso-threshold-label')].some(e => e.textContent.includes('−0.5 La Niña threshold'));
        }""")
        check("ONI hero has a y-axis and both labelled thresholds", axis)

        # A ceiling check against live data proves nothing: today's reading and the
        # record both sit under the old fixed 2.6 axis, so a clamped chart passes it
        # too. The only way to test a clamp is to feed it a value that would clamp.
        # Serve a spiked enso.json and require the axis to move.
        base_top = marker["topBarY"] if marker else None
        hist_max = page.evaluate(
            "async () => Math.max(...(await (await fetch('data/enso.json')).json())"
            ".data.history.map(r => Math.abs(r.anom)))")
        spike = round(hist_max + 1.5, 2)

        def spike_enso(route):
            r = route.fetch()
            body = r.json()
            body["data"]["latest"]["anom"] = spike
            route.fulfill(json=body)

        page.route("**/data/enso.json", spike_enso)
        try:
            open_panel(page, base)
            page.wait_for_selector(".enso-strip", timeout=20_000)
            page.wait_for_timeout(1100)
            spiked = page.evaluate(NOW_PROBE)
        finally:
            page.unroute("**/data/enso.json")

        check("a reading above the record is printed at its true value",
              bool(spiked) and ("%.2f" % spike) in (spiked["label"] or ""),
              f'want {spike:.2f} in {spiked and spiked["label"]}')
        # NOTE this one is a smoke check only: a clamped rule also lands inside the
        # plot, so it passes on the buggy code too. Named for what it can actually
        # detect rather than for what the fix was.
        check("the marker stays inside the plot",
              bool(spiked) and spiked["ruleY"] > 2,
              f'rule at {spiked and spiked["ruleY"]}px')
        # THIS is the discriminating one. Under a fixed axis the record bar cannot
        # move when the live value changes; verified to fail against the old code
        # with 'record bar 12.27 -> 12.27'.
        check("the whole record rescales to make room",
              bool(spiked) and base_top is not None and spiked["topBarY"] > base_top + 2,
              f'record bar {base_top} -> {spiked and spiked["topBarY"]} (must drop)')

        open_panel(page, base)   # back to real data for everything downstream
        page.wait_for_selector(".enso-strip", timeout=20_000)



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

        print("\ncrop colour encodes the change, not the raw slope")
        # The coefficients are %/ONI slopes and ONI is negative under La Nina, so
        # a POSITIVE nina slope is a production FALL. Colouring the raw slope is
        # right under El Nino by coincidence and inverted under La Nina.
        WARM = "(el) => { const c = getComputedStyle(el).color.match(/\\d+/g).map(Number); return c[0] > c[2]; }"

        def slope_of(cell) -> float:
            return float(cell.inner_text().replace("−", "-").replace("+", ""))

        page.select_option("#enso-level", "1.5")
        page.select_option("#enso-country", "USA")
        page.wait_for_timeout(300)
        cell = page.locator("#enso-detail tbody tr td.num").nth(0)
        sl, warm = slope_of(cell), cell.evaluate(WARM)
        check("El Nino: a positive slope is coloured as a rise",
              (sl > 0) == (not warm), f"slope {sl}, warm={warm}")

        page.select_option("#enso-level", "-1.5")
        page.wait_for_timeout(300)
        cell = page.locator("#enso-detail tbody tr td.num").nth(1)
        sl, warm = slope_of(cell), cell.evaluate(WARM)
        check("La Nina: a positive slope is coloured as a FALL (ONI is negative)",
              (sl > 0) == warm, f"slope {sl}, warm={warm}")
        check("the table states that sign and colour may disagree",
              "not match the sign of the colour" in page.locator("#enso-detail").inner_text())
        page.select_option("#enso-level", "1.5")

        print("\nthe crop legend describes what the fill encodes")
        page.select_option("#enso-mode", "crop")
        page.wait_for_timeout(350)
        leg = page.locator("#enso-legend").inner_text()
        check("crop legend does not call the fill a %/ONI slope",
              "%/ONI" not in leg, leg[:120])
        check("crop legend names the scenario the colour is scaled to",
              "Strong El" in leg and "ONI" in leg, leg[:160])
        page.select_option("#enso-level", "-1.5")
        page.wait_for_timeout(350)
        leg_nina = page.locator("#enso-legend").inner_text()
        check("crop legend follows the selected scenario",
              "La Ni" in leg_nina and leg_nina != leg, leg_nina[:160])
        page.select_option("#enso-level", "1.5")
        page.select_option("#enso-mode", "impact")
        page.wait_for_timeout(250)

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

        print("\npanel styling stays inside the panel")
        # .viewswitch is site-wide (Rankings, Scenario, About & Method, Data all
        # use it) and --fs-* are the site's own five-step scale. A previous pass
        # restyled both globally while only the El Nino panel was in scope.
        style = page.evaluate("""() => {
            const pick = sel => { const e = document.querySelector(sel); if (!e) return null;
              const cs = getComputedStyle(e);
              return {ff: cs.fontFamily.split(',')[0].replace(/"/g,''), tt: cs.textTransform}; };
            const root = getComputedStyle(document.documentElement);
            return {
              scenario: pick('#tab-scenario .viewswitch-btn'),
              about:    pick('#tab-about .viewswitch-btn'),
              elnino:   pick('#enso-view-nav .viewswitch-btn'),
              fsBody: root.getPropertyValue('--fs-body').trim(),
              fsMeta: root.getPropertyValue('--fs-meta').trim(),
              fsHead: root.getPropertyValue('--fs-head').trim(),
            };
        }""")
        for other in ("scenario", "about"):
            v = style[other]
            check(f"site tab bar ({other}) keeps its own treatment",
                  bool(v) and v["tt"] == "uppercase" and v["ff"] == "Geist Mono", str(v))
        check("El Nino tab bar uses the panel register",
              style["elnino"] and style["elnino"]["tt"] == "none", str(style["elnino"]))
        check("site type tokens are untouched",
              (style["fsBody"], style["fsMeta"], style["fsHead"]) == ("12px", "10px", "18px"),
              f'{style["fsBody"]}/{style["fsMeta"]}/{style["fsHead"]}')

        print("\nmap annotations contain their own text")
        # iconSize was [188, 1]: Leaflet wrote that height inline, so the card was
        # one pixel tall and every line of body text sat outside it, on the map.
        boxes = page.evaluate("""() => [...document.querySelectorAll('.enso-anno')].map(e => {
            const r = e.getBoundingClientRect();
            const last = e.querySelector('span');
            const lr = last ? last.getBoundingClientRect() : null;
            return {h: Math.round(r.height), contains: lr ? (lr.bottom <= r.bottom + 1) : false};
        })""")
        check("annotation cards are taller than a single line",
              bool(boxes) and all(b["h"] > 30 for b in boxes), str(boxes))
        check("annotation text sits inside its card",
              bool(boxes) and all(b["contains"] for b in boxes), str(boxes))

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

        print("\nthe Panama step chart does not fake elapsed time")
        page.goto(f"{base}/index.html?tab=ensowater", wait_until="networkidle")
        page.wait_for_selector("#enso-c-panama", timeout=20_000)
        page.wait_for_timeout(2000)
        pan = page.evaluate(PANAMA_PROBE)
        # Category labels space a 4-day gap and a 2-year gap identically, so the
        # interval has to be stated on the label itself.
        check("step labels carry their real interval",
              bool(pan) and sum(1 for l in pan["labels"] if "+" in l) >= 4, str(pan and pan["labels"]))
        check("the multi-year jump to today is spelled out",
              bool(pan) and any("yr" in l for l in pan["labels"]), str(pan and pan["labels"]))
        check("the caption says the axis is ordinal",
              bool(pan) and "not to scale in time" in (pan["cap"] or ""), (pan or {}).get("cap", "")[:120])

        # Back to the view the layout checks below were written against — they
        # measure the news rail and bulletin strip, which only exist there.
        open_panel(page, base, "ensolive")
        page.wait_for_selector(".enso-bul", timeout=20_000)

        print("\nlayout")
        # Plate grammar: bulletin rows carry an agency column and the indices
        # comparison sits at its plate's inner edge, so the tab no longer has one
        # global text edge. Text inside each container must still share one edge.
        edges = page.evaluate("""() => {
            const groups = {
              bulletins: ['.enso-bul .enso-bul-k', '.enso-bul .enso-bul-sub'],
              indices:   ['.enso-idx-cmps .enso-idx-cmp-h', '.enso-idx-cmps .enso-idx-cmp-b'],
            };
            const out = {};
            for (const [name, sels] of Object.entries(groups)) {
              const seen = new Set();
              for (const sel of sels) for (const n of document.querySelectorAll(sel)) {
                if (!n.offsetParent) continue;            // hidden (closed details, other view)
                const r = n.getBoundingClientRect(), cs = getComputedStyle(n);
                seen.add(Math.round(r.left + parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth)));
              }
              out[name] = [...seen];
            }
            return out;
        }""")
        check("text inside each container shares one left edge",
              all(len(v) <= 1 for v in edges.values()) and len(edges["bulletins"]) == 1, str(edges))
        desktop_ok = page.evaluate(
            "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        mobile_overflow = []
        rounded = []
        page.set_viewport_size({"width": 390, "height": 844})
        for tab in ("elnino", "ensomech", "ensowater", "ensomoney", "ensolive"):
            open_panel(page, base, tab)
            page.wait_for_selector(f"#subview-{tab} .enso-subview-meta")
            page.eval_on_selector_all("#tab-elnino details", "els => els.forEach(e => e.open = true)")
            page.wait_for_timeout(350)
            rounded.extend(page.evaluate("""() => [...document.querySelectorAll('#tab-elnino *')]
                .filter(e => { const c = getComputedStyle(e); return [c.borderTopLeftRadius, c.borderTopRightRadius, c.borderBottomLeftRadius, c.borderBottomRightRadius]
                    .some(r => parseFloat(r) > 0); })
                .map(e => e.id || e.className).slice(0, 10)"""))
            overflow = page.evaluate("""() => {
                const selectors = ['html', '#nav', '#enso-view-nav', '#tab-elnino',
                    '#tab-elnino .content-page', '#tab-elnino .subview.active',
                    '#tab-elnino .subview.active .enso-tblwrap'];
                return selectors.filter(s => [...document.querySelectorAll(s)].some(e =>
                    e.clientWidth && e.scrollWidth > e.clientWidth + 1));
            }""")
            print(f"  390px {tab}: " + (", ".join(overflow) if overflow else "no horizontal overflow"))
            mobile_overflow.extend(f"{tab}: {s}" for s in overflow)
        check("no horizontal overflow", desktop_ok and not mobile_overflow, "; ".join(mobile_overflow))

        check("every element in the El Niño tab has square corners", not rounded, str(rounded))
        page.set_viewport_size({"width": 1440, "height": 1000})
        open_panel(page, base, "ensomech")
        page.wait_for_selector('.enso-xsec-lead')
        labels = page.eval_on_selector_all(
            '.enso-xsec-lead [role="img"], .enso-xsec-refs [role="img"]',
            "els => els.map(e => e.getAttribute('aria-label'))")
        check("three cross-sections have distinct state descriptions",
              len(labels) == 3 and len(set(labels)) == 3 and all(labels), str(labels))
        page.locator('#viewbtn-ensowater').click()
        page.wait_for_selector('#subview-ensowater.active #enso-c-panama')
        page.locator('#viewbtn-ensowater').focus()
        page.keyboard.press('ArrowRight')
        page.wait_for_selector('#subview-ensomoney.active #enso-c-record')
        check("view switcher works with clicks and arrow keys",
              page.locator('#viewbtn-ensomoney').get_attribute('aria-selected') == 'true')

        check("no console errors", not errors, "; ".join(errors[:2]))
        browser.close()

    httpd.shutdown()
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} passed")
    if FAILURES:
        print("failed: " + ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
