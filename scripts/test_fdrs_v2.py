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

# v79 — IMPORT THE REAL FORMULA, DO NOT MIRROR IT.
#
# This file used to carry its own copy of the arithmetic, "kept in lockstep" by
# hand. It drifted in the worst possible way: when the shipped formula changed
# to renormalise over observed weight, this copy kept the old missing-as-zero
# behaviour and went on passing — a green regression gate that was testing
# itself and had stopped touching the code it guards. A test that reimplements
# its subject cannot detect a change in its subject.
#
# It now imports the builder's function directly, so the Python side is
# genuinely pinned. index.html's copy is checked separately by
# test_fdrs_v2_js.mjs, which extracts the shipped JS and runs these same
# fixtures — that pair is the actual parity guarantee.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_countries_dataset import _fdrs_v2, FDRS_V2_WEIGHTS


def fdrs_v2(cv, weights):
    if list(weights) != list(FDRS_V2_WEIGHTS):
        raise AssertionError(
            f"fixture weights {weights} != builder FDRS_V2_WEIGHTS {FDRS_V2_WEIGHTS}")
    return _fdrs_v2(cv)

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
