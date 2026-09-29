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
import re
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

PANAMA_PROBE = """async () => {
    const svg = document.querySelector('.enso-pan-since');
    if (!svg) return null;
    const p = (await (await fetch('data/enso_lanes.json')).json()).data.lanes.find(l => l.id === 'panama');
    // 2026-09-29 audit: the slot limits read as a dated ladder in words under the chart, one step per advisory.
    const cap = svg.closest('.enso-plate').querySelector('.enso-log');
    return {steps: svg.closest('.enso-plate').querySelectorAll('.enso-pan-slots .enso-pan-step').length, want: p.precedent_2023.steps.length + p.live_2026.steps.length,
            years: [...svg.querySelectorAll('text')].map(t => t.textContent).filter(t => /^20\\d\\d(-\\d\\d)?$/.test(t)), cap: cap ? cap.textContent : ''};
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


def iso_text(d):
    # The page prints ISO dates as "21 Sep 2025" (isoText in index.html).
    import re as _re
    m = _re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(d))
    mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    return f"{int(m.group(3))} {mo[int(m.group(2)) - 1]} {m.group(1)}" if m else str(d)

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
        leads = switcher.evaluate("el => !!(el.compareDocumentPosition(document.getElementById('enso-hero')) & Node.DOCUMENT_POSITION_FOLLOWING) && !!(el.compareDocumentPosition(document.getElementById('enso-map')) & Node.DOCUMENT_POSITION_FOLLOWING) && !!el.closest('#tab-elnino') && getComputedStyle(el).position === 'sticky' && getComputedStyle(el).backgroundColor !== 'rgba(0, 0, 0, 0)' && el.getBoundingClientRect().bottom <= document.getElementById('enso-hero').getBoundingClientRect().top + 1")
        visible_here = switcher.is_visible()
        page.evaluate("showTab('global')")
        hidden_elsewhere = not switcher.is_visible()
        page.evaluate("showTab('elnino')")
        # 2026-09-22: the bar is opaque and docks flush under the site nav (owner:
        # content bleeding through the translucent bar read as a gap).
        check("view switcher leads the tab, pins as an opaque docked bar, and only shows on its host tab",
              leads and visible_here and hidden_elsewhere and switcher.is_visible())
        check("view bar has five equal cells with descriptors and an orange top rule",
              switcher.evaluate("""el => {
                const tabs = [...el.querySelectorAll('[role="tab"]')];
                const widths = tabs.map(t => t.getBoundingClientRect().width);
                return tabs.length === 5 && tabs.every(t => t.querySelector('.enso-view-desc')?.textContent)
                    && Math.max(...widths) - Math.min(...widths) < 1
                    && el.getBoundingClientRect().height === 40
                    && getComputedStyle(el.querySelector('[aria-selected="true"]')).borderTopColor === 'rgb(201, 119, 58)';
              }"""))
        # Like the other content tabs, .content-page alone scrolls; #main is clipped.
        top_before = page.evaluate("""() => {
            const s = document.querySelector('#tab-elnino .content-page');
            s.scrollTop = 1400;
            return getComputedStyle(document.getElementById('main')).overflowY === 'hidden' ? s.scrollTop : 0;
        }""")
        check("switcher pins flush below site navigation without a shadow", switcher.evaluate("""el => {
            const r = el.getBoundingClientRect(), nav = document.getElementById('nav').getBoundingClientRect();
            return Math.abs(r.top-nav.bottom) <= 1 && getComputedStyle(el).boxShadow === 'none';
        }"""))
        page.evaluate("showTab('ensowater')")
        page.wait_for_selector('#subview-ensowater.active .enso-subview-meta')
        top_after = page.evaluate("() => { const s = document.querySelector('#tab-elnino .content-page'); return s ? s.scrollTop : window.scrollY; }")
        page.evaluate("showTab('elnino')")
        page.wait_for_selector('#subview-elnino.active .enso-subview-meta')
        check("a view change resets the sole content-page scroller", top_before > 0 and top_after == 0,
              f"{top_before} -> {top_after}")
        page.locator('#enso-map').scroll_into_view_if_needed()
        page.locator('#enso-map').hover(position={"x": 120, "y": 100})
        wheel_before = page.eval_on_selector('#tab-elnino .content-page', 'e => e.scrollTop')
        page.mouse.wheel(0, 240)
        page.wait_for_timeout(250)
        wheel_after = page.eval_on_selector('#tab-elnino .content-page', 'e => e.scrollTop')
        check("a wheel over the map scrolls the content page", wheel_after > wheel_before,
              f"{wheel_before} -> {wheel_after}")
        # A layer picked by hand belongs to the view it was picked on. Pressing
        # "Food inflation" on Ocean must not paint Reported with it. Off-lens
        # layers sit in the "All layers" fold, so open it first.
        page.evaluate("() => document.querySelectorAll('#tab-elnino details.enso-all-layers').forEach(d => d.open = true)")
        page.locator('[data-native="enso-mode"][data-value="rtfp"]:visible').first.click()
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
              picked_here == 'rtfp' and after_switch == 'rain' and 'enso_mode=rtfp' not in url_after,
              f"{picked_here} -> {after_switch} {url_after}")
        page.eval_on_selector_all(".enso-idx", "els => els.forEach(e => e.open = true)")
        kinds = page.eval_on_selector_all(
            ".enso-idx-grp-h b", "els => els.map(e => e.textContent)")
        check("rows are grouped by averaging window", len(kinds) >= 3, str(kinds))
        # Stage J replaced the shared per-group peak with each index's own
        # published threshold, so there is deliberately no 100% bar any more and
        # nothing is scaled against a different index. The bar carries its
        # threshold and a track that spans a whole number of thresholds. The
        # selector takes the fill span directly: the page's number walker wraps
        # digits in spans, and the tick labels now hold digits.
        bars = page.evaluate("""() => [...document.querySelectorAll('.enso-idx-grp')].map(g =>
            [...g.querySelectorAll('.enso-idx-bar')].map(b => ({
                threshold: Number(b.dataset.threshold),
                width: parseFloat((b.querySelector(':scope > span') || {style:{}}).style.width),
                ticks: b.querySelectorAll('.enso-idx-tick').length})))""")
        check("every index bar is scaled to its own published threshold, not a shared peak",
              bool(bars) and any(g for g in bars) and all(
                  b["threshold"] and b["ticks"] == 2 and b["width"] == b["width"] and 0 <= b["width"] <= 100
                  for g in bars for b in g), str(bars))
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
        page.wait_for_selector('#subview-ensoharvest.active .enso-cal', state='attached')  # 2026-09-27: the calendar is a folded reference
        check("Harvests defaults to impact without the ocean backdrop",
              page.input_value('#enso-mode') == 'impact' and not page.is_checked('#enso-tog-sst'))
        print("\nscenario disclosure")
        tag = page.locator(".enso-tag-interpolation")
        check("modelled layer discloses interpolation at the observed ONI", tag.count() == 1 and "interpolated between" in tag.text_content())
        # Read expected values from the feeds rather than hardcoding them.
        feed = page.evaluate("async () => (await (await fetch('data/enso.json')).json()).data.latest")
        indices = page.evaluate("async () => (await (await fetch('data/enso_indices.json')).json()).data.indices")
        # The state sentence sits in the hero on Ocean and in the status row on
        # every other view (2026-09-22), so read both.
        hero = page.locator("#enso-hero").text_content() + page.locator("#enso-status-home").text_content()
        val = ("%+.2f" % feed["anom"]).replace("-", "−")
        check("hero leads with the CPC outlook and distinguishes the observed indices",
              all(t in hero for t in ("RONI", "ONI", "more than 90%"))
              and all(("%+.2f" % next(r["value"] for r in indices if r["key"] == key)).replace("-", "−") in hero
                      for key in ("roni", "oni")), hero[:240])

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
        weekly = page.evaluate("async () => (await (await fetch('data/enso.json')).json()).data.weekly_nino34")
        # The evidence sits in a closed fold, so read text_content, not rendered text.
        upwelling = " ".join((page.locator('.enso-mechanism-evidence section', has_text='The Humboldt upwelling is capped').text_content() or "").split())
        check("the CPC strength table prints the agency's percentages for every season", page.evaluate("""async () => {
            const T = (await (await fetch('data/enso_strengths.json')).json()).data;
            const rows = [...document.querySelectorAll('.enso-strength-table tbody tr')];
            // 2026-09-29: the header is a calendar (a year row over a middle-month row); seasons are the month row's columns.
            return rows.length > 0 && T.seasons.length === document.querySelectorAll('.enso-strength-table thead tr:last-child th').length - 1
                && rows.every(r => { const c = r.querySelector('th').textContent;
                    return [...r.querySelectorAll('td')].every((td, i) => (T.seasons[i].classes[c] ? T.seasons[i].classes[c] + '%' : '<1%') === td.textContent); });
        }"""))
        check("capped-upwelling evidence follows the weekly Niño 1+2 feed",
              ("%+.1f °C" % weekly["nino12_anom"]).replace("-", "−") in upwelling, upwelling)
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
            // The ONI plate lives in the Ocean view since 2026-09-22; the hero keeps the heading.
            const h = document.querySelector('.enso-oni-plate') || document.querySelector('#enso-hero');
            return !!h.querySelector('svg .enso-y-axis') &&
              [...h.querySelectorAll('.enso-threshold-label')].some(e => e.textContent.includes('+0.5 El Niño threshold')) &&
              [...h.querySelectorAll('.enso-threshold-label')].some(e => e.textContent.includes('−0.5 La Niña threshold'));
        }""")
        check("analog plate has fixed anchors and both labelled thresholds", axis and marker["ticks"] == ["−1", "0", "+4"])  # 2026-09-27: −1/+4 hold the CPC band

        # Inject an out-of-range current value: preserve fixed anchors and
        # historical geometry while printing the true value and overflow.
        base_past = marker["past"] if marker else None
        hist_max = page.evaluate(
            "async () => Math.max(...(await (await fetch('data/enso.json')).json())"
            ".data.history.map(r => Math.abs(r.anom)))")
        spike = round(hist_max + 2.5, 2)  # beyond the +4 anchor

        def spike_enso(route):
            # The page's freshness poll sends HEAD requests; they carry no body to rewrite.
            if route.request.method == "HEAD":
                return route.continue_()
            r = route.fetch()
            body = r.json()
            body["data"]["latest"]["anom"] = spike
            route.fulfill(json=body)

        page.route("**/data/enso.json", spike_enso)
        try:
            open_panel(page, base)
            page.wait_for_selector(".enso-strip", state="attached", timeout=20_000)
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
              and spiked["ticks"] == ["−1", "0", "+4"]
              and ("%.2f" % spike) in spiked["overflow"] and 'exceed' in spiked["overflow"],
              str(spiked))

        open_panel(page, base)   # back to real data for everything downstream
        page.wait_for_selector(".enso-strip", state="attached", timeout=20_000)



        page.evaluate("showTab('ensoharvest')")
        # 2026-09-29 audit: the fitted table sits behind "Show per country"; open it to read rendered text.
        page.locator('#enso-harvest-fig').evaluate('e => e.open = true')
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
        sl, colour = slope_of(cell), cell.get_attribute('data-direction')
        check("El Nino: table records the implied yield direction",
              colour == ("rise" if sl > 0 else "fall"), f"slope {sl}, colour={colour}")

        page.select_option("#enso-level", "-1.5")
        page.wait_for_timeout(300)
        cell = page.locator("#enso-detail tbody tr td.num").nth(1)
        sl, colour = slope_of(cell), cell.get_attribute('data-direction')
        check("La Nina: positive slope records a yield fall",
              colour == ("fall" if sl > 0 else "rise"), f"slope {sl}, colour={colour}")
        check("the table states how La Nina reverses the slope sign",
              "La Niña reverses the slope sign" in page.locator("#enso-detail").inner_text())
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

        print("\nHarvests map: one harvest at a time, callouts and two kinds of lines (2026-09-29 audit, second pass)")
        # The circles gave way to text callouts with a tonnes bar; only the chosen harvest's lines draw, in two inks by role.
        page.evaluate("showTab('ensoharvest')")
        page.wait_for_selector('#subview-ensoharvest.active .enso-subview-meta')
        page.wait_for_function("() => document.querySelectorAll('#enso-map path.enso-flow').length > 0", timeout=20_000)
        page.wait_for_timeout(900)
        marks = page.evaluate("""async () => {
            const O = (await (await fetch('data/enso_outlook.json')).json()).data;
            const fk = v => { const a = Math.abs(v), g = v > 0 ? '+' : v < 0 ? '\u2212' : ''; return a >= 1000 ? g + (a / 1000).toFixed(1) + ' Mt' : g + Math.round(a) + ' kt'; };
            const rows = O.rows_all.filter(r => r.status === 'shown' && !r.in_season && typeof r.change_kt_record === 'number' && Math.abs(r.change_kt_record) >= 150)
                .sort((a, b) => Math.abs(b.change_kt_record) - Math.abs(a.change_kt_record));
            const calls = [...document.querySelectorAll('#enso-map .enso-hcall')];
            const chips = [...document.querySelectorAll('#enso-map .enso-follow [data-story]')];
            const top = rows[0], w = O.who_pays.find(x => x.iso === top.iso && x.crop === top.crop);
            const buyerLabs = [...document.querySelectorAll('#enso-map .enso-flab-b.is-buy')].map(e => e.textContent);
            return {annos: document.querySelectorAll('.enso-anno').length, circles: document.querySelectorAll('#enso-map path.enso-shift').length,
                    calls: calls.length, want: rows.length, on: document.querySelectorAll('#enso-map .enso-hcall.is-on').length,
                    plateNums: rows.every(r => calls.some(c => c.textContent.includes(fk(r.change_kt_record)) && c.textContent.includes(r.harvest))),
                    chips: chips.length, pressed: chips.filter(c => c.getAttribute('aria-pressed') === 'true').map(c => c.dataset.story),
                    topKey: top.iso + '/' + top.crop, oni: (document.querySelector('#enso-map .enso-follow') || {}).textContent || '',
                    wcOni: O.cases[O.who_pays_case].oni,
                    lost: document.querySelectorAll('#enso-map path.enso-flow.is-lost').length, arcs: document.querySelectorAll('#enso-map path.enso-flow').length,
                    wantLost: w ? w.buyers.length : 0, labelled: !w || w.buyers.every(b => buyerLabs.some(t => t.includes(fk(-b.kt)))),
                    oneLine: [...document.querySelectorAll('#enso-map .enso-hcall-t')].every(l => l.getBoundingClientRect().height < 20),
                    key: (document.getElementById('enso-legend') || {}).textContent || ''};
        }""")
        check("Harvests draws one callout per 2027 harvest with the plate's numbers, no circles, the largest harvest chosen",
              marks["annos"] == 0 and marks["circles"] == 0 and marks["calls"] == marks["want"] == marks["chips"] and marks["on"] == 1
              and marks["plateNums"] and marks["oneLine"] and marks["pressed"] == [marks["topKey"]]
              and ("ONI +%.1f" % marks["wcOni"]) in marks["oni"], str({k: v for k, v in marks.items() if k != 'key'}))
        check("only the chosen harvest's lines: supply at risk to each labelled buyer, and where buyers could turn, method in the key",
              marks["lost"] == marks["wantLost"] and marks["arcs"] > marks["lost"] and marks["labelled"]
              and "where trade could come from, not a forecast of trade" in marks["key"] and "Two inks by role" in marks["key"], str({k: marks[k] for k in ('lost', 'wantLost', 'arcs', 'labelled')}))
        # 2026-09-29: "no other usual supplier" is said once, on the producer callout, not under each buyer.
        said = page.evaluate("""() => {
            const t = [...document.querySelectorAll('#enso-map .enso-flab-b')];
            return {once: t.filter(e => /no other usual supplier/.test(e.textContent)).length, onCall: t.filter(e => /no other usual supplier/.test(e.textContent) && e.classList.contains('is-on')).length,
                    buyers: t.filter(e => e.classList.contains('is-buy') && /no other usual|no import record/.test(e.textContent)).length};
        }""")
        check("Harvests says 'no other usual supplier' once, on the producer callout, not under each buyer",
              said["once"] == 1 and said["onCall"] == 1 and said["buyers"] == 0, str(said))
        # 2026-09-30 (V2 audit, "show a zoomed out map first and then you can click to zoom in"): the map opens on the
        # world view with every callout on screen; a chip click frames that harvest; Reset returns to the world view.
        world = page.evaluate("""async () => {
            const box = document.getElementById('enso-map').getBoundingClientRect();
            const seen = () => [...document.querySelectorAll('#enso-map .enso-hcall')].filter(c => { const r = c.getBoundingClientRect(); return r.width > 0 && getComputedStyle(c.closest('.enso-flab-b') || c).visibility !== 'hidden' && r.left >= box.left - 1 && r.right <= box.right + 1 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1; }).length;
            const all = document.querySelectorAll('#enso-map .enso-hcall').length, before = seen();
            document.querySelectorAll('#enso-map .enso-follow [data-story]')[0].click();
            await new Promise(r => setTimeout(r, 900));
            const zoomed = seen();
            document.querySelector('#enso-mapwrap [data-z="0"]').click();
            await new Promise(r => setTimeout(r, 1200));
            return {all, before, zoomed, after: seen()};
        }""")
        check("Harvests opens on the world view with every callout on screen; a chip zooms in, Reset returns",
              world["all"] > 1 and world["before"] == world["all"] and world["zoomed"] < world["all"] and world["after"] == world["all"], str(world))
        switched = page.evaluate("""async () => {
            const O = (await (await fetch('data/enso_outlook.json')).json()).data;
            const w = O.who_pays.filter(x => (x.buyers || []).length).slice(-1)[0];
            document.querySelector(`#enso-map .enso-follow [data-story="${w.iso}/${w.crop}"]`).click();
            await new Promise(r => setTimeout(r, 1200));
            return {lost: document.querySelectorAll('#enso-map path.enso-flow.is-lost').length, want: w.buyers.length,
                    on: (document.querySelector('#enso-map .enso-hcall.is-on') || {}).textContent || '', name: w.iso};
        }""")
        check("a harvest chip swaps the lines to that harvest alone", switched["lost"] == switched["want"] and switched["on"] != "", str(switched))
        flows = page.evaluate("""async () => {
            const O = (await (await fetch('data/enso_outlook.json')).json()).data;
            const R = (await (await fetch('data/trade_restrictions.json')).json()).data;
            const d = await ensoTradeFlows('compensate'), a = await ensoTradeFlows('atrisk');
            const shortOf = new Set(O.rows_all.filter(r => r.status === 'shown' && r.change_pct_record < 0).map(r => r.iso + '/' + r.crop));
            const rep = d.flows.filter(f => f.role === 'replace'), lost = d.flows.filter(f => f.role === 'lost');
            const wantLost = O.who_pays.reduce((n, w) => n + (w.buyers || []).length, 0);
            const perBuyer = {}; rep.forEach(f => { const k = f.to + '/' + f.crop; perBuyer[k] = (perBuyer[k] || 0) + 1; });
            return {shape: ['mode', 'oni', 'year', 'flows', 'buyers'].every(k => k in d) && d.oni === O.cases[O.who_pays_case].oni,
                    lost: lost.length === wantLost, noShort: rep.every(f => !shortOf.has(f.from + '/' + f.crop)),
                    cap: Object.values(perBuyer).every(n => n <= 2), noBan: rep.every(f => !R.some(m => m.iso === f.from && /ban/i.test(m.measure || '') && m.status !== 'historical' && (f.crop === 'corn' ? /maize|corn/i : new RegExp(f.crop, 'i')).test(m.commodity || ''))),
                    atrisk: a && a.mode === 'atrisk' && a.flows.every(f => f.role === 'export'),
                    // 2026-09-30 logic check: a replacement ships at least 10 kt a year to that buyer (no 7 kt Swiss record).
                    floor: rep.every(f => f.t >= 10000 && f.share_pct >= 1)};
        }""")
        check("ensoTradeFlows: one lost arc per named buyer, replacements capped and never from a short or banned exporter, atrisk mode ready",
              all(flows.values()), str(flows))
        page.evaluate("document.querySelector('#enso-map path.enso-flow:not(.is-lost)').dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: 10, clientY: 10}))")
        card = page.locator('#enso-map .flow-pop .flow-card')
        check("a where-buyers-could-turn line opens the shared flow card on the El Niño map",
              card.count() == 1 and "not a forecast of trade" in card.inner_text(), card.inner_text()[:160] if card.count() else "no card")
        page.evaluate("document.querySelectorAll('#enso-map .leaflet-popup-close-button').forEach(b => b.click())")

        print("\nagency bulletins in the news view")
        # 2026-09-24: the official outlooks plate lives on Ocean now.
        open_panel(page, base, "elnino")
        page.wait_for_selector(".enso-bul", timeout=20_000)
        ags = page.eval_on_selector_all(".enso-bul-ag", "e => e.map(x => x.textContent)")
        check("BoM weekly appears in news", any("BoM" in a for a in ags), str(ags))
        check("more than one agency is represented", len(set(ags)) >= 2, str(set(ags)))
        # 2026-09-29 (audit, "declutter"): the unreached-sources note sits in the strength plate's closed notes fold.
        skip_note = " ".join((page.locator(".enso-bul-skip").text_content() or "").split())
        check("the frozen NOAA ENSO blog is named without a derived age",
              "NOAA’s ENSO blog is left out: its latest post is 25 June 2025." in skip_note
              and "days old" not in skip_note)
        check("unreached agencies are named locally without a partial-feed warning",
              page.evaluate("""async () => {
                const feed = await (await fetch('data/enso_bulletins.json')).json();
                const names = {jma_monthly:'JMA', enfen_monthly:'ENFEN', iri_monthly:'IRI', bom_weekly:'BoM', cpc_monthly:'NOAA CPC', wmo_news:'WMO'};
                const skip = document.querySelector('.enso-bul-skip').textContent;
                return (feed.data.unavailable || []).filter(u => u.key !== 'climate_gov_enso_blog')
                    .every(u => skip.includes((names[u.key] || u.key.replaceAll('_', ' ')) + ' not reached this cycle'))
                    && !document.querySelector('#enso-failures').textContent.includes('source reports partial');
              }"""))
        hrefs = page.eval_on_selector_all(
            ".enso-news-t", "e => e.map(x => x.getAttribute('href') || '')")
        check("no feed link escapes the http(s) allow-list",
              all(h.startswith("http") for h in hrefs) if hrefs else True, str(hrefs[:3]))

        print("\nthe Panama slot limits sit in true time")
        page.goto(f"{base}/index.html?tab=ensowater", wait_until="networkidle")
        page.wait_for_selector(".enso-pan-since", timeout=20_000)
        page.wait_for_timeout(2000)
        pan = page.evaluate(PANAMA_PROBE)
        check("every dated slot advisory is a step on the Panama plate's slot ladder",
              bool(pan) and pan["steps"] == pan["want"], str(pan and (pan["steps"], pan["want"])))
        check("the Panama chart sets the last El Niño against this one",
              bool(pan) and "2023-24" in pan["years"] and "2026" in pan["years"], str(pan and pan["years"]))
        check("the caption says where the 2023-24 line stops",
              bool(pan) and "last one it dates" in (pan["cap"] or ""), (pan or {}).get("cap", "")[:160])

        # Back to the view the layout checks below were written against — they
        # measure the news rail and bulletin strip, which only exist there.
        # 2026-09-24: the official outlooks plate lives on Ocean now.
        open_panel(page, base, "elnino")
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
            inner_scrollers = page.evaluate("""() => [...document.querySelectorAll('#tab-elnino .content-page *')]
                .filter(e => e.tagName !== 'SELECT' && e.getClientRects().length
                    && /auto|scroll/.test(getComputedStyle(e).overflowY))
                .map(e => e.id || e.className)""")
            check(f"{tab} has no inner vertical scroll containers", not inner_scrollers, str(inner_scrollers))
            print(f"  390px {tab}: " + (", ".join(overflow) if overflow else "no horizontal overflow"))
            mobile_overflow.extend(f"{tab}: {s}" for s in overflow)
        check("no horizontal overflow", desktop_ok and not mobile_overflow, "; ".join(mobile_overflow))

        check("every element in the El Niño tab has square corners", not rounded, str(rounded))
        page.set_viewport_size({"width": 1440, "height": 1000})
        open_panel(page, base, "ensomech")
        page.wait_for_selector('#pac-stack')
        page.wait_for_timeout(300)
        # 2026-09-27: the engraved states live inside the Pacific explainer, blended by the ONI; the five
        # steps, their readings and the evidence are in the same plate.
        check("mechanism alias activates Ocean and scrolls to the Pacific explainer",
              page.locator('#viewbtn-elnino').get_attribute('aria-selected') == 'true'
              and page.locator('#subview-elnino').evaluate("e => e.classList.contains('active')")
              and abs(page.locator('#enso-pacific').bounding_box()['y']
                      - page.locator('#tab-elnino .content-page').bounding_box()['y'] - 120) < 5)
        ticks = page.locator('#enso-pacific [data-ruler]')
        check("five named longitude tabs replace the scroll tour", ticks.count() == 5
              and page.locator('.tour-step').count() == 0 and page.locator('.enso-mechanism-plate').count() == 0
              and ticks.evaluate_all("els => els.every(e => e.title && e.querySelector('.enso-ruler-longitude') && e.querySelector('.enso-ruler-name'))"))
        ticks.first.focus()
        page.keyboard.press('ArrowRight')
        check("step arrow key updates focus and visible text",
              ticks.nth(1).get_attribute('aria-selected') == 'true'
              and page.locator('#enso-step-soi').is_visible()
              and not page.locator('#enso-step-trades').is_visible())
        page.keyboard.press('End')
        check("step End key selects eastern upwelling, the plate showing today's El Niño state",
              ticks.last.get_attribute('aria-selected') == 'true'
              and page.locator('#enso-step-upwelling').is_visible()
              and page.locator('#pac-stack').get_attribute('data-state') == 'elnino')
        check("three registered illustrations are stacked; at today's ONI the El Niño plate is the one shown", page.evaluate("""() => {
            const layers = [...document.querySelectorAll('#pac-stack > .enso-raster')];
            const imgs = layers.map(l => l.querySelector('img'));
            const el = document.querySelector('#pac-stack [data-pacific-state="elnino"]');
            return layers.length === 3 && ['walker', 'elnino', 'lanina'].every((n, i) => imgs[i].src.includes(n))
                && getComputedStyle(el).opacity === '1' && el.classList.contains('is-dom') && el.getAttribute('aria-hidden') === 'false'
                && imgs.every(img => img.complete && img.naturalWidth > 0 && img.loading === 'eager'
                    && img.getAttribute('width') === '1536' && img.getAttribute('height') === '1024');
        }"""))
        labels = []
        for button, state in (('[data-pac-set="0"]', 'normal'), ('[data-pac-set]:not([data-pac-set="0"]):not([data-pac-set="now"])', 'lanina')):
            page.locator(button).click()
            page.wait_for_timeout(1100)
            layer = page.locator(f'#pac-stack [data-pacific-state="{state}"]')
            labels.append(layer.get_attribute('aria-label'))
            check(f"{state} illustration takes over when the dial moves there", layer.evaluate("""(r, state) =>
                r.classList.contains('is-dom') && getComputedStyle(r.querySelector('.enso-raster-label')).visibility === 'visible'
                    && [...r.parentElement.querySelectorAll('.enso-raster:not(.is-dom)')].every(o => getComputedStyle(o.querySelector('.enso-raster-label')).visibility === 'hidden')
                    && (state === 'normal' ? [...r.parentElement.querySelectorAll('.enso-raster:not(.is-dom)')].every(o => getComputedStyle(o).opacity === '0')
                        : getComputedStyle(r).opacity === '1')""", state))
        check("each state carries its own description", len(set(labels)) == 2 and all(labels))
        for i in range(5):
            ticks.nth(i).click()
            check(f"step {i + 1} moves the rectangle to its place on the La Niña plate", page.evaluate("""i => {
                const rect = document.querySelector('#pac-stack .enso-ruler-highlight');
                const lanina = [[18,34,92,42],[5,6,88,40],[10,38,42,50],[3,5,32,38],[78,38,92,62]];
                const values = rect.style.transform.match(/-?\\d*\\.?\\d+/g).map(Number);
                const box = lanina[i];
                return JSON.stringify(values) === JSON.stringify([box[0], box[1], (box[2] - box[0]) / 100, (box[3] - box[1]) / 100])
                    && document.getElementById('pac-stack').dataset.step === String(i);
            }""", i))
        check("illustration overlays run only transform and opacity animations", page.evaluate("""() => {
            const animations = document.querySelector('.pac-plate').getAnimations({subtree:true})
                .filter(a => a instanceof CSSAnimation && a.playState === 'running'
                    && a.effect.target && a.effect.target.closest('.enso-flow'));
            const metadata = new Set(['offset', 'computedOffset', 'easing', 'composite']);
            const properties = animations.flatMap(a => a.effect.getKeyframes().flatMap(frame =>
                Object.keys(frame).filter(key => !metadata.has(key))));
            return animations.length >= 8 && properties.length > 0
                && properties.every(key => key === 'transform' || key === 'opacity');
        }"""))
        page.emulate_media(reduced_motion='reduce')
        ticks.nth(2).click()
        page.wait_for_timeout(700)
        check("reduced motion leaves no running animations in the Pacific explainer", page.evaluate("""() =>
            document.querySelector('.pac-plate').getAnimations({subtree:true})
              .filter(a => a instanceof CSSAnimation && a.playState === 'running').length === 0"""))
        page.emulate_media(reduced_motion='no-preference')
        for width, height in ((1440, 900), (1280, 800)):
            page.set_viewport_size({"width": width, "height": height})
            check(f"Pacific explainer fits a laptop at {width}px: illustration beside the readout and the selected step",
                  page.evaluate("""() => {
                    const stack = document.getElementById('pac-stack').getBoundingClientRect();
                    const side = document.querySelector('.pac-read').getBoundingClientRect();
                    const card = document.querySelector('.pac-stepcard').getBoundingClientRect();
                    return stack.height <= 620 && side.left >= stack.right && card.left >= stack.right && card.height > 0
                        && document.getElementById('enso-view-nav').getBoundingClientRect().height === 40;
                  }"""))
            # 2026-09-29 round 2 (owner: "this takes too much space", "try condense", "the top explanation cuts of the bottom"):
            # the explainer is a full-width row above the figure, the readout column ends before the figure does, the play
            # control shares the tabs' row, and the Nino-region and past-peak labels each sit on one line without overlap.
            page.wait_for_timeout(300)
            check(f"Pacific explainer at {width}px: intro row above the figure, readout fits beside it, compact tab row, one-line labels", page.evaluate("""() => {
                const p = document.querySelector('#enso-pacific .pac-plate'), what = p.querySelector(':scope > .pac-what'), read = p.querySelector('.pac-read');
                const stack = document.getElementById('pac-stack').getBoundingClientRect(), r = read.getBoundingClientRect(), m = p.querySelector('.pac-main').getBoundingClientRect();
                const tabs = [...p.querySelectorAll('.pac-main > .enso-ruler [role="tab"]')].map(t => t.getBoundingClientRect()), play = p.querySelector('[data-pac-story]').getBoundingClientRect();
                const one = els => { const rs = els.map(e => e.getBoundingClientRect()).sort((a, b) => a.left - b.left); return rs.length > 1 && rs.every(x => Math.abs(x.top - rs[0].top) < 2) && rs.every((x, i) => !i || x.left >= rs[i - 1].right); };
                return !!what && !read.querySelector('.pac-what') && what.getBoundingClientRect().bottom <= stack.top && r.bottom <= m.bottom + 1
                    && tabs.length === 5 && tabs.every(t => Math.abs(t.top - tabs[0].top) < 2 && t.height <= 48) && Math.abs(play.top + play.height / 2 - (tabs[0].top + tabs[0].height / 2)) < 12
                    && one([...p.querySelectorAll('.pac-lane .pac-box em')]) && one([...p.querySelectorAll('.pac-peak b')]);
            }"""))
        page.set_viewport_size({"width":390,"height":844})
        check("phone switcher stays one 36px row and the explainer stacks without overflow", page.evaluate("""() => {
            const root = document.querySelector('#tab-elnino .content-page'), bar = document.getElementById('enso-view-nav');
            const stack = document.getElementById('pac-stack').getBoundingClientRect(), side = document.querySelector('.pac-read').getBoundingClientRect();
            return bar.getBoundingClientRect().height === 36 && side.top >= stack.bottom && root.scrollWidth <= root.clientWidth;
        }"""))
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator('#viewbtn-ensowater').click()
        page.wait_for_selector('#subview-ensowater.active .enso-pan-since')
        page.locator('#viewbtn-ensowater').focus()
        page.keyboard.press('ArrowRight')
        page.wait_for_selector('#subview-ensomoney.active #enso-c-record')
        check("view switcher works with clicks and arrow keys",
              page.locator('#viewbtn-ensomoney').get_attribute('aria-selected') == 'true')

        print("\nstage A shared structure and lens contracts")
        open_panel(page, base)
        lens_results, headings, frames, lens_texts, spill = [], [], [], [], []
        heights = {}
        page.evaluate("window._stageAMap = document.getElementById('enso-map'); window._stageAMapId = window._stageAMap._leaflet_id")
        for tab, mode in (("elnino", "sst"), ("ensoharvest", "impact"), ("ensowater", "none"), ("ensomoney", "staple"), ("ensolive", "rain")):
            page.evaluate("tab => showTab(tab)", tab)
            page.wait_for_selector(f'#subview-{tab}.active .enso-subview-meta')
            lens_results.append(page.input_value('#enso-mode') == mode
                and page.is_checked('#enso-tog-sst') == (tab == 'elnino')
                and page.is_checked('#enso-tog-lanes') == (tab == 'ensowater')
                and page.is_checked('#enso-tog-alerts') == (tab == 'ensolive'))
            lens_texts.append(page.locator('#tab-elnino').inner_text())
            if tab == "ensomoney":
                legend = page.locator('#enso-legend').text_content()
                tag = page.locator('#enso-maptag').text_content()
                # 2026-09-26: staple prices (FAO GIEWS FPMA) over the published harvest effect. The key states sizes,
                # the harvest encoding and the countries with no series; the map tag counts from the same data.
                counts = page.evaluate("""async () => {
                    const F = (await (await fetch('data/fpma_prices.json')).json()).data;
                    const R = (await (await fetch('data/enso_regions.json')).json()).data.regions;
                    const tele = [...new Set(R.flatMap(r => r.iso3 || []))];
                    const has = tele.filter(i => F[i] && (typeof F[i].staple_price_real_yoy_pct === 'number' || typeof F[i].staple_price_yoy_pct === 'number'));
                    return [has.length, tele.length, tele.filter(i => !has.includes(i)).sort().join(', ')];
                }""")
                # 2026-09-29 audit ("doesn't really show food prices"): the map leads with real staple prices since March in
                # the high-effect countries (data/enso_price_outlook.json); its one-line tag states the price model's verdict
                # from the file. The older 37-country layer is the "Year on year" view and keeps its checks there.
                pout = page.evaluate("async () => (await (await fetch('data/enso_price_outlook.json')).json()).data")
                check("Prices map leads with staple prices since March and states the model verdict from the data",
                      'Staple price since March' in legend and tag.startswith('After inflation, Mar')
                      and (pout['model_status'] != 'no_skill' or f"tested on {pout['skill']['n_events']} past El Niños, no better than replaying them: not shown" in tag), tag)
                page.click('[data-pview="change"]'); page.wait_for_timeout(300)
                legend = page.locator('#enso-legend').text_content()
                tag = page.locator('#enso-maptag').text_content()
                check("staple legend states sizes, the harvest encoding and the countries without a series",
                      all(t in legend for t in ('Staple price, year on year', 'Blue falling', 'ochre to red rising', 'Dashed outline'))
                      and ('No staple series' in legend) == bool(counts[2]), str(counts))
                check("staple map tag counts the countries with a series from the data", tag.startswith(f"{counts[0]} of {counts[1]} El Niño countries have staple prices"), tag)
                page.click('[data-pview="since"]'); page.wait_for_timeout(300)
            headings.append(page.locator('#tab-elnino h2:visible').count())
            # 2026-09-24: a no-wrap table once pushed the Reported ledger 557px past its plate.
            heights[tab] = page.evaluate("() => document.querySelector('#tab-elnino .content-page').scrollHeight")
            spill.append(page.evaluate("""() => { const bad = []; document.querySelectorAll('#tab-elnino .enso-plate').forEach(pl => { if (!pl.offsetParent) return;
                const pr = pl.getBoundingClientRect(); pl.querySelectorAll('table, canvas, p').forEach(e => { const r = e.getBoundingClientRect(); if (r.width && r.right > pr.right + 2) bad.push(((pl.querySelector('.enso-plate-t') || {}).textContent || '?') + ' ' + e.tagName); }); });
                return bad; }"""))
            frames.append(page.evaluate("""() => [...document.querySelectorAll('#tab-elnino .enso-plate[data-kind], #enso-mapwrap[data-kind]')].every(e =>
                getComputedStyle(e).borderTopStyle === (['modelled','estimated'].includes(e.dataset.kind) ? 'dashed' : 'solid'))"""))
        check("each view applies its layer and overlay defaults", all(lens_results), str(lens_results))
        check("one visible Instrument Serif H2 per view", headings == [1] * 5
              and page.locator('#tab-elnino h2:visible').evaluate("e => getComputedStyle(e).fontFamily.includes('Instrument Serif')"), str(headings))
        check("dashed frames mark the site's model and estimates only; observed and published are solid", all(frames), str(frames))
        check("no table, chart or paragraph runs past its plate on any lens", not any(spill), str([x for x in spill if x]))
        # 2026-09-24 (court): the lenses may not grow unnoticed. Ceilings sit about 5% above the
        # heights at 1440x1000 on 24 Sep 2026; adding a plate means removing or folding another.
        # Lowered 24 Sep after the duplicate displays were removed (Ocean 4.2k, Shipping 6.9k, Prices 4.9k at 1440x900).
        # 2026-09-24: Harvests gains the published-estimates plate, Shipping the freight plate.
        # 2026-09-27: Ocean gains the Pacific explainer, which now carries the engraved states and the five steps (5.0k).
        # 2026-09-27: Prices folds the published models into the past-price plate (5.5k -> 4.7k); its cap drops to 5000.
        # 2026-09-27 (later): Shipping folds the ordinal slot chart into the monthly Panama chart and the freight plate
        # into the river chain (7.4k -> 6.9k), so its cap drops to 7300; Prices gains the southern-Africa maize analog
        # from FPMA (+0.6k) and pays part of it back: prices beside their sparklines, a one-line map key (5.26k), cap 5400.
        CEIL = {'elnino': 5250, 'ensoharvest': 5000, 'ensowater': 7300, 'ensomoney': 5400, 'ensolive': 6100}
        check("no lens grows past its height ceiling", all(heights.get(k, 0) <= v for k, v in CEIL.items()), str(heights))
        # 2026-09-24: the Ocean lens leads with a dated calendar joined from the other lenses' data.
        page.evaluate("showTab('elnino')")
        page.wait_for_selector('#subview-elnino.active .enso-next12-bar')
        check("Ocean opens with the map and the ONI record; the next twelve months has each line typed and linked to its lens", page.evaluate("""async () => {
            const O = (await (await fetch('data/enso_outlook.json')).json()).data;
            const items = [...document.querySelectorAll('.enso-next12 li')];
            const first = document.querySelector('#subview-elnino > *:not([hidden])');
            const harv = O.rows_all.filter(r => r.status === 'shown' && !r.in_season && Math.abs(r.change_kt_record || 0) >= 150);
            const text = document.querySelector('.enso-next12').textContent;
            // 2026-09-29 (audit, owner: "second block should be ... 'What is El Nino'"): map, the Pacific explainer, CPC's odds,
            // the dated twelve months, then the ONI record. Round 2 (owner: "switch position of This El Niño against the five
            // strongest since 1950 with The next twelve months"): the ONI record is fourth, the twelve months fifth.
            const second = first && first.nextElementSibling, third = second && second.nextElementSibling, fourth = third && third.nextElementSibling, fifth = fourth && fourth.nextElementSibling;
            return first && first.classList.contains('enso-mapgrid') && second && second.id === 'enso-pacific' && third && third.id === 'enso-strengths' && fourth && fourth.classList.contains('enso-oni-plate') && fifth && fifth.id === 'enso-next12'
                && items.length >= 6 && items.every(li => /^is-(forecast|published|modelled|precedent)$/.test(li.className) && li.querySelector('[data-goto-lens]'))
                // 2026-09-27: the fitted harvests are one pointer row naming each (sizes live on Harvests).
                && harv.every(r => text.includes(r.iso === 'USA' ? 'United States' : r.iso === 'ZAF' ? 'South Africa' : ''))
                && (text.match(/harvests the model says El Niño moves/g) || []).length === 1
                && (text.match(/more maize from abroad/g) || []).length <= 1;
        }"""))
        # The month-axis timeline: one bar per line, each bar inside its own row (the tab's 44px
        # touch minimum once made every bar 44px tall and pushed them off their labels).
        check("next-twelve-months timeline draws one bar per line, each inside its row", page.evaluate("""() => {
            const bars = [...document.querySelectorAll('.enso-next12-bar')], items = document.querySelectorAll('.enso-next12 li');
            return bars.length === items.length && bars.every(b => { const r = b.getBoundingClientRect(), row = b.parentElement.getBoundingClientRect(); return r.top >= row.top - 1 && r.bottom <= row.bottom + 1; });
        }"""))
        # 2026-09-29 audit (Ocean): read everything from the data files, never from typed numbers.
        check("Ocean audit: the strip says when the newest El Niño feed was collected", page.evaluate("""async () => {
            // The page's rule: every loaded feed named enso*, sst_*, rain_* or seasonal_outlook, except the hand-run forecast track record.
            const names = ['enso','enso_exposure','enso_model','enso_regions','enso_lanes','enso_corridors','enso_econ','enso_mechanism','enso_indices','enso_bulletins','sst_anomaly','enso_news','enso_outlook','enso_gauges','enso_situation','enso_strengths','enso_ports','enso_hindcast','enso_freight','enso_price_analogs','enso_published_effects','sst_composites','seasonal_outlook','rain_anomaly','enso_past_events','sst_months','rain_months','enso_recent_events','enso_auto_events','enso_outlook_events','enso_price_outlook'];
            const ds =(await Promise.all(names.map(n => fetch('data/' + n + '.json').then(r => r.json()).catch(() => null)))).filter(Boolean).map(j => new Date(j._meta.generated_at || j._meta.generated)).filter(d => !isNaN(d));
            const t = (document.querySelector('#enso-status-home .enso-now-upd') || {}).textContent || '';
            const hm = d => String(d.getUTCHours()).padStart(2, '0') + ':' + String(d.getUTCMinutes()).padStart(2, '0');
            const newest = new Date(Math.max(...ds));
            return /^Data updated/.test(t) && / UTC$/.test(t.trim()) && t.includes(hm(newest));
        }"""))
        check("Ocean audit: 'What is El Niño' follows the map, explains in a lede and four points, and stays near 1000 px", page.evaluate("""() => {
            const p = document.querySelector('#enso-pacific .pac-plate'), t = p && p.querySelector('.enso-plate-t');
            return !!t && t.textContent === 'What is El Niño' && !!p.querySelector('.pac-what-lede') && p.querySelectorAll('.pac-what li').length === 4
                && p.querySelectorAll(':scope > details').length === 1 && p.getBoundingClientRect().height <= 1060
                && [...p.querySelectorAll('.pac-how-src a')].every(a => /^https:/.test(a.href));
        }"""))
        check("Ocean audit: the strength table reads as a calendar and the agencies are one compact row", page.evaluate("""async () => {
            const T = (await (await fetch('data/enso_strengths.json')).json()).data;
            const top = T.roni_outlook.slice().sort((a, b) => b.median - a.median)[0];
            const pk = document.querySelector('.enso-strength-table thead th.is-peakcol');
            const plate = document.querySelector('.enso-strength-plate'), rows = plate.querySelectorAll('.enso-bul > .enso-bul-row');
            return !!document.querySelector('.enso-strength-table .enso-cal-y') && !!document.querySelector('.enso-strength-table th.is-ybreak')
                && !!pk && pk.title.startsWith(top.season) && rows.length >= 2 && [...rows].every(r => r.getBoundingClientRect().height < 40)
                && !!plate.querySelector('details.enso-strength-notes .enso-bul-skip');
        }"""))
        check("Ocean audit: the hero's CPC odds come from the strength table, not a typed row", page.evaluate("""async () => {
            const T = (await (await fetch('data/enso_strengths.json')).json()).data;
            const top = T.roni_outlook.slice().sort((a, b) => b.median - a.median)[0], s = T.seasons.find(x => x.season === top.season);
            const t = document.querySelector('#enso-agency-status').textContent;
            return t.includes(s.classes['very strong El Niño'] + '% chance') && !/beats every event/.test(t)
                && (!T.roni_record || top.median <= T.roni_record.value || t.includes(T.roni_record.value.toFixed(2)));
        }"""))
        check("Ocean audit: the twelve months adds the map's forecasts by region and stays within 14 rows", page.evaluate("""async () => {
            const J = await (await fetch('data/enso_outlook_events.json')).json(), E = (J.data || J).events || [];
            const rows = document.querySelectorAll('#enso-next12 .enso-next12-rows > .enso-next12-row'), text = document.querySelector('#enso-next12 .enso-next12').innerHTML;
            const linked = E.filter(e => e.source && e.source.url && text.includes(e.source.url.replace(/&/g, '&amp;'))).length;
            return rows.length <= 14 && document.querySelectorAll('.enso-next12 li.is-modelled').length >= 2 && /modelled: (drier|wetter)/.test(text) && linked >= 10;
        }"""))
        check("Ocean audit: the forecast plate gives the September forecasts' track record from the data file", page.evaluate("""async () => {
            const F = (await (await fetch('data/enso_forecast_skill.json')).json()).data, t = (document.querySelector('.enso-oni-plate .enso-skill') || {}).textContent || '';
            const title = document.querySelector('.enso-oni-plate .enso-plate-t').textContent;
            return !/ONI|RONI/.test(title) && t.includes(F.summary.mae.toFixed(2)) && t.includes(F.summary.years + ' winters') && t.includes('in ' + F.summary.within_0_5 + ')')
                && (!F.current || !!document.querySelector('.enso-oni-plate .enso-skill-mark'));
        }"""))
        check("one persistent map instance across all five views", page.evaluate("""() =>
            document.querySelectorAll('#enso-map').length === 1 && document.getElementById('enso-map') === window._stageAMap
            && window._stageAMap._leaflet_id === window._stageAMapId
            && document.querySelectorAll('#enso-map .leaflet-map-pane').length === 1"""))
        check("El Niño user-facing text contains no hex colour codes",
              not any(re.search(r'#[0-9a-f]{6}', text, re.I) for text in lens_texts))
        option_texts = page.locator('#enso-country option').all_text_contents()
        check("France is named in the country selector", "France" in option_texts and "FRA" not in option_texts)
        # 2026-09-22: the limits fold shows once, on Ocean; the state sentence
        # follows the reader to every view (hero on Ocean, status row elsewhere).
        # 2026-09-24: the limits describe the fitted harvest layer and price
        # evidence, so the fold moved from Ocean to Harvests.
        page.evaluate("showTab('ensoharvest')")
        page.wait_for_selector('#subview-ensoharvest.active .enso-subview-meta')
        # 2026-09-24 (court): off Ocean the state follows as one line (#enso-status-short) with a link back.
        limits_ocean = page.locator('#enso-limits').is_visible() and page.locator('#enso-status-home #enso-status-short').is_visible()
        page.evaluate("showTab('ensowater')")
        page.wait_for_selector('#subview-ensowater.active .enso-subview-meta')
        limits_elsewhere = (not page.locator('#enso-limits').is_visible()) and page.locator('#enso-status-home #enso-status-short').is_visible() \
            and page.locator('#enso-status-home #enso-status-short').count() == 1 and 'more than 90%' in page.locator('#enso-status-home').inner_text()
        check("limits show once, on Harvests, and the state sentence follows every view",
              limits_ocean and limits_elsewhere
              and page.locator('#enso-limits').evaluate("e => !e.closest('.subview')"))
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
        # 2026-09-27: zero is a dark neutral that recedes; falls step up in ochre, rises in teal
        # (2026-09-28 audit: green rises failed protan/deutan contrast against the weak ochre step).
        check("yield ramp has fixed ochre, dark neutral and teal anchors",
              palette[0] == 'rgb(224, 103, 60)' and palette[len(palette)//2] == 'rgb(78, 80, 84)' and palette[-1] == 'rgb(134, 199, 204)', str(palette))
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
        check("nine scenario rungs remain in All layers beside one instrument row",
              page.locator('[data-native="enso-level"]:not([data-value="observed"])').count() == 9
              and page.locator('[data-native="enso-level"][data-value="observed"]').count() == 1
              and page.locator('.is-observed-rung').count() == 1
              and page.locator('.enso-instrument-row').count() == 1
              and page.locator('.enso-all-layers summary').text_content() == 'All layers')
        page.locator('.enso-all-layers').evaluate('e => e.open = true')
        page.locator('[data-native="enso-level"][data-value="-1.5"]').click()
        page.locator('[data-native="enso-mode"][data-value="crop"]').click()
        check("visible scenario and layer buttons drive the native change handlers",
              page.input_value('#enso-level') == '-1.5' and page.input_value('#enso-mode') == 'crop'
              and page.locator('[data-native="enso-level"][data-value="-1.5"]').get_attribute('aria-pressed') == 'true'
              and 'La Ni' in page.locator('#enso-legend').inner_text())
        country_name = page.locator('#enso-country option[value="ZWE"]').text_content()
        page.locator('#enso-harvest-fig').evaluate('e => e.open = true')   # 2026-09-29: the table sits behind "Show per country"
        page.locator('#enso-country-search').fill(country_name)
        page.wait_for_function("() => document.getElementById('enso-harvest-fig').dataset.iso === 'ZWE'")
        check("country search synchronises native selection and fitted coefficients",
              page.input_value('#enso-country') == 'ZWE'
              and country_name in page.locator('#enso-detail').inner_text()
              and page.locator('#enso-harvest-fig td[data-direction="fall"]').count() > 0
              and page.locator('#enso-harvest-fig .enso-tbl').count() == 1)
        page.evaluate("ensoFocus('USA')")
        page.wait_for_function("() => document.getElementById('enso-harvest-fig').dataset.iso === 'USA'")
        check("ensoFocus updates the single fitted table and opens Show per country",
              page.input_value('#enso-country') == 'USA'
              and page.locator('#enso-harvest-fig').evaluate('e => e.open')
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
        page.wait_for_selector('#subview-ensowater.active .enso-pan-since')
        water = page.evaluate("""() => {
            const slot = document.querySelector('.enso-pan-since'), ais = document.getElementById('enso-c-panama-daily');
            return {lead: !!(slot.compareDocumentPosition(ais) & Node.DOCUMENT_POSITION_FOLLOWING),
                    slot: slot.closest('figure').dataset.kind, ais: ais.closest('figure').dataset.kind,
                    source: ais.closest('details').querySelector('summary').textContent};
        }""")
        history = page.evaluate("async () => (await (await fetch('data/portwatch_history.json')).json()).data.chokepoints.panama.dates")
        lane_count = page.evaluate("async () => (await (await fetch('data/enso_lanes.json')).json()).data.lanes.length")
        check("the monthly Panama chart with its slot limits leads the daily AIS with its actual coverage",
              water['lead'] and water['slot'] == 'observed' and water['ais'] == 'observed'
              and iso_text(history[0]) in water['source'] and iso_text(history[-1]) in water['source'])
        board_slots = page.evaluate("async () => (await (await fetch('data/enso_lanes.json')).json()).data.lanes.find(l => l.id === 'panama').live_2026.steps.at(-1).total")
        # 2026-09-29 audit: El Niño-linked lanes as tight rows, the lanes with no ENSO link in one closed fold.
        check("Shipping answers every lane from the current JSON: linked lanes as rows, the rest folded",
              page.locator('.enso-lcard[data-board-lane]').count() + page.locator('.enso-lanes-other li[data-board-lane]').count() == lane_count
              and page.locator('.enso-lanes-other').get_attribute('open') is None
              and f'{board_slots} slots/day' in page.locator('[data-board-lane="panama"]').text_content())
        # 2026-09-30 (V2 audit: "it overlays a lot with the shipping map ... should first be a full view of map with lines
        # on what trade/ shipping is exposed"): the harvest story is Harvests-only. Shipping draws the grain trade through
        # Panama as solid bands, width in tonnes (FAOSTAT), every linked lane's status after its name, and no story.
        page.wait_for_function("() => document.querySelectorAll('#enso-map path.enso-xtrade').length > 0", timeout=20_000)
        page.wait_for_timeout(700)
        ship_map = page.evaluate("""async () => {
            const L = (await (await fetch('data/enso_lanes.json')).json()).data.lanes, xt = await ensoExposedTrade();
            const bands = [...document.querySelectorAll('#enso-map path.enso-xtrade')], w = bands.map(b => Number(b.getAttribute('stroke-width')));
            const today = new Date().toISOString().slice(0, 10), pan = L.find(l => l.id === 'panama');
            const state = id => (document.querySelector('.enso-choke-label[data-lane="' + id + '"] .enso-lane-state') || {}).textContent || '';
            return {story: document.querySelectorAll('#enso-map .enso-follow:not([hidden]) [data-story], #enso-map path.enso-flow, #enso-map .enso-hcall').length,
                    bands: bands.length, want: xt.buyers.length + (xt.buyers.some(b => b.side === 'asia') ? 2 : 1), trunk: Math.max(...w) === 12,
                    solid: bands.every(b => !b.hasAttribute('stroke-dasharray')), floor: xt.buyers.every(b => b.t >= 100000), year: xt.year,
                    title: document.querySelector('#enso-mapwrap .enso-plate-t').textContent,
                    unlinked: L.filter(l => l.phase === 'none').every(l => !document.querySelector('.enso-choke-label[data-lane="' + l.id + '"]')),
                    lanina: L.filter(l => l.phase === 'la_nina').every(l => state(l.id) === ' · La Niña side'),
                    panama: ((pan.live_2026 || {}).steps || []).some(s => (s.booking_from || s.effective) <= today) ? state('panama') === ' · restricted now' : state('panama').length > 3,
                    key: document.getElementById('enso-legend').textContent.includes('FAOSTAT ' + xt.year)};
        }""")
        check("Shipping map draws the grain through Panama in tonnes, each lane's status, no harvest story, only El Niño-linked lanes",
              ship_map["story"] == 0 and ship_map["bands"] == ship_map["want"] and ship_map["trunk"] and ship_map["solid"] and ship_map["floor"]
              and ship_map["unlinked"] and ship_map["lanina"] and ship_map["panama"] and ship_map["key"] and 'El Niño' in ship_map["title"], str(ship_map))
        check("Panama reads month by month, with the last twelve months day by day in a fold of the same plate", page.evaluate("""() => {
            const a = document.querySelector('.enso-pansince-plate'), b = document.querySelector('.enso-panama-daily');
            return !!a && !!b && a.contains(b) && b.tagName === 'DETAILS' && !document.querySelector('.enso-panama-pair');
        }"""))
        # Shipping logic (2026-09-23): every lane gets a stated outlook, the gauges
        # print the agencies' own latest readings, and Gatún is read against its record.
        check("Shipping gives every lane an outlook and prints each gauge's latest reading", page.evaluate("""async () => {
            const G = (await (await fetch('data/enso_gauges.json')).json()).data.gauges;
            const lanes = (await (await fetch('data/enso_lanes.json')).json()).data.lanes.filter(l => l.phase && l.phase !== 'none');
            const verdicts = [...document.querySelectorAll('.enso-lcard[data-board-lane] .enso-lcard-o')].map(b => b.textContent.trim());
            const cards = [...document.querySelectorAll('.enso-gauge:not(.is-freight) .enso-gauge-v b')].map(b => parseFloat(b.textContent));
            const want = ['stlouis', 'barge_stlouis', 'gulf_loadings', 'kaub', 'rosario', 'manaus'].filter(k => G[k]).map(k => G[k].latest.value);
            const g = Chart.getChart(document.getElementById('enso-c-gatun'));
            const labels = g ? g.data.datasets.map(d => d.label) : [];
            return verdicts.length === lanes.length && verdicts.every(v => v.length > 0)
                && cards.length === want.length && cards.every((v, i) => Math.abs(v - want[i]) < 1)
                && ['1997-98', '2015-16', '2023-24', '2026'].every(l => labels.includes(l))
                && document.getElementById('enso-lane-record-panama').textContent.includes('Japan maize');
        }"""))
        # 2026-09-27: Panama through the last El Niño (observed) and the Gatún dry-season outlook (this site's model).
        check("Shipping shows Panama since 2019 and a scored Gatún outlook that prints its file's numbers", page.evaluate("""async () => {
            const g = (await (await fetch('data/enso_gauges.json')).json()).data.gauges.gatun, O = g.outlook;
            const pm = (await (await fetch('data/portwatch_history.json')).json()).data.panama_monthly || [];
            const since = document.querySelector('.enso-pansince-plate'), fit = document.querySelector('.enso-gatunfit-plate');
            if (!O || !since || !fit || pm.length < 24) return false;
            const D = O.distribution, t = fit.textContent;
            const sonar = fit.querySelector('.enso-gatun-sonar'), st = sonar ? sonar.textContent : '';
            return !!sonar && st.includes('Now ' + g.latest.value.toFixed(1) + ' ft') && st.includes('median ' + D.p50.toFixed(1) + ' ft') && st.includes('Record low ' + D.record_ft.toFixed(1) + ' ft')
                && fit.querySelector('.enso-gatun-fit').closest('details') !== null
                && fit.dataset.kind === 'modelled' && getComputedStyle(fit).borderTopStyle === 'dashed'
                && D.p10 <= D.p50 && D.p50 <= D.p90 && O.loo_rmse_ft < O.loo_rmse_average_ft && O.n_seasons >= 40
                && t.includes(D.p50.toFixed(1) + ' ft') && t.includes(D.p10.toFixed(1) + '–' + D.p90.toFixed(1))
                && fit.querySelectorAll('.enso-gatun-fit circle').length === O.points.length
                && since.querySelectorAll('svg path').length >= 3;
        }"""))
        # 2026-09-24: the import end of the chain, from PortWatch's daily ports feed.
        check("Shipping measures the gateway import ports, one row per port in the feed", page.evaluate("""async () => {
            const P = (await (await fetch('data/enso_ports.json')).json()).data.ports;
            const rows = [...document.querySelectorAll('.enso-ports-table tbody tr:not(.enso-ol-year)')];
            return P.length >= 5 && rows.length === P.length && rows.every((r, i) => r.textContent.includes(P[i].name) || P.some(p => r.textContent.includes(p.name)))
                && !document.querySelector('.enso-meet-plate') && document.querySelector('.enso-ports-plate').textContent.includes('South Africa’s maize')
                && document.querySelector('#enso-water > figure') === document.querySelector('.enso-ports-plate');
        }"""))
        # 2026-09-24 (court): hand-checked facts expire. A hand-kept file older than its review window,
        # or an export measure past its end date still marked in force, fails the gate.
        import json as _json, datetime as _dt
        _today = _dt.date.today()
        _stale = []
        for _f, _days in (("enso_situation", 30), ("trade_restrictions", 14), ("enso_lanes", 30),
                          ("enso_mechanism", 30), ("enso_econ", 45), ("enso_regions", 45)):
            _m = _json.load(open(f"data/{_f}.json"))["_meta"]
            _stamps = [str(_m.get(k) or "")[:10] for k in ("generated_at", "reviewed_at") if _m.get(k)]
            _age = (_today - max(_dt.date.fromisoformat(x) for x in _stamps)).days if _stamps else 999
            if _age > _days:
                _stale.append(f"{_f} {_age}d > {_days}d")
        for _r in _json.load(open("data/trade_restrictions.json"))["data"]:
            if _r.get("status") in ("official", "reported") and _r.get("ends_date") and _dt.date.fromisoformat(_r["ends_date"]) < _today:
                _stale.append(f"{_r['country']} {_r['commodity']} ended {_r['ends_date']} but still marked {_r['status']}")
        check("hand-checked El Niño facts are within their review windows", not _stale, str(_stale))
        # 2026-09-24: the fit is scored on winters it was not fitted on, and each ledger row says how it did.
        page.evaluate("showTab('ensoharvest')")
        page.wait_for_selector('#subview-ensoharvest.active .enso-ol-chart')
        check("Harvests ledger reports the leave-one-El-Niño-out hindcast per row", page.evaluate("""async () => {
            const H = (await (await fetch('data/enso_hindcast.json')).json()).data.pairs;
            const txt = document.querySelector('.enso-outlook-plate').textContent;
            return Object.keys(H).length > 0 && Object.values(H).every(h => txt.includes('sign right ' + h.sign_right + ' of ' + h.events)) && document.querySelectorAll('.enso-ol-chart .enso-ol-row').length > 0
                && [...document.querySelectorAll('.enso-ol-chart .enso-ol-row:not(.enso-ol-head):not(.enso-ol-axisrow) .enso-ol-skill')].every(sp => /^\d+\/\d+ right/.test(sp.textContent))
                && !txt.includes('has not been scored') && txt.includes('held-out harvests');
        }"""))
        page.evaluate("showTab('ensowater')")
        page.wait_for_selector('#subview-ensowater.active .enso-lane-board')
        page.evaluate("document.querySelector('.enso-lanes-other').open = true")
        page.locator('[data-open-lane="rhine"]').click()
        check("lane board opens the matching folded record",
              page.locator('#enso-lane-record-rhine details').get_attribute('open') is not None
              and page.locator('.enso-lanes-more').get_attribute('open') is not None)
        # 2026-09-22: three lanes lead, the rest sit in a "N more lanes" fold with
        # the same two-column table, so rows are counted across both tables.
        check("one two-column lane record retains every lane",
              page.locator('.enso-lanes-table tbody tr').count() == lane_count
              and page.locator('#enso-lane-story').count() == 0
              and page.locator('.enso-lanes-table').first.locator('th').count() == 2)
        page.evaluate("showTab('ensomoney')")
        page.wait_for_selector('#subview-ensomoney.active #enso-c-record')
        check("Who pays lists every modelled shortfall with its buyers from the outlook file", page.evaluate("""async () => {
            const W = (await (await fetch('data/enso_outlook.json')).json()).data.who_pays;
            // 2026-09-28 (redesign): one row per payer. Every producer that imports to cover its shortfall and every named
            // buyer of a producer's exports has a row with its tonnes; a country hit both ways is one row.
            const rows = [...document.querySelectorAll('.enso-whopays-plate .enso-wp-row.is-pay:not(.enso-wp-head)')];
            const isos = rows.map(r => (r.querySelector('button') || {dataset: {}}).dataset.mapCountry).filter(Boolean);
            const want = new Set(W.flatMap(c => (c.extra_import_kt ? [c.iso + '/' + c.crop] : []).concat((c.buyers || []).map(b => b.iso + '/' + c.crop))));
            const zwe = rows.find(r => (r.querySelector('button') || {dataset: {}}).dataset.mapCountry === 'ZWE');
            return rows.length >= want.size && [...want].every(k => isos.includes(k.split('/')[0]))
                && (!zwe || /lost from its own harvest/.test(zwe.textContent) && /less from South Africa/.test(zwe.textContent))
                && document.querySelector('.enso-whopays-plate').textContent.includes('hit twice')
                && document.querySelectorAll('.enso-whopays-plate .enso-wp-total').length === ((await (await fetch('data/enso_outlook.json')).json()).data.who_pays_totals || []).length;
        }"""))
        # 2026-09-24: past El Niños and world prices are one dot plot; food inflation by country is the
        # map and its ranked list only (the bar chart and 37-row table repeated it).
        check("Prices shows past El Niños as one dot plot and food inflation once",
              page.locator('#enso-c-record .enso-event').count() == 7
              and page.locator('#enso-c-record .enso-pp-prev').count() == 7
              and page.locator('#enso-c-ffpi, #enso-c-rtfp, #enso-c-ffpilive, #enso-money-story').count() == 0)
        # 2026-09-29 audit ("why is it there? weirdly designed"): the watch table is a calendar in the Ocean grammar. Each row
        # label carries the World Bank price and its change on a year earlier; marks come from the model's harvest windows
        # and the export measures file; the ECB lag is a shaded column; the old policy text box is gone.
        check("Which prices to watch is a calendar: priced rows, harvest windows, dated measures and the ECB lag", page.evaluate("""async () => {
            const O = (await (await fetch('data/enso_outlook.json')).json()).data, R = (await (await fetch('data/trade_restrictions.json')).json()).data;
            const pl = document.querySelector('.enso-pricewatch-plate'); if (!pl) return false;
            const labels = [...pl.querySelectorAll('.enso-pw-labels span')].map(e => e.textContent);
            const harvests = O.rows_all.filter(r => r.status === 'shown' && !r.in_season && ['corn', 'wheat', 'rice'].includes(r.crop)).length;
            const dated = R.filter(r => r.status !== 'historical' && (r.ends_date || (r.next_step && r.next_step.date))).length;
            return labels.length >= 5 && labels.filter(t => /\$[\d,]+\/t/.test(t)).length >= 5
                && pl.querySelectorAll('.enso-pw-bar.is-modelled').length >= 1 && pl.querySelectorAll('.enso-pw-bar.is-modelled').length <= harvests
                && pl.querySelectorAll('.enso-pw-bar.is-point').length === dated
                && !!pl.querySelector('.enso-pw-lag') && !pl.querySelector('table');
        }"""))
        # 2026-09-27: the humanitarian record is its own fold at the end of Prices; the estimates fold into the past-price plate.
        check("reported humanitarian need is its own fold; the published models sit inside the past-price plate",
              page.locator('#subview-ensomoney #enso-people tr').count() >= 5
              and 'humanitarian' in page.locator('#enso-people').inner_text().lower()
              and page.locator('.enso-pastprice-plate .enso-estimates-fold tr').count() >= 5)
        page.evaluate("showTab('ensolive')")
        check("Reported board distinguishes published stories and reported assessments",
              'Everything on this board is observed' not in page.locator('#enso-live').inner_text()
              and page.locator('#enso-live .enso-wire-plate').get_attribute('data-kind') == 'published'
              and page.locator('#enso-live figure[data-kind="reported"]').count() >= 1)
        # Reported redesign (2026-09-23): decisions, regional concern, a country
        # ledger joined across feeds, then a deduplicated, food-first wire.
        check("Reported shows FEWS NET's regions, a country ledger and in-force measures with a countdown", page.evaluate("""async () => {
            const sit = (await (await fetch('data/enso_situation.json')).json()).data;
            const regions = [...document.querySelectorAll('#enso-live .enso-region-table tbody tr')];
            const ledger = document.querySelectorAll('#enso-live .enso-ledger tbody tr').length;
            const policy = document.querySelector('#enso-live .enso-policy-plate').textContent;
            return regions.length === sit.fewsnet.regions.length
                && regions.every((r, i) => r.textContent.includes(sit.fewsnet.regions[i].region) && r.textContent.includes(sit.fewsnet.regions[i].concern))
                && ledger >= 8 && /\\d+ days?/.test(policy);
        }"""))
        check("the wire shows each story once and no raw HTML entities leak", page.evaluate("""() => {
            const t = [...document.querySelectorAll('#enso-live .enso-wire-plate .enso-news-t')].map(a => a.textContent.toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\\s+/g, ' ').trim().slice(0, 70));
            return t.length > 0 && new Set(t).size === t.length && !document.getElementById('enso-live').textContent.includes('&amp;');
        }"""))
        # 2026-09-24: press headlines and ReliefWeb reports are one stream; reports skip
        # monsoon/cyclone/earthquake sitreps and donor notices, and never repeat a wire story.
        check("humanitarian reports keep to El Niño, drought and food security",
              page.evaluate("""() => {
                  const r = [...document.querySelectorAll('#enso-live .enso-wire-plate .is-report .enso-news-t')].map(a => a.textContent.toLowerCase());
                  return r.every(x => !/earthquake|monsoon|cyclone|charity/.test(x));
              }"""))

        # 2026-09-29 audit (Reported): the three plates follow the Ocean and Harvests grammar (one lede, at most three
        # bullets), the ledger has one small "note" marker per hotspot row instead of a fold per row, the note opens in
        # place, and no plate runs past ~820 px at 1440 wide.
        check("Reported plates carry one lede and at most three bullets, and stay under ~820 px",
              page.evaluate("""() => [...document.querySelectorAll('#enso-live > .enso-plate')].every(f =>
                  f.querySelectorAll(':scope > .plate-body > .enso-plate-lede').length === 1
                  && f.querySelectorAll(':scope > .plate-body > .enso-bullets > li').length <= 3
                  && f.getBoundingClientRect().height <= 830)"""))
        check("ledger hotspot notes are one marker per row, closed by default, and open in place",
              page.evaluate("""() => {
                  const led = document.querySelector('#enso-live .enso-ledger-plate');
                  if (led.querySelector('details.enso-live-comment')) return false;
                  const b = led.querySelector('.enso-asap-btn'); if (!b) return false;
                  const n = document.getElementById(b.getAttribute('aria-controls'));
                  const closed = n.hidden && b.getAttribute('aria-expanded') === 'false';
                  b.click();
                  const open = !n.hidden && b.getAttribute('aria-expanded') === 'true' && n.textContent.trim().length > 20;
                  b.click();
                  return closed && open && n.hidden;
              }"""))

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
        # The controls sentence lives in the Ocean fold only (2026-09-22); the
        # other views carry a shorter "Map sources" fold.
        page.evaluate("showTab('elnino')")
        page.wait_for_selector('#subview-elnino.active .enso-subview-meta')
        check("Stage H fold explains button zoom and page scrolling",
              'so the page can scroll over it' in page.locator('#enso-legend details').text_content())
        page.locator('#enso-map').scroll_into_view_if_needed()
        pacific = page.evaluate("""() => {
            const m = window._stageHMap, box = m.getContainer().getBoundingClientRect();
            const p = m.latLngToContainerPoint([0,-150]);
            return {x:box.left+p.x,y:box.top+p.y};
        }""")
        page.mouse.move(pacific['x'], pacific['y'])
        check("Ocean sea-cell hover follows the cursor and shows an anomaly",
              page.locator('.enso-sst-readout').is_visible()
              and '°C' in page.locator('.enso-sst-readout').inner_text())
        land = page.evaluate("""() => {
            const m = window._stageHMap, box = m.getContainer().getBoundingClientRect();
            const p = m.latLngToContainerPoint([0,20]); return {x:box.left+p.x,y:box.top+p.y};
        }""")
        page.mouse.move(land['x'],land['y'])
        # 2026-09-28 (owner: "it should show rain that there is this week"): over land the readout quotes the last
        # 30 days of rain when data/rain_anomaly.json has the cell, never a sea temperature. Later that day (owner:
        # "when hovering over a country you get both of the info"): it is the only hover on the sea layer, so it
        # names the country and no crop tooltip opens.
        ro_land = page.locator('.enso-sst-readout')
        check("over land the readout names the country and quotes rain, never a sea temperature, and no crop tooltip opens",
              ro_land.is_visible() and '°C' not in ro_land.inner_text() and len(ro_land.inner_text().split('\n')[0]) > 2
              and page.evaluate("() => ![...document.querySelectorAll('#enso-map .leaflet-tooltip')].some(t => /measured|coverage|log points/.test(t.textContent))"))
        page.mouse.move(1,1)
        check("SST readout hides on pointer leave", not page.locator('.enso-sst-readout').is_visible())
        collapse = []
        for tab in ('elnino', 'ensowater', 'ensomoney', 'ensolive'):
            page.evaluate("tab => showTab(tab)", tab)
            page.wait_for_selector(f'#subview-{tab}.active .enso-subview-meta')
            # 2026-09-22: the "Scenario" toggle that expanded the modelled rungs on
            # observed lenses is gone; a control that paints nothing on the lens it
            # sits on was one of the parts the owner asked to take away. The rungs
            # live on Harvests only, and the deep link still lands there.
            collapse.append(not page.locator('#enso-scenario-rungs').is_visible()
                            and not page.locator('#enso-scenario-toggle').is_visible())
        page.evaluate("showTab('ensoharvest')")
        page.wait_for_selector('#subview-ensoharvest.active .enso-subview-meta')
        page.locator('.enso-all-layers').evaluate('e => e.open = true')
        page.locator('[data-native="enso-level"][data-value="-1.5"]').click()
        explicit = page.input_value('#enso-level') == '-1.5' and 'enso_level=-1.5' in page.url
        check("Stage H scenario is absent on observed lenses and lives on Harvests without losing deep links",
              all(collapse) and explicit and page.locator('#enso-scenario-rungs').is_visible()
              and not page.locator('#enso-scenario-toggle').is_visible()
              and page.input_value('#enso-level') == '-1.5')
        ranked = []
        for tab, feed in (('ensomoney', 'staple'), ('ensolive', 'reported')):
            page.evaluate("tab => showTab(tab)", tab)
            page.wait_for_selector('#enso-map-ranking button')
            # The price rail no longer ranks the twelve highest inflations in the
            # whole RTFP feed. The owner asked for "all countries of the el nino",
            # so it lists every country the published teleconnection layer names,
            # valued ones first in descending order, then those with no monitored
            # market, then the monitored countries outside the layer.
            valid = page.evaluate("""async feed => {
                if (feed === 'staple') {
                    // 2026-09-29: the Prices rail lists every staple series in the price outlook, largest rise since March
                    // first; the title counts series and countries; Zimbabwe, with no series before 2024, is named as such.
                    const P = (await (await fetch('data/enso_price_outlook.json')).json()).data;
                    const rows = P.rows.filter(r => r.aftermath && r.aftermath.now);
                    const btn = [...document.querySelectorAll('#enso-map-ranking button')];
                    const vals = btn.map(b => parseFloat(b.querySelector('.enso-rank-value').textContent.replace('−', '-')));
                    const title = document.querySelector('#enso-map-ranking .enso-legend-t').textContent;
                    const m = document.getElementById('enso-map').getBoundingClientRect();
                    const a = document.getElementById('enso-map-ranking').getBoundingClientRect();
                    const zwe = P.excluded.some(x => x.iso3 === 'ZWE' && /no El Niño on record/.test(x.reason));
                    return a.left >= m.right - 1 && btn.length === rows.length && vals.every((v, i) => !i || v <= vals[i - 1])
                        && title.includes(P.rows.length + ' staples, ' + new Set(P.rows.map(r => r.iso3)).size + ' countries')
                        && (!zwe || document.getElementById('enso-map-ranking').textContent.includes('Zimbabwe'));
                }
                if (feed === 'reported') {
                    // 2026-09-27: the Reported rail lists El Niño countries with a report, a crisis classification or a
                    // headline, sorted by severity (crisis phase), then report count, then fits; the title counts the rows.
                    const b = [...document.querySelectorAll('#enso-map-ranking button')];
                    const k = x => [+x.dataset.phase, +x.dataset.total, +x.dataset.fits];
                    const sorted = b.every((x, i) => { if (!i) return true; const p = k(b[i-1]), q = k(x);
                        for (let j = 0; j < 3; j++) { if (p[j] !== q[j]) return p[j] > q[j]; } return true; });
                    const m = document.getElementById('enso-map').getBoundingClientRect();
                    const a = document.getElementById('enso-map-ranking').getBoundingClientRect();
                    const title = document.querySelector('#enso-map-ranking .enso-legend-t').textContent;
                    return a.left >= m.right - 1 && sorted && b.length > 0 && title.includes(b.length + ' El Niño countries');
                }
                const data = (await (await fetch('data/' + feed + '.json')).json()).data;
                const buttons = [...document.querySelectorAll('#enso-map-ranking button')];
                const isos = buttons.map(b => b.dataset.mapCountry);
                const m = document.getElementById('enso-map').getBoundingClientRect();
                const a = document.getElementById('enso-map-ranking').getBoundingClientRect();
                if (a.left < m.right - 1) return false;
                if (feed !== 'rtfp') {
                    const rows = isos.map(i => data[i]);
                    // 2026-09-26: the Reported rail lists hotspots in El Niño regions only, like its pins.
                    const regs = (await (await fetch('data/enso_regions.json')).json()).data.regions;
                    const teleSet = new Set(regs.flatMap(r => r.iso3 || []));
                    const expected = Object.entries(data).filter(([i, r]) => teleSet.has(i) && [1,2].includes(r.hotspot_code)).map(([, r]) => r);
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
                      && buttons[i].textContent.includes(((d) => { const m = String(d).match(/^(\d{4})-(\d{2})-(\d{2})/); return m ? ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m[2] - 1] + ' ' + m[1] : d; })(data[iso].as_of))
                    // 2026-09-24: a country with no monitored market ranks by its official food CPI
                    // (FAOSTAT, same month a year earlier) or is named as having no value in either source.
                    : (buttons[i].textContent.includes('official food CPI')
                       || (buttons[i].closest('.enso-rank-missing') || {textContent: ''}).textContent.includes('No value in either source')));
            }""", feed)
            first = page.locator('#enso-map-ranking button').first
            iso = first.get_attribute('data-map-country')
            # Each lens fits its own view on open; read the zoom once that settles.
            page.wait_for_timeout(800)
            before_zoom = page.evaluate('_stageHMap.getZoom()')
            first.click()
            ranked.append(valid and page.locator(f'#subview-{tab}').evaluate("e => e.classList.contains('active')")
                          and page.input_value('#enso-country') == iso and first.get_attribute('aria-pressed') == 'true'
                          and page.evaluate('_stageHMap.getZoom()') == before_zoom)
        check("Stage H ranked lists retain values, dates, order and lens when selecting a country", all(ranked))
        # 2026-09-26: Reported draws the Disturbances events that fit El Niño's usual sign; the key counts countries drawn.
        check("Stage H Reported key counts the countries it draws", page.evaluate("""() => {
            let n = 0;
            _stageHMap.eachLayer(l => { if (l.options && l.options.ensoSource === 'event') n++; });
            const key = document.getElementById('enso-legend').textContent;
            const tag = document.getElementById('enso-maptag').textContent;
            const m = tag.match(/^(\d+) of (\d+) hazard reports/);
            return n > 0 && key.includes('Fitting is not attribution') && !!m && key.includes(m[2] + ' reports');
        }"""))
        page.set_viewport_size({'width':390,'height':844})
        stacked = []
        for tab in ('ensomoney','ensolive'):
            page.evaluate("tab => showTab(tab)", tab)
            stacked.append(page.evaluate("""() => document.getElementById('enso-map-ranking').getBoundingClientRect().top >=
                document.getElementById('enso-map').getBoundingClientRect().bottom - 1"""))
        check("Stage H both ranked lists stack below the map on phones", all(stacked))
        page.evaluate("showTab('ensowater')")
        # 2026-09-29 audit: only lanes (and their corridors) with a published ENSO link are drawn.
        linked_corridors = page.evaluate("""async () => { const L = (await (await fetch('data/enso_lanes.json')).json()).data.lanes, C = (await (await fetch('data/enso_corridors.json')).json()).data.corridors;
            return C.filter(c => L.some(l => l.id === c.lane && l.phase === c.phase && l.phase !== 'none')).length; }""")
        linked_lanes = page.evaluate("async () => (await (await fetch('data/enso_lanes.json')).json()).data.lanes.filter(l => l.phase && l.phase !== 'none').length")
        check("Stage H phones show no corridor chips and retain all routes in the fold",
              page.locator('.enso-corridor-chip').count() == 0
              and page.locator('path.enso-corridor').count() == linked_corridors
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
        check("Stage H chokepoint labels use unboxed text with a ground halo", page.evaluate("""() => {
            const labels = [...document.querySelectorAll('.enso-choke-label')];
            return labels.length === LINKED && document.querySelectorAll('.enso-anno').length === 0 && labels.every(label => {
                const style = getComputedStyle(label), text = label.querySelector('text'), ink = getComputedStyle(text);
                return style.borderTopWidth === '0px' && style.backgroundColor === 'rgba(0, 0, 0, 0)'
                    && ink.paintOrder.startsWith('stroke') && ink.strokeWidth === '2px'
                    // 2026-09-26: size follows the lane's ENSO tier: 12px linked, 11px weak link, 10px none.
                    && ink.fontSize === ({t1: '12px', t2: '11px', t3: '10px'}[[...label.closest('.enso-choke').classList].find(c => /^t[123]$/.test(c))] || '11px')
                    && ink.stroke === 'rgb(11, 16, 23)';
            });
        }""".replace("LINKEDC", str(linked_corridors)).replace("LINKED", str(linked_lanes))))

        print("\nStage I shipping marks and measurements")
        check("Stage I corridor polylines are dashed and lane polylines remain solid", page.evaluate("""() => {
            const corridors = [], lanes = [];
            _stageHMap.eachLayer(l => {
                if (l.options.className === 'enso-corridor') corridors.push(l);
                if (l.options.className === 'enso-lane') lanes.push(l);
            });
            // The supplied lanes have no line geometry; Stage D exercises solid lane fixtures.
            return corridors.length === LINKEDC && corridors.every(l => l.options.dashArray === '6 4'
                && l.getElement().getAttribute('stroke-dasharray') === '6 4')
                && lanes.every(l => !l.options.dashArray && !l.getElement().hasAttribute('stroke-dasharray'));
        }""".replace("LINKEDC", str(linked_corridors)).replace("LINKED", str(linked_lanes))))
        visible_key = page.locator('#enso-legend').text_content()  # 2026-09-27: meanings sit in the key's fold
        # The key used to show a solid-line swatch for "observed transits". No
        # lane in enso_lanes.json carries a geometry, so that line is never
        # drawn and the key described a mark the map does not have. The observed
        # mark is the diamond. 2026-09-29 (owner: no size-scaled circles on maps): the ring is gone.
        check("Stage I visible key distinguishes observed transits from published schematic corridors",
              'diamond: observed, measured at the chokepoint' in visible_key and 'Ring size' not in visible_key
              and 'dashed: published schematic corridor through named ports' in visible_key)
        # 2026-09-30: Shipping opens on the world view with the Panama trade bands drawn (the harvest story is Harvests-only).
        check("Stage I Shipping opens on the world view: every El Niño lane on screen, the Panama trade drawn", page.evaluate("""() => {
            const box = document.getElementById('enso-map').getBoundingClientRect();
            const pins = [...document.querySelectorAll('.enso-choke')];
            return pins.length === LINKED && pins.every(p => { const r = p.getBoundingClientRect(); return r.left >= box.left && r.right <= box.right && r.top >= box.top && r.bottom <= box.bottom; })
                && document.querySelectorAll('#enso-map path.enso-xtrade').length > 0 && !document.querySelector('#enso-map path.enso-flow');
        }""".replace("LINKED", str(linked_lanes))))
        check("Stage I magnitude joins each lane to PortWatch and names missing values", page.evaluate("""async () => {
            const lanes = (await (await fetch('data/enso_lanes.json')).json()).data.lanes;
            const feed = await (await fetch('data/portwatch.json')).json();
            const rings = document.querySelectorAll('.enso-choke > i[data-yoy]');
            const key = document.getElementById('enso-legend').textContent;
            const date = s => new Date(s).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
            let measured = 0, missing = 0;
            return lanes.filter(ln => ln.phase && ln.phase !== 'none').every(ln => {
                const label = document.querySelector('.enso-choke-label[data-lane="' + ln.id + '"]');
                if (!label) return false;
                const pin = label.closest('.enso-choke'), ring = pin.querySelector('i[data-yoy]');
                const pw = feed.data[ln.portwatch_key], dry = pw && Number.isFinite(pw.yoy.dry_bulk_pct) && Number.isFinite(pw.transits_per_day.dry_bulk), pct = pw && pw.yoy && (dry ? pw.yoy.dry_bulk_pct : pw.yoy.total_pct);
                if (!pw || !Number.isFinite(pct) || !Number.isFinite(pw.transits_per_day.total)) {
                    missing++;
                    return !ring && !pin.querySelector('.enso-transit-ring') && pin.classList.contains('no-transit') && !label.textContent.includes('no transit data')
                        && key.includes('no PortWatch transit change available')
                        && key.includes(label.querySelector('text').textContent.split(' · ')[0]);
                }
                measured++;
                // 2026-09-29: the mark is a fixed-size diamond filled by the transit-change class, not a ring scaled by the change.
                const want = pct <= -25 ? '#b8452a' : pct <= -10 ? '#d9824a' : pct < -3 ? '#e2b27c' : pct <= 3 ? '#77797d' : pct < 10 ? '#a9c3da' : '#5f8fbf';
                const rgb = 'rgb(' + [1, 3, 5].map(i => parseInt(want.slice(i, i + 2), 16)).join(', ') + ')';
                const signed = (pct > 0 ? '+' : pct < 0 ? '−' : '') + Math.abs(pct).toFixed(1) + '% y/y';
                return ring && Number(ring.dataset.yoy) === pct
                    && getComputedStyle(ring).backgroundColor === rgb && !pin.querySelector('.enso-transit-ring')
                    && label.textContent.includes(signed)
                    && !pin.classList.contains('no-transit')
                    && key.includes(pw.window_days + '-day mean to ' + date(pw.latest_date))
                    && pin.getAttribute('aria-label').includes(pw.transits_per_day.total.toFixed(1) + ' transits/day');
            }) && measured === rings.length && measured > 0 && missing > 0
                && key.includes('per day against a year earlier, all vessels where dry bulk is missing, IMF PortWatch')
                && key.includes('collected ' + date(feed._meta.generated_at))
                && key.includes('Every diamond is the same size') && !key.includes('Ring area grows');
        }"""))
        check("Stage I both Panama arcs carry a visible matching continuation name", page.evaluate("""() => {
            const markers = [];
            _stageHMap.eachLayer(l => {
                if ((l.options.icon && l.options.icon.options.className || '').includes('enso-corridor-edge')) markers.push(l);
            });
            return markers.length === 2 && markers.map(l => l.getLatLng().lng).sort((a,b)=>a-b).join(',') === '-180,180'
                && markers.every(l => {
                    const span = l.getElement().querySelector('span'), box = span.getBoundingClientRect(), style = getComputedStyle(span);
                    return span.dataset.corridor === 'us_gulf_panama_east_asia'
                        && span.textContent === '↔ US Gulf to East Asia' && getComputedStyle(span).visibility === 'visible'
                        && style.backgroundColor === 'rgba(0, 0, 0, 0)' && style.borderTopWidth === '0px'
                        && style.textShadow !== 'none' && box.width > 0 && box.height > 0;
                });
        }"""))
        # 2026-09-26: corridors are weighted by the lane's published ENSO link (tier 1 phase ink 2px, tier 2 1.25px, tier 3 grey 1px).
        page.locator('[data-sview="enso"]').click()  # the link inks are the El Niño link view (2026-09-27)
        page.wait_for_timeout(400)
        # 2026-09-29 audit: only the corridors of lanes with a published ENSO link are drawn.
        check("Stage I routes of ENSO-linked lanes are focusable, weighted by ENSO link, with corridor names in tooltips", page.evaluate("""async () => {
            const lanes = (await (await fetch('data/enso_lanes.json')).json()).data.lanes;
            const feed = (await (await fetch('data/enso_corridors.json')).json()).data.corridors.filter(c => lanes.some(l => l.id === c.lane && l.phase === c.phase && l.phase !== 'none'));
            const lines = [...document.querySelectorAll('path.enso-corridor')];
            return lines.length === feed.length && !document.querySelector('.enso-corridor-chip') && feed.every(c => {
                const line = lines.find(e => e.dataset.corridor === c.id), ln = lanes.find(l => l.id === c.lane);
                const tier = ln.phase === 'none' ? 3 : ln.attribution === 'weak' ? 2 : 1;
                const ink = tier === 3 ? '#7b8491' : {el_nino: '#e0673c', la_nina: '#5b9bd0'}[ln.phase];
                return line && line.tabIndex === 0 && line.getAttribute('aria-label').includes(c.name)
                    && line.getAttribute('stroke') === ink
                    && Number(line.getAttribute('stroke-width')) === [0, 2, 1.25, 1][tier]
                    && Number(line.getAttribute('stroke-opacity')) === [0, .85, .55, .35][tier]
                    && document.querySelector('#enso-legend details').textContent.includes(c.name);
            });
        }"""))
        routes = page.locator('path.enso-corridor')
        focus_results = []
        for i in range(routes.count()):
            route = routes.nth(i)
            route.focus()
            focus_results.append(page.locator('.enso-corridor-tip').is_visible())
            route.evaluate('e => e.blur()')
            focus_results.append(page.locator('.enso-corridor-tip').count() == 0)
        check("Stage I route tooltips open on keyboard focus and close on blur", len(focus_results) == 2 * linked_corridors and all(focus_results))
        route = routes.first
        route.dispatch_event('mouseover')
        hovered = page.locator('.enso-corridor-tip').is_visible()
        route.dispatch_event('mouseout')
        check("Stage I hovering a route reveals its corridor name", hovered and page.locator('.enso-corridor-tip').count() == 0)
        # Rings are orange on lanes with a published ENSO link and blue-grey where
        # the change has other causes (2026-09-23), so a war ring is not read as El Niño.
        # 2026-09-27 (map research): the map opens on the change now; the driver inks are the El Niño link view.
        page.locator('[data-sview="enso"]').click()
        page.wait_for_timeout(400)
        check("Stage I corridors have no destination triangles and chokepoint diamonds carry the driver ink", page.evaluate("""async () => {
            const lanes = (await (await fetch('data/enso_lanes.json')).json()).data.lanes;
            // 2026-09-29: the ring is gone (no size-scaled circles on maps); the diamond carries the phase ink and stays one size per tier.
            const phase = Object.fromEntries(lanes.map(l => [l.id, l.phase]));
            const marks = [...document.querySelectorAll('.enso-choke > i[data-yoy]')];
            return !document.querySelector('.enso-corridor-arrow') && !document.querySelector('.enso-transit-ring') && marks.length > 0 && marks.every(mark => {
                const id = mark.closest('.enso-choke').querySelector('.enso-choke-label').dataset.lane;
                const want = {el_nino: '#e0673c', la_nina: '#5b9bd0'}[phase[id]] || '#7b8491';
                return mark.style.getPropertyValue('--ink-phase') === want && mark.style.background === ''
                    && ['13px', '11px', '8px'].includes(getComputedStyle(mark).width);
            });
        }"""))

        print("\nStage J chart and geometry gates")
        page.set_viewport_size({"width": 390, "height": 1000})
        page.evaluate("showTab('ensowater')")
        page.locator('#enso-mapwrap [data-z="0"]').click()
        page.wait_for_timeout(350)
        check("Stage J all chokepoint labels fit the plate at 390px", page.evaluate("""() => {
            const box = document.getElementById('enso-map').getBoundingClientRect();
            const labels = [...document.querySelectorAll('.enso-choke-label')];
            return labels.length === LINKED && labels.every(e => {
                const r = e.getBoundingClientRect();
                return r.width > 0 && r.left >= box.left && r.right <= box.right
                    && r.top >= box.top && r.bottom <= box.bottom;
            });
        }""".replace("LINKEDC", str(linked_corridors)).replace("LINKED", str(linked_lanes))))
        check("Stage J Panama has dated advisories and no ordinal slot chart", page.evaluate("""() => !document.getElementById('enso-c-panama') && !!document.querySelector('.enso-pansince-plate .enso-pan-step')"""))
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator('#enso-mapwrap [data-z="0"]').click()
        page.wait_for_timeout(350)
        check("Stage J default Amazon label clears graticule text", page.evaluate("""() => {
            const r = document.querySelector('.enso-choke-label[data-lane="amazon"]').getBoundingClientRect();
            const labs = [...document.querySelectorAll('.enso-grat-lab')];
            return labs.length > 0 && labs.every(e => {
                const g = e.getBoundingClientRect();
                return r.right <= g.left || r.left >= g.right || r.bottom <= g.top || r.top >= g.bottom;
            });
        }"""))
        page.evaluate("showTab('elnino')")
        page.wait_for_selector('.enso-analog-endlabels', state='visible')
        check("Stage J five visible analog connectors meet their label edges at 1440px", page.evaluate("""() => {
            const plot = document.querySelector('.enso-analog'), svg = plot.querySelector('svg');
            const lines = [...plot.querySelectorAll('.enso-analog-leaders line')];
            const labels = [...plot.querySelectorAll('.enso-analog-endlabels .enso-analog-lab')];
            return lines.length === 5 && lines.every((line,i) => {
                const pt = svg.createSVGPoint(); pt.x = line.x2.baseVal.value; pt.y = line.y2.baseVal.value;
                const p = pt.matrixTransform(svg.getScreenCTM()), r = labels[i].getBoundingClientRect();
                return line.getBoundingClientRect().width > 0 && r.width > 0
                    && Math.abs(p.x-r.left) < 1 && Math.abs(p.y-(r.top+r.height/2)) < 1;
            });
        }"""))
        page.locator('#enso-indices details').evaluate('e => e.open = true')
        check("Stage J indices use own thresholds and SOI is not the longest bar", page.evaluate("""() => {
            const row = key => document.querySelector('[data-index="'+key+'"]');
            const bar = key => row(key).querySelector('.enso-idx-bar');
            const width = key => parseFloat(bar(key).querySelector('span').style.width);
            return !bar('wk34') && row('wk34').textContent.includes('value only')
                && [['oni',.5],['roni',.5],['bom_rel',.8],['soi',-7]].every(([key,t]) =>
                    Number(bar(key).dataset.threshold) === t && bar(key).querySelectorAll('.enso-idx-tick').length === 2)
                && width('soi') < width('oni') && width('soi') < width('bom_rel');
        }"""))
        page.evaluate("showTab('ensoharvest')")
        # 2026-09-28: coverage follows the phase in view (La Niña reads coverage_la_nina, which after the
        # phase-specific refit is Pakistan maize alone), so read the ramp at the observed El Niño ONI.
        page.select_option('#enso-level', 'observed')
        page.locator('[data-native="enso-mode"][data-value="coverage"]').click()
        # paint() throttles a layer change behind a burst guard and a globe-wide
        # ripple, so reading fillColor in the same tick reads the previous layer.
        page.wait_for_selector('.enso-coverage-ramp')
        page.wait_for_timeout(1500)
        check("Stage J coverage shows three anchors and distinct USA and ZAF fills", page.evaluate("""() => {
            const ramp = document.querySelector('.enso-coverage-ramp'), fills = {};
            // The basemap does not always carry ISO_A3; the page falls back
            // through ADM0_A3, iso_a3 and id, so the check must too. More than
            // one layer can carry the same country's feature (the teleconnection
            // overlay holds unfilled copies), so keep the painted one rather
            // than whichever eachLayer happens to reach last.
            const isoOf = f => { const p = (f && f.properties) || {};
                return p.ISO_A3 || p.ADM0_A3 || p.iso_a3 || p.id || ''; };
            _stageHMap.eachLayer(layer => {
                const iso = isoOf(layer.feature);
                if ((iso === 'USA' || iso === 'ZAF') && layer.options.fillColor) fills[iso] = layer.options.fillColor;
            });
            return ramp && ramp.nextElementSibling.textContent === '0%50%100%'
                && fills.USA && fills.ZAF && fills.USA !== fills.ZAF;
        }"""))
        import json as _pjson
        _pout_year = _pjson.load(open(ROOT / 'data' / 'enso_price_outlook.json'))['data']['rows'][0]['aftermath']['now']['from'][:4]
        for width, height in ((1280, 800), (1440, 900)):
            page.set_viewport_size({'width': width, 'height': height})
            for tab, labels in (
                ('ensowater', ['No land layer', 'Shipping', 'Change nowEl Niño link']),
                # 2026-09-29 round 2 (owner picked one map): the past sits on the chips, so the stage switch is gone; the
                # chips compare with one past El Niño, picked by a 2023-24 / 2015-16 switch.
                ('ensomoney', ['Staple prices', 'Grain imports', 'Since MarchWho paysYear on year', '2023-242015-16']),
                ('elnino', []),  # 2026-09-29 round 2 (owner: "remove Sea-surface"): one layer, no chip
                ('ensoharvest', ['Production shock', 'Strongest crop', 'Coverage', 'Crop stress now', 'Teleconnections']),
                ('ensolive', ['Rain pattern', 'Hotspots', 'IPC', 'Hazards', 'Headlines', 'Elsewhere'])):
                page.evaluate('tab => showTab(tab)', tab)
                page.wait_for_timeout(150)
                # Search and zoom sit on the map (2026-09-23), so the header is
                # title/source, one layer row and a caption: map within 110px.
                check(f"map instruments at {width}: {tab} has its lens chips, map within 110px of the plate head, search and zoom on the map", page.evaluate("""expected => {
                    const row = document.querySelector('.enso-instrument-row');
                    const labels = [...row.children].filter(e => e.tagName !== 'DETAILS').map(e => e.textContent.trim());
                    const head = document.querySelector('#enso-mapwrap > .enso-plate-h').getBoundingClientRect();
                    const map = document.querySelector('#enso-map').getBoundingClientRect();
                    const search = document.querySelector('#enso-map .enso-map-tools .enso-country-search');
                    const tools = document.querySelector('#enso-map .enso-map-tools');
                    const t = tools && tools.getBoundingClientRect();
                    return JSON.stringify(labels) === JSON.stringify(expected)
                        && !row.querySelector('details').open && map.top - head.top <= 110
                        && !!search && !!t && t.left >= map.left && t.right <= map.right && t.top >= map.top && t.bottom <= map.bottom;
                }""", labels))
                if tab == 'elnino':
                    # 2026-09-29 round 2 (owner: "remove Sea-surface. so its one straight line"): title, source and All layers on one line.
                    check(f"Ocean map head at {width}: title, source and All layers on one line", page.evaluate("""() => {
                        const t = document.querySelector('#enso-mapwrap > .enso-plate-h .enso-plate-t').getBoundingClientRect();
                        const sub = document.querySelector('#enso-mapwrap > .enso-plate-h .enso-plate-sub').getBoundingClientRect();
                        const s = document.querySelector('#enso-mapwrap .enso-all-layers > summary').getBoundingClientRect();
                        return t.height < 34 && sub.top < t.bottom && s.top < t.bottom && s.bottom > t.top && s.left > sub.right;
                    }"""))
            page.evaluate("showTab('ensomoney')")
            page.wait_for_timeout(150)
            # 2026-09-29: the default view shades the price-outlook countries by their real change since March, the larger
            # move where a country has two staples; other land stays neutral.
            check(f"Prices at {width}: El Niño countries shaded by their staple price class, labels on the largest moves", page.evaluate("""async () => {
                const P = (await (await fetch('data/enso_price_outlook.json')).json()).data;
                const tele = new Set(P.rows.map(r => r.iso3)), seen = new Set();
                const lead = {}; P.rows.forEach(r => { const v = r.aftermath.now && r.aftermath.now.pct; if (typeof v === 'number' && (lead[r.iso3] == null || Math.abs(v) > Math.abs(lead[r.iso3]))) lead[r.iso3] = v; });
                const cls = v => { const B = [-10, -3, 3, 10, 20, 30]; for (let i = 0; i < B.length; i++) if (v < B[i]) return i; return B.length; };
                let good = true;
                _stageHMap.eachLayer(l => {
                    const p = l.feature && l.feature.properties, iso = p && (p.ISO_A3 || p.ADM0_A3 || p.iso_a3 || p.id);
                    if (!iso) return;
                    if (tele.has(iso) && l.options.fillColor) { seen.add(iso); good = good && l.options.opacity >= .6 && l.options.weight >= .7
                        && l.options.fillColor === ['#3f6f9c', '#8fb1cf', '#77797d', '#d9b27c', '#dd8a45', '#cc5a2e', '#9e2f1c'][cls(lead[iso])]; }
                });
                // 2026-09-27 (map research): a signed seven-class choropleth replaces the circles.
                const classes = ['#3f6f9c', '#8fb1cf', '#77797d', '#d9b27c', '#dd8a45', '#cc5a2e', '#9e2f1c'];
                let shaded = 0, circles = 0; _stageHMap.eachLayer(l => { if (l.feature && classes.includes(l.options.fillColor)) shaded++; if (l instanceof L.CircleMarker && !l.feature && l.options.radius >= 4) circles++; });
                const pane = _stageHMap.getPane('ensoGraticule');
                // 2026-09-29 round 2: text labels became one chip per country (no size encoding); every country fits.
                return good && seen.size === tele.size && shaded >= 10 && circles === 0 && pane.style.zIndex === '210'
                    && pane.querySelectorAll('.enso-grat-lab').length === 3
                    && document.querySelectorAll('#enso-map .enso-pchip').length === tele.size;
            }"""))

        # 2026-09-29 audit, Prices, round 2 ("I prefer not to have circles on maps"; ?pmap default a). The same months
        # of 2023-24 and 2015-16 sit on each country's chip instead of behind a stage switch; "Who pays" is a second,
        # modelled view (dashed frame) that fills buyers by tonnes, with no circles; no price-model number anywhere.
        page.evaluate("showTab('ensomoney')"); page.wait_for_timeout(900)
        # 2026-09-29 round 2, final map (owner: "I like C but have main % be the price now … under what it was last el
        # nino … price should have the forecast"; "adjust price forward view to inflation and pressure that has nothing to
        # do with el nino"). Each chip: now; the picked past El Niño at this stage and at its peak; the El Niño-effect
        # replay (normal year, then with the effect) from effect_replay. Every number must equal the file's fields.
        page.evaluate("showTab('ensomoney')"); page.wait_for_timeout(900)
        _chip_js = """async ev => {
            const P = (await (await fetch('data/enso_price_outlook.json')).json()).data;
            const pct = v => Math.round(v) === 0 ? '0%' : (v > 0 ? '+' : '−') + Math.abs(v).toFixed(0) + '%';
            const M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            const bad = [];
            for (const iso of ['ZAF', 'MOZ', 'ZMB', 'THA']) {
                const r = P.rows.filter(x => x.iso3 === iso).sort((x, y) => Math.abs(y.aftermath.now.pct) - Math.abs(x.aftermath.now.pct))[0];
                const a = r.aftermath, e = r.effect_replay.paths[ev], an = r.analog.paths[ev];
                const chip = [...document.querySelectorAll('#enso-map .enso-pchip')].find(c => c.textContent.startsWith(iso === 'ZAF' ? 'South Africa' : iso === 'MOZ' ? 'Mozambique' : iso === 'ZMB' ? 'Zambia' : 'Thailand'));
                const t = chip ? chip.textContent.replace(/\s+/g, ' ') : '';
                const peak = ((1 + a[ev].pct / 100) * (1 + an.peak_pct / 100) - 1) * 100;
                const want = [pct(a.now.pct), ev + ' ' + pct(a[ev].pct), 'peak ' + pct(peak),
                    'normal ' + (e.normal_at_peak_pct == null ? 'n/a' : pct(e.normal_at_peak_pct)),
                    'with El Niño ' + pct(e.peak_pct) + ' by ' + M[+e.peak_month.slice(5, 7) - 1] + ' ' + e.peak_month.slice(0, 4)];
                if (iso === 'THA') want.push('Bangkok wholesale');
                const main = chip && chip.querySelector('.enso-pchip-v');
                want.forEach(w => { if (!t.includes(w)) bad.push(iso + ' lacks ' + w); });
                if (!main || main.textContent !== pct(a.now.pct)) bad.push(iso + ' main number');
            }
            return bad.length ? bad.join('; ') : true;
        }"""
        _r = page.evaluate(_chip_js, '2023-24')
        check("Prices chips: now, 2023-24 same stage and peak, normal year and El Niño-effect replay equal the file" + ('' if _r is True else f' ({_r})'), _r is True and page.evaluate("""() => {
            const tag = document.getElementById('enso-maptag').textContent;
            return !document.querySelector('[data-pstage]') && tag.startsWith('After inflation, Mar')
                && tag.includes('no better than replaying them: not shown.') && tag.includes('take out inflation and world-price moves')
                && !/forecast:/i.test(tag) && document.querySelectorAll('[data-pev]').length === 2;
        }"""))
        page.click('[data-pev="2015-16"]'); page.wait_for_timeout(1200)
        _r = page.evaluate(_chip_js, '2015-16')
        check("Prices 2015-16 switch: chips re-read the same stage, peak and replay of 2015-16" + ('' if _r is True else f' ({_r})'), _r is True)
        page.click('[data-pev="2023-24"]'); page.wait_for_timeout(900)
        page.click('[data-pview="pays"]'); page.wait_for_timeout(900)
        check("Prices 'Who pays' view: buyers filled by tonnes from the outlook file, a chip each, no circles, modelled frame", page.evaluate("""async () => {
            const W = (await (await fetch('data/enso_outlook.json')).json()).data.who_pays;
            const isos = new Set(W.flatMap(c => (c.extra_import_kt ? [c.iso] : []).concat((c.buyers || []).map(b => b.iso))));
            const cols = ['#2c3f52', '#3f5f7d', '#6388ab', '#9dbfdc']; const filled = new Set(); let circles = 0;
            _stageHMap.eachLayer(l => { const p = l.feature && l.feature.properties, iso = p && (p.ISO_A3 || p.ADM0_A3);
                if (iso && isos.has(iso) && cols.includes(l.options.fillColor)) filled.add(iso);
                if (l instanceof L.CircleMarker && !l.feature && l.options.radius >= 4) circles++; });
            return filled.size === isos.size && circles === 0 && document.querySelectorAll('#enso-map .enso-pchip').length === isos.size
                && document.getElementById('enso-mapwrap').dataset.kind === 'modelled'
                && document.getElementById('enso-legend').textContent.includes('lost from its own harvest');
        }"""))
        page.click('[data-pview="since"]'); page.wait_for_timeout(600)
        check("no price-model number on Prices: the model failed its skill test", page.evaluate("""async () => {
            const P = (await (await fetch('data/enso_price_outlook.json')).json()).data;
            const t = document.getElementById('subview-ensomoney').textContent + document.getElementById('enso-mapwrap').textContent;
            return P.model_status !== 'no_skill' || (P.rows.every(r => r.model == null) && !/forecast of|price forecast:/i.test(t));
        }"""))
        check("Who pays follows the map; the past-price plate has a 2026 row from the FAO index and one fold", page.evaluate("""async () => {
            const order = [...document.querySelectorAll('#subview-ensomoney > *')].map(e => e.id || e.className);
            const F = (await (await fetch('data/fao_ffpi.json')).json()).data.series.map(x => x.fpi), n = F.length;
            const avg = a => a.reduce((t, x) => t + x, 0) / a.length, prev = (avg(F.slice(n - 6)) / avg(F.slice(n - 12, n - 6)) - 1) * 100;
            const pl = document.querySelector('.enso-pastprice-plate'), now = pl.querySelector('.enso-pp-nowrow');
            const txt = now ? now.textContent.replace('−', '-') : '';
            return order.indexOf('enso-whopays') === order.findIndex(x => /enso-mapgrid/.test(x)) + 1
                && !!now && txt.includes((prev > 0 ? '+' : '') + prev.toFixed(1) + '%')
                && pl.querySelectorAll('.plate-body > details').length === 1 && /up to 9% for food/.test(pl.textContent);
        }"""))
        check("maize panels are scaled to their own data and carry the replay's past misses", page.evaluate("""() => {
            const tops = [...document.querySelectorAll('.enso-pa-svg')].map(s => Math.max(...[...s.querySelectorAll('text.enso-hw-t')].map(t => +t.textContent).filter(Number.isFinite)));
            return tops.length >= 6 && new Set(tops).size >= 3 && document.querySelectorAll('.enso-pa-miss').length >= 3
                && /missed the highest month by about \d+ points/.test(document.querySelector('.enso-pa-plate .enso-est-block').textContent);
        }"""))
        # 2026-09-29 round 2: the dashed estimate and its ± are the El Niño-effect replay (effect_replay / replay_skill).
        check("maize panels: dashed estimate and ± from the El Niño-effect replay", page.evaluate("""async () => {
            const P = (await (await fetch('data/enso_price_outlook.json')).json()).data;
            const r = P.rows.find(x => x.iso3 === 'ZAF' && x.staple === 'maize');
            const cell = [...document.querySelectorAll('.enso-pa-cell')].find(c => c.querySelector('h4').textContent.startsWith('South Africa'));
            const miss = cell && cell.querySelector('.enso-pa-miss');
            const A = (await (await fetch('data/enso_price_analogs.json')).json()).data.countries.find(c => c.iso === 'ZAF');
            const now = A.events['2026-27'].slice(-1)[0][1], tops = Object.values(r.effect_replay.paths).map(p => Math.round(now * (1 + p.peak_pct / 100) - 100)).sort((a, b) => a - b);
            const pct = d => (d > 0 ? '+' : d < 0 ? '−' : '') + Math.abs(d) + '%';
            const est = cell && cell.querySelector('.is-est');
            const block = document.querySelector('.enso-pa-plate .enso-est-block').textContent;
            return !!miss && miss.textContent === '±' + Math.round(r.effect_replay.skill.mae_adjusted) + ' pts'
                && !!est && est.textContent === 'est. ' + pct(tops[0]) + ' to ' + pct(tops[tops.length - 1])
                && block.includes('about ' + Math.round(P.replay_skill.mae_adjusted) + ' points') && block.includes('normal year plus the El Niño effect');
        }"""))

        # 2026-09-28: the El Niño month slider was removed at the owner's request (it mixed observed, estimated
        # and fitted values in one control); each lens keeps its dated plates instead.
        check("no month slider on the El Niño lenses", page.evaluate("() => !document.querySelector('[data-tl], #enso-tl')"))
        # 2026-09-28 (owner): the Ocean map can show the average December-February anomaly of past El Niño winters by
        # strength, from data/sst_composites.json. The strip, key and caption follow the slider; leaving Ocean resets it.
        page.evaluate("showTab('elnino')")
        # 2026-09-28 (owner: "a bar you can drag back n forth with months"): the time control is a scrubber under the
        # map; its Past zone opens the class average that matches CPC's forecast strength.
        page.wait_for_selector('#enso-scrub [data-sst-view="past"]')
        page.click('#enso-scrub [data-sst-view="past"]')
        page.wait_for_timeout(700)
        check("the Ocean composite shows past very strong El Niño winters, sea and rain, from the composite file", page.evaluate("""async () => {
            const C = (await (await fetch('data/sst_composites.json')).json()).data, vs = C.classes.find(c => c.key === 'very_strong');
            const tag = document.getElementById('enso-maptag').textContent, key = document.getElementById('enso-legend').textContent;
            const strip = document.getElementById('enso-weekly').textContent, head = document.querySelector('#enso-mapwrap > .enso-plate-h').textContent;
            const cap = tag;  // 2026-09-28: the map description names the winters averaged
            return !!vs && tag.includes('not a forecast') && key.includes('average of ' + vs.n + ' past winters') && key.includes('Rain on land')
                && vs.events.every(e => cap.includes(e.label.replace('-', '–'))) && strip.includes('Very strong') && /very strong El Niños, Dec/.test(head)
                && !!document.querySelector('.enso-rain-canvas') && document.querySelectorAll('#enso-scrub .sc-win').length === C.classes.reduce((n, c) => n + c.events.length, 0)
                && document.querySelectorAll('#enso-scrub .sc-win.is-cls').length === vs.n;
        }"""))
        page.click('[data-sst-season="SON"]')
        page.wait_for_timeout(500)
        check("the Ocean composite season switch changes the maps and says so", page.evaluate("() => /Sep/.test(document.querySelector('#enso-mapwrap > .enso-plate-h').textContent) && /Sep/.test(document.getElementById('enso-weekly').textContent)"))
        # 2026-09-28 (owner: "past el nino should map historical el nino from data"): one observed winter on its own,
        # from data/sst_winters/<label>.json, named as observed; the outlook is a forecast with the dashed frame.
        page.click('[data-sst-season="DJF"]')
        page.click('[data-sst-win="2015-16"]')
        page.wait_for_function("() => /in the 2015–16 El Niño/.test(document.querySelector('#enso-mapwrap > .enso-plate-h').textContent)", timeout=15000)
        check("one past winter opens from its own observed file and says so", page.evaluate("""async () => {
            const W = (await (await fetch('data/sst_winters/2015-16.json')).json()).data, tag = document.getElementById('enso-maptag').textContent;
            const strip = document.getElementById('enso-weekly').textContent, n = W.box_means_c.DJF.nino34;
            return W.label === '2015-16' && !!W.maps.DJF && tag.includes('2015–16') && tag.includes('Observed, not a forecast')
                && strip.includes('Niño 3.4 ' + (n >= 0 ? '+' : '−') + Math.abs(n).toFixed(1))
                && document.querySelector('[data-sst-win="2015-16"]').classList.contains('is-pick') && !!document.querySelector('.enso-rain-canvas');
        }"""))
        # 2026-09-28 (owner: "include where there were natural events"): the winter's sourced events are marked on the
        # map, one https source each; the time bar's thumb sits on the pressed view.
        check("a past winter marks its sourced natural events, and the time bar thumb sits on Past", page.evaluate("""async () => {
            const E = (await (await fetch('data/enso_past_events.json')).json()).data.events.filter(e => e.winter === '2015-16');
            const marks = [...document.querySelectorAll('#enso-map .enso-pev')].reduce((n, m) => n + (+(m.querySelector('[data-n]') || {dataset: {n: 1}}).dataset.n), 0), sc = document.getElementById('enso-scrub'), hd = sc.querySelector('.sc-handle');
            return E.length > 0 && marks === E.length && E.every(e => /^https:\\/\\//.test(e.source.url))
                && sc.dataset.v === 'past' && hd.getAttribute('aria-valuetext').startsWith('2015–16');
        }"""))
        # The scrubber drags and steps: from a past winter, the "2026" switch brings back this year's track; pull the knob
        # to the far right (the last outlook season), then Home from the keyboard lands on the first month shown.
        page.click('#enso-scrub .sc-mode[data-sst-view="now"]')
        page.wait_for_timeout(500)
        box = page.locator('#enso-scrub .sc-track').bounding_box()
        knob = page.locator('#enso-scrub .sc-knob').bounding_box()
        page.mouse.move(knob['x'] + knob['width'] / 2, knob['y'] + knob['height'] / 2)
        page.mouse.down()
        page.mouse.move(box['x'] + box['width'] * .6, box['y'] + 40, steps=6)
        page.mouse.move(box['x'] + box['width'] - 2, box['y'] + 40, steps=6)
        page.mouse.up()
        page.wait_for_timeout(600)
        dragged = page.evaluate("() => document.getElementById('enso-scrub').dataset.v + '|' + (document.querySelector('#enso-scrub .is-ol.is-pick') || {getAttribute() { return ''; }}).getAttribute('data-sst-ol')")
        page.focus('#enso-scrub .sc-handle')
        page.keyboard.press('Home')
        page.wait_for_timeout(900)
        check("the time scrubber drags to the last outlook season and Home steps to the first month shown", page.evaluate("""d => {
            const hd = document.querySelector('#enso-scrub .sc-handle'), first = document.querySelector('#enso-scrub .sc-track [data-i="0"]');
            const last = [...document.querySelectorAll('#enso-scrub [data-sst-ol]')].pop();
            return d === 'outlook|' + last.dataset.sstOl && hd.getAttribute('aria-valuenow') === '0' && first.classList.contains('is-pick') && !!first.dataset.sstMon
                && /^Sea temperature (and rain )?against normal, \\w+ \\d{4}/.test(document.querySelector('#enso-mapwrap > .enso-plate-h').textContent);
        }""", dragged))
        if page.locator('#enso-scrub [data-sst-view="outlook"]').count():
            page.click('#enso-scrub [data-sst-view="outlook"]')
            page.wait_for_timeout(500)
            check("the outlook view is a labelled forecast in the modelled frame", page.evaluate("""async () => {
                const O = (await (await fetch('data/seasonal_outlook.json')).json()).data, head = document.querySelector('#enso-mapwrap > .enso-plate-h').textContent;
                const tag = document.getElementById('enso-maptag').textContent, strip = document.getElementById('enso-weekly').textContent;
                // 2026-09-28 (owner: "outlook should be 6 months"): six monthly NMME maps, lead 1 to 6.
                const list = O.months && O.months.length ? O.months : O.seasons;
                return list.length === (O.months ? 6 : 3) && head.startsWith('Forecast sea temperature') && head.includes(list[0].label) && tag.includes('A forecast, not observed') && strip.includes('forecast')
                    && document.getElementById('enso-mapwrap').classList.contains('is-modelled') && document.querySelectorAll('#enso-scrub [data-sst-ol]').length === list.length;
            }"""))
            # 2026-09-28, fifth pass (owner: "the drought/ rain overlay in the outlook seems to have failed ... use real
            # data real modelling ... the outlook adjusts to this live data"): the outlook paints the drought index of the
            # three months to each forecast month (NMME joined with the 1951-2020 El Niño fit, observed months carried in),
            # and every forecast month shows the official forecasts that name it.
            # 2026-09-29 (owner: "the outlook needs to have real predictions make sure to check them"): each month paints
            # its own forecast, scored in the key against the official forecasts it marks; observed months stay in the hover.
            check("the outlook paints each month's forecast, scores it against the official forecasts, and marks them", page.evaluate("""async () => {
                const O = (await (await fetch('data/seasonal_outlook.json')).json()).data, m = O.months[0], W = (O.rain_model || {}).windows || [];
                const key = document.getElementById('enso-legend').textContent, tag = document.getElementById('enso-maptag').textContent;
                const ev = await fetch('data/enso_outlook_events.json').then(r => r.ok ? r.json() : null).catch(() => null);
                const n = ev ? ev.data.events.filter(e => e.months.includes(m.key)).length : 0;
                const marks = [...document.querySelectorAll('#enso-map .enso-pev.is-now')].reduce((k, x) => k + (+(x.querySelector('[data-n]') || {dataset: {n: 1}}).dataset.n), 0);
                const col = document.querySelector('#enso-scrub [data-sst-ol="' + m.key + '"] .sc-c').textContent;
                return Array.isArray(m.maps.rain_spi) && m.maps.rain_spi.length === 7488 && Array.isArray(m.maps.rain_spi3) && !!document.querySelector('.enso-rain-canvas')
                    && /Rain on land, \\w+ \\d{4}: forecast/.test(key) && /Rain: the forecast for/.test(tag) && W.length === O.months.length && W[0].observed.length > 0
                    && (!ev || /official forecasts for these months: at their places it agrees on \\d+ month points, differs on \\d+/.test(key))
                    && marks === n && col === (n ? String(n) : '');
            }"""))
        page.evaluate("showTab('ensowater')")
        page.evaluate("showTab('elnino')")
        page.wait_for_selector('[data-sst-view="now"]')
        # Rain on land in this view only when the rain feed is loaded, and then the key names its window.
        check("leaving Ocean resets the map to now, rain on land only with a dated key", page.evaluate("""() => {
            const key = document.getElementById('enso-legend').textContent;
            return document.querySelector('[data-sst-view="now"]').getAttribute('aria-pressed') === 'true'
                && !!document.querySelector('.enso-rain-canvas') === /last (7|30) days/.test(key)
                && document.getElementById('enso-weekly').textContent.includes('CPC weekly');
        }"""))
        # 2026-09-28 (owner: "go back 6 months ... change based on 30 day and 7 day to show the movement"): the 2026 track
        # steps through the last complete months (OISST monthly means), the last 30 days and the last 7; sea and rain
        # switch window together, and the Now view marks only reported floods and droughts, never past-winter events.
        if page.locator('[data-sst-now="30"]').count():
            page.click('[data-sst-now="30"]')
            page.wait_for_timeout(500)
            check("the 30-day stop switches sea and rain to the last 30 days and says so in key, caption and strip", page.evaluate("""() =>
                /last 30 days/.test(document.getElementById('enso-legend').textContent) && /30-day mean/.test(document.getElementById('enso-maptag').textContent)
                && /last 30 days/.test(document.getElementById('enso-maptag').textContent) && /^Last 30 days/.test(document.getElementById('enso-weekly').textContent)
                && document.querySelector('#enso-scrub .sc-handle').getAttribute('aria-valuetext').startsWith('Last 30 days')
                && !document.querySelector('#enso-map .enso-pev:not(.is-now)')"""))
            page.click('[data-sst-now="7"]')
            page.wait_for_timeout(400)
            # 2026-09-28 (owner: "the 30 d should have its own headlines ... only add headlines that can be a result or
            # impact of el nino"): a stop marks only the checked headlines of its own window, and the count on the track
            # is the same number.
            # 2026-09-28, fifth pass (owner: "make sure teh 7 day automatically updates with new info"): the week also shows
            # marks picked by rule every six hours from the Reported feeds (data/enso_auto_events.json), never unlabelled.
            check("the 7-day stop marks the checked El Niño headlines of its own week plus the wire's, and the track counts them", page.evaluate("""async () => {
                const E0 = (await (await fetch('data/enso_recent_events.json')).json()).data.events, w = (await (await fetch('data/rain_anomaly.json')).json()).data.week;
                const A = await fetch('data/enso_auto_events.json').then(r => r.ok ? r.json() : null).then(j => j ? j.data.events : []).catch(() => []);
                if (!A.every(e => e.checked === false && /^https:\\/\\//.test(e.source.url))) return false;
                const E = E0.concat(A), n = E.filter(e => e.date_start <= w.end && e.date_end >= w.start).length;
                const marks = [...document.querySelectorAll('#enso-map .enso-pev.is-now')].reduce((k, m) => k + (+(m.querySelector('[data-n]') || {dataset: {n: 1}}).dataset.n), 0), col = document.querySelector('#enso-scrub [data-sst-now="7"] .sc-c').textContent;
                return n > 0 && marks === n && col === String(n) && E0.every(e => ['attributed', 'consistent'].includes(e.enso_link) && /^https:\\/\\//.test(e.source.url));
            }"""))
        # 2026-09-28 (owner: "make wetter on 7 day and more drought be an overlay on 30 day"): the 7-day stop keeps the
        # 30-day picture as its base and stripes the week on top; the 30-day stop has no stripes. The Past El Niños
        # button sits at the left end of the 2026 track.
        check("the 7-day stop glazes the week over the 30-day picture, the 30-day stop does not, and Past El Niños sits left of the track", page.evaluate("""() => {
            const over = !!document.querySelector('#enso-map .enso-rain-over'), key = document.getElementById('enso-legend').textContent;
            const btn = document.querySelector('#enso-scrub .sc-end[data-sst-view="past"]'), track = document.querySelector('#enso-scrub .sc-track');
            return over && /last 30 days/.test(key) && /darker where the week adds/.test(key) && !!btn && btn.getBoundingClientRect().right <= track.getBoundingClientRect().left;
        }"""))
        page.click('[data-sst-now="30"]')
        page.wait_for_timeout(400)
        check("the 30-day stop has no week overlay", page.evaluate("() => !document.querySelector('#enso-map .enso-rain-over') && !/darker where the week adds/.test(document.getElementById('enso-legend').textContent)"))
        page.click('[data-sst-now="7"]')
        page.wait_for_timeout(400)
        mons = page.evaluate("() => [...document.querySelectorAll('#enso-scrub [data-sst-mon]')].map(b => b.getAttribute('data-sst-mon'))")
        check("the 2026 track carries six past months, then the last 30, 14 and 7 days, then the outlook", len(mons) == 6 and page.evaluate("""() => {
            const k = [...document.querySelectorAll('#enso-scrub .sc-track [data-i]')].map(b => b.dataset.sstMon ? 'm' : b.dataset.sstNow ? 'n' + b.dataset.sstNow : b.dataset.sstOl ? 'o' : '?').join('');
            return /^m{6}n30(n14)?n7o{6}$/.test(k);
        }"""))
        # 2026-09-29 (owner: "look back 14 days then 30 days not per week. so its last 7, 14, 30 thats the recent ones that
        # should auto update"): a rolling 14-day stop between 30 and 7 days, its sea and rain from the same newest day,
        # glazed over the 30-day picture, with its own headlines.
        if page.locator('[data-sst-now="14"]').count():
            page.click('[data-sst-now="14"]')
            page.wait_for_timeout(600)
            check("the 14-day stop paints the last 14 days of sea and rain over the 30-day picture, with its own headlines", page.evaluate("""async () => {
                const R = (await (await fetch('data/rain_anomaly.json')).json()).data, w = R.d14;
                const E = (await (await fetch('data/enso_recent_events.json')).json()).data.events.filter(e => e.date_start <= w.end && e.date_end >= w.start).length;
                const A = await fetch('data/enso_auto_events.json').then(r => r.ok ? r.json() : null).then(j => j ? j.data.events.filter(e => e.date_start <= w.end && e.date_end >= w.start).length : 0).catch(() => 0);
                const head = document.querySelector('#enso-mapwrap > .enso-plate-h').textContent, key = document.getElementById('enso-legend').textContent;
                const col = document.querySelector('#enso-scrub [data-sst-now="14"] .sc-c').textContent;
                return /Sea temperature and rain against normal, last 14 days/.test(head) && /^Last 14 days/.test(document.getElementById('enso-weekly').textContent)
                    && !!document.querySelector('#enso-map .enso-rain-over') && /darker where the 14 days add/.test(key) && /last 30 days/.test(key)
                    && w.end === R.week.end && col === ((E + A) ? String(E + A) : '') && document.querySelector('#enso-scrub .sc-handle').getAttribute('aria-valuetext').startsWith('Last 14 days');
            }"""))
            page.click('[data-sst-now="7"]')
            page.wait_for_timeout(400)
        if mons:
            page.click(f'[data-sst-mon="{mons[-1]}"]')
            page.wait_for_timeout(500)
            check("a month stop paints that month's observed sea mean and says which", page.evaluate("""m => {
                const [y, mo] = m.split('-'), name = ['January','February','March','April','May','June','July','August','September','October','November','December'][+mo - 1] + ' ' + y;
                // The month's own rain too (data/rain_months.json, CPC gauges + CHIRPS), and its checked El Niño headlines only.
                return document.querySelector('#enso-mapwrap > .enso-plate-h').textContent.includes('Sea temperature and rain against normal, ' + name)
                    && document.getElementById('enso-weekly').textContent.includes('OISST monthly mean') && !!document.querySelector('.enso-rain-canvas')
                    && new RegExp('last 30 days|' + name).test(document.getElementById('enso-legend').textContent)
                    && !document.querySelector('#enso-map .enso-pev:not(.is-now)');
            }""", mons[-1]))
            # 2026-09-28 (owner: "this prompt is on all of the last couple look backs"): hover a headline, then step the
            # scrubber with the keyboard: no tooltip may stay open on the next stops.
            marks = page.locator('#enso-map .enso-pev.is-now')
            if marks.count():
                box = marks.first.bounding_box()
                page.mouse.move(box['x'] + box['width'] / 2 - 6, box['y'] + box['height'] / 2 - 6)
                page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2, steps=3)
                page.wait_for_timeout(250)
                page.focus('#enso-scrub .sc-handle')
                page.keyboard.press('ArrowLeft')
                page.wait_for_timeout(400)
                page.keyboard.press('ArrowLeft')
                page.wait_for_timeout(400)
                check("stepping the scrubber leaves no headline tooltip open", page.evaluate("() => ![...document.querySelectorAll('#enso-map .leaflet-tooltip')].some(t => t.offsetParent && !t.classList.contains('enso-price-lbl'))"))
            page.click('#enso-scrub [data-sst-view="now"]')
            page.wait_for_timeout(400)

        print("\nTier 1: figures come from the feeds (court 2026-09-23)")
        src = (ROOT / "index.html").read_text()
        check("no hand-typed feed figures left in the page",
              not any(s in src for s in ("SOI −18.7", "up to 40 records", "a European drought among them")))
        page.evaluate("showTab('ensoharvest')")
        page.wait_for_selector('#subview-ensoharvest.active .enso-subview-meta')
        check("the outlook shows only pairs whose El Niño slope passes, adds no tonnes to in-season harvests and names every pair it leaves out", page.evaluate("""async () => {
            const O = (await (await fetch('data/enso_outlook.json')).json()).data;
            const shown = O.rows_all.filter(r => r.status === 'shown'), left = O.rows_all.filter(r => r.status !== 'shown');
            const rows = [...document.querySelectorAll('.enso-outlook-plate tbody tr:not(.enso-ol-year)')];
            const note = document.querySelector('.enso-outlook-plate .enso-ol-excluded');
            return rows.length === shown.length && shown.filter(r => r.in_season).every(r => r.change_kt_record === null)
                && shown.every(r => r.q_nino < 0.10) && (!left.length || (note && left.every(r => note.textContent.includes(r.crop === 'corn' ? 'maize' : r.crop))));
        }"""))
        # 2026-09-24: the list uses the outlook's own predicate, an El Niño slope
        # that passes on its own (shared-IOD and La Niña-only pairs are out).
        check("the harvest country list names only countries whose El Niño slope passes", page.evaluate("""async () => {
            const O = (await (await fetch('data/enso_outlook.json')).json()).data;
            const want = new Set(O.rows_all.filter(r => r.status === 'shown').map(r => r.iso));
            const chips = document.querySelectorAll('#enso-land-head .enso-country-list .enso-chip').length;
            return chips === want.size && /passes on its own/.test(document.getElementById('enso-land-head').textContent);
        }"""))
        # 2026-09-29 audit: the 2027 plate opens with one plain sentence and at most three bullets (two since the
        # 2026-09-30 V2 audit, "declutter"; the source details moved into the method fold); the chart
        # legend moved into the method fold; the fitted-responses plate became a "Show per country" button;
        # the calendar is open and shows only the outlook's pairs until "All N pairs" is pressed.
        audit = page.evaluate("""async () => {
            const O = (await (await fetch('data/enso_outlook.json')).json()).data;
            const shown = O.rows_all.filter(r => r.status === 'shown');
            const worst = shown.filter(r => !r.in_season && r.change_kt_record < 0).sort((a, b) => a.change_kt_record - b.change_kt_record)[0];
            const lede = document.querySelector('.enso-outlook-plate .enso-ol-lead > p.enso-ol-lede');
            const bullets = document.querySelectorAll('.enso-outlook-plate .enso-ol-lead > ul > li').length;
            const legendLine = [...document.querySelectorAll('.enso-outlook-plate li')].find(li => li.textContent.startsWith('Bar: tonnes'));
            const cal = document.querySelector('#enso-calendar .enso-cal');
            const visible = () => [...cal.querySelectorAll('.cal-row:not(.cal-head)')].filter(r => r.firstElementChild.getBoundingClientRect().height > 0).length;
            const calRows = [...cal.querySelectorAll('.cal-row:not(.cal-head)')].filter(r => shown.some(o => o.iso === r.dataset.iso && o.crop === r.dataset.crop)).length;
            const shortRows = visible(), plateH = document.querySelector('#enso-calendar .enso-cal-plate').getBoundingClientRect().height;
            const btn = document.querySelector('#enso-calendar .enso-cal-all'); btn.click();
            const allRows = [...document.querySelectorAll('#enso-calendar .cal-row:not(.cal-head)')].filter(r => r.firstElementChild.getBoundingClientRect().height > 0).length;
            document.querySelector('#enso-calendar .enso-cal-all').click();
            return {lede: !!lede && lede.textContent.includes((Math.abs(worst.change_kt_record) / 1000).toFixed(1) + ' Mt') && lede.textContent.includes(worst.harvest),
                    bullets: bullets > 0 && bullets <= 2, legendFolded: !!legendLine && !!legendLine.closest('details'),
                    noCoefPlate: [...document.querySelectorAll('#tab-elnino .enso-plate-t')].every(t => t.textContent !== 'Fitted crop responses')
                        && /Show per country/.test(document.querySelector('#enso-harvest-fig > summary').textContent),
                    calOpen: !document.querySelector('#enso-calendar details.enso-cal-fold') && shortRows === calRows && allRows === cal.querySelectorAll('.cal-row:not(.cal-head)').length && allRows > shortRows,
                    calH: Math.round(plateH)};
        }""")
        check("Show per country is the third fold in the 2027 plate's footer, styled as the other two", page.evaluate("""() => {
            const f = document.querySelector('.enso-outlook-plate #enso-harvest-fig'), hc = document.querySelector('.enso-outlook-plate .enso-hc-fold');
            if (!f || !hc) return false;
            const a = getComputedStyle(f.querySelector('summary')), b = getComputedStyle(hc.querySelector('summary'));
            return f.classList.contains('enso-evidence-note') && a.borderTopWidth === '0px' && a.fontSize === b.fontSize && a.color === b.color
                && f.getBoundingClientRect().top >= document.querySelector('.enso-outlook-plate .enso-ol-tablefold').getBoundingClientRect().top;
        }"""))
        check("2027 plate: plain sentence from the data, at most three bullets, chart legend in the fold",
              audit["lede"] and audit["bullets"] and audit["legendFolded"], str(audit))
        check("fitted responses sit behind Show per country; the calendar is open with the outlook's pairs first",
              audit["noCoefPlate"] and audit["calOpen"] and audit["calH"] <= 520, str(audit))
        check("the Reported badge counts what its label names",
              page.locator('#viewbtn-ensolive .enso-view-desc').inner_text().strip() == 'news & alerts')

        check("no console errors", not errors, "; ".join(errors[:2]))
        browser.close()

    httpd.shutdown()
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} passed")
    if FAILURES:
        print("failed: " + ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
