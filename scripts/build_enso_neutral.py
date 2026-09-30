#!/usr/bin/env python3
"""
build_enso_neutral.py: ENSO-neutral yield baseline per fitted country x crop pair.

WHY
---
build_enso_outlook.py used to apply the fitted El Nino yield change to USDA's
2026/27 production estimate. USDA's estimate may already allow for the event, so
that can count the same shock twice. This builder gives the outlook a baseline
that has no El Nino term in it: the model's own trend yield.

HOW TREND IS ESTIMATED (same detrending as build_enso_model.py)
----------------------------------------------------------------
The model's anomaly is log(yield) minus a centred 9-year moving average of
log(yield) (MA_WINDOW 9, at least 5 points). That moving average IS the model's
trend. Here:
  1. Take the model's moving average at its last FULL window (last observed year
     minus 4). Edge windows are one-sided and lag a rising series, so they are
     not used.
  2. Take the slope of that moving-average series over its last 15 full-window
     points (OLS of log trend on year).
  3. trend_log(h) = MA(last full year) + slope x (h - last full year).
  Sensitivity ("alt"): the model's edge window (mean of the last 5 log yields,
  centred two years back) with the same slope. Both are stored.
The ENSO term is excluded: neutral yield = exp(trend_log), no exp(b x ONI).

BASIS
-----
FAOSTAT QCL yield and area are not on USDA's basis (rice: paddy vs milled; USDA
area can differ from FAOSTAT's). For pairs whose USDA marketing-year to harvest
year mapping is checked (PSD_HARVEST_OFFSET in build_enso_outlook.py) this file
stores ka = median(USDA area / FAOSTAT
area) and kp = median(USDA production / FAOSTAT production) over the last 5
overlapping harvests, plus USDA's own area by harvest year. The outlook then
expresses neutral tonnes on USDA's basis. Unmapped pairs stay on FAOSTAT's basis
and are never compared with USDA.

HAND-RUN: needs FAOSTAT QCL (34 MB) and the USDA PSD bulk zips, cached under
$FOODSHIELD_CACHE (default ~/.cache/foodshield). Output is committed; the cron
does not run it (CI has no FAOSTAT bulk). Re-run after build_enso_model.py.
  python3 scripts/build_enso_neutral.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import statistics
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import stamp_inputs, write_json  # noqa: E402
import build_enso_model as bm  # noqa: E402
from build_enso_outlook import PSD_HARVEST_OFFSET  # noqa: E402
from refresh_usda_psd import COMMODITY_TO_KEY, FAS_TO_ISO3, NAME_TO_ISO3, URLS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("FOODSHIELD_CACHE") or Path.home() / ".cache" / "foodshield")
SLOPE_POINTS = 15
OVERLAP = 5
PSD_ZIPS = {"grains_pulses": "psd_grains_pulses.zip", "oilseeds": "psd_oilseeds.zip"}
SRC = {
    "faostat": "https://bulks-faostat.fao.org/production/Production_Crops_Livestock_E_All_Data_(Normalized).zip",
    "usda": "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip and psd_oilseeds_csv.zip",
}


def psd_series(pairs: set) -> dict:
    """(iso, crop) -> marketing year -> {prod_kt, area_kha, yield}. Only the wanted pairs."""
    out: dict = {}
    want = {c for _, c in pairs}
    for key, url in URLS:
        path = CACHE / PSD_ZIPS[key]
        if not path.exists() or path.stat().st_size < 10000:
            import urllib.request
            req = urllib.request.Request(url, headers=bm.HEADERS)
            path.write_bytes(urllib.request.urlopen(req, timeout=300).read())
        zf = zipfile.ZipFile(path)
        with zf.open(zf.namelist()[0]) as fh:
            for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace")):
                crop = COMMODITY_TO_KEY.get((row.get("Commodity_Code") or "").strip())
                if crop not in want:
                    continue
                iso = (FAS_TO_ISO3.get((row.get("Country_Code") or "").strip())
                       or NAME_TO_ISO3.get((row.get("Country_Name") or "").strip()))   # Zimbabwe is FAS "RH", not "ZI"
                if (iso, crop) not in pairs:
                    continue
                field = {"Production": "prod_kt", "Area Harvested": "area_kha", "Yield": "yield"}.get(
                    (row.get("Attribute_Description") or "").strip())
                if not field:
                    continue
                try:
                    my, v = int(row["Market_Year"]), float(row["Value"])
                except (ValueError, TypeError, KeyError):
                    continue
                out.setdefault((iso, crop), {}).setdefault(my, {})[field] = v
    return out


def trend(years: list, vals: list) -> dict | None:
    """The model's own centred 9-year MA of log yield, extended to later years."""
    logy = np.log(np.array(vals, float))
    half = bm.MA_WINDOW // 2
    ma_y, ma_v = [], []
    for i in range(len(logy)):
        if i - half < 0 or i + half + 1 > len(logy):
            continue                      # full windows only
        ma_y.append(years[i]); ma_v.append(float(logy[i - half:i + half + 1].mean()))
    if len(ma_y) < SLOPE_POINTS:
        return None
    sy, sv = np.array(ma_y[-SLOPE_POINTS:], float), np.array(ma_v[-SLOPE_POINTS:])
    slope = float(np.polyfit(sy, sv, 1)[0])
    edge5 = float(logy[-5:].mean())       # the model's edge window at the last year
    return {"anchor_year": ma_y[-1], "anchor_log": ma_v[-1], "slope_log_per_yr": slope,
            "alt_anchor_year": years[-1] - 2, "alt_anchor_log": edge5, "last_year": years[-1]}


def ratio(pairs_ab: list):
    r = [a / b for a, b in pairs_ab if b and a]
    return round(statistics.median(r), 4) if r else None


def main() -> int:
    model = json.loads((ROOT / "data" / "enso_model.json").read_text())["data"]
    panel = bm.load_faostat()
    want = {(iso, crop) for iso, cs in model.items() for crop, c in cs.items()
            if isinstance(c, dict) and (c.get("signal") or c.get("nino_signal"))}
    psd = psd_series({p for p in want if p in PSD_HARVEST_OFFSET})
    out = {}
    for iso, crop in sorted(want):
        s = (panel.get(iso) or {}).get(crop) or {}
        years = sorted(y for y in s if s[y].get("yield"))
        if len(years) < bm.MIN_YEARS:
            continue
        t = trend(years, [s[y]["yield"] for y in years])
        if not t:
            continue
        areas = [y for y in years if s[y].get("area")]
        row = dict(t, fao_yield_last=s[years[-1]]["yield"], fao_area_year=areas[-1], fao_area_ha=s[areas[-1]]["area"],
                   fao_yield_kg_ha_unit="kg/ha", usda_mapped=False)
        off = PSD_HARVEST_OFFSET.get((iso, crop))
        if off is not None and (iso, crop) in psd:
            ps = psd[(iso, crop)]
            ov = [h for h in sorted(years, reverse=True) if (h - off) in ps and ps[h - off].get("area_kha")
                  and s[h].get("area") and s[h].get("prod")][:OVERLAP]
            row.update(
                usda_mapped=True, harvest_offset=off, overlap_harvests=sorted(ov),
                ka=ratio([(ps[h - off]["area_kha"] * 1000, s[h]["area"]) for h in ov]),
                kp=ratio([(ps[h - off]["prod_kt"] * 1000, s[h]["prod"]) for h in ov]),
                usda_area_kha_by_harvest={str(my + off): ps[my]["area_kha"] for my in sorted(ps) if ps[my].get("area_kha") and my + off >= years[-1] - 1})
        out[f"{iso}/{crop}"] = row
    n_map = sum(1 for r in out.values() if r["usda_mapped"])
    write_json("enso_neutral.json", {
        "method": ("Neutral yield = the model's own trend: centred 9-year moving average of log yield (build_enso_model.detrend) "
                   f"at its last full window, extended by the OLS slope of that average over its last {SLOPE_POINTS} full-window points. "
                   "No El Niño term. Sensitivity: the model's edge window (mean of the last 5 log yields) with the same slope."),
        "basis": ("FAOSTAT yield and area; for pairs with a checked USDA marketing-year mapping, ka and kp are the median "
                  f"USDA/FAOSTAT ratios over the last {OVERLAP} overlapping harvests."),
        "pairs": out,
    }, source=f"Derived: FAOSTAT QCL ({SRC['faostat']}); USDA PSD bulk ({SRC['usda']}); fetched {datetime.now(timezone.utc).date()}; FAOSTAT licence CC BY-NC-SA 3.0 IGO, USDA public domain",
       notes="Hand-run builder (numpy, needs the FAOSTAT bulk); output committed, not refreshed by the cron. Re-run after build_enso_model.py.", status="ok")
    stamp_inputs("enso_neutral.json")
    print(f"[OK] enso_neutral: {len(out)} pairs, {n_map} with USDA mapping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
