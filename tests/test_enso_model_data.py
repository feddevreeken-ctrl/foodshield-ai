#!/usr/bin/env python3
"""Data-file invariants for the 2026-10-01 model audit (stdlib only): python3 tests/test_enso_model_data.py"""
import json
import math
import sys
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "data"
MAT = {"enso_observations": "observed_operational", "fitted_crop_relationships": "historically_fitted",
       "pair_hindcast_direction": "backtested_held_out", "portfolio_total": "conditionally_backtested_not_vintage",
       "outcomes_above_record_oni": "extrapolation", "production_2027": "scenario_not_forecast"}
fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def load(n):
    return json.loads((D / n).read_text(encoding="utf-8"))


for f in ("enso_model.json", "enso_exposure.json", "enso_outlook.json", "enso_distribution.json"):
    m = load(f)["_meta"]
    check(f"{f}: maturity vocabulary", m.get("maturity") == MAT, m.get("maturity"))
    check(f"{f}: production_ready is false", m.get("production_ready") is False, m.get("production_ready"))

dist = load("enso_distribution.json")
d = dist["data"]
rec = d["target"]["record_oni"]
for p in d["pairs"]:
    lin, cap, ind = p["change_pct"], p["change_pct_capped"], p["change_pct_independent"]
    check(f"{p['key']}: capped response is no larger than linear", abs(cap["p50"]) <= abs(lin["p50"]) + 0.05, (cap["p50"], lin["p50"]))
    sp = p["split_by_record"]
    check(f"{p['key']}: split share equals target share", abs(sp["share_above"] - d["target"]["prob_oni_above_record"]) < 0.002)
    check(f"{p['key']}: independent and joint agree on the median within 2 points", abs(ind["p50"] - lin["p50"]) < 2.0)
check("sensitivity table covers every pair", len(d["sensitivity"]["table"]) == len(d["pairs"]))
check("joint-vs-independent report present", "joint_vs_independent" in dist["_meta"])

rp = load("enso_replay.json")
ev = rp["data"]["events"]
scored = [e for e in ev if e.get("scored")]
check("replay has scored winters", len(scored) >= 4, len(scored))
for e in scored:
    check(f"replay {e['winter']}: input source stated", e["enso_input"]["kind"] in ("iri_plume_september", "jja_persistence_regression"))
    check(f"replay {e['winter']}: said block has up to 3 exposures", 1 <= len(e["said"]["top3"]) <= 3)
    check(f"replay {e['winter']}: pair tonnes add to the total",
          abs(sum(p["predicted_kt"] for p in e["pairs"]) - e["predicted_kt"]) <= len(e["pairs"]))
    check(f"replay {e['winter']}: selection used only earlier data (issued the September before)", e["issued"] == f"{e['djf_year'] - 1}-09")
check("replay lists the variants it tried", rp["data"]["variants"]["variants_tried"] == len(rp["data"]["variants"]["table"]))
sys.exit(1 if fails else 0)
