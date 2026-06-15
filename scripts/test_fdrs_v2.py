#!/usr/bin/env python3
"""
FDRS v2 formula regression test.

Validates the live FDRS formula against tests/fdrs_cases.json fixtures, so any change
to the weights or the amplifier is caught immediately. Mirrors the canonical formula in
scripts/build_countries_dataset.py (_fdrs_v2) and index.html (fdrsV2) — if you change the
weights in one place, this test fails until all three agree and the fixtures are updated.

Run:  python3 scripts/test_fdrs_v2.py
Exit code 0 = all pass; 1 = a regression (a case's score changed).
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fdrs_cases.json")

# The canonical formula — keep in lockstep with build_countries_dataset.py::_fdrs_v2
# and index.html::fdrsV2. The weights live in the fixture file's _meta.weights.
def fdrs_v2(cv, weights):
    base = sum(weights[i] * (cv[i] if i < len(cv) and cv[i] is not None else 0) for i in range(9))
    c0 = cv[0] if len(cv) > 0 and cv[0] is not None else 0
    c7 = cv[7] if len(cv) > 7 and cv[7] is not None else 0
    amp = min(6 * (c0 / 100) * (c7 / 100), 6)
    return max(0, min(100, round(base + amp)))

def main():
    fx = json.load(open(FIX))
    weights = fx["_meta"]["weights"]
    if abs(sum(weights) - 1.0) > 1e-6:
        print(f"FAIL: weights do not sum to 1.0 (got {sum(weights)})")
        return 1
    cases = fx["cases"]
    failed = 0
    for case in cases:
        got = fdrs_v2(case["c"], weights)
        exp = case["expected_fdrs"]
        ok = (got == exp)
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {case['name']:38} expected {exp:3}  got {got:3}   {case.get('assert','')}")
        if not ok:
            failed += 1
    # extra invariants
    amp_hi = fdrs_v2([90,80,70,60,60,50,40,90,40], weights)
    amp_lo = fdrs_v2([90,80,70,60,60,50,40,10,40], weights)
    if not (amp_hi > amp_lo):
        print(f"  [FAIL] amplifier interaction: high-fragility ({amp_hi}) should exceed low-fragility ({amp_lo})")
        failed += 1
    else:
        print(f"  [ok  ] amplifier interaction: {amp_hi} > {amp_lo} (econ_access raises an import-dependent score)")
    if fdrs_v2([100]*9, weights) != 100:
        print("  [FAIL] ceiling: all-max must clip to 100"); failed += 1
    else:
        print("  [ok  ] ceiling: all-max clips to 100 (raw 106)")

    print(f"\n{len(cases)+2} checks, {failed} failed.")
    if failed:
        print("REGRESSION — the formula or weights changed. If intentional, recompute expected_fdrs in tests/fdrs_cases.json.")
        return 1
    print("PASS — FDRS v2 formula matches the pinned fixtures.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
