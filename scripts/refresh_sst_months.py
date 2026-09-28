#!/usr/bin/env python3
"""refresh_sst_months.py -- the observed sea-surface anomaly for each of the last few complete months.

Feeds the El Nino Ocean map's time scrubber, which steps back from this week
through the months before it (owner, 2026-09-28: "the sea temperature on the map
live one should also go back from here a couple of months"). Same product, grid
and base as refresh_sst_anomaly.py (NOAA OISST v2.1 daily anomaly through the
CoastWatch ERDDAP griddap endpoint, one 0.25-degree cell in eight, 1971-2000
base), averaged over every day of the calendar month instead of seven days.

ERDDAP has no monthly OISST product, so each month is the mean of its daily
fields. A complete month does not change once the final (non-preliminary)
dataset covers it, so months are cached in data/sst_months.json and fetched
once; a month first taken from the near-real-time dataset is fetched again when
the final one covers it. Pure Python (requests only), like the weekly collector.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_json  # noqa: E402
import refresh_sst_anomaly as W  # noqa: E402

MONTHS = 6    # owner: "I want the temp to go back 6 months for the slider"
LAST = 30     # the scrubber's 30-day stop pairs a 30-day sea mean with the 30-day rain
OUT = Path(__file__).resolve().parent.parent / "data" / "sst_months.json"


def _month_bounds(y: int, m: int) -> tuple[datetime, datetime]:
    first = datetime(y, m, 1, 12, tzinfo=timezone.utc)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first, datetime(nxt.year, nxt.month, nxt.day, 12, tzinfo=timezone.utc) - timedelta(days=1)


def _mean_field(ds: str, t0: datetime, t1: datetime):
    rows = W._fetch(ds, t0, t1)
    lats = sorted({r["latitude"] for r in rows})
    lons = sorted({r["longitude"] for r in rows})
    li = {v: i for i, v in enumerate(lats)}
    lo = {v: i for i, v in enumerate(lons)}
    acc = [[0.0, 0] for _ in range(len(lats) * len(lons))]
    days = set()
    for r in rows:
        days.add(r["time"])
        v = r.get("anom")
        if v is not None:
            k = li[r["latitude"]] * len(lons) + lo[r["longitude"]]
            acc[k][0] += float(v)
            acc[k][1] += 1
    return lats, lons, days, [int(round(10 * s / n)) if n else None for s, n in acc]


def _boxes(lats, lons, grid) -> dict:
    out = {}
    for key, (lat_s, lat_n, lon_w, lon_e) in W.BOXES.items():
        s = n = 0
        for i, la in enumerate(lats):
            if lat_s <= la <= lat_n:
                for j, ln in enumerate(lons):
                    inside = (lon_w <= ln <= lon_e) if lon_w < lon_e else (ln >= lon_w or ln <= lon_e)
                    g = grid[i * len(lons) + j]
                    if inside and g is not None:
                        s += g / 10.0
                        n += 1
        out[key] = round(s / n, 2) if n else None
    return out


def _last_days(newest: dict) -> dict:
    """The last LAST days of the freshest dataset: the sea half of the scrubber's 30-day stop."""
    for ds, label in W.DATASETS:  # near-real-time first, as the weekly map does
        if ds not in newest:
            continue
        t1 = newest[ds]
        t0 = t1 - timedelta(days=LAST - 1)
        lats, lons, days, grid = _mean_field(ds, t0, t1)
        if len(days) >= LAST - 3:
            return {"start": t0.date().isoformat(), "end": t1.date().isoformat(), "dataset": ds, "product": label,
                    "preliminary": ds.startswith("ncdcOisst21Nrt"), "days_averaged": len(days),
                    "grid": {"lat0": lats[0], "lon0": lons[0], "step_deg": W.STEP * 0.25, "nlat": len(lats), "nlon": len(lons)},
                    "anom": grid, "box_means_c": _boxes(lats, lons, grid)}
    return {}


def _one_month(y: int, m: int, newest: dict) -> dict | None:
    t0, t1 = _month_bounds(y, m)
    for ds, label in W.DATASETS[::-1]:  # final first: a closed month should be final when it can
        if ds not in newest or newest[ds] < t1:
            continue
        rows = W._fetch(ds, t0, t1)
        lats = sorted({r["latitude"] for r in rows})
        lons = sorted({r["longitude"] for r in rows})
        li = {v: i for i, v in enumerate(lats)}
        lo = {v: i for i, v in enumerate(lons)}
        acc = [[0.0, 0] for _ in range(len(lats) * len(lons))]
        days = set()
        for r in rows:
            days.add(r["time"])
            v = r.get("anom")
            if v is None:
                continue
            k = li[r["latitude"]] * len(lons) + lo[r["longitude"]]
            acc[k][0] += float(v)
            acc[k][1] += 1
        need = (t1 - t0).days + 1
        if len(days) < need - 3:  # the final dataset can miss a day; the payload records how many were averaged
            raise RuntimeError(f"{y}-{m:02d}: {ds} returned {len(days)} of {need} days")
        grid = [int(round(10 * s / n)) if n else None for s, n in acc]

        def box_mean(b):
            s = n = 0
            lat_s, lat_n, lon_w, lon_e = b
            for i, la in enumerate(lats):
                if lat_s <= la <= lat_n:
                    for j, ln in enumerate(lons):
                        inside = (lon_w <= ln <= lon_e) if lon_w < lon_e else (ln >= lon_w or ln <= lon_e)
                        g = grid[i * len(lons) + j]
                        if inside and g is not None:
                            s += g / 10.0
                            n += 1
            return round(s / n, 2) if n else None

        return {"month": f"{y}-{m:02d}", "dataset": ds, "product": label, "preliminary": ds.startswith("ncdcOisst21Nrt"),
                "days_averaged": len(days), "days_in_month": need, "grid": {"lat0": lats[0], "lon0": lons[0], "step_deg": W.STEP * 0.25,
                                                     "nlat": len(lats), "nlon": len(lons)},
                "anom": grid, "box_means_c": {k: box_mean(b) for k, b in W.BOXES.items()}}
    return None


def build() -> dict:
    newest = {}
    for ds, _ in W.DATASETS:
        try:
            newest[ds] = W._last_time(ds)
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] {ds}: {type(e).__name__}: {e}")
    if not newest:
        raise RuntimeError("no OISST dataset reachable")
    last = max(newest.values())
    # The last MONTHS calendar months that have ended by the newest day either dataset holds.
    y, m = last.year, last.month
    if last < _month_bounds(y, m)[1]:
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    want = []
    for _ in range(MONTHS):
        want.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    want.reverse()
    old = {}
    if OUT.exists():
        try:
            old = {x["month"]: x for x in json.loads(OUT.read_text())["data"]["months"]}
        except Exception:  # noqa: BLE001
            old = {}
    months = []
    for y, m in want:
        key = f"{y}-{m:02d}"
        prev = old.get(key)
        final_ready = W.DATASETS[1][0] in newest and newest[W.DATASETS[1][0]] >= _month_bounds(y, m)[1]
        if prev and (not prev.get("preliminary") or not final_ready):
            months.append(prev)
            continue
        got = _one_month(y, m, newest)
        if got:
            print(f"[fetch] {key} from {got['dataset']} ({got['days_averaged']} days)")
            months.append(got)
        elif prev:
            months.append(prev)
    if not months:
        raise RuntimeError("no complete month available")
    last30 = _last_days(newest)
    return {
        "last30": last30,
        "product": "NOAA OISST v2.1 daily anomaly, monthly mean", "base": "1971-2000 (OISST daily climatology)",
        "source_url": f"{W.ERDDAP}/{W.DATASETS[1][0]}.html",
        "encoding": "row-major from the southern edge, tenths of a degree C, null over land",
        "months": months,
        "notes": ["Each month is the mean of every daily OISST v2.1 anomaly field in it, on the same 2-degree grid and "
                  "1971-2000 base as this week's map; a month still on the preliminary near-real-time data says so.",
                  "The box means are a cross-check of the picture, not CPC's monthly Nino indices (ERSST, 1991-2020 base)."],
    }


def main() -> int:
    payload = build()
    path = write_json("sst_months.json", payload, source="NOAA NCEI OISST v2.1 via CoastWatch ERDDAP",
                      notes="Monthly mean sea-surface temperature anomaly for the last complete months, El Nino Ocean map.",
                      status="ok")
    path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=False, separators=(",", ":")))
    for x in payload["months"]:
        print(f"[OK] {x['month']} {x['product']} days {x['days_averaged']} nino34 {x['box_means_c'].get('nino34')} nino12 {x['box_means_c'].get('nino12')}")
    l30 = payload.get("last30") or {}
    print(f"[OK] last 30 days {l30.get('start')}..{l30.get('end')} ({l30.get('days_averaged')} days) nino34 {(l30.get('box_means_c') or {}).get('nino34')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
