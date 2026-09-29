#!/usr/bin/env python3
"""build_chirps_spi_params.py -- 1991-2020 gamma fits for a Standardized Precipitation Index of the CHIRPS fill.

Why: the El Nino Ocean map shows observed rain on land at three kinds of stop (the last 7 days, the last
30 days, each of the last six calendar months). As a percent of the 1991-2020 mean these do not read
alike: a week's rain varies far more from year to year than a month's, so the same percent is ordinary
over a week and rare over 30 days. One scale for every stop is the Standardized Precipitation Index
(SPI; McKee, Doesken and Kleist 1993; the index WMO recommends, WMO-No. 1090, 2012): the window's total
is placed on the distribution of the same window's totals at the same time of year in 1991-2020, and
that cumulative probability is read off a standard normal. SPI -1 / -1.5 / -2 = moderately / severely /
extremely dry; +1 / +1.5 / +2 wet.

This script builds, once and locally, the climatology half for the cells chirps_rain_fill.py can fill,
and writes OUT (data/ref/chirps3_spi_params_1991_2020.json.gz, 1.65 MB). It needs numpy and scipy, which
CI does not install; CI never runs it. The live code evaluates SPI from OUT in pure Python: spi() below
is that evaluation (math + statistics only), and every check here runs through it.

Data
  * Pentads: CHIRPS v3.0 final pentads 1991-2020 (plus 1990 pentads 68-72, which the first six-pentad
    windows of 1991 reach back into), the BIL archive NORMAL_CACHE was built from (F.CHIRPS_BIL,
    v3p0chirps<year><pentad>.tar.gz). Treated exactly as the normal build treats it: int16 whole mm
    rounded DOWN, so +0.5 mm on every pixel of 1 mm or more, 0 stays 0; a pixel is land where the
    value is >= 0, and the land mask must be the same in every file (it is, pentads and months alike).
  * Calendar months: CHIRPS v3.0 final monthly totals from the BIL archive of the monthly product
    (MONTHLY_BIL, v3p0chirps<year><month>.tar.gz), with the same +0.5 mm; not the sum of six pentad
    files. CHC's monthly float GeoTIFF is the sum of its six pentad GeoTIFFs (July 2020: every land pixel
    within 0.0005 mm), and the monthly BIL is floor() of it (July 2020 and January 1998: every land
    pixel), so both routes describe the same rain; but six pentad files lose up to six sub-millimetre
    remainders where the monthly file loses one. Against the float monthly file, cell means of the
    monthly BIL sit within -0.09%..+0.03% (July 2020; -0.45%..+0.05% January 1998) and the six-pentad
    BIL sums within -2.9%..+0.05% (-4.1%..+0.02%), 5th..95th percentile of cells over 0.5 mm a day.
    The live month layer reads the float monthly file, so the monthly BIL it is.
  * A cell is the mean of its 0.05-degree land pixels (up to 2,500), on the 2.5-degree output grid of
    refresh_rain_anomaly.py, with chirps_rain_fill.py's reshape; the cell list and pixel counts must equal
    NORMAL_CACHE's. Every one of its 2,697 cells gets parameters: the fill can fill any cell whose land
    pixels the observation covers in full.

Windows (what the live fill shows; a window's year is the year its last pentad falls in)
  * p1: one pentad (the fill's "7 days"), ending at each of the 72 pentads of the year.
  * p3: three pentads (the fill's "14 days", 13-16 days), ending at each of the 72 pentads (added 29
    September 2026; the other kinds are unchanged by it).
  * p6: six pentads (the fill's "30 days", 28-31 days), ending at each of the 72 pentads.
  * month: each of the 12 calendar months.
  The fitted quantity is the window's cell-mean rate, total / days, in mm a day, so the leap-year
  February pentad (4 days, not 3) and a 29-day February compare like with like.

Fit, per cell and window, over the 30 years
  * Zero class: a window whose cell-mean total is under ZERO_MM (1 mm) counts as dry. The archive
    cannot see less: any pixel under 1 mm reads 0, so a cell can read exactly 0 or a few thousandths of a
    mm from one pixel, and such values would drag a gamma fit (whose likelihood runs through log x)
    far from the rest of the record. The live code applies the same 1 mm to its observed total.
  * Gamma on the wet years by maximum likelihood (exact: Newton's method on ln a - digamma(a) =
    ln(mean) - mean(ln x), started from Thom's 1958 approximation that McKee et al. used; Thom's shape
    is within 0%..+1.1% of it, 5th..95th percentile). ML keeps the wet mean exactly, so each window
    stores shape and wet mean (scale = wet mean / shape).
  * SPI of a rate x (the live code's recipe, spi() below): with n = 30 years and d dry years,
    q = d / n (McKee et al. 1993; WMO-No. 1090); x from a total >= 1 mm: H = q + (1 - q) G(x), G the
    fitted gamma; a dry window (total < 1 mm): H = (d + 1) / (2 (n + 1)), the centre of the dry
    class's probability on the Weibull plotting position, as recommended by Stagge et al. (2015,
    Int. J. Climatol. 35, 4027-4040), so a rainless window reads 0 or below even where dry windows are
    common, and a finite value where the record has none. SPI = inverse standard normal of H.
    A rainless window cannot read lower than the dry class allows: -1.85 with one dry year in 30,
    -1.30 with five, -0.65 with fifteen. That is the honest answer (a dry week is no rarity there),
    and it is why single pentads show fewer extreme values than a normal curve would (checks).
  * Beyond about SPI -1.85 / +1.85 (1 in 31, the rarest rank 30 years give) a value rests on the fitted
    gamma's tail, not on ranked years; below -3 it lies well past anything in 1991-2020 (Para now).
    The page should say "-3 or below" rather than print the digits.
  * No parameters (null) where
      - the window's 1991-2020 normal in NORMAL_CACHE (the normal the live percent uses) is under
        SPI_ARID_MM_DAY: p1 and p3 0.5 mm a day (ARID_MM_DAY, the live arid mask), p6 and month 0.1 (below);
      - fewer than MIN_WET (15) of the 30 years are wet. Then the window's median total is a dry one,
        any rain at all ranks above it, and a gamma rests on under 15 points. This blanks 8.6% of the
        non-arid single-pentad windows (20 wet years would blank 20.7%), 13 six-pentad windows and 1
        month;
      - the record is frozen: its wet totals take fewer than MIN_DISTINCT (10) distinct values, or the
        fitted shape is over MAX_SHAPE (400, the wet years varying by under 5%). CHIRPS repeats itself
        in some windows at tiny islands and along its 57.5-60N edge -- 30 years of one pentad at 5.071
        or 4.976 mm near Malden Island (shape 13,216) -- which is CHIRPS falling back on its
        climatology, not rain; a gamma through it turns any real shower into an SPI in the tens. For
        comparison the steadiest six-pentad or monthly record anywhere varies by 6.5% (shape 236), and
        99.9% of single-pentad records in cells of 1,000+ land pixels south of 57.5N by 22% or more.
        Blanks 173 single-pentad windows in 21 cells (one window in a full land cell: eastern Yemen,
        6-10 January), 12 six-pentad windows and 5 months in 2 cells (south Greenland, a 3.75S atoll).

Since 28 September 2026 p6 and month fits reach down to a normal of 0.1 mm a day, p1 and p3 fits keep 0.5 (inland
Australia's wet August 2026 read blank). The calibration that decided it, both products, is in
build_cpc_spi_params.py's docstring.

Storage: quantised integers, as NORMAL_CACHE: shape x 1000, wet mean in 0.001 mm a day (shapes >= 0.33 and wet
means >= 0.495: steps of 0.3% at worst; SPI moves <= 0.004), dry years as a count; lists [window][cell].

Checks (build of 2026-09-28; also in OUT["checks"])
  * The 1991-2020 pentad means recomputed here equal NORMAL_CACHE to 0.0005 mm a day (its rounding).
  * Monthly BIL against the sum of its six pentad BILs, every month 1991-2020, cells over 0.5 mm a day:
    -0.03% / +0.08% / +1.8% (5th / 50th / 95th percentile); their 1991-2020 means 0.0% / +0.1% / +1.5%.
  * 200 random cells, SPI of their own 30 years under the stored fit (expected: mean 0, SD 1, and 15.9%
    / 6.7% / 2.3% below -1 / -1.5 / -2):
      p1     mean +0.01, SD 0.96; 12.9% / 3.4% / 0.6% (the dry class, above); KS not rejected 96.6%
      p6     mean  0.00, SD 1.00; 16.1% / 6.6% / 2.1%; KS not rejected 99.2%
      month  mean  0.00, SD 1.00; 16.1% / 6.6% / 2.1%; KS not rejected 99.3%
    (Kolmogorov-Smirnov of the wet years against their fitted gamma at 5%; with estimated parameters
    the test is lenient, so 96.6% says single pentads fit a gamma less well than longer windows.)
    spi() matches scipy to 1e-13.
  * The live observation is a float file, the climatology the floored archive. Evaluated on July 2020
    with the stored fits, the float reads wetter by SPI -0.003 / 0.000 / +0.025 / +0.09 (5th / 50th /
    95th / 99th percentile of cells) for single pentads and -0.001 / +0.001 / +0.042 / +0.15 for six.
    The live code can floor each pixel of a pentad or month file as the archive does and remove this;
    the Early Estimates' six-pentad total cannot be floored pentad by pentad.
  * The fill now (data/rain_anomaly.json of 28 September 2026: 30 days = 26 Aug-25 Sep, week = 21-25
    Sep, pentad 53), whole-mm totals as stored (the float files behind them agree to 0.03):
      Para (6.25S 56.25W)       30 days  -73%  SPI -3.14 (lower than all 30 years)   week -81%  SPI -1.56
      Congo (1.25S 23.75E)      30 days  -15%  SPI -1.07                             week -36%  SPI -1.06
      Borneo (1.25N 113.75E)    30 days  -61%  SPI -1.93                             week -29%  SPI -0.47
      western Niger (13.75N 1.25E)  30 days +2% SPI +0.18                          week +148% SPI +1.39
      central Ethiopia (8.75N 38.75E) 30 days +8% SPI +0.44                        week -26%  SPI -0.45

Run (5.3 GB of archives, 2,165 pentad and 360 monthly files, not kept; --series caches what was read):
  python3 scripts/build_chirps_spi_params.py <pentad .tar.gz folder> <monthly .tar.gz folder> [--series x.npz]
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import sys
import tarfile
from datetime import date
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chirps_rain_fill as F  # noqa: E402
import refresh_rain_anomaly as R  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "ref" / "chirps3_spi_params_1991_2020.json.gz"
MONTHLY_BIL = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/monthly/global/bils/"
Y0, Y1 = 1991, 2020
N_YEARS = Y1 - Y0 + 1
ZERO_MM = 1.0             # window total (cell mean, mm) under which a window counts as dry
MIN_WET = 15              # wet years (of 30) a fit needs: the window's median must be a wet one
MIN_DISTINCT = 10         # distinct wet totals a record needs (see "frozen records" in the docstring)
MAX_SHAPE = 400           # gamma shape above which the wet years vary by under 5% (ditto)
ARID_MM_DAY = R.ARID_MM_DAY
# A window whose 1991-2020 normal is under this (mm a day) gets no fit: p6 and month reach below the live percent's
# arid mask, p1 does not (the docstring has the calibration that decided it).
SPI_ARID_MM_DAY = {"p1": ARID_MM_DAY, "p3": ARID_MM_DAY, "p6": 0.1, "month": 0.1}
KINDS = (("p1", 72), ("p3", 72), ("p6", 72), ("month", 12))
PLACES = (("Para, Brazil", -6.25, -56.25), ("Congo basin", -1.25, 23.75), ("Borneo", 1.25, 113.75),
          ("western Niger", 13.75, 1.25), ("central Ethiopia", 8.75, 38.75))
_ND = NormalDist()


# --- the evaluation the live code does (pure Python) -----------------------

def gammp(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x): series below a + 1, Lentz continued fraction above."""
    if x <= 0:
        return 0.0
    lead = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1:
        ap, d = a, 1.0 / a
        s = d
        for _ in range(100000):
            ap += 1
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-15:
                break
        return min(1.0, s * math.exp(lead))
    tiny = 1e-300
    b = x + 1 - a
    c, d = 1 / tiny, 1 / b
    h = d
    for i in range(1, 100000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = d if abs(d) > tiny else tiny
        c = b + an / c
        c = c if abs(c) > tiny else tiny
        d = 1 / d
        h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return max(0.0, 1.0 - math.exp(lead) * h)


def spi(total_mm: float, days: float, shape: float, wet_mean: float, dry_years: int, n: int = N_YEARS) -> float:
    """SPI of a window total (cell mean, mm over `days` days) from one cell-window's parameters."""
    if total_mm < ZERO_MM:
        # Same monotonic rule as scripts/spi.py: never less dry than a total just above the threshold.
        q = dry_years / n
        h = min((dry_years + 1) / (2 * (n + 1)), q + (1 - q) * gammp(shape, ZERO_MM / days * shape / wet_mean))
    else:
        q = dry_years / n
        h = q + (1 - q) * gammp(shape, total_mm / days * shape / wet_mean)
    return _ND.inv_cdf(min(max(h, 1e-12), 1 - 1e-12))


# --- reading the archives (numpy) ------------------------------------------

def _read(path: str):
    """One archive -> (per output cell, sum of its land pixels in mm after the +0.5 correction, south-first
    rows 0..46 flattened; sha1 of the packed land mask)."""
    import numpy as np
    with tarfile.open(path) as t:
        m = next(x for x in t.getmembers() if x.name.endswith(".bil"))
        raw = t.extractfile(m).read()
    if len(raw) != F.H * F.W * 2:
        raise RuntimeError(f"{path}: {len(raw)} bytes, not {F.W}x{F.H} int16")
    a = np.frombuffer(raw, dtype="<i2").reshape(F.H, F.W)[:F.NROWS]
    land = a >= 0
    wet = a >= 1  # sum of (a + 0.5) over wet pixels, in integers (exact, and a tenth of the memory of floats)
    blk = lambda v: v.reshape(F.NBAND, F.PX, F.NLON, F.PX).sum((1, 3), dtype=np.int64)
    s = (blk(np.where(wet, a, 0)) + 0.5 * blk(wet))[::-1].reshape(-1)
    return path, s, hashlib.sha1(np.packbits(land).tobytes()).hexdigest()


def _npix(path: str):
    import numpy as np
    with tarfile.open(path) as t:
        m = next(x for x in t.getmembers() if x.name.endswith(".bil"))
        a = np.frombuffer(t.extractfile(m).read(), dtype="<i2").reshape(F.H, F.W)[:F.NROWS]
    return (a >= 0).reshape(F.NBAND, F.PX, F.NLON, F.PX).sum((1, 3))[::-1].reshape(-1)


def read_series(pent_dir: str, mon_dir: str, cells: list[int], npix_cache: list[int]):
    """P[year-1990, pentad-1, cell] and M[year-1991, month-1, cell]: cell-mean totals in mm (NaN = not read)."""
    import numpy as np
    from multiprocessing import Pool
    jobs = {}
    for y in range(Y0 - 1, Y1 + 1):
        for p in range(1, 73):
            if y >= Y0 or p >= 68:
                jobs[str(Path(pent_dir) / f"v3p0chirps{y}{p:02d}.tar.gz")] = ("P", y - (Y0 - 1), p - 1)
    for y in range(Y0, Y1 + 1):
        for m in range(1, 13):
            jobs[str(Path(mon_dir) / f"v3p0chirps{y}{m:02d}.tar.gz")] = ("M", y - Y0, m - 1)
    missing = [f for f in jobs if not Path(f).exists()]
    if missing:
        raise RuntimeError(f"missing {len(missing)} archive files, e.g. {missing[:3]}")
    first = next(iter(jobs))
    npix = _npix(first)
    if npix[cells].tolist() != npix_cache or int(npix.sum()) != int(npix[cells].sum()):
        raise RuntimeError("land pixels per cell differ from the normal cache")
    P = np.full((N_YEARS + 1, 72, len(cells)), np.nan)
    M = np.full((N_YEARS, 12, len(cells)), np.nan)
    masks, nbytes = set(), 0
    with Pool(4) as pool:
        for i, (path, s, h) in enumerate(pool.imap_unordered(_read, list(jobs), chunksize=8)):
            kind, a, b = jobs[path]
            (P if kind == "P" else M)[a, b] = s[cells] / npix[cells]
            masks.add(h)
            nbytes += Path(path).stat().st_size
            if i % 250 == 0:
                print(f"[read] {i}/{len(jobs)}", flush=True)
    if len(masks) != 1:
        raise RuntimeError(f"land mask not fixed across the archives: {len(masks)} masks")
    return P, M, len(jobs), nbytes


# --- windows and fits ------------------------------------------------------

def _pdays(y: int, p: int) -> int:
    s, e = F.pentad_dates(y, p)
    return (e - s).days + 1


def windows(P, M):
    """{kind: (totals[30, nwin, ncell] in mm, days[30, nwin])}; window year = year of its last pentad."""
    import numpy as np
    yrs = range(Y0, Y1 + 1)
    t1 = P[1:]
    d1 = np.array([[_pdays(y, p) for p in range(1, 73)] for y in yrs], float)
    t3, d3, t6, d6 = np.zeros_like(t1), np.zeros_like(d1), np.zeros_like(t1), np.zeros_like(d1)
    for iy, y in enumerate(yrs):
        for p in range(1, 73):
            for n, t, d in ((3, t3, d3), (6, t6, d6)):
                for yy, pp in F._back(y, p, n):
                    t[iy, p - 1] += P[yy - (Y0 - 1), pp - 1]
                    d[iy, p - 1] += _pdays(yy, pp)
    dm = np.array([[(date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days for m in range(1, 13)] for y in yrs],
                  float)
    out = {"p1": (t1, d1), "p3": (t3, d3), "p6": (t6, d6), "month": (M, dm)}
    for k, (t, _) in out.items():
        if np.isnan(t).any():
            raise RuntimeError(f"{k}: windows with unread files")
    return out


def cache_normals(c: dict):
    """The live normal (NORMAL_CACHE) per kind, [nwin, ncell] mm a day, days of a non-leap year."""
    import numpy as np
    rate = np.array(c["rate"], float) / 1000
    d = np.array([_pdays(2019, p) for p in range(1, 73)], float)
    n3, n6 = (np.stack([sum(rate[pp - 1] * _pdays(yy, pp) for yy, pp in F._back(2019, p, n))
                        / sum(_pdays(yy, pp) for yy, pp in F._back(2019, p, n)) for p in range(1, 73)]) for n in (3, 6))
    nm = np.stack([(rate[6 * m:6 * m + 6] * d[6 * m:6 * m + 6, None]).sum(0) / d[6 * m:6 * m + 6].sum()
                   for m in range(12)])
    return {"p1": rate, "p3": n3, "p6": n6, "month": nm}


def fit(tot, days):
    """Exact gamma ML on the wet years of each [window, cell]: (shape, wet mean mm/day, dry years, Thom shape,
    distinct wet totals at 0.001 mm)."""
    import numpy as np
    from scipy.special import digamma, polygamma
    x = tot / days[:, :, None]
    wet = tot >= ZERO_MM
    nwet = wet.sum(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(wet, x, 0).sum(0) / nwet
        mlog = np.where(wet, np.log(np.where(wet, x, 1)), 0).sum(0) / nwet
        A = np.log(mean) - mlog
        thom = (1 + np.sqrt(1 + 4 * A / 3)) / (4 * A)
        a = thom.copy()
        for _ in range(50):
            step = (np.log(a) - digamma(a) - A) / (1 / a - polygamma(1, a))
            a = np.clip(a - step, a / 10, a * 10)
            if np.nanmax(np.abs(step / a)) < 1e-12:
                break
        srt = np.sort(np.where(wet, np.round(tot, 3), np.inf), axis=0)  # dry years sort last as inf
        distinct = (np.isfinite(srt[:1]) + ((np.diff(srt, axis=0) > 0) & np.isfinite(srt[1:])).sum(0, keepdims=True))[0]
    return a, mean, N_YEARS - nwet, thom, distinct


# --- checks ----------------------------------------------------------------

def _pct(v, qs=(5, 50, 95)):
    import numpy as np
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return [round(float(np.percentile(v, q)), 3) for q in qs] if v.size else None


def checks(ser, fits, stored, cells, normals, P, M, c) -> dict:
    import numpy as np
    from scipy import stats
    out = {}
    # 1. the recomputed pentad means are the committed normal
    rate = np.array(c["rate"], float) / 1000
    mine = (ser["p1"][0] / ser["p1"][1][:, :, None]).mean(0)
    out["normal_cache_max_abs_diff_mm_day"] = round(float(np.abs(mine - rate).max()), 5)
    # 2. monthly files against the sum of their six pentads (cells with a normal of 0.5 mm a day or more)
    psum = np.stack([P[1:, 6 * m:6 * m + 6].sum(1) for m in range(12)], 1)
    keep = np.broadcast_to(normals["month"][None] >= ARID_MM_DAY, M.shape)
    r = (M[keep] - psum[keep]) / np.maximum(psum[keep], 1e-9)
    with np.errstate(invalid="ignore", divide="ignore"):
        clim = (M.mean(0) - psum.mean(0)) / psum.mean(0)
    out["month_vs_pentad_sum"] = {"each_month_pct_5_50_95": _pct(100 * r[psum[keep] >= 10]),
                                  "mean_1991_2020_pct_5_50_95": _pct(100 * clim[normals["month"] >= ARID_MM_DAY]),
                                  "n": int(keep.sum())}
    # 3. 200 cells: SPI of the climatology years under their own (stored, quantised) fit
    rng = np.random.default_rng(20260928)
    pick = rng.choice(len(cells), 200, replace=False)
    for kind, _ in KINDS:
        tot, days = ser[kind]
        sh, wm, dy = stored[kind]
        a_f, m_f = fits[kind][0], fits[kind][1]
        allz, means, sds, ks_ok, ks_n, dq, dsc = [], [], [], 0, 0, [], []
        for i in pick:
            for w in range(tot.shape[1]):
                if sh[w][i] is None:
                    continue
                a, mw, d = sh[w][i] / 1000, wm[w][i] / 1000, dy[w][i]
                z = [spi(tot[y, w, i], days[y, w], a, mw, d) for y in range(N_YEARS)]
                allz += z
                means.append(np.mean(z))
                sds.append(np.std(z, ddof=1))
                wet = tot[:, w, i] >= ZERO_MM
                xw = tot[wet, w, i] / days[wet, w]
                ks_n += 1
                ks_ok += stats.kstest(xw, "gamma", args=(a, 0, mw / a)).pvalue >= 0.05
                for y in range(0, N_YEARS, 7):
                    if wet[y]:
                        xx = tot[y, w, i] / days[y, w]
                        q = d / N_YEARS
                        ref = stats.norm.ppf(q + (1 - q) * stats.gamma.cdf(xx, a, scale=mw / a))
                        dsc.append(abs(spi(tot[y, w, i], days[y, w], a, mw, d) - ref))
                        dq.append(abs(z[y] - spi(tot[y, w, i], days[y, w], a_f[w, i], m_f[w, i], d)))
        out[f"{kind}_200_cells"] = {
            "cell_windows": len(means), "pooled_mean": round(float(np.mean(allz)), 3),
            "pooled_sd": round(float(np.std(allz)), 3),
            "share_below_-1_-1.5_-2": [round(float(np.mean(np.array(allz) < t)), 3) for t in (-1, -1.5, -2)],
            "per_window_mean_5_50_95": _pct(means), "per_window_sd_5_50_95": _pct(sds),
            "ks_not_rejected_5pct_share": round(ks_ok / max(ks_n, 1), 3),
            "max_abs_spi_pure_python_vs_scipy": float(f"{max(dsc):.1e}"),
            "max_abs_spi_quantised_vs_unquantised": round(max(dq), 4)}
    # 4. Thom's approximation against exact ML (fitted cell-windows)
    rat = np.concatenate([(fits[k][3] / fits[k][0])[np.array([[v is not None for v in row] for row in stored[k][0]])]
                          for k, _ in KINDS])
    out["thom_over_ml_shape_5_50_95"] = _pct(rat)
    return out


def fill_now(stored, cells, ser) -> list[dict]:
    """SPI of the fill now in data/rain_anomaly.json (whole-mm totals) at PLACES, with the 1991-2020 record of
    the same window for comparison (lowest total, rescaled to the window's days; years below the value now)."""
    fl = json.loads((ROOT / "data" / "rain_anomaly.json").read_text())["data"]["fill"]
    end = date.fromisoformat(fl["window"]["end"])
    p = (end.month - 1) * 6 + min((end.day - 1) // 5, 5) + 1
    idx, ci = {k: i for i, k in enumerate(fl["cells"])}, {k: i for i, k in enumerate(cells)}
    rows = []
    for label, lat, lon in PLACES:
        k = int(round((lat - F.LAT0) / F.STEP)) * F.NLON + int(round((lon - F.LON0) / F.STEP))
        row = {"place": label, "lat": lat, "lon": lon, "cell": k, "pentad": p}
        for key, blk, kind in (("30d", fl, "p6"), ("week", fl["week"], "p1")):
            if k not in idx or blk["mm"][idx[k]] is None:
                row[key] = "CPC has this cell (no fill)"
                continue
            j, i = idx[k], ci[k]
            sh, wm, dy = (stored[kind][n][p - 1][i] for n in range(3))
            nd = blk["days"] if kind == "p1" else fl["window"]["days"]
            z = None if sh is None else round(spi(blk["mm"][j], nd, sh / 1000, wm / 1000, dy), 2)
            rec = ser[kind][0][:, p - 1, i] / ser[kind][1][:, p - 1] * nd
            row[key] = {"mm": blk["mm"][j], "norm_mm": blk["norm_mm"][j], "pct": blk["anom"][j], "spi": z,
                        "dry_years": dy, "record_min_mm": round(float(rec.min()), 1),
                        "years_below_now": int((rec < blk["mm"][j]).sum())}
        rows.append(row)
    return rows


# --- build -----------------------------------------------------------------

def main(argv: list[str]) -> int:
    import numpy as np
    if len(argv) not in (3, 5) or (len(argv) == 5 and argv[3] != "--series"):
        raise SystemExit(__doc__.split("Run (")[1])
    with gzip.open(F.NORMAL_CACHE, "rt", encoding="utf-8") as fh:
        c = json.load(fh)
    cells = c["cells"]
    series = Path(argv[4]) if len(argv) == 5 else None
    if series and series.exists():
        z = np.load(series)
        P, M, nfiles, nbytes = z["P"], z["M"], int(z["nfiles"]), int(z["nbytes"])
    else:
        P, M, nfiles, nbytes = read_series(argv[1], argv[2], cells, c["npix"])
        if series:
            np.savez(series, P=P, M=M, nfiles=nfiles, nbytes=nbytes)
    ser, normals = windows(P, M), cache_normals(c)
    fits, stored, counts = {}, {}, {}
    for kind, nw in KINDS:
        a, mw, dry, thom, distinct = fit(*ser[kind])
        fits[kind] = (a, mw, dry, thom)
        arid = normals[kind] < SPI_ARID_MM_DAY[kind]
        few = ~arid & (N_YEARS - dry < MIN_WET)
        frozen = ~arid & ~few & ((distinct < MIN_DISTINCT) | (a > MAX_SHAPE))
        ok = ~arid & ~few & ~frozen
        if not np.isfinite(a[ok]).all() or (a[ok] <= 0).any():
            raise RuntimeError(f"{kind}: a fitted window has no finite shape")
        sh = np.where(ok, np.rint(a * 1000), -1).astype(int)
        wm = np.where(ok, np.rint(mw * 1000), -1).astype(int)
        stored[kind] = ([[None if v < 0 else int(v) for v in row] for row in sh],
                        [[None if v < 0 else int(v) for v in row] for row in wm], dry.astype(int).tolist())
        counts[kind] = {"cell_windows": int(a.size), "fitted": int(ok.sum()), "null_arid": int(arid.sum()),
                        "null_too_few_wet_years": int(few.sum()), "null_frozen_record": int(frozen.sum()),
                        "with_a_dry_year_share": round(float((dry[ok] > 0).mean()), 3),
                        "shape_5_50_95": _pct(a[ok]), "fitted_cells_any_window": int(ok.any(0).sum())}
        print(f"[fit] {kind}: {counts[kind]}")
    chk = checks(ser, fits, stored, cells, normals, P, M, c)
    for k, v in chk.items():
        print(f"[check] {k}: {v}")
    now = fill_now(stored, cells, ser)
    for r in now:
        print(f"[check] fill now, {r['place']} ({r['lat']}, {r['lon']}), pentad {r['pentad']}: 30d {r['30d']}; "
              f"week {r['week']}")
    out = {
        "what": "Gamma parameters (with a dry-window probability) for the Standardized Precipitation Index of "
                "CHIRPS v3.0 rain, per 2.5-degree cell of refresh_rain_anomaly.py and per window of the year, fitted "
                "to 1991-2020; for the CHIRPS fill of the El Nino Ocean map",
        "index": "Standardized Precipitation Index: McKee, Doesken and Kleist (1993); WMO-No. 1090 (2012)",
        "source_pentads": F.CHIRPS_BIL, "source_monthly": MONTHLY_BIL,
        "source_files": "v3p0chirps<year><pentad>.tar.gz 1991-2020 (and 1990 pentads 68-72); "
                        "v3p0chirps<year><month>.tar.gz 1991-2020",
        "n_files": nfiles, "source_bytes": nbytes,
        "floor_correction": "the archives store floor(mm) as int16; +0.5 mm on every pixel of 1 mm or more",
        "built": date.today().isoformat(),
        "built_by": "python3 scripts/build_chirps_spi_params.py <pentad folder> <monthly folder> (numpy, scipy)",
        "grid": c["grid"], "cells": cells,
        "cells_encoding": f"output cell index, row-major from the southern edge; identical to {F.NORMAL_CACHE.name}",
        "years": f"{Y0}-{Y1}", "n_years": N_YEARS, "zero_mm": ZERO_MM, "min_wet_years": MIN_WET,
        "min_distinct_wet_totals": MIN_DISTINCT, "max_shape": MAX_SHAPE,
        "arid_mm_day": ARID_MM_DAY, "spi_arid_mm_day": SPI_ARID_MM_DAY,
        "windows": {"p1": "one CHIRPS pentad, ending at pentad 1..72 of the year (1-5 January first)",
                    "p3": "three pentads, ending at pentad 1..72 (the first two reach into the previous year)",
                    "p6": "six pentads, ending at pentad 1..72 (the first five reach into the previous year)",
                    "month": "calendar months January..December, from CHIRPS v3.0 monthly files"},
        "fit": "gamma by exact maximum likelihood on the wet years (window total >= zero_mm), rate = total / days "
               "in mm a day",
        "how_to_use": "x = observed cell-mean total / window days. If the total < zero_mm: H = (dry + 1) / (2 * "
                      "(n_years + 1)). Else H = q + (1 - q) * P(shape, x * shape / wet_mean), q = dry / n_years, P the "
                      "regularised lower incomplete gamma. SPI = inverse standard normal of H. null shape: no SPI. "
                      "Past about +-1.85 (1 in 31) SPI rests on the fitted tail; show under -3 as '-3 or below'.",
        "encoding": "shape: gamma shape x 1000; wet_mean: mean rate of the wet years in 0.001 mm a day (= shape x "
                    "scale); dry: years of 30 with a total under zero_mm. Each [window][cell], parallel to cells; "
                    "shape and wet_mean null where the normal is under spi_arid_mm_day for that kind, wet years < "
                    "min_wet_years, or "
                    "the record is frozen (under min_distinct_wet_totals distinct wet totals, or shape > max_shape).",
        "counts": counts, "checks": chk,
    }
    for kind, _ in KINDS:
        out[kind] = {"shape": stored[kind][0], "wet_mean": stored[kind][1], "dry": stored[kind][2]}
    OUT.write_bytes(gzip.compress(json.dumps(out, separators=(",", ":")).encode(), 9, mtime=0))
    print(f"[OK] wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
