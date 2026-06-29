#!/usr/bin/env python3
"""
Formula-integrity gate — cross-checks the math that test_fdrs_v2.py can't see alone.

WHERE THIS FITS
  test_fdrs_v2.py    -> pins the FDRS score for fixed component vectors (regression).
  audit_formulas.py  -> THIS: cross-implementation + cross-file consistency, so the
                        three FDRS copies can't silently diverge, every scenario stays
                        "alive" against the trade atlas, and FOOD_WEIGHT covers every
                        scenario commodity. Catches drift the per-vector test cannot.

CHECKS
  1. FDRS weights identical across build_countries_dataset.py (FDRS_V2_WEIGHTS),
     index.html (FDRS_V2_W) and tests/fdrs_cases.json (_meta.weights); sum == 1.0.
  2. Amplifier expression identical in the Python builder and the JS runtime
     (both: min(6 * (c0/100) * (c7/100), 6)).
  3. FOOD_WEIGHT covers every scenario _commodity (a missing key silently mid-weights
     a shock — the "wine moves like wheat" class of bug).
  4. No DEAD SHOCKS: every flow-based scenario's _shockIso(s) appears as an exporter
     OR importer for its _commodity in data/commodity_flows.json.

EXIT 0 = all pass; 1 = a hard inconsistency.
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")
BUILD = os.path.join(ROOT, "scripts", "build_countries_dataset.py")
FIX = os.path.join(ROOT, "tests", "fdrs_cases.json")
ATLAS = os.path.join(ROOT, "data", "commodity_flows.json")

fails = []
warns = []

def _nums(s):
    return [float(x) for x in re.findall(r"-?\d+\.?\d*", s)]

html = open(HTML, encoding="utf-8").read()
build = open(BUILD, encoding="utf-8").read()
fix = json.load(open(FIX))

print("=== Formula-integrity audit ===\n")

# 1) weights agree + sum to 1
w_fix = fix["_meta"]["weights"]
m_build = re.search(r"FDRS_V2_WEIGHTS\s*=\s*\[([^\]]+)\]", build)
m_html = re.search(r"FDRS_V2_W\s*=\s*\[([^\]]+)\]", html)
w_build = _nums(m_build.group(1)) if m_build else None
w_html = _nums(m_html.group(1)) if m_html else None
print("--- 1. FDRS weights ---")
if w_build == w_html == w_fix:
    print(f"  PASS weights identical across build / index.html / fixture: {w_fix}")
else:
    fails.append("FDRS weights differ across implementations")
    print(f"  FAIL build={w_build}\n       html ={w_html}\n       fix  ={w_fix}")
if abs(sum(w_fix) - 1.0) > 1e-6:
    fails.append(f"weights sum != 1.0 ({sum(w_fix)})")
    print(f"  FAIL weights sum = {sum(w_fix)} (must be 1.0)")
else:
    print(f"  PASS weights sum = 1.0")

# 2) amplifier expression matches (normalise whitespace)
print("\n--- 2. Amplifier expression ---")
def amp_norm(src):
    m = re.search(r"min\(\s*6\s*\*\s*\(\s*c0\s*/\s*100\s*\)\s*\*\s*\(\s*c7\s*/\s*100\s*\)\s*,\s*6\s*\)", src)
    return bool(m)
# build uses c0/c7; html uses comp[0]/comp[7]
amp_build = amp_norm(build)
amp_html = bool(re.search(r"Math\.min\(\s*6\s*\*\s*\(\s*comp\[0\]\s*/\s*100\s*\)\s*\*\s*\(\s*comp\[7\]\s*/\s*100\s*\)\s*,\s*6\s*\)", html))
if amp_build and amp_html:
    print("  PASS amplifier = min(6*(import_dep/100)*(econ_access/100), 6) in both builder and runtime")
else:
    fails.append("amplifier expression mismatch")
    print(f"  FAIL builder match={amp_build}, runtime match={amp_html}")

# 3) FOOD_WEIGHT covers every scenario _commodity
print("\n--- 3. FOOD_WEIGHT commodity coverage ---")
fw_block = html[html.index("const FOOD_WEIGHT"): html.index("const FOOD_WEIGHT") + 900]
fw_keys = set(re.findall(r"(\w+)\s*:", fw_block.split("}")[0]))
commodities = set(re.findall(r"_commodity:\s*['\"]([a-z]+)['\"]", html))
miss_fw = sorted(commodities - fw_keys)
if not miss_fw:
    print(f"  PASS all {len(commodities)} scenario commodities have a FOOD_WEIGHT entry")
else:
    fails.append(f"FOOD_WEIGHT missing: {miss_fw}")
    print(f"  FAIL FOOD_WEIGHT missing entries for: {miss_fw} (would mid-weight via 0.5 fallback)")

# 4) no dead shocks
print("\n--- 4. Dead-shock check (scenario _shockIso vs trade atlas) ---")
atlas = json.load(open(ATLAS))["commodities"]
exp = {k: {f.get("from") for f in (v.get("flows") or [])} for k, v in atlas.items() if isinstance(v, dict)}
imp = {k: {f.get("to") for f in (v.get("flows") or [])} for k, v in atlas.items() if isinstance(v, dict)}
dead = []
for m in re.finditer(r"_shockIso[s]?:\s*(\[[^\]]*\]|['\"][A-Z]{3}['\"])[^\n]*?_commodity:\s*['\"]([a-z]+)['\"]", html):
    raw, com = m.group(1), m.group(2)
    isos = re.findall(r"[A-Z]{3}", raw)
    if com not in exp:
        continue  # commodity not in atlas → not a flow-based dead shock this gate owns
    if not any(i in exp[com] or i in imp[com] for i in isos):
        dead.append((isos, com))
if not dead:
    print("  PASS every flow-based shock's origin has live atlas flows (no dead shocks)")
else:
    fails.append(f"dead shocks: {dead}")
    for d in dead:
        print(f"  FAIL dead shock {d[0]} in '{d[1]}' — no atlas flows, produces zero risk")

# summary
print(f"\n=== {('FAIL — ' + str(len(fails)) + ' issue(s)') if fails else 'PASS — all formula-integrity checks passed'} ===")
for f in fails:
    print("  !!", f)
sys.exit(1 if fails else 0)
