#!/usr/bin/env python3
"""refresh_rain_weeks.py -- observed rain on land week by week: a rolling archive for the El Nino Ocean map.

Why. The map's "last 7 days" stop (rain_anomaly.json "week") moves with every run, so a week's
picture was gone a week later (owner, 2026-09-28: "for next week make it automatically show last
week too so store data"). This file keeps the weeks, and sst_weeks.json (refresh_sst_weeks.py)
keeps the sea for the same weeks.

Weeks. Calendar weeks, Monday to Sunday, keyed by their Sunday. Not "the 7 days ending on the
newest CPC day, and 7, 14 and 21 days before": that day moves one day every day, so windows
anchored on it give a new set of weeks, overlapping yesterday's, on every run, and an archive of
them is no history. A fixed weekly phase is needed; a calendar one needs no state shared with the
sea file, whose step runs before this one. When the newest CPC day is a Sunday the newest week is
the live week. Each run (re)computes the four most recent complete weeks (Sunday on or before the
newest CPC day) that are not final yet; on the first run that is four weeks.

A week is exactly what the live week layer (refresh_rain_anomaly.py, the same functions) shows when
that Sunday is the newest CPC day: CPC gauge cells over the seven days; the gauge test over the 30
days ending on the Sunday (MIN_GAUGES reports a day inside the cell) and then over the week on its
own; the spike filter against the cell's normal over those 30 days; NEED_SHARE of the days; the arid
mask; percent against the 1991-2020 normal of the same calendar days; SPI x 100 from the 7-day fit of
the pentad end nearest the Sunday. The run's 30 CPC days come from the "CPC observed rain" step
(refresh_rain_anomaly.LAST, the same run_all process); a week whose 30-day context reaches further
back reads the missing days itself (1.8 MB each; up to 21 days on a first run, 0-4 afterwards).

30-day base (d30, added 28 September 2026). On the live 7-day stop the page paints the last 30 days
(rain_anomaly.json top level) and glazes the week over it, so each week also carries "d30": the
live 30-day layer as it reads with that Sunday as the newest CPC day (the same _windows call that
gives the week: CPC cells over the 30 days ending on the Sunday, percent, mm, SPI from the d30 fit
of the pentad end nearest the Sunday), with its own CHIRPS fill from a six-pentad total. A week
stored before d30 existed gets it once, then follows the finality rule like the rest of the week.

CHIRPS fill. Cells CPC leaves blank that week get the CHIRPS v3 Early Estimates single-pentad
total (chirps_rain_fill.py: same files, 1991-2020 pentad normal, pixel rule, arid mask and p1 SPI
fit) of the pentad that shares the most days with the week (ties: the later one); d30's blank cells
get the six-pentad total (p6 fit) whose six pentads share the most days with the 30 days (ties: the
later). Not the live rule (the newest posted pentad, often before the window): a past week can wait
for its own pentads. For the week ending on a live newest Sunday both rules pick the live fill's
pentad (checked: identical). Not "the four newest pentads in order" either: four pentads span about 20 days and four
weeks 28, so the oldest week would share one day with its pentad. The chosen pentad shares at
least 3 of the week's 7 days and is posted by the Thursday after it. CHC keeps the last 72
single- and six-pentad totals; the two the live fill read are reused, others are read here (66 MB,
about 10-15 s each, within CHIRPS_BUDGET_S). A fill left out, or on pentads that were not the best
ones, is redone on a later run.

Final. CPC posts a day the next afternoon and re-issues it once, two days after it (21:51 UTC). Of
the 270 days of 2026 in CPC's RT listing on 28 September 2026, 268 were last modified within 2.9
days of the day itself; one at 6.8 days (15 September), one at 62 (27 May). A week is final, and never recomputed again, once
the run date (UTC) is FINAL_AFTER = 7 days past its Sunday; until then every run recomputes it. At
most MAX_WEEKS = 12 are kept (the oldest drop off); a stored week is never dropped because a run
could not recompute it. A week with too few CPC days (NEED_SHARE) is left out. When no week could be computed
and one of them is still open the step fails and run_all keeps the last good file.

Encoding: see ENCODING (sparse; about 2,800 of the 7,488 cells are land with a reading).

Pure Python (requests only), like the collectors it reuses. Standalone:
    python3 scripts/refresh_rain_weeks.py     (runs the live build first, then the weeks)
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402
import chirps_rain_fill as F  # noqa: E402
import refresh_rain_anomaly as R  # noqa: E402
import spi as SPI  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "rain_weeks.json"
WANT, MAX_WEEKS, FINAL_AFTER = 4, 12, 7
CHIRPS_BUDGET_S = 150
GRID = (R.LAT0, R.LON0, R.STEP, R.NLAT, R.NLON)
ENCODING = ("weeks: oldest first, one per calendar week (Monday start .. Sunday end). In each week, cells lists the "
            "output grid indexes (row-major from the southern edge, as rain_anomaly.json) where CPC has a gauge "
            "reading that week, ascending; anom (integer percent change against 1991-2020, null where arid), spi (SPI "
            "x 100, integers -300..300, null where the cell has no fit, set on arid cells too; the whole list null if the fit "
            "file could not be read), mm and norm_mm (whole mm over the week's days: what fell, and the 1991-2020 "
            "normal of the same days) are parallel to cells. fill (null when CHIRPS could not be read): the same "
            "fields for the cells CPC leaves blank that week and CHIRPS covers, over the pentad fill.start..fill.end "
            "(fill.days); fill.cells never repeats a cell of the week's cells. A cell in neither list is blank (sea, "
            "or no reading). d30: the same fields over the 30 days ending on the week's Sunday (d30.start..d30.end, "
            "d30.days CPC days), the live 30-day layer's rules and d30 fit; d30.fill is the six-pentad CHIRPS total "
            "d30.fill.pentads (d30.fill.start..end), its cells never on a cell of d30.cells.")
# (label, lat_s, lat_n, lon_w, lon_e) on cell centres, for the printed checks only.
REGIONS = (("Maritime Continent", -11, 6, 95, 141), ("Amazon interior", -12, 0, -72, -50),
           ("Central America dry corridor", 11, 16, -92, -85), ("East Africa", -5, 12, 33, 51),
           ("Europe", 36, 71, -10, 40), ("eastern Australia", -38, -15, 140, 154))


def sundays(newest: date, n: int = WANT) -> list[date]:
    """Last days of the n most recent complete Monday-Sunday weeks ending on or before newest, oldest first."""
    last = newest - timedelta(days=(newest.weekday() + 1) % 7)
    return [last - timedelta(days=7 * i) for i in range(n - 1, -1, -1)]


def _stored() -> dict:
    try:
        return {w["end"]: w for w in json.loads(OUT.read_text())["data"]["weeks"]}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


# --- CPC ---------------------------------------------------------------------

def _cpc_days(ends: list[date]) -> tuple[dict, set]:
    """Per-day cell values (refresh_rain_anomaly._cell_day) for the 30 days up to each Sunday, and CPC's listing."""
    st = R.LAST
    per, listed = dict(st["per"]), set(st["listed"])
    first = min(ends) - timedelta(days=R.DAYS - 1)
    for y in range(first.year, st["end"].year):  # a context reaching into a year the live step did not list
        if not any(d.year == y for d in listed):
            listed |= R._listed_days(y)
    need = sorted({s - timedelta(days=i) for s in ends for i in range(R.DAYS)} & (listed - set(per)))
    if need:
        norm, _ = R._normal(need)
        with ThreadPoolExecutor(4) as ex:
            for d, obs in zip(need, ex.map(R._cpc_day, need)):
                per[d] = R._cell_day(obs, norm[d])
        print(f"[fetch] {len(need)} more CPC days, {need[0]}..{need[-1]}")
    return per, listed


def _block(c: list, kind: str, sunday: date, nominal: int, n: int) -> dict:
    """One CPC layer (refresh_rain_anomaly._windows output) as a sparse block, as the live layer writes it."""
    anom = R._pct(c)
    try:
        spi, _ = R._spi(c, anom, kind, sunday, nominal)
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as e:
        print(f"[WARN] {kind} to {sunday}: SPI left out: {type(e).__name__}: {e}")
        spi = None
    cells = [k for k, x in enumerate(c) if x is not None]
    return {"start": (sunday - timedelta(days=nominal - 1)).isoformat(), "end": sunday.isoformat(), "days": n,
            "cells": cells, "anom": [anom[k] for k in cells], "spi": None if spi is None else [spi[k] for k in cells],
            "mm": [int(round(c[k][0] * n)) for k in cells], "norm_mm": [int(round(c[k][1] * n)) for k in cells],
            "n_valid": sum(anom[k] is not None for k in cells)}


def cpc_week(per: dict, listed: set, sunday: date) -> tuple[dict, dict] | None:
    """The live week and 30-day layers' CPC part with sunday as the newest day: (week block, d30 block)."""
    days = [d for d in (sunday - timedelta(days=i) for i in range(R.DAYS - 1, -1, -1)) if d in listed]
    wdays = [d for d in days if d > sunday - timedelta(days=R.WEEK)]
    if len(days) < R.NEED_SHARE * R.DAYS or len(wdays) < R.NEED_SHARE * R.WEEK:
        print(f"[WARN] week to {sunday}: CPC has {len(days)}/{R.DAYS} context days, {len(wdays)}/{R.WEEK} in the week")
        return None
    c30, c7, _, _, _ = R._windows(per, days, wdays)
    return _block(c7, "d7", sunday, R.WEEK, len(wdays)), _block(c30, "d30", sunday, R.DAYS, len(days))


# --- CHIRPS --------------------------------------------------------------------

def _pentad_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) * 6 + min((d.day - 1) // 5, 5) + 1


def _span(yp: tuple[int, int], n: int) -> tuple[date, date, list]:
    """First day, last day and pentads of the n pentads ending with yp."""
    w = F._back(*yp, n)
    return F.pentad_dates(*w[0])[0], F.pentad_dates(*yp)[1], w


def best_pentad(sunday: date, posted: set, n: int = 1) -> tuple[int, int] | None:
    """The last pentad of the posted n-pentad total sharing the most days with the 7 (n = 1) or 30 (n = 6) days
    ending on sunday (ties: the later one); None if none shares a day."""
    first = sunday - timedelta(days=6 if n == 1 else R.DAYS - 1)

    def shared(yp):
        s, e, _ = _span(yp, n)
        return (min(e, sunday) - max(s, first)).days + 1
    ends = {_pentad_of(sunday + timedelta(days=i)) for i in range(-15, 8)} & posted
    cands = sorted((yp for yp in ends if shared(yp) > 0), key=lambda yp: (shared(yp), yp))
    return cands[-1] if cands else None


def _tag(yp: tuple[int, int], n: int) -> str:
    if n == 1:
        return f"{yp[0]}-p{yp[1]:02d}"
    (y0, p0), (y1, p1) = _span(yp, n)[2][0], yp
    return f"{y0}-p{p0:02d}..{'' if y1 == y0 else f'{y1}-'}p{p1:02d}"


_normal: list = []


def pentad_grid(y: int, p: int, n: int = 1) -> list:
    """Per output cell, (observed, 1991-2020 normal) mm a day over the n pentads ending with (y, p), or None:
    chirps_rain_fill.fill's layer(), term for term."""
    if not _normal:
        _normal.append(F._cache())
    c = _normal[0]
    obs = F._tif_cells(F.EE_TIF.format(n=n, y=y, p=p))
    if abs(sum(obs[1]) - c["land_pixels"]) > 0.001 * c["land_pixels"]:
        raise RuntimeError(f"{sum(obs[1])} land pixels in {_tag((y, p), n)}, {c['land_pixels']} in the normal")
    window = F._back(y, p, n)
    nd, out = sum((F.pentad_dates(*w)[1] - F.pentad_dates(*w)[0]).days + 1 for w in window), [None] * R.NCELL
    for i, (k, npix) in enumerate(zip(c["cells"], c["npix"])):
        if obs[1][k] == npix:
            norm = sum(c["rate"][q - 1][i] / 1000 * ((F.pentad_dates(yy, q)[1] - F.pentad_dates(yy, q)[0]).days + 1)
                       for yy, q in window)
            out[k] = (obs[0][k] / npix / nd, norm / nd)
    return out


def fill_block(need: list, yp: tuple[int, int], grid: list, n: int = 1) -> dict:
    """A fill: the CHIRPS total of the n pentads ending with yp on the cells where need is True and CHIRPS has a
    reading; the week's (n = 1, "pentad") or d30's (n = 6, "pentads")."""
    y, p = yp
    s, e, _ = _span(yp, n)
    nd = (e - s).days + 1
    cells = [k for k in range(R.NCELL) if need[k] and grid[k] is not None]
    anom = [None if grid[k][1] < R.ARID_MM_DAY else int(round(100 * (grid[k][0] - grid[k][1]) / grid[k][1]))
            for k in cells]
    blk = {"pentad" if n == 1 else "pentads": _tag(yp, n), "start": s.isoformat(), "end": e.isoformat(), "days": nd,
           "cells": cells, "anom": anom, "spi": None, "mm": [int(round(grid[k][0] * nd)) for k in cells],
           "norm_mm": [int(round(grid[k][1] * nd)) for k in cells], "n_valid": sum(a is not None for a in anom)}
    try:
        fits, m = SPI.chirps_fits(GRID, f"p{n}", p - 1)
        blk["spi"] = [None if k not in fits else
                      SPI.to_x100(SPI.spi_chirps(grid[k][0] * nd, nd, fits[k], m["zero_mm"], m["n_years"]))
                      for k in cells]
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as e:
        print(f"[WARN] CHIRPS SPI for {_tag(yp, n)} left out: {type(e).__name__}: {e}")
    return blk


def _remask(fill: dict | None, cpc_cells: list) -> dict | None:
    """A stored fill without the cells CPC now covers (a recomputed week whose CHIRPS could not be read this run)."""
    if not fill:
        return None
    have = set(cpc_cells)
    keep = [i for i, k in enumerate(fill["cells"]) if k not in have]
    out = dict(fill)
    for key in ("cells", "anom", "spi", "mm", "norm_mm"):
        if out.get(key) is not None:
            out[key] = [fill[key][i] for i in keep]
    out["n_valid"] = sum(a is not None for a in out["anom"])
    return out


def _chirps(weeks: dict, fresh: set, old: dict) -> None:
    """Give each kept week's layers (the week: n = 1; d30: n = 6) the fill of their best posted CHIRPS total, newest
    week first, within CHIRPS_BUDGET_S. fresh: (Sunday, n) pairs whose CPC block this run recomputed."""
    posted, grids = {1: set(), 6: set()}, {}
    for n in posted:
        try:
            html = http_get(F.EE_DIR.format(n=n), timeout=60, headers=F.UA, retries=3).text
            posted[n] = {(int(y), int(p)) for y, p in re.findall(rf"Total_{n:02d}PentAccum_(\d{{4}})_p(\d\d)\.tif", html)}
        except RuntimeError as e:
            print(f"[WARN] CHIRPS listing ({n} pentads): {e}")
    lf, ld = R.LAST["payload"].get("fill"), R.LAST.get("chirps")
    if lf and ld:  # the two totals the live fill already read
        yp = _pentad_of(date.fromisoformat(lf["week"]["end"]))
        for n, key in ((1, "c7"), (6, "c30")):
            grids[(n, yp)] = ld[key]
            posted[n].add(yp)
    F._deadline[0] = time.monotonic() + CHIRPS_BUDGET_S
    for key in sorted(weeks, reverse=True)[:MAX_WEEKS]:
        for n, blk, prev in ((1, weeks[key], old.get(key) or {}), (6, weeks[key].get("d30"), (old.get(key) or {}).get("d30") or {})):
            if blk is None:
                continue
            yp = best_pentad(date.fromisoformat(key), posted[n], n)
            if (key, n) not in fresh and (yp is None or (blk.get("fill") or {}).get("pentad" if n == 1 else "pentads") == _tag(yp, n)):
                continue  # stored, and no better total is posted
            try:
                if yp is None:
                    raise RuntimeError("no posted CHIRPS total overlaps the window")
                if (n, yp) not in grids:
                    t = time.monotonic()
                    grids[(n, yp)] = pentad_grid(*yp, n)
                    print(f"[fetch] CHIRPS {_tag(yp, n)} ({time.monotonic() - t:.0f} s)")
                cpc = set(blk["cells"])
                blk["fill"] = fill_block([k not in cpc for k in range(R.NCELL)], yp, grids[(n, yp)], n)
            except Exception as e:  # noqa: BLE001 -- a fill only: the CPC layer stands without it
                print(f"[WARN] {'week' if n == 1 else 'd30'} to {key}: CHIRPS fill: {type(e).__name__}: {e}")
                if (key, n) in fresh:
                    blk["fill"] = _remask(prev.get("fill"), blk["cells"])


# --- the archive -----------------------------------------------------------------

def build(standalone: bool = False) -> tuple[dict, dict]:
    if not R.LAST:
        if not standalone:
            raise RuntimeError("the 'CPC observed rain' step left no CPC days in this process; keeping the last good file")
        R.build()
    newest, run_day = R.LAST["end"], datetime.now(timezone.utc).date()
    old = _stored()
    todo = [s for s in sundays(newest) if not (old.get(s.isoformat()) or {}).get("final")]
    # Weeks stored before d30 existed get it once (the week itself stays as stored).
    no_d30 = [date.fromisoformat(k) for k in sorted(old)[-MAX_WEEKS:] if "d30" not in old[k]
              and date.fromisoformat(k) not in todo]
    fresh, weeks = set(), {k: dict(w) for k, w in old.items()}
    if todo or no_d30:
        per, listed = _cpc_days(todo + no_d30)
        for s in todo + no_d30:
            got = cpc_week(per, listed, s)
            if not got:
                continue
            wk, d30 = got
            d30["fill"] = None
            if s in todo:
                wk.update(final=(run_day - s).days >= FINAL_AFTER, computed=run_day.isoformat(), fill=None)
                weeks[wk["end"]] = wk
                fresh.add((wk["end"], 1))
            weeks[wk["end"]]["d30"] = d30
            fresh.add((wk["end"], 6))
        del per
        if not any(n == 1 for _, n in fresh) and any((run_day - s).days < FINAL_AFTER for s in todo):
            raise RuntimeError(f"no week could be computed ({', '.join(map(str, todo))}); keeping the last good file")
    _chirps(weeks, fresh, old)
    kept = [weeks[k] for k in sorted(weeks)][-MAX_WEEKS:]
    payload = {
        "product": "NOAA CPC Global Unified Gauge-Based Analysis of Daily Precipitation, real-time, 0.5 degree",
        "fill_product": F.PRODUCT,
        "source_url": f"{R.CPC_RT}/", "normal_source_url": R.PSL_PAGE, "fill_source_url": F.EE + "/",
        "base": "1991-2020",
        "grid": {"lat0": R.LAT0, "lon0": R.LON0, "step_deg": R.STEP, "nlat": R.NLAT, "nlon": R.NLON},
        "encoding": ENCODING,
        "weeks_rule": ("calendar weeks, Monday to Sunday, keyed by the Sunday; each run recomputes the four most recent "
                       "complete weeks that are not final; a week is the live week layer of rain_anomaly.json as it "
                       "reads with that Sunday as the newest CPC day, filled from the CHIRPS pentad sharing the most "
                       "days with it"),
        "final_rule": (f"final, never recomputed, once the run date (UTC) is {FINAL_AFTER} days past the Sunday (CPC "
                       "re-issues a day two days after it); at most "
                       f"{MAX_WEEKS} weeks kept, oldest dropped first"),
        "max_weeks": MAX_WEEKS,
        "newest_cpc_day": newest.isoformat(),
        "min_gauges_per_day": R.MIN_GAUGES, "arid_mm_day": R.ARID_MM_DAY,
        "spi_info": {"cpc": (f"data/ref/{SPI.CPC_PARAMS.name}, the 7-day (week) and 30-day (d30) fits ending on the "
                             "pentad end nearest the Sunday (as week.spi and spi in rain_anomaly.json)"),
                     "fill": (f"data/ref/{SPI.CHIRPS_PARAMS.name}, the one-pentad (fill) and six-pentad (d30.fill) fits "
                              "ending at the fill's last pentad (as fill.week.spi and fill.spi in rain_anomaly.json)"),
                     "encoding": "SPI x 100 as integers, clamped to -300..300 (-300 = -3 or below)"},
        "weeks": kept,
        "notes": [
            "Rain for each calendar week, Monday to Sunday, against the 1991-2020 average of the same days, from rain "
            "gauges (NOAA CPC). Each week reads as the 'last 7 days' layer read when that Sunday was the newest day.",
            "Where CPC has too few gauges the cell shows CHIRPS (satellite infrared blended with stations) for the "
            "five-day pentad that shares the most days with the week; its own dates are given.",
            "Each week also carries the 30 days ending on its Sunday (d30), as the 'last 30 days' layer read then, so a "
            "past week can be shown over its own 30-day picture, as the live week is.",
            f"A week is recomputed while CPC may still revise its days and frozen {FINAL_AFTER} days after its "
            f"Sunday. The last {MAX_WEEKS} weeks are kept.",
            "SPI puts every week on the drought scale of the 30-day and monthly layers: -1, -1.5 and -2 are "
            "moderately, severely and extremely dry for that place and time of year; +1, +1.5 and +2 the wet mirror.",
        ],
    }
    return payload, {"fresh": sorted({k for k, n in fresh if n == 1}), "d30": sorted({k for k, n in fresh if n == 6})}


def _spi_of(wk: dict) -> dict:
    out = {}
    for blk in (wk, wk.get("fill") or {}):
        for k, v in zip(blk.get("cells") or [], blk.get("spi") or []):
            if v is not None:
                out[k] = v
    return out


def _check_live(wk: dict) -> None:
    """The week ending on the live step's newest day must equal rain_anomaly.json's week and 30-day layers."""
    pay = R.LAST["payload"]
    lf = pay.get("fill") or {}
    for label, mine, live, lfill, lend in (("week", wk, pay["week"], lf.get("week"), (lf.get("week") or {}).get("end")),
                                           ("d30", wk.get("d30") or {}, pay, lf or None, (lf.get("window") or {}).get("end"))):
        full = {key: [None] * R.NCELL for key in ("anom", "spi", "mm", "norm_mm")}
        for key in full:
            for k, v in zip(mine.get("cells") or [], mine.get(key) or [None] * len(mine.get("cells") or [])):
                full[key][k] = v
        same = {key: full[key] == live.get(key) for key in full if live.get(key) is not None}
        print(f"[check] {label} to {wk['end']} vs rain_anomaly.json (same CPC run): identical {same}, "
              f"{mine.get('start')}..{mine.get('end')} vs {(live.get('window') or live).get('start')}..")
        f = mine.get("fill")
        if lfill and f and f["end"] == lend:
            n = len(pay["fill"]["cells"])
            theirs = [t for t in zip(pay["fill"]["cells"], lfill["anom"], lfill.get("spi") or [None] * n, lfill["mm"],
                                     lfill["norm_mm"]) if t[3] is not None]
            ours = list(zip(f["cells"], f["anom"], f["spi"] or [None] * len(f["cells"]), f["mm"], f["norm_mm"]))
            print(f"[check]   {label} CHIRPS fill {f.get('pentad') or f.get('pentads')} {f['start']}..{f['end']} vs the "
                  f"live fill ({len(ours)} cells): identical {ours == theirs}")


def main(standalone: bool = False) -> int:
    t0 = time.monotonic()
    payload, diag = build(standalone)
    path = write_json("rain_weeks.json", payload,
                      source=("NOAA CPC Global Unified Gauge-Based daily precipitation (CPC FTP) against its 1991-2020 "
                              "daily normal (NOAA PSL, committed cache); where CPC has too few gauges, CHIRPS v3.0 (CHC "
                              "Early Estimates single-pentad totals) against its own 1991-2020 pentad normal"),
                      notes=("Observed rain on land for each of the last calendar weeks (Monday-Sunday) against "
                             "1991-2020, a rolling archive of up to 12 weeks for the El Nino Ocean map, on the grid of "
                             "rain_anomaly.json. Gauge analysis, not a model."),
                      status="ok")
    path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=False, separators=(",", ":")))
    print(f"[OK] rain weeks: {len(payload['weeks'])} kept, recomputed {diag['fresh'] or 'none'}, d30 {diag['d30'] or 'none'} | "
          f"{path.stat().st_size / 1e3:.0f} KB | {time.monotonic() - t0:.0f} s")
    for wk in payload["weeks"]:
        fl, spi = wk.get("fill") or {}, _spi_of(wk)
        s = sorted(spi.values())
        print(f"[check] {wk['start']}..{wk['end']} {'final' if wk['final'] else 'open '} CPC {len(wk['cells'])} cells "
              f"({wk['n_valid']} not arid, {wk['days']} days); fill {fl.get('pentad', 'none')} {fl.get('start', '')}.."
              f"{fl.get('end', '')} {len(fl.get('cells') or [])} cells; SPI {len(s)} cells, median "
              f"{s[len(s) // 2] / 100 if s else float('nan'):+.2f}, |SPI|>=1 "
              f"{100 * sum(abs(v) >= 100 for v in s) / max(1, len(s)):.0f}%")
        d, df = wk.get("d30") or {}, (wk.get("d30") or {}).get("fill") or {}
        print(f"[check]   d30 {d.get('start')}..{d.get('end')} CPC {len(d.get('cells') or [])} cells ({d.get('n_valid')} not "
              f"arid, {d.get('days')} days); fill {df.get('pentads', 'none')} {df.get('start', '')}..{df.get('end', '')} "
              f"{len(df.get('cells') or [])} cells")
        for label, sa, na, wa, ea in REGIONS:
            v = sorted(x for k, x in spi.items() if sa <= R.LAT0 + R.STEP * (k // R.NLON) <= na
                       and wa <= R.LON0 + R.STEP * (k % R.NLON) <= ea)
            print(f"[check]   {label}: {len(v)} cells with SPI, |SPI|>=1 "
                  f"{100 * sum(abs(x) >= 100 for x in v) / max(1, len(v)):.0f}%, median "
                  f"{v[len(v) // 2] / 100 if v else float('nan'):+.2f}")
    newest = R.LAST["end"].isoformat()
    if newest in diag["fresh"]:
        _check_live(next(w for w in payload["weeks"] if w["end"] == newest))
    R.LAST.clear()  # the daily values (~20 MB) are not needed by any later step
    return 0


if __name__ == "__main__":
    raise SystemExit(main(standalone=True))
