#!/usr/bin/env python3
"""
GROUND-TRUTH PROBE for FAOSTAT TCL (Trade_CropsLivestock) bulk feed.

Why this exists
---------------
`refresh_net_food_trade.py` returns 0 of 174 countries. We need to know the
*actual* current schema of the bulk CSV before we can fix the parser. This
script answers, from real data, every question the fix depends on:

  - What are the exact CSV column headers? (dual Item-Code columns? CPC?)
  - What are the first ~40 distinct (Item Code, Item) pairs? Are 1841/1842
    still present? What are their *exact* names (case + punctuation)?
  - What are the distinct (Element Code, Element) pairs? Are 5622/5922 still
    the import/export *value* codes, or did they renumber?
  - What do a few (Area Code, Area) rows look like? (so we know which area
    columns exist and whether names match the FBS NAME_TO_ISO3 map)
  - How many rows match each candidate filter combination, so we can see
    exactly which filter is zeroing out.

It uses ONLY the Python standard library (csv, io, zipfile, urllib) so it runs
on a clean Mac with no project dependencies installed.

USAGE
-----
  # download the ~250 MB ZIP fresh (needs internet):
  python3 scripts/_probe_net_food_trade.py

  # OR point it at an already-downloaded ZIP (no network):
  python3 scripts/_probe_net_food_trade.py /path/to/Trade_CropsLivestock_E_All_Data_(Normalized).zip

The download is large. If you already have the ZIP, pass the path to skip it.
"""
import csv
import io
import os
import sys
import glob
import zipfile
import urllib.request

BULK_URL = "https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip"
# Where we cache the downloaded ZIP so repeated runs don't re-pull 264 MB.
CACHE_PATHS = [
    os.path.expanduser("~/Downloads/Trade_CropsLivestock_E_All_Data_(Normalized).zip"),
    "/tmp/Trade_CropsLivestock_E_All_Data_(Normalized).zip",
]

# Candidate filters we want to test against real data.
CANDIDATE_ITEM_CODES = {1841, 1842}
CANDIDATE_ELEMENT_CODES = {5622, 5922}
# Element-value candidates seen historically in FAOSTAT trade domains.
EXTRA_ELEMENT_CANDIDATES = {5610, 5910, 5622, 5922, 5640, 5940}
CANDIDATE_ITEM_NAME_SUBSTRINGS = ["food", "agricultural products", "agriculture", "total"]


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def fetch_zip_bytes(arg_path):
    # 1) explicit path argument wins
    if arg_path:
        print(f"[probe] reading local ZIP: {arg_path}")
        with open(arg_path, "rb") as fh:
            return fh.read()
    # 2) reuse a cached copy if we downloaded one before (no re-pull)
    for p in CACHE_PATHS + glob.glob(os.path.expanduser("~/Downloads/Trade_CropsLivestock*.zip")):
        if os.path.exists(p) and os.path.getsize(p) > 1024 * 1024:
            print(f"[probe] reusing cached ZIP: {p} ({os.path.getsize(p)//1024//1024} MB)")
            with open(p, "rb") as fh:
                return fh.read()
    # 3) download fresh AND save to cache so the next run is instant
    print(f"[probe] downloading (~250 MB, be patient): {BULK_URL}")
    req = urllib.request.Request(
        BULK_URL,
        headers={"User-Agent": "FoodShield-probe/1.0", "Accept": "application/zip,*/*"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = resp.read()
    print(f"[probe] downloaded {len(data) // 1024 // 1024} MB")
    try:
        with open(CACHE_PATHS[0], "wb") as fh:
            fh.write(data)
        print(f"[probe] cached to {CACHE_PATHS[0]} for reuse")
    except OSError as e:
        print(f"[probe] (could not cache: {e})")
    return data


def main():
    arg_path = sys.argv[1] if len(sys.argv) > 1 else None
    zip_bytes = fetch_zip_bytes(arg_path)
    print(f"[probe] ZIP size: {len(zip_bytes)} bytes")

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    print(f"[probe] ZIP members: {zf.namelist()}")

    # Pick the Normalized CSV, else first CSV.
    csv_member = None
    for name in zf.namelist():
        if name.endswith(".csv") and "Normalized" in name:
            csv_member = name
            break
    if not csv_member:
        for name in zf.namelist():
            if name.endswith(".csv"):
                csv_member = name
                break
    if not csv_member:
        print("[probe] FATAL: no CSV in ZIP")
        return
    print(f"[probe] reading CSV member: {csv_member}")

    # Diagnostics accumulators
    distinct_items = {}          # item_code(legacy) -> item_name (first seen)
    distinct_items_cpc = {}      # cpc_code -> item_name (first seen), if CPC col exists
    distinct_elements = {}       # element_code -> element_name
    sample_areas = []            # list of (area_code, area_name)
    seen_area_keys = set()

    rows_total = 0
    # Filter counters
    cnt_itemcode_match = 0
    cnt_elem_5622_5922 = 0
    cnt_itemcode_and_elem = 0
    cnt_name_food_total = 0      # item name == "food, total"
    cnt_name_agri_total = 0      # item name == "agricultural products, total"
    name_total_counts = {}       # exact-lower item name -> count, for names containing "total"
    elem_extra_counts = {c: 0 for c in EXTRA_ELEMENT_CANDIDATES}

    with zf.open(csv_member, "r") as f:
        text = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
        reader = csv.DictReader(text)
        headers = reader.fieldnames or []
        print("\n========== CSV HEADERS ==========")
        for h in headers:
            print(f"  | {h!r}")
        print("=================================\n")

        # Resolve which header is which (report both, don't guess silently).
        def has(name):
            return name if name in headers else None

        col_item_code = has("Item Code") or has("Item Code (FAO)")
        col_item_code_cpc = has("Item Code (CPC)")
        col_item = has("Item")
        col_elem_code = has("Element Code")
        col_elem = has("Element")
        col_area_code = has("Area Code") or has("Area Code (M49)")
        col_area = has("Area")
        col_year = has("Year")
        col_value = has("Value")
        col_flag = has("Flag")
        print("[probe] resolved columns:")
        print(f"    item_code(legacy) = {col_item_code!r}")
        print(f"    item_code(cpc)    = {col_item_code_cpc!r}")
        print(f"    item              = {col_item!r}")
        print(f"    element_code      = {col_elem_code!r}")
        print(f"    element           = {col_elem!r}")
        print(f"    area_code         = {col_area_code!r}")
        print(f"    area              = {col_area!r}")
        print(f"    year              = {col_year!r}")
        print(f"    value             = {col_value!r}")
        print(f"    flag              = {col_flag!r}\n")

        for row in reader:
            rows_total += 1

            ic = _to_int(row.get(col_item_code)) if col_item_code else None
            item_name = (row.get(col_item) or "").strip() if col_item else ""
            item_name_l = item_name.lower()
            ec = _to_int(row.get(col_elem_code)) if col_elem_code else None
            elem_name = (row.get(col_elem) or "").strip() if col_elem else ""

            if ic is not None and ic not in distinct_items and len(distinct_items) < 60:
                distinct_items[ic] = item_name
            if col_item_code_cpc:
                cpc = (row.get(col_item_code_cpc) or "").strip()
                if cpc and cpc not in distinct_items_cpc and len(distinct_items_cpc) < 60:
                    distinct_items_cpc[cpc] = item_name
            if ec is not None and ec not in distinct_elements:
                distinct_elements[ec] = elem_name

            if col_area_code and col_area and len(sample_areas) < 30:
                ac = (row.get(col_area_code) or "").strip()
                an = (row.get(col_area) or "").strip()
                key = (ac, an)
                if key not in seen_area_keys:
                    seen_area_keys.add(key)
                    sample_areas.append(key)

            # Filter counters
            ic_match = ic in CANDIDATE_ITEM_CODES
            ec_match = ec in CANDIDATE_ELEMENT_CODES
            if ic_match:
                cnt_itemcode_match += 1
            if ec_match:
                cnt_elem_5622_5922 += 1
            if ic_match and ec_match:
                cnt_itemcode_and_elem += 1
            if item_name_l == "food, total":
                cnt_name_food_total += 1
            if item_name_l == "agricultural products, total":
                cnt_name_agri_total += 1
            if "total" in item_name_l:
                name_total_counts[item_name_l] = name_total_counts.get(item_name_l, 0) + 1
            if ec in elem_extra_counts:
                elem_extra_counts[ec] += 1

            if rows_total % 1000000 == 0:
                print(f"  [probe] {rows_total} rows scanned...")

    print(f"\n[probe] TOTAL ROWS: {rows_total}\n")

    print("========== DISTINCT ITEM CODE + NAME (legacy numeric column) ==========")
    for code in sorted(distinct_items.keys())[:40]:
        print(f"  {code:>8}  |  {distinct_items[code]!r}")
    print("(showing up to 40)\n")

    if distinct_items_cpc:
        print("========== DISTINCT CPC CODE + NAME (string column) ==========")
        for cpc in list(distinct_items_cpc.keys())[:40]:
            print(f"  {cpc:>10}  |  {distinct_items_cpc[cpc]!r}")
        print("(showing up to 40)\n")

    print("========== DISTINCT ELEMENT CODE + NAME ==========")
    for code in sorted(distinct_elements.keys()):
        print(f"  {code:>8}  |  {distinct_elements[code]!r}")
    print()

    print("========== SAMPLE AREA CODE + NAME (first 30 distinct) ==========")
    for ac, an in sample_areas:
        print(f"  {ac:>6}  |  {an!r}")
    print()

    print("========== ITEM NAMES CONTAINING 'total' (with row counts) ==========")
    for name, n in sorted(name_total_counts.items(), key=lambda kv: -kv[1])[:30]:
        print(f"  {n:>10}  |  {name!r}")
    print()

    print("========== FILTER MATCH COUNTS ==========")
    print(f"  rows where Item Code in {{1841,1842}}                : {cnt_itemcode_match}")
    print(f"  rows where Element Code in {{5622,5922}}             : {cnt_elem_5622_5922}")
    print(f"  rows where BOTH item-code AND element-code match    : {cnt_itemcode_and_elem}")
    print(f"  rows where Item name == 'food, total'               : {cnt_name_food_total}")
    print(f"  rows where Item name == 'agricultural products, total': {cnt_name_agri_total}")
    print()
    print("  element-code candidate counts (which value codes actually carry rows):")
    for c in sorted(elem_extra_counts.keys()):
        print(f"    element {c}: {elem_extra_counts[c]} rows")
    print()

    print("[probe] INTERPRETATION GUIDE:")
    print("  - If 'Item Code in {1841,1842}' == 0 but item names show a 'Food, Total'-")
    print("    like row under a DIFFERENT code, the legacy item codes were renumbered.")
    print("  - If the legacy numeric Item Code column is missing/empty and only CPC")
    print("    codes exist, we must match on Item NAME (use the exact strings above).")
    print("  - If 'Element Code in {5622,5922}' == 0, the import/export VALUE codes")
    print("    changed — see the element table above for the new value codes.")
    print("  - Match the area names above against NAME_TO_ISO3 / FAO_AREA_TO_ISO3.")


if __name__ == "__main__":
    main()
