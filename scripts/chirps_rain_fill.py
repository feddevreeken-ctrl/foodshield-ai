#!/usr/bin/env python3
"""chirps_rain_fill.py -- CHIRPS v3 rain for the land cells CPC's gauge analysis leaves blank.

refresh_rain_anomaly.py blanks a 2.5-degree land cell when NOAA CPC's real-time gauge
analysis has too few gauge reports inside it (the Congo basin, the Amazon interior, much of
Africa and Indonesia). fill() gives those cells, and only those, a reading from a second
product, with that product's own normal over the same 1991-2020 base, in a separate block
(rain_anomaly.json "fill") so the page can mark every such cell as CHIRPS. Its own module
(not more code in refresh_rain_anomaly.py) because it fails on its own: if CHC is down, the
CPC layers are still written, without "fill".

Data:
  * Observed: CHIRPS v3.0 (Climate Hazards Center, UC Santa Barbara): infrared satellite
    estimates blended with station reports, 0.05 degree, 60S-60N, land only. CHIRPS is a
    pentad product (days 1-5, 6-10, 11-15, 16-20, 21-25 and 26-end of each month). Two days
    after a pentad ends, CHC's Early Estimates post its total over the newest pentad and over
    the newest six, summed from CHIRPS v3 final where CHC has released it (about the third
    week of the next month) and CHIRPS v3 preliminary for the newer pentads (EE_TIF:
    uncompressed float32 GeoTIFF, 7200 x 2400 from 180W 60N, NaN over sea, 66 MB). Read with
    the small TIFF reader below and HTTP Range requests. Which pentads were preliminary is
    read from the run log CHC posts beside them (EE_LOG). So the "30-day" layer is the last
    six pentads (28 to 31 days) and the "7-day" layer the last pentad (3 to 6 days), ending
    two to seven days before the run, where CPC's end one to two days before; both windows
    are stated in the output. The Early Estimates' own Anomaly and % of average use a
    1996-2025 base, so they are not used.
  * Normal: the 1991-2020 mean of CHIRPS v3.0 final pentads, same product, from CHC's
    pentad archive in BIL format (CHIRPS_BIL: 2,160 files of 2.4 MB). Built once, by hand,
    into NORMAL_CACHE by
        python3 scripts/chirps_rain_fill.py --build-normal <folder of v3p0chirps<YYYY><PP>.tar.gz>
    (needs numpy, which CI does not install). The BIL archive stores whole millimetres
    rounded DOWN (int16; checked against the float GeoTIFF of 26-31 August 2026: every
    pixel is floor() of it), so the build adds 0.5 mm to each pixel of 1 mm or more. Against
    the float file that leaves 2.5-degree cell means within -1.0%..+0.1% (5th..95th
    percentile of 1,292 cells over 0.5 mm a day, median -0.02%); rain under 1 mm in a pentad
    reads 0, so the normal runs slightly low in the driest cells. The cache holds, per
    pentad of the year and per cell, the 1991-2020 mean rain rate (the pentad's total over
    its days; February's last pentad has 3 or 4) in 0.001 mm a day, and each cell's count of
    0.05-degree land pixels. A window's normal is sum(rate x days) over its pentads.
  * A cell is the mean of its 0.05-degree land pixels (up to 2,500) -- the same pixels for
    the observation and the normal: CHIRPS's land mask is fixed (4,848,282 pixels in every
    archive file); a cell where the observation misses any of them is left out.
  * Cells north of 60N (outside CHIRPS) get no fill.
  * SPI (added 28 September 2026): fill.spi (the six pentads) and fill.week.spi (the one
    pentad), parallel to fill.cells, give the Standardized Precipitation Index on the same
    scale as the CPC cells' spi (see refresh_rain_anomaly.py). The observed total (the
    unrounded cell mean of the Early Estimates file) is placed on the cell's 1991-2020 gamma
    fit of CHIRPS v3.0 final pentads for the same window ending at the same pentad of the year
    (scripts/build_chirps_spi_params.py -> data/ref/chirps3_spi_params_1991_2020.json.gz: p6
    and p1 rows, pentad of year minus one) by scripts/spi.py, with that build's own rule: a
    total under 1 mm (the archive's whole-mm floor) gets the middle of the dry class, (dry
    years + 1) / 62. SPI x 100, integers clamped to -300..300. Null where the fill has no
    reading on that layer (CPC has it) or the window has no fit (a 1991-2020 normal under 0.1
    mm a day, fewer than 15 of 30 wet years, or a frozen CHIRPS record at tiny islands and along
    57.5-60N). Arid cells (percent blank, normal under 0.5 mm a day) keep their SPI since 28
    September 2026 (see refresh_rain_anomaly.py). Known small bias: the live
    Early Estimates are float files, the fit's archive floors each pixel, so the live SPI reads
    a little wet (95th percentile of cells +0.03 for a pentad, +0.04 for six; build's check).
    If the parameter file cannot be read the fill is written without SPI.

Pure Python (requests only) in CI; numpy only in --build-normal.
"""
from __future__ import annotations

import array
import gzip
import json
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get  # noqa: E402
import spi as SPI  # noqa: E402

EE = "https://data.chc.ucsb.edu/products/Early_Estimates/v3/Recent_Rainfall/observed"
EE_DIR = EE + "/moving_{n:02d}pentad/global/tifs/archive/"
EE_TIF = EE_DIR + "Total_{n:02d}PentAccum_{y}_p{p:02d}.tif"
EE_LOG = EE + "/logs/archive/eerr_cron_log_{y}_p{p:02d}.txt"
EE_PAGE = "https://www.chc.ucsb.edu/monitoring/early-estimates/info"
CHIRPS_PAGE = "https://www.chc.ucsb.edu/data/chirps3"
CHIRPS_BIL = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/pentads/global/bils/"
NORMAL_CACHE = Path(__file__).resolve().parent.parent / "data" / "ref" / "chirps3_rain_normal_1991_2020.json.gz"
UA = {"User-Agent": "FoodShield-AI data refresh (github.com/feddevreeken-ctrl/foodshield-ai)"}
PRODUCT = ("CHIRPS v3.0, Climate Hazards Center, UC Santa Barbara: infrared satellite estimates blended "
           "with station reports, 0.05 degree, pentads")

# Output grid: refresh_rain_anomaly.py's (checked there).
LAT0, LON0, STEP, NLAT, NLON = -56.25, -178.75, 2.5, 52, 144
NCELL = NLAT * NLON
PX, W, H = 50, 7200, 2400        # 0.05-degree pixels per 2.5-degree cell each way; CHIRPS 60N..60S, 180W..180E
NBAND = 47                       # output rows 0..46 (57.5S..60N) lie inside CHIRPS
NROWS = NBAND * PX               # image rows 0..2349 (60N..57.5S); the last 50 (57.5S..60S) are unused
ROW_BYTES = W * 4
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
MAX_AGE = 15                     # days from the newest pentad's end to today before the feed counts as frozen
BUDGET_S = 150                   # the fill gives up after this, so run_all's step timeout never costs the CPC layers
_deadline = [float("inf")]


def pentad_dates(y: int, p: int) -> tuple[date, date]:
    """Pentad p (1..72) of year y -> (first day, last day)."""
    m, k = divmod(p - 1, 6)
    start = date(y, m + 1, 1 + 5 * k)
    if k < 5:
        return start, start + timedelta(days=4)
    return start, date(y + (m == 11), (m + 1) % 12 + 1, 1) - timedelta(days=1)


def _back(y: int, p: int, n: int) -> list[tuple[int, int]]:
    """The n pentads ending with (y, p), oldest first."""
    out = [(y, p)]
    while len(out) < n:
        y, p = (y, p - 1) if p > 1 else (y - 1, 72)
        out.insert(0, (y, p))
    return out


# --- the observation: CHC Early Estimates totals, pure Python ---------------

def _get(url: str, a: int | None = None, b: int | None = None) -> bytes:
    left = _deadline[0] - time.monotonic()
    if left < 10:
        raise RuntimeError(f"CHIRPS time budget ({BUDGET_S} s) spent")
    hdr = dict(UA, **({"Range": f"bytes={a}-{b}"} if a is not None else {}))
    r = http_get(url, timeout=min(60, left), headers=hdr, retries=2, backoff=3)
    c = r.content
    if a is not None and r.status_code == 200:  # server ignored the Range
        c = c[a:b + 1]
    return c


def _tif_tags(url: str) -> dict:
    """The first IFD of a little-endian classic TIFF, {tag: tuple}, fetched with Range requests."""
    head = _get(url, 0, 7)
    if head[:4] != b"II*\x00":
        raise RuntimeError(f"{url}: not a little-endian classic TIFF")
    ifd = struct.unpack("<I", head[4:])[0]
    blk = _get(url, ifd, ifd + 65535)
    kinds = {2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("I", 8), 12: ("d", 8)}
    tags = {}
    for i in range(struct.unpack("<H", blk[:2])[0]):
        tag, typ, cnt = struct.unpack("<HHI", blk[2 + 12 * i:10 + 12 * i])
        if typ not in kinds:
            continue
        code, size = kinds[typ]
        raw = blk[10 + 12 * i:14 + 12 * i]
        if cnt * size > 4:
            off = struct.unpack("<I", raw)[0]
            raw = (blk[off - ifd:off - ifd + cnt * size] if off >= ifd and off + cnt * size <= ifd + len(blk)
                   else _get(url, off, off + cnt * size - 1))
        tags[tag] = raw[:cnt] if typ == 2 else struct.unpack(f"<{cnt * (2 if typ == 5 else 1)}{code}", raw[:cnt * size])
    return tags


def _tif_cells(url: str) -> tuple[list[float], list[int]]:
    """An Early Estimates total -> per output cell, (sum of its non-NaN pixels in mm, how many)."""
    t = _tif_tags(url)
    want = {256: (W,), 257: (H,), 258: (32,), 259: (1,), 277: (1,), 339: (3,)}
    bad = {k: t.get(k) for k, v in want.items() if t.get(k) != v}
    tie, scale = t.get(33922, ()), t.get(33550, ())
    if bad or tie[3:5] != (-180.0, 60.0) or len(scale) < 2 or abs(scale[0] - 0.05) > 1e-6 or abs(scale[1] - 0.05) > 1e-6:
        raise RuntimeError(f"{url}: not the 7200x2400 float32 uncompressed 180W/60N grid ({bad}, {tie}, {scale})")
    offs, cnts = t[273], t[279]
    if len(offs) != H or any(offs[i] != offs[0] + i * ROW_BYTES or cnts[i] != ROW_BYTES for i in range(NROWS)):
        raise RuntimeError(f"{url}: rows are not stored one after another")
    step = 235  # ten Range requests of 6.8 MB, four at a time
    spans = [(i, min(i + step, NROWS)) for i in range(0, NROWS, step)]
    with ThreadPoolExecutor(4) as ex:
        parts = list(ex.map(lambda s: _get(url, offs[0] + s[0] * ROW_BYTES, offs[0] + s[1] * ROW_BYTES - 1), spans))
    sums, n = [0.0] * NCELL, [0] * NCELL
    row = 0
    for (a, b), part in zip(spans, parts):
        if len(part) != (b - a) * ROW_BYTES:
            raise RuntimeError(f"{url}: {len(part)} bytes for rows {a}..{b - 1}")
        for j in range(0, len(part), ROW_BYTES):
            f = array.array("f")
            f.frombytes(part[j:j + ROW_BYTES])
            if sys.byteorder != "little":
                f.byteswap()
            base = (NROWS - 1 - row) // PX * NLON  # image row 0 is 60N, output row 0 is 57.5S..55S
            for c in range(NLON):
                sl = f[c * PX:(c + 1) * PX]
                s = sum(sl)
                if s == s:
                    sums[base + c] += s
                    n[base + c] += PX
                else:
                    v = [x for x in sl if x == x]
                    if v:
                        sums[base + c] += sum(v)
                        n[base + c] += len(v)
            row += 1
    return sums, n


def _newest() -> tuple[int, int]:
    """Newest (year, pentad of year) for which CHC has posted both the 6-pentad and the 1-pentad total."""
    got = []
    for n in (6, 1):
        html = http_get(EE_DIR.format(n=n), timeout=60, headers=UA, retries=3).text
        got.append({(int(y), int(p)) for y, p in re.findall(rf"Total_{n:02d}PentAccum_(\d{{4}})_p(\d\d)\.tif", html)})
    both = got[0] & got[1]
    if not both:
        raise RuntimeError("no Early Estimates totals listed")
    return max(both)


def _final_through(y: int, p: int, window: list[tuple[int, int]]) -> str | None:
    """Last day of the newest window pentad CHC summed from CHIRPS final (older ones are final too), from its
    run log; None when the log does not list the window's pentads."""
    try:
        log = http_get(EE_LOG.format(y=y, p=p), timeout=60, headers=UA, retries=2).text
    except RuntimeError:
        return None
    kind = {(int(yy), MONTHS.index(mo) * 6 + int(k)): v
            for v, yy, mo, k in re.findall(r"^\s*(Final|Prelim)\s+(\d{4})\s+([A-Z][a-z]{2})_p(\d)\s", log, re.M)}
    if not all(w in kind for w in window):
        return None
    fin = [w for w in window if kind[w] == "Final"]
    return pentad_dates(*fin[-1])[1].isoformat() if fin else ""


# --- the normal ----------------------------------------------------------

def _cache() -> dict:
    with gzip.open(NORMAL_CACHE, "rt", encoding="utf-8") as fh:
        c = json.load(fh)
    g = c["grid"]
    if ((g["lat0"], g["lon0"], g["step_deg"], g["nlat"], g["nlon"]) != (LAT0, LON0, STEP, NLAT, NLON)
            or len(c["rate"]) != 72 or not all(len(r) == len(c["cells"]) for r in c["rate"])
            or len(c["npix"]) != len(c["cells"])):
        raise RuntimeError(f"{NORMAL_CACHE.name}: grid or shape does not match this script")
    return c


# --- the fill block ------------------------------------------------------

def fill(need30: list[bool], need7: list[bool], arid_mm_day: float) -> tuple[dict, dict]:
    """need30/need7: per output cell, True where CPC has no reading on that layer.

    Returns (block, diag). block is sparse: "cells" lists the output cells (row-major from the south, as
    the CPC arrays) where CPC has no reading on the 30-day or the 7-day layer and CHIRPS has one; anom, mm
    and norm_mm (and week.*) are parallel to "cells", null where that layer is not filled there (CPC has
    it) or, for anom, where the cell is arid. diag holds the full CHIRPS grids, (obs, normal) in mm a
    day per cell or None, for the checks."""
    _deadline[0] = time.monotonic() + BUDGET_S
    y, p = _newest()
    end = pentad_dates(y, p)[1]
    if (date.today() - end).days > MAX_AGE:
        raise RuntimeError(f"newest CHIRPS pentad ends {end}, {(date.today() - end).days} days ago -- frozen feed")
    c = _cache()
    w30, w7 = _back(y, p, 6), [(y, p)]
    days30 = sum((pentad_dates(*w)[1] - pentad_dates(*w)[0]).days + 1 for w in w30)
    days7 = (end - pentad_dates(y, p)[0]).days + 1
    obs30, obs7 = _tif_cells(EE_TIF.format(n=6, y=y, p=p)), _tif_cells(EE_TIF.format(n=1, y=y, p=p))
    total = sum(obs7[1])
    if abs(total - c["land_pixels"]) > 0.001 * c["land_pixels"]:
        raise RuntimeError(f"{total} land pixels in the newest pentad, {c['land_pixels']} in the normal")

    def layer(obs, window, ndays):
        out, short = [None] * NCELL, 0
        for i, (k, npix) in enumerate(zip(c["cells"], c["npix"])):
            if obs[1][k] != npix:
                short += 1
                continue
            norm = sum(c["rate"][q - 1][i] / 1000 * ((pentad_dates(yy, q)[1] - pentad_dates(yy, q)[0]).days + 1)
                       for yy, q in window)
            out[k] = (obs[0][k] / npix / ndays, norm / ndays)
        return out, short

    f30, short30 = layer(obs30, w30, days30)
    f7, short7 = layer(obs7, w7, days7)
    pct = lambda x: None if x is None or x[1] < arid_mm_day else int(round(100 * (x[0] - x[1]) / x[1]))
    mm = lambda x, i, n: None if x is None else int(round(x[i] * n))
    cells = [k for k in range(NCELL) if (need30[k] and f30[k]) or (need7[k] and f7[k])]
    a30 = [f30[k] if need30[k] else None for k in cells]
    a7 = [f7[k] if need7[k] else None for k in cells]
    start = pentad_dates(*w30[0])[0]
    fin = _final_through(y, p, w30)
    block = {
        "product": PRODUCT,
        "source_url": EE + "/", "source_page": EE_PAGE, "product_page": CHIRPS_PAGE,
        "normal_source_url": CHIRPS_BIL,
        "base": "1991-2020",
        "base_source": "1991-2020 mean of CHIRPS v3.0 final pentads (CHC pentad archive), the same product",
        "base_read_from": f"committed cache data/ref/{NORMAL_CACHE.name}",
        "cells": cells,
        "encoding": ("cells: output grid indexes (row-major from the southern edge, as anom) where CPC has no "
                     "reading on the 30-day or 7-day layer and CHIRPS has one. anom/mm/norm_mm here and in "
                     "week are parallel to cells; null where that layer is CPC's, or (anom) arid. Percent and "
                     "whole mm as the CPC fields, over this block's own windows."),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days30, "pentads": len(w30),
                   "final_through": fin},
        "anom": [pct(x) for x in a30], "mm": [mm(x, 0, days30) for x in a30],
        "norm_mm": [mm(x, 1, days30) for x in a30],
        "n_valid": sum(pct(x) is not None for x in a30), "n_cells": sum(x is not None for x in a30),
        "week": {"start": pentad_dates(y, p)[0].isoformat(), "end": end.isoformat(), "days": days7, "pentads": 1,
                 "anom": [pct(x) for x in a7], "mm": [mm(x, 0, days7) for x in a7],
                 "norm_mm": [mm(x, 1, days7) for x in a7],
                 "n_valid": sum(pct(x) is not None for x in a7), "n_cells": sum(x is not None for x in a7)},
        "notes": [
            "Where NOAA CPC's gauge analysis has too few gauges, the cell shows CHIRPS instead: satellite "
            "infrared rain estimates blended with station reports, from the Climate Hazards Center (UC Santa "
            "Barbara). These cells are CHIRPS, not gauges, and are marked as such.",
            f"CHIRPS comes in five-day pentads, two days after each ends. Its 30-day layer is the six pentads "
            f"{start.isoformat()} to {end.isoformat()} ({days30} days) and its week the pentad "
            f"{pentad_dates(y, p)[0].isoformat()} to {end.isoformat()} ({days7} days): a few days older than "
            "CPC's windows.",
            ("CHIRPS final data runs to " + fin + "; later pentads are CHIRPS preliminary, which uses fewer "
             "stations and is revised about three weeks after the month ends.") if fin else
            "The newest pentads are CHIRPS preliminary, which uses fewer stations and is revised about three "
            "weeks after the month ends.",
            "The percent change is against CHIRPS's own 1991-2020 average for the same pentads, the same base "
            f"period as the gauge cells. Land that averages under {arid_mm_day} mm a day then is blank, as for "
            "the gauge cells.",
        ],
    }
    if short30 or short7:
        block["notes"].append(f"Cells with CHIRPS pixels missing are left out rather than averaged over less ground "
                              f"than their normal: {short30} on the 30-day layer, {short7} on the week.")
    try:  # an addition: the fill stands without it
        s = {}
        for kind, arr, nd in (("p6", a30, days30), ("p1", a7, days7)):
            fits, m = SPI.chirps_fits((LAT0, LON0, STEP, NLAT, NLON), kind, p - 1)
            s[kind] = [None if x is None or k not in fits
                       else SPI.to_x100(SPI.spi_chirps(x[0] * nd, nd, fits[k], m["zero_mm"], m["n_years"]))
                       for k, x in zip(cells, arr)]
        block["spi"], block["week"]["spi"] = s["p6"], s["p1"]
        block["spi_info"] = {
            "params": f"data/ref/{SPI.CHIRPS_PARAMS.name}", "pentad_of_year": p, "zero_mm": m["zero_mm"],
            "fit": ("gamma by maximum likelihood on the wet years of 1991-2020 CHIRPS v3.0 final pentads, per cell, "
                    f"for the six pentads (spi) and the one pentad (week.spi) ending at pentad {p} of the year; a "
                    "total under zero_mm gets the middle of the dry class, (dry years + 1) / 62"),
            "encoding": ("spi and week.spi: SPI x 100 as integers, parallel to cells, clamped to -300..300; null where "
                         "that layer's mm is null or the window has no 1991-2020 fit; set on arid cells too"),
            "n_valid": sum(v is not None for v in s["p6"]), "week_n_valid": sum(v is not None for v in s["p1"]),
        }
        block["notes"].append("SPI for these cells is against CHIRPS's own 1991-2020 record of the same pentads, on "
                              "the same scale as the gauge cells.")
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as e:
        print(f"[WARN] CHIRPS SPI left out: {type(e).__name__}: {e}")
    return block, {"c30": f30, "c7": f7}


# --- checks printed by refresh_rain_anomaly.main ---------------------------

REGIONS = (("Congo basin", -5, 5, 15, 30), ("Amazon interior", -12, 0, -72, -50),
           ("Indonesia / Maritime Continent", -10, 6, 95, 141), ("Sahel", 10, 18, -17, 30),
           ("East Africa", -5, 12, 33, 51), ("Southern Africa", -35, -15, 12, 40), ("Africa", -35, 37, -18, 52))


def _in(k, s, n, w, e):
    return s <= LAT0 + STEP * (k // NLON) <= n and w <= LON0 + STEP * (k % NLON) <= e


def _med(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def _corr(a, b):
    n = len(a)
    if n < 3:
        return float("nan")
    ma, mb = sum(a) / n, sum(b) / n
    sab = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    saa, sbb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return sab / (saa * sbb) ** 0.5 if saa and sbb else float("nan")


def report(payload: dict, cpc: dict, chirps: dict, arid_mm_day: float) -> None:
    fl = payload.get("fill")
    if not fl:
        print("[check] no CHIRPS fill this run")
        return
    fm = lambda x: "n/a" if x is None else f"{x:+d}%"
    for key, blk, cp in (("30d", fl, payload), ("7d", fl["week"], payload["week"])):
        filled = [v for v in blk["anom"] if v is not None]
        print(f"[check] fill {key}: {blk['n_cells']} CPC-blank cells get CHIRPS ({len(filled)} not arid), median "
              f"{fm(_med(filled))}; CPC valid {sum(v is not None for v in cp['anom'])}")
    pct = lambda x: None if x is None or x[1] < arid_mm_day else 100 * (x[0] - x[1]) / x[1]
    g = cpc["gauges"]
    for key in ("c30", "c7"):
        for label, keep in (("all overlap", lambda k: True), (">=10 gauges/day", lambda k: (g[k] or 0) >= 10)):
            ks = [k for k in range(NCELL) if keep(k) and pct(cpc[key][k]) is not None and pct(chirps[key][k]) is not None]
            a, b = [pct(cpc[key][k]) for k in ks], [pct(chirps[key][k]) for k in ks]
            nr = _med([chirps[key][k][1] / cpc[key][k][1] for k in ks])
            print(f"[check] CPC vs CHIRPS {key[1:]}d, {label}: {len(ks)} cells, CHIRPS-CPC median "
                  f"{_med([y - x for x, y in zip(a, b)] or [float('nan')]):+.0f} pts, r {_corr(a, b):.2f}, normal ratio "
                  f"CHIRPS/CPC median {nr if nr is None else round(nr, 2)}")
    idx = {k: i for i, k in enumerate(fl["cells"])}
    for label, s, n, w, e in REGIONS:
        ks = [k for k in range(NCELL) if _in(k, s, n, w, e)]
        land = [k for k in ks if chirps["c30"][k] is not None or cpc["c30"][k] is not None]
        before = sum(payload["anom"][k] is not None for k in ks)
        fills = [fl["anom"][idx[k]] for k in ks if k in idx and fl["anom"][idx[k]] is not None]
        wf = [fl["week"]["anom"][idx[k]] for k in ks if k in idx and fl["week"]["anom"][idx[k]] is not None]
        blank = [k for k in land if payload["anom"][k] is None and not (k in idx and fl["anom"][idx[k]] is not None)]
        nrm = lambda k: (cpc["c30"][k] or chirps["c30"][k])[1]  # CPC's normal decides where CPC has a reading
        arid = sum(1 for k in blank if nrm(k) < arid_mm_day)
        print(f"[check] {label}: {len(land)} land cells, 30d with a value {before} (CPC) -> {before + len(fills)} "
              f"(+CHIRPS); blank {len(blank)} ({arid} arid); CPC median "
              f"{fm(_med([payload['anom'][k] for k in ks if payload['anom'][k] is not None]))}, fill median "
              f"30d {fm(_med(fills))}, 7d {fm(_med(wf))}")
        wet = [k for k in blank if nrm(k) >= arid_mm_day]
        if wet:
            print(f"[check]   {label} still blank, not arid (lat, lon): "
                  + ", ".join(f"({LAT0 + STEP * (k // NLON):g}, {LON0 + STEP * (k % NLON):g})" for k in wet[:30]))


# --- one-off build of NORMAL_CACHE (numpy) ---------------------------------

def _bil_pentad(job):
    """(pentad of year, [(year, path)]) -> (pentad, per-cell sum of the mean rate over land pixels in mm/day as
    a 47x144 array, north first, the land mask packed, files whose mask differs, bytes read)."""
    import tarfile
    import numpy as np
    p, files = job
    tot, mask, odd, nbytes = None, None, [], 0
    for y, path in files:
        with tarfile.open(path) as t:
            m = next(x for x in t.getmembers() if x.name.endswith(".bil"))
            raw = t.extractfile(m).read()
        nbytes += Path(path).stat().st_size
        a = np.frombuffer(raw, dtype="<i2").reshape(H, W)[:NROWS]
        land = a >= 0
        if mask is None:
            mask, tot = land, np.zeros((NBAND, NLON))
        elif not np.array_equal(land, mask):
            odd.append(y)
            continue
        s, e = pentad_dates(y, p)
        v = np.where(a >= 1, a + 0.5, 0.0) / ((e - s).days + 1)  # undo the archive's floor(); 0 stays 0
        tot += v.reshape(NBAND, PX, NLON, PX).sum((1, 3))
    return p, tot / (len(files) - len(odd)), np.packbits(mask), odd, nbytes


def build_normal_cache(folder: str) -> int:
    import numpy as np
    from multiprocessing import Pool
    jobs = []
    for p in range(1, 73):
        files = [(y, Path(folder) / f"v3p0chirps{y}{p:02d}.tar.gz") for y in range(1991, 2021)]
        missing = [str(f) for _, f in files if not f.exists()]
        if missing:
            raise RuntimeError(f"missing {len(missing)} archive files, e.g. {missing[:3]}")
        jobs.append((p, files))
    with Pool(4) as pool:  # ~400 MB a worker
        res = pool.map(_bil_pentad, jobs)
    masks = {r[2].tobytes() for r in res}
    odd = [(p, y) for p, _, _, ys, _ in res for y in ys]
    if len(masks) != 1 or odd:
        raise RuntimeError(f"land mask not fixed across the archive: {len(masks)} masks, odd files {odd[:10]}")
    land = np.unpackbits(res[0][2])[:NROWS * W].reshape(NROWS, W).astype(bool)
    npix = land.reshape(NBAND, PX, NLON, PX).sum((1, 3))[::-1].reshape(-1)       # output rows 0..46, south first
    rate = np.stack([r[1][::-1].reshape(-1) for r in res])                       # 72 x (47*144)
    cells = np.nonzero(npix)[0]
    out = {
        "what": "1991-2020 mean rain rate of CHIRPS v3.0 final pentads per pentad of the year, averaged to the "
                "2.5-degree cells of refresh_rain_anomaly.py over each cell's 0.05-degree land pixels",
        "source": CHIRPS_BIL, "source_files": "v3p0chirps<year><pentad>.tar.gz, 1991-2020, pentads 1-72",
        "n_files": 72 * 30, "source_bytes": int(sum(r[4] for r in res)),
        "floor_correction": "the archive stores floor(mm) as int16; +0.5 mm on every pixel of 1 mm or more",
        "land_pixels": int(land.sum()),
        "built": date.today().isoformat(),
        "built_by": "python3 scripts/chirps_rain_fill.py --build-normal <folder of the 2,160 archive files>",
        "grid": {"lat0": LAT0, "lon0": LON0, "step_deg": STEP, "nlat": NLAT, "nlon": NLON},
        "cells_encoding": "output cell index, row-major from the southern edge; cells with CHIRPS land pixels",
        "npix_encoding": "per cell, its number of 0.05-degree CHIRPS land pixels (at most 2,500)",
        "rate_encoding": "72 pentads of the year, 1-5 January first; per pentad, per listed cell, the 1991-2020 "
                         "mean of the cell's pentad total over the pentad's days, in 0.001 mm a day (integers)",
        "cells": cells.tolist(), "npix": npix[cells].tolist(),
        "rate": np.rint(rate[:, cells] / npix[cells] * 1000).astype(int).tolist(),
    }
    NORMAL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    NORMAL_CACHE.write_bytes(gzip.compress(json.dumps(out, separators=(",", ":")).encode(), 9, mtime=0))
    print(f"[OK] wrote {NORMAL_CACHE} ({len(cells)} cells, {out['land_pixels']} land pixels, "
          f"{NORMAL_CACHE.stat().st_size / 1e3:.0f} KB)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--build-normal":
        raise SystemExit(build_normal_cache(sys.argv[2]))
    raise SystemExit("usage: chirps_rain_fill.py --build-normal <folder>  (the fill runs from refresh_rain_anomaly.py)")
