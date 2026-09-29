#!/usr/bin/env python3
"""build_cpc_spi_params.py -- SPI gamma parameters of CPC gauge rain, 1991-2020, per land cell and window.

Why. The El Nino Ocean map shows observed rain on land over the last 7 days, the last 30 days
and each of the last six calendar months (refresh_rain_anomaly.py, refresh_rain_months.py). A
percent of the 1991-2020 mean does not mean the same thing on those windows: a week varies far
more than a month, so -40% is an ordinary week and a rare month. The Standardized Precipitation
Index (McKee, Doesken and Kleist 1993; WMO's recommended meteorological drought index, WMO-No.
1090, 2012) puts every window on one scale: the probability of the window's rain under the
cell's own 1991-2020 distribution for the same window at the same time of year, mapped to a
standard normal. SPI -1 / -1.5 / -2 is moderately / severely / extremely dry (about 16% / 7% /
2% of years that dry or drier); +1 / +1.5 / +2 the wet mirror.

This one-off LOCAL builder writes the parameters to PARAMS
(data/ref/cpc_spi_params_1991_2020.json.gz). It needs numpy, scipy and netCDF4, which CI does
not install; block(), spi() and load() are pure Python for the collectors.

Data:
  * PSL's yearly files of the CPC Unified daily gauge analysis, precip.1990.nc .. precip.2020.nc
    (PSL_FTP_YEAR, about 60 MB each, NetCDF-4; PSL's HTTP host answered 502 in September 2026,
    its FTP served them). PSL averaged these files into precip.day.ltm.1991-2020.nc, the live
    normal (refresh_rain_anomaly.NORMAL_CACHE): the build's 1991-2020 mean of its own cell series
    matches that cache to its 0.01 mm rounding on every cell-day with all 30 years. Through 2005
    the files are CPC's retrospective V1.0 analysis, from 2006 the real-time one (as the normal);
    the build prints how far the two halves differ. 1990 only supplies the December days of the
    windows that end in early January 1991: a window belongs to the year of its last day, so every
    window has the 30 years 1991-2020.
  * Aggregation: the live collector's. Each day, each 2.5-degree cell is the mean of its
    0.5-degree cells in the normal cache's 25-bit mask (refresh_rain_anomaly.CELL_SUBS); a
    cell-day where one of them has no value is missing. On 15 July 2020, CPC's own real-time file
    through the live code (R._cpc_day, R._cell_day) and PSL's file through this build agree to
    0.00 mm on all 3,133 cells both have. Consequence: 95 coastal and island cells (Indonesia,
    Chile, Madagascar, Tanzania, Victoria...) hold 0.5-degree cells that CPC's analysis only
    covers from 2007, so they have at most 14 years and get no fit (the 2/3 rule below) rather
    than a climatology on other ground than the live reading.
  * Spike filter: the live rule, per window. A cell-day over SPIKE_MM and over SPIKE_X times the
    cell's mean normal daily rain over the 30 days ending on the window's last day is dropped
    (the calendar month for month windows); as live, the 7-day window uses the 30-day window's
    normal. A window needs NEED_SHARE of its days after the filter; its value is the mean over
    the days kept. 26 February 2007 is missing in PSL's files and is skipped like a missing day.
  * Gauge counts: PSL's files do not carry CPC's gauge-count field, so the climatology has no
    gauge mask. Every land cell is fitted; the live MIN_GAUGES test alone decides whether a cell
    shows a CPC reading.
  * 29 February is dropped from every leap year, so every window is a run of days on a 365-day
    calendar, as in the normal cache (where 29 February reads 28 February). The value fitted is a
    rate, the window's mean rain per day, so a live window that holds 29 February or misses a day
    compares like any other.

Windows (value fitted: the window's mean rain in mm a day, over the days kept):
  * d7, d14, d30: the 7, 14 and 30 days ending on each pentad end day, day of year 5, 10, .., 365 (ENDS).
    (d14 added 29 September 2026, for the live 14-day layer; the other kinds are unchanged by it.)
    A live window ending on day d uses the nearest end, block() = ((d + 2) // 5 - 1) % 73. Each
    end pools the windows ending up to POOL = 5 days either side (wrapping round the year inside
    the same year, so every value is a window ending in 1991-2020): 11 x 30 = 330 values.
    Chosen out of sample: fit on the 15 odd years, SPI of the 15 even years (the same order when
    trained on the even years). Pool +-0 / 2 / 5 / 10 days:
        7-day   SD 1.135 / 1.095 / 1.059 / 1.030; under -1.5: 9.2 / 8.2 / 7.5 / 6.9% (normal 6.7)
                                                  under -2:   3.2 / 2.9 / 2.3 / 1.9% (normal 2.3)
        30-day  SD 1.138 / 1.129 / 1.115 / 1.089; under -1.5: 9.4 / 9.2 / 9.0 / 8.5%; -2: 4.5 / 4.4 / 4.2 / 3.8%
    Wider pools keep helping on average because 15 training years are noisy, but they blur fast
    seasonal changes such as a monsoon onset, and 30 years halve the noise: +-5 days is the
    compromise. The 30-day windows stay over-dispersed out of sample (about 4% under -2).
  * month: the 12 calendar months, 30 values each (February is 28 days, as above).

Fit: SPI's mixed gamma (McKee et al. 1993; Thom 1966), per cell and window. q is the share of
windows whose total is under ZERO_MM (0.5 mm: what the page prints as 0 mm); alpha and beta
are the maximum-likelihood gamma of the others: Newton's method on ln(a) - digamma(a) = ln(mean)
- mean(ln x), started from Thom's (1958) approximation, then beta = mean / alpha (scale, mm a
day). A window's SPI is invnorm(q + (1 - q) G(x; alpha, beta)). A window under ZERO_MM, where
q > 0, gets the middle of the dry class, invnorm(q / 2) (the "centre of mass" of Stagge et al.
2015), so a dry week where dry weeks are common does not read as extreme: a week without rain
is SPI -1.28 where q = 0.2, -0.97 where q = 1/3 (the most the 2/3 rule allows); hence fewer 7-day
values under -2 than normal. SPI is clipped to +-SPI_CLIP (1 in 740; 30 years cannot tell rarer).
Null, no SPI: where the cell's live 1991-2020 normal over the window ending on the block's end
day is under SPI_ARID_MM_DAY (d7 0.5 mm a day, the live arid mask; d30 and month 0.1, see below;
for months, the month's normal), or
fewer than MIN_NONZERO (2/3) of the values are over ZERO_MM ("20 of 30 years"): 220 of 330
pooled windows, 20 of 30 months.

Since 28 September 2026 the 30-day (six-pentad) and monthly fits reach down to a normal of
SPI_ARID_MM_DAY (0.1 mm a day) instead of the live percent's arid mask (0.5): inland Australia's
wet August 2026 (BoM drought statement, 7 September) had no fit and read blank on every layer.
Checked before adopting, on the cell-windows the change adds (normal 0.1-0.5 mm a day), share
of the 30 years under their own fit below -1 / above +1 (normal 15.9 each), and out of sample
(fit on the odd years, SPI of the even ones; the old population's figures in brackets):
    CPC d30     16.9 / 15.9 (15.9 / 15.8);  out of sample 18.9 / 18.1 (17.7 / 18.1)
    CPC month   16.8 / 16.1 (16.1 / 16.0)
    CHIRPS p6   13.5 / 15.8 (16.1 / 16.0);  out of sample 18.6 / 17.9 (18.2 / 18.1)
    CHIRPS month 12.8 / 15.7 (16.1 / 16.0); out of sample 17.1 / 18.6 (18.1 / 18.2)
The 7-day and single-pentad fits keep 0.5: there most added windows are dry-class years and the
scale is off -- CPC d7 19.8 / 14.7 (out of sample 22.0 / 16.0), CHIRPS p1 4.5 / 13.3 with 4.5%
above +2 (normal 2.3) -- and capping the dry share at 1 in 6 fixed CPC d7 but not CHIRPS p1.
The 14-day kinds (d14, p3; 29 September 2026), the same test:
    CPC d14 at 0.1        17.7 / 15.6 (16.2 / 16.0);  out of sample 19.7 / 17.5 (17.7 / 17.9)
    CPC d14, q <= 1/6     15.4 / 15.8;                out of sample 18.2 / 17.9   -> adopted (SPI_ARID_MAX_Q)
    CHIRPS p3 at 0.1       7.6 / 15.4 (16.0 / 16.2);  out of sample 18.1 / 16.8 (18.3 / 18.1), 6.3% above +2
    CHIRPS p3, dry <= 5   11.0 / 15.1;                out of sample 14.6 / 17.2, 7.4% above +2 -> p3 keeps 0.5
(0.2 and 0.3 mm a day moved neither by more than 2 points.) The cap applies only below 0.5 mm a day, so
every fit the 0.5 rule gives is unchanged.

Storage (PARAMS, gzipped JSON, 1.6 MB): cells lists the output cells (row-major from the south)
with a fit; d7, d14 and d30 (73 ends) and month (12) hold rows "a", "b", "q" parallel to cells:
round(LN_SCALE ln alpha), round(LN_SCALE ln beta) with beta in mm a day, round(1000 q); null =
no fit. load() decodes them; the rounding moves SPI by at most 0.03 (the build prints it).
Build of 28 September 2026: each climatology year's SPI under its own fit, 200 random cells, has
median SD 0.99 (7-day), 1.01 (30-day), 1.02 (month); under -2 1.1% / 2.6% / 2.6% (normal 2.3%).

Usage (local, once; the folder also gets cells_daily.npy, so a re-run skips the NetCDF reads):
    python3 scripts/build_cpc_spi_params.py <folder with precip.1990.nc .. precip.2020.nc>
"""
from __future__ import annotations

import gzip
import json
import math
import sys
from datetime import date
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refresh_rain_anomaly as R  # noqa: E402

PARAMS = R.NORMAL_CACHE.parent / "cpc_spi_params_1991_2020.json.gz"
PSL_FTP_YEAR = "ftp://ftp.cdc.noaa.gov/Datasets/cpc_global_precip/precip.{y}.nc"
FIRST, LAST = 1991, 2020
ENDS = tuple(range(5, 366, 5))           # pentad end days, 365-day calendar
POOL = 5                                 # end days pooled either side of each pentad end
ZERO_MM = 0.5                            # window total under this is the dry class
MIN_NONZERO = 2 / 3                      # share of values over ZERO_MM a fit needs ("20 of 30 years")
SPI_CLIP = 3.0
# A window whose 1991-2020 normal is under this (mm a day) gets no fit. d30 and month reach below the live percent's
# arid mask (R.ARID_MM_DAY); d7 does not (the docstring has the calibration that decided it).
SPI_ARID_MM_DAY = {"d7": R.ARID_MM_DAY, "d14": 0.1, "d30": 0.1, "month": 0.1}
# ... and d14 only where the dry share q is at most this, below R.ARID_MM_DAY (fits at or above it are the 0.5 rule's).
SPI_ARID_MAX_Q = {"d14": 1 / 6}
LN_SCALE = 1000                          # a, b = round(LN_SCALE * ln(alpha, beta)): 0.1% steps
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
KINDS = {"d7": 7, "d14": 14, "d30": 30}
_ND = NormalDist()


# --- pure Python: what the collectors use ------------------------------------

def block(d: date) -> int:
    """Index into ENDS of the d7/d30 row for a window whose last day is d: the nearest pentad end."""
    doy = R._ltm_index(d, 365) + 1       # 365-day calendar: 29 February reads 28 February
    return ((doy + 2) // 5 - 1) % len(ENDS)


def _gammp(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x): series below a + 1, continued fraction above."""
    if x <= 0:
        return 0.0
    lead = math.exp(-x + a * math.log(x) - math.lgamma(a))
    if x < a + 1:
        ap, s, d = a, 1 / a, 1 / a
        while abs(d) >= abs(s) * 1e-15:
            ap += 1
            d *= x / ap
            s += d
        return min(1.0, s * lead)
    tiny, b, c = 1e-300, x + 1 - a, 1e300
    h = d = 1 / b
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1 / (d if abs(d) > tiny else tiny)
        c = b + an / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return max(0.0, 1 - lead * h)


def spi(rate: float, alpha: float, beta: float, q: float, days: int) -> float:
    """SPI of a window's mean rain (mm a day) under one cell-window fit; days = the window's nominal
    length (7, 30, or the month's days), used only for the ZERO_MM dry-class test."""
    if rate * days < ZERO_MM and q > 0:
        p = q / 2
    else:
        p = q + (1 - q) * _gammp(alpha, rate / beta)
    lo = _ND.cdf(-SPI_CLIP)
    return _ND.inv_cdf(min(max(p, lo), 1 - lo))


def load(path: Path = PARAMS) -> dict:
    """PARAMS decoded: {"cells": [...], kind: [[(alpha, beta, q) or None per cell] per end day / month]}."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        c = json.load(fh)
    g = c["grid"]
    if (g["lat0"], g["lon0"], g["step_deg"], g["nlat"], g["nlon"]) != (R.LAT0, R.LON0, R.STEP, R.NLAT, R.NLON):
        raise RuntimeError(f"{path.name}: grid does not match refresh_rain_anomaly.py")
    s = c["ln_scale"]
    kinds = [k for k in (*KINDS, "month") if k in c]
    out = {"cells": c["cells"], "meta": {k: v for k, v in c.items() if k not in kinds}}
    for kind in kinds:
        k = c[kind]
        out[kind] = [[None if a is None else (math.exp(a / s), math.exp(b / s), q / 1000)
                      for a, b, q in zip(ra, rb, rq)] for ra, rb, rq in zip(k["a"], k["b"], k["q"])]
    return out


# --- local build (numpy, scipy, netCDF4) ---------------------------------------

def _cache_cells():
    import numpy as np
    with gzip.open(R.NORMAL_CACHE, "rt", encoding="utf-8") as fh:
        c = json.load(fh)
    cells = np.array(c["cells"])
    bits = ((np.array(c["mask"])[:, None] >> np.arange(R.SUB * R.SUB)) & 1).astype(bool)
    subs = np.array(R.CELL_SUBS)[cells]
    return cells, subs, bits, np.array(c["days"], dtype=np.float64) / 100   # normal: 365 x ncell, mm a day


def _year(path: Path, y: int, subs, bits):
    """One PSL yearly file -> (365 x ncell cell means in mm a day, NaN where missing; file title)."""
    import netCDF4
    import numpy as np
    f = netCDF4.Dataset(path)
    lat, lon = f["lat"][:], f["lon"][:]
    if len(lon) != 720 or abs(lat[35] - 72.25) > 1e-3 or abs(lat[294] + 57.25) > 1e-3 or abs(lon[0] - 0.25) > 1e-3:
        raise RuntimeError(f"{path}: unexpected grid")
    t = netCDF4.num2date(f["time"][:], f["time"].units)
    p = f["precip"]
    p.set_auto_mask(False)
    out = np.full((365, len(subs)), np.nan, np.float32)
    nb = bits.sum(1)
    for i0 in range(0, len(t), 16):                                   # 16 days at a time: 12 MB
        band = np.asarray(p[i0:i0 + 16, 35:295, :])[:, ::-1, :].reshape(-1, R.NSUB)   # rows south first
        for v, tt in zip(band, t[i0:i0 + 16]):
            if tt.year != y:
                raise RuntimeError(f"{path}: day {tt} is not in {y}")
            if (tt.month, tt.day) == (2, 29):
                continue
            v = v[subs]
            ok = (v >= 0) & (v < 1e5)
            m = np.where(bits, v, 0).sum(1, dtype=np.float64) / nb
            m[(bits & ~ok).any(1)] = np.nan
            out[date(2001, tt.month, tt.day).timetuple().tm_yday - 1] = m
    title = f.getncattr("title")
    f.close()
    return out, title


def _daily(folder: Path, subs, bits):
    """(years 1990..LAST) x 365 x ncell, cached in folder/cells_daily.npy."""
    import numpy as np
    files = [folder / f"precip.{y}.nc" for y in range(FIRST - 1, LAST + 1)]
    sig = [[f.name, f.stat().st_size] for f in files]
    npy, side = folder / "cells_daily.npy", folder / "cells_daily.json"
    if npy.exists() and side.exists() and json.loads(side.read_text())["files"] == sig:
        return np.load(npy), json.loads(side.read_text())["titles"]
    S, titles = [], {}
    for y, f in zip(range(FIRST - 1, LAST + 1), files):
        a, titles[y] = _year(f, y, subs, bits)
        S.append(a)
        print(f"[read] {f.name}: {titles[y]}, {int(np.isnan(a).all(1).sum())} days all missing")
    S = np.stack(S)
    np.save(npy, S)
    side.write_text(json.dumps({"files": sig, "titles": titles}))
    return S, titles


def _kept_mean(W, nbar, L):
    """W: years x days x ncell. The live spike filter and NEED_SHARE -> (mean over the last L days kept, spikes)."""
    import numpy as np
    with np.errstate(invalid="ignore"):
        spike = W > np.maximum(R.SPIKE_MM, R.SPIKE_X * nbar)
    keep = (np.isfinite(W) & ~spike)[:, -L:]
    n, s = keep.sum(1), np.where(keep, W[:, -L:], 0).sum(1, dtype=np.float64)
    return np.where(n >= R.NEED_SHARE * L, s / np.maximum(n, 1), np.nan), int(spike.sum())


def _windows(S, N):
    """Mean rain rate per window after the spike filter, NaN where under NEED_SHARE: d7/d14/d30 (years x 365
    end days x ncell) and month (years x 12 x ncell); the windows' normals; spike counts."""
    import numpy as np
    ny, nc = LAST - FIRST + 1, S.shape[2]
    flat = S.reshape(-1, nc)
    yrs = np.arange(1, ny + 1) * 365          # day 0 of each year FIRST..LAST in flat
    first = np.cumsum((0,) + MONTH_DAYS[:-1])
    norms = {"d7": np.stack([N[np.arange(e - 6, e + 1) % 365].mean(0) for e in range(365)]),
             "d14": np.stack([N[np.arange(e - 13, e + 1) % 365].mean(0) for e in range(365)]),
             "d30": np.stack([N[np.arange(e - 29, e + 1) % 365].mean(0) for e in range(365)]),
             "month": np.stack([N[first[m]:first[m] + L].mean(0) for m, L in enumerate(MONTH_DAYS)])}
    out = {k: np.full((ny, 365 if k in KINDS else 12, nc), np.nan, np.float32) for k in (*KINDS, "month")}
    spikes = {"d30": 0, "month": 0}
    for e in range(365):
        W = flat[(yrs + e)[:, None] + np.arange(-29, 1)]          # ny x 30 x ncell
        out["d30"][:, e], n = _kept_mean(W, norms["d30"][e], 30)
        out["d7"][:, e] = _kept_mean(W, norms["d30"][e], 7)[0]  # as live: the 30-day window's normal
        out["d14"][:, e] = _kept_mean(W, norms["d30"][e], 14)[0]  # ditto
        spikes["d30"] += n
    for m, L in enumerate(MONTH_DAYS):
        out["month"][:, m], n = _kept_mean(flat[(yrs + first[m])[:, None] + np.arange(L)], norms["month"][m], L)
        spikes["month"] += n
    return out, norms, spikes


def _fit_blocks(Rk, pool: int, zero_rate):
    """years x 365 end days x ncell -> _fit (73 x ncell) of each end e over end days e-pool..e+pool."""
    import numpy as np
    parts = []
    for e in ENDS:
        X = Rk[:, [(e - 1 + k) % 365 for k in range(-pool, pool + 1)]].reshape(1, -1, Rk.shape[2])
        parts.append(_fit(X, zero_rate, X.shape[1]))
    return tuple(np.concatenate(z) for z in zip(*parts))


def _fit(X, zero_rate, nominal):
    """X: windows x samples x ncell (NaN = missing). -> alpha, beta, q, fitted (bool), all windows x ncell."""
    import numpy as np
    from scipy.special import digamma, polygamma
    ok = np.isfinite(X)
    with np.errstate(invalid="ignore", divide="ignore"):
        pos = ok & (X >= zero_rate)
        n, n1 = ok.sum(1), pos.sum(1)
        mean = np.where(pos, X, 0).sum(1, dtype=np.float64) / n1
        A = np.log(mean) - np.where(pos, np.log(np.where(pos, X, 1)), 0).sum(1, dtype=np.float64) / n1
        A = np.where(A > 1e-9, A, np.nan)             # A = 0 (one value, or all equal): no gamma; keeps Newton finite
        a = (1 + np.sqrt(1 + 4 * A / 3)) / (4 * A)                # Thom's approximation
        for _ in range(50):                                       # Newton on the ML equation
            nxt = a - (np.log(a) - digamma(a) - A) / (1 / a - polygamma(1, a))
            a = np.where(nxt > 0, nxt, a / 2)
        resid = np.abs(np.log(a) - digamma(a) - A)
        fitted = (n1 >= MIN_NONZERO * nominal) & np.isfinite(a) & (resid < 1e-8 * np.maximum(A, 1e-3))
        return a, mean / a, (n - n1) / n, fitted


def _quant(a, b, q, fitted):
    import numpy as np
    qa = np.where(fitted, np.rint(LN_SCALE * np.log(np.where(fitted, a, 1))), 0).astype(int)
    qb = np.where(fitted, np.rint(LN_SCALE * np.log(np.where(fitted, b, 1))), 0).astype(int)
    qq = np.where(fitted, np.rint(1000 * np.where(fitted, q, 0)), 0).astype(int)
    return qa, qb, qq


def _spi_np(X, a, b, q, zero_rate):
    """spi() for arrays (numpy/scipy), same conventions."""
    import numpy as np
    from scipy.special import gammainc, ndtr, ndtri
    with np.errstate(invalid="ignore", divide="ignore"):
        G = gammainc(a, np.maximum(X, 0) / b)
        p = np.where((X < zero_rate) & (q > 0), q / 2, q + (1 - q) * G)
    lo = ndtr(-SPI_CLIP)
    return np.where(np.isfinite(X), ndtri(np.clip(p, lo, 1 - lo)), np.nan)


def _zero_rate(kind):
    import numpy as np
    return ZERO_MM / (np.array(MONTH_DAYS, float)[:, None] if kind == "month" else KINDS[kind])


def build(folder: Path) -> int:
    import numpy as np
    cells, subs, bits, N = _cache_cells()
    S, titles = _daily(folder, subs, bits)
    win, norms, spikes = _windows(S, N)
    fits, samples = {}, {}
    for kind in (*KINDS, "month"):
        if kind == "month":
            a, b, q, fitted = _fit(np.moveaxis(win["month"], 0, 1), _zero_rate(kind)[..., None], LAST - FIRST + 1)
        else:
            a, b, q, fitted = _fit_blocks(win[kind], POOL, _zero_rate(kind))
        normal = norms[kind][[e - 1 for e in ENDS]] if kind != "month" else norms["month"]
        fitted &= normal >= SPI_ARID_MM_DAY[kind]
        if kind in SPI_ARID_MAX_Q:
            fitted &= (normal >= R.ARID_MM_DAY) | (q <= SPI_ARID_MAX_Q[kind] + 1e-9)
        fits[kind] = (a, b, q, fitted)
        samples[kind] = win[kind][:, [e - 1 for e in ENDS]] if kind != "month" else win["month"]  # years x rows x cells
    keep = np.logical_or.reduce([f[3].any(0) for f in fits.values()])      # cells with at least one fit
    out = {
        "what": "SPI parameters (mixed gamma: probability of a dry window q, shape alpha, scale beta) of the "
                "1991-2020 NOAA CPC Unified gauge analysis per 2.5-degree land cell of refresh_rain_anomaly.py, "
                "for the 7 and 30 days ending on each pentad end day and for each calendar month",
        "source_files": PSL_FTP_YEAR.format(y="{1990..2020}"), "source_titles": titles,
        "normal": f"data/ref/{R.NORMAL_CACHE.name}",
        "built": date.today().isoformat(),
        "built_by": "python3 scripts/build_cpc_spi_params.py <folder of precip.1990.nc .. precip.2020.nc>",
        "method": "see the docstring of scripts/build_cpc_spi_params.py",
        "grid": {"lat0": R.LAT0, "lon0": R.LON0, "step_deg": R.STEP, "nlat": R.NLAT, "nlon": R.NLON},
        "value": "the window's mean rain in mm a day over the days kept (spike filter as the live collector)",
        "ends": list(ENDS), "pool_end_days": POOL,
        "block_rule": "a window ending on day-of-year d (365-day calendar, 29 February reads 28 February) "
                      "uses d7/d14/d30 row ((d + 2) // 5 - 1) % 73, the nearest end; month rows are January..December",
        "zero_mm": ZERO_MM, "min_nonzero_share": MIN_NONZERO, "arid_mm_day": R.ARID_MM_DAY,
        "spi_arid_mm_day": SPI_ARID_MM_DAY, "spi_arid_max_q": SPI_ARID_MAX_Q, "spi_clip": SPI_CLIP,
        "ln_scale": LN_SCALE,
        "encoding": "cells: output cell indexes, row-major from the southern edge, with at least one fit. "
                    "Per kind, a/b/q: one row per end day (d7, d30) or month, parallel to cells. alpha = "
                    "exp(a / ln_scale), beta = exp(b / ln_scale) in mm a day, q = q / 1000; null = no fit "
                    "(normal under spi_arid_mm_day for that kind, or fewer than min_nonzero_share of the values "
                    "over zero_mm).",
        "spi_rule": "p = q / 2 if the window total < zero_mm and q > 0, else q + (1 - q) * P(alpha, x / beta) "
                    "with x the mean rain in mm a day; SPI = inverse normal of p, clipped to +-spi_clip",
        "cells": cells[keep].tolist(),
    }
    for kind, (a, b, q, fitted) in fits.items():
        qa, qb, qq = _quant(a, b, q, fitted)
        f = fitted[:, keep]
        out[kind] = {k: [[int(v) if ok else None for v, ok in zip(row, frow)] for row, frow in zip(arr[:, keep], f)]
                     for k, arr in (("a", qa), ("b", qb), ("q", qq))}
        out[kind]["n_fits"] = int(f.sum())
    PARAMS.parent.mkdir(parents=True, exist_ok=True)
    PARAMS.write_bytes(gzip.compress(json.dumps(out, separators=(",", ":")).encode(), 9, mtime=0))
    print(f"[OK] wrote {PARAMS} ({int(keep.sum())} cells, "
          + ", ".join(f"{k} {out[k]['n_fits']} fits" for k in fits) + f", {PARAMS.stat().st_size / 1e6:.2f} MB)")
    _report(S, N, cells, win, norms, spikes, fits, samples)
    return 0


# --- checks (printed) ------------------------------------------------------------

def _pcts(v, ps=(5, 50, 95)):
    import numpy as np
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return "/".join(f"{x:+.2f}" for x in np.percentile(v, ps)) if len(v) else "n/a"


def _report(S, N, cells, win, norms, spikes, fits, samples):
    import numpy as np
    from scipy.special import gammainc
    from scipy.stats import kstwo
    rng = np.random.default_rng(20260928)
    ny = LAST - FIRST + 1
    # 1. The cell series against the live normal (PSL's LTM of the same files, averaged the same way).
    full = ~np.isnan(S[1:]).any(0)                                   # cell-days with all 30 years
    d = np.abs(np.nanmean(S[1:], 0) - N)[full]
    short = (np.isnan(S[1:]).mean(1) > 0.5).sum(0)
    print(f"[check] 1991-2020 mean of the cell series vs the live normal cache, {100 * full.mean():.1f}% of "
          f"cell-days with all 30 years: |diff| median {np.median(d):.4f}, max {d.max():.4f} mm/day; cells missing "
          f"12+ years: {int((short >= 12).sum())}, 1-11 years: {int(((short > 0) & (short < 12)).sum())}")
    # 2. Retrospective V1.0 (to 2005) against real time (from 2006), by annual mean rate.
    wet = (N.mean(0) >= R.ARID_MM_DAY) & (short == 0)
    ratio = np.nanmean(S[16:], (0, 1))[wet] / np.nanmean(S[1:16], (0, 1))[wet]
    print(f"[check] 2006-2020 / 1991-2005 mean rain, {int(wet.sum())} non-arid cells with all years: 5/25/50/75/95th "
          f"pct {'/'.join(f'{x:.2f}' for x in np.percentile(ratio, (5, 25, 50, 75, 95)))}")
    print(f"[check] spike filter, 1991-2020: {spikes['month']} cell-days dropped in month windows, "
          f"{spikes['d30']} cell-day-window hits over the 30-day windows")
    try:  # 3. The live code on CPC's own real-time file for one day, against this build on PSL's file.
        day = date(2020, 7, 15)
        live = R._cell_day(R._cpc_day(day), R._cache_normal([day])[day])
        mine = S[day.year - FIRST + 1, R._ltm_index(day, 365)]
        dd = np.abs([live[k][0] - m for k, m in zip(cells, mine) if live[k] is not None and np.isfinite(m)])
        print(f"[check] {day} CPC RT file through R._cell_day vs PSL's precip.2020.nc here: {len(dd)} cells, "
              f"|diff| median {np.median(dd):.4f}, 99th pct {np.percentile(dd, 99):.3f}, max {dd.max():.2f} mm")
    except Exception as e:  # noqa: BLE001 -- a network check only
        print(f"[check] live-code comparison skipped: {type(e).__name__}: {e}")
    # 4. Pure-Python spi() against the numpy/scipy version.
    ts = [(float(rng.uniform(0, 20)), float(np.exp(rng.uniform(-1, 5))), float(np.exp(rng.uniform(-4, 2))),
           float(rng.choice([0, 0.1, 0.3])), 7) for _ in range(3000)]
    err = max(abs(spi(*t) - float(_spi_np(np.array(t[0]), t[1], t[2], t[3], ZERO_MM / 7))) for t in ts)
    print(f"[check] pure-Python spi() vs scipy on 3,000 random cases: max |diff| {err:.2e}")
    for kind, (a, b, q, f) in fits.items():
        X, zr = samples[kind], _zero_rate(kind)                     # years x windows x cells
        qa, qb, qq = _quant(a, b, q, f)
        raw = np.where(f, _spi_np(X, a, b, q, zr), np.nan)
        dec = np.where(f, _spi_np(X, np.exp(qa / LN_SCALE), np.exp(qb / LN_SCALE), qq / 1000, zr), np.nan)
        print(f"[check] {kind}: {int(f.sum())} cell-windows fitted of {f.size} "
              f"(alpha 5/50/95th pct {_pcts(a[f])}, q>0 in {100 * (q[f] > 0).mean():.0f}%); quantisation moves "
              f"SPI by at most {np.nanmax(np.abs(raw - dec)):.4f}")
        # 5. Each climatology year's SPI under its own fit (the window ending on the block's own end day).
        cw = np.nonzero(f.any(0))[0]
        pick = rng.choice(cw, size=min(200, len(cw)), replace=False)
        z, ok = dec[:, :, pick], f[:, pick]
        m, sd, allz = np.nanmean(z, 0), np.nanstd(z, 0, ddof=1), z[:, ok]
        tails = "  ".join(f"<{t:g} {100 * np.mean(allz < t):.1f}% ({100 * _ND.cdf(t):.1f})" for t in (-1, -1.5, -2)) \
            + "  " + "  ".join(f">{t:g} {100 * np.mean(allz > t):.1f}%" for t in (1, 1.5, 2))
        print(f"[check]   200 cells, SPI of the 30 years per cell-window: mean 5/50/95th pct {_pcts(m[ok])}, "
              f"SD {_pcts(sd[ok]).replace('+', '')}; share {tails}")
        # 6. Goodness of fit: KS of the 30 years' non-dry values against the fitted gamma, every fit.
        Xs = np.sort(np.where(X >= zr, X, np.nan)[:, f], 0)          # years x fits
        n = np.isfinite(Xs).sum(0)
        F = gammainc(np.exp(qa / LN_SCALE)[f], Xs / np.exp(qb / LN_SCALE)[f])
        i = np.arange(1, ny + 1)[:, None]
        with np.errstate(invalid="ignore"):
            D = np.nanmax(np.maximum(i / n - F, F - (i - 1) / n), 0)
        p = kstwo.sf(D, n)
        print(f"[check]   KS, 30 years' non-dry values vs the fit: p < 0.05 in {100 * np.mean(p < 0.05):.1f}% "
              f"of {len(p)} fits, p < 0.01 in {100 * np.mean(p < 0.01):.1f}% (parameters fitted on these years "
              "and their neighbour days, so the test is lenient)")
    # 7. Pooling, out of sample: fit on the odd years, score the even years' windows ending on each end day.
    ends = [e - 1 for e in ENDS]
    for kind in KINDS:
        rk, zr = win[kind], _zero_rate(kind)
        tr, te = rk[0::2], rk[1::2][:, ends]
        zs = {}
        for pool in (0, 2, 5, 10):
            a, b, q, f = _fit_blocks(tr, pool, zr)
            keep = f & (norms[kind][ends] >= SPI_ARID_MM_DAY[kind])
            if kind in SPI_ARID_MAX_Q:
                keep &= (norms[kind][ends] >= R.ARID_MM_DAY) | (q <= SPI_ARID_MAX_Q[kind] + 1e-9)
            zs[pool] = np.where(keep, _spi_np(te, a, b, q, zr), np.nan)
        common = np.logical_and.reduce([np.isfinite(z).any(0) for z in zs.values()])
        for pool, z in zs.items():
            z = z[:, common][np.isfinite(z[:, common])]
            print(f"[check] {kind} pool +-{pool}, fit on the odd years, SPI of the even years ({int(common.sum())} "
                  f"cell-windows fitted with every pool): SD {z.std():.3f}, <-1.5 {100 * np.mean(z < -1.5):.1f}% "
                  f"(normal 6.7), <-2 {100 * np.mean(z < -2):.1f}% (2.3), >+1.5 {100 * np.mean(z > 1.5):.1f}%, "
                  f">+2 {100 * np.mean(z > 2):.1f}%")
    _current()


def _current():
    """SPI of the current windows in data/rain_anomaly.json and rain_months.json at four places."""
    P = load()
    idx = {k: i for i, k in enumerate(P["cells"])}
    base = R.NORMAL_CACHE.parent.parent
    ra = json.loads((base / "rain_anomaly.json").read_text())["data"]
    rm = json.loads((base / "rain_months.json").read_text())["data"]
    for name, lat, lon in (("Iowa", 41.25, -93.75), ("Bavaria", 48.75, 11.25), ("NSW", -33.75, 148.75),
                           ("central India", 21.25, 78.75)):
        k = round((lat - R.LAT0) / R.STEP) * R.NLON + round((lon - R.LON0) / R.STEP)
        parts = []
        for label, kind, blk, days in (("30d", "d30", ra, 30), ("14d", "d14", ra.get("d14"), 14), ("7d", "d7", ra["week"], 7)):
            if blk is None or kind not in P:
                continue
            end = date.fromisoformat(blk.get("end") or ra["window"]["end"])
            mm, nm, n = blk["mm"][k], blk["norm_mm"][k], blk.get("days") or ra["window"]["days"]
            fit = P[kind][block(end)][idx[k]] if k in idx else None
            parts.append(f"{label} n/a" if mm is None or fit is None else f"{label} {mm}/{nm} mm ({blk['anom'][k]}%) "
                         f"SPI {spi(mm / n, *fit, days):+.2f}")
        for mo in rm["months"][-3:]:
            m = int(mo["month"][5:]) - 1
            mm = mo["cpc"]["mm"][k]
            fit = P["month"][m][idx[k]] if k in idx else None
            parts.append(f"{mo['month']} " + ("n/a" if mm is None or fit is None else
                         f"{mo['cpc']['anom'][k]}% SPI {spi(mm / mo['days'], *fit, MONTH_DAYS[m]):+.2f}"))
        print(f"[check] {name} ({lat}, {lon}), ending {ra['window']['end']}: " + "; ".join(parts))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split("Usage")[1])
    raise SystemExit(build(Path(sys.argv[1])))
