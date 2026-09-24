#!/usr/bin/env python3
"""
refresh_import_ports.py — the ports that feed the El Nino-hit import markets.

The Shipping lens measured the export lanes (Panama, the Mississippi, the
Parana) but not the other end: the ports through which southern and eastern
Africa import grain in a drought year. IMF PortWatch publishes daily AIS-based
port calls and estimated import tonnes by vessel type for each port; this reads
dry bulk (the vessel class that carries grain, and also fertiliser, coal and
ore) for the gateway ports of the FEWS NET concern regions.

  Southern Africa  Durban, Maputo, Beira, Nacala, Dar es Salaam, Walvis Bay
  Horn and East    Mombasa, Djibouti, Berbera, Port Sudan

For each port: the last 28 days of dry-bulk imports against the same 28 days a
year earlier and against the mean of that window in every earlier year the feed
holds (from 2019), plus monthly totals for two years. Estimated tonnes are
PortWatch's, from AIS draught changes; they are not customs data and not grain
alone, and the page says so.

Source: IMF PortWatch, anonymous ArcGIS FeatureServer, no key.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402

URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/"
       "Daily_Ports_Data/FeatureServer/0/query")
UA = {"User-Agent": "Mozilla/5.0 (FoodShield AI; public food-security dashboard)", "Accept": "*/*"}
WINDOW = 28
PORTS = [
    # portid, name, iso3, region, what it serves (named inland routes only)
    ("port311", "Durban", "ZAF", "Southern Africa", "South Africa's main grain port; transit to Zimbabwe, Botswana"),
    ("port702", "Maputo", "MOZ", "Southern Africa", "southern Mozambique; corridor to South Africa and Eswatini"),
    ("port137", "Beira", "MOZ", "Southern Africa", "Beira corridor to Zimbabwe, Malawi, Zambia"),
    ("port784", "Nacala", "MOZ", "Southern Africa", "Nacala corridor to Malawi"),
    ("port278", "Dar Es Salaam", "TZA", "Southern Africa", "Tanzania; central and TAZARA corridors to Zambia, Malawi"),
    ("port1381", "Walvis Bay", "NAM", "Southern Africa", "Namibia; Trans-Kalahari corridor to Botswana"),
    ("port757", "Mombasa", "KEN", "East Africa", "Kenya; Northern Corridor to Uganda, South Sudan"),
    ("port294", "Djibouti", "DJI", "East Africa", "Ethiopia's main import port"),
    ("port146", "Berbera", "SOM", "East Africa", "Somaliland; corridor to Ethiopia"),
    ("port977", "Port Sudan", "SDN", "East Africa", "Sudan's main port"),
]


def fetch(portid: str, since: str) -> list[dict]:
    rows, offset = [], 0
    while True:
        r = http_get(URL, timeout=90, headers=UA, retries=3, params={
            "where": f"portid='{portid}' AND date >= '{since}'",
            "outFields": "date,portcalls_dry_bulk,import_dry_bulk",
            "orderByFields": "date ASC", "resultOffset": offset, "resultRecordCount": 2000,
            "returnGeometry": "false", "f": "json"}).json()
        if "error" in r:
            raise RuntimeError(f"{portid}: {r['error']}")
        feats = [f["attributes"] for f in r.get("features", [])]
        rows += feats
        if not r.get("exceededTransferLimit") or not feats:
            return rows
        offset += len(feats)


def summarise(rows: list[dict]) -> dict:
    by = {r["date"]: r for r in rows if r.get("date")}
    last = max(by)
    end = date.fromisoformat(last)

    def window_sum(e: date) -> tuple[float, int, int]:
        days = [(e - timedelta(days=i)).isoformat() for i in range(WINDOW)]
        got = [by[d] for d in days if d in by]
        return (sum(g.get("import_dry_bulk") or 0 for g in got),
                sum(g.get("portcalls_dry_bulk") or 0 for g in got), len(got))

    now_t, now_calls, now_n = window_sum(end)
    past = {}
    for y in sorted({int(d[:4]) for d in by}):
        if y >= end.year:
            continue
        try:
            e = end.replace(year=y)
        except ValueError:
            e = end.replace(year=y, day=28)
        t, c, n = window_sum(e)
        if n >= WINDOW - 3:
            past[y] = {"t": t, "calls": c}
    ya = past.get(end.year - 1)
    earlier = [v["t"] for v in past.values()]
    mean_prior = sum(earlier) / len(earlier) if earlier else None
    monthly = defaultdict(float)
    cutoff = (end - timedelta(days=730)).isoformat()
    for d, r in by.items():
        if d >= cutoff:
            monthly[d[:7]] += r.get("import_dry_bulk") or 0
    return {
        "latest_date": last,
        "window_days": WINDOW, "days_observed": now_n,
        "dry_bulk_import_t": round(now_t), "dry_bulk_calls": now_calls,
        "year_ago_t": round(ya["t"]) if ya else None,
        "yoy_pct": round((now_t / ya["t"] - 1) * 100, 1) if ya and ya["t"] else None,
        "mean_prior_t": round(mean_prior) if mean_prior is not None else None,
        "prior_years": f"{min(past)}–{max(past)}" if past else None,
        "vs_mean_pct": round((now_t / mean_prior - 1) * 100, 1) if mean_prior else None,
        # The last month is partial; the page leaves it out of the chart.
        "monthly_t": [{"month": m, "t": round(v)} for m, v in sorted(monthly.items())],
    }


def main() -> int:
    since = (date.today() - timedelta(days=366 * 8)).isoformat()
    ports, unavailable = [], []
    for pid, name, iso, region, serves in PORTS:
        try:
            s = summarise(fetch(pid, since))
            ports.append({"portid": pid, "name": name, "iso3": iso, "region": region, "serves": serves, **s})
            print(f"  ok   {name}: {s['dry_bulk_import_t']:,} t in {WINDOW}d to {s['latest_date']}, "
                  f"y/y {s['yoy_pct']}%, vs {s['prior_years']} mean {s['vs_mean_pct']}%")
        except Exception as e:  # noqa: BLE001 -- one port down must not blank the rest
            unavailable.append({"port": name, "reason": f"{type(e).__name__}: {e}"})
            print(f"  FAIL {name}: {e}")
    if not ports:
        raise RuntimeError("no port reachable -- keeping the previous file")
    write_json("enso_ports.json", {"ports": ports, "unavailable": unavailable, "window_days": WINDOW,
                                   "url": "https://portwatch.imf.org/"},
               source="IMF PortWatch, Daily Ports Data (AIS-based port calls and estimated import tonnes)",
               notes="Dry-bulk imports at the gateway ports of the FEWS NET El Niño concern regions. Estimated from "
                     "AIS draught by PortWatch; dry bulk includes grain, fertiliser, coal and ore.",
               status="ok" if not unavailable else "partial")
    return 0


if __name__ == "__main__":
    sys.exit(main())
