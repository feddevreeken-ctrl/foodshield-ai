#!/usr/bin/env python3
"""spi.py -- the Standardized Precipitation Index of an observed rain window, pure Python (stdlib only).

Why. The El Nino Ocean map shows observed rain on land at three kinds of stop: the last 7 days,
the last 30 days and each of the last six calendar months (refresh_rain_anomaly.py,
chirps_rain_fill.py, refresh_rain_months.py). As a percent of the 1991-2020 mean these do not
read alike: a week's rain varies far more from year to year than a month's, so -40% is an
ordinary week and a rare month, and the week layer looked calmer than the 30-day one. The
Standardized Precipitation Index (McKee, Doesken and Kleist 1993; the drought index WMO
recommends, WMO-No. 1090, 2012) puts every stop on one scale: the probability of the window's
rain under the cell's own 1991-2020 distribution for the same window at the same time of year,
read off a standard normal. SPI -1 / -1.5 / -2 = moderately / severely / extremely dry (about
16% / 7% / 2% of years that dry or drier), +1 / +1.5 / +2 the wet mirror.

This module only EVALUATES. The fits were made once, locally, with numpy and scipy:
  * CPC gauge cells: scripts/build_cpc_spi_params.py -> data/ref/cpc_spi_params_1991_2020.json.gz
    (windows d7, d14 and d30 ending on each of 73 pentad end days of a 365-day year, pooled +-5 days;
    calendar months). SPI rule of that build (its docstring has the reasons):
        total = rate x nominal days (7, 14, 30, or the month's days with February 28);
        total < zero_mm (0.5 mm) and q > 0:  H = q / 2          (middle of the dry class)
        otherwise:                           H = q + (1 - q) P(alpha, rate / beta)
        H clipped to [Phi(-3), Phi(3)], SPI = Phi^-1(H).
  * CHIRPS fill cells: scripts/build_chirps_spi_params.py -> data/ref/chirps3_spi_params_1991_2020.json.gz
    (windows p1 = one pentad, p3 = three and p6 = six pentads, ending at each of the 72 CHIRPS pentads of
    the year; calendar months). SPI rule of that build:
        total < zero_mm (1 mm, the BIL archive's whole-mm floor):  H = (dry + 1) / (2 (n + 1))
        otherwise: q = dry / n, H = q + (1 - q) P(shape, x shape / wet_mean), x = total / days
        H clipped to [1e-12, 1 - 1e-12], SPI = Phi^-1(H).
  Each parameter set is read with the rule it was fitted and checked under (q, the zero
  threshold and the null rules are part of each fit), so the two are NOT merged here. They differ
  only for a window below the zero threshold, which gets the middle of its dry class under both
  rules, and in the wet-year minimum each build applied before storing a fit (CPC 2/3 of values,
  CHIRPS 15 of 30). Had the CPC fits used CHIRPS's (d + 1) / (2 (n + 1)) instead of q / 2, a
  rainless window would read higher by 0.016 (median over fits with dry windows; 95th percentile
  0.14) for 7 days, 0.04 (0.22) for 30 days and 0.28 for a month with one dry year in 30. Zero
  thresholds, clip and year counts are read from each file, not repeated here.

Window to parameter row (the time of year closest to the window's end):
  * CPC d7/d14/d30: cpc_row(end) = ((doy + 2) // 5 - 1) % 73 on a 365-day calendar (29 February reads
    28 February): the pentad end day nearest the window's last day. Months: row month - 1.
  * CHIRPS p1/p3/p6: the fill's windows ARE CHIRPS pentads, so the row is the pentad of year of the
    window's last pentad, minus one. Months: row month - 1.

Numerics:
  * gammp(a, x), the regularised lower incomplete gamma P(a, x): the series for x < a + 1 and the
    Lentz continued fraction for Q otherwise (Numerical Recipes, gser / gcf), to 1e-15.
  * The inverse standard normal is statistics.NormalDist().inv_cdf, the standard library's
    implementation of Wichura's AS241 (relative error about 1e-16), equivalent to and more
    accurate than Acklam's rational approximation (1.15e-9); stdlib since Python 3.8.
  * Checked against scipy 1.18 (gammainc, ndtri, and SPI built from scipy.stats) on 10,000 random
    draws per function (python3 scripts/spi.py in a venv with numpy and scipy; 28 September 2026):
    max |error| gammp 9.3e-13, norm_ppf 3.6e-15, spi_cpc 1.2e-12, spi_chirps 9.4e-13. On 21,600
    cases each from the stored fits, spi_cpc and spi_chirps equal the builders' own spi() exactly.

Output: to_x100(spi) = SPI x 100 as an integer, clamped to -300..300. Past about +-1.85 (1 in 31,
the rarest rank 30 years give) a value rests on the fitted gamma's tail rather than on ranked
years, and the fits cannot tell anything past +-3 apart, so -300 / 300 mean "-3 or below" /
"+3 or above".
"""
from __future__ import annotations

import gzip
import json
import math
from datetime import date
from pathlib import Path
from statistics import NormalDist

REF = Path(__file__).resolve().parent.parent / "data" / "ref"
CPC_PARAMS = REF / "cpc_spi_params_1991_2020.json.gz"
CHIRPS_PARAMS = REF / "chirps3_spi_params_1991_2020.json.gz"
X100_CLAMP = 300
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)   # the CPC fit's nominal month lengths
_ND = NormalDist()
_loaded: dict = {}


# --- numerics ------------------------------------------------------------------

def gammp(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x), a > 0: series below a + 1, continued fraction above."""
    if x <= 0:
        return 0.0
    lead = math.exp(-x + a * math.log(x) - math.lgamma(a))
    if x < a + 1:  # P = lead * sum_n x^n / (a (a+1) .. (a+n))
        ap, d = a, 1.0 / a
        s = d
        for _ in range(100000):
            ap += 1
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-15:
                break
        return min(1.0, s * lead)
    tiny = 1e-300  # Q = lead * continued fraction, modified Lentz
    b = x + 1 - a
    c, d = 1 / tiny, 1 / b
    h = d
    for i in range(1, 100000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1 / (d if abs(d) > tiny else tiny)
        c = b + an / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return max(0.0, 1.0 - lead * h)


def norm_ppf(p: float) -> float:
    """Inverse standard normal (AS241 via the standard library), 0 < p < 1."""
    return _ND.inv_cdf(p)


def to_x100(z: float | None) -> int | None:
    """SPI -> integer SPI x 100, clamped to -300..300; None stays None."""
    if z is None or z != z:
        return None
    return max(-X100_CLAMP, min(X100_CLAMP, int(round(100 * z))))


# --- parameter files -------------------------------------------------------------

def params_id() -> str:
    """First 12 hex of the sha256 of both parameter files. A collector that caches SPI (rain_months.json,
    rain_weeks.json) stores it beside the values and recomputes them when the fits have been rebuilt."""
    import hashlib
    h = hashlib.sha256()
    for path in (CPC_PARAMS, CHIRPS_PARAMS):
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _load(path: Path, grid: tuple) -> dict:
    """The gzipped JSON, read once per run; grid = (lat0, lon0, step, nlat, nlon) the caller writes on."""
    if path not in _loaded:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            _loaded[path] = json.load(fh)
    c = _loaded[path]
    g = c["grid"]
    if (g["lat0"], g["lon0"], g["step_deg"], g["nlat"], g["nlon"]) != tuple(grid):
        raise RuntimeError(f"{path.name}: grid {g} is not {grid}")
    return c


def cpc_row(end: date) -> int:
    """d7/d14/d30 row of a CPC window whose last day is `end`: the nearest pentad end day, 365-day calendar."""
    doy = date(2001, end.month, 28 if (end.month, end.day) == (2, 29) else end.day).timetuple().tm_yday
    return ((doy + 2) // 5 - 1) % 73


def cpc_fits(grid: tuple, kind: str, row: int, path: Path = CPC_PARAMS) -> tuple[dict, dict]:
    """({output cell: (alpha, beta mm/day, q)} for one row of kind 'd7', 'd14', 'd30' or 'month'; the file's settings)."""
    c = _load(path, grid)
    s, k = c["ln_scale"], c[kind]
    fits = {cell: (math.exp(a / s), math.exp(b / s), q / 1000)
            for cell, a, b, q in zip(c["cells"], k["a"][row], k["b"][row], k["q"][row]) if a is not None}
    meta = {"zero_mm": c["zero_mm"], "clip": c["spi_clip"], "end_day": c["ends"][row] if kind != "month" else None,
            "pool_end_days": c.get("pool_end_days")}
    return fits, meta


def spi_cpc(rate: float, fit: tuple, days: int, zero_mm: float, clip: float) -> float:
    """SPI of a CPC window's mean rain (mm a day) under its fit; days = the nominal window length."""
    alpha, beta, q = fit
    if rate * days < zero_mm and q > 0:
        h = q / 2
    else:
        h = q + (1 - q) * gammp(alpha, max(rate, 0.0) / beta)
    lo = _ND.cdf(-clip)
    return norm_ppf(min(max(h, lo), 1 - lo))


def chirps_fits(grid: tuple, kind: str, row: int, path: Path = CHIRPS_PARAMS) -> tuple[dict, dict]:
    """({output cell: (shape, wet mean mm/day, dry years)} for one row of kind 'p1', 'p6' or 'month'; settings)."""
    c = _load(path, grid)
    k = c[kind]
    fits = {cell: (sh / 1000, wm / 1000, dy)
            for cell, sh, wm, dy in zip(c["cells"], k["shape"][row], k["wet_mean"][row], k["dry"][row])
            if sh is not None and wm is not None}
    return fits, {"zero_mm": c["zero_mm"], "n_years": c["n_years"]}


def spi_chirps(total_mm: float, days: float, fit: tuple, zero_mm: float, n_years: int) -> float:
    """SPI of a CHIRPS window total (cell mean, mm over `days` days) under its fit."""
    shape, wet_mean, dry = fit
    if total_mm < zero_mm:
        # Never less dry than a total just above the threshold: where the record has no dry window the
        # plotting position (dry+1)/(2(n+1)) could sit above the gamma CDF at zero_mm and invert the order
        # (independent verification, 2026-09-28).
        q = dry / n_years
        h = min((dry + 1) / (2 * (n_years + 1)), q + (1 - q) * gammp(shape, zero_mm / days * shape / wet_mean))
    else:
        q = dry / n_years
        h = q + (1 - q) * gammp(shape, total_mm / days * shape / wet_mean)
    return norm_ppf(min(max(h, 1e-12), 1 - 1e-12))


# --- local check (numpy + scipy, never in CI) -----------------------------------------

def check_against_scipy(n: int = 10000, seed: int = 20260928) -> dict:
    """Max |error| of gammp, norm_ppf, spi_cpc and spi_chirps against scipy on n random draws each."""
    import numpy as np
    from scipy import stats
    from scipy.special import gammainc, ndtri
    rng = np.random.default_rng(seed)
    out = {}
    a = np.exp(rng.uniform(np.log(0.05), np.log(2000), n))          # shapes 0.05..2000 (the files: 0.2..1100)
    x = a * np.exp(rng.normal(0, 0.8, n))                             # x around a, both tails
    out["gammp"] = max(abs(gammp(float(ai), float(xi)) - float(gammainc(ai, xi))) for ai, xi in zip(a, x))
    p = np.concatenate([rng.uniform(1e-12, 1 - 1e-12, n // 2), 10.0 ** rng.uniform(-12, -1, n - n // 2)])
    out["norm_ppf"] = max(abs(norm_ppf(float(pi)) - float(ndtri(pi))) for pi in p)
    clip, zc = 3.0, 0.5
    lo = stats.norm.cdf(-clip)
    err = 0.0
    for _ in range(n):
        al, be = float(np.exp(rng.uniform(-1.6, 7))), float(np.exp(rng.uniform(-4, 3)))
        q, days = float(rng.choice([0, 0.05, 0.2, 0.33])), int(rng.choice([7, 30, 31, 28]))
        rate = float(al * be * np.exp(rng.normal(0, 1)) * (rng.random() > 0.05))
        h = q / 2 if rate * days < zc and q > 0 else q + (1 - q) * stats.gamma.cdf(rate, al, scale=be)
        ref = float(stats.norm.ppf(np.clip(h, lo, 1 - lo)))
        err = max(err, abs(spi_cpc(rate, (al, be, q), days, zc, clip) - ref))
    out["spi_cpc"] = err
    err = 0.0
    for _ in range(n):
        sh, wm = float(np.exp(rng.uniform(-1.1, 6))), float(np.exp(rng.uniform(-1, 3.5)))
        dry, days = int(rng.integers(0, 16)), float(rng.choice([5, 31, 30, 3]))
        tot = float(wm * days * np.exp(rng.normal(0, 1.2)) * (rng.random() > 0.05))
        if tot < 1.0:
            h = (dry + 1) / 62
        else:
            h = dry / 30 + (1 - dry / 30) * stats.gamma.cdf(tot / days, sh, scale=wm / sh)
        ref = float(stats.norm.ppf(np.clip(h, 1e-12, 1 - 1e-12)))
        err = max(err, abs(spi_chirps(tot, days, (sh, wm, dry), 1.0, 30) - ref))
    out["spi_chirps"] = err
    return out


if __name__ == "__main__":
    for k, v in check_against_scipy().items():
        print(f"[check] {k}: max |error| against scipy on 10,000 random draws {v:.2e}")
