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

2026-09-30 audit (model_tests in the output): the same walk is repeated with the pair
selection re-run inside each winter on earlier data only (leak-free selection), and with
ten pre-declared variants (empirical-Bayes shrinkage, pooled slopes, forward gates). None
met the adoption rule, so the fit itself is unchanged; per-pair forward scores (by_pair)
feed the model_quality tiers in build_enso_outlook.py.

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


# ---------------------------------------------------------------------------------------------
# Model tests (2026-09-30). Every variant is scored on the same forward walk: a pair is fitted on
# earlier harvests only, and every choice that depends on data (which pairs, how much to shrink)
# uses only what was known before that winter. The variants were fixed before scoring; all are listed.
# ---------------------------------------------------------------------------------------------
ADOPT_RULE = ("A change is adopted only if, on the same winters as the current fit, the portfolio's forward mean absolute error falls, "
              "its direction-right count rises and its false alarms do not rise. A variant that scores fewer winters is reported but not "
              "adopted: dropping the winters the fit got wrong is not accuracy.")


def _bh(ps):
    m = len(ps); order = sorted(range(m), key=lambda i: ps[i]); q = [1.0] * m; run = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]; run = min(run, ps[i] * m / rank); q[i] = run
    return q


def _all_pair_records(oni, cal, panel, shown, region_of):
    """One record per (pair, El Nino winter) for EVERY calendared pair, fitted on earlier harvests only."""
    keep, M.MIN_YEARS = M.MIN_YEARS, 12
    recs = []
    try:
        for iso in panel:
            for crop, series in panel[iso].items():
                yrs = sorted(y for y, v in series.items() if v.get("yield"))
                shift = M.shift_for((cal.get(iso) or {}).get(crop, {}))
                if shift is None or len(yrs) < 20:
                    continue
                ty, ta = H.trailing(yrs, [series[y]["yield"] for y in yrs])
                data = [(y, a, oni.get(y + shift)) for y, a in zip(ty, ta) if oni.get(y + shift) is not None]
                for hy, act, o in [d for d in data if d[2] >= H.EVENT_ONI]:
                    past = [d for d in data if d[0] < hy]
                    if len(past) < H.MIN_TRAIN or not series.get(hy, {}).get("prod"):
                        continue
                    pred, base, enso, sd = H.score(past, act, o)
                    b, _cov, _s = H.ols([d[2] for d in past], [d[1] for d in past])
                    f = M.fit([d[0] for d in past], [d[1] for d in past], oni, shift)
                    prod = series[hy]["prod"]; e0 = prod * math.exp(base - act)
                    recs.append({"key": f"{iso}/{crop}", "iso": iso, "w": int(hy + shift), "o": float(o), "act": act, "base": base,
                                 "enso": enso, "b1": float(b[1]), "mp": float(np.mean([max(d[2], 0) for d in past])),
                                 "e0": e0, "act_t": prod - e0, "pn": f["p_nino"] if f else 1.0,
                                 "sen": min(f["se_nino"], 5.0) if f else 5.0, "bn": f["b_nino"] if f else 0.0,
                                 "shown": (iso, crop) in shown, "region": region_of.get((iso, crop)) or "_" + iso})
    finally:
        M.MIN_YEARS = keep
    return recs


def _shrink_factors(by):
    """Empirical-Bayes factors per winter from the pairs' own earlier-data slopes (forward only)."""
    for rs in by.values():
        b = np.array([r["bn"] for r in rs]); se2 = np.array([r["sen"] ** 2 for r in rs])
        tau0 = max(0.0, float(np.mean(b ** 2) - np.mean(se2)))
        grp = defaultdict(list)
        for r in rs:
            grp[r["region"]].append(r)
        dev = []
        for r in rs:
            oth = [x for x in grp[r["region"]] if x is not r]
            if oth:
                wt = np.array([1 / x["sen"] ** 2 for x in oth]); r["mu"] = float(np.sum(wt * np.array([x["bn"] for x in oth])) / wt.sum())
            else:
                r["mu"] = 0.0
            dev.append(r["bn"] - r["mu"])
        tauR = max(0.0, float(np.mean(np.array(dev) ** 2) - np.mean(se2)))
        for r in rs:
            r["shr0"] = tau0 / (tau0 + r["sen"] ** 2) if tau0 > 0 else 0.0
            r["shrR"] = tauR / (tauR + r["sen"] ** 2) if tauR > 0 else 0.0


def _cells(sel, fn):
    cells = []
    for w in sorted(sel):
        its = sel[w]
        if its:
            cells.append((sum(r["e0"] * (math.exp(fn(r)) - 1) for r in its), sum(r["act_t"] for r in its), len(its)))
    n = len(cells)
    if not n:
        return {"winters": 0}
    return {"winters": n, "mean_pairs": round(sum(c[2] for c in cells) / n, 1),
            "sign_right": sum((c[0] < 0) == (c[1] < 0) for c in cells),
            "mae_kt": round(sum(abs(c[0] - c[1]) for c in cells) / n / 1e3),
            "mae_yardstick_kt": round(sum(abs(c[1]) for c in cells) / n / 1e3),
            "bias_kt": round(sum(c[0] - c[1] for c in cells) / n / 1e3),
            "beats_yardstick": sum(abs(c[0] - c[1]) < abs(c[1]) for c in cells),
            "false_alarms": sum(1 for c in cells if c[0] < 0 and c[1] > 0),
            "predicted_falls": sum(1 for c in cells if c[0] < 0)}


def _pair_level(rs, fn):
    n = len(rs)
    return {"pair_winters": n, "sign_right": sum((fn(r) < 0) == (r["act"] - r["base"] < 0) for r in rs),
            "beats_yardstick": sum(abs(r["act"] - r["base"] - fn(r)) < abs(r["act"] - r["base"]) for r in rs),
            "false_alarms": sum(1 for r in rs if fn(r) < 0 and r["act"] - r["base"] > 0)}


def model_tests(oni, cal, panel, shown, region_of):
    recs = _all_pair_records(oni, cal, panel, shown, region_of)
    by = defaultdict(list)
    for r in recs:
        by[r["w"]].append(r)
    _shrink_factors(by)
    def enso_b(r, b1n):   # the fit's El Nino term with its El Nino slope replaced
        return r["enso"] + (b1n - r["b1"]) * (r["o"] - r["mp"])
    zero = lambda r: r["enso"]
    shr0 = lambda r: enso_b(r, r["b1"] * r["shr0"])
    shrR = lambda r: enso_b(r, r["mu"] + (r["b1"] - r["mu"]) * r["shrR"])
    pool = lambda r: enso_b(r, r["mu"])
    sel_shown = {w: [r for r in rs if r["shown"]] for w, rs in by.items()}
    sel_gate = {}
    for w, rs in by.items():
        q = _bh([r["pn"] for r in rs])
        sel_gate[w] = [r for r, qq in zip(rs, q) if qq < 0.10]
    hist = defaultdict(list)
    for w in sorted(by):
        for r in sel_shown[w]:
            hist[r["key"]].append((w, r))
    sel_perf = {}
    for w in by:
        ok = []
        for r in sel_shown[w]:
            prev = [x for ww, x in hist[r["key"]] if ww < w]
            if len(prev) >= 3:
                sr = sum((x["enso"] < 0) == (x["act"] - x["base"] < 0) for x in prev)
                e1 = sum(abs(x["act"] - x["base"] - x["enso"]) for x in prev); e0 = sum(abs(x["act"] - x["base"]) for x in prev)
                if sr >= math.ceil(2 * len(prev) / 3) and e1 < e0:
                    ok.append(r)
        sel_perf[w] = ok
    sel_both = {w: [r for r in sel_gate[w] if r["shown"]] for w in by}
    V = [
        ("A0", "Current fit, the seven pairs the outlook shows (selected on the full record)", sel_shown, zero),
        ("A1", "A0 with each pair's El Niño slope shrunk toward zero (empirical Bayes, strength set from the pairs' earlier-data slopes)", sel_shown, shr0),
        ("A2", "A0 with each slope shrunk toward the precision-weighted mean of the other pairs in its region", sel_shown, shrR),
        ("A3", "Pair selection re-run inside each winter: all calendared pairs, El Niño slope q < 0.10 (Benjamini-Hochberg) on earlier data only", sel_gate, zero),
        ("A4", "A3 with slopes shrunk toward zero", sel_gate, shr0),
        ("A5", "A0 pairs that, on at least 3 earlier scored winters, had direction right in two thirds and lower error than the yardstick", sel_perf, zero),
        ("A6", "A0 pairs using the region-pooled slope only (no per-pair slope)", sel_shown, pool),
        ("A7", "All calendared pairs, no gate, slopes shrunk toward zero", by, shr0),
        ("A8", "All calendared pairs, no gate, unshrunk", by, zero),
        ("A9", "A0 pairs that also pass the A3 gate on earlier data", sel_both, zero),
    ]
    variants = []
    for vid, label, sel, fn in V:
        variants.append(dict(id=vid, label=label, **_cells(sel, fn)))
    a0 = variants[0]
    # A9 and A5 score fewer winters: A0 on exactly those winters, for a like-for-like comparison.
    variants[5]["a0_same_winters"] = _cells({w: sel_shown[w] for w in by if sel_perf.get(w)}, zero)
    variants[9]["a0_same_winters"] = _cells({w: sel_shown[w] for w in by if sel_both.get(w)}, zero)
    variants[3]["a0_same_winters"] = _cells({w: sel_shown[w] for w in by if sel_gate.get(w)}, zero)
    pl = {"all_pair_winters": _pair_level(recs, zero), "outlook_pairs": _pair_level([r for r in recs if r["shown"]], zero),
          "rule_selected_on_earlier_data": _pair_level([r for ws in sel_gate.values() for r in ws], zero),
          "rule_selected_not_outlook_pairs": _pair_level([r for ws in sel_gate.values() for r in ws if not r["shown"]], zero),
          "outlook_pairs_shrunk_to_zero": _pair_level([r for r in recs if r["shown"]], shr0)}
    pl["outlook_pairs_also_passing_earlier_gate"] = sum(1 for ws in sel_gate.values() for r in ws if r["shown"])
    pl["outlook_pair_winters"] = sum(1 for r in recs if r["shown"])
    # Does ENSO act through area where the fit looks at yield? El Nino slope on trailing log-AREA anomalies, same alignment.
    area = {}
    for (iso, crop) in sorted(shown):
        series = panel[iso][crop]
        shift = M.shift_for((cal.get(iso) or {}).get(crop, {}))
        ys = sorted(y for y, v in series.items() if v.get("area") and v.get("yield"))
        ty, ta = H.trailing(ys, [series[y]["area"] for y in ys])
        fa = M.fit(ty, ta, oni, shift)
        ty2, ta2 = H.trailing(ys, [series[y]["yield"] for y in ys])
        fy = M.fit(ty2, ta2, oni, shift)
        if fa and fy:
            area[f"{iso}/{crop}"] = {"yield_pct_per_oni": round(fy["b_nino"] * 100, 1), "area_pct_per_oni": round(fa["b_nino"] * 100, 1),
                                     "p_yield": round(fy["p_nino"], 3), "p_area": round(fa["p_nino"], 3)}
    return {"adoption_rule": ADOPT_RULE, "variants_tried": len(V), "variants": variants,
            "pair_level": pl, "area_check": area,
            "note": ("Ten variants were fixed before scoring and all are listed, so a variant that looks good at this sample size "
                     "(seven winters) could be luck. Pair selection re-run on earlier data (A3, A4) is the leak-free test: "
                     "it is the rule the page uses, applied to what was known then.")}


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
    # Per-pair forward scores in tonnes: the basis of the model_quality tiers in build_enso_outlook.py.
    pair_cells = defaultdict(list)
    for e in events:
        for p in e.get("pairs", []):
            pair_cells[p["pair"]].append((p["predicted_kt"], p["actual_kt"]))
    by_pair = {k: {"events": len(v), "sign_right": sum((p < 0) == (a < 0) for p, a in v),
                   "beats_yardstick": sum(abs(p - a) < abs(a) for p, a in v),
                   "mae_kt": round(sum(abs(p - a) for p, a in v) / len(v)), "mae_yardstick_kt": round(sum(abs(a) for _, a in v) / len(v)),
                   "false_alarms": sum(1 for p, a in v if p < 0 and a > 0), "predicted_falls": sum(1 for p, a in v if p < 0)}
               for k, v in pair_cells.items()}
    for v in by_pair.values():
        v["beats_yardstick_mae"] = bool(v["mae_kt"] < v["mae_yardstick_kt"])
    tests = model_tests(oni, cal, panel, {(r["iso"], r["crop"]) for r in pairs}, region_of)
    by_crop = {k: dict(score_cells(v), label=CROP_LABEL.get(k, k)) for k, v in crop_cells.items()}
    by_region = {k: score_cells(v) for k, v in reg_cells.items()}

    payload = {"_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(), "version": "v2", "builder": "scripts/build_enso_portfolio_hindcast.py (hand-run, needs numpy and scipy; output committed)",
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
        "leakage": ("Leakage that remains: (1) the pairs were chosen on the full 1961-2024 record, and that selection is not re-run inside each winter, so the pairs the model keeps are ones that fitted the past (model_tests re-runs the selection inside each winter: variants A3 and A4); "
                    "(2) the winter's observed ONI is used, where a forecaster would have had a forecast of it; (3) crop calendars and the two-slope form were fixed with the full record in view; "
                    "(4) FAOSTAT values are the current revision, not what was published then. This is a conditional hindcast, not a vintage forecast. "
                    "Seven pairs only; most early winters are not scored because a pair needs 15 earlier harvests."),
        "caveat": "No winter reached ONI +2.5 before 2015-16, so the size of a very strong winter is barely tested. Few events and pairs: read the counts, not the percentages.",
        "pairs_used": [f"{r['iso']}/{r['crop']}" for r in pairs],
    }, "data": {"events": events, "overall": overall, "by_crop": by_crop, "by_region": by_region, "by_pair": by_pair, "model_tests": tests}}
    with open(os.path.join(ROOT, "data", "enso_portfolio_hindcast.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False); fh.write("\n")
    from pipeline_dag import stamp_file; stamp_file("enso_portfolio_hindcast.json")
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
