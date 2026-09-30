"""
enso_replay_lib.py: the selection-safe vintage replay harness used by build_enso_replay.py.

For a past El Nino winter W (DJF ending in January of year W) the replay behaves as of September of year W-1:
  - only harvests before the harvest in question exist (harvest year hy = W - shift, shift from build_enso_model.shift_for);
  - the pairs are chosen from ALL calendared country-crop pairs with the page's rule (El Nino slope p-value, Benjamini-Hochberg
    across every pair that can be fitted that winter, q < 0.10, plus the Indian Ocean Dipole diagnostic) on those earlier harvests only;
  - the slope is the page's own fit (build_enso_model.detrend + build_enso_model.fit: centred 9-year moving average of log
    yield, two slopes, HAC errors) on the same earlier harvests;
  - the neutral baseline is the page's own trend (build_enso_neutral.trend: moving average at its last full window, extended by the
    slope of its last 15 full-window points) through the prior harvest, scaled to tonnes by the last known harvest (production
    = yield x area, so this is trend yield x the last known area);
  - the ENSO input is what was knowable in September (see ensoin()), not the observed DJF ONI.
Predicted change (t) = neutral x (exp(slope x ENSO input) - 1). Actual change (t) = FAOSTAT production - neutral. The no-effect
yardstick predicts zero change, so its error is |actual change|. Nothing here is tuned: every variant is fixed in build_enso_replay.py.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
from scipy.stats import norm, spearmanr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_enso_model as M  # noqa: E402
import build_enso_hindcast as H  # noqa: E402
import build_enso_neutral as NB  # noqa: E402

MIN_TRAIN = H.MIN_TRAIN          # earlier harvests with an anomaly and an index value: the project's 15
P_GATE = M.P_GATE
EVENT_ONI = H.EVENT_ONI
Q = 0.10


def huber_b1(years, anom, idx, shift, c=1.345, iters=60):
    """El Nino slope of the two-slope fit by Huber iteratively reweighted least squares (MAD scale)."""
    x, y = [], []
    for yr, a in zip(years, anom):
        o = idx.get(yr + shift)
        if o is not None:
            x.append(o); y.append(a)
    o, Y = np.array(x), np.array(y)
    X = np.column_stack([np.ones(len(o)), np.maximum(o, 0.0), np.minimum(o, 0.0)])
    w = np.ones(len(o))
    b = np.linalg.lstsq(X, Y, rcond=None)[0]
    for _ in range(iters):
        r = Y - X @ b
        s = max(np.median(np.abs(r - np.median(r))) / 0.6745, 1e-6)
        u = np.abs(r) / (c * s)
        w = np.where(u <= 1, 1.0, 1.0 / u)
        W = np.sqrt(w)[:, None]
        nb = np.linalg.lstsq(X * W, Y * W[:, 0], rcond=None)[0]
        if np.max(np.abs(nb - b)) < 1e-9:
            b = nb
            break
        b = nb
    return float(b[1])


def resid_sd(years, anom, idx, shift):
    x, y = [], []
    for yr, a in zip(years, anom):
        o = idx.get(yr + shift)
        if o is not None:
            x.append(o); y.append(a)
    o, Y = np.array(x), np.array(y)
    X = np.column_stack([np.ones(len(o)), np.maximum(o, 0.0), np.minimum(o, 0.0)])
    b = np.linalg.lstsq(X, Y, rcond=None)[0]
    r = Y - X @ b
    return float(math.sqrt(float(r @ r) / max(1, len(o) - 3)))


class Panel:
    """Pairs with calendars, and what is needed per pair to replay a winter."""

    def __init__(self, panel, cals):
        self.pairs = {}
        for iso in panel:
            for crop, series in panel[iso].items():
                shift = M.shift_for((cals.get(iso) or {}).get(crop) or {}) if (cals.get(iso) or {}).get(crop) else None
                if shift is None:
                    continue
                yrs = sorted(y for y, v in series.items() if v.get("yield") and v.get("prod"))
                if len(yrs) < MIN_TRAIN + 8:
                    continue
                self.pairs[(iso, crop)] = {"series": series, "shift": shift, "years": yrs}


def pair_record(p, key, W, idx, o_in, dmi, winsor=False, slope="ols"):
    """One pair, one winter, everything known in September of W-1. None when the pair cannot be replayed."""
    shift, series = p["shift"], p["series"]
    hy = W - shift
    if not series.get(hy, {}).get("prod"):
        return None
    train = [y for y in p["years"] if y < hy]
    if len(train) < MIN_TRAIN + 8:
        return None
    tv = [series[y]["yield"] for y in train]
    ay, anom = M.detrend(train, tv)
    if winsor:
        lo, hi = np.percentile(anom, [5, 95])
        anom = [float(min(max(a, lo), hi)) for a in anom]
    f = M.fit(ay, anom, idx, shift)
    if not f or f["n"] < MIN_TRAIN:
        return None
    fd = M.fit(ay, anom, idx, shift, dmi=dmi) if dmi is not None else None
    t = NB.trend(train, tv)
    if not t:
        return None
    last = train[-1]
    trend_log = t["anchor_log"] + t["slope_log_per_yr"] * (hy - t["anchor_year"])
    neutral_t = series[last]["prod"] * math.exp(trend_log - math.log(series[last]["yield"]))
    b1 = huber_b1(ay, anom, idx, shift) if slope == "huber" else f["b_nino"]
    s = resid_sd(ay, anom, idx, shift)
    pred_log = b1 * o_in
    sd = math.sqrt((f["se_nino"] * o_in) ** 2 + s * s)
    act_t = series[hy]["prod"]
    return {"key": f"{key[0]}/{key[1]}", "iso": key[0], "crop": key[1], "W": W, "hy": int(hy), "n_train": f["n"],
            "p_nino": f["p_nino"], "b1": float(b1), "se": f["se_nino"], "enso_specific": bool(fd and fd["p_joint"] < P_GATE) if fd else True,
            "neutral_t": neutral_t, "actual_t": float(act_t), "act_chg_t": float(act_t - neutral_t),
            "pred_log": float(pred_log), "pred_pct": (math.exp(pred_log) - 1) * 100, "pred_chg_t": neutral_t * (math.exp(pred_log) - 1),
            "p_fall": float(norm.cdf(-pred_log / sd)) if sd > 0 else 0.5, "o_in": float(o_in)}


def select(recs, rule="bh"):
    """The page's rule on the records of one winter: BH across every fitted pair's El Nino p, q < 0.10, ENSO-specific."""
    q = M.bh_q({r["key"]: r["p_nino"] for r in recs})
    for r in recs:
        r["q_nino"] = q[r["key"]]
        r["selected"] = bool(r["q_nino"] < Q and r["enso_specific"])
    return recs


def cell(items):
    pred = sum(i["pred_chg_t"] for i in items)
    act = sum(i["act_chg_t"] for i in items)
    return {"pairs": len(items), "pred_t": pred, "act_t": act, "err_t": abs(pred - act), "err0_t": abs(act),
            "sign_right": (pred < 0) == (act < 0), "false_alarm": pred < 0 < act, "predicted_fall": pred < 0}


def score_winters(cells):
    """cells: {winter: cell}. Portfolio scores over the winters that have a cell."""
    n = len(cells)
    if not n:
        return {"winters": 0}
    cs = list(cells.values())
    return {"winters": n, "sign_right": sum(c["sign_right"] for c in cs),
            "mae_kt": round(sum(c["err_t"] for c in cs) / n / 1e3), "mae_yardstick_kt": round(sum(c["err0_t"] for c in cs) / n / 1e3),
            "beats_yardstick": sum(c["err_t"] < c["err0_t"] for c in cs),
            "false_alarms": sum(c["false_alarm"] for c in cs), "predicted_falls": sum(c["predicted_fall"] for c in cs),
            "bias_kt": round(sum(c["pred_t"] - c["act_t"] for c in cs) / n / 1e3)}


def pair_scores(items):
    n = len(items)
    if not n:
        return {"pair_winters": 0}
    sr = sum((i["pred_chg_t"] < 0) == (i["act_chg_t"] < 0) for i in items)
    return {"pair_winters": n, "sign_right": sr, "sign_right_share": round(sr / n, 3),
            "beats_yardstick": sum(abs(i["pred_chg_t"] - i["act_chg_t"]) < abs(i["act_chg_t"]) for i in items),
            "false_alarms": sum(1 for i in items if i["pred_chg_t"] < 0 < i["act_chg_t"])}


def brier(items):
    n = len(items)
    if not n:
        return None
    b = sum((i["p_fall"] - (1.0 if i["act_chg_t"] < 0 else 0.0)) ** 2 for i in items) / n
    return {"pair_winters": n, "brier": round(b, 4), "brier_always_50": 0.25, "skill_vs_50": round(1 - b / 0.25, 3)}


def boot_improvement(base_cells, var_cells, n_boot=10000, seed=20261001):
    """Winter-block bootstrap of the mean tonnes-error improvement (baseline error minus variant error) on common winters."""
    ws = sorted(set(base_cells) & set(var_cells))
    d = np.array([base_cells[w]["err_t"] - var_cells[w]["err_t"] for w in ws]) / 1e3
    if len(d) < 2:
        return {"winters": len(d)}
    rng = np.random.default_rng(seed)
    means = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(n_boot)])
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"winters": len(d), "mean_improvement_kt": round(float(d.mean())), "ci95_kt": [round(float(lo)), round(float(hi)), ],
            "excludes_zero_positive": bool(lo > 0)}


def rank_stats(items):
    """Do the predicted and actual changes order the selected pairs alike?"""
    if len(items) < 4:
        return None
    p = [i["pred_chg_t"] for i in items]
    a = [i["act_chg_t"] for i in items]
    rho = float(spearmanr(p, a)[0]) if len(set(p)) > 1 and len(set(a)) > 1 else None
    k = min(3, len(items))
    top_p = {i["key"] for i in sorted(items, key=lambda i: i["pred_chg_t"])[:k]}
    top_a = {i["key"] for i in sorted(items, key=lambda i: i["act_chg_t"])[:k]}
    return {"pairs": len(items), "spearman": None if rho is None else round(rho, 2), "top3_overlap": len(top_p & top_a),
            "worst_predicted": sorted(items, key=lambda i: i["pred_chg_t"])[0]["key"],
            "worst_actual": sorted(items, key=lambda i: i["act_chg_t"])[0]["key"]}
