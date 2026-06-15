"""
Quick verifier for faostat_food.json — run AFTER refresh_faostat.py on the Mac.

Confirms the file is populated, well-formed, and that the YoY / inflation_shock
fields look sane. Exits non-zero with a clear message if the food-index pull is
still empty (the known open item as of 2026-06-15).

Usage:
    cd scripts && python3 refresh_faostat.py && python3 verify_faostat_food.py
"""
import json
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "faostat_food.json"


def main():
    if not PATH.exists():
        print(f"[FAIL] {PATH} does not exist — run refresh_faostat.py first.")
        return 1

    raw = json.loads(PATH.read_text())
    data = raw.get("data", raw)  # tolerate envelope or bare dict

    n = len(data)
    if n == 0:
        print("[FAIL] faostat_food.json is EMPTY (0 countries).")
        print("       The food-index filter still matched nothing. Re-run")
        print("       refresh_faostat.py and read the [DIAG] elements/months lines —")
        print("       they print the exact codes/names the bulk CSV actually carries.")
        print("       If [DIAG] shows only a *general* CPI element, the CP bulk does")
        print("       not carry the food sub-index → accept as degraded (nowcast falls")
        print("       back to WB WDI + Eurostat; validators accept the empty file).")
        return 1

    with_yoy = sum(1 for v in data.values() if v.get("food_cpi_yoy_pct") is not None)
    shocks = sum(1 for v in data.values() if v.get("inflation_shock"))
    sample = list(data.items())[:5]

    print(f"[OK] faostat_food.json populated: {n} countries.")
    print(f"     {with_yoy} have a YoY figure · {shocks} flagged inflation_shock (>15%).")
    print("     sample:")
    for iso, v in sample:
        print(f"       {iso}: index={v.get('food_cpi_index_latest')} "
              f"yoy={v.get('food_cpi_yoy_pct')}% year={v.get('year_latest')} "
              f"shock={v.get('inflation_shock')}")

    # sanity gates
    bad = [iso for iso, v in data.items()
           if not isinstance(v.get("food_cpi_index_latest"), (int, float))]
    if bad:
        print(f"[WARN] {len(bad)} countries missing a numeric index: {bad[:10]}")
    if n < 50:
        print(f"[WARN] only {n} countries — expected ~150+ from the CP bulk. "
              f"Check the [DIAG] output for a partial match.")
        return 0
    print("[PASS] looks healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
