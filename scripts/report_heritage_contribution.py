#!/usr/bin/env python3
"""
FDRS climate/conflict heritage-contribution report.

The climate (c[4]) and conflict (c[5]) components are a 60% sourced / 40% heritage blend
(see FDRS_CLIMATE_CONFLICT_BLEND.md). This report shows WHERE the 40% heritage anchor matters
most — i.e. the countries whose blended climate/conflict score differs most from a pure-sourced
score. Those are the countries whose FDRS leans most on the editorial baseline, and the ones to
review if you ever consider shifting the blend toward pure-sourced.

NOTE: the sourced climate/conflict scores are computed in the BROWSER from live feeds
(INFORM/Aqueduct/CCKP/ACLED/WGI/LPI), so this script works against `_cOriginal` (heritage,
preserved in the data) vs the current blended `c.c` ONLY when the data file already carries the
blended values. If `_cOriginal` isn't persisted (it's a runtime field), the script reports that
the comparison must be run from a browser-exported snapshot — and explains how.

Run:  python3 scripts/report_heritage_contribution.py
"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNTRIES = os.path.join(ROOT, "data", "countries.json")
W = [0.23, 0.16, 0.11, 0.09, 0.09, 0.08, 0.06, 0.12, 0.06]
BLEND_SOURCED = 0.60
BLEND_HERITAGE = 0.40

def fdrs(cv):
    base = sum(W[i] * (cv[i] if i < len(cv) and cv[i] is not None else 0) for i in range(9))
    c0 = cv[0] if cv and cv[0] is not None else 0
    c7 = cv[7] if len(cv) > 7 and cv[7] is not None else 0
    return max(0, min(100, round(base + min(6*(c0/100)*(c7/100), 6))))

def main():
    d = json.load(open(COUNTRIES)); C = d["data"]["countries"]
    have_original = 0
    rows = []
    for iso, r in C.items():
        if len(iso) != 3 or iso.startswith("US-"): continue
        c = r.get("c"); cv = c.get("value") if isinstance(c, dict) else c
        orig = r.get("_cOriginal")  # heritage vector, if persisted
        if not isinstance(cv, list) or len(cv) < 9: continue
        if isinstance(orig, list) and len(orig) >= 9:
            have_original += 1
            # difference the blend introduced on climate(4) + conflict(5)
            d4 = (cv[4] or 0) - (orig[4] or 0)
            d5 = (cv[5] or 0) - (orig[5] or 0)
            # FDRS with heritage-only c4/c5 vs blended
            heritage_cv = cv.copy(); heritage_cv[4] = orig[4]; heritage_cv[5] = orig[5]
            fdrs_blended = fdrs(cv); fdrs_heritage = fdrs(heritage_cv)
            rows.append((iso, r.get("n") or "", d4, d5, fdrs_heritage, fdrs_blended,
                         fdrs_blended - fdrs_heritage))

    if not rows:
        print("No countries carry a persisted `_cOriginal` heritage vector in data/countries.json.")
        print("That field is created at RENDER time in the browser, so the exact heritage-vs-blended")
        print("comparison must be exported from a live page. The blend ratio is fixed and documented:")
        print(f"  climate/conflict = heritage × {BLEND_HERITAGE} + sourced × {BLEND_SOURCED}")
        print("  weights: climate 0.09, conflict 0.08 -> at most ~0.068 of the score is heritage-anchored.")
        print("  A 30-pt heritage/sourced gap on a component => at most ~2 FDRS points.")
        print("\nTo get the per-country numbers: in the browser console on the live page, run")
        print("  copy(JSON.stringify(COUNTRIES.map(c=>({iso:c.iso,c:c.c,o:c._cOriginal}))))")
        print("and save it as data/_blend_snapshot.json, then extend this script to read it.")
        return 0

    rows.sort(key=lambda x: -abs(x[6]))
    print(f"{have_original} countries with a heritage vector. Top heritage-driven FDRS differences:\n")
    print(f"  {'ISO':4} {'climate Δ':>9} {'conflict Δ':>10} {'FDRS heritage':>13} {'FDRS blended':>12} {'Δ':>4}")
    for iso, name, d4, d5, fh, fb, df in rows[:25]:
        print(f"  {iso:4} {d4:>9} {d5:>10} {fh:>13} {fb:>12} {df:>+4}")
    big = [r for r in rows if abs(r[6]) >= 5]
    print(f"\n{len(big)} countries where the blend moves FDRS by >=5 points.")
    return 0

if __name__ == "__main__":
    main()
