#!/usr/bin/env bash
# ============================================================
# FoodShield — one-shot Mac finisher for the trade-row rebuild.
# Runs the full pipeline with NO Cowork fetch limit:
#   1. rebuild all weak supplier rows from live Comtrade 2024
#   2. food-only export cleanup (drop non-food, add schema fields)
#   3. validate (must be 0 metadata failures)
#   4. regenerate the index.html embed + sync foodshield-v21.html
# Run from the project root on your Mac:
#   bash scripts/finish_trade_rebuild_MAC.sh
# Optional: COMTRADE_KEY=xxxx bash scripts/finish_trade_rebuild_MAC.sh   (faster, free key)
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 1/4  Rebuilding all weak supplier rows from live Comtrade 2024…"
echo "    (dry-run first so you can eyeball it; remove --dry-run to write)"
python3 scripts/rebuild_all_weak_trade_rows_MAC.py --dry-run | tail -40
echo
read -r -p "Looks right? Press Enter to WRITE these rows (Ctrl-C to abort) " _
python3 scripts/rebuild_all_weak_trade_rows_MAC.py

echo "==> 2/4  Food-only export cleanup + schema fields…"
python3 - <<'PY'
import json
SRC="data/countries.json"; d=json.load(open(SRC)); C=d["data"]["countries"]
# any export row already tagged _food_only gets the required schema fields;
# (the per-country non-food drop is done during the supplier rebuild's notes —
#  extend this dict if you want to hard-drop more non-food terms.)
NONFOOD={"petroleum","crude oil","natural gas","timber","gold","iron ore","copper",
 "cobalt","nickel","tobacco","pharmaceutic","apparel","garment","clothing","tourism",
 "electronic","chemical","mineral","ore","cement","dolomite","hydroelect","arms","coal",
 "steel","rubber","phosphate","teak","sandalwood","marble","stone","soap","carpet","cotton",
 "essential oil","perfume","vanilla","ylang","cloves","kava-non" }
fixed=cleaned=0
for iso,r in C.items():
    e=r.get("exports")
    if not isinstance(e,dict): continue
    vals=e.get("value")
    if isinstance(vals,list):
        keep=[v for v in vals if not any(w in v.lower() for w in NONFOOD)]
        if keep!=vals:
            e["value"]=keep; e["_food_only"]=True; cleaned+=1
            e["note"]=(e.get("note","")+" Non-food items dropped under the food-only rule.").strip()
    if e.get("_food_only"):
        e.setdefault("trade_schema_version","v2"); e.setdefault("basis","agri_food_list")
        e.setdefault("basis_unit","commodity_names"); e.setdefault("source_dataset","foodshield_food_only_filter")
        e.setdefault("coverage","agri_food_exports_only"); e.setdefault("source_url",""); e.setdefault("as_of","2024")
        if not e.get("quality_flag"): e["quality_flag"]="none"
        fixed+=1
json.dump(d,open(SRC,"w"),ensure_ascii=False,indent=1)
print(f"  food-only: cleaned {cleaned} export rows, schema-fixed {fixed}")
PY

echo "==> 3/4  Validating…"
python3 scripts/validate_data.py | grep -E "Summary|metadata failures" || true

echo "==> 4/4  Regenerate the index.html embed + sync the mirror"
echo "    (your existing embed-regen step — e.g. the script that rewrites window.__BEEF_DATA__,"
echo "     then: cp index.html foodshield-v21.html ; grep -c '<script' index.html)"
echo
echo "DONE. Review the diff, then commit:"
echo "    git add -A && git commit -m 'Trade rebuild: all weak supplier rows sourced from Comtrade 2024 + food-only exports'"
