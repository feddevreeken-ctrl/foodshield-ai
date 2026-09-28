#!/usr/bin/env python3
"""precl_model.py -- evaluate the PREC/L SPI fits and ENSO rain model in pure Python (stdlib only).

Why. Every rain layer on the El Nino Ocean map (observed months, the 30-day window, the Outlook)
should read rain on one drought scale and one idea of what El Nino does to it. The fits were made
once, locally, by scripts/build_precl_enso_model.py (numpy, scipy) into
data/ref/precl_enso_model.json.gz; the refresh scripts run in GitHub Actions with only requests and
openpyxl, so this module reads that file and evaluates it with the standard library, reusing
spi.gammp and spi.norm_ppf (checked there against scipy).

Windows: kind "m1" = one calendar month's total, "m3" = the total of that month and the two before
(m3 of January = Nov + Dec + Jan); `month` is always the window's LAST month, 1..12. Totals are in mm
over the nominal days (February 28): mm a day x days. `cell` is the rain-grid index (row-major from
the southern edge, lat0 -56.25, lon0 -178.75, 2.5 degrees, 52 x 144).

  spi_from_total(cell, kind, month, total_mm) -> SPI (None: no fit). The CPC rule of spi.spi_cpc:
      total < zero_mm and q > 0: H = q / 2; else H = q + (1 - q) P(alpha, total / beta);
      H clipped to Phi(+-3); SPI = Phi^-1(H).
  total_from_spi(cell, kind, month, spi) -> the total (mm) with that SPI: the inverse, by bisection
      on spi.gammp. An SPI inside the dry class (H <= q) returns 0.0.
  enso_predict(cell, kind, month, oni) -> (spi, resid_sd, skilful) or None: the fitted line
      SPI = c0 + c1 x ONI, with ONI capped to the range of the fit years for that window (a very
      strong forecast beyond the strongest El Nino on record is read at that record, not
      extrapolated). resid_sd is the fit's residual SD in SPI units; skilful is the stored flag
      (rule in the file: enso_rule.skill_rule).

Self-test (local, numpy and scipy): python3 scripts/precl_model.py compares spi_from_total and
total_from_spi with scipy.stats on random stored fits and random totals and prints the max error.
"""
from __future__ import annotations

import gzip
import json
import math
import sys
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spi import gammp, norm_ppf  # noqa: E402

PARAMS = Path(__file__).resolve().parent.parent / "data" / "ref" / "precl_enso_model.json.gz"
GRID = (-56.25, -178.75, 2.5, 52, 144)
_ND = NormalDist()
_cache: dict = {}


def load(path: Path = PARAMS) -> dict:
    """The model file, read once per run, with a cell -> position map; the grid is checked."""
    if path not in _cache:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            m = json.load(fh)
        g = m["grid"]
        if (g["lat0"], g["lon0"], g["step_deg"], g["nlat"], g["nlon"]) != GRID:
            raise RuntimeError(f"{path.name}: grid {g} is not the rain grid {GRID}")
        m["_pos"] = {c: i for i, c in enumerate(m["cells"])}
        _cache[path] = m
    return _cache[path]


def fit(cell: int, kind: str, month: int, path: Path = PARAMS) -> tuple[float, float, float] | None:
    """(alpha, beta in mm, q) of one cell and window, or None where the cell has no fit."""
    m = load(path)
    i = m["_pos"].get(cell)
    if i is None:
        return None
    t, s = m["spi"][kind], m["spi_rule"]["ln_scale"]
    a = t["a"][month - 1][i]
    if a is None:
        return None
    return math.exp(a / s), math.exp(t["b"][month - 1][i] / s), t["q"][month - 1][i] / 1000


def spi_of(total: float, f: tuple, zero_mm: float, clip: float) -> float:
    """SPI of a window total under one fit (alpha, beta, q)."""
    alpha, beta, q = f
    h = q / 2 if (total < zero_mm and q > 0) else q + (1 - q) * gammp(alpha, max(total, 0.0) / beta)
    lo = _ND.cdf(-clip)
    return norm_ppf(min(max(h, lo), 1 - lo))


def total_of(z: float, f: tuple, clip: float) -> float:
    """The window total (mm) whose SPI is z under fit f: inverse of spi_of by bisection on gammp."""
    alpha, beta, q = f
    h = _ND.cdf(min(max(z, -clip), clip))
    if h <= q:
        return 0.0
    g = (h - q) / (1 - q)
    lo, hi = 0.0, alpha * beta
    while gammp(alpha, hi / beta) < g:
        lo, hi = hi, hi * 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if gammp(alpha, mid / beta) < g:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-12 * hi:
            break
    return (lo + hi) / 2


def spi_from_total(cell: int, kind: str, month: int, total_mm: float, path: Path = PARAMS) -> float | None:
    """SPI of a window total (mm) at one cell; None where there is no fit."""
    f = fit(cell, kind, month, path)
    if f is None:
        return None
    r = load(path)["spi_rule"]
    return spi_of(total_mm, f, r["zero_mm"], r["spi_clip"])


def total_from_spi(cell: int, kind: str, month: int, spi: float, path: Path = PARAMS) -> float | None:
    """The window total (mm) at one cell that has this SPI; None where there is no fit."""
    f = fit(cell, kind, month, path)
    if f is None:
        return None
    return total_of(spi, f, load(path)["spi_rule"]["spi_clip"])


def enso_predict(cell: int, kind: str, month: int, oni: float, path: Path = PARAMS):
    """(predicted SPI, residual SD, skilful) of one cell and window at this ONI, capped to the fit's ONI range."""
    m = load(path)
    i = m["_pos"].get(cell)
    if i is None:
        return None
    e, sc = m["enso"][kind], m["enso_rule"]["scales"]
    c0 = e["c0"][month - 1][i]
    if c0 is None:
        return None
    lo, hi = m["enso_rule"]["oni_range"][kind][month - 1]
    x = min(max(oni, lo), hi)
    return (c0 / sc["c0"] + e["c1"][month - 1][i] / sc["c1"] * x, e["sd"][month - 1][i] / sc["sd"],
            bool(e["skill"][month - 1][i]))


# --- local check (numpy + scipy, never in CI) -----------------------------------------

def check_against_scipy(n: int = 20000, seed: int = 20260928) -> dict:
    """Max |error| of spi_from_total and total_from_spi against scipy.stats on random stored fits."""
    import numpy as np
    from scipy import stats
    rng = np.random.default_rng(seed)
    m = load()
    r = m["spi_rule"]
    lo = stats.norm.cdf(-r["spi_clip"])
    err_spi = err_tot = err_rel = err_pred = 0.0
    for _ in range(n):
        kind = str(rng.choice(["m1", "m3"]))
        month = int(rng.integers(1, 13))
        cell = int(rng.choice(m["cells"]))
        f = fit(cell, kind, month)
        if f is None:
            continue
        al, be, q = f
        tot = float(al * be * np.exp(rng.normal(0, 1.0))) * (rng.random() > 0.03)
        h = q / 2 if (tot < r["zero_mm"] and q > 0) else q + (1 - q) * stats.gamma.cdf(tot, al, scale=be)
        ref = float(stats.norm.ppf(np.clip(h, lo, 1 - lo)))
        err_spi = max(err_spi, abs(spi_from_total(cell, kind, month, tot) - ref))
        z = float(rng.uniform(-3, 3))
        H = stats.norm.cdf(z)
        want = 0.0 if H <= q else float(stats.gamma.ppf((H - q) / (1 - q), al, scale=be))
        got = total_from_spi(cell, kind, month, z)
        err_tot = max(err_tot, abs(got - want))
        err_rel = max(err_rel, abs(got - want) / max(want, 1.0))
        oni = float(rng.uniform(-2.5, 3.5))
        p = enso_predict(cell, kind, month, oni)
        i = m["_pos"][cell]
        lo_x, hi_x = m["enso_rule"]["oni_range"][kind][month - 1]
        want_p = (m["enso"][kind]["c0"][month - 1][i] + m["enso"][kind]["c1"][month - 1][i] * float(np.clip(oni, lo_x, hi_x))) / 1000
        err_pred = max(err_pred, abs(p[0] - want_p))
    return {"spi_from_total": err_spi, "total_from_spi_mm": err_tot, "total_from_spi_relative": err_rel,
            "enso_predict": err_pred}


if __name__ == "__main__":
    for k, v in check_against_scipy().items():
        print(f"[check] {k}: max |error| against scipy on 20,000 random draws {v:.2e}")
