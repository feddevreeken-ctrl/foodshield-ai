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
  - The score is compared with "no change" (anomaly 0), the forecast anyone
    could make without a model.

Per pair: sign right (n of N), mean absolute error against no change, and how
many held-out harvests fell inside the 90% prediction band. Nothing here moves
the published coefficients; it says how far to trust them.

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
        held = []
        for hy, act, o in [d for d in data if d[2] >= EVENT_ONI]:
            train = [d for d in data if d[0] != hy]
            b, cov, s = ols([d[2] for d in train], [d[1] for d in train])
            pred = b[0] + b[1] * o
            sd = math.sqrt(cov[0, 0] + o * o * cov[1, 1] + 2 * o * cov[0, 1] + s * s)
            held.append({
                "harvest_year": int(hy), "djf_oni": round(float(o), 2),
                "actual_pct": round((math.exp(act) - 1) * 100, 1),
                "predicted_pct": round((math.exp(pred) - 1) * 100, 1),
                "sign_right": bool((act < 0) == (pred < 0)),
                "in_band": bool(pred - 1.645 * sd <= act <= pred + 1.645 * sd),
                "abs_err": abs(act - pred), "abs_err_zero": abs(act),
            })
        n = len(held)
        if not n:
            continue
        mae = sum(h["abs_err"] for h in held) / n
        mae0 = sum(h["abs_err_zero"] for h in held) / n
        for h in held:
            h.pop("abs_err"); h.pop("abs_err_zero")
        key = f"{iso}/{crop}"
        out[key] = {
            "iso": iso, "crop": crop, "events": n,
            "sign_right": sum(h["sign_right"] for h in held),
            "in_band": sum(h["in_band"] for h in held),
            "mae_log_pts": round(mae * 100, 1), "mae_no_change_log_pts": round(mae0 * 100, 1),
            "beats_no_change": bool(mae < mae0),
            "held_out": held,
        }
        print(f"  {key}: sign {out[key]['sign_right']}/{n}, band {out[key]['in_band']}/{n}, "
              f"error {mae * 100:.1f} vs no change {mae0 * 100:.1f}")
    payload = {"_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(), "version": "v1",
        "method": (f"Leave-one-El-Niño-out: each winter with DJF ONI ≥ +{EVENT_ONI} is held out, the two-slope fit "
                   f"re-estimated on the other years, and that harvest predicted from its ONI. Trailing detrend "
                   f"(mean of up to {TRAIL} earlier log yields), so no future harvest informs a prediction. Scored "
                   "against 'no change'. The 90% prediction band includes residual variance."),
        "source": "FAOSTAT QCL yields; NOAA CPC ONI (oni.ascii.txt); pairs as shown in enso_outlook.json",
        "caveat": ("Nine winters per pair at most, and none above ONI +2.5, so this tests direction and rough size "
                   "in past events, not the size of a stronger winter."),
    }, "data": {"pairs": out}}
    with open(os.path.join(ROOT, "data", "enso_hindcast.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False); fh.write("\n")
    print(f"[OK] enso_hindcast: {len(out)} pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
