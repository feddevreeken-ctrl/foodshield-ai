#!/usr/bin/env python3
"""refresh_sst_anomaly.py -- the observed sea-surface temperature anomaly field.

Feeds the El Nino map. Until now the Pacific on that map was blank slate with
a dashed Nino 3.4 box drawn on it: the page described an ocean anomaly without
ever showing one. This collector fetches NOAA's OISST v2.1 daily anomaly
through the CoastWatch ERDDAP griddap endpoint, averages the last seven
available days (the same convention as CPC's weekly SST anomaly map), thins
the 0.25-degree field to a 2-degree grid, and ships it as one integer array
the page can paint on a canvas. No raster is generated here, so the palette
stays in the page where it can be validated, and every cell keeps a number.

Two ERDDAP datasets carry the product. The near-real-time one runs about a day
behind today and is preliminary; the final one is released roughly two weeks
later. The collector prefers NRT for currency and records which one it used
and the exact time window, so the plate can say "preliminary" when it is.

Honesty notes carried in the payload:
  * OISST anomalies are relative to OISST's own 1971-2000 daily climatology.
    CPC's ONI and weekly Nino 3.4 use ERSST and a 1991-2020 base, so a box
    mean computed from this grid will NOT equal CPC's number. The grid is for
    the picture; the indices remain the instrument. A grid box mean is still
    computed and shipped, labelled as a cross-check, never as the index.
  * A 2-degree thinning takes one 0.25-degree cell in eight, not an average.
    Fine structure along the Peruvian coast is therefore under-sampled; the
    payload says so.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402

ERDDAP = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"
DATASETS = (("ncdcOisst21NrtAgg_LonPM180", "OISST v2.1 near-real-time (preliminary)"),
            ("ncdcOisst21Agg_LonPM180", "OISST v2.1 final"))
UA = {"User-Agent": "FoodShield-AI data refresh (github.com/feddevreeken-ctrl/foodshield-ai)"}

STEP = 8                 # 0.25 deg * 8 = 2 deg
# Full Web Mercator extent (Leaflet clips at 85.05 degrees): the first grid
# centre at or above -85 is -84.875, so the 2 degree samples run to 84.875
# and the top cell edge sits past the map edge. Cells under sea ice are kept;
# OISST sets the surface there to the freezing point, so their anomaly is small.
LAT0, LAT1 = -85.0, 85.0
LON0, LON1 = -180.0, 178.0
DAYS = 7

# Nino boxes, for the cross-check only (lat_s, lat_n, lon_w, lon_e).
BOXES = {"nino12": (-10, 0, -90, -80), "nino3": (-5, 5, -150, -90),
         "nino34": (-5, 5, -170, -120), "nino4": (-5, 5, 160, -150)}


def _last_time(ds: str) -> datetime:
    url = f"{ERDDAP}/{ds}.json?time%5B(last)%5D"
    j = http_get(url, timeout=90, headers=UA, retries=2).json()
    t = j["table"]["rows"][0][0]
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def _fetch(ds: str, t0: datetime, t1: datetime) -> list:
    q = (f"anom%5B({t0.strftime('%Y-%m-%dT%H:%M:%SZ')}):1:({t1.strftime('%Y-%m-%dT%H:%M:%SZ')})%5D"
         f"%5B(0.0)%5D%5B({LAT0}):{STEP}:({LAT1})%5D%5B({LON0}):{STEP}:({LON1})%5D")
    url = f"{ERDDAP}/{ds}.json?{q}"
    j = http_get(url, timeout=300, headers=UA, retries=2).json()
    cols = j["table"]["columnNames"]
    return [dict(zip(cols, r)) for r in j["table"]["rows"]]


def build() -> dict:
    errors = []
    for ds, label in DATASETS:
        try:
            t1 = _last_time(ds)
            age = (datetime.now(timezone.utc) - t1).days
            if age > 40:
                raise RuntimeError(f"newest time {t1.date()} is {age} days old -- frozen feed")
            t0 = t1 - timedelta(days=DAYS - 1)
            rows = _fetch(ds, t0, t1)
            if len(rows) < 1000:
                raise RuntimeError(f"only {len(rows)} cells returned")
            break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{ds}: {type(e).__name__}: {e}")
            rows = None
    if not rows:
        raise RuntimeError("no OISST dataset reachable -- " + " | ".join(errors))

    lats = sorted({r["latitude"] for r in rows})
    lons = sorted({r["longitude"] for r in rows})
    li = {v: i for i, v in enumerate(lats)}
    lo = {v: i for i, v in enumerate(lons)}
    acc = [[0.0, 0] for _ in range(len(lats) * len(lons))]
    days = set()
    for r in rows:
        v = r.get("anom")
        days.add(r["time"])
        if v is None:
            continue
        k = li[r["latitude"]] * len(lons) + lo[r["longitude"]]
        acc[k][0] += float(v)
        acc[k][1] += 1
    grid = [int(round(10 * s / n)) if n else None for s, n in acc]

    def box_mean(b):
        s, n = 0.0, 0
        lat_s, lat_n, lon_w, lon_e = b
        for i, la in enumerate(lats):
            if not (lat_s <= la <= lat_n):
                continue
            for j, ln in enumerate(lons):
                inside = (lon_w <= ln <= lon_e) if lon_w < lon_e else (ln >= lon_w or ln <= lon_e)
                if not inside:
                    continue
                g = grid[i * len(lons) + j]
                if g is not None:
                    s += g / 10.0
                    n += 1
        return round(s / n, 2) if n else None

    vals = [g for g in grid if g is not None]
    return {
        "dataset": ds, "product": label,
        "source_url": f"{ERDDAP}/{ds}.html",
        "time_start": t0.date().isoformat(), "time_end": t1.date().isoformat(),
        "days_averaged": len(days),
        "preliminary": ds.startswith("ncdcOisst21Nrt"),
        "lat0": lats[0], "lon0": lons[0], "step_deg": STEP * 0.25,
        "nlat": len(lats), "nlon": len(lons),
        "encoding": "row-major from the southern edge, tenths of a degree C, null over land (ice-covered sea keeps a value)",
        "anom": grid,
        "range_c": [min(vals) / 10.0, max(vals) / 10.0],
        "box_means_c": {k: box_mean(b) for k, b in BOXES.items()},
        "box_note": ("Means of this 2-degree OISST grid over the Nino boxes, for a cross-check "
                     "of the picture against the indices only. They are NOT the ONI or CPC's "
                     "weekly values: those use ERSST and a 1991-2020 base, this field uses "
                     "OISST's 1971-2000 daily climatology."),
        "sampling_note": ("One 0.25-degree cell in eight in each direction, not an area "
                          "average; coastal structure off Peru is under-sampled. Rows run "
                          "from 84.875 S to 84.875 N, the Web Mercator extent; ice-covered "
                          "cells are kept, with the surface set to the freezing point by OISST."),
    }


def main() -> int:
    payload = build()
    write_json("sst_anomaly.json", payload, source="NOAA NCEI OISST v2.1 via CoastWatch ERDDAP",
               notes=("Seven-day mean sea-surface temperature anomaly on a 2-degree grid, "
                      "60S-60N, for the El Nino map. Anomaly base is OISST's 1971-2000 "
                      "climatology; do not read box means as the ONI."),
               status="preliminary" if payload["preliminary"] else "ok")
    print(f"[OK] OISST anomaly {payload['time_start']}..{payload['time_end']} "
          f"({payload['product']}) | {payload['nlat']}x{payload['nlon']} cells | "
          f"range {payload['range_c'][0]:+.1f}..{payload['range_c'][1]:+.1f} C | "
          f"box means {payload['box_means_c']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
