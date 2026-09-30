#!/usr/bin/env python3
"""
build_enso_portfolio_hindcast.py -- score the production-weighted AGGREGATE, not single pairs.

data/enso_hindcast.json scores each (country, crop) fit on its own harvests, in yield
terms. The page adds those pairs up in tonnes, and that sum had never been scored.
This does it. For every past El Nino winter (DJF ONI of +1.0 or more), each pair the
outlook shows is refitted on EARLIER harvests only (the forward walk of
build_enso_hindcast.py, same two-slope fit, same trailing detrend, at least 15
earlier harvests), then the pairs are summed in tonnes.

Tonnes, per pair and winter:
  yardstick E0  = the production with no El Nino effect: the harvest's own
                  production scaled by exp(base - actual anomaly), where base is
                  the intercept-only forecast (mean training anomaly). E0 carries the
                  trend, exactly as in the hindcast. Harvested area cancels out.
  actual change = FAOSTAT production - E0
  predicted     = E0 * (exp(fitted El Nino term) - 1)
  90% band      = E0 * (exp(pred +/- 1.645 sd - base) - 1), the hindcast's band
Portfolio = sum over the pairs scored in that winter (global, per crop, per region).
The no-El-Nino yardstick predicts zero change, so its error is |actual change|.

Band for a sum: pair bands are added end to end (perfect correlation, the wide and
safe choice; pairs share winters and regions). The independent-pairs band (root sum of
squares) is stored too and is narrower.

Leakage that remains, stated in _meta: the pairs were chosen on the full record, the
winter's observed ONI is used, harvested area is not forecast (it cancels in E0).

Run by hand after build_enso_hindcast.py (needs numpy and scipy). Same cache
(FOODSHIELD_CACHE) for FAOSTAT QCL and ONI.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_enso_model as M  # noqa: E402
import build_enso_hindcast as H  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Z = 1.645
CROP_LABEL = {"corn": "maize", "wheat": "wheat", "rice": "rice", "soybeans": "soy", "sorghum": "sorghum", "barley": "barley"}


def winter_label(w: int) -> str:
    return f"{w - 1}-{str(w)[2:]}"


def sgn_fall(x: float) -> bool:
    return x < 0


def agg(items):
    """Sum a list of pair dicts into one portfolio cell."""
    pred = sum(i["pred_t"] for i in items)
    act = sum(i["act_t"] for i in items)
    lo = sum(i["lo_t"] for i in items)
    hi = sum(i["hi_t"] for i in items)
    dn = math.sqrt(sum((i["pred_t"] - i["lo_t"]) ** 2 for i in items))
    up = math.sqrt(sum((i["hi_t"] - i["pred_t"]) ** 2 for i in items))
    return {"pairs": len(items), "predicted_kt": round(pred / 1e3), "actual_kt": round(act / 1e3),
            "yardstick_kt": round(sum(i["e0"] for i in items) / 1e3),
            "band90_kt": [round(lo / 1e3), round(hi / 1e3)],
            "band90_indep_kt": [round((pred - dn) / 1e3), round((pred + up) / 1e3)],
            "sign_right": bool(sgn_fall(pred) == sgn_fall(act)),
            "in_band": bool(lo <= act <= hi), "in_band_indep": bool(pred - dn <= act <= pred + up),
            "_pred": pred, "_act": act}


def score_cells(cells):
    """cells: list of agg() dicts -> overall scores."""
    n = len(cells)
    if not n:
        return {"events": 0}
    mae = sum(abs(c["_pred"] - c["_act"]) for c in cells) / n
    mae0 = sum(abs(c["_act"]) for c in cells) / n
    return {"events": n, "sign_right": sum(c["sign_right"] for c in cells),
            "in_band": sum(c["in_band"] for c in cells), "in_band_indep": sum(c["in_band_indep"] for c in cells),
            "mae_kt": round(mae / 1e3), "mae_yardstick_kt": round(mae0 / 1e3),
            "bias_kt": round(sum(c["_pred"] - c["_act"] for c in cells) / n / 1e3),
            "beats_yardstick": sum(abs(c["_pred"] - c["_act"]) < abs(c["_act"]) for c in cells),
            "false_alarms": sum(1 for c in cells if c["_pred"] < 0 and c["_act"] > 0),
            "predicted_falls": sum(1 for c in cells if c["_pred"] < 0),
            "missed_falls": sum(1 for c in cells if c["_pred"] >= 0 and c["_act"] < 0)}


def main() -> int:
    oni, cal, panel = M.load_oni(), M.load_calendars(), M.load_faostat()
    with open(os.path.join(ROOT, "data", "enso_outlook.json"), encoding="utf-8") as fh:
        rows = json.load(fh)["data"]["rows_all"]
    pairs = [r for r in rows if r["status"] == "shown"]
    region_of = {(r["iso"], r["crop"]): (r.get("region") or r["iso"]) for r in pairs}

    by_w = defaultdict(list)       # winter -> scored pair dicts
    abstain = defaultdict(list)    # winter -> pairs with too few earlier harvests
    for r in pairs:
        iso, crop = r["iso"], r["crop"]
        series = (panel.get(iso) or {}).get(crop) or {}
        yrs = sorted(y for y, v in series.items() if v.get("yield"))
        shift = M.shift_for((cal.get(iso) or {}).get(crop, {}))
        if shift is None or len(yrs) < 20:
            continue
        ty, ta = H.trailing(yrs, [series[y]["yield"] for y in yrs])
        data = [(y, a, oni.get(y + shift)) for y, a in zip(ty, ta) if oni.get(y + shift) is not None]
        for hy, act, o in [d for d in data if d[2] >= H.EVENT_ONI]:
            w = int(hy + shift)
            past = [d for d in data if d[0] < hy]
            if len(past) < H.MIN_TRAIN or not series.get(hy, {}).get("prod"):
                abstain[w].append(f"{iso}/{crop}")
                continue
            pred, base, enso, sd = H.score(past, act, o)
            prod = series[hy]["prod"]
            e0 = prod * math.exp(base - act)
            prev = series.get(hy - 1, {}).get("prod")
            by_w[w].append({
                "iso": iso, "crop": crop, "region": region_of[(iso, crop)], "harvest_year": int(hy),
                "e0": e0, "prod": prod, "prev": prev,
                "act_t": prod - e0, "pred_t": e0 * (math.exp(enso) - 1),
                "lo_t": e0 * (math.exp(pred - Z * sd - base) - 1), "hi_t": e0 * (math.exp(pred + Z * sd - base) - 1),
            })

    winters = sorted(w for w, v in oni.items() if v >= H.EVENT_ONI and w >= 1966)
    events, g_cells, crop_cells, reg_cells = [], [], defaultdict(list), defaultdict(list)
    rank_rows = []
    for w in winters:
        items = by_w.get(w, [])
        ev = {"winter": winter_label(w), "djf_year": w, "djf_oni": round(float(oni[w]), 2),
              "pairs_scored": len(items), "pairs_abstained": len(abstain.get(w, []))}
        if not items:
            ev["scored"] = False
            events.append(ev)
            continue
        g = agg(items)
        g_cells.append(g)
        ev["scored"] = True
        ev["global"] = {k: v for k, v in g.items() if not k.startswith("_")}
        prevs = [i["prev"] for i in items]
        if all(p for p in prevs):
            ev["global"]["prod_rose_yoy"] = bool(sum(i["prod"] for i in items) > sum(prevs))
        ev["global"]["false_alarm"] = bool(g["_pred"] < 0 and g["_act"] > 0)
        ev["pairs"] = [{"pair": f"{i['iso']}/{i['crop']}", "predicted_kt": round(i["pred_t"] / 1e3),
                        "actual_kt": round(i["act_t"] / 1e3)} for i in items]
        for key, cells, field in (("crops", crop_cells, "crop"), ("regions", reg_cells, "region")):
            grp = defaultdict(list)
            for i in items:
                grp[i[field]].append(i)
            ev[key] = {}
            for k, v in grp.items():
                a = agg(v)
                cells[k].append(a)
                ev[key][k] = {x: y for x, y in a.items() if not x.startswith("_")}
        # country-level ranking of who is hit hardest
        ctry = defaultdict(lambda: [0.0, 0.0])
        for i in items:
            ctry[i["iso"]][0] += i["pred_t"]; ctry[i["iso"]][1] += i["act_t"]
        if len(ctry) >= 3:
            isos = sorted(ctry)
            p = [ctry[c][0] for c in isos]; a = [ctry[c][1] for c in isos]
            rho = float(spearmanr(p, a)[0]) if len(set(p)) > 1 and len(set(a)) > 1 else None
            top_p = isos[int(np.argmin(p))]; top_a = isos[int(np.argmin(a))]
            ev["ranking"] = {"countries": isos, "spearman": None if rho is None else round(rho, 2),
                             "worst_predicted": top_p, "worst_actual": top_a, "top1_right": bool(top_p == top_a)}
            rank_rows.append(ev["ranking"])
        events.append(ev)

    overall = score_cells(g_cells)
    overall["yoy_rose_and_predicted_fall"] = sum(
        1 for e in events if e.get("scored") and e["global"].get("prod_rose_yoy") and e["global"]["predicted_kt"] < 0)
    rs = [r["spearman"] for r in rank_rows if r["spearman"] is not None]
    overall["rank"] = {"events": len(rank_rows), "top1_right": sum(r["top1_right"] for r in rank_rows),
                       "mean_spearman": round(float(np.mean(rs)), 2) if rs else None, "spearman_events": len(rs)}
    by_crop = {k: dict(score_cells(v), label=CROP_LABEL.get(k, k)) for k, v in crop_cells.items()}
    by_region = {k: score_cells(v) for k, v in reg_cells.items()}

    payload = {"_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(), "version": "v1", "builder": "scripts/build_enso_portfolio_hindcast.py (hand-run, needs numpy and scipy; output committed)",
        "source": "FAOSTAT QCL production and yield (https://bulks-faostat.fao.org/production/, fetched 2026-09-30, open licence CC BY-NC-SA 3.0 IGO); NOAA CPC ONI (oni.ascii.txt); pairs as shown in enso_outlook.json; per-pair fit as in enso_hindcast.json",
        "method": ("Forward walk on the pairs the outlook adds up. For each El Niño winter (DJF ONI of +1.0 or more, 1965-66 to 2023-24) "
                   f"each pair is fitted only on earlier harvests (at least {H.MIN_TRAIN}; pairs with fewer abstain and are left out of that winter). "
                   "The fit is the hindcast's two-slope fit on anomalies against the mean of up to 8 earlier harvests. "
                   "Predicted change in tonnes = the no-El-Niño production times the fitted El Niño term. "
                   "Actual change = FAOSTAT production minus that no-El-Niño production."),
        "yardstick": ("The no-El-Niño yardstick is the same fit without its El Niño term (the mean anomaly of the earlier harvests, which carries the yield trend). "
                      "It predicts zero change, so its error is the size of the actual change. Because it is built from the harvest's own production and the "
                      "intercept-only forecast, harvested area cancels and is not forecast."),
        "interval": ("90% band of a sum: the pair bands are added end to end (assumes the pairs miss together; wide and safe). "
                     "The band for independent pairs is stored as in_band_indep and is narrower. Pair bands include year-to-year noise."),
        "rank": "Rank accuracy uses country totals (all crops) in winters with at least 3 countries scored: Spearman between predicted and actual tonnes, and whether the country with the largest predicted fall had the largest actual fall.",
        "false_alarm": "A false alarm is a winter where the portfolio is predicted to fall (predicted change below zero) and the actual change against the yardstick is above zero. prod_rose_yoy also records whether summed production rose on the year before.",
        "leakage": ("Leakage that remains: (1) the pairs were chosen on the full 1961-2024 record, and that selection is not re-run inside each winter, so the pairs the model keeps are ones that fitted the past; "
                    "(2) the winter's observed ONI is used, where a forecaster would have had a forecast of it; (3) crop calendars and the two-slope form were fixed with the full record in view; "
                    "(4) FAOSTAT values are the current revision, not what was published then. This is a conditional hindcast, not a vintage forecast. "
                    "Seven pairs only; most early winters are not scored because a pair needs 15 earlier harvests."),
        "caveat": "No winter reached ONI +2.5 before 2015-16, so the size of a very strong winter is barely tested. Few events and pairs: read the counts, not the percentages.",
        "pairs_used": [f"{r['iso']}/{r['crop']}" for r in pairs],
    }, "data": {"events": events, "overall": overall, "by_crop": by_crop, "by_region": by_region}}
    with open(os.path.join(ROOT, "data", "enso_portfolio_hindcast.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False); fh.write("\n")
    print(json.dumps(overall, indent=1))
    for e in events:
        if e.get("scored"):
            g = e["global"]
            print(e["winter"], e["djf_oni"], "pairs", e["pairs_scored"], "pred", g["predicted_kt"], "act", g["actual_kt"],
                  "sign", g["sign_right"], "band", g["in_band"], g.get("ranking") or e.get("ranking", ""))
        else:
            print(e["winter"], e["djf_oni"], "not scored, abstained", e["pairs_abstained"])
    print(json.dumps(by_crop, indent=0)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
