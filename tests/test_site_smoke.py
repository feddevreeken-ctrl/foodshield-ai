#!/usr/bin/env python3
"""Smoke gate for every tab outside El Niño (which has its own suite).

Per tab, at 1440 and 390 px: the tab opens without page errors, renders
visible content, and does not scroll sideways. Run from the repo root:
    python3 tests/test_site_smoke.py
"""
from __future__ import annotations

import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TABS = ["global", "country", "commodities", "scenario", "forecast", "score", "compare",
        "companies", "disturbances", "livedata", "datastatus", "news", "about", "methodology"]
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f" — {detail}"))
    if not ok:
        FAILURES.append(name)


def serve(port: int) -> socketserver.TCPServer:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a: None

    class Quiet(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = Quiet(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> int:
    srv = serve(8871)
    base = "http://127.0.0.1:8871/index.html"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for width in (1440, 390):
            page = browser.new_page(viewport={"width": width, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script("try{localStorage.setItem('foodshield_skip_intro','1');"
                                 "localStorage.setItem('foodshield_hint_shown','1')}catch(e){}")
            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(2500)
            print(f"\n{width}px")
            for tab in TABS:
                errors.clear()
                page.evaluate("t => showTab(t)", tab)
                page.wait_for_timeout(1800)
                state = page.evaluate("""() => {
                    const pg = document.querySelector('.tab-page.active');
                    const cp = pg && (pg.querySelector('.content-page') || pg);
                    const text = pg ? pg.innerText.trim().length : 0;
                    const W = document.documentElement.clientWidth;
                    /* Page-level scroll, or a content container that scrolls sideways (overflow-x auto/scroll).
                       Content clipped by overflow:hidden is not a scroll, and maps pan by design. */
                    const ox = cp ? getComputedStyle(cp).overflowX : 'visible';
                    const inner = cp && (ox === 'auto' || ox === 'scroll') && cp.scrollWidth > cp.clientWidth + 1;
                    return {id: pg && pg.id, text, doc: document.documentElement.scrollWidth, W, ox, cpw: cp && cp.scrollWidth, cpc: cp && cp.clientWidth,
                            sideways: document.documentElement.scrollWidth > W + 1 || !!inner};
                }""")
                check(f"{width}px {tab}: opens without page errors", not errors, "; ".join(errors)[:300])
                check(f"{width}px {tab}: renders content", bool(state["id"]) and state["text"] > 200, str(state))
                check(f"{width}px {tab}: no sideways scroll", not state["sideways"], str(state))
            page.close()
        browser.close()
    srv.shutdown()
    total = len(TABS) * 2 * 3
    print(f"\n{total - len(FAILURES)}/{total} passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
