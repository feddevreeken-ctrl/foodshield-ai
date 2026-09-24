#!/usr/bin/env python3
"""
refresh_enso_freight.py — what the El Nino shipping lanes cost and carry, for grain.

The Shipping lens shows the water (enso_gauges.json) and the chokepoint
transits (portwatch). This adds the grain-specific money and volume, all from
USDA AMS AgTransport (Socrata, no key), the data behind the weekly Grain
Transportation Report (GTR):

  ehs5-yac3  Vessel Rates: monthly ocean freight, US Gulf -> Japan and
             PNW -> Japan, $/metric ton, and the Gulf-PNW spread (GTR Fig. 20).
             A Panama restriction lands on the Gulf leg, so the spread is its
             cost signal.
  sruw-w49i  FGIS grain inspections for export, weekly, by port region
             (GTR Table 18). Mississippi Gulf = port "MISSISSIPPI R.",
             Texas Gulf = "N. TEXAS" + "S. TEXAS", PNW = "COLUMBIA R." + "PUGET SOUND".
  n4pw-9ygw  Downbound grain barge tonnage by lock, weekly (USACE via GTR
             Table 10). The GTR weekly total is Mississippi L27 + Ohio Olmsted
             + Arkansas L1; Illinois La Grange is shown on its own.

Baselines: monthly rates against the 2019-2025 same-month MEAN; weekly volumes
against the 2021-2025 same-week MEDIAN (the dated point nearest the same
calendar day in each year, within 3 days). Each series is independent: one
that fails is listed under `unavailable` and nothing is back-filled or guessed.
"""
from __future__ import annotations

import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402

API = "https://agtransport.usda.gov/resource/{}.json"
PAGE = "https://agtransport.usda.gov/d/{}"
GTR = "https://www.ams.usda.gov/services/transportation-analysis/gtr"

RATES_ID, INSP_ID, BARGE_ID = "ehs5-yac3", "sruw-w49i", "n4pw-9ygw"
PORTS = {"MS_GULF": ("MISSISSIPPI R.",), "TX_GULF": ("N. TEXAS", "S. TEXAS"),
         "PNW": ("COLUMBIA R.", "PUGET SOUND")}
BARGE_TOTAL_LOCKS = ("MS Locks 27", "OH Olmsted", "AK Lock 1")
MONTHLY_KEEP, WEEKLY_KEEP = 36, 156
RATE_BASE_YEARS = range(2019, 2026)
VOL_BASE_YEARS = range(2021, 2026)


def get(ds: str, **params):
    return http_get(API.format(ds), params=params, timeout=90, retries=2).json()


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _same_day(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:
        return d.replace(year=year, day=28)


def _nearest(pts: list[dict], target: date, tol: int = 3) -> dict | None:
    best = min(pts, key=lambda p: abs((_d(p["date"]) - target).days), default=None)
    return best if best and abs((_d(best["date"]) - target).days) <= tol else None


def _yoy(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b not in (None, 0) else None


def _series(label, unit, ds, freq, pts, latest, year_ago, base_vals, stat, window, notes=None):
    base = None
    if base_vals:
        v = statistics.mean(base_vals) if stat == "mean" else statistics.median(base_vals)
        v = round(v, 2) if unit.startswith("US$") else round(v)
        base = {"value": v, stat: v, "stat": stat, "window_label": window, "n_years": len(base_vals)}
    out = {
        "label": label, "unit": unit, "source": "USDA AMS AgTransport (Grain Transportation Report data)",
        "source_url": PAGE.format(ds), "report_url": GTR, "dataset_id": ds, "frequency": freq,
        "points": pts, "latest": latest, "year_ago": year_ago, "baseline": base,
        "yoy_pct": _yoy(latest["value"], year_ago["value"] if year_ago else None),
        "vs_baseline_pct": _yoy(latest["value"], base["value"] if base else None),
    }
    if notes:
        out["notes"] = notes
    return out


def _check_fresh(last: date, today: date, max_days: int, what: str):
    if (today - last).days > max_days:
        raise RuntimeError(f"{what} last reported {last}, {(today - last).days} days ago: stale")


def ocean(today: date) -> dict:
    rows = get(RATES_ID, **{"$select": "date,gulf_to_japan,pnw_to_japan,gulf_pnw_spread",
                            "$where": "date >= '2019-01-01'", "$order": "date", "$limit": "400"})
    cols = {"ocean_gulf_japan": ("gulf_to_japan", "Ocean freight, US Gulf to Japan (grain)"),
            "ocean_pnw_japan": ("pnw_to_japan", "Ocean freight, US Pacific Northwest to Japan (grain)"),
            "ocean_gulf_pnw_spread": ("gulf_pnw_spread", "Gulf minus PNW ocean freight to Japan (spread)")}
    out = {}
    for key, (col, label) in cols.items():
        pts = [{"date": r["date"][:10], "value": round(float(r[col]), 2)} for r in rows if r.get(col)]
        if not pts:
            raise RuntimeError(f"{col} empty")
        last = pts[-1]
        _check_fresh(_d(last["date"]), today, 100, col)
        ld = _d(last["date"])
        ya = next((p for p in pts if _d(p["date"]) == ld.replace(year=ld.year - 1)), None)
        base = [p["value"] for p in pts if _d(p["date"]).month == ld.month and _d(p["date"]).year in RATE_BASE_YEARS]
        out[key] = _series(label, "US$ per metric ton", RATES_ID, "monthly", pts[-MONTHLY_KEEP:], last, ya,
                           base, "mean", f"{ld.strftime('%B')} mean, 2019–2025",
                           notes="Monthly average of Maritime Research, Inc. fixtures as reported in the GTR." if key == "ocean_gulf_japan" else None)
    return out


def _weekly(pts_by_date: dict[date, float], today: date, label: str, unit: str, ds: str, notes: str | None = None):
    pts = [{"date": d.isoformat(), "value": round(v)} for d, v in sorted(pts_by_date.items())]
    if not pts:
        raise RuntimeError(f"{label}: no points")
    last = pts[-1]
    ld = _d(last["date"])
    _check_fresh(ld, today, 45, label)
    ya = _nearest(pts, _same_day(ld, ld.year - 1))
    base = [p["value"] for y in VOL_BASE_YEARS if (p := _nearest(pts, _same_day(ld, y)))]
    return _series(label, unit, ds, "weekly", pts[-WEEKLY_KEEP:], last, ya, base, "median",
                   f"same week, 2021–2025 median", notes)


def inspections(today: date) -> dict:
    ports = [p for v in PORTS.values() for p in v]
    rows = get(INSP_ID, **{"$select": "date,port,sum(mt) as mt", "$group": "date,port",
                           "$where": "date >= '2020-11-01' AND port in(" + ",".join(f"'{p}'" for p in ports) + ")",
                           "$order": "date", "$limit": "5000"})
    dates = sorted({_d(r["date"]) for r in rows})
    # A week-ending Thursday is published the following Monday: skip a week that
    # has not closed yet rather than showing a partial total.
    dates = [d for d in dates if (today - d).days >= 3]
    agg = {k: {d: 0.0 for d in dates} for k in PORTS}
    for r in rows:
        d = _d(r["date"])
        for k, names in PORTS.items():
            if r["port"] in names and d in agg[k]:
                agg[k][d] += float(r["mt"])
    gulf = {d: agg["MS_GULF"][d] + agg["TX_GULF"][d] for d in dates}
    note = ("All grains inspected for export (FGIS), week ending Thursday; includes revisions, so recent weeks "
            "can differ slightly from the printed GTR Table 18.")
    return {
        "inspections_us_gulf": _weekly(gulf, today, "Grain inspected for export, US Gulf (Mississippi + Texas)",
                                       "metric tons", INSP_ID, note),
        "inspections_ms_gulf": _weekly(agg["MS_GULF"], today, "Grain inspected for export, Mississippi Gulf",
                                       "metric tons", INSP_ID, note),
        "inspections_pnw": _weekly(agg["PNW"], today, "Grain inspected for export, Pacific Northwest",
                                   "metric tons", INSP_ID, note),
    }


def barges(today: date) -> dict:
    locks = BARGE_TOTAL_LOCKS + ("IL La Grange",)
    rows = get(BARGE_ID, **{"$select": "date,lock,sum(tons) as tons", "$group": "date,lock",
                            "$where": "date >= '2020-11-01' AND lock in(" + ",".join(f"'{l}'" for l in locks) + ")",
                            "$order": "date", "$limit": "5000"})
    by = {}
    for r in rows:
        by.setdefault(r["lock"], {})[_d(r["date"])] = float(r["tons"])
    total_dates = set.intersection(*(set(by.get(l, {})) for l in BARGE_TOTAL_LOCKS))
    total = {d: sum(by[l][d] for l in BARGE_TOTAL_LOCKS) for d in total_dates}
    note = "USACE lock data via the GTR; week ending Saturday; all grains (corn, wheat, soybeans, other)."
    return {
        "barge_grain_total": _weekly(total, today, "Downbound grain barged (Mississippi L27 + Ohio Olmsted + Arkansas L1)",
                                     "short tons", BARGE_ID, note + " Same three locks as the GTR weekly total."),
        "barge_ms_lock27": _weekly(by.get("MS Locks 27", {}), today, "Downbound grain barged, Mississippi River Locks 27",
                                   "short tons", BARGE_ID, note),
        "barge_il_lagrange": _weekly(by.get("IL La Grange", {}), today, "Downbound grain barged, Illinois River La Grange",
                                     "short tons", BARGE_ID, note + " Upstream of L27, so already inside the Mississippi total."),
    }


def main() -> int:
    today = datetime.now(timezone.utc).date()
    series, unavailable = {}, [{
        "key": "inspections_via_panama",
        "reason": ("No official series found. FGIS inspections record port region and destination, not transit "
                   "route; the AgTransport catalog (454 views, checked 2026-09-24) and the GTR of 2026-09-17 "
                   "carry no Panama Canal grain-transit table."),
    }]
    for name, fn in (("ocean", ocean), ("inspections", inspections), ("barge", barges)):
        try:
            got = fn(today)
            series.update(got)
            for k, s in got.items():
                print(f"  ok   {k}: latest {s['latest']} | year-ago {s['year_ago']} | "
                      f"baseline {s['baseline']['value'] if s['baseline'] else None} | yoy {s['yoy_pct']}% "
                      f"[{s['dataset_id']}]")
        except Exception as e:  # noqa: BLE001 -- one dataset down must not blank the others
            unavailable.append({"key": name, "reason": f"{type(e).__name__}: {e}"})
            print(f"  FAIL {name}: {e}")
    if not series:
        raise RuntimeError("no AgTransport series reachable -- keeping the previous file")
    write_json("enso_freight.json", {"series": series, "unavailable": unavailable},
               source="USDA AMS AgTransport (datasets ehs5-yac3, sruw-w49i, n4pw-9ygw); Grain Transportation Report",
               notes=("Grain ocean freight to Japan (Gulf vs PNW), weekly export inspections by port region and weekly "
                      "barged grain at the Mississippi/Ohio/Arkansas/Illinois locks. Monthly baseline = 2019-2025 "
                      "same-month mean; weekly baseline = 2021-2025 same-week median."),
               status="ok" if len(unavailable) == 1 else "partial")
    return 0


if __name__ == "__main__":
    sys.exit(main())
