#!/usr/bin/env python3
"""
FDRS v2 rank-diff reporter.

Two modes:
  (1) snapshot  — compute every sovereign's FDRS with the current weights and save a baseline
  (2) diff      — recompute with NEW weights (or after a data change) and show what MOVED:
                  top rank movers, score-distribution shift, risk-band counts, correlation.

This is the missing validation layer: before changing a weight, run a diff to SEE the ranking
impact instead of changing it blind. It also back-tests directionally — flag whether the
ranking aligns with known high-vulnerability countries.

Run:
  python3 scripts/report_fdrs_rank_diffs.py snapshot          # save current as baseline
  python3 scripts/report_fdrs_rank_diffs.py diff              # current weights vs baseline
  python3 scripts/report_fdrs_rank_diffs.py diff --weights 0.25,0.16,0.11,0.09,0.09,0.08,0.06,0.10,0.06
"""
import argparse, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNTRIES = os.path.join(ROOT, "data", "countries.json")
BASELINE = os.path.join(ROOT, "data", "_fdrs_baseline.json")
DEFAULT_W = [0.23, 0.16, 0.11, 0.09, 0.09, 0.08, 0.06, 0.12, 0.06]

# directional back-test anchors: countries widely regarded as high food-insecurity (IPC/WFP).
HIGH_VULN = {"SOM","YEM","SSD","AFG","SDN","COD","HTI","TCD","ETH","SYR","MLI","NER","BFA","NGA","CAF"}

def fdrs(cv, w):
    base = sum(w[i] * (cv[i] if i < len(cv) and cv[i] is not None else 0) for i in range(9))
    c0 = cv[0] if cv and cv[0] is not None else 0
    c7 = cv[7] if len(cv) > 7 and cv[7] is not None else 0
    return max(0, min(100, round(base + min(6*(c0/100)*(c7/100), 6))))

def all_scores(w):
    d = json.load(open(COUNTRIES)); C = d["data"]["countries"]
    out = {}
    for iso, r in C.items():
        if len(iso) != 3 or iso.startswith("US-"): continue
        c = r.get("c"); cv = c.get("value") if isinstance(c, dict) else c
        if isinstance(cv, list) and len(cv) >= 9:
            out[iso] = fdrs(cv, w)
    return out

def dist(scores):
    import statistics as st
    v = sorted(scores.values())
    bands = {"low(<30)": 0, "mod(30-50)": 0, "high(50-70)": 0, "crit(70+)": 0}
    for s in v:
        if s < 30: bands["low(<30)"] += 1
        elif s < 50: bands["mod(30-50)"] += 1
        elif s < 70: bands["high(50-70)"] += 1
        else: bands["crit(70+)"] += 1
    return {"n": len(v), "min": min(v), "median": st.median(v), "mean": round(st.mean(v),1),
            "max": max(v), "bands": bands}

def backtest(scores):
    """Directional check: do known-high-vulnerability countries rank in the top tier?"""
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    n = len(ranked)
    top_third = set(iso for iso,_ in ranked[:n//3])
    present = HIGH_VULN & set(scores.keys())
    in_top = present & top_third
    return len(in_top), len(present), sorted(present - in_top)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["snapshot", "diff"])
    ap.add_argument("--weights", default="")
    args = ap.parse_args()
    w = [float(x) for x in args.weights.split(",")] if args.weights else DEFAULT_W
    if len(w) != 9 or abs(sum(w)-1.0) > 1e-6:
        print(f"weights must be 9 numbers summing to 1.0 (got {len(w)}, sum {sum(w):.3f})"); return 1

    cur = all_scores(w)
    if args.mode == "snapshot":
        json.dump({"weights": w, "scores": cur}, open(BASELINE, "w"), indent=1)
        d = dist(cur)
        print(f"Snapshot saved: {d['n']} countries. {d}")
        hit, tot, missed = backtest(cur)
        print(f"Back-test: {hit}/{tot} known high-vulnerability countries rank in the top third.")
        if missed: print(f"  NOT in top third (worth a look): {missed}")
        return 0

    # diff
    if not os.path.exists(BASELINE):
        print("No baseline. Run `snapshot` first."); return 1
    base = json.load(open(BASELINE))
    old = base["scores"]; oldw = base["weights"]
    print(f"Baseline weights: {oldw}")
    print(f"Current weights:  {w}\n")
    print("Distribution — baseline vs current:")
    print(f"  baseline: {dist(old)}")
    print(f"  current : {dist(cur)}\n")
    # rank movers
    common = set(old) & set(cur)
    moves = sorted(((iso, cur[iso]-old[iso]) for iso in common), key=lambda x: -abs(x[1]))
    print("Top 20 score movers (current - baseline):")
    for iso, dlt in moves[:20]:
        if dlt == 0: break
        print(f"  {iso}: {old[iso]:3} -> {cur[iso]:3}  ({'+' if dlt>0 else ''}{dlt})")
    # correlation
    import statistics as st
    xs = [old[i] for i in common]; ys = [cur[i] for i in common]
    try:
        corr = st.correlation(xs, ys)
        print(f"\nRank correlation (baseline vs current): {corr:.4f}  (1.0 = identical ordering)")
    except Exception:
        pass
    hit, tot, missed = backtest(cur)
    print(f"Back-test: {hit}/{tot} known high-vulnerability countries in top third (current weights).")
    if missed: print(f"  NOT in top third: {missed}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
