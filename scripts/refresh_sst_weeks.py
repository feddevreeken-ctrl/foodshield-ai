#!/usr/bin/env python3
"""refresh_sst_weeks.py -- the observed sea-surface anomaly week by week: a rolling archive for the El Nino Ocean map.

The sea half of rain_weeks.json (refresh_rain_weeks.py), for the same weeks: calendar weeks, Monday
to Sunday, keyed by their Sunday (a fixed weekly phase; see that module for why not windows anchored
on the newest day). Each run (re)computes the four most recent complete weeks (Sunday on or before
the newest OISST day) that are not final; a week is final, and never recomputed again, once the run
date (UTC) is FINAL_AFTER = 7 days past its Sunday; at most MAX_WEEKS = 12 are kept (oldest dropped);
a stored week is never dropped because a run could not recompute it.

Same product, grid and base as sst_anomaly.json (refresh_sst_anomaly.py): NOAA OISST v2.1 daily
anomaly through CoastWatch ERDDAP, one 0.25-degree cell in eight (2 degrees), 84.875S-84.875N,
against OISST's 1971-2000 daily climatology; a week is the mean of its daily fields, tenths of a
degree C, and needs NEED_DAYS of its 7 days. Box means over the Nino boxes are a cross-check of the
picture, not CPC's indices (see sst_anomaly.json box_note).

Data: the 30 daily fields refresh_sst_months.py fetched for its last30 block in this run_all process
(refresh_sst_months.LAST30: no second request; one 30-day ERDDAP fetch takes about 80 s) when they
cover the weeks to compute, else one fetch spanning them (a first run: four weeks). Near-real-time
first, as the live map. The near-real-time data is preliminary; NOAA's final dataset replaces it
about two weeks later. Each week says which it was read from (dataset, preliminary); a stored week is
not refetched when the final data arrives (sst_months.json does that for months).

Pure Python (requests only). Standalone:  python3 scripts/refresh_sst_weeks.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_json  # noqa: E402
import refresh_sst_anomaly as W  # noqa: E402
import refresh_sst_months as M  # noqa: E402
from refresh_rain_weeks import sundays  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "sst_weeks.json"
WANT, MAX_WEEKS, FINAL_AFTER, NEED_DAYS = 4, 12, 7, 6
LABEL = dict(W.DATASETS)


def _stored() -> tuple[dict, dict | None]:
    try:
        d = json.loads(OUT.read_text())["data"]
        return {w["end"]: w for w in d["weeks"]}, d["grid"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}, None


def _noon(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)


def _source(standalone: bool) -> tuple[str, list | None, date]:
    """(dataset, the refresh_sst_months rows or None, newest day of that dataset)."""
    st = M.LAST30
    if st:
        return st["ds"], st["rows"], date.fromisoformat(max(r["time"] for r in st["rows"])[:10])
    if not standalone:
        raise RuntimeError("the 'OISST monthly SST' step left no daily fields in this process; keeping the last good file")
    errors = []
    for ds, _ in W.DATASETS:  # near-real-time first, as the weekly map
        try:
            t1 = W._last_time(ds)
            if (datetime.now(timezone.utc) - t1).days > 40:
                raise RuntimeError(f"newest time {t1.date()} -- frozen feed")
            return ds, None, t1.date()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{ds}: {type(e).__name__}: {e}")
    raise RuntimeError("no OISST dataset reachable -- " + " | ".join(errors))


def _weeks(rows: list, todo: list[date]) -> tuple[dict, dict]:
    """{Sunday: (grid of tenths of a degree C, days averaged)} for the weeks in todo, and the grid."""
    lats = sorted({r["latitude"] for r in rows})
    lons = sorted({r["longitude"] for r in rows})
    li, lo = {v: i for i, v in enumerate(lats)}, {v: i for i, v in enumerate(lons)}
    n = len(lats) * len(lons)
    of = {(s - timedelta(days=i)).isoformat(): s for s in todo for i in range(7)}
    acc = {s: ([0.0] * n, [0] * n, set()) for s in todo}
    for r in rows:
        s = of.get(r["time"][:10])
        if s is None:
            continue
        a = acc[s]
        a[2].add(r["time"][:10])
        v = r.get("anom")
        if v is not None:
            k = li[r["latitude"]] * len(lons) + lo[r["longitude"]]
            a[0][k] += float(v)
            a[1][k] += 1
    grid = {"lat0": lats[0], "lon0": lons[0], "step_deg": W.STEP * 0.25, "nlat": len(lats), "nlon": len(lons)}
    return {s: ([int(round(10 * t / c)) if c else None for t, c in zip(a[0], a[1])], len(a[2]))
            for s, a in acc.items()}, {"grid": grid, "lats": lats, "lons": lons}


def build(standalone: bool = False) -> tuple[dict, dict]:
    ds, rows, newest = _source(standalone)
    run_day = datetime.now(timezone.utc).date()
    old, old_grid = _stored()
    todo = [s for s in sundays(newest, WANT) if not (old.get(s.isoformat()) or {}).get("final")]
    weeks, fresh, grid = dict(old), [], old_grid
    if todo:
        first, last = min(todo) - timedelta(days=6), max(todo)
        have = {r["time"][:10] for r in rows} if rows else set()
        if not all((first + timedelta(days=i)).isoformat() in have for i in range((last - first).days + 1)):
            t = time.monotonic()
            rows = W._fetch(ds, _noon(first), _noon(last))
            print(f"[fetch] {ds} {first}..{last}: {len(rows)} cells ({time.monotonic() - t:.0f} s)")
        got, g = _weeks(rows, todo)
        del rows
        if old_grid and old_grid != g["grid"]:
            raise RuntimeError(f"OISST grid {g['grid']} differs from the stored weeks' {old_grid}; keeping the last good file")
        grid = g["grid"]
        for s in todo:
            anom, ndays = got[s]
            if ndays < NEED_DAYS:
                print(f"[WARN] week to {s}: {ndays} of 7 days in {ds}; not written")
                continue
            vals = [v for v in anom if v is not None]
            weeks[s.isoformat()] = {
                "start": (s - timedelta(days=6)).isoformat(), "end": s.isoformat(), "days_averaged": ndays,
                "dataset": ds, "product": LABEL.get(ds, ds), "preliminary": ds.startswith("ncdcOisst21Nrt"),
                "final": (run_day - s).days >= FINAL_AFTER, "computed": run_day.isoformat(),
                "range_c": [min(vals) / 10.0, max(vals) / 10.0],
                "box_means_c": M._boxes(g["lats"], g["lons"], anom), "anom": anom}
            fresh.append(s.isoformat())
        if not fresh and any((run_day - s).days < FINAL_AFTER for s in todo):  # an old week the data never completed
            raise RuntimeError(f"no week could be computed ({', '.join(map(str, todo))}); keeping the last good file")
    if not grid:
        raise RuntimeError("no stored or computed week")
    payload = {
        "product": "NOAA OISST v2.1 daily anomaly, weekly mean", "base": "1971-2000 (OISST daily climatology)",
        "source_url": f"{W.ERDDAP}/{W.DATASETS[0][0]}.html",
        "grid": grid,
        "encoding": ("weeks: oldest first, one per calendar week (Monday start .. Sunday end); anom row-major from the "
                     "southern edge on grid, tenths of a degree C, null over land (ice-covered sea keeps a value), as "
                     "sst_anomaly.json"),
        "weeks_rule": ("calendar weeks, Monday to Sunday, keyed by the Sunday, the same weeks as rain_weeks.json; each "
                       "run recomputes the four most recent complete weeks that are not final; a week is the mean of "
                       f"its daily OISST anomaly fields and needs {NEED_DAYS} of its 7 days"),
        "final_rule": (f"final, never recomputed, once the run date (UTC) is {FINAL_AFTER} days past the Sunday; at "
                       f"most {MAX_WEEKS} weeks kept, oldest dropped first; a week read from the near-real-time "
                       "dataset stays marked preliminary"),
        "max_weeks": MAX_WEEKS,
        "newest_oisst_day": newest.isoformat(),
        "box_note": ("Means of this 2-degree OISST grid over the Nino boxes, a cross-check of the picture only: not the "
                     "ONI (ERSST) or CPC's weekly values (1991-2020 base); this field uses OISST's 1971-2000 daily "
                     "climatology."),
        "weeks": [weeks[k] for k in sorted(weeks)][-MAX_WEEKS:],
        "notes": ["Each week is the mean of the daily OISST v2.1 anomaly fields from Monday to Sunday, on the same "
                  "2-degree grid and 1971-2000 base as this week's map. Weeks on the preliminary near-real-time data "
                  "say so."],
    }
    return payload, {"fresh": fresh}


def main(standalone: bool = False) -> int:
    t0 = time.monotonic()
    payload, diag = build(standalone)
    path = write_json("sst_weeks.json", payload, source="NOAA NCEI OISST v2.1 via CoastWatch ERDDAP",
                      notes=("Weekly (Monday-Sunday) mean sea-surface temperature anomaly on a 2-degree grid, a rolling "
                             "archive of up to 12 weeks for the El Nino Ocean map. Base is OISST's 1971-2000 "
                             "climatology; do not read box means as the ONI."),
                      status="ok")
    path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=False, separators=(",", ":")))
    print(f"[OK] sea weeks: {len(payload['weeks'])} kept, recomputed {diag['fresh'] or 'none'} | "
          f"{path.stat().st_size / 1e3:.0f} KB | {time.monotonic() - t0:.0f} s")
    for w in payload["weeks"]:
        b = w["box_means_c"]
        print(f"[check] {w['start']}..{w['end']} {'final' if w['final'] else 'open '} {w['dataset']} "
              f"{w['days_averaged']} days, range {w['range_c'][0]:+.1f}..{w['range_c'][1]:+.1f} C, nino34 "
              f"{b.get('nino34')} nino12 {b.get('nino12')} nino3 {b.get('nino3')} nino4 {b.get('nino4')}")
    M.LAST30.clear()  # ~450,000 rows, not needed by any later step
    return 0


if __name__ == "__main__":
    raise SystemExit(main(standalone=True))
