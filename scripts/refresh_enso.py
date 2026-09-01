#!/usr/bin/env python3
"""
refresh_enso.py — live ENSO state from NOAA CPC.

Feeds the El Nino panel. Three plain-HTTP text sources, no key, no library:

  ONI            cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
  weekly Nino3.4 cpc.ncep.noaa.gov/data/indices/wksst9120.for
  SOI            cpc.ncep.noaa.gov/data/indices/soi

TRAPS CONFIRMED BY FETCH -- every one of these was hit while building this:

* wksst8110.for is FROZEN. It returns HTTP 200 and ~102 KB of healthy-looking
  data whose last row is 27JAN2021. The live file is wksst9120.for. A collector
  pointed at the old name looks perfectly well and silently serves 5-year-old
  SSTs.
* origin.cpc.ncep.noaa.gov is unreachable from here; www.cpc.* serves the same
  paths.
* The weekly file fuses a negative anomaly onto the SST that precedes it:
  ' 02SEP1981     20.6-0.1     24.8-0.1 ...'. split() therefore returns 5 fields
  for that row and 9 for ' 26AUG2026     25.0 4.2 ...'. MEASURED on the live
  file: 1,729 of 2,352 rows are fused. Today's anomalies happen to be positive,
  so a split()-based parser looks perfectly correct right now and would start
  lying the moment ENSO flips sign. Parsed here with a regex whose separator is
  `\s*` before an optional minus, which reads both forms.
* The SOI file has TWO stacked blocks (ANOMALY, then STANDARDIZED) with
  identical headers, and is pre-padded to 2030 with -999.9. Reading "the last
  row" returns 2030 and all-missing.

INDEX VERSIONING MATTERS AND IS RECORDED. CPC's official index is now RONI;
BoM uses a relative Nino3.4 with a higher threshold. The same July 2026 ocean
reads +1.4 (CPC relative), +2.03 (IRI traditional) and +2.20 (BoM relative).
Mixing them produces a wrong number, so `index` and `index_note` travel with
the data and the UI must not compare a value here against another agency's.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import http_get, write_json  # noqa: E402

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
WEEKLY_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"
SOI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/soi"

UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}

# CPC's conventional strength bands on the ONI.
def band(v: float) -> str:
    a = abs(v)
    if a < 0.5:
        return "Neutral"
    tier = ("Weak" if a < 1.0 else "Moderate" if a < 1.5
            else "Strong" if a < 2.0 else "Very strong")
    return f"{tier} {'El Nino' if v > 0 else 'La Nina'}"


def parse_oni(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        f = line.split()
        if len(f) != 4 or f[0] == "SEAS":
            continue
        try:
            out.append({"season": f[0], "year": int(f[1]),
                        "sst": float(f[2]), "anom": float(f[3])})
        except ValueError:
            continue
    if len(out) < 500:
        raise RuntimeError(f"ONI parse got {len(out)} rows -- feed shape changed")
    return out


_WEEKLY_RE = re.compile(r"^\s*(\d{2}[A-Z]{3}\d{4})" + r"\s+(-?\d+\.\d)\s*(-?\d+\.\d)" * 4 + r"\s*$")


def parse_weekly(text: str) -> list[dict]:
    """Columns are Nino1+2, Nino3, Nino3.4, Nino4 -- each an SST then an anomaly."""
    rows = []
    for line in text.splitlines():
        m = _WEEKLY_RE.match(line)
        if not m:
            continue
        g = m.groups()
        try:
            datetime.strptime(g[0], "%d%b%Y")
        except ValueError:
            continue
        rows.append({
            "date": g[0],
            "nino12": float(g[1]), "nino12_anom": float(g[2]),
            "nino3": float(g[3]), "nino3_anom": float(g[4]),
            "nino34": float(g[5]), "nino34_anom": float(g[6]),
            "nino4": float(g[7]), "nino4_anom": float(g[8]),
        })
    if len(rows) < 100:
        raise RuntimeError(f"weekly SST parse got {len(rows)} rows -- feed shape changed")
    newest = max(rows, key=lambda r: datetime.strptime(r["date"], "%d%b%Y"))
    age = (datetime.now(timezone.utc).date() - datetime.strptime(newest["date"], "%d%b%Y").date()).days
    if age > 45:
        raise RuntimeError(
            f"weekly SST newest row is {newest['date']}, {age} days old -- "
            "this is how wksst8110.for fails (HTTP 200, frozen content)")
    return rows


def main() -> int:
    oni = parse_oni(http_get(ONI_URL, timeout=60, headers=UA, retries=3).text)
    weekly = parse_weekly(http_get(WEEKLY_URL, timeout=60, headers=UA, retries=3).text)

    latest = oni[-1]
    recent = oni[-14:]
    w = weekly[-1]

    # Rank the latest value against the SAME overlapping season in every prior
    # year -- MJJ against MJJ, not against DJF. ENSO has a strong annual cycle
    # and peaks around DJF, so ranking a mid-year value against DJF seasons
    # understates it and is not a like-for-like comparison.
    same = sorted(r["anom"] for r in oni if r["season"] == latest["season"])
    rank = sum(1 for v in same if v >= latest["anom"])

    payload = {
        "index": "ONI (CPC oni.ascii.txt)",
        "index_note": (
            "ONI as published by CPC. CPC's OFFICIAL headline index is now RONI, and BoM "
            "uses a relative Nino3.4 with a higher threshold -- the same ocean reads "
            "differently on each. Do not compare this value against another agency's."),
        "latest": {
            "season": latest["season"], "year": latest["year"],
            "anom": latest["anom"], "band": band(latest["anom"]),
            "sst_c": latest["sst"],
        },
        "trajectory": [{"season": r["season"], "year": r["year"], "anom": r["anom"]}
                       for r in recent],
        "weekly_nino34": {
            "date": w["date"], "anom": w["nino34_anom"], "sst_c": w["nino34"],
            "nino12_anom": w["nino12_anom"],
            "east_based": bool(w["nino12_anom"] > w["nino34_anom"]),
            "east_based_note": (
                "Nino1+2 running warmer than Nino3.4 indicates an EAST-BASED event, which "
                "matters most for coastal Peru and Ecuador. Note: no published source was "
                "found linking this structure to a distinct global crop-teleconnection "
                "footprint, so the dashboard does not model one."),
        },
        "historical_rank": {
            "rank": rank, "of": len(same), "season": latest["season"],
            "note": (f"{latest['season']} {latest['year']} ranks {rank} of {len(same)} "
                     f"among all {latest['season']} seasons since 1950. Compared "
                     f"like-for-like: ENSO has a strong annual cycle and peaks near DJF, "
                     f"so ranking a mid-year season against DJF would understate it."),
        },
    }
    payload["source_url"] = ONI_URL
    write_json("enso.json", payload, source="NOAA CPC",
               notes=("Live ENSO state: ONI series, latest weekly Nino3.4, and the "
                      "strength band. Weekly file is wksst9120.for -- wksst8110.for is "
                      "frozen at Jan 2021 and still returns HTTP 200."))
    print(f"[OK] ONI {latest['season']} {latest['year']} = {latest['anom']:+.2f} ({band(latest['anom'])})")
    print(f"[OK] weekly Nino3.4 {w['date']} = {w['nino34_anom']:+.2f} | Nino1+2 {w['nino12_anom']:+.2f}")
    print(f"[OK] rank {rank}/{len(same)} among all {latest['season']} seasons since 1950")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
