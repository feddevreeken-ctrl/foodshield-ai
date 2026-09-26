#!/usr/bin/env python3
"""
build_enso_hindcast.py — score the El Nino yield fit on winters it was not fitted on.

The Harvests lens says what the fit implies for 2027. Until now it could only
add "the model has not been scored on harvests it was not trained on". This
scores it: for every pair the outlook shows, each El Nino winter with DJF ONI
of +1.0 or more is held out in turn, the two-slope fit is re-estimated on the
remaining years, and the held-out harvest is predicted from that winter's ONI.

Two choices keep the test honest:
  - The detrend looks back only (the mean of up to eight earlier log yields).
    The fit on the page uses a centred window, which borrows the four harvests
    after each year; a live forecast cannot, so the hindcast does not either.
  - A trailing mean lags a rising yield trend, so most anomalies come out
    positive and a fit scores the "sign" from trend alone. v2 therefore scores
    only the El Nino part: the training intercept b0 carries the trend, the
    El Nino component is b1*ONI, and a year counts as right when
    sign(actual - b0) == sign(b1*ONI). The benchmark is b0 alone (same
    training years), so "beats" means the El Nino term moved the forecast
    closer, not that the trend did.

Per pair: sign right (n of N), mean absolute error against the intercept-only
forecast, and how many held-out harvests fell inside the 90% prediction band.
Nothing here moves the published coefficients; it says how far to trust them.

Run by hand after build_enso_model.py, with the same FAOSTAT cache
(FOODSHIELD_CACHE).
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_enso_model as M  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAIL = 8          # earlier harvests in the trailing mean
MIN_PRIOR = 5      # fewer than this and a year gets no anomaly
EVENT_ONI = 1.0    # winters held out: DJF ONI at or above this
MIN_TRAIN = 15     # forward walk: earlier harvests needed before a winter is scored


def trailing(years, vals):
    logy = np.log(np.array(vals, float))
    oy, oa = [], []
    for i in range(len(logy)):
        lo = max(0, i - TRAIL)
        if i - lo < MIN_PRIOR:
            continue
        oy.append(years[i]); oa.append(float(logy[i] - logy[lo:i].mean()))
    return oy, oa


def ols(x, y):
    o = np.array(x, float); Y = np.array(y, float)
    X = np.column_stack([np.ones(len(o)), np.maximum(o, 0), np.minimum(o, 0)])
    b, *_ = np.linalg.lstsq(X, Y, rcond=None)
    r = Y - X @ b
    s2 = float(r @ r) / max(1, len(o) - 3)
    return b, np.linalg.inv(X.T @ X) * s2, math.sqrt(s2)


def score(train, act, o):
    """One held-out harvest against a fit on `train`; the yardstick is the same years without the El Nino term."""
    b, cov, s = ols([d[2] for d in train], [d[1] for d in train])
    base = float(np.mean([d[1] for d in train]))
    pred = float(b[0] + b[1] * o)
    enso = pred - base
    sd = math.sqrt(cov[0, 0] + o * o * cov[1, 1] + 2 * o * cov[0, 1] + s * s)
    return pred, base, enso, sd


def summary(held):
    n = len(held)
    mae = sum(h["abs_err"] for h in held) / n
    mae0 = sum(h["abs_err_zero"] for h in held) / n
    for h in held:
        h.pop("abs_err"); h.pop("abs_err_zero")
    return {"events": n, "sign_right": sum(h["sign_right"] for h in held), "in_band": sum(h["in_band"] for h in held),
            "mae_log_pts": round(mae * 100, 1), "mae_no_change_log_pts": round(mae0 * 100, 1), "beats_no_change": bool(mae < mae0)}


def main() -> int:
    oni, cal, panel = M.load_oni(), M.load_calendars(), M.load_faostat()
    with open(os.path.join(ROOT, "data", "enso_outlook.json"), encoding="utf-8") as fh:
        rows = json.load(fh)["data"]["rows_all"]
    pairs = [(r["iso"], r["crop"]) for r in rows if r["status"] == "shown"]
    out = {}
    for iso, crop in pairs:
        series = (panel.get(iso) or {}).get(crop) or {}
        yrs = sorted(y for y, v in series.items() if v.get("yield"))
        shift = M.shift_for((cal.get(iso) or {}).get(crop, {}))
        if shift is None or len(yrs) < 20:
            continue
        ty, ta = trailing(yrs, [series[y]["yield"] for y in yrs])
        data = [(y, a, oni.get(y + shift)) for y, a in zip(ty, ta) if oni.get(y + shift) is not None]
        held, fwd, abstain = [], [], []
        for hy, act, o in [d for d in data if d[2] >= EVENT_ONI]:
            # Forward walk: fit only on harvests before the held-out one, as a forecaster would have had.
            past = [d for d in data if d[0] < hy]
            if len(past) < MIN_TRAIN:
                abstain.append(int(hy))
            else:
                fp, fb, fe, fsd = score(past, act, o)
                fwd.append({"harvest_year": int(hy), "djf_oni": round(float(o), 2), "train_years": len(past),
                            "actual_pct": round((math.exp(act) - 1) * 100, 1), "predicted_pct": round((math.exp(fp) - 1) * 100, 1),
                            "baseline_pct": round((math.exp(fb) - 1) * 100, 1),
                            "sign_right": bool(((act - fb) < 0) == (fe < 0)),
                            "in_band": bool(fp - 1.645 * fsd <= act <= fp + 1.645 * fsd),
                            "abs_err": abs(act - fp), "abs_err_zero": abs(act - fb)})
            train = [d for d in data if d[0] != hy]
            b, cov, s = ols([d[2] for d in train], [d[1] for d in train])
            # The yardstick is the model without the El Nino term, refit on the same
            # years: an intercept-only fit, i.e. the mean training anomaly (it carries
            # the trend the trailing detrend leaves in). The fit's own intercept b0 is
            # not that model and made too gentle a benchmark.
            base = float(np.mean([d[1] for d in train]))
            pred = float(b[0] + b[1] * o)
            enso = pred - base                    # what the El Nino term adds over the yardstick
            sd = math.sqrt(cov[0, 0] + o * o * cov[1, 1] + 2 * o * cov[0, 1] + s * s)
            held.append({
                "harvest_year": int(hy), "djf_oni": round(float(o), 2),
                "actual_pct": round((math.exp(act) - 1) * 100, 1),
                "predicted_pct": round((math.exp(pred) - 1) * 100, 1),
                "baseline_pct": round((math.exp(base) - 1) * 100, 1),
                "elnino_actual_log_pts": round((act - base) * 100, 1),
                "elnino_predicted_log_pts": round(enso * 100, 1),
                "sign_right": bool(((act - base) < 0) == (enso < 0)),
                "in_band": bool(pred - 1.645 * sd <= act <= pred + 1.645 * sd),
                "abs_err": abs(act - pred), "abs_err_zero": abs(act - base),
            })
        n = len(held)
        if not n:
            continue
        mae = sum(h["abs_err"] for h in held) / n
        mae0 = sum(h["abs_err_zero"] for h in held) / n
        for h in held:
            h.pop("abs_err"); h.pop("abs_err_zero")
        key = f"{iso}/{crop}"
        forward = dict(summary(fwd), held_out=fwd) if fwd else {"events": 0, "held_out": []}
        forward["abstained_years"] = abstain
        out[key] = {
            "iso": iso, "crop": crop, "events": n,
            "sign_right": sum(h["sign_right"] for h in held),
            "in_band": sum(h["in_band"] for h in held),
            "mae_log_pts": round(mae * 100, 1), "mae_no_change_log_pts": round(mae0 * 100, 1),
            "beats_no_change": bool(mae < mae0),
            "held_out": held,
            "forward": forward,
        }
        print(f"  {key}: sign {out[key]['sign_right']}/{n}, band {out[key]['in_band']}/{n}, "
              f"error {mae * 100:.1f} vs baseline {mae0 * 100:.1f}; forward sign {forward.get('sign_right', 0)}/{forward['events']}"
              f"{', beats' if forward.get('beats_no_change') else ''}, abstained {len(abstain)}")
    payload = {"_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(), "version": "v2", "method_version": "v2",
        "method": (f"Each El Niño winter (DJF ONI of +{EVENT_ONI} or more) is left out in turn and the fit is "
                   "redone on the other years. A harvest is measured against the average of up to "
                   f"{TRAIL} earlier harvests, so no later year informs it. Because yields trend upward, that "
                   "average runs low. The yardstick is the same fit without its El Niño term (the average "
                   "of the other years), which carries that trend. 'Sign right' means the fit and the harvest "
                   "both landed on the same side of the yardstick; beating it means the El Niño term moved the "
                   "forecast closer to what happened. The 90% band includes year-to-year noise."),
        "source": "FAOSTAT QCL yields; NOAA CPC ONI (oni.ascii.txt); pairs as shown in enso_outlook.json",
        "caveat": ("Nine winters per pair at most, and none above ONI +2.5, so this tests direction and rough size "
                   "in past events, not the size of a stronger winter."),
        "method_forward": (f"Forward walk: each El Niño winter is scored with a fit on earlier harvests only (at least {MIN_TRAIN}); "
                           "winters with fewer earlier harvests are abstentions, listed per pair. The pairs themselves were chosen "
                           "on the full record and that selection is not re-run, and the winter's observed ONI is used, so this is "
                           "a conditional hindcast, not a vintage forecast."),
    }, "data": {"pairs": out}}
    with open(os.path.join(ROOT, "data", "enso_hindcast.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False); fh.write("\n")
    print(f"[OK] enso_hindcast: {len(out)} pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
