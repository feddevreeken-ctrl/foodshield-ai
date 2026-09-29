#!/usr/bin/env python3
"""Render the El Niño link-preview image (img/og/elnino.png, 1200x627) from the real Ocean map.

Serves the repo on a local port, opens the El Niño tab in headless Chrome, lets the map
draw from the committed data (sea-surface temperature and rain, as the page shows them),
captures the map with its own dated caption, and sets it under a one-line title band.
The page's map tools (search, zoom, reset) are hidden for the capture; nothing else is
changed. The PNG is quantised to stay under 300 KB, which LinkedIn and Slack handle well.

Hand-run, then commit img/og/elnino.png:

    python3 scripts/build_og_elnino.py            # port 8893
    python3 scripts/build_og_elnino.py --port 8899

Refresh it when the Ocean map's week moves on and a shared link should show the new one.
"""
from __future__ import annotations

import argparse
import base64
import functools
import html
import http.server
import io
import socketserver
import threading
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'img' / 'og' / 'elnino.png'
W, H = 1200, 627
TOP, BOTTOM = 60, 64
MAX_BYTES = 300_000


def serve(port: int) -> socketserver.TCPServer:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(Quiet, directory=str(ROOT))

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    httpd = Server(('127.0.0.1', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


CARD = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="{base}/js/vendor/fonts/fonts-critical.css">
<link rel="stylesheet" href="{base}/js/vendor/fonts/fonts-rest.css">
<style>
  html,body {{ margin:0; width:{w}px; height:{h}px; background:#0b0b0d; overflow:hidden; }}
  .top {{ height:{top}px; display:flex; align-items:center; justify-content:space-between; padding:0 32px; box-sizing:border-box; }}
  .t {{ font-family:'Instrument Serif',serif; font-size:40px; color:#f2efe6; letter-spacing:0.2px; }}
  .u {{ font-family:'Geist Mono',monospace; font-size:15px; color:#9a988f; }}
  .map {{ height:{maph}px; background:#0b1017 url({img}) center/cover no-repeat; }}
  .cap {{ height:{bottom}px; display:flex; flex-direction:column; justify-content:center; gap:4px; padding:0 32px; box-sizing:border-box;
          font-family:'Geist',sans-serif; font-size:14px; color:#b9b6ad; }}
  .cap div {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .cap b {{ color:#f2efe6; font-weight:600; font-size:15px; margin-right:12px; }}
  .cap i {{ font-style:normal; font-family:'Geist Mono',monospace; font-size:12.5px; color:#9a988f; }}
</style></head><body>
<div class="top"><span class="t">El Niño 2026-27 · FoodShield</span><span class="u">foodshield.nl/elnino</span></div>
<div class="map"></div>
<div class="cap"><div><b>{title}</b><i>{source}</i></div><div>{caption}</div></div>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8893)
    args = ap.parse_args()
    httpd = serve(args.port)
    base = f'http://127.0.0.1:{args.port}'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            ctx = browser.new_context(viewport={'width': 1440, 'height': 900}, device_scale_factor=2)
            ctx.add_init_script("try{localStorage.setItem('foodshield_skip_intro','1');localStorage.setItem('foodshield_hint_shown','1')}catch(e){}")
            page = ctx.new_page()
            page.goto(f'{base}/index.html?tab=elnino', wait_until='load', timeout=90_000)
            page.wait_for_load_state('networkidle', timeout=60_000)
            page.wait_for_selector('#enso-map-body .leaflet-overlay-pane canvas, #enso-map-body .leaflet-overlay-pane svg, #enso-map-body img.leaflet-image-layer', timeout=60_000)
            page.wait_for_timeout(4000)  # paint() is throttled behind a burst guard and a ripple
            info = page.evaluate("""() => {
                const tag = document.getElementById('enso-maptag');
                const t = document.querySelector('#enso-mapwrap .enso-plate-t');
                const s = document.querySelector('#enso-mapwrap .enso-plate-sub');
                return { caption: tag ? tag.textContent.trim() : '', title: t ? t.textContent.trim() : '', source: s ? s.textContent.trim() : '' };
            }""")
            if not info['caption']:
                raise SystemExit('map caption (#enso-maptag) is empty: the map did not draw')
            page.add_style_tag(content='#enso-map .enso-map-tools, #enso-map .leaflet-control-container { visibility:hidden !important; }')
            page.wait_for_timeout(300)
            shot = page.locator('#enso-map-body').screenshot(type='png')

            card = ctx.new_page()
            card.set_viewport_size({'width': W, 'height': H})
            card.set_content(CARD.format(
                base=base, w=W, h=H, top=TOP, bottom=BOTTOM, maph=H - TOP - BOTTOM,
                img='data:image/png;base64,' + base64.b64encode(shot).decode(),
                title=html.escape(info['title'] or 'Sea and rain'), source=html.escape(info['source']),
                caption=html.escape(info['caption'])))
            card.evaluate('document.fonts.ready')
            card.wait_for_timeout(500)
            png = card.screenshot(type='png', clip={'x': 0, 'y': 0, 'width': W, 'height': H})
            browser.close()
    finally:
        httpd.shutdown()

    img = Image.open(io.BytesIO(png)).convert('RGB').resize((W, H), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for colors in (256, 192, 128, 96, 64):
        buf = io.BytesIO()
        img.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(buf, 'PNG', optimize=True)
        if buf.tell() <= MAX_BYTES:
            break
    OUT.write_bytes(buf.getvalue())
    print(f'{OUT.relative_to(ROOT)}: {W}x{H}, {buf.tell() // 1024} KB, {colors} colours')
    print(f"caption: {info['title']} | {info['source']} | {info['caption']}")


if __name__ == '__main__':
    main()
