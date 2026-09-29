#!/usr/bin/env python3
"""refresh_rain_anomaly.py -- observed rain on land, last 30, 14 and 7 days, against 1991-2020.

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
    NOAA PSL publishes as precip.day.ltm.1991-2020.nc (365 calendar days, no
    29 February; 29 February reads 28 February), taken for the same calendar
    days. It is read from NORMAL_CACHE first: a committed, gzipped JSON built
    once from PSL's file (PSL_FTP, 568 MB, NetCDF-3) by
        python3 scripts/refresh_rain_anomaly.py --build-normal precip.day.ltm.1991-2020.nc
    (needs numpy and scipy, which CI does not install and does not need). Per
    calendar day and per 2.5-degree cell it holds the mean of the cell's
    0.5-degree cells that have a normal (0.01 mm a day, integers), plus a
    25-bit mask of which 0.5-degree cells those are, and the source file's
    sha256. The normal never changes, so CI reads no PSL server at all. Only
    if the cache is missing or unreadable is PSL's THREDDS OPeNDAP read
    (PSL_LTM, DAP2 binary in ten-day pieces) as before. PSL THREDDS answered
    503 and downloads.psl.noaa.gov 502 from 27 September 2026; PSL's FTP
    (ftp.cdc.noaa.gov) still served the file, and the cache was built from it
    on 28 September 2026. There is deliberately no fallback base: CPC's
    PREC/L monthly analysis was tried and rejected. For the same month
    (August 2026) the real-time daily analysis sat 12% below PREC/L at the
    median of well-gauged cells and 74% below where no gauge reports, so a
    PREC/L base paints droughts that are not there. If the cache and PSL both
    fail the step fails and run_all keeps the last good file.
  * Each day, each 2.5-degree cell is the mean of its 0.5-degree land cells
    that have a normal (up to 25: a true cell mean, not a centre sample). The
    observed mean is taken over exactly those 0.5-degree cells; a cell-day where
    one of them has no observation is left out, so the observation and the
    normal always average the same ground. (CPC's real-time land mask is the
    normal's minus a few remote islands -- South Georgia, Marion, Easter,
    Pitcairn, some days Hawaii -- so this drops almost nothing.)
  * Gauge mask. CPC's file also carries the number of reporting gauges per
    0.5-degree box. Away from reporting gauges the real-time analysis decays
    towards no rain (Indonesia, the Congo basin, the Amazon interior), so a
    cell needs on average MIN_GAUGES gauge reports a day inside it over the
    window, or it is null. MIN_GAUGES was 1 until 28 September 2026; it is 3
    because the dry pull reaches past one gauge. Over 29 August..27 September
    2026, by mean reports a day per cell, the median cell read -23% (area-
    weighted observed/normal 0.85) under 1 a day, -18% (0.97) at 1-3, -12%
    (1.10) at 3-10 and about 0% at 10 or more.
  * CHIRPS fill. Cells left without a reading (gauge mask, missing days) get
    CHIRPS v3 against its own 1991-2020 normal in a separate "fill" block (see
    chirps_rain_fill.py); arid cells have a CPC reading and are not filled. If
    CHC fails, the CPC layers are written without "fill"; never the reverse.
  * Spike filter. The real-time analysis carries bad reports that the
    interpolation spreads into bumps of several hundred mm a day (seen on
    2026-09-18: 900 mm over Arctic Siberia; and near-daily 600-850 mm over
    Sumatra). A cell-day more than SPIKE_X times the cell's normal daily rain
    over the window and above SPIKE_MM is dropped, for the observations and
    the normal alike. A cell needs NEED_SHARE of the window's days to show.
  * Cells whose normal over the window is under 0.3 mm a day (the same arid
    threshold as the composites) are null on the percent layers, as are sea
    cells.
  * 14 days (added 29 September 2026; owner: "last 7, 14, 30 thats the recent
    ones that should auto update"): "d14", the 14 days ending on the same newest
    day, beside "week". The same rules as the week: the 30-day gauge test and
    spike filter, then the 14 days' own gauge test (MIN_GAUGES reports a day
    over the days kept) and NEED_SHARE of them; SPI from the d14 fit; CHIRPS
    fills its blank cells with three pentads (fill.d14, chirps_rain_fill.py).
  * SPI (added 28 September 2026). A percent does not read alike on 7 and 30
    days: a week varies far more than a month, so the week looked calmer than
    the 30-day layer. "spi" (30 days) and "week.spi" give each cell's
    Standardized Precipitation Index (McKee et al. 1993; WMO-No. 1090), one
    scale for every window: the window's mean rain per day (the unrounded value
    behind the percent, after the gauge mask and spike filter) placed on the
    cell's 1991-2020 mixed-gamma fit for the same window at the same time of
    year (scripts/build_cpc_spi_params.py -> data/ref/cpc_spi_params_1991_2020
    .json.gz, fitted to PSL's yearly files of this CPC product, aggregated as
    here), read off a standard normal by scripts/spi.py (pure Python). The fit
    row is the pentad end day nearest the window's last day (365-day calendar);
    the nominal window length (30 or 7 days) only decides whether the total is
    in the dry class (under 0.5 mm). Stored as SPI x 100, integers clamped to
    -300..300 (-300 = -3 or below). Null where the cell has no reading (sea,
    missing, gauge mask), or no fit: a 1991-2020 normal under 0.1 mm a day over
    the window (build_cpc_spi_params.SPI_ARID_MM_DAY), fewer than 2/3 of the
    1991-2020 windows over 0.5 mm, or a footprint CPC's analysis only covers
    from 2007 (95 coastal and island cells). Not tied to the percent's arid
    blank (normal under ARID_MM_DAY, 0.5 mm a day) since 28 September 2026:
    inland Australia's wet August 2026 was blank on every layer. The percent
    keeps that blank, where one shower reads as hundreds of percent; SPI does
    not have that problem, it ranks the window against the cell's own years. SPI measures how
    unusual each window is on its own: a dry week where dry weeks are common
    reads only mildly dry even inside a severe 30-day drought. If the parameter
    file cannot be read the percent layers are written without SPI.

Pure Python (requests only), because the GitHub Actions job installs nothing
else.
"""
from __future__ import annotations

import array
import gzip
import json
import re
import struct
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402
import chirps_rain_fill  # noqa: E402
import spi as SPI  # noqa: E402

CPC_RT = "https://ftp.cpc.ncep.noaa.gov/precip/CPC_UNI_PRCP/GAUGE_GLB/RT"
CPC_FILE = "PRCP_CU_GAUGE_V1.0GLB_0.50deg.lnx.{d}.RT"
PSL_LTM = "https://psl.noaa.gov/thredds/dodsC/Datasets/cpc_global_precip/precip.day.ltm.1991-2020.nc"
PSL_PAGE = "https://psl.noaa.gov/data/gridded/data.cpc.globalprecip.html"
PSL_FTP = "ftp://ftp.cdc.noaa.gov/Datasets/cpc_global_precip/precip.day.ltm.1991-2020.nc"
NORMAL_CACHE = Path(__file__).resolve().parent.parent / "data" / "ref" / "cpc_rain_normal_1991_2020.json.gz"
UA = {"User-Agent": "FoodShield-AI data refresh (github.com/feddevreeken-ctrl/foodshield-ai)"}

DAYS, WEEK, D14 = 30, 7, 14
NEED_SHARE = 0.85          # share of the window's days a cell needs after the spike filter
# 0.5, not the composites' 0.3: over 7 or 30 days a normal of a few tenths of a mm a day is one shower, and the
# dry-season percents it gives (+183% over southern Africa in September 2026) read as a wet spell that is not there.
ARID_MM_DAY = 0.5
SPIKE_X, SPIKE_MM = 20, 50
MIN_GAUGES = 3.0          # mean gauge reports a day inside a 2.5-degree cell (1.0 before 2026-09-28: see the docstring)
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
# The last build()'s per-day cell values, CPC listing, newest day, payload and CHIRPS grids, kept in this process so
# refresh_rain_weeks.py (the next run_all step) builds the weekly archive without downloading the days again.
LAST: dict = {}


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


def _ltm_index(d: date, n: int) -> int:
    """Index of d's calendar day on a 365-day (no 29 February: it reads 28 February) or 366-day axis."""
    ref = 2001 if n == 365 else 2000
    return date(ref, d.month, 28 if (d.month, d.day, ref) == (2, 29, 2001) else d.day).timetuple().tm_yday - 1


def _cells_from_field(f: array.array) -> tuple[list[int], list]:
    """A 0.5-degree normal on the rows of _cpc_day -> (per output cell, a 25-bit mask of its
    0.5-degree cells that have a normal; per output cell, their mean in mm or None)."""
    masks, means = [], []
    for subs in CELL_SUBS:
        m, t, k = 0, 0.0, 0
        for i, s in enumerate(subs):
            if f[s] >= 0:
                m |= 1 << i
                t += f[s]
                k += 1
        masks.append(m)
        means.append(t / k if k else None)
    return masks, means


def _cache_normal(days: list[date]) -> dict:
    """{day: (masks, means)} as _cells_from_field, from the committed NORMAL_CACHE."""
    with gzip.open(NORMAL_CACHE, "rt", encoding="utf-8") as fh:
        c = json.load(fh)
    g = c["grid"]
    if ((g["lat0"], g["lon0"], g["step_deg"], g["nlat"], g["nlon"]) != (LAT0, LON0, STEP, NLAT, NLON)
            or len(c["days"]) != 365 or not all(len(row) == len(c["cells"]) for row in c["days"])
            or len(c["mask"]) != len(c["cells"])):
        raise RuntimeError(f"{NORMAL_CACHE.name}: grid or shape does not match this script")
    masks = [0] * NCELL
    for k, m in zip(c["cells"], c["mask"]):
        masks[k] = m
    out = {}
    for d in days:
        means = [None] * NCELL
        for k, v in zip(c["cells"], c["days"][_ltm_index(d, 365)]):
            means[k] = v / 100
        out[d] = (masks, means)
    return out


def _normal(days: list[date]) -> tuple[dict, str]:
    """The committed cache first, PSL THREDDS second; never another product (see the docstring)."""
    try:
        return _cache_normal(days), f"committed cache data/ref/{NORMAL_CACHE.name}, built from {PSL_FTP}"
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
        print(f"[WARN] {NORMAL_CACHE.name} unusable ({e}); reading PSL THREDDS")
    return {d: _cells_from_field(f) for d, f in _psl_normal(days).items()}, f"PSL THREDDS {PSL_LTM}"


def _psl_normal(days: list[date]) -> dict:
    """{day: 1991-2020 daily mean in mm on the 0.5-degree rows of _cpc_day}, from PSL's LTM."""
    dds = http_get(f"{PSL_LTM}.dds", timeout=60, headers=UA, retries=2).text
    m = re.search(r"Float32 precip\[time = (\d+)\]\[lat = 360\]\[lon = 720\]", dds)
    if not m or m.group(1) not in ("365", "366"):
        raise RuntimeError("PSL daily LTM shape changed")
    idx = [_ltm_index(d, int(m.group(1))) for d in days]
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

def _cell_day(day: tuple[array.array, array.array], norm: tuple[list[int], list]) -> list:
    """One day: per output cell, (observed mm, normal mm, gauge reports) over the 0.5-degree cells
    that have a normal; None where there are none or one of them has no observation that day."""
    obs, gauges = day
    masks, means = norm
    out = []
    for subs, m, nv in zip(CELL_SUBS, masks, means):
        o = g = 0.0
        k = 0
        for i, s in enumerate(subs):
            if m >> i & 1:
                if obs[s] < 0:
                    k = 0
                    break
                o += obs[s]
                g += max(0.0, gauges[s])
                k += 1
        out.append((o / k, nv, g) if k and nv is not None else None)
    return out


def _windows(per: dict, days: list[date], wdays: list[date]):
    """Per cell over the window and the week, after the gauge mask and the spike filter:
    (observed, normal) mm/day or None."""
    need30, need7 = NEED_SHARE * len(days), NEED_SHARE * len(wdays)
    c30, c7, dropped, ungauged, gauges = [], [], [], 0, []
    for k in range(NCELL):
        got = [(d, per[d][k]) for d in days if per[d][k] is not None]
        gauges.append(sum(x[2] for _, x in got) / len(got) if got else None)
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
        wk = [x for d, x in keep if d in wdays]
        # The week needs its own gauge test: a cell whose gauges stopped reporting this week would otherwise keep
        # a CPC week value that decays towards no rain, instead of the CHIRPS fill (audit, 2026-09-28).
        if wk and sum(x[2] for x in wk) / len(wk) < MIN_GAUGES:
            wk = []
        for xs, need, out in (([x for _, x in keep], need30, c30), (wk, need7, c7)):
            out.append((sum(x[0] for x in xs) / len(xs), sum(x[1] for x in xs) / len(xs))
                       if xs and len(xs) >= need else None)
    return c30, c7, dropped, ungauged, gauges


def _pct(cells) -> list:
    return [None if x is None or x[1] < ARID_MM_DAY else int(round(100 * (x[0] - x[1]) / x[1])) for x in cells]


def _spi(cells: list, pct: list, kind: str, end: date, days: int) -> tuple[list, dict]:
    """SPI x 100 per cell of one CPC layer (kind 'd30', 'd14' or 'd7'), from the unrounded mean rain per day; null where
    the cell has no reading or no fit for this window and time of year (see the docstring). pct is not used: SPI
    is not blanked with the percent's arid mask."""
    fits, m = SPI.cpc_fits((LAT0, LON0, STEP, NLAT, NLON), kind, SPI.cpc_row(end))
    return [None if x is None or k not in fits
            else SPI.to_x100(SPI.spi_cpc(x[0], fits[k], days, m["zero_mm"], m["clip"]))
            for k, x in enumerate(cells)], m


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
    dstart = end - timedelta(days=D14 - 1)
    ddays = [d for d in days if d >= dstart]
    if len(days) < NEED_SHARE * DAYS or len(wdays) < NEED_SHARE * WEEK or len(ddays) < NEED_SHARE * D14:
        raise RuntimeError(f"CPC has {len(days)}/{DAYS} days, {len(ddays)}/{D14} in 14 days and {len(wdays)}/{WEEK} "
                           "this week")

    norm, normal_from = _normal(days)  # no fallback base: see the module docstring
    per = {d: _cell_day(_cpc_day(d), norm[d]) for d in days}
    del norm
    c30, c7, dropped, ungauged, gauges = _windows(per, days, wdays)
    _, c14, _, _, _ = _windows(per, days, ddays)  # the week's rules on 14 days: same 30-day test, own gauge test

    anom, wanom, danom = _pct(c30), _pct(c7), _pct(c14)
    tot = lambda cells, i, n: [None if x is None else int(round(x[i] * n)) for x in cells]
    wmm = tot(c7, 0, len(wdays))
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
        "base_read_from": normal_from,
        "anom": anom,
        "n_valid": sum(v is not None for v in anom),
        # The baseline in millimetres beside what fell, so a reader sees the excess or shortfall itself.
        "mm": tot(c30, 0, len(days)), "norm_mm": tot(c30, 1, len(days)),
        "week": {"start": wstart.isoformat(), "end": end.isoformat(), "days": len(wdays),
                 "anom": wanom, "n_valid": sum(v is not None for v in wanom),
                 "mm": wmm, "norm_mm": tot(c7, 1, len(wdays)),
                 "mm_encoding": "row-major from the southern edge, whole millimetres over the window: mm what "
                                "fell, norm_mm the 1991-2020 normal for the same days; null over sea or missing"},
        "d14": {"start": dstart.isoformat(), "end": end.isoformat(), "days": len(ddays),
                "anom": danom, "n_valid": sum(v is not None for v in danom),
                "mm": tot(c14, 0, len(ddays)), "norm_mm": tot(c14, 1, len(ddays))},
        "min_gauges_per_day": MIN_GAUGES,
        "cells_without_gauges": ungauged,
        "spike_cell_days_dropped": len(dropped),
        "notes": [
            f"Observed rain from rain gauges, not a model or a forecast. The last {len(days)} days end on "
            f"{end.isoformat()}, the newest day NOAA CPC had posted when this ran; CPC's gauge analysis runs "
            "one to two days behind, and it re-issues its newest days as late reports arrive.",
            "Colour is the percent change against the 1991-2020 average for the same calendar days. The 14-day "
            "and week layers do the same for the last 14 and 7 days, and give the rain that fell in millimetres.",
            f"Each 2.5-degree cell is the average of the 0.5-degree gauge analysis inside it, over land only. "
            f"Land that averages under {ARID_MM_DAY} mm of rain a day at this time of year is left blank on the "
            "percent layers, where a small shower reads as a huge percent. Sea cells are blank.",
            f"CPC's daily real-time analysis has bad reports that show up as hundreds of mm in a day where "
            f"little fell. A cell's day is dropped when it is over {SPIKE_MM} mm and over {SPIKE_X} times the "
            f"cell's normal daily rain ({len(dropped)} cell-days this run). A real extreme storm can be "
            "dropped too, so a cell hit by one may read drier than it was.",
            "Seven days of rain are patchy: one storm can double a week. Read the 30-day layer for the pattern "
            "and the week for what is happening now.",
            f"Land with fewer than {MIN_GAUGES:g} gauge reports a day inside the cell gets no gauge reading "
            f"({ungauged} cells this run), as in much of Africa, Indonesia and the Amazon. Away from gauges "
            "CPC's daily analysis falls towards no rain, which would read as a drought that is not there: in "
            "the 30 days to 27 September 2026 cells with under one report a day read a median -23%, one to "
            "three -18%, three to ten -12%, ten or more about 0%.",
        ] + ([f"CPC had not posted {', '.join(missing)}; the window and its normal skip those days."]
             if missing else []),
    }
    try:  # an addition: the percent layers stand without it
        s30, m = _spi(c30, anom, "d30", end, DAYS)
        s7, _ = _spi(c7, wanom, "d7", end, WEEK)
        payload["spi"], payload["week"]["spi"] = s30, s7
        payload["spi_info"] = {
            "index": "Standardized Precipitation Index (McKee, Doesken and Kleist 1993; WMO-No. 1090, 2012)",
            "params": f"data/ref/{SPI.CPC_PARAMS.name}",
            "fit": ("mixed gamma (probability of a total under zero_mm, then gamma) per cell, fitted to the 1991-2020 "
                    "CPC Unified daily gauge analysis (NOAA PSL yearly files) aggregated as here, for the 30 and the 7 "
                    f"days ending on day of year {m['end_day']} (365-day calendar, the pentad end nearest "
                    f"{end.isoformat()}) and on the days up to {m['pool_end_days']} either side"),
            "fit_end_day": m["end_day"], "zero_mm": m["zero_mm"],
            "encoding": ("spi (30 days) and week.spi: SPI x 100 as integers, row-major from the southern edge as anom, "
                         "clamped to -300..300 (-300 = -3 or below, 300 = +3 or above); null where mm is null (no "
                         "reading) or the cell has no 1991-2020 fit for this window and time of year; set on arid "
                         "cells too, where anom is null"),
            "n_valid": sum(v is not None for v in s30), "week_n_valid": sum(v is not None for v in s7),
        }
        payload["notes"].append(
            "SPI, the Standardized Precipitation Index, puts the 30 days and the week on one scale: how unusual this "
            "much rain is at this place for the same window at the same time of year in 1991-2020. -1, -1.5 and -2 "
            "are moderately, severely and extremely dry (about 1 year in 6, 15 and 44 that dry or drier); +1, +1.5 "
            "and +2 the wet mirror. It judges each window on its own: where a week without rain is common, a dry "
            "week reads only mildly dry, even inside a severe 30-day drought.")
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as e:
        print(f"[WARN] SPI left out: {type(e).__name__}: {e}")
    if "spi_info" in payload:
        try:  # the d14 fit, on its own: the 30-day and week SPI stand without it
            payload["d14"]["spi"], _ = _spi(c14, danom, "d14", end, D14)
            payload["spi_info"]["d14_n_valid"] = sum(v is not None for v in payload["d14"]["spi"])
            payload["spi_info"]["encoding"] += "; d14.spi the same over the 14 days (d14 fit)"
        except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as e:
            print(f"[WARN] 14-day SPI left out: {type(e).__name__}: {e}")
    cf, fdiag = chirps_rain_fill, None
    if (cf.LAT0, cf.LON0, cf.STEP, cf.NLAT, cf.NLON) != (LAT0, LON0, STEP, NLAT, NLON):
        raise RuntimeError("chirps_rain_fill.py grid differs from this script's")
    try:  # a fill only: CPC is written without it, never the reverse
        payload["fill"], fdiag = chirps_rain_fill.fill([x is None for x in c30], [x is None for x in c7], ARID_MM_DAY,
                                                       [x is None for x in c14])
    except Exception as e:  # noqa: BLE001 -- any CHC failure leaves the CPC layers as they are
        print(f"[WARN] CHIRPS fill left out: {e}")
    LAST.clear()
    LAST.update(per=per, listed=listed, end=end, payload=payload, chirps=fdiag)
    return payload, {"c30": c30, "c14": c14, "c7": c7, "dropped": dropped, "gauges": gauges, "chirps": fdiag}


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
                             "1991-2020 daily normal of the same product (NOAA PSL precip.day.ltm.1991-2020.nc, "
                             "read from the " + payload["base_read_from"].split(",")[0] + ")"
                             + ("; where CPC has too few gauges, CHIRPS v3.0 (CHC Early Estimates pentad totals) "
                                "against its own 1991-2020 pentad normal, in the fill block" if "fill" in payload else ""),
                      notes=("Observed rain on land over the last 30 and 7 days as a percent change against "
                             "1991-2020 for the same calendar days, on the El Nino Ocean map's 2.5-degree land "
                             "grid (same grid as sst_composites.json rain_grid). Gauge analysis, not a model."),
                      status="ok")
    # Three 7,488-cell grids at write_json's indent=2 are several hundred KB. Same
    # envelope, no whitespace (as build_sst_composites does).
    path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=False, separators=(",", ":")))
    w, wk = payload["window"], payload["week"]
    d14 = payload["d14"]
    print(f"[OK] CPC rain {w['start']}..{w['end']} ({w['days']} days), 14 days {d14['start']}..{d14['end']}, week "
          f"{wk['start']}..{wk['end']} | valid cells 30d {payload['n_valid']}, 14d {d14['n_valid']}, week "
          f"{wk['n_valid']} | {payload['cells_without_gauges']} "
          f"cells without gauges | {len(diag['dropped'])} spike cell-days dropped | {path.stat().st_size / 1e3:.0f} KB")
    vals = sorted(v for v in payload["anom"] if v is not None)
    print(f"[check] all valid cells, 30d: median {vals[len(vals) // 2]:+d}%, "
          f"IQR {vals[len(vals) // 4]:+d}..{vals[3 * len(vals) // 4]:+d}% (a large offset would mean a biased base)")
    print("[check] largest dropped cell-days (mm, day, lat, lon): "
          + ", ".join(map(str, sorted(diag["dropped"], reverse=True)[:8])))
    for key, blk in (("30d", payload), ("14d", payload["d14"]), ("7d", wk)):
        s = [v for v in blk.get("spi") or [] if v is not None]
        if s:
            share = lambda t: 100 * sum(abs(v) >= t for v in s) / len(s)  # noqa: E731
            print(f"[check] SPI {key} (CPC): {len(s)} cells of {sum(v is not None for v in blk['anom'])} with a "
                  f"percent, median {sorted(s)[len(s) // 2] / 100:+.2f}, |SPI| >= 1 in {share(100):.0f}%, >= 2 in "
                  f"{share(200):.0f}% (a normal year: 32% and 5%)")
    for label, s, n, we, e in CHECKS:
        for key, pct in (("30d", payload["anom"]), ("14d", payload["d14"]["anom"]), ("7d", wk["anom"])):
            k, kv, med, obs, nrm = _region(diag["c" + key[:-1]], pct, s, n, we, e)
            print(f"[check] {label} {key}: {k} land cells, {kv} not arid, median {med if med is None else f'{med:+d}%'}"
                  f", observed {obs:.1f} vs normal {nrm:.1f} mm/day")
    if diag["chirps"]:
        chirps_rain_fill.report(payload, diag, diag["chirps"], ARID_MM_DAY)
    return 0


def build_normal_cache(nc_path: str) -> int:
    """One-off, by hand: PSL's precip.day.ltm.1991-2020.nc (NetCDF-3, from PSL_FTP) -> NORMAL_CACHE.
    Imports numpy and scipy here only, so the CI collector stays requests-only."""
    import hashlib
    import numpy as np
    from scipy.io import netcdf_file

    raw = Path(nc_path).read_bytes()
    f = netcdf_file(nc_path, "r", mmap=False)
    lat, lon, pr = f.variables["lat"][:], f.variables["lon"][:], f.variables["precip"]
    climo = f.variables["time"].climo_period.decode()
    if pr.shape != (365, 360, 720) or abs(lat[0] - 89.75) > 1e-3 or abs(lon[0] - 0.25) > 1e-3 \
            or climo != "1991/01/01 - 2020/12/31":
        raise RuntimeError(f"unexpected LTM file: {pr.shape}, lat0 {lat[0]}, lon0 {lon[0]}, {climo}")
    # Rows north first: 72.25N..57.25S is rows 35..294; flip to the CPC order (south first).
    band = np.asarray(pr[:, 35:295, :], dtype=np.float64)[:, ::-1, :].reshape(365, NSUB)
    valid = (band >= 0) & (band < 1e5)
    # The land mask is the 0.5-degree cells with a normal on every day. In the 2021 file 3 July
    # also carries 4,463 sea cells averaged from one year (valid_yr_count 1), and four other days
    # one cell each: not land, left out.
    land = valid.all(0)
    odd = [int(i) for i in np.nonzero((valid != land).any(1))[0]]
    print(f"[build] {int(land.sum())} land 0.5-degree cells; day indexes with extra cells left out: {odd}")
    subs = np.array(CELL_SUBS)                      # NCELL x 25
    v = land[subs]
    cnt = v.sum(1)
    cells = np.nonzero(cnt)[0]
    masks = (v[cells] * (1 << np.arange(SUB * SUB))).sum(1)
    means = np.where(v[cells][None], band[:, subs[cells]], 0.0).sum(2) / cnt[cells]
    out = {
        "what": "1991-2020 daily long-term mean of NOAA CPC Global Unified Gauge-Based daily precipitation "
                "(0.5 degree), averaged to the 2.5-degree cells of refresh_rain_anomaly.py",
        "source_file": PSL_FTP, "source_page": PSL_PAGE,
        "source_bytes": len(raw), "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_history": f.history.decode(), "climo_period": climo,
        "built": date.today().isoformat(),
        "built_by": "python3 scripts/refresh_rain_anomaly.py --build-normal precip.day.ltm.1991-2020.nc",
        "grid": {"lat0": LAT0, "lon0": LON0, "step_deg": STEP, "nlat": NLAT, "nlon": NLON},
        "cells_encoding": "output cell index, row-major from the southern edge; only cells with a normal",
        "mask_encoding": "per cell, bit i set = its 0.5-degree cell i // 5 rows north and i % 5 columns east "
                         "of the south-west corner has a normal on every day of the year; the mean is over "
                         "those cells (3 July's extra one-year sea cells are left out)",
        "days_encoding": "365 calendar days, 1 January first, no 29 February; per day, per listed cell, "
                         "the mean normal in 0.01 mm per day (integers)",
        "cells": cells.tolist(), "mask": masks.tolist(),
        "days": np.rint(means * 100).astype(int).tolist(),
    }
    f.close()
    NORMAL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    NORMAL_CACHE.write_bytes(gzip.compress(json.dumps(out, separators=(",", ":")).encode(), 9, mtime=0))
    print(f"[OK] wrote {NORMAL_CACHE} ({len(cells)} cells, {NORMAL_CACHE.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--build-normal":
        raise SystemExit(build_normal_cache(sys.argv[2]))
    raise SystemExit(main())
