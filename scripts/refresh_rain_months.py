#!/usr/bin/env python3
"""refresh_rain_months.py -- observed rain on land for each of the last six complete months, against 1991-2020.

Feeds the El Nino Ocean map's time scrubber, which steps back from the live rain
layer (refresh_rain_anomaly.py) through the months before it, as sst_months.json
does for the sea. Same 2.5-degree land grid (52 x 144 from 56.25S / 178.75W,
row-major from the south), same sources, thresholds and base, with the calendar
month as the window:

  * CPC: NOAA CPC Global Unified Gauge-Based daily analysis, real-time, 0.5
    degree, every day of the month, against the committed 1991-2020 daily normal
    of the same product. The live module's own functions do the work, so its
    gauge mask (MIN_GAUGES reports a day inside a cell), spike filter, share of
    days a cell needs (NEED_SHARE) and arid mask (ARID_MM_DAY) apply unchanged.
    mm and norm_mm are month totals: the cell's mean over the days kept, times
    the days in the month.
  * Fill: where CPC leaves a land cell blank, CHIRPS v3.0 for the month, against
    the 1991-2020 pentad normal cache of chirps_rain_fill.py summed over the
    month's six pentads (CHIRPS pentads split every month into six). CHC posts a
    preliminary month two days after it ends (PRELIM_DIR) and a final one, with
    more stations and quality control, in the middle of the next month
    (FINAL_DIR). Both are LZW-compressed float32 GeoTIFFs on the CHIRPS 0.05
    degree grid, read here with a small pure-Python LZW decoder.

Cache: data/rain_months.json itself. A month is fetched once. While its CHIRPS
part is preliminary (or not posted yet) each run checks CHC's listings, and the
month is fetched again, CPC too, once when the final file appears: by then
CPC's late gauge reports for the month are in as well. On a normal day this
step is one CPC listing and, at most, two CHC listings.

SPI (added 28 September 2026): cpc.spi and fill.spi, the month's Standardized
Precipitation Index on the live layers' scale (refresh_rain_anomaly.py), from
scripts/spi.py and the calendar month's 1991-2020 fit of the same product:
data/ref/cpc_spi_params_1991_2020.json.gz (nominal month length, February 28
days; 0.5 mm dry class) and data/ref/chirps3_spi_params_1991_2020.json.gz (CHIRPS
v3.0 final monthly files; 1 mm dry class). SPI x 100, integers in -300..300;
null where the percent is null or there is no fit. A fetched month feeds its
unrounded mean rain per day (spi_from "rate"); months cached before SPI existed
get it once from their stored whole-mm totals (spi_from "whole_mm", at most 0.5
mm off over the month; on the live 30-day layer of 28 September 2026 that moved
SPI by 0.01 median, 0.03 at the 95th percentile, 0.10 at most) until refetched.

Pure Python (requests only), like the live collector.
"""
from __future__ import annotations

import array
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402
import chirps_rain_fill as F  # noqa: E402
import refresh_rain_anomaly as R  # noqa: E402
import spi as SPI  # noqa: E402

MONTHS = 6
OUT = Path(__file__).resolve().parent.parent / "data" / "rain_months.json"
CHC = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0"
FINAL_DIR = CHC + "/monthly/global/tifs/"
PRELIM_DIR = CHC + "/prelim/monthly/global/tifs/"
TIF = "chirps-v3.0.{y}.{m:02d}.tif"
BUDGET_S = 150          # per CHIRPS month; chirps_rain_fill._get enforces it
NCELL, NLON, NLAT = R.NCELL, R.NLON, R.NLAT
# (label, lat_s, lat_n, lon_w, lon_e) on cell centres, for the printed checks only.
BOXES = (("Maritime Continent / Indonesia", -11, 6, 95, 141),
         ("Amazon", -12, 2, -73, -50),
         ("southern Africa", -35, -15, 12, 40),
         ("East Africa", -5, 12, 33, 51),
         ("India", 8, 30, 70, 88),
         ("eastern Australia", -38, -15, 140, 154),
         ("southern Brazil / Uruguay", -35, -22, -58, -48),
         ("Central America dry corridor", 11, 16, -92, -85),
         ("Sahel", 10, 18, -17, 30))


def _month_end(y: int, m: int) -> date:
    return date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)


# --- CPC part --------------------------------------------------------------

def _cpc_month(y: int, m: int, listed: set) -> tuple[dict, dict]:
    first, last = date(y, m, 1), _month_end(y, m)
    ndays = (last - first).days + 1
    days = [first + timedelta(days=i) for i in range(ndays) if first + timedelta(days=i) in listed]
    if len(days) < R.NEED_SHARE * ndays:
        raise RuntimeError(f"{y}-{m:02d}: CPC has {len(days)} of {ndays} days")
    norm, normal_from = R._normal(days)
    with ThreadPoolExecutor(4) as ex:  # downloads overlap; the per-day reduction runs as each arrives
        per = {d: R._cell_day(obs, norm[d]) for d, obs in zip(days, ex.map(R._cpc_day, days))}
    del norm
    cells, _, dropped, ungauged, gauges = R._windows(per, days, [])
    tot = lambda i: [None if x is None else int(round(x[i] * ndays)) for x in cells]
    anom = R._pct(cells)
    block = {"days": len(days), "anom": anom, "mm": tot(0), "norm_mm": tot(1)}
    if len(days) < ndays:
        block["missing_days"] = [(first + timedelta(days=i)).isoformat() for i in range(ndays)
                                 if first + timedelta(days=i) not in listed]
    stats = {"n_valid_cpc": sum(v is not None for v in anom), "cells_without_gauges": ungauged,
             "spike_cell_days_dropped": len(dropped)}
    return {"block": block, "stats": stats, "normal_from": normal_from}, {"cells": cells, "gauges": gauges}


# --- CHIRPS part -----------------------------------------------------------

def _lzw(data: bytes) -> bytes:
    """TIFF LZW (codes MSB-first, 9 to 12 bits, early change), pure Python."""
    d = data + b"\0\0\0"
    base = [bytes((i,)) for i in range(256)] + [b"", b""]
    table = list(base)
    out = bytearray()
    width, limit, pos, nbits, prev = 9, 511, 0, len(data) * 8, b""
    while pos + width <= nbits:
        i = pos >> 3
        code = ((d[i] << 16 | d[i + 1] << 8 | d[i + 2]) >> (24 - (pos & 7) - width)) & ((1 << width) - 1)
        pos += width
        if code == 256:
            table, width, limit, prev = list(base), 9, 511, b""
            continue
        if code == 257:
            break
        if code < len(table):
            s = table[code]
            if prev:
                table.append(prev + s[:1])
        elif code == len(table) and prev:
            s = prev + prev[:1]
            table.append(s)
        else:
            raise RuntimeError(f"LZW code {code} past the table ({len(table)})")
        out += s
        prev = s
        if len(table) >= limit and width < 12:
            width += 1
            limit = (1 << width) - 1
    return bytes(out)


def _tif_month_cells(url: str) -> tuple[list[float], list[int]]:
    """A CHIRPS v3 monthly GeoTIFF -> per output cell, (sum of its land pixels in mm, how many)."""
    t = F._tif_tags(url)
    want = {256: (F.W,), 257: (F.H,), 258: (32,), 259: (5,), 277: (1,), 278: (1,), 339: (3,)}
    bad = {k: t.get(k) for k, v in want.items() if t.get(k) != v}
    tie, scale = t.get(33922, ()), t.get(33550, ())
    if (bad or t.get(317, (1,)) != (1,) or tie[3:5] != (-180.0, 60.0) or len(scale) < 2
            or abs(scale[0] - 0.05) > 1e-6 or abs(scale[1] - 0.05) > 1e-6):
        raise RuntimeError(f"{url}: not the 7200x2400 float32 LZW 180W/60N grid ({bad}, {tie}, {scale})")
    offs, cnts = t[273], t[279]
    if len(offs) != F.H:
        raise RuntimeError(f"{url}: {len(offs)} strips, expected one per row")
    a, b = min(offs[:F.NROWS]), max(o + c for o, c in zip(offs[:F.NROWS], cnts[:F.NROWS]))
    if b - a > 60e6:
        raise RuntimeError(f"{url}: rows span {b - a} bytes")
    step = -(-(b - a) // 4)
    spans = [(s, min(s + step, b)) for s in range(a, b, step)]
    with ThreadPoolExecutor(4) as ex:
        buf = b"".join(ex.map(lambda s: F._get(url, s[0], s[1] - 1), spans))
    if len(buf) != b - a:
        raise RuntimeError(f"{url}: {len(buf)} bytes for a {b - a}-byte span")
    sums, n = [0.0] * NCELL, [0] * NCELL
    px = F.PX
    for row in range(F.NROWS):
        raw = _lzw(buf[offs[row] - a:offs[row] - a + cnts[row]])
        if len(raw) != F.ROW_BYTES:
            raise RuntimeError(f"{url}: row {row} decodes to {len(raw)} bytes")
        f = array.array("f")
        f.frombytes(raw)
        if sys.byteorder != "little":
            f.byteswap()
        base = (F.NROWS - 1 - row) // px * NLON  # image row 0 is 60N, output row 0 is 57.5S..55S
        for c in range(NLON):
            sl = f[c * px:(c + 1) * px]
            s = sum(sl)
            if s == s and min(sl) >= 0:
                sums[base + c] += s
                n[base + c] += px
            else:  # sea is -9999; NaN treated the same
                v = [x for x in sl if x >= 0]
                if v:
                    sums[base + c] += sum(v)
                    n[base + c] += len(v)
    return sums, n


def _chirps_month(y: int, m: int, final: bool) -> tuple[list, int, str]:
    """Per output cell (observed, normal) in mm a day over the month, or None; cells left out; the file read."""
    F._deadline[0] = time.monotonic() + BUDGET_S  # this module's own budget, whatever ran before in run_all
    url = (FINAL_DIR if final else PRELIM_DIR) + TIF.format(y=y, m=m)
    sums, n = _tif_month_cells(url)
    c = F._cache()
    # A wrong grid or mask moves far more than 1%. Small gaps are real: the March 2026 files carry no data in
    # their top two pixel rows (59.9N-60N), 6,210 pixels; the cells they touch are left out below.
    if abs(sum(n) - c["land_pixels"]) > 0.01 * c["land_pixels"]:
        raise RuntimeError(f"{url}: {sum(n)} land pixels, {c['land_pixels']} in the normal")
    ps = [(m - 1) * 6 + k for k in range(1, 7)]
    pdays = [(F.pentad_dates(y, p)[1] - F.pentad_dates(y, p)[0]).days + 1 for p in ps]
    ndays = sum(pdays)
    if ndays != (_month_end(y, m) - date(y, m, 1)).days + 1:
        raise RuntimeError(f"{y}-{m:02d}: pentads cover {ndays} days")
    out, short = [None] * NCELL, 0
    for i, (k, npix) in enumerate(zip(c["cells"], c["npix"])):
        if n[k] != npix:
            short += 1
            continue
        norm = sum(c["rate"][p - 1][i] / 1000 * dd for p, dd in zip(ps, pdays))
        out[k] = (sums[k] / npix / ndays, norm / ndays)
    return out, short, url


def _fill_block(cpc_cells: list, chirps: list, ndays: int, final: bool, url: str, short: int) -> dict:
    arid = R.ARID_MM_DAY
    cells = [k for k in range(NCELL) if cpc_cells[k] is None and chirps[k] is not None]
    pct = lambda x: None if x[1] < arid else int(round(100 * (x[0] - x[1]) / x[1]))
    blk = {"product": "CHIRPS v3.0 " + ("final" if final else "preliminary") + " monthly", "final": final,
           "source_url": url, "cells": cells,
           "anom": [pct(chirps[k]) for k in cells],
           "mm": [int(round(chirps[k][0] * ndays)) for k in cells],
           "norm_mm": [int(round(chirps[k][1] * ndays)) for k in cells]}
    if short:
        blk["cells_left_out"] = short
    return blk


def _add_spi(rec: dict, cpc_cells: list | None, chirps: list | None) -> None:
    """cpc.spi and fill.spi of one month (see the docstring). cpc_cells / chirps: this run's unrounded per-cell
    (observed, normal) mm a day, or None where the block is the cache's; a cached block that has spi keeps it."""
    m, ndays, g = int(rec["month"][5:]), rec["days"], (R.LAT0, R.LON0, R.STEP, NLAT, NLON)
    cp = rec["cpc"]
    if cpc_cells is not None or "spi" not in cp:
        fits, s = SPI.cpc_fits(g, "month", m - 1)
        rate = ([None if x is None else x[0] for x in cpc_cells] if cpc_cells is not None
                else [None if v is None else v / ndays for v in cp["mm"]])
        cp["spi"] = [None if r is None or a is None or k not in fits
                     else SPI.to_x100(SPI.spi_cpc(r, fits[k], SPI.MONTH_DAYS[m - 1], s["zero_mm"], s["clip"]))
                     for k, (r, a) in enumerate(zip(rate, cp["anom"]))]
        cp["spi_from"] = "rate" if cpc_cells is not None else "whole_mm"
    fl = rec.get("fill")
    if fl and (chirps is not None or "spi" not in fl):
        fits, s = SPI.chirps_fits(g, "month", m - 1)
        tot = [chirps[k][0] * ndays for k in fl["cells"]] if chirps is not None else fl["mm"]
        fl["spi"] = [None if t is None or a is None or k not in fits
                     else SPI.to_x100(SPI.spi_chirps(t, ndays, fits[k], s["zero_mm"], s["n_years"]))
                     for k, t, a in zip(fl["cells"], tot, fl["anom"])]
        fl["spi_from"] = "rate" if chirps is not None else "whole_mm"


def _listing(url: str) -> set:
    html = http_get(url, timeout=60, headers=R.UA, retries=2).text
    return {(int(y), int(m)) for y, m in re.findall(r"chirps-v3\.0\.(\d{4})\.(\d\d)\.tif\b", html)}


# --- build -----------------------------------------------------------------

def _fetch(y: int, m: int, listed: set, chc: dict, prev: dict | None, cpc_too: bool) -> tuple[dict, dict]:
    """One month: CPC (or prev's CPC when cpc_too is False) and the best CHIRPS file CHC has posted."""
    key, ndays = f"{y}-{m:02d}", (_month_end(y, m) - date(y, m, 1)).days + 1
    diag = {}
    if cpc_too:
        t0 = time.monotonic()
        got, diag["cpc"] = _cpc_month(y, m, listed)
        rec = {"month": key, "days": ndays, "fetched": date.today().isoformat(), "cpc": got["block"],
               **got["stats"], "fill": None, "n_fill": 0}
        diag["normal_from"] = got["normal_from"]
        print(f"[fetch] {key} CPC {got['block']['days']} days in {time.monotonic() - t0:.0f} s")
    else:
        rec = dict(prev, fill=None, n_fill=0)
        diag["cpc"] = None
    cpc_cells = diag["cpc"]["cells"] if diag["cpc"] else [
        None if v is None else True for v in rec["cpc"]["mm"]]  # a CPC mm means CPC has the cell
    for final in (True, False):
        if (y, m) not in chc.get(final, set()):
            continue
        try:
            t0 = time.monotonic()
            ch, short, url = _chirps_month(y, m, final)
            rec["fill"] = _fill_block(cpc_cells, ch, ndays, final, url, short)
            rec["n_fill"] = sum(v is not None for v in rec["fill"]["anom"])
            diag["chirps"] = ch
            print(f"[fetch] {key} CHIRPS {'final' if final else 'preliminary'} in {time.monotonic() - t0:.0f} s")
            break
        except Exception as e:  # noqa: BLE001 -- the CPC part stands without the fill
            print(f"[WARN] {key} CHIRPS {'final' if final else 'preliminary'}: {type(e).__name__}: {e}")
    return rec, diag


def build() -> tuple[dict, dict]:
    today = date.today()
    lists = {}

    def listed_for(year: int) -> set:
        if year not in lists:
            try:
                lists[year] = R._listed_days(year)
            except RuntimeError:  # early January, before CPC opens the new year's folder
                lists[year] = set()
        return lists[year]

    newest = (max((d for d in listed_for(today.year) if d <= today), default=None)
              or max((d for d in listed_for(today.year - 1) if d <= today), default=None))
    if newest is None:
        raise RuntimeError("CPC lists no days")
    y, m = newest.year, newest.month
    if newest < _month_end(y, m):
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
        except Exception:  # noqa: BLE001 -- an unreadable cache means fetch everything again
            old = {}
    chc = {}
    if any(not ((old.get(f"{y}-{m:02d}") or {}).get("fill") or {}).get("final") for y, m in want):
        for final, url in ((True, FINAL_DIR), (False, PRELIM_DIR)):
            try:
                chc[final] = _listing(url)
            except RuntimeError as e:
                print(f"[WARN] CHC listing {url}: {e}")

    months, diags, normal_from = [], {}, None
    for y, m in want:
        key = f"{y}-{m:02d}"
        prev = old.get(key)
        pfill = (prev or {}).get("fill")
        if prev and pfill and pfill.get("final"):
            months.append(prev)
            continue
        if prev and not ((y, m) in chc.get(True, set()) or (not pfill and (y, m) in chc.get(False, set()))):
            months.append(prev)  # nothing newer at CHC
            continue
        # New month, or the final CHIRPS file is out (fetch CPC again too: its late reports are in by
        # now), or no CHIRPS yet and the preliminary one is out (CHIRPS only).
        cpc_too = prev is None or (y, m) in chc.get(True, set())
        try:
            rec, diag = _fetch(y, m, listed_for(y), chc, prev, cpc_too)
        except Exception as e:  # noqa: BLE001 -- keep the cached month rather than lose it
            print(f"[WARN] {key}: {type(e).__name__}: {e}")
            if prev:
                months.append(prev)
            continue
        months.append(rec)
        diags[key] = diag
        normal_from = diag.get("normal_from") or normal_from
    if not months:
        raise RuntimeError("no complete month available")
    for rec in months:
        d = diags.get(rec["month"]) or {}
        try:  # an addition: the month stands without it
            _add_spi(rec, (d.get("cpc") or {}).get("cells"), d.get("chirps"))
        except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as e:
            print(f"[WARN] {rec['month']} SPI left out: {type(e).__name__}: {e}")

    payload = {
        "product": "NOAA CPC Global Unified Gauge-Based Analysis of Daily Precipitation, real-time, 0.5 degree, "
                   "summed over each calendar month",
        "source_url": f"{R.CPC_RT}/", "normal_source_url": R.PSL_PAGE,
        "base": "1991-2020",
        "base_source": "NOAA PSL daily long-term mean 1991-2020 of the same CPC product, summed over the month",
        "base_read_from": normal_from or f"committed cache data/ref/{R.NORMAL_CACHE.name}, built from {R.PSL_FTP}",
        "fill_product": F.PRODUCT.replace("pentads", "monthly totals"),
        "fill_source_url": FINAL_DIR, "fill_prelim_source_url": PRELIM_DIR, "fill_product_page": F.CHIRPS_PAGE,
        "fill_base_source": "1991-2020 mean of CHIRPS v3.0 final pentads (CHC pentad archive), the month's six "
                            "pentads summed",
        "fill_normal_source_url": F.CHIRPS_BIL,
        "fill_base_read_from": f"committed cache data/ref/{F.NORMAL_CACHE.name}",
        "grid": {"lat0": R.LAT0, "lon0": R.LON0, "step_deg": R.STEP, "nlat": NLAT, "nlon": NLON,
                 "encoding": "row-major from the southern edge; same grid as rain_anomaly.json"},
        "encoding": ("months oldest first. cpc.anom: integer percent change of the month's rain against the "
                     "1991-2020 normal for the same month, null over sea, arid (normal under arid_mm_day a day), "
                     "too few gauges or missing days. cpc.mm / cpc.norm_mm: whole mm over the calendar month (the "
                     "cell's mean over the days kept, times days), null over sea or where CPC has no reading. "
                     "fill: CHIRPS for cells CPC leaves null (not arid ones); cells lists grid indexes, anom/mm/"
                     "norm_mm are parallel to it, anom null where arid; null when CHC has not posted the month. "
                     "n_valid_cpc and n_fill count non-null percents."),
        "spi_encoding": ("cpc.spi (grid, as cpc.anom) and fill.spi (parallel to fill.cells): Standardized "
                         "Precipitation Index of the month x 100 as integers, clamped to -300..300 (-300 = -3 or "
                         "below); null where the percent is null or the cell has no 1991-2020 fit for that calendar "
                         "month. spi_from: 'rate' = from the unrounded mean rain per day, 'whole_mm' = from the "
                         "stored whole-mm total (months cached before SPI was added)."),
        "spi_params": {"cpc": f"data/ref/{SPI.CPC_PARAMS.name}", "fill": f"data/ref/{SPI.CHIRPS_PARAMS.name}"},
        "min_gauges_per_day": R.MIN_GAUGES, "arid_mm_day": R.ARID_MM_DAY, "need_share": R.NEED_SHARE,
        "spike_filter": {"times_normal": R.SPIKE_X, "over_mm": R.SPIKE_MM},
        "months": months,
        "notes": [
            "Observed rain for each of the last six complete calendar months, as a percent change against the "
            "1991-2020 average for the same month. Rain gauges and satellite estimates, not a model.",
            "Most land cells come from NOAA CPC's daily gauge analysis, summed over the month and averaged to "
            "2.5-degree cells, against the 1991-2020 average of the same product. It is the same method as the "
            "live 30-day rain layer.",
            f"A cell with fewer than {R.MIN_GAUGES:g} gauge reports a day gets no gauge reading, because away from "
            "gauges CPC's analysis falls towards no rain. Those cells show CHIRPS v3.0 instead, satellite "
            "infrared estimates blended with station reports from the Climate Hazards Center (UC Santa "
            "Barbara), against CHIRPS's own 1991-2020 average. They are marked as CHIRPS.",
            "CHIRPS posts a preliminary month two days after it ends and a final one, with more stations and "
            "more checks, in the middle of the next month. Each month says which it uses. A preliminary month "
            "is replaced when the final one is posted.",
            f"Land that averages under {R.ARID_MM_DAY} mm of rain a day in that month is blank, because there a "
            "single shower reads as a huge percent. Sea is blank.",
            f"A day in a cell is dropped from CPC's analysis when it is over {R.SPIKE_MM} mm and over "
            f"{R.SPIKE_X} times the cell's normal daily rain. This removes bad reports, but it can also remove "
            "a real extreme storm, so a cell hit by one may read drier than it was.",
            "SPI, the Standardized Precipitation Index, puts each month on the same scale as the last 30 days and "
            "the last week: how unusual that month's rain was at that place against the same calendar month in "
            "1991-2020. -1, -1.5 and -2 are moderately, severely and extremely dry (about 1 year in 6, 15 and 44 "
            "that dry or drier); +1, +1.5 and +2 the wet mirror.",
        ],
    }
    return payload, diags


# --- checks ----------------------------------------------------------------

def _med(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def _fm(x):
    return "n/a" if x is None else f"{x:+d}%"


def report(payload: dict, diags: dict) -> None:
    for mo in payload["months"]:
        cp, fl = mo["cpc"], mo.get("fill") or {}
        fidx = {k: i for i, k in enumerate(fl.get("cells", []))}
        ca = [v for v in cp["anom"] if v is not None]
        fa = [v for v in fl.get("anom", []) if v is not None]
        print(f"[check] {mo['month']}: CPC {mo['n_valid_cpc']} valid ({cp['days']}/{mo['days']} days, "
              f"{mo['cells_without_gauges']} cells without gauges, {mo['spike_cell_days_dropped']} spike cell-days), "
              f"CHIRPS {mo['n_fill']} valid ({fl.get('product', 'none')}); median CPC {_fm(_med(ca))}, "
              f"CHIRPS {_fm(_med(fa))}, both {_fm(_med(ca + fa))}")
        sp = [v for v in (cp.get("spi") or []) + (fl.get("spi") or []) if v is not None]
        if sp:
            print(f"[check]   SPI ({cp.get('spi_from')}/{fl.get('spi_from')}): {len(sp)} cells, median "
                  f"{_med(sp) / 100:+.2f}, |SPI| >= 1 in {100 * sum(abs(v) >= 100 for v in sp) / len(sp):.0f}%, "
                  f">= 2 in {100 * sum(abs(v) >= 200 for v in sp) / len(sp):.0f}% (a normal year: 32% and 5%)")
        for label, s, n, w, e in BOXES:
            vals, mm, nm, kc, kf = [], 0, 0, 0, 0
            for r in range(NLAT):
                for c in range(NLON):
                    if not (s <= R.LAT0 + R.STEP * r <= n and w <= R.LON0 + R.STEP * c <= e):
                        continue
                    k = r * NLON + c
                    if cp["anom"][k] is not None:
                        vals.append(cp["anom"][k])
                        mm, nm, kc = mm + cp["mm"][k], nm + cp["norm_mm"][k], kc + 1
                    elif k in fidx and fl["anom"][fidx[k]] is not None:
                        i = fidx[k]
                        vals.append(fl["anom"][i])
                        mm, nm, kf = mm + fl["mm"][i], nm + fl["norm_mm"][i], kf + 1
            tot = f"{100 * (mm - nm) / nm:+.0f}%" if nm else "n/a"
            print(f"[check]   {label}: {kc} CPC + {kf} CHIRPS cells, median {_fm(_med(vals))}, "
                  f"box total {mm} vs {nm} mm ({tot})")
        d = diags.get(mo["month"]) or {}
        if d.get("cpc") and d.get("chirps"):
            arid = R.ARID_MM_DAY
            pct = lambda x: None if x is None or x[1] < arid else 100 * (x[0] - x[1]) / x[1]
            g = d["cpc"]["gauges"]
            for lab, keep in (("all overlap", lambda k: True), (">=10 gauges/day", lambda k: (g[k] or 0) >= 10)):
                ks = [k for k in range(NCELL) if keep(k) and pct(d["cpc"]["cells"][k]) is not None
                      and pct(d["chirps"][k]) is not None]
                diff = _med([pct(d["chirps"][k]) - pct(d["cpc"]["cells"][k]) for k in ks])
                print(f"[check]   CHIRPS vs CPC, {lab}: {len(ks)} cells, CHIRPS-CPC median "
                      f"{'n/a' if diff is None else f'{diff:+.0f}'} pts, same sign in "
                      f"{sum((pct(d['chirps'][k]) >= 0) == (pct(d['cpc']['cells'][k]) >= 0) for k in ks)}")


def main() -> int:
    payload, diags = build()
    fills = [x["fill"] for x in payload["months"] if x.get("fill")]
    path = write_json("rain_months.json", payload,
                      source="NOAA CPC Global Unified Gauge-Based daily precipitation (CPC FTP) against the 1991-2020 "
                             "daily normal of the same product (NOAA PSL precip.day.ltm.1991-2020.nc, committed cache)"
                             + ("; where CPC has too few gauges, CHIRPS v3.0 monthly (CHC) against its own 1991-2020 "
                                "pentad normal, in each month's fill block" if fills else ""),
                      notes="Observed rain on land for each of the last six complete months as a percent change "
                            "against 1991-2020, on the El Nino Ocean map's 2.5-degree land grid. Not a model.",
                      status="ok")
    path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=False, separators=(",", ":")))
    print(f"[OK] {len(payload['months'])} months {payload['months'][0]['month']}..{payload['months'][-1]['month']} "
          f"| {path.stat().st_size / 1e3:.0f} KB")
    report(payload, diags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
