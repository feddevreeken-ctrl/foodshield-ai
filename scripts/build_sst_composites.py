#!/usr/bin/env python3
"""build_sst_composites.py -- what the ocean looked like in past El Nino winters, by strength.

Feeds the strength slider on the El Nino Ocean map. This is NOT a model run and
NOT a forecast. For each CPC strength class (weak, moderate, strong, very
strong) it averages the OBSERVED December-February sea-surface temperature
anomaly of the past El Nino winters in that class, and ships one integer grid
per class that the page's existing canvas layer can paint.

Data:
  * SST: NOAA ERSST v5 monthly on its native 2-degree grid, read through the
    CoastWatch ERDDAP griddap endpoint as NetCDF-3 (scipy reads it; no netCDF4
    needed). Decade chunks are cached in $FOODSHIELD_CACHE or
    /tmp/foodshield-sst-cache. A chunk is never refetched once cached, so
    ERSST's small revisions to recent months only arrive when a new winter
    completes and the last chunk gets a new end date (or the cache is cleared).
  * Anomalies are against a 1991-2020 monthly climatology computed here from
    the same ERSST file, one per calendar month.
  * Events and classes: CPC oni.ascii.txt. ONI values are rounded to one
    decimal as in CPC's table. An episode is a run of at least five
    consecutive overlapping seasons with ONI >= +0.5 (CPC's rule). An El Nino
    winter is a DJF inside an episode. Its class is the episode's peak ONI in
    the July-June year around that winter, so a two-winter episode such as
    1986-88 gives two winters with their own peaks (CPC's bins: weak +0.5 to
    +0.9, moderate +1.0 to +1.4, strong +1.5 to +1.9, very strong >= +2.0).

Two versions per class:
  * anom: each winter against 1991-2020. Older winters sit on a cooler
    baseline, so they read cooler than they were against their own climate.
  * anom_relative: each winter's cos-latitude weighted 20S-20N ocean mean is
    removed before averaging (the idea behind CPC's RONI, without its variance
    rescaling). This takes out most of the warming trend and the tropics-wide
    warmth, and leaves the pattern.
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
from scipy.io import netcdf_file

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402

ERDDAP = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"
DS = "nceiErsstv5_LonPM180"
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
UA = {"User-Agent": "FoodShield-AI data refresh (github.com/feddevreeken-ctrl/foodshield-ai)"}
# CPC answers browser user agents more reliably (same choice as refresh_enso_indices).
CPC_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
CACHE = Path(os.environ.get("FOODSHIELD_CACHE") or "/tmp/foodshield-sst-cache")

# ERSST centres run -88..88 by 2; 84 is the last row inside the Web Mercator
# extent the page paints (Leaflet clips at 85.05).
LAT0, LAT1, LON0, LON1, STEP = -84.0, 84.0, -180.0, 178.0, 2.0
CLIM = (1991, 2020)
FIRST_WINTER = 1950  # first DJF in CPC's ONI table
SEASONS = "DJF JFM FMA MAM AMJ MJJ JJA JAS ASO SON OND NDJ".split()
# key, label, CPC range text, lower bound (inclusive), upper bound (exclusive)
CLASSES = (("weak", "Weak El Niño", "+0.5 to +0.9", 0.5, 1.0),
           ("moderate", "Moderate El Niño", "+1.0 to +1.4", 1.0, 1.5),
           ("strong", "Strong El Niño", "+1.5 to +1.9", 1.5, 2.0),
           ("very_strong", "Very strong El Niño", "+2.0 and above", 2.0, 99.0))
# (lat_s, lat_n, lon_w, lon_e), inclusive on cell centres
BOXES = {"nino34": (-5, 5, -170, -120), "nino12": (-10, 0, -90, -80)}
TROPICS = (-20, 20)


def _r1(text: str) -> float:
    return float(Decimal(text).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def load_oni() -> list[tuple[int, int, float]]:
    """Chronological (year, centre month, ONI to one decimal). DJF 1950 -> (1950, 1)."""
    text = http_get(ONI_URL, timeout=60, headers=CPC_UA, retries=3).text
    rows = []
    for line in text.splitlines():
        f = line.split()
        if len(f) == 4 and f[0] in SEASONS:
            try:
                rows.append((int(f[1]), SEASONS.index(f[0]) + 1, _r1(f[3])))
            except (ValueError, ArithmeticError):
                continue
    if len(rows) < 800:
        raise RuntimeError(f"ONI parse got {len(rows)} seasons -- feed shape changed")
    for a, b in zip(rows, rows[1:]):
        if (a[0] * 12 + a[1] + 1) != (b[0] * 12 + b[1]):
            raise RuntimeError(f"ONI seasons not consecutive at {a} -> {b}")
    return rows


def el_nino_winters(rows) -> list[dict]:
    runs, cur = [], []
    for r in rows + [(0, 0, -9.0)]:  # sentinel closes the last run
        if r[2] >= 0.5:
            cur.append(r)
            continue
        if len(cur) >= 5:
            runs.append(cur)
        cur = []
    out = []
    for run in runs:
        for y, m, v in run:
            if m != 1 or y < FIRST_WINTER:
                continue
            peak = max(x[2] for x in run if (y - 1, 7) <= (x[0], x[1]) <= (y, 6))
            out.append({"year": y, "label": f"{y - 1}-{y % 100:02d}", "peak_oni": peak, "djf_oni": v})
    return out


def _chunk(t0: str, t1: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"ersstv5_{t0}_{t1}.nc"
    if p.exists() and p.stat().st_size > 100_000:
        return p.read_bytes()
    q = (f"sst%5B({t0}T00:00:00Z):1:({t1}T00:00:00Z)%5D%5B(0.0)%5D"
         f"%5B({LAT0}):1:({LAT1})%5D%5B({LON0}):1:({LON1})%5D")
    b = http_get(f"{ERDDAP}/{DS}.nc?{q}", timeout=300, headers=UA, retries=3).content
    tmp = p.with_suffix(".part")
    tmp.write_bytes(b)
    tmp.replace(p)
    return b


def load_djf_months(last_year: int):
    """{(year, month): 2-D SST array} for Dec, Jan, Feb from Dec 1949 to Feb last_year."""
    spans = [("1949-12-01", "1959-12-31")]
    spans += [(f"{y}-01-01", f"{y + 9}-12-31") for y in range(1960, last_year - 9, 10)]
    start = int(spans[-1][1][:4]) + 1
    spans.append((f"{start}-01-01", f"{last_year}-02-20"))
    fields, lats, lons = {}, None, None
    for t0, t1 in spans:
        nc = netcdf_file(io.BytesIO(_chunk(t0, t1)), mmap=False)
        lats = np.array(nc.variables["latitude"][:], dtype=float)
        lons = np.array(nc.variables["longitude"][:], dtype=float)
        sst = np.array(nc.variables["sst"][:], dtype=float)[:, 0]
        for k, t in enumerate(nc.variables["time"][:]):
            d = datetime.fromtimestamp(float(t), timezone.utc)
            if d.month in (12, 1, 2):
                fields[(d.year, d.month)] = sst[k]
        nc.close()
    if lats[0] > lats[-1] or len(lats) != 85 or len(lons) != 180:
        raise RuntimeError(f"unexpected ERSST grid {lats[0]}..{lats[-1]} x {len(lons)}")
    return fields, lats, lons


def _wmean(field, lats, lons, lat_s, lat_n, lon_w=-180, lon_e=180) -> float:
    rows = (lats >= lat_s) & (lats <= lat_n)
    cols = (lons >= lon_w) & (lons <= lon_e)
    sub = field[np.ix_(rows, cols)]
    w = np.broadcast_to(np.cos(np.radians(lats[rows]))[:, None], sub.shape)
    ok = np.isfinite(sub)
    return float((sub[ok] * w[ok]).sum() / w[ok].sum())


def _encode(field) -> list:
    return [None if not np.isfinite(v) else int(round(10 * v)) for v in field.ravel()]


def build() -> dict:
    oni = load_oni()
    j = http_get(f"{ERDDAP}/{DS}.json?time%5B(last)%5D", timeout=90, headers=UA, retries=2).json()
    last = datetime.fromisoformat(j["table"]["rows"][0][0].replace("Z", "+00:00"))
    last_year = last.year if last.month >= 2 else last.year - 1
    djf_oni = {y: v for y, m, v in oni if m == 1}
    last_year = min(last_year, max(djf_oni))
    fields, lats, lons = load_djf_months(last_year)

    clim = {}
    for m in (12, 1, 2):
        yrs = [(y, m) for y in range(CLIM[0], CLIM[1] + 1)]
        missing = [k for k in yrs if k not in fields]
        if missing:
            raise RuntimeError(f"climatology months missing: {missing[:5]}")
        clim[m] = np.mean([fields[k] for k in yrs], axis=0)

    winters = [w for w in el_nino_winters(oni) if w["year"] <= last_year]
    maps, maps_rel = {}, {}
    for w in winters:
        y = w["year"]
        need = [(y - 1, 12), (y, 1), (y, 2)]
        if any(k not in fields for k in need):
            raise RuntimeError(f"ERSST months missing for winter {w['label']}")
        a = np.mean([fields[k] - clim[k[1]] for k in need], axis=0)
        maps[y] = a
        maps_rel[y] = a - _wmean(a, lats, lons, *TROPICS)
        w["nino34_c"] = round(_wmean(a, lats, lons, *BOXES["nino34"]), 2)

    classes, box_means = [], {}
    for key, label, rng, lo, hi in CLASSES:
        ev = [w for w in winters if lo <= w["peak_oni"] < hi]
        if not ev:
            raise RuntimeError(f"no winters in class {key}")
        comp = np.mean([maps[w["year"]] for w in ev], axis=0)
        comp_rel = np.mean([maps_rel[w["year"]] for w in ev], axis=0)
        ok = comp[np.isfinite(comp)]
        ok_rel = comp_rel[np.isfinite(comp_rel)]
        classes.append({
            "key": key, "label": label, "oni_range": rng, "n": len(ev),
            "events": [{"label": w["label"], "peak_oni": w["peak_oni"], "djf_oni": w["djf_oni"]} for w in ev],
            "mean_djf_oni": round(sum(w["djf_oni"] for w in ev) / len(ev), 2),
            "anom": _encode(comp), "anom_relative": _encode(comp_rel),
            "range_c": [round(float(ok.min()), 1), round(float(ok.max()), 1)],
            "range_relative_c": [round(float(ok_rel.min()), 1), round(float(ok_rel.max()), 1)],
        })
        box_means[key] = {}
        for b, box in BOXES.items():
            box_means[key][b] = round(_wmean(comp, lats, lons, *box), 2)
            box_means[key][b + "_relative"] = round(_wmean(comp_rel, lats, lons, *box), 2)

    n = {c["key"]: c["n"] for c in classes}
    return {
        "source_url": f"{ERDDAP}/{DS}.html", "oni_url": ONI_URL,
        "grid": {"lat0": float(lats[0]), "lon0": float(lons[0]), "step_deg": STEP,
                 "nlat": len(lats), "nlon": len(lons),
                 "encoding": "row-major from the southern edge, tenths of a degree C, null over land"},
        "base": "ERSST v5, 1991–2020 monthly climatology", "season": "DJF",
        "classes": classes,
        "box_means_c": box_means,
        "notes": [
            "A composite of past winters, not a model run or a forecast. Each map is the average "
            "observed December to February sea-surface temperature anomaly of the past El Niño "
            "winters in that strength class.",
            "Anomalies are against a 1991-2020 monthly climatology computed from the same ERSST v5 "
            "record. Cells are ERSST's native 2-degree grid, centres from 84S to 84N; ice-covered "
            "sea keeps a value near zero.",
            "A winter counts when its DJF ONI is +0.5 or more inside an episode of at least five "
            "overlapping seasons at or above +0.5, with ONI rounded to one decimal as in CPC's "
            "table. The class is the episode's peak ONI in the July to June year around that winter.",
            "Every winter is measured against 1991-2020, so older winters sit on a cooler baseline "
            "and read cooler than they were against the climate of their day. The relative version "
            "removes each winter's 20S-20N ocean mean before averaging, the idea behind CPC's RONI "
            "without its variance rescaling.",
            "ERSST is a statistical reconstruction. Before about 1980 the Southern Ocean and parts "
            "of the tropical Pacific rest on few ship observations, so older winters are smoother "
            "than recent ones.",
            "CPC's ONI now comes from ERSST v6 on centred 30-year base periods. These maps use "
            "ERSST v5 on a fixed 1991-2020 base, so a box mean here will not equal the ONI listed "
            "for the same winters.",
            f"Each class averages a handful of winters ({n['weak']} weak, {n['moderate']} moderate, "
            f"{n['strong']} strong, {n['very_strong']} very strong), so a single unusual winter can "
            "shape a class map.",
        ],
        "_check": {w["label"]: {"djf_oni": w["djf_oni"], "nino34_c": w["nino34_c"]} for w in winters},
    }


def main() -> int:
    payload = build()
    check = payload.pop("_check")
    path = write_json("sst_composites.json", payload,
                      source="NOAA NCEI ERSST v5 via CoastWatch ERDDAP; NOAA CPC ONI",
                      notes=("Average observed DJF sea-surface temperature anomaly of past El Niño "
                             "winters by CPC strength class, on ERSST's 2-degree grid, against "
                             "1991-2020. Composites of past winters, not a model or a forecast."),
                      status="ok")
    # Eight 15,300-cell maps are ~1.8 MB at write_json's indent=2. Same envelope,
    # no whitespace (as build_trade_matrix does), keeps it near 0.5 MB.
    path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=False, separators=(",", ":")))
    for c in payload["classes"]:
        bm = payload["box_means_c"][c["key"]]
        print(f"[OK] {c['key']:<12} n={c['n']:<2} mean DJF ONI {c['mean_djf_oni']:+.2f} | "
              f"Nino3.4 {bm['nino34']:+.2f} (rel {bm['nino34_relative']:+.2f}) | "
              f"Nino1+2 {bm['nino12']:+.2f} | range {c['range_c']} | "
              + ", ".join(f"{e['label']}({e['peak_oni']})" for e in c["events"]))
    print("[check] winter: DJF ONI vs ERSST v5 Nino3.4 (1991-2020 base) -- "
          + ", ".join(f"{k} {v['djf_oni']:+.1f}/{v['nino34_c']:+.2f}" for k, v in check.items()))
    print(f"[OK] {path} {path.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
