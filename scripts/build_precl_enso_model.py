#!/usr/bin/env python3
"""build_precl_enso_model.py -- one statistical base for every rain layer: SPI fits and an ENSO rain model on PREC/L.

Why. The El Nino Ocean map paints rain on land at observed stops (SPI) and in an Outlook for the
coming six months. The Outlook read CPC's calibrated NMME tercile odds, which sit at 33% (no
signal) almost everywhere, so it came out nearly blank even with a very strong El Nino forecast.
What the map needs is what past El Ninos did to each cell's rain, as a number on the same drought
scale as the observed stops, so an outlook can say "at this ONI, this cell's month has usually
been this dry" and live rain can be read against the same distribution. This builder fits that
once, on one record (NOAA PREC/L, gauge based, monthly since 1948) and one grid (the rain grid:
PREC/L's 2.5-degree grid, rows 7..58, lat0 -56.25, lon0 -178.75, 52 x 144, row-major from the
southern edge), so every rain layer can share it. scripts/precl_model.py evaluates it in pure
Python for the refresh scripts.

1. SPI fits (1991-2020), per land cell, for two kinds of window ending in each calendar month:
     m1 = that month's total, m3 = the total of that month and the two before (m3 of January =
     Nov + Dec + Jan). A total is PREC/L's mean rate (mm a day) x the month's nominal days
     (February 28, as spi.MONTH_DAYS), so a leap February reads like any other.
   The CPC rule of scripts/spi.py and build_cpc_spi_params.py: q = share of the 30 totals under
   ZERO_MM (0.5 mm); alpha, beta = maximum-likelihood gamma of the others (Newton on
   ln a - digamma(a) = ln mean - mean ln x, from Thom's approximation), beta in mm; a total under
   ZERO_MM with q > 0 reads H = q / 2, otherwise H = q + (1 - q) P(alpha, total / beta); H clipped
   to Phi(+-3). Null (no fit): 1991-2020 mean rate under ARID_MM_DAY (0.3 mm a day, the arid mask
   of every rain layer), or fewer than 2/3 of the 30 totals over ZERO_MM (the CPC build's rule).
2. ENSO model, per fitted cell and window: that window's SPI in every year FIT_YEARS (1951-2020;
   a window belongs to the year of its last month) regressed on CPC's ONI for the three-month
   season centred on the window's middle month (m1: the month itself, so October -> SON; m3: its
   middle month, so Oct-Dec -> OND). Two forms, compared by leave-one-out cross-validation scored
   on El Nino years only (season ONI >= EN_ONI, 1.0), pooled over every fitted cell and window:
     all = OLS over all years; warm = OLS over years with ONI >= 0 (El Nino and La Nina impacts are
     not mirror images, so the La Nina half may bias the warm-side slope).
   ONE form is chosen for the whole grid by that pooled score (lower mean squared error of the
   left-out El Nino years); per-cell choice would let noise pick the model. The meta records the
   numbers and the reason. Stored per cell and window: intercept, slope, residual SD (n - 2), n,
   two-sided p of the slope (t test), the leave-one-out cross-validated correlation over the fit
   years, and skill = p < SKILL_P and CV correlation > SKILL_R.
3. The ONI range of each window's fit years is stored; forecasts beyond it (a very strong event)
   must cap the predictor there, which precl_model.enso_predict does.
4. Validation in the meta: hindcast pattern correlations for four strong events, the skilful share
   per month, and the sign at ONI +2.5 in thirteen regions with published El Nino signals.

Storage (data/ref/precl_enso_model.json.gz, gzipped JSON): cells = rain-grid indices with a fit in
at least one window; spi[kind] rows a, b, q (12 rows by calendar end month, parallel to cells) =
round(1000 ln alpha), round(1000 ln beta) with beta in mm, round(1000 q); enso[kind] rows c0, c1,
sd (x1000), n, p (x10000), cvr (x1000), skill (0/1); null = no fit.

Usage (local, once; numpy and scipy; PREC/L and the ONI table are fetched through
build_sst_composites.load_precl / load_oni and cached under FOODSHIELD_CACHE):
    python3 scripts/build_precl_enso_model.py
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
from scipy import stats
from scipy.special import digamma, gammainc, ndtr, ndtri, polygamma

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sst_composites import ONI_URL, PRECL_FILE, load_oni, load_precl  # noqa: E402
import precl_validate as V  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "ref" / "precl_enso_model.json.gz"
CLIM, FIT_YEARS = (1991, 2020), (1951, 2020)
# Gauge dropout (independent verification, 28 Sep 2026): after about 1990 PREC/L lost its gauges in the Congo basin,
# Angola, Papua New Guinea and a few islands, and falls back to near its own mean there. The 1991-2020 fit is then
# far too narrow (gamma shape in the hundreds or thousands, against a median of about 6), so a modest change reads
# as SPI +-3. A fit is dropped where the 1991-2020 spread is under DROP_RATIO of the 1951-1980 spread AND the shape
# exceeds DROP_ALPHA; the shape test keeps Australia's naturally variable arid cells, which the ratio alone would catch.
DROP_RATIO, DROP_ALPHA = 0.33, {"m1": 30.0, "m3": 60.0}
ZERO_MM, ARID_MM_DAY, MIN_NONZERO, SPI_CLIP = 0.5, 0.3, 2 / 3, 3.0
EN_ONI, STRONG_ONI = 1.0, 1.5
SKILL_P, SKILL_R = 0.05, 0.2
MONTH_DAYS = np.array((31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31), float)
KINDS = {"m1": 1, "m3": 3}
FORMS = ("all", "warm")
LN_SCALE = 1000


def window_total(rain: dict, year: int, month: int, length: int):
    """Rain grid total (mm) of the `length` months ending (year, month); None if a month is missing."""
    tot = 0.0
    for k in range(length):
        y, m = (year * 12 + month - 1 - k) // 12, (year * 12 + month - 1 - k) % 12 + 1
        if (y, m) not in rain:
            return None
        tot = tot + rain[(y, m)] * MONTH_DAYS[m - 1]
    return tot


def centre(year: int, month: int, kind: str) -> tuple[int, int]:
    """ONI key (year, centre month) of a window ending (year, month): the month itself, or its middle month."""
    if kind == "m1":
        return year, month
    t = year * 12 + month - 2
    return t // 12, t % 12 + 1


def fit_gamma(X: np.ndarray, days: float):
    """X: 30 years x cells of totals. -> alpha, beta (mm), q, fitted; the CPC build's _fit rule."""
    pos = X >= ZERO_MM
    n1 = pos.sum(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(pos, X, 0).sum(0) / n1
        A = np.log(mean) - np.where(pos, np.log(np.where(pos, X, 1)), 0).sum(0) / n1
        A = np.where(A > 1e-9, A, np.nan)
        a = (1 + np.sqrt(1 + 4 * A / 3)) / (4 * A)
        for _ in range(50):
            nxt = a - (np.log(a) - digamma(a) - A) / (1 / a - polygamma(1, a))
            a = np.where(nxt > 0, nxt, a / 2)
        resid = np.abs(np.log(a) - digamma(a) - A)
    fitted = (n1 >= MIN_NONZERO * X.shape[0]) & np.isfinite(a) & (resid < 1e-8 * np.maximum(A, 1e-3))
    fitted &= X.mean(0) / days >= ARID_MM_DAY
    return a, mean / a, (X.shape[0] - n1) / X.shape[0], fitted


def spi_np(X, a, b, q):
    """SPI of totals X under fits (a, b, q), spi.spi_cpc's rule, vectorised."""
    with np.errstate(invalid="ignore", divide="ignore"):
        G = gammainc(a, np.maximum(X, 0) / b)
        h = np.where((X < ZERO_MM) & (q > 0), q / 2, q + (1 - q) * G)
    lo = ndtr(-SPI_CLIP)
    return ndtri(np.clip(h, lo, 1 - lo))


def ols_loo(x: np.ndarray, Y: np.ndarray) -> dict:
    """OLS of every column of Y on x, with leave-one-out predictions (closed form, e_i / (1 - h_i))."""
    n = len(x)
    xm, Ym = x.mean(), Y.mean(0)
    sxx = ((x - xm) ** 2).sum()
    b = ((x - xm)[:, None] * (Y - Ym)).sum(0) / sxx
    a = Ym - b * xm
    e = Y - (a + b * x[:, None])
    sd = np.sqrt((e ** 2).sum(0) / (n - 2))
    h = 1 / n + (x - xm) ** 2 / sxx
    loo = Y - e / (1 - h)[:, None]
    t = b / (sd / np.sqrt(sxx))
    p = 2 * stats.t.sf(np.abs(t), n - 2)
    lc, yc = loo - loo.mean(0), Y - Ym
    with np.errstate(invalid="ignore", divide="ignore"):
        cvr = (lc * yc).sum(0) / np.sqrt((lc ** 2).sum(0) * (yc ** 2).sum(0))
    return {"a": a, "b": b, "sd": sd, "p": p, "cvr": np.nan_to_num(cvr, nan=0.0), "loo": loo, "n": n}


def build() -> tuple[dict, dict]:
    rain, lats, lons = load_precl()
    oni_rows = load_oni()
    oni = {(y, m): v for y, m, v in oni_rows}
    nlat, nlon = len(lats), len(lons)
    last = max(rain)
    land = np.isfinite(np.array([rain[k] for k in sorted(rain)])).all(0).ravel()
    ncell = nlat * nlon
    years_all = list(range(1949, last[0] + 1))

    spi_par, spi_obs, fits, dropped = {}, {}, {}, {}   # spi_obs[(kind, m)] = {year: SPI grid (flat)}
    for kind, L in KINDS.items():
        for m in range(1, 13):
            days = MONTH_DAYS[[(m - 1 - k) % 12 for k in range(L)]].sum()
            X = np.array([window_total(rain, y, m, L).ravel() for y in range(CLIM[0], CLIM[1] + 1)])
            a, b, q, ok = fit_gamma(X[:, land], days)
            A, B, Q, OK = (np.full(ncell, np.nan) for _ in range(4))
            A[land], B[land], Q[land], OK[land] = a, b, q, ok
            OK = OK == 1
            X0 = np.array([window_total(rain, y, m, L).ravel() for y in range(1951, 1981)])
            sd_new, sd_old = np.nanstd(X, axis=0), np.nanstd(X0, axis=0)
            gone = OK & (sd_new < DROP_RATIO * sd_old) & (np.nan_to_num(A, nan=0.0) > DROP_ALPHA[kind])
            dropped[(kind, m)] = int(gone.sum())
            OK = OK & ~gone
            spi_par[(kind, m)] = (A, B, Q, OK)
            obs = {}
            for y in years_all:
                T = window_total(rain, y, m, L)
                if T is not None:
                    obs[y] = np.where(OK, spi_np(T.ravel(), A, B, Q), np.nan)
            spi_obs[(kind, m)] = obs

    # ENSO model, both forms, and their leave-one-out score on El Nino years.
    score = {f: {"se": 0.0, "se_strong": 0.0, "n": 0, "n_strong": 0, "pred": [], "obs": []} for f in FORMS}
    ref = {"se": 0.0, "se_strong": 0.0}
    per_window = []
    for kind in KINDS:
        for m in range(1, 13):
            A, B, Q, OK = spi_par[(kind, m)]
            yrs = [y for y in range(FIT_YEARS[0], FIT_YEARS[1] + 1)]
            x = np.array([oni[centre(y, m, kind)] for y in yrs])
            Y = np.array([spi_obs[(kind, m)][y][OK] for y in yrs])
            en = x >= EN_ONI
            strong = x >= STRONG_ONI
            ref["se"] += (Y[en] ** 2).sum()
            ref["se_strong"] += (Y[strong] ** 2).sum()
            row = {"window": f"{kind}/{m}", "n_en": int(en.sum())}
            for f in FORMS:
                sel = np.ones(len(x), bool) if f == "all" else x >= 0
                r = ols_loo(x[sel], Y[sel])
                fits[(f, kind, m)] = (r, x[sel], np.array(yrs)[sel])
                loo = np.full(Y.shape, np.nan)
                loo[sel] = r["loo"]
                err = (loo[en] - Y[en]) ** 2
                score[f]["se"] += err.sum()
                score[f]["n"] += err.size
                score[f]["se_strong"] += ((loo[strong] - Y[strong]) ** 2).sum()
                score[f]["n_strong"] += int(strong.sum()) * Y.shape[1]
                score[f]["pred"].append(loo[en].ravel())
                score[f]["obs"].append(Y[en].ravel())
                row[f] = round(float(err.mean()), 4)
            row["winner"] = min(FORMS, key=lambda f: row[f])
            per_window.append(row)

    cv = {}
    for f in FORMS:
        s = score[f]
        pr, ob = np.concatenate(s["pred"]), np.concatenate(s["obs"])
        cv[f] = {"mse_en": round(float(s["se"] / s["n"]), 4),
                 "mse_strong": round(float(s["se_strong"] / s["n_strong"]), 4),
                 "msss_en_vs_zero": round(float(1 - s["se"] / ref["se"]), 4),
                 "msss_strong_vs_zero": round(float(1 - s["se_strong"] / ref["se_strong"]), 4),
                 "corr_en": round(float(np.corrcoef(pr, ob)[0, 1]), 4), "n_cases_en": s["n"],
                 "n_cases_strong": s["n_strong"]}
    form = min(FORMS, key=lambda f: cv[f]["mse_en"])
    other = [f for f in FORMS if f != form][0]
    wins = sum(1 for r in per_window if r["winner"] == form)

    # Cells with a fit anywhere, then the compact tables.
    anyfit = np.zeros(ncell, bool)
    for (kind, m), (_, _, _, OK) in spi_par.items():
        anyfit |= OK
    cells = np.flatnonzero(anyfit)
    spi_out, enso_out, oni_range = {}, {}, {}
    for kind in KINDS:
        sa, sb, sq = [], [], []
        ec = {k: [] for k in ("c0", "c1", "sd", "n", "p", "cvr", "skill")}
        lo_hi = []
        for m in range(1, 13):
            A, B, Q, OK = spi_par[(kind, m)]
            r, xs, _ = fits[(form, kind, m)]
            full = {k: np.full(ncell, np.nan) for k in ("a", "b", "sd", "p", "cvr")}
            for k in full:
                full[k][OK] = r[k]
            ok = OK[cells]

            def enc(v, s):
                return [int(round(s * t)) if o else None for t, o in zip(v, ok)]
            sa.append(enc(np.log(np.where(OK, A, 1))[cells], LN_SCALE))
            sb.append(enc(np.log(np.where(OK, B, 1))[cells], LN_SCALE))
            sq.append(enc(np.where(OK, Q, 0)[cells], 1000))
            ec["c0"].append(enc(full["a"][cells], 1000))
            ec["c1"].append(enc(full["b"][cells], 1000))
            ec["sd"].append(enc(full["sd"][cells], 1000))
            ec["n"].append([r["n"] if o else None for o in ok])
            ec["p"].append(enc(full["p"][cells], 10000))
            ec["cvr"].append(enc(full["cvr"][cells], 1000))
            sk = (full["p"] < SKILL_P) & (full["cvr"] > SKILL_R)
            ec["skill"].append([int(s) if o else None for s, o in zip(sk[cells], ok)])
            lo_hi.append([float(xs.min()), float(xs.max())])
        spi_out[kind] = {"a": sa, "b": sb, "q": sq}
        enso_out[kind] = ec
        oni_range[kind] = lo_hi

    model = {
        "grid": {"lat0": float(lats[0]), "lon0": float(lons[0]), "step_deg": 2.5, "nlat": nlat, "nlon": nlon,
                 "encoding": "cells = row-major index from the southern edge; tables [end month - 1][cell position]"},
        "cells": [int(c) for c in cells],
        "kinds": {"m1": "the calendar month's total", "m3": "the total of the calendar month and the two before it"},
        "spi_rule": {"clim": list(CLIM), "zero_mm": ZERO_MM, "spi_clip": SPI_CLIP, "ln_scale": LN_SCALE,
                     "min_nonzero": round(MIN_NONZERO, 4), "arid_mm_day": ARID_MM_DAY,
                     "month_days": [int(d) for d in MONTH_DAYS],
                     "text": "total under zero_mm with q > 0: H = q/2; else H = q + (1-q) P(alpha, total/beta); "
                             "H clipped to Phi(+-spi_clip); SPI = Phi^-1(H). beta in mm.",
                     "gauge_dropout": {"ratio": DROP_RATIO, "alpha": DROP_ALPHA,
                                       "dropped": {k: [dropped[(k, m)] for m in range(1, 13)] for k in KINDS},
                                       "text": "No fit where PREC/L has too few gauges after 1990: the 1991-2020 spread of the "
                                               "window total is under ratio x its 1951-1980 spread and the gamma shape is above "
                                               "alpha, so the record is near its own mean, not real rain."}},
        "spi": spi_out,
        "enso": enso_out,
        "enso_rule": {
            "form": form, "fit_years": list(FIT_YEARS),
            "predictor": "CPC ONI of the 3-month season centred on the window's middle month (m1: the month)",
            "oni_range": oni_range,
            "oni_range_note": "min and max ONI of each window's fit years [kind][end month - 1]; predictions cap "
                              "the predictor to this range",
            "scales": {"c0": 1000, "c1": 1000, "sd": 1000, "p": 10000, "cvr": 1000},
            "skill_rule": f"p < {SKILL_P} and leave-one-out CV correlation > {SKILL_R}",
        },
        "sources": {"precl": PRECL_FILE, "oni": ONI_URL,
                    "precl_last_month": f"{last[0]}-{last[1]:02d}",
                    "oni_last_season": f"{oni_rows[-1][0]}-{oni_rows[-1][1]:02d} (centre month)",
                    "built": date.today().isoformat()},
    }
    cvmeta = {
        "scored_on": f"leave-one-out predictions of years with season ONI >= {EN_ONI}, pooled over every "
                     "fitted cell and all 24 windows; msss = 1 - mse / mse of predicting SPI 0",
        "all_years": cv["all"], "warm_side_only": cv["warm"],
        "chosen": form,
        "reason": (f"'{form}' has the lower pooled mean squared error on left-out El Nino years "
                   f"({cv[form]['mse_en']} against {cv[other]['mse_en']}; ONI >= {STRONG_ONI}: "
                   f"{cv[form]['mse_strong']} against {cv[other]['mse_strong']}) and wins {wins} of 24 windows."),
        "per_window": per_window,
    }
    ctx = {"spi_par": spi_par, "spi_obs": spi_obs, "fits": fits, "form": form, "oni": oni, "lats": lats,
           "lons": lons, "ncell": ncell, "land": land}
    model["meta"] = {"cv": cvmeta}
    return model, ctx


def rounding_check(model: dict, ctx: dict, n: int = 20000, seed: int = 7) -> float:
    """Max |SPI change| from storing ln alpha, ln beta, q at 1/1000 steps, over random stored fits and totals."""
    rng = np.random.default_rng(seed)
    cells = np.array(model["cells"])
    worst = 0.0
    for kind in KINDS:
        for m in range(1, 13):
            A, B, Q, OK = ctx["spi_par"][(kind, m)]
            idx = np.flatnonzero(OK)
            c = rng.choice(idx, n // 24)
            pos = np.searchsorted(cells, c)
            ra = np.exp(np.array([model["spi"][kind]["a"][m - 1][p] for p in pos]) / LN_SCALE)
            rb = np.exp(np.array([model["spi"][kind]["b"][m - 1][p] for p in pos]) / LN_SCALE)
            rq = np.array([model["spi"][kind]["q"][m - 1][p] for p in pos]) / 1000
            X = A[c] * B[c] * np.exp(rng.normal(0, 0.7, len(c)))
            worst = max(worst, float(np.abs(spi_np(X, A[c], B[c], Q[c]) - spi_np(X, ra, rb, rq)).max()))
    return worst


def main() -> int:
    model, ctx = build()
    model["meta"]["rounding_max_abs_spi"] = round(rounding_check(model, ctx), 4)
    model["meta"].update(V.validate(model, ctx))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(model, fh, separators=(",", ":"))
    cv = model["meta"]["cv"]
    print(f"[cv] all years: {cv['all_years']}")
    print(f"[cv] warm side: {cv['warm_side_only']}")
    print(f"[cv] chosen {cv['chosen']}: {cv['reason']}")
    print(f"[check] rounding moves SPI by at most {model['meta']['rounding_max_abs_spi']}")
    V.report(model)
    print(f"[OK] {OUT} {OUT.stat().st_size / 1e6:.2f} MB, {len(model['cells'])} cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
