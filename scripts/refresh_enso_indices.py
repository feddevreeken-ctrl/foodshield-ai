#!/usr/bin/env python3
"""Five ENSO indices, each from a machine-readable source.

Why this file exists
--------------------
The panel's limits have always said "the same ocean reads differently on each"
index while showing exactly one number. This carries the others so the claim is
shown rather than asserted.

The rule this collector enforces
--------------------------------
Never compare across averaging windows. A three-month seasonal mean set against
a weekly value is not two agencies disagreeing -- it is one number that has been
averaged down and one that has not. So this does NOT emit a global min/max
spread. It emits explicit PAIRS, each holding everything constant but one
variable, and states which variable is free. A pair with more than one free
variable is emitted as `invalid` with the reason, because "these two cannot be
compared" is itself the finding.

A source that fails is OMITTED and recorded in `unavailable`. It is never
back-filled from a previous run -- a stale number that looks live is the exact
failure mode this project has already been bitten by twice (wksst8110.for,
rel_wksst9120.txt: both HTTP 200, both frozen).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import http_get, write_json  # noqa: E402
from refresh_enso import parse_weekly  # noqa: E402  (reuses its frozen-feed guard)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
RONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt"
WEEKLY_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"
BOM_RNINO_URL = "https://www.bom.gov.au/clim_data/IDCK000072/rnino_3.4.txt"
BOM_SOI_URL = "https://www.bom.gov.au/clim_data/IDCKGSM000/soi.txt"

MAX_AGE_DAYS = 45


def _fetch(url: str) -> str:
    return http_get(url, timeout=60, headers=UA, retries=3).text


def parse_seasonal(text: str, anom_col: int) -> tuple[str, int, float]:
    """Last (SEAS, YR, ANOM) row. anom_col is the 0-indexed field of the anomaly."""
    rows = []
    for line in text.splitlines():
        f = line.split()
        if len(f) <= anom_col or not re.fullmatch(r"[A-Z]{3}", f[0]):
            continue
        try:
            rows.append((f[0], int(f[1]), float(f[anom_col])))
        except ValueError:
            continue
    if len(rows) < 100:
        raise RuntimeError(f"seasonal parse got {len(rows)} rows -- feed shape changed")
    return rows[-1]


def parse_bom_weekly(text: str) -> tuple[str, str, float]:
    """BoM 'YYYYMMDD,YYYYMMDD,value' -- returns (start, end, value)."""
    rows = []
    for line in text.splitlines():
        f = [p.strip() for p in line.split(",")]
        if len(f) != 3 or not re.fullmatch(r"\d{8}", f[0]):
            continue
        try:
            rows.append((f[0], f[1], float(f[2])))
        except ValueError:
            continue
    if len(rows) < 50:
        raise RuntimeError(f"BoM parse got {len(rows)} rows -- feed shape changed")
    newest = max(rows, key=lambda r: r[1])
    age = (datetime.now(timezone.utc).date()
           - datetime.strptime(newest[1], "%Y%m%d").date()).days
    if age > MAX_AGE_DAYS:
        raise RuntimeError(f"BoM newest row ends {newest[1]}, {age} days old -- frozen feed")
    return newest


def _d(ymd: str) -> str:
    return datetime.strptime(ymd, "%Y%m%d").strftime("%-d %b %Y")


def main() -> int:
    indices: list[dict] = []
    unavailable: list[dict] = []

    def attempt(key, fn):
        try:
            indices.append(fn())
        except Exception as e:  # noqa: BLE001 -- omit, never back-fill
            unavailable.append({"key": key, "reason": f"{type(e).__name__}: {e}"})

    def oni():
        seas, yr, v = parse_seasonal(_fetch(ONI_URL), 3)
        return {
            "key": "oni", "label": "ONI", "agency": "NOAA CPC", "value": v, "unit": "°C",
            "window": f"{seas} {yr}, 3-month mean", "window_kind": "seasonal",
            "region": "Niño 3.4 (170°W–120°W)", "baseline": "absolute, fixed 1991–2020",
            "threshold": 0.5,
            "note": "The index this page headlines. Absolute anomaly against a fixed base.",
            "url": ONI_URL,
        }

    def roni():
        seas, yr, v = parse_seasonal(_fetch(RONI_URL), 2)
        return {
            "key": "roni", "label": "RONI", "agency": "NOAA CPC", "value": v, "unit": "°C",
            "window": f"{seas} {yr}, 3-month mean", "window_kind": "seasonal",
            "region": "Niño 3.4", "baseline": "relative to the tropical mean",
            "threshold": 0.5,
            "note": "Subtracts the tropical-mean warming trend, so it reads lower than ONI "
                    "as the tropics warm.",
            "url": RONI_URL,
        }

    def weekly():
        rows = parse_weekly(_fetch(WEEKLY_URL))
        newest = max(rows, key=lambda r: datetime.strptime(r["date"], "%d%b%Y"))
        d = datetime.strptime(newest["date"], "%d%b%Y")
        return {
            "key": "wk34", "label": "Weekly Niño 3.4", "agency": "NOAA CPC",
            "value": newest["nino34_anom"], "unit": "°C",
            "window": f"week ending {d.strftime('%-d %b %Y')}", "window_kind": "weekly",
            "region": "Niño 3.4", "baseline": "absolute, fixed 1991–2020", "threshold": None,
            "note": "A single week, not a season. Runs hotter than the seasonal mean "
                    "because it has not been averaged down.",
            "url": WEEKLY_URL,
        }

    def bom_rel():
        start, end, v = parse_bom_weekly(_fetch(BOM_RNINO_URL))
        return {
            "key": "bom_rel", "label": "Relative Niño 3.4", "agency": "BoM Australia",
            "value": v, "unit": "°C",
            "window": f"week {_d(start)} – {_d(end)}", "window_kind": "weekly",
            "region": "Niño 3.4", "baseline": "relative to the tropical mean",
            "threshold": 0.8,
            "note": "BoM's operational ocean index since Sept 2025, and it uses a higher "
                    "threshold (+0.8) than CPC (+0.5) -- the same water clears a different bar.",
            "url": BOM_RNINO_URL,
        }

    def soi():
        start, end, v = parse_bom_weekly(_fetch(BOM_SOI_URL))
        return {
            "key": "soi", "label": "Troup SOI", "agency": "BoM Australia",
            "value": v, "unit": "index",
            "window": f"30 days to {_d(end)}", "window_kind": "atmospheric",
            "region": "Tahiti–Darwin pressure", "baseline": "n/a", "threshold": -7,
            "note": "The ATMOSPHERE, not the ocean. Negative is El Niño-like. Shows the "
                    "ocean signal is coupled rather than SST-only.",
            "url": BOM_SOI_URL,
        }

    for k, fn in (("oni", oni), ("roni", roni), ("wk34", weekly),
                  ("bom_rel", bom_rel), ("soi", soi)):
        attempt(k, fn)

    by = {i["key"]: i for i in indices}

    def pair(a, b, holds, differs, reading):
        if a not in by or b not in by:
            return None
        return {
            "a": a, "b": b, "holds_constant": holds, "free_variable": differs,
            "delta": round(abs(by[a]["value"] - by[b]["value"]), 2),
            "reading": reading,
        }

    comparisons = [c for c in (
        pair("oni", "roni",
             ["agency (CPC)", "region (Niño 3.4)", "window (same 3-month season)"],
             "baseline: absolute vs relative to the tropical mean",
             "A like-for-like comparison. The whole gap is the choice of baseline -- "
             "removing the tropical-mean warming trend, nothing else."),
        pair("oni", "wk34",
             ["agency (CPC)", "region (Niño 3.4)", "baseline (absolute 1991–2020)"],
             "averaging window: 3-month season vs a single week",
             "NOT a disagreement. The gap is arithmetic: one number has been averaged "
             "over three months and the other has not."),
    ) if c]

    invalid = []
    if "bom_rel" in by and "wk34" in by:
        invalid.append({
            "a": "wk34", "b": "bom_rel",
            "why": "Three variables are free at once -- different agency, different "
                   "baseline (absolute vs relative) and a different week. The two numbers "
                   "being close is a coincidence of this particular week, not agreement.",
        })

    payload = {
        "indices": indices,
        "comparisons": comparisons,
        "invalid_comparisons": invalid,
        "unavailable": unavailable,
        "stale_after_days": MAX_AGE_DAYS,
    }
    write_json(
        "enso_indices.json", payload,
        source="NOAA CPC (ONI, RONI, weekly Niño 3.4); BoM Australia (relative Niño 3.4, Troup SOI)",
        notes="Five indices, each parsed from a machine-readable feed. NO global spread is "
              "published: the indices differ in region, baseline AND averaging window, so a "
              "min/max across all of them would be the exact error the panel warns against. "
              "Only pairs with a single free variable are comparable, and those are listed "
              "explicitly. A source that fails is omitted, never back-filled.",
        status="ok" if not unavailable else "partial",
    )
    print(f"enso_indices: {len(indices)} indices, {len(comparisons)} valid pairs, "
          f"{len(unavailable)} unavailable")
    for i in indices:
        print(f"  {i['label']:22s} {i['value']:+6.2f}  {i['window']}")
    for u in unavailable:
        print(f"  MISSING {u['key']}: {u['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
