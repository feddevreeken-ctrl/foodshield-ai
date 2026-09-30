#!/usr/bin/env python3
"""
build_enso_replay.py: what FoodShield would have said in September of each past El Nino winter, scored.

Hand-run (scipy, the FAOSTAT QCL bulk in $FOODSHIELD_CACHE, ~/.cache/foodshield by default). Output data/enso_replay.json is
committed; the cron does not run it. The harness is scripts/enso_replay_lib.py: read its docstring for the vintage rules.

Why this exists (2026-10-01 audit): enso_portfolio_hindcast.json refits each pair on earlier harvests but picks the seven pairs
on the full 1961-2024 record and feeds the observed ONI, so it is a conditional hindcast. Here, for each El Nino winter W
(DJF ONI >= +1.0, at least 15 earlier harvests and 23 earlier yield years for the page's neutral trend):
  (a) pairs are chosen from ALL calendared pairs on harvests before the harvest in question, with the page's rule (Benjamini-Hochberg
      on the El Nino slope p across every pair fitted that winter, q < 0.10, ENSO-specific by the Dipole diagnostic);
  (b) slopes are fitted on harvests through the prior year (the page's centred-moving-average two-slope fit, HAC errors);
  (c) the ENSO input is the forecast of the DJF anomaly available in September of W-1: IRI's published mid-September plume
      average (Nino 3.4; data/enso_forecast_skill.json) where the archive has one (2009, 2015, 2023 among these winters), else a
      persistence regression of DJF ONI on the preceding June-August ONI, fitted on earlier winters only (IRI's archive starts
      in 2004, so 1986, 1991 and 1997 have no published plume in it: this is a proxy and every winter says which it used);
  (d) the neutral baseline is the page's trend through the prior harvest;
  (e) the predicted change per pair and in total is frozen;  (f) it is compared with FAOSTAT's actual harvest.
Variants (M4) are pre-declared in VARIANTS and all are reported; adoption needs better direction AND tonnes error AND a
winter-block bootstrap interval on the improvement that excludes zero.

Run: FOODSHIELD_CACHE=~/.cache/foodshield python3 scripts/build_enso_replay.py
"""
from __future__ import annotations

import gzip
import json
import math
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import enso_replay_lib as R  # noqa: E402
M = R.M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REF = os.path.join(DATA, "ref")
NINO_URL = "https://www.cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii"
NINO_GZ = os.path.join(REF, "cpc_ersst5_nino_mth.txt.gz")
CROP_LABEL = {"corn": "maize", "wheat": "wheat", "rice": "rice", "soybeans": "soy", "sorghum": "sorghum", "barley": "barley", "millet": "millet"}

ADOPTION_RULE = ("A change goes into the production builders only if, on the same winters as the current model, (1) its portfolio direction is right "
                 "in more winters, (2) its mean tonnes error is lower, and (3) a winter-block bootstrap (10,000 resamples of the winters) of the "
                 "mean improvement has a 95% interval above zero. Not because it helps on winters already looked at.")


def winter_label(w: int) -> str:
    return f"{w - 1}-{str(w)[2:]}"


def load_nino() -> dict:
    """ERSST v5 Nino 1+2 and 3.4 anomalies (1991-2020 base) as DJF means keyed by the January year, cached in data/ref."""
    if not os.path.exists(NINO_GZ):
        req = urllib.request.Request(NINO_URL, headers=M.HEADERS)
        txt = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
        with open(NINO_GZ, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
            fh.write(txt.encode("utf-8"))
    with gzip.open(NINO_GZ, "rt", encoding="utf-8") as fh:
        txt = fh.read()
    mon = {}
    for line in txt.splitlines():
        f = line.split()
        if len(f) == 10 and f[0].isdigit():
            mon[(int(f[0]), int(f[1]))] = {"n12": float(f[3]), "n34": float(f[9])}
    out = {"n12": {}, "n34": {}}
    for (y, m) in list(mon):
        if m == 1 and (y - 1, 12) in mon and (y, 2) in mon:
            for k in out:
                out[k][y] = (mon[(y - 1, 12)][k] + mon[(y, 1)][k] + mon[(y, 2)][k]) / 3.0
    return out


def load_jja() -> dict:
    raw = M._cache("oni.ascii.txt", M.ONI_URL).decode("utf-8", "replace")
    out = {}
    for line in raw.splitlines():
        f = line.split()
        if len(f) == 4 and f[0] == "JJA":
            try:
                out[int(f[1])] = float(f[3])
            except ValueError:
                pass
    return out


class Inputs:
    """The ENSO input for each winter and index: observed, or what was knowable in September of W-1."""

    def __init__(self, oni, jja, nino, skill):
        self.idx = {"oni": oni, "n34": nino["n34"], "n12": nino["n12"]}
        base = [y for y in range(1951, 1982) if y in oni and y in nino["n12"]]
        sd_o = float(np.std([oni[y] for y in base])); sd_12 = float(np.std([nino["n12"][y] for y in base]))
        self.blend_scale = sd_o / sd_12
        self.idx["blend"] = {y: 0.5 * oni[y] + 0.5 * nino["n12"][y] * self.blend_scale for y in oni if y in nino["n12"]}
        self.oni, self.jja = oni, jja
        self.iri = {int(r["djf"][:4]) + 1: r for r in skill["rows"]}

    def stat_forecast(self, W):
        """DJF(W) ONI from JJA(W-1) ONI by least squares over the winters before W (June-August is known in September)."""
        ys = [y for y in range(1951, W) if y in self.oni and (y - 1) in self.jja]
        x = np.array([self.jja[y - 1] for y in ys]); y = np.array([self.oni[v] for v in ys])
        b, a = np.polyfit(x, y, 1)
        return float(a + b * self.jja[W - 1]), len(ys)

    def ensoin(self, W, index="oni", mode="forecast"):
        if mode == "observed" or index != "oni":
            return float(self.idx[index][W]), {"kind": "observed", "note": "observed DJF value of the index (oracle input)"}
        if W in self.iri:
            r = self.iri[W]
            return float(r["forecast_avg"]), {"kind": "iri_plume_september", "issued": r["issued"], "models": r["models"],
                                              "model_lo": r["model_lo"], "model_hi": r["model_hi"]}
        v, n = self.stat_forecast(W)
        return v, {"kind": "jja_persistence_regression", "fitted_on_winters": n, "jja_oni": self.jja[W - 1],
                   "note": "no published September plume in IRI's archive for this winter; ONI forecast from June-August ONI, earlier winters only"}


def compute(panel, inputs, winters, dmi, index="oni", mode="forecast", winsor=False, slope="ols"):
    """{W: [records]} for every replayable pair, with the page's selection applied inside each winter."""
    out = {}
    for W in winters:
        o_in, src = inputs.ensoin(W, index, mode)
        idx = inputs.idx[index]
        recs = []
        for key, p in panel.pairs.items():
            r = R.pair_record(p, key, W, idx, o_in, dmi, winsor=winsor, slope=slope)
            if r:
                recs.append(r)
        if recs:
            R.select(recs)
        out[W] = {"records": recs, "input": o_in, "source": src}
    return out


def cells_of(res, pick):
    """{winter: cell} for the records `pick(record)` keeps."""
    cells = {}
    for W, v in res.items():
        items = [r for r in v["records"] if pick(r)]
        if items:
            cells[W] = R.cell(items)
    return cells


def tiered(res, base_pick):
    """Forward-only tier rule (the page's 'moderate' bar): a pair is kept in winter W if, in at least 3 earlier replayed winters
    where it was selected, direction was right with a coin-flip chance of 25% or less and its tonnes error was below the yardstick's."""
    hist = defaultdict(list)
    keep = {}
    for W in sorted(res):
        ks = set()
        for r in res[W]["records"]:
            if not base_pick(r):
                continue
            prev = hist[r["key"]]
            if len(prev) >= 3:
                n, k = len(prev), sum((x["pred_chg_t"] < 0) == (x["act_chg_t"] < 0) for x in prev)
                tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
                e1 = sum(abs(x["pred_chg_t"] - x["act_chg_t"]) for x in prev); e0 = sum(abs(x["act_chg_t"]) for x in prev)
                if tail <= 0.25 and e1 < e0:
                    ks.add(r["key"])
            hist[r["key"]].append(r)
        keep[W] = ks
    return lambda r: base_pick(r) and r["key"] in keep.get(r["W"], set())


def pub(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


def main() -> int:
    os.environ.setdefault("FOODSHIELD_CACHE", os.path.expanduser("~/.cache/foodshield"))
    M.MIN_YEARS = R.MIN_TRAIN            # the page uses 30 on the full record; a replay of the 1980s cannot
    oni, cals, faostat, dmi = M.load_oni(), M.load_calendars(), M.load_faostat(), M.load_dmi()
    skill = json.load(open(os.path.join(DATA, "enso_forecast_skill.json"), encoding="utf-8"))["data"]
    inputs = Inputs(oni, load_jja(), load_nino(), skill)
    panel = R.Panel(faostat, cals)
    outlook = json.load(open(os.path.join(DATA, "enso_outlook.json"), encoding="utf-8"))["data"]["rows_all"]
    shown = {(r["iso"], r["crop"]) for r in outlook if r["status"] == "shown"}
    regions = json.load(open(os.path.join(DATA, "enso_regions.json"), encoding="utf-8"))["data"]["regions"]
    region_of = {}
    for g in regions:
        for iso in g["iso3"]:
            region_of.setdefault(iso, g["label"])
    allw = sorted(w for w, v in oni.items() if v >= R.EVENT_ONI and w >= 1966 and w <= 2024)
    print(f"[INFO] pairs with a calendar and history {len(panel.pairs)}; El Nino winters {[winter_label(w) for w in allw]}")

    sel = lambda r: r["selected"]                                   # noqa: E731
    fixed = lambda r: (r["iso"], r["crop"]) in shown                # noqa: E731 (the seven pairs chosen on the full record)
    base = compute(panel, inputs, allw, dmi)
    obs = compute(panel, inputs, allw, dmi, mode="observed")
    base_cells, obs_cells = cells_of(base, sel), cells_of(obs, sel)
    fix_cells, fix_obs_cells = cells_of(base, fixed), cells_of(obs, fixed)
    winters = sorted(base_cells)

    # ---------------- per-winter record (what FoodShield would have said, and what happened) ----------------
    events = []
    for W in allw:
        v = base[W]
        ev = {"winter": winter_label(W), "djf_year": W, "issued": f"{W - 1}-09", "observed_djf_oni": round(float(oni[W]), 2),
              "enso_input": {"value": round(v["input"], 2), "proxy_for": "DJF ONI", **v["source"]},
              "pairs_replayable": len(v["records"])}
        items = [r for r in v["records"] if r["selected"]]
        if W not in base_cells:
            ev["scored"] = False
            ev["reason"] = "no pair could be replayed" if not v["records"] else "no pair passed the selection on earlier harvests"
            events.append(ev)
            continue
        c = base_cells[W]
        top = sorted(items, key=lambda r: r["pred_chg_t"])[:3]
        said_pairs = [{"pair": r["key"], "crop": CROP_LABEL.get(r["crop"], r["crop"]), "iso": r["iso"],
                       "pct": round(r["pred_pct"], 1), "kt": round(r["pred_chg_t"] / 1e3)} for r in top]
        ev.update(scored=True, selected={"count": len(items), "pairs": sorted(r["key"] for r in items)},
                  predicted_kt=round(c["pred_t"] / 1e3), actual_kt=round(c["act_t"] / 1e3),
                  error_kt=round(abs(c["pred_t"] - c["act_t"]) / 1e3), yardstick_error_kt=round(abs(c["act_t"]) / 1e3),
                  sign_right=bool(c["sign_right"]), false_alarm=bool(c["false_alarm"]),
                  beats_yardstick=bool(c["err_t"] < c["err0_t"]))
        ev["said"] = {"issued": f"September {W - 1}", "enso_input_c": round(v["input"], 2), "enso_input_kind": v["source"]["kind"],
                      "selected_pairs": len(items), "total_kt": round(c["pred_t"] / 1e3), "top3": said_pairs,
                      "line": (f"September {W - 1}: DJF ONI input {v['input']:+.1f} ({'IRI plume' if v['source']['kind'] == 'iri_plume_september' else 'proxy from June-August ONI'}); "
                               f"{len(items)} pairs selected on earlier harvests; largest predicted changes: "
                               + "; ".join(f"{p['iso']} {p['crop']} {p['pct']:+.0f}% ({p['kt']:+,} kt)" for p in said_pairs)
                               + f"; total {round(c['pred_t'] / 1e3):+,} kt.").replace("−", "-")}
        by_crop, by_region = defaultdict(list), defaultdict(list)
        for r in items:
            by_crop[CROP_LABEL.get(r["crop"], r["crop"])].append(r)
            by_region[region_of.get(r["iso"], "Other: " + r["iso"])].append(r)
        for name, grp in (("by_crop", by_crop), ("by_region", by_region)):
            ev[name] = {k: {"pairs": len(g), "predicted_kt": round(sum(i["pred_chg_t"] for i in g) / 1e3),
                            "actual_kt": round(sum(i["act_chg_t"] for i in g) / 1e3),
                            "sign_right": bool((sum(i["pred_chg_t"] for i in g) < 0) == (sum(i["act_chg_t"] for i in g) < 0))}
                        for k, g in grp.items()}
        ev["rank"] = R.rank_stats(items)
        ev["pairs"] = [{"pair": r["key"], "predicted_pct": round(r["pred_pct"], 1), "predicted_kt": round(r["pred_chg_t"] / 1e3),
                        "actual_pct": round((r["actual_t"] / r["neutral_t"] - 1) * 100, 1), "actual_kt": round(r["act_chg_t"] / 1e3),
                        "sign_right": bool((r["pred_chg_t"] < 0) == (r["act_chg_t"] < 0)), "q_nino": round(r["q_nino"], 4)} for r in sorted(items, key=lambda r: r["pred_chg_t"])]
        if W in obs_cells:
            oc = obs_cells[W]
            ev["with_observed_oni"] = {"selected": sum(1 for r in obs[W]["records"] if r["selected"]), "predicted_kt": round(oc["pred_t"] / 1e3),
                                       "sign_right": bool(oc["sign_right"])}
        if W in fix_cells:
            fc = fix_cells[W]
            ev["fixed_outlook_pairs"] = {"pairs": fc["pairs"], "predicted_kt": round(fc["pred_t"] / 1e3), "sign_right": bool(fc["sign_right"])}
        events.append(ev)

    # ---------------- summary and pair-level ----------------
    sel_items = [r for W in winters for r in base[W]["records"] if r["selected"]]
    all_items = [r for W in winters for r in base[W]["records"]]
    summary = {"winters_total": len(allw), "winters_scored": len(winters), "winters_scored_list": [winter_label(w) for w in winters],
               "winters_not_scored": [winter_label(w) for w in allw if w not in winters],
               "portfolio": R.score_winters(base_cells),
               "rank": {"winters": sum(1 for e in events if e.get("rank")),
                        "mean_spearman": (lambda v: round(float(np.mean(v)), 2) if v else None)([e["rank"]["spearman"] for e in events if e.get("rank") and e["rank"]["spearman"] is not None]),
                        "worst_pair_right": sum(1 for e in events if e.get("rank") and e["rank"]["worst_predicted"] == e["rank"]["worst_actual"])},
               "pair_level_selected": R.pair_scores(sel_items), "pair_level_all_replayable_pairs": R.pair_scores(all_items),
               "selected_pairs_per_winter": {winter_label(w): sum(1 for r in base[w]["records"] if r["selected"]) for w in allw},
               "brier_selected_pairs": R.brier(sel_items),
               "with_observed_oni": R.score_winters(obs_cells),
               "coverage_note": "coverage = winters scored / El Nino winters; 1965-66, 1972-73 and 1982-83 have fewer than 23 earlier yield years, which the page's neutral trend needs."}

    pf = json.load(open(os.path.join(DATA, "enso_portfolio_hindcast.json"), encoding="utf-8"))["data"]
    pf_by = {e["winter"]: e for e in pf["events"] if e.get("scored")}
    cmp_rows = []
    for e in events:
        p = pf_by.get(e["winter"])
        cmp_rows.append({"winter": e["winter"], "replay_scored": bool(e.get("scored")), "replay_pairs": (e.get("selected") or {}).get("count"),
                         "replay_predicted_kt": e.get("predicted_kt"), "replay_actual_kt": e.get("actual_kt"), "replay_sign_right": e.get("sign_right"),
                         "full_record_scored": bool(p), "full_record_predicted_kt": p["global"]["predicted_kt"] if p else None,
                         "full_record_actual_kt": p["global"]["actual_kt"] if p else None, "full_record_sign_right": p["global"]["sign_right"] if p else None})
    common = [w for w in winters if winter_label(w) in pf_by]
    pf_common = {w: {"err_t": abs(pf_by[winter_label(w)]["global"]["predicted_kt"] - pf_by[winter_label(w)]["global"]["actual_kt"]) * 1e3,
                     "err0_t": abs(pf_by[winter_label(w)]["global"]["actual_kt"]) * 1e3,
                     "sign_right": pf_by[winter_label(w)]["global"]["sign_right"],
                     "false_alarm": pf_by[winter_label(w)]["global"]["false_alarm"], "predicted_fall": pf_by[winter_label(w)]["global"]["predicted_kt"] < 0,
                     "pred_t": pf_by[winter_label(w)]["global"]["predicted_kt"] * 1e3, "act_t": pf_by[winter_label(w)]["global"]["actual_kt"] * 1e3} for w in common}
    comparison = {
        "rows": cmp_rows, "common_winters": [winter_label(w) for w in common],
        "on_common_winters": {"selection_safe_replay": R.score_winters({w: base_cells[w] for w in common}),
                              "same_harness_pairs_chosen_on_full_record": R.score_winters({w: fix_cells[w] for w in common if w in fix_cells}),
                              "enso_portfolio_hindcast_json_full_record_selection_observed_oni": R.score_winters(pf_common)},
        "same_harness_fixed_pairs_all_scored_winters": R.score_winters(fix_cells),
        "same_harness_fixed_pairs_observed_oni": R.score_winters(fix_obs_cells),
        "note": ("Three like-for-like readings. 'same_harness_pairs_chosen_on_full_record' uses this replay's baseline and ENSO input but the seven pairs "
                 "the outlook shows (chosen on the full record): the difference to the replay is the price of honest selection. The portfolio-hindcast "
                 "file uses observed ONI and a yield-anomaly yardstick, so its tonnes are not on this replay's scale; compare direction and false alarms, "
                 "not kt."),
    }

    # ---------------- M4 variants, fixed in advance ----------------
    variants = []

    def add(vid, label, cells, baseline_cells, extra=None, adoptable=True):
        row = {"id": vid, "label": label, **R.score_winters(cells)}
        common_w = sorted(set(cells) & set(baseline_cells))
        row["baseline_same_winters"] = R.score_winters({w: baseline_cells[w] for w in common_w})
        b = R.boot_improvement(baseline_cells, cells)
        row["bootstrap_mae_improvement"] = b
        vs, bs = row.get("sign_right"), row["baseline_same_winters"].get("sign_right")
        row["more_winters_direction_right"] = bool(common_w and R.score_winters({w: cells[w] for w in common_w})["sign_right"] > bs)
        row["lower_mae"] = bool(common_w and R.score_winters({w: cells[w] for w in common_w})["mae_kt"] < row["baseline_same_winters"]["mae_kt"])
        row["adopted"] = bool(adoptable and row["more_winters_direction_right"] and row["lower_mae"] and b.get("excludes_zero_positive"))
        if extra:
            row.update(extra)
        variants.append(row)

    huber = compute(panel, inputs, allw, dmi, slope="huber")
    add("V1", "Huber (robust) El Nino slope instead of OLS; selection unchanged", cells_of(huber, sel), base_cells)
    wins = compute(panel, inputs, allw, dmi, winsor=True)
    add("V2", "Yield anomalies winsorised at the training window's 5th and 95th percentile before selection and fitting", cells_of(wins, sel), base_cells)
    pick_tier = tiered(base, sel)
    add("V5", "Quality tiers, forward-only: keep a selected pair only if, on at least 3 earlier replayed winters, direction was right (coin-flip chance <= 25%) and its error beat the yardstick", cells_of(base, pick_tier), base_cells)
    for vid, ix, lab in (("V4a", "n34", "Nino 3.4 (ERSST v5, fixed 1991-2020 base) instead of ONI"), ("V4b", "n12", "Nino 1+2 instead of ONI"),
                         ("V4c", "blend", "half ONI, half Nino 1+2 rescaled to ONI's spread (scale from 1951-1981 only)")):
        r_ = compute(panel, inputs, allw, dmi, index=ix, mode="observed")
        add(vid, lab + ". Compared with V0 on observed ONI, because no September forecast of this index exists", cells_of(r_, sel), obs_cells,
            extra={"input": "observed DJF value of the index"})
    # V3 is a scoring change, not a prediction change: probabilistic P(fall) per pair, Brier against always-50%.
    v3 = R.brier(sel_items)
    v3_fixed = R.brier([r for W in winters for r in base[W]["records"] if fixed(r)])
    variants.append({"id": "V3", "label": "Probabilistic: P(fall) per pair from the slope, its standard error and the fit's residual spread; scored by Brier against always 50%",
                     "selection_safe_pairs": v3, "pairs_chosen_on_full_record": v3_fixed, "adopted": False,
                     "why_not_adopted": "It changes what is said, not the tonnes, so the direction-and-tonnes adoption rule cannot adopt it; reported as a skill score only."})

    tried = len(variants)
    variants_block = {"adoption_rule": ADOPTION_RULE, "variants_tried": tried,
                      "baseline_V0": {"forecast_input": R.score_winters(base_cells), "observed_oni_input": R.score_winters(obs_cells)},
                      "table": variants, "adopted": [v["id"] for v in variants if v.get("adopted")],
                      "multiplicity": (f"{tried} variants (V1 to V5, V4 has three index choices) were declared before any was scored, on at most "
                                       f"{len(winters)} winters. With that few winters a bootstrap interval above zero needs the variant to win in nearly every winter; "
                                       "with this many variants, one looking good by luck is expected, so nothing is adopted on a single favourable row.")}

    payload = {"_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(), "version": "v1",
        "builder": "scripts/build_enso_replay.py with scripts/enso_replay_lib.py (hand-run, numpy and scipy; output committed)",
        "maturity": {"portfolio_total": "conditionally_backtested_not_vintage"},
        "sources": {"yields": "FAOSTAT QCL production and yield (https://bulks-faostat.fao.org/production/, CC BY-NC-SA 3.0 IGO), current revision not as published then",
                    "oni": "NOAA CPC oni.ascii.txt", "nino": f"{NINO_URL} (cached data/ref/cpc_ersst5_nino_mth.txt.gz; public domain)",
                    "forecast": "data/enso_forecast_skill.json: IRI mid-September plume, combined model average for DJF (Nino 3.4 on the models' base period) used as the ONI input",
                    "neutral": "trend method of scripts/build_enso_neutral.py (build_enso_neutral.trend)", "model": "scripts/build_enso_model.py (detrend, fit, bh_q, shift_for)"},
        "method": (
            "As of September of the winter's first year. Pairs: all calendared country-crop pairs with at least 23 earlier yield years; El Nino slope p on earlier "
            "harvests only (page fit), Benjamini-Hochberg q < 0.10 across every pair fitted that winter, ENSO-specific by the Dipole diagnostic. Slopes: the page's "
            "two-slope fit on earlier harvests. Baseline: the page's neutral trend through the prior harvest, scaled by the last known harvest. ENSO input: IRI "
            "September plume average if the archive has that year, else a forward-only regression of DJF ONI on June-August ONI. Predicted change = baseline x "
            "(exp(slope x input) - 1); actual change = FAOSTAT production minus baseline; the no-effect yardstick predicts zero."),
        "proxy_statement": "The published forecast of the DJF Nino 3.4 anomaly stands in for the ONI. IRI's archive does not reach 1986, 1991 or 1997: those winters use a June-August persistence regression, labelled per winter in enso_input.kind.",
        "differences_from_page": ("At least 15 earlier harvests instead of 30 years for a pair to be fitted; the centred moving-average detrend is cut at the harvest before "
                                  "the one predicted; FAOSTAT is the current revision; crop calendars and the two-slope form were fixed with the full record in view."),
        "honesty": ("This is the most honest test the data allows, not a vintage forecast. Few winters: read the counts. Predicted tonnes of different crops are added "
                    "as the page adds them. A poor result is the result: nothing here was tuned."),
        "adoption": ADOPTION_RULE,
    }, "data": {"events": events, "summary": summary, "comparison_full_record_selection": comparison, "variants": variants_block}}
    with open(os.path.join(DATA, "enso_replay.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    from pipeline_dag import stamp_file
    stamp_file("enso_replay.json")
    print(json.dumps(summary["portfolio"]), json.dumps(summary["selected_pairs_per_winter"]))
    for e in events:
        if e.get("scored"):
            print(e["winter"], e["enso_input"]["value"], e["enso_input"]["kind"], "sel", e["selected"]["count"], "pred", e["predicted_kt"], "act", e["actual_kt"], "sign", e["sign_right"])
        else:
            print(e["winter"], "not scored", e["reason"])
    for v in variants:
        print(v["id"], {k: v.get(k) for k in ("winters", "sign_right", "mae_kt", "mae_yardstick_kt", "adopted")}, v.get("bootstrap_mae_improvement"))
    print(json.dumps(comparison["on_common_winters"], indent=0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
