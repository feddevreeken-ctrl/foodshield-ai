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
            now: (document.querySelector('.enso-analog-now-lab') || {}).textContent || null,
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
    const plot = document.querySelector('.enso-analog');
    const svg = plot && plot.querySelector('svg');
    const lab = document.querySelector('.enso-analog-now-lab');
    const point = svg && svg.querySelector('.enso-live-point');
    if (!svg || !plot || !lab || !point) return null;
    const pr = svg.getBoundingClientRect(), gr = point.getBoundingClientRect();
    const pointY = gr.top + gr.height / 2, lr = lab.getBoundingClientRect();
    return {offset: Math.round((lr.top + lr.height / 2) - pointY),
            ruleY: Math.round(pointY - pr.top), label: lab.textContent,
            past: [...svg.querySelectorAll('.enso-analog-past')].map(p => p.getAttribute('d')),
            ticks: [...plot.querySelectorAll('.enso-y-tick')].map(e => e.textContent),
            overflow: (document.querySelector('.enso-overflow') || {}).textContent || ''};
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
    resolved = "elnino" if tab == "ensomech" else tab
    page.wait_for_selector(f"#subview-{resolved}.active .enso-subview-meta", timeout=25_000)


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
        # A fetch cut off by the test's own navigation (the live-alerts poll) is not an application error.
        page.on("requestfailed",
                lambda r: None if 'ERR_ABORTED' in str(r.failure or '') else note(f"request failed: {r.failure or ''}", r.url))

        print("\nindex strip — comparability")
        open_panel(page, base)
        switcher = page.locator('#enso-view-nav')
        # The switcher is the tab's jump nav: first in the tab, ahead of the status
        # header, the map and every view, pinned as a translucent hairline bar
        # (the owner: "this should stick but elegantly").
        leads = switcher.evaluate("el => !!(el.compareDocumentPosition(document.getElementById('enso-hero')) & Node.DOCUMENT_POSITION_FOLLOWING) && !!(el.compareDocumentPosition(document.getElementById('enso-map')) & Node.DOCUMENT_POSITION_FOLLOWING) && !!el.closest('#tab-elnino') && getComputedStyle(el).position === 'sticky' && getComputedStyle(el).backgroundColor.startsWith('rgba') && getComputedStyle(el).backgroundColor.startsWith('rgba') && el.getBoundingClientRect().bottom <= document.getElementById('enso-hero').getBoundingClientRect().top + 1")
        visible_here = switcher.is_visible()
        page.evaluate("showTab('global')")
        hidden_elsewhere = not switcher.is_visible()
        page.evaluate("showTab('elnino')")
        check("view switcher leads the tab, pins as a translucent bar, and only shows on its host tab",
              leads and visible_here and hidden_elsewhere and switcher.is_visible())
        # A view change lands the reader at the top of the tab, not mid-page.
        page.evaluate("() => { const s = document.querySelector('#tab-elnino .content-page'); if (s) s.scrollTop = 1400; }")
        page.evaluate("showTab('ensowater')")
        page.wait_for_selector('#subview-ensowater.active .enso-subview-meta')
        top_after = page.evaluate("() => { const s = document.querySelector('#tab-elnino .content-page'); return s ? s.scrollTop : window.scrollY; }")
        page.evaluate("showTab('elnino')")
        page.wait_for_selector('#subview-elnino.active .enso-subview-meta')
        check("a view change starts at the top of the tab", top_after == 0, str(top_after))
        # A layer picked by hand belongs to the view it was picked on. Pressing
        # "Food inflation" on Ocean must not paint Reported with it.
        page.locator('[data-native="enso-mode"][data-value="rtfp"]').click()
        page.wait_for_timeout(600)
        picked_here = page.input_value('#enso-mode')
        page.evaluate("showTab('ensolive')")
        page.wait_for_selector('#subview-ensolive.active .enso-subview-meta')
        page.wait_for_timeout(600)
        after_switch = page.input_value('#enso-mode')
        url_after = page.evaluate("() => location.search")
        page.evaluate("showTab('elnino')")
        page.wait_for_selector('#subview-elnino.active .enso-subview-meta')
        check("a hand-picked layer stays on its view; the next view opens on its own lens",
              picked_here == 'rtfp' and after_switch == 'asap' and 'enso_mode=rtfp' not in url_after,
              f"{picked_here} -> {after_switch} {url_after}")
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

        check("Ocean defaults to observed SST with the backdrop enabled",
              page.input_value('#enso-mode') == 'sst' and page.is_checked('#enso-tog-sst')
              and page.locator('.enso-tag-interpolation').count() == 0)
        page.evaluate("showTab('ensoharvest')")
        page.wait_for_selector('#subview-ensoharvest.active .enso-cal')
        check("Harvests defaults to impact without the ocean backdrop",
              page.input_value('#enso-mode') == 'impact' and not page.is_checked('#enso-tog-sst'))
        print("\nscenario disclosure")
        tag = page.locator(".enso-tag-interpolation")
        check("modelled layer discloses interpolation at the observed ONI", tag.count() == 1 and "interpolated between" in tag.text_content())
        # Read expected values from the feed rather than hardcoding them: CPC
        # publishes a new season every month, and this assertion is about the hero
        # agreeing with enso.json, not about any particular number.
        feed = page.evaluate("async () => (await (await fetch('data/enso.json')).json()).data.latest")
        hero = page.locator("#enso-hero").text_content()
        band = feed["band"].replace("El Nino", "El Niño").replace("La Nina", "La Niña")
        val = ("%+.2f" % feed["anom"]).replace("-", "−")
        check("hero prints the observed agency band",
              band.lower() in hero.lower() and val.lower() in hero.lower(), "want %s / %s in: %s" % (band, val, hero[:110]))

        page.evaluate("showTab('elnino')")
        print("\nthe record strip is the whole record")
        strip = page.evaluate(STRIP_PROBE)
        hist = page.evaluate("async () => (await (await fetch('data/enso.json')).json()).data.history.length")
        check("one bar per winter in the published record",
              bool(strip) and strip["bars"] == hist,
              "%s bars vs %s winters" % (strip and strip["bars"], hist))
        check("the analog plate marks and labels the latest current reading",
              bool(strip) and strip["now"] and feed["season"].lower() in strip["now"].lower() and val in strip["now"],
              str(strip and strip["now"]))
        # preserveAspectRatio="none" stretches glyphs, so no text may live in the SVG
        check("no text inside the stretched chart",
              bool(strip) and strip["textInSvg"] == 0, str(strip and strip["textInSvg"]))
        check("the strip carries an accessible description", bool(strip and strip["hasLabel"]))

        page.wait_for_timeout(1100)  # opening plot animation has completed
        marker = page.evaluate(NOW_PROBE)
        # The HTML callout and SVG endpoint must use the same y coordinate.
        check("the analog callout sits on its plotted point",
              bool(marker) and abs(marker["offset"]) <= 3, str(marker))

        axis = page.evaluate("""() => {
            const h = document.querySelector('#enso-hero');
            return !!h.querySelector('svg .enso-y-axis') &&
              [...h.querySelectorAll('.enso-threshold-label')].some(e => e.textContent.includes('+0.5 El Niño threshold')) &&
              [...h.querySelectorAll('.enso-threshold-label')].some(e => e.textContent.includes('−0.5 La Niña threshold'));
        }""")
        check("analog plate has fixed anchors and both labelled thresholds", axis and marker["ticks"] == ["−3", "0", "+3"])

        # Inject an out-of-range current value: preserve fixed anchors and
        # historical geometry while printing the true value and overflow.
        base_past = marker["past"] if marker else None
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
        check("the marker stays inside the plot",
              bool(spiked) and spiked["ruleY"] > 2,
              f'rule at {spiked and spiked["ruleY"]}px')
        check("fixed anchors retain historical geometry and disclose overflow",
              bool(spiked) and spiked["past"] == base_past
              and spiked["ticks"] == ["−3", "0", "+3"]
              and ("%.2f" % spike) in spiked["overflow"] and 'exceed' in spiked["overflow"],
              str(spiked))

        open_panel(page, base)   # back to real data for everything downstream
        page.wait_for_selector(".enso-strip", timeout=20_000)



        page.evaluate("showTab('ensoharvest')")
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
        check("explicit scenario replaces interpolation once chosen by hand",
              "Explicit scenario:" in page.locator(".enso-tag-interpolation").text_content())

        print("\ncrop colour encodes the change, not the raw slope")
        # The coefficients are %/ONI slopes and ONI is negative under La Nina, so
        # a POSITIVE nina slope is a production FALL. Colouring the raw slope is
        # right under El Nino by coincidence and inverted under La Nina.
        PALETTE = """el => {
            const c = getComputedStyle(el).color.match(/\\d+/g).map(Number);
            return c[0] > c[1] && c[1] > c[2] ? 'fall' : c[1] > c[0] && c[1] > c[2] ? 'rise' : 'neutral';
        }"""

        def slope_of(cell) -> float:
            return float(cell.inner_text().replace("−", "-").replace("+", ""))

        page.select_option("#enso-level", "1.5")
        page.select_option("#enso-country", "USA")
        page.wait_for_timeout(300)
        cell = page.locator("#enso-detail tbody tr td.num").nth(0)
        sl, colour = slope_of(cell), cell.evaluate(PALETTE)
        check("El Nino: positive change uses green and negative change uses ochre",
              colour == ("rise" if sl > 0 else "fall"), f"slope {sl}, colour={colour}")

        page.select_option("#enso-level", "-1.5")
        page.wait_for_timeout(300)
        cell = page.locator("#enso-detail tbody tr td.num").nth(1)
        sl, colour = slope_of(cell), cell.evaluate(PALETTE)
        check("La Nina: positive slope uses ochre for the yield fall",
              colour == ("fall" if sl > 0 else "rise"), f"slope {sl}, colour={colour}")
        check("the table states that sign and colour may disagree",
              "not match the sign of the colour" in page.locator("#enso-detail").inner_text())
        page.select_option("#enso-level", "1.5")

        print("\nthe crop legend describes what the fill encodes")
        page.select_option("#enso-mode", "crop")
        page.wait_for_timeout(350)
        leg = page.locator("#enso-legend").text_content()
        check("crop legend does not call the fill a %/ONI slope",
              "%/ONI" not in leg, leg[:120])
        check("crop legend names the scenario the colour is scaled to",
              "Strong El" in leg and "ONI" in leg, leg[:160])
        page.select_option("#enso-level", "-1.5")
        page.wait_for_timeout(350)
        leg_nina = page.locator("#enso-legend").text_content()
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
        # The harvest notes (Kansas, Free State) draw on the Harvests lens only;
        # the canal note belongs to Shipping. Measure them where they exist.
        page.evaluate("showTab('ensoharvest')")
        page.wait_for_selector('#subview-ensoharvest.active .enso-subview-meta')
        page.wait_for_timeout(600)
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
        for tab in ("elnino", "ensomech", "ensoharvest", "ensowater", "ensomoney", "ensolive"):
            open_panel(page, base, tab)
            resolved = "elnino" if tab == "ensomech" else tab
            page.wait_for_selector(f"#subview-{resolved} .enso-subview-meta")
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
        page.wait_for_timeout(300)
        check("mechanism alias activates Ocean and scrolls to the mechanism",
              page.locator('#viewbtn-elnino').get_attribute('aria-selected') == 'true'
              and page.locator('#subview-elnino').evaluate("e => e.classList.contains('active')")
              and abs(page.locator('#enso-mech').bounding_box()['y']
                      - page.locator('#tab-elnino .content-page').bounding_box()['y'] - 120) < 5)
        labels = page.eval_on_selector_all(
            '.enso-xsec-lead [role="img"], .enso-xsec-refs [role="img"]',
            "els => els.map(e => e.getAttribute('aria-label'))")
        check("the cross-sections all carry distinct state descriptions",
              len(labels) >= 3 and len(set(labels)) == len(labels) and all(labels), str(labels))
        page.locator('#viewbtn-ensowater').click()
        page.wait_for_selector('#subview-ensowater.active #enso-c-panama')
        page.locator('#viewbtn-ensowater').focus()
        page.keyboard.press('ArrowRight')
        page.wait_for_selector('#subview-ensomoney.active #enso-c-record')
        check("view switcher works with clicks and arrow keys",
              page.locator('#viewbtn-ensomoney').get_attribute('aria-selected') == 'true')

        print("\nstage A shared structure and lens contracts")
        open_panel(page, base)
        lens_results, headings, frames = [], [], []
        page.evaluate("window._stageAMap = document.getElementById('enso-map'); window._stageAMapId = window._stageAMap._leaflet_id")
        for tab, mode in (("elnino", "sst"), ("ensoharvest", "impact"), ("ensowater", "none"), ("ensomoney", "rtfp"), ("ensolive", "asap")):
            page.evaluate("tab => showTab(tab)", tab)
            page.wait_for_selector(f'#subview-{tab}.active .enso-subview-meta')
            lens_results.append(page.input_value('#enso-mode') == mode
                and page.is_checked('#enso-tog-sst') == (tab == 'elnino')
                and page.is_checked('#enso-tog-lanes') == (tab == 'ensowater')
                and page.is_checked('#enso-tog-alerts') == (tab == 'ensolive'))
            if tab == "ensomoney":
                dates = page.evaluate("async () => Object.values((await (await fetch('data/rtfp.json')).json()).data).map(r => r.as_of).filter(Boolean).sort()")
                legend = page.locator('#enso-legend').text_content()
                check("rtfp legend states fixed anchors and country as_of range",
                      all(t in legend for t in ('Fixed anchors', '−10%', '0%', '+30%', 'beyond the ends', 'as of', dates[0], dates[-1])))
            headings.append(page.locator('#tab-elnino h2:visible').count())
            frames.append(page.evaluate("""() => [...document.querySelectorAll('#tab-elnino .enso-plate[data-kind], #enso-mapwrap[data-kind]')].every(e =>
                getComputedStyle(e).borderTopStyle === (['modelled','published'].includes(e.dataset.kind) ? 'dashed' : 'solid'))"""))
        check("each view applies its layer and overlay defaults", all(lens_results), str(lens_results))
        check("one visible Instrument Serif H2 per view", headings == [1] * 5
              and page.locator('#tab-elnino h2:visible').evaluate("e => getComputedStyle(e).fontFamily.includes('Instrument Serif')"), str(headings))
        check("observed frames are solid and modelled or published frames dashed", all(frames), str(frames))
        check("one persistent map instance across all five views", page.evaluate("""() =>
            document.querySelectorAll('#enso-map').length === 1 && document.getElementById('enso-map') === window._stageAMap
            && window._stageAMap._leaflet_id === window._stageAMapId
            && document.querySelectorAll('#enso-map .leaflet-map-pane').length === 1"""))
        check("limits remain reachable from every view", page.locator('#enso-limits').is_visible()
              and page.locator('#enso-limits').evaluate("e => !e.closest('.subview')")
              and page.locator('#enso-agency-status').is_visible())
        page.evaluate("showTab('ensoharvest')")
        calendar = page.evaluate("""async () => {
            const model = (await (await fetch('data/enso_model.json')).json()).data;
            const cal = (await (await fetch('data/crop_calendars.json')).json()).data;
            let expected = 0;
            for (const [iso, crops] of Object.entries(model)) for (const [crop, c] of Object.entries(crops))
                if (c.signal && cal[iso] && cal[iso][crop] && (cal[iso][crop].harvest || []).length) expected++;
            const rows = [...document.querySelectorAll('#enso-calendar .cal-row:not(.cal-head)')];
            return {expected: Math.min(30, expected), actual: rows.length,
                    complete: rows.every(r => r.querySelectorAll('.cal-c').length === 12)};
        }""")
        check("moved calendar retains every eligible row and all twelve months",
              calendar['actual'] == calendar['expected'] and calendar['actual'] > 0 and calendar['complete'], str(calendar))
        page.select_option('#enso-mode', 'impact')
        palette = page.eval_on_selector_all('#enso-legend .enso-ramp i', "els => els.map(e => getComputedStyle(e).backgroundColor)")
        check("yield ramp has fixed ochre, warm grey and green anchors",
              palette[0] == 'rgb(201, 119, 58)' and palette[len(palette)//2] == 'rgb(139, 137, 128)' and palette[-1] == 'rgb(107, 163, 107)', str(palette))
        page.goto(f"{base}/index.html?tab=ensomoney&enso_level=-1.5&enso_mode=crop", wait_until='networkidle')
        page.wait_for_selector('#subview-ensomoney.active .enso-subview-meta')
        check("explicit URL scenario and mode override view defaults",
              page.input_value('#enso-level') == '-1.5' and page.input_value('#enso-mode') == 'crop')
        page.evaluate("async () => await ensoRetry()")
        check("retry rebuilds one map and preserves explicit selections",
              page.locator('#enso-map .leaflet-map-pane').count() == 1
              and page.input_value('#enso-level') == '-1.5' and page.input_value('#enso-mode') == 'crop')

        print("\nstage B instruments and consolidated plates")
        page.evaluate("showTab('ensoharvest')")
        page.wait_for_selector('#subview-ensoharvest.active #enso-harvest-fig')
        check("nine scenario rungs retain the observed marker and both instrument rows",
              page.locator('[data-native="enso-level"]:not([data-value="observed"])').count() == 9
              and page.locator('[data-native="enso-level"][data-value="observed"]').count() == 1
              and page.locator('.is-observed-rung').count() == 1
              and page.locator('.enso-instrument-row').count() == 2)
        page.locator('[data-native="enso-level"][data-value="-1.5"]').click()
        page.locator('[data-native="enso-mode"][data-value="crop"]').click()
        check("visible scenario and layer buttons drive the native change handlers",
              page.input_value('#enso-level') == '-1.5' and page.input_value('#enso-mode') == 'crop'
              and page.locator('[data-native="enso-level"][data-value="-1.5"]').get_attribute('aria-pressed') == 'true'
              and 'La Ni' in page.locator('#enso-legend').inner_text())
        country_name = page.locator('#enso-country option[value="ZWE"]').text_content()
        page.locator('#enso-country-search').fill(country_name)
        page.wait_for_function("() => document.getElementById('enso-harvest-fig').dataset.iso === 'ZWE'")
        check("country search synchronises native selection, bars and coefficients",
              page.input_value('#enso-country') == 'ZWE'
              and country_name in page.locator('#enso-detail').inner_text()
              and page.locator('#enso-harvest-fig .hs-bar.neg[data-k="La Niña"]').count() > 0)
        page.evaluate("ensoFocus('USA')")
        page.wait_for_function("() => document.getElementById('enso-harvest-fig').dataset.iso === 'USA'")
        check("ensoFocus updates both harvest surfaces without a scroll tour",
              page.input_value('#enso-country') == 'USA'
              and page.locator('.enso-harvest-pair #enso-coeffs #enso-detail').count() == 1
              and page.locator('#enso-harvest-story').count() == 0)
        grouped = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#enso-calendar .cal-row:not(.cal-head)')];
            const order = ['harvesting now','planting','in the ground','between seasons'];
            const ranks = rows.map(r => order.indexOf(r.dataset.st));
            return document.querySelectorAll('#enso-calendar .cal-group').length === 4
                && ranks.every((v, i) => v >= 0 && (!i || v >= ranks[i - 1]))
                && [...document.querySelectorAll('#enso-calendar .cal-group b')]
                     .reduce((n, el) => n + Number(el.textContent), 0) === rows.length;
        }""")
        months = page.locator('#enso-calendar .cal-head .cal-c').all_text_contents()
        check("calendar groups retain counts and explicit Jan to Dec columns",
              grouped and months == ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])
        register_count = page.evaluate("async () => (await (await fetch('data/enso_econ.json')).json()).data.do_not_publish.rows.length")
        check("shared How to read retains all eight limits and rejected claims",
              page.locator('#enso-limits .enso-lim').count() == 8
              and page.locator('#enso-limits .enso-reg-row').count() == register_count
              and page.locator('#enso-limits #enso-gate').count() == 1
              and 'How to read' in page.locator('#enso-limits summary').first.text_content())
        page.evaluate("showTab('ensowater')")
        page.wait_for_selector('#subview-ensowater.active #enso-c-panama')
        water = page.evaluate("""() => {
            const slot = document.getElementById('enso-c-panama'), ais = document.getElementById('enso-c-panama-daily');
            return {lead: !!(slot.compareDocumentPosition(ais) & Node.DOCUMENT_POSITION_FOLLOWING),
                    slot: slot.closest('figure').dataset.kind, ais: ais.closest('figure').dataset.kind,
                    source: ais.closest('figure').querySelector('.enso-plate-sub').textContent};
        }""")
        history = page.evaluate("async () => (await (await fetch('data/portwatch_history.json')).json()).data.chokepoints.panama.dates")
        lane_count = page.evaluate("async () => (await (await fetch('data/enso_lanes.json')).json()).data.lanes.length")
        check("published Panama limits lead observed AIS with its actual coverage",
              water['lead'] and water['slot'] == 'published' and water['ais'] == 'observed'
              and history[0] in water['source'] and history[-1] in water['source'])
        check("one lanes table replaces the tour and duplicate appendix",
              page.locator('.enso-lanes-table tbody tr').count() == lane_count
              and page.locator('#enso-lane-story').count() == 0)
        page.evaluate("showTab('ensomoney')")
        page.wait_for_selector('#subview-ensomoney.active #enso-c-ffpi')
        ffpi = page.evaluate("""() => {
            const c = Chart.getChart(document.getElementById('enso-c-ffpi'));
            return c.data.datasets.map(d => ({label:d.label, line:!!d.showLine, style:d.pointStyle}));
        }""")
        check("Prices has one seven-event surface and distinct annual and monthly FFPI series",
              page.locator('#enso-c-record .enso-event').count() == 7
              and page.locator('#enso-c-ffpi').count() == 1
              and page.locator('#enso-c-ffpilive, #enso-money-story').count() == 0
              and len(ffpi) == 3 and all(not d['line'] for d in ffpi)
              and 'monthly' in ffpi[2]['label'] and ffpi[1]['style'] != ffpi[2]['style'])
        check("reported humanitarian need is the final price plate",
              page.locator('#subview-ensomoney figure').last.get_attribute('data-kind') == 'reported'
              and 'Reported humanitarian need' in page.locator('#subview-ensomoney figure').last.inner_text())
        page.evaluate("showTab('ensolive')")
        check("Reported board distinguishes published stories and reported assessments",
              'Everything on this board is observed' not in page.locator('#enso-live').inner_text()
              and page.locator('#enso-live .enso-wire-plate').get_attribute('data-kind') == 'published'
              and page.locator('#enso-live .enso-live-rail figure[data-kind="reported"]').count() >= 1)

        print("\nstage H map interactions and ranked readings")
        # Capture the actual rebuilt Leaflet instance without adding a production test API.
        page.evaluate("""async () => {
            const create = L.map;
            L.map = function(host, options) {
                const map = create.call(this, host, options);
                if (host.id === 'enso-map') window._stageHMap = map;
                return map;
            };
            try { await ensoRetry(); } finally { L.map = create; }
        }""")
        check("Stage H map instance disables gesture and keyboard zoom but retains dragging", page.evaluate("""() => {
            const m = window._stageHMap;
            return ['scrollWheelZoom','doubleClickZoom','touchZoom','boxZoom','keyboard'].every(k =>
                m.options[k] === false && !m[k].enabled()) && m.dragging.enabled();
        }"""))
        page.locator('#enso-mapwrap [data-z="0"]').click()
        initial_zoom = page.evaluate("_stageHMap.getZoom()")
        page.locator('#enso-mapwrap [data-z="1"]').click()
        zoomed = page.evaluate("_stageHMap.getZoom()")
        page.locator('#enso-mapwrap [data-z="-1"]').click()
        zoomed_out = page.evaluate("_stageHMap.getZoom()")
        page.locator('#enso-mapwrap [data-z="1"]').click()
        page.locator('#enso-mapwrap [data-z="0"]').click()
        check("Stage H plate buttons zoom in, zoom out and reset", zoomed == initial_zoom + 1
              and zoomed_out == initial_zoom and page.evaluate("_stageHMap.getZoom()") == 2)
        check("Stage H fold explains button zoom and page scrolling",
              'so the page can scroll over it' in page.locator('#enso-legend details').text_content())
        collapse = []
        for tab in ('elnino', 'ensowater', 'ensomoney', 'ensolive'):
            page.evaluate("tab => showTab(tab)", tab)
            page.wait_for_selector(f'#subview-{tab}.active .enso-subview-meta')
            collapse.append(not page.locator('#enso-scenario-rungs').is_visible()
                            and page.locator('#enso-scenario-toggle').is_visible())
        page.locator('#enso-scenario-toggle').click()
        expanded = page.locator('#enso-scenario-rungs').is_visible()
        page.locator('[data-native="enso-level"][data-value="-1.5"]').click()
        explicit = page.input_value('#enso-level') == '-1.5' and 'enso_level=-1.5' in page.url
        page.evaluate("showTab('ensoharvest')")
        check("Stage H scenario is quiet on observed lenses and expands without losing deep links",
              all(collapse) and expanded and explicit and page.locator('#enso-scenario-rungs').is_visible()
              and not page.locator('#enso-scenario-toggle').is_visible()
              and page.input_value('#enso-level') == '-1.5')
        ranked = []
        for tab, feed in (('ensomoney', 'rtfp'), ('ensolive', 'asap')):
            page.evaluate("tab => showTab(tab)", tab)
            page.wait_for_selector('#enso-map-ranking button')
            # The price rail no longer ranks the twelve highest inflations in the
            # whole RTFP feed. The owner asked for "all countries of the el nino",
            # so it lists every country the published teleconnection layer names,
            # valued ones first in descending order, then those with no monitored
            # market, then the monitored countries outside the layer.
            valid = page.evaluate("""async feed => {
                const data = (await (await fetch('data/' + feed + '.json')).json()).data;
                const buttons = [...document.querySelectorAll('#enso-map-ranking button')];
                const isos = buttons.map(b => b.dataset.mapCountry);
                const m = document.getElementById('enso-map').getBoundingClientRect();
                const a = document.getElementById('enso-map-ranking').getBoundingClientRect();
                if (a.left < m.right - 1) return false;
                if (feed !== 'rtfp') {
                    const rows = isos.map(i => data[i]);
                    const expected = Object.values(data).filter(r => [1,2].includes(r.hotspot_code));
                    const top = expected.map(r => r.hotspot_code).sort((x,y) => y-x);
                    return JSON.stringify(rows.map(r => r.hotspot_code)) === JSON.stringify(top)
                        && rows.every((r,i) => buttons[i].textContent.includes(
                            new Date(r.assessment_date).toLocaleDateString('en-US', {month:'long',year:'numeric',timeZone:'UTC'})));
                }
                const regions = (await (await fetch('data/enso_regions.json')).json()).data.regions;
                const tele = [...new Set(regions.flatMap(r => r.iso3 || []))];
                const valuedOf = i => Number.isFinite((data[i] || {}).food_inflation_pct);
                if (!tele.every(i => isos.includes(i))) return false;
                const outside = Object.keys(data).filter(i => valuedOf(i) && !tele.includes(i));
                if (!outside.every(i => isos.includes(i))) return false;
                const teleRows = isos.filter(i => tele.includes(i));
                const teleValued = teleRows.filter(valuedOf);
                if (teleRows.slice(0, teleValued.length).some(i => !valuedOf(i))) return false;
                const vals = teleValued.map(i => data[i].food_inflation_pct);
                if (vals.some((v,i) => i && v > vals[i-1])) return false;
                return isos.every((iso,i) => valuedOf(iso)
                    ? buttons[i].textContent.includes(data[iso].markets + ' markets')
                      && buttons[i].textContent.includes(data[iso].as_of)
                    : buttons[i].textContent.includes('No monitored market'));
            }""", feed)
            first = page.locator('#enso-map-ranking button').first
            iso = first.get_attribute('data-map-country')
            before_zoom = page.evaluate('_stageHMap.getZoom()')
            first.click()
            ranked.append(valid and page.locator(f'#subview-{tab}').evaluate("e => e.classList.contains('active')")
                          and page.input_value('#enso-country') == iso and first.get_attribute('aria-pressed') == 'true'
                          and page.evaluate('_stageHMap.getZoom()') == before_zoom)
        check("Stage H ranked lists retain values, dates, order and lens when selecting a country", all(ranked))
        check("Stage H alert keys count the actual mapped GDACS and ReliefWeb reports", page.evaluate("""() => {
            const counts = {gdacs:0,relief:0};
            _stageHMap.eachLayer(l => { if (l.options && l.options.ensoSource in counts) counts[l.options.ensoSource]++; });
            const key = document.getElementById('enso-legend').textContent;
            return key.includes('GDACS ' + counts.gdacs) && key.includes('ReliefWeb ' + counts.relief);
        }"""))
        page.set_viewport_size({'width':390,'height':844})
        stacked = []
        for tab in ('ensomoney','ensolive'):
            page.evaluate("tab => showTab(tab)", tab)
            stacked.append(page.evaluate("""() => document.getElementById('enso-map-ranking').getBoundingClientRect().top >=
                document.getElementById('enso-map').getBoundingClientRect().bottom - 1"""))
        check("Stage H both ranked lists stack below the map on phones", all(stacked))
        page.evaluate("showTab('ensowater')")
        check("Stage H phones show only three El Nino corridor chips and retain all routes in the fold",
              page.locator('.enso-corridor-chip > span:visible').count() == 3
              and page.locator('.enso-corridor-secondary > span:visible').count() == 0
              and 'all corridors are listed here' in page.locator('#enso-legend details').text_content())
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('#enso-mapwrap [data-z="0"]').click()
        check("Stage H Panama renders two dateline arcs without a world-spanning chord", page.evaluate("""() => {
            let found = false;
            _stageHMap.eachLayer(l => {
                if (l.options.className !== 'enso-corridor' || !l.getTooltip().getContent().includes('US Gulf grain to East Asia')) return;
                const arcs = l.getLatLngs();
                found = arcs.length === 2 && arcs.every(a => a.length > 1 && a.every((p,i) => !i || Math.abs(p.lng-a[i-1].lng) <= 180))
                    && arcs[0][arcs[0].length-1].lng === -180 && arcs[1][0].lng === 180;
            });
            return found;
        }"""))
        check("Stage H Panama and Amazon corridor chips clear chokepoint chips", page.evaluate("""() => {
            const overlap = (a,b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
            const chips = [...document.querySelectorAll('.enso-corridor-chip > span')].filter(e => /Gulf to East Asia|Amazon northern arc/.test(e.textContent));
            const chokes = [...document.querySelectorAll('.enso-choke-label')].map(e => e.getBoundingClientRect());
            return chips.length === 2 && chips.every(c => chokes.every(b => !overlap(c.getBoundingClientRect(),b)));
        }"""))

        print("\nStage I shipping marks and measurements")
        check("Stage I corridor polylines are dashed and lane polylines remain solid", page.evaluate("""() => {
            const corridors = [], lanes = [];
            _stageHMap.eachLayer(l => {
                if (l.options.className === 'enso-corridor') corridors.push(l);
                if (l.options.className === 'enso-lane') lanes.push(l);
            });
            // The supplied lanes have no line geometry; Stage D exercises solid lane fixtures.
            return corridors.length === 9 && corridors.every(l => l.options.dashArray === '6 4'
                && l.getElement().getAttribute('stroke-dasharray') === '6 4')
                && lanes.every(l => !l.options.dashArray && !l.getElement().hasAttribute('stroke-dasharray'));
        }"""))
        visible_key = page.locator('#enso-legend > .enso-legend').inner_text()
        # The key used to show a solid-line swatch for "observed transits". No
        # lane in enso_lanes.json carries a geometry, so that line is never
        # drawn and the key described a mark the map does not have. The observed
        # mark is the diamond and its ring.
        check("Stage I visible key distinguishes observed transits from published schematic corridors",
              'diamond and ring: observed, measured at the chokepoint' in visible_key
              and 'dashed: published schematic corridor through named ports' in visible_key)
        check("Stage I magnitude joins each lane to PortWatch and names missing values", page.evaluate("""async () => {
            const lanes = (await (await fetch('data/enso_lanes.json')).json()).data.lanes;
            const feed = await (await fetch('data/portwatch.json')).json();
            const rings = document.querySelectorAll('.enso-choke .enso-transit-ring');
            const key = document.querySelector('#enso-legend > .enso-legend').innerText;
            const date = s => new Date(s).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
            let measured = 0, missing = 0;
            return lanes.every(ln => {
                const label = document.querySelector('.enso-choke-label[data-lane="' + ln.id + '"]');
                if (!label) return false;
                const pin = label.closest('.enso-choke'), ring = pin.querySelector('.enso-transit-ring');
                const pw = feed.data[ln.portwatch_key], pct = pw && pw.yoy && pw.yoy.total_pct;
                if (!pw || !Number.isFinite(pct) || !Number.isFinite(pw.transits_per_day.total)) {
                    missing++;
                    return !ring && pin.classList.contains('no-transit') && label.textContent.includes('no transit data')
                        && key.includes('no PortWatch transit change available')
                        && key.includes(label.firstChild.textContent);
                }
                measured++;
                const expected = 2 * (9 + Math.min(Math.abs(pct),100) * .24);
                const signed = (pct > 0 ? '+' : pct < 0 ? '−' : '') + Math.abs(pct).toFixed(1) + '% y/y';
                return ring && Number(ring.dataset.yoy) === pct
                    && Math.abs(parseFloat(ring.style.width) - expected) < .001
                    && ring.style.width === ring.style.height && label.textContent.includes(signed)
                    && !pin.classList.contains('no-transit')
                    && key.includes(pw.window_days + '-day mean to ' + date(pw.latest_date))
                    && pin.getAttribute('aria-label').includes(pw.transits_per_day.total.toFixed(1) + ' transits/day');
            }) && measured === rings.length && measured > 0 && missing > 0
                && key.includes('mean transits per day against a year earlier, IMF PortWatch')
                && key.includes('collected ' + date(feed._meta.generated_at))
                && key.includes('Ring radius grows with absolute change, capped at 100%');
        }"""))
        check("Stage I both Panama arcs carry a visible matching continuation name", page.evaluate("""() => {
            const markers = [];
            _stageHMap.eachLayer(l => {
                if ((l.options.icon && l.options.icon.options.className || '').includes('enso-corridor-edge')) markers.push(l);
            });
            return markers.length === 2 && markers.map(l => l.getLatLng().lng).sort((a,b)=>a-b).join(',') === '-180,180'
                && markers.every(l => {
                    const span = l.getElement().querySelector('span'), box = span.getBoundingClientRect();
                    return span.dataset.corridor === 'us_gulf_panama_east_asia'
                        && span.textContent === '↔ Gulf to East Asia' && getComputedStyle(span).visibility === 'visible'
                        && box.width > 0 && box.height > 0;
                });
        }"""))
        check("Stage I nine routes and chips remain focusable with only three chips at rest", page.evaluate("""async () => {
            const feed = (await (await fetch('data/enso_corridors.json')).json()).data.corridors;
            const lines = [...document.querySelectorAll('path.enso-corridor')];
            const chips = [...document.querySelectorAll('.enso-corridor-chip')];
            const shown = e => getComputedStyle(e.querySelector('span')).visibility === 'visible';
            return lines.length === 9 && chips.length === 9 && chips.filter(shown).length === 3
                && feed.every(c => {
                    const line = lines.find(e => e.dataset.corridor === c.id), chip = chips.find(e => e.dataset.corridor === c.id);
                    const primary = c.phase === 'el_nino';
                    return line && chip && line.tabIndex === 0 && chip.tabIndex === 0 && shown(chip) === primary
                        && Number(line.getAttribute('stroke-width')) === (primary ? 2 : 1)
                        && Number(line.getAttribute('stroke-opacity')) === (primary ? .75 : .35)
                        && document.querySelector('#enso-legend details').textContent.includes(c.name);
                });
        }"""))
        context_chips = page.locator('.enso-corridor-secondary')
        focus_results = []
        for i in range(context_chips.count()):
            chip = context_chips.nth(i)
            chip.focus()
            focus_results.append(chip.evaluate("e => e === document.activeElement && getComputedStyle(e.querySelector('span')).visibility === 'visible'"))
            chip.evaluate('e => e.blur()')
            focus_results.append(not chip.locator('span').is_visible())
        check("Stage I context chips reveal on keyboard focus and return to quiet on blur", len(focus_results) == 12 and all(focus_results))
        context_id = context_chips.first.get_attribute('data-corridor')
        context_path = page.locator(f'path.enso-corridor[data-corridor="{context_id}"]')
        context_path.dispatch_event('mouseover')
        hovered = context_chips.first.locator('span').is_visible()
        context_path.dispatch_event('mouseout')
        check("Stage I hovering a context route reveals its chip", hovered and not context_chips.first.locator('span').is_visible())
        check("Stage I direction marks are seven pixel SVG triangles in the route hue", page.evaluate("""() => {
            const arrows = [...document.querySelectorAll('.enso-corridor-arrow')], lines = [];
            _stageHMap.eachLayer(l => { if (l.options.className === 'enso-corridor') lines.push(l); });
            return arrows.length === 9 && arrows.every((el,i) => {
                const svg = el.querySelector('svg'), path = svg && svg.querySelector('path');
                return svg && svg.getAttribute('width') === '7' && svg.getAttribute('height') === '7'
                    && path.getAttribute('d') === 'M0 0 L7 3.5 L0 7 Z' && !el.textContent.includes('›')
                    && path.getAttribute('fill') === lines[i].options.color && svg.style.transform.startsWith('rotate(');
            });
        }"""))

        check("no console errors", not errors, "; ".join(errors[:2]))
        browser.close()

    httpd.shutdown()
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} passed")
    if FAILURES:
        print("failed: " + ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
