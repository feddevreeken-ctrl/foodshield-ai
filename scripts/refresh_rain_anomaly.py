#!/usr/bin/env python3
"""refresh_rain_anomaly.py -- observed rain on land, last 30 days and last 7 days, against 1991-2020.

Feeds the El Nino Ocean map. The past-El-Nino rain layer there shows what
past events did; this file shows what the rain is doing now, on exactly the
same 2.5-degree land grid (build_sst_composites.py rain_grid: centres from
56.25S to 71.25N and 178.75W eastward, 52 x 144), so the page can paint it
with the same layer. Observed gauge analysis only, no model.

Data:
  * Observed: NOAA CPC Global Unified Gauge-Based Analysis of Daily
    Precipitation, real-time version, 0.5 degree, one little-endian binary
    file per day on CPC's own server (CPC_RT). Only the grid's latitude band
    of the rain and gauge-count fields is pulled, with one HTTP Range request
    (1.8 MB a day). The window ends on the newest day CPC has posted, usually one to two
    days behind today. CPC re-issues its newest days as late reports arrive.
  * Normal: the 1991-2020 daily long-term mean of the same CPC product, which
    NOAA PSL publishes (PSL_LTM, read through THREDDS OPeNDAP as DAP2 binary in
    ten-day pieces), taken for the same calendar days. There is deliberately
    no fallback base: CPC's PREC/L monthly analysis was tried and rejected.
    For the same month (August 2026) the real-time daily analysis sat 12%
    below PREC/L at the median of well-gauged cells and 74% below where no
    gauge reports, so a PREC/L base paints droughts that are not there. If PSL
    is down the step fails and run_all keeps the last good file.
  * Each day, each 2.5-degree cell is the mean of its 0.5-degree land cells
    that have a value in both the observations and the normal (up to 25: a
    true cell mean, not a centre sample).
  * Gauge mask. CPC's file also carries the number of reporting gauges per
    0.5-degree box. Away from reporting gauges the real-time analysis decays
    towards no rain (Indonesia, the Congo basin, the Amazon interior), so a
    cell needs on average MIN_GAUGES gauge reports a day inside it over the
    window, or it is null.
  * Spike filter. The real-time analysis carries bad reports that the
    interpolation spreads into bumps of several hundred mm a day (seen on
    2026-09-18: 900 mm over Arctic Siberia; and near-daily 600-850 mm over
    Sumatra). A cell-day more than SPIKE_X times the cell's normal daily rain
    over the window and above SPIKE_MM is dropped, for the observations and
    the normal alike. A cell needs NEED_SHARE of the window's days to show.
  * Cells whose normal over the window is under 0.3 mm a day (the same arid
    threshold as the composites) are null on the percent layers, as are sea
    cells.

Pure Python (requests only), because the GitHub Actions job installs nothing
else.
"""
from __future__ import annotations

import array
import json
import re
import struct
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402

CPC_RT = "https://ftp.cpc.ncep.noaa.gov/precip/CPC_UNI_PRCP/GAUGE_GLB/RT"
CPC_FILE = "PRCP_CU_GAUGE_V1.0GLB_0.50deg.lnx.{d}.RT"
PSL_LTM = "https://psl.noaa.gov/thredds/dodsC/Datasets/cpc_global_precip/precip.day.ltm.1991-2020.nc"
PSL_PAGE = "https://psl.noaa.gov/data/gridded/data.cpc.globalprecip.html"
UA = {"User-Agent": "FoodShield-AI data refresh (github.com/feddevreeken-ctrl/foodshield-ai)"}

DAYS, WEEK = 30, 7
NEED_SHARE = 0.85          # share of the window's days a cell needs after the spike filter
ARID_MM_DAY = 0.3
SPIKE_X, SPIKE_MM = 20, 50
MIN_GAUGES = 1.0          # mean gauge reports a day inside a 2.5-degree cell
# Output grid: the composites' rain_grid.
LAT0, LON0, STEP, NLAT, NLON = -56.25, -178.75, 2.5, 52, 144
NCELL, SUB = NLAT * NLON, 5    # 0.5-degree cells per 2.5-degree cell, each way
# CPC grid: 720 x 360, lon 0.25E eastward, lat 89.75S northward, 4-byte floats.
# Rows 65..324 are 57.25S..72.25N, the 0.5-degree centres inside the output grid.
CPC_NX, ROW0, NROW = 720, 65, NLAT * SUB
NSUB = NROW * CPC_NX
# The file holds the rain field, then the gauge-count field, 360 rows each. One Range
# request runs from the band's first rain row to its last gauge row (1.8 MB a day).
RANGE = (ROW0 * CPC_NX * 4, (360 + ROW0 + NROW) * CPC_NX * 4 - 1)
# For each output cell, its 0.5-degree cells (output lon -180+2.5c is CPC column 360+5c).
CELL_SUBS = [[(r * SUB + dr) * CPC_NX + (360 + c * SUB + dc) % CPC_NX for dr in range(SUB) for dc in range(SUB)]
             for r in range(NLAT) for c in range(NLON)]
# (label, lat_s, lat_n, lon_w, lon_e) for the printed season check only.
CHECKS = (("southern Africa (dry season)", -35, -15, 15, 40),
          ("Sahel (late rains)", 10, 18, -17, 30),
          ("Horn of Africa (short rains start)", -5, 12, 35, 51),
          ("India (monsoon retreat)", 8, 30, 70, 88),
          ("Indonesia", -10, 5, 95, 141),
          ("eastern Australia", -38, -15, 140, 154),
          ("US Corn Belt", 37, 45, -98, -82),
          ("southern Brazil / Argentina", -38, -22, -65, -45))


# --- observations ---------------------------------------------------------

def _listed_days(year: int) -> set[date]:
    html = http_get(f"{CPC_RT}/{year}/", timeout=60, headers=UA, retries=3).text
    return {date(int(s[:4]), int(s[4:6]), int(s[6:])) for s in re.findall(r"0\.50deg\.lnx\.(\d{8})\.RT", html)}


def _cpc_day(d: date) -> tuple[array.array, array.array]:
    """One day, 0.5-degree, rows 57.25S..72.25N: (rain in mm, negative = sea; gauge reports per box)."""
    url = f"{CPC_RT}/{d.year}/" + CPC_FILE.format(d=d.strftime("%Y%m%d"))
    r = http_get(url, timeout=90, headers={**UA, "Range": "bytes=%d-%d" % RANGE}, retries=3)
    b = r.content
    if r.status_code == 200 and len(b) == CPC_NX * 360 * 4 * 2:  # server ignored the Range
        b = b[RANGE[0]:RANGE[1] + 1]
    if len(b) != RANGE[1] - RANGE[0] + 1:
        raise RuntimeError(f"{url}: {len(b)} bytes, expected {RANGE[1] - RANGE[0] + 1}")
    rain, gauges = array.array("f"), array.array("f")
    rain.frombytes(b[:NSUB * 4])
    gauges.frombytes(b[-NSUB * 4:])
    if sys.byteorder != "little":
        rain.byteswap()
        gauges.byteswap()
    return array.array("f", (v / 10 if v >= 0 else -1.0 for v in rain)), gauges  # CPC stores 0.1 mm


# --- normal ---------------------------------------------------------------

def _dap_arrays(buf: bytes) -> list[array.array]:
    """A DAP2 binary reply -> its arrays in order (Grid array, then its maps). XDR, big-endian."""
    head, sep, body = buf.partition(b"\nData:\n")
    types = re.findall(rb"(Float32|Float64)\s+\w+\[", head)
    if not sep or not types:
        raise RuntimeError("DAP2 reply has no data section")
    out, pos = [], 0
    for t in types:
        n = struct.unpack(">I", body[pos:pos + 4])[0]
        code, size = ("f", 4) if t == b"Float32" else ("d", 8)
        a = array.array(code)
        a.frombytes(body[pos + 8:pos + 8 + n * size])
        if sys.byteorder == "little":
            a.byteswap()
        out.append(a)
        pos += 8 + n * size
    if pos != len(body):
        raise RuntimeError("DAP2 reply did not parse to its end")
    return out


def _psl_normal(days: list[date]) -> dict:
    """{day: 1991-2020 daily mean in mm on the 0.5-degree rows of _cpc_day}, from PSL's LTM."""
    dds = http_get(f"{PSL_LTM}.dds", timeout=60, headers=UA, retries=2).text
    m = re.search(r"Float32 precip\[time = (\d+)\]\[lat = 360\]\[lon = 720\]", dds)
    if not m or m.group(1) not in ("365", "366"):
        raise RuntimeError("PSL daily LTM shape changed")
    ref = 2001 if m.group(1) == "365" else 2000  # a no-leap or a leap day axis
    idx = [date(ref, d.month, 28 if (d.month, d.day, ref) == (2, 29, 2001) else d.day).timetuple().tm_yday - 1
           for d in days]
    # PSL rows run north to south (89.75N first): 72.25N..57.25S is rows 35..294.
    p0, p1 = int(round((89.75 - 72.25) / 0.5)), int(round((89.75 + 57.25) / 0.5))
    runs = []
    for i in idx:  # consecutive calendar days, in pieces of at most ten
        if runs and i == runs[-1][1] + 1 and runs[-1][1] - runs[-1][0] < 9:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    fields = []
    for a, b in runs:
        pr, _t, lat, lon = _dap_arrays(http_get(
            f"{PSL_LTM}.dods?precip[{a}:1:{b}][{p0}:1:{p1}][0:1:719]",
            timeout=180, headers=UA, retries=3).content)
        if abs(lat[0] - 72.25) > 1e-3 or abs(lat[-1] + 57.25) > 1e-3 or abs(lon[0] - 0.25) > 1e-3:
            raise RuntimeError(f"PSL LTM grid is {lat[0]}..{lat[-1]} x {lon[0]}..")
        for k in range(b - a + 1):
            day = pr[k * NSUB:(k + 1) * NSUB]
            f = array.array("f")  # flip rows to the CPC order (south first)
            for r in range(NROW - 1, -1, -1):
                f.extend(day[r * CPC_NX:(r + 1) * CPC_NX])
            fields.append(array.array("f", (v if 0 <= v < 1e5 else -1.0 for v in f)))
    if len(fields) != len(days):
        raise RuntimeError(f"PSL LTM gave {len(fields)} days for {len(days)}")
    return dict(zip(days, fields))


# --- grid -----------------------------------------------------------------

def _cell_day(day: tuple[array.array, array.array], norm: array.array) -> list:
    """One day: per output cell, (observed mm, normal mm, gauge reports) over 0.5-degree cells
    valid in both the observations and the normal."""
    obs, gauges = day
    out = []
    for subs in CELL_SUBS:
        o = n = g = 0.0
        k = 0
        for s in subs:
            ov, nv = obs[s], norm[s]
            if ov >= 0 and nv >= 0:
                o += ov
                n += nv
                g += max(0.0, gauges[s])
                k += 1
        out.append((o / k, n / k, g) if k else None)
    return out


def _windows(per: dict, days: list[date], wdays: list[date]):
    """Per cell over the window and the week, after the gauge mask and the spike filter:
    (observed, normal) mm/day or None."""
    need30, need7 = NEED_SHARE * len(days), NEED_SHARE * len(wdays)
    c30, c7, dropped, ungauged = [], [], [], 0
    for k in range(NCELL):
        got = [(d, per[d][k]) for d in days if per[d][k] is not None]
        if got and len(got) >= need30 and sum(x[2] for _, x in got) / len(got) < MIN_GAUGES:
            ungauged += 1
            got = []
        if len(got) < need30:
            c30.append(None)
            c7.append(None)
            continue
        nbar = sum(x[1] for _, x in got) / len(got)
        keep = []
        for d, x in got:
            if x[0] > SPIKE_MM and x[0] > SPIKE_X * nbar:
                dropped.append((round(x[0]), d.isoformat(), LAT0 + STEP * (k // NLON), LON0 + STEP * (k % NLON)))
            else:
                keep.append((d, x))
        for xs, need, out in (([x for _, x in keep], need30, c30),
                              ([x for d, x in keep if d in wdays], need7, c7)):
            out.append((sum(x[0] for x in xs) / len(xs), sum(x[1] for x in xs) / len(xs))
                       if xs and len(xs) >= need else None)
    return c30, c7, dropped, ungauged


def _pct(cells) -> list:
    return [None if x is None or x[1] < ARID_MM_DAY else int(round(100 * (x[0] - x[1]) / x[1])) for x in cells]


def build() -> tuple[dict, dict]:
    today = date.today()
    try:
        listed = _listed_days(today.year)
    except RuntimeError:  # early January, before CPC opens the new year's folder
        listed = set()
    if not any(d <= today for d in listed):
        listed |= _listed_days(today.year - 1)
    end = max(d for d in listed if d <= today)
    if (today - end).days > 10:
        raise RuntimeError(f"newest CPC day {end} is {(today - end).days} days old -- frozen feed")
    start = end - timedelta(days=DAYS - 1)
    if start.year != end.year:
        listed |= _listed_days(start.year)
    days = [start + timedelta(days=i) for i in range(DAYS) if start + timedelta(days=i) in listed]
    wstart = end - timedelta(days=WEEK - 1)
    wdays = [d for d in days if d >= wstart]
    if len(days) < NEED_SHARE * DAYS or len(wdays) < NEED_SHARE * WEEK:
        raise RuntimeError(f"CPC has {len(days)}/{DAYS} days and {len(wdays)}/{WEEK} this week")

    norm = _psl_normal(days)  # no fallback base: see the module docstring
    per = {d: _cell_day(_cpc_day(d), norm[d]) for d in days}
    del norm
    c30, c7, dropped, ungauged = _windows(per, days, wdays)

    anom, wanom = _pct(c30), _pct(c7)
    wmm = [None if x is None else int(round(x[0] * len(wdays))) for x in c7]
    missing = [(start + timedelta(days=i)).isoformat() for i in range(DAYS)
               if start + timedelta(days=i) not in listed]
    payload = {
        "source_url": f"{CPC_RT}/", "normal_source_url": PSL_PAGE,
        "product": "NOAA CPC Global Unified Gauge-Based Analysis of Daily Precipitation, real-time, 0.5 degree",
        "grid": {"lat0": LAT0, "lon0": LON0, "step_deg": STEP, "nlat": NLAT, "nlon": NLON,
                 "encoding": "row-major from the southern edge, integer percent change, null over sea, arid or missing"},
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": len(days)},
        "base": "1991-2020",
        "base_source": "NOAA PSL daily long-term mean 1991-2020 of the same CPC product",
        "anom": anom,
        "n_valid": sum(v is not None for v in anom),
        "week": {"start": wstart.isoformat(), "end": end.isoformat(), "days": len(wdays),
                 "anom": wanom, "n_valid": sum(v is not None for v in wanom),
                 "mm": wmm,
                 "mm_encoding": "row-major from the southern edge, observed rain in whole millimetres "
                                "over the week, null over sea or missing"},
        "min_gauges_per_day": MIN_GAUGES,
        "cells_without_gauges": ungauged,
        "spike_cell_days_dropped": len(dropped),
        "notes": [
            f"Observed rain from rain gauges, not a model or a forecast. The last {len(days)} days end on "
            f"{end.isoformat()}, the newest day NOAA CPC had posted when this ran; CPC's gauge analysis runs "
            "one to two days behind, and it re-issues its newest days as late reports arrive.",
            "Colour is the percent change against the 1991-2020 average for the same calendar days. The week "
            "layer does the same for the last seven days and also gives the rain that fell, in millimetres.",
            f"Each 2.5-degree cell is the average of the 0.5-degree gauge analysis inside it, over land only. "
            f"Land that averages under {ARID_MM_DAY} mm of rain a day at this time of year is left blank on the "
            "percent layers, where a small shower reads as a huge percent. Sea cells are blank.",
            f"CPC's daily real-time analysis has bad reports that show up as hundreds of mm in a day where "
            f"little fell. A cell's day is dropped when it is over {SPIKE_MM} mm and over {SPIKE_X} times the "
            f"cell's normal daily rain ({len(dropped)} cell-days this run). A real extreme storm can be "
            "dropped too, so a cell hit by one may read drier than it was.",
            "Seven days of rain are patchy: one storm can double a week. Read the 30-day layer for the pattern "
            "and the week for what is happening now.",
            f"Land with fewer than {MIN_GAUGES:g} gauge report a day inside the cell is left blank "
            f"({ungauged} cells this run), as in parts of Africa, Indonesia and the Amazon. Away from gauges "
            "CPC's daily analysis falls towards no rain, which would read as a drought that is not there.",
        ] + ([f"CPC had not posted {', '.join(missing)}; the window and its normal skip those days."]
             if missing else []),
    }
    return payload, {"c30": c30, "c7": c7, "dropped": dropped}


def _region(cells, pct, s, n, w, e):
    ks = [r * NLON + c for r in range(NLAT) for c in range(NLON)
          if s <= LAT0 + STEP * r <= n and w <= LON0 + STEP * c <= e and cells[r * NLON + c] is not None]
    vals = sorted(pct[k] for k in ks if pct[k] is not None)
    med = vals[len(vals) // 2] if vals else None
    obs = sum(cells[k][0] for k in ks) / len(ks) if ks else float("nan")
    nrm = sum(cells[k][1] for k in ks) / len(ks) if ks else float("nan")
    return len(ks), len(vals), med, obs, nrm


def main() -> int:
    payload, diag = build()
    path = write_json("rain_anomaly.json", payload,
                      source="NOAA CPC Global Unified Gauge-Based daily precipitation (CPC FTP); "
                             "1991-2020 daily normal of the same product (NOAA PSL THREDDS)",
                      notes=("Observed rain on land over the last 30 and 7 days as a percent change against "
                             "1991-2020 for the same calendar days, on the El Nino Ocean map's 2.5-degree land "
                             "grid (same grid as sst_composites.json rain_grid). Gauge analysis, not a model."),
                      status="ok")
    # Three 7,488-cell grids at write_json's indent=2 are several hundred KB. Same
    # envelope, no whitespace (as build_sst_composites does).
    path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=False, separators=(",", ":")))
    w, wk = payload["window"], payload["week"]
    print(f"[OK] CPC rain {w['start']}..{w['end']} ({w['days']} days), week {wk['start']}..{wk['end']} "
          f"| valid cells 30d {payload['n_valid']}, week {wk['n_valid']} | {payload['cells_without_gauges']} "
          f"cells without gauges | {len(diag['dropped'])} spike cell-days dropped | {path.stat().st_size / 1e3:.0f} KB")
    vals = sorted(v for v in payload["anom"] if v is not None)
    print(f"[check] all valid cells, 30d: median {vals[len(vals) // 2]:+d}%, "
          f"IQR {vals[len(vals) // 4]:+d}..{vals[3 * len(vals) // 4]:+d}% (a large offset would mean a biased base)")
    print("[check] largest dropped cell-days (mm, day, lat, lon): "
          + ", ".join(map(str, sorted(diag["dropped"], reverse=True)[:8])))
    for label, s, n, we, e in CHECKS:
        for key, pct in (("30d", payload["anom"]), ("7d", wk["anom"])):
            k, kv, med, obs, nrm = _region(diag["c" + key[:-1]], pct, s, n, we, e)
            print(f"[check] {label} {key}: {k} land cells, {kv} not arid, median {med if med is None else f'{med:+d}%'}"
                  f", observed {obs:.1f} vs normal {nrm:.1f} mm/day")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
