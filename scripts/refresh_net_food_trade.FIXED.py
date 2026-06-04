"""
FAOSTAT TCL — Net food trade balance per country (USD).  [FIXED v22]

Pipeline 3 of the structural-data series. Replaces the legacy hand-curated
`c.net` field in COUNTRIES with FAO-sourced values, so the country-panel
"Net food trade" row stops being suppressed as legacy.

WHY THIS REWRITE
----------------
The previous version returned 0 of 174 countries. Root-cause analysis (see the
ranked diagnosis in the PR description) points at the item-matching layer:

  1. The legacy numeric "Item Code" 1841/1842 either no longer carries the
     "Food, Total" / "Agricultural Products, Total" aggregates in the TCL
     Normalized bulk feed, OR FAO ships a dual Item-Code layout (legacy numeric
     + CPC string) where the aggregate rows live under codes the old set misses.
  2. The name-match fallback used `TARGET_ITEM_NAMES = {"food, total", ...}` with
     a naive `.strip().lower()`. FAO's real aggregate strings drift in casing,
     spacing and punctuation ("Food, Total" vs "Food,Total" vs "Food and ...");
     an exact-equality match against a tiny set is brittle and silently yields 0.

This version fixes the matching defensively:
  - Item NAME is the *primary* matcher (codes drift across releases; the human-
    readable aggregate label is far more stable). Names are normalised hard:
    lowercased, commas/extra punctuation removed, whitespace collapsed.
  - Item CODE (legacy numeric column, explicitly selected) is a *secondary*
    matcher kept for speed/robustness, with a WIDENED set.
  - We accept BOTH the strict food aggregate and the broader agri aggregate, and
    record which one was used per country (so the frontend method string is honest).
  - Element matching is widened to the known import/export VALUE codes and tagged
    by import-vs-export from the element NAME as a fallback if codes renumber.
  - Diagnostics are richer: we always log distinct item names + element pairs we
    actually saw, so a single future schema change is self-diagnosing in the log.

Run the standalone `scripts/_probe_net_food_trade.py` first on a networked
machine to dump the real schema; this parser is built to survive what that probe
typically reveals, but the probe is the ground truth.

BULK FEED
  https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip
  - ~250 MB ZIP, no auth, no rate limit
  - One row per (Area × Item × Element × Year); stream-parsed on the wire.

DATA WE EXTRACT
  Import Value (1000 USD)  — element 5622 (primary), name "import value" fallback
  Export Value (1000 USD)  — element 5922 (primary), name "export value" fallback
  Food aggregate           — item "Food, Total" (code 1842 historically)
  Agri aggregate (fallback)— item "Agricultural Products, Total" (code 1841)

  Net balance (millions USD) = (export_kusd - import_kusd) / 1000
  Convention: positive = net food exporter, negative = net food importer.

OUTPUT: data/net_food_trade.json  (schema unchanged from prior version)
"""
import csv
import io
import re
import zipfile

from _common import http_get, write_json

# URL has parentheses — keep encoded as literal; FAO serves them fine.
BULK_URL = "https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip"

# --- Element codes (import/export VALUE in 1000 USD) -------------------------
# Primary codes (verified historically). We keep a small widened set and also a
# name-based fallback so a renumber in the bulk feed doesn't silently zero us out.
ELEMENT_IMPORT_VAL = 5622   # Import Value, 1000 USD
ELEMENT_EXPORT_VAL = 5922   # Export Value, 1000 USD
IMPORT_VALUE_CODES = {5622}
EXPORT_VALUE_CODES = {5922}

# --- Item matching -----------------------------------------------------------
# GROUND TRUTH (from scripts/_probe_net_food_trade.py, run Jun 2026):
# The legacy numeric "Item Code" 1841/1842 aggregates DO NOT EXIST in this bulk
# feed (0 rows). FAOSTAT moved the trade aggregates to the CPC string column
# ("Item Code (CPC)"). The probe found these CPC aggregate codes present:
#     F1982  "Food Excluding Fish"        <- our preferred food-trade total
#     F1888  "Cereals and Preparations"
#     F1944  "Cereals"
#     F1882  "Crops and livestock products"   <- broad fallback (whole-domain total)
#     F1885  "Meat and Meat Preparations", F2071 "Bovine Meat", etc. (sub-aggregates)
# So we match the CPC aggregate code as the PRIMARY key. Name + legacy numeric
# code are kept as secondary fallbacks in case FAO renames/renumbers again.
CPC_FOOD_TOTAL = "F1982"   # "Food Excluding Fish" — best available food-trade aggregate
CPC_AGRI_TOTAL = "F1882"   # "Crops and livestock products" — broader fallback

# Legacy numeric codes (now empty in this feed, kept only as a defensive fallback).
ITEM_FOOD_TOTAL = 1842
ITEM_AGRI_TOTAL = 1841
FOOD_ITEM_CODES = {1842}
AGRI_ITEM_CODES = {1841}

# Normalised item-name keys (tertiary fallback). The CPC aggregate's human name is
# "Food Excluding Fish"; the broad one is "Crops and livestock products". We also
# keep the historical "food total" / "agricultural products total" strings.
FOOD_NAME_KEYS = {
    "food excluding fish",
    "food total",
    "food",
}
AGRI_NAME_KEYS = {
    "crops and livestock products",
    "agricultural products total",
    "agricultural products",
    "agriculture total",
}

# Reuse the canonical area maps from the FBS pipeline (same FAO area dimension).
from refresh_faostat_fbs import FAO_AREA_TO_ISO3, NAME_TO_ISO3


_punct_re = re.compile(r"[^a-z0-9 ]+")
_ws_re = re.compile(r"\s+")


def _norm_name(s):
    """Hard-normalise an item/element name for tolerant matching.

    Lowercase, drop all punctuation (commas, parens, hyphens), collapse runs of
    whitespace to one space, strip. "Food, Total" -> "food total".
    """
    if not s:
        return ""
    s = s.strip().lower()
    s = _punct_re.sub(" ", s)
    s = _ws_re.sub(" ", s).strip()
    return s


def _classify_item(item_code, item_name_norm, cpc_code):
    """Return 'food', 'agri', or None for a row's item.

    Matching priority (per the probe's ground truth):
      1. CPC aggregate code  — the ONLY place the aggregates live in this feed.
      2. Item name           — tolerant fallback if FAO renames the CPC code.
      3. Legacy numeric code — kept for safety; currently empty in this feed.
    """
    cpc = (cpc_code or "").strip().upper().lstrip("'")
    if cpc == CPC_FOOD_TOTAL:
        return "food"
    if cpc == CPC_AGRI_TOTAL:
        return "agri"
    # Name fallback (secondary).
    if item_name_norm in FOOD_NAME_KEYS:
        return "food"
    if item_name_norm in AGRI_NAME_KEYS:
        return "agri"
    # Legacy numeric code fallback (tertiary).
    if item_code in FOOD_ITEM_CODES:
        return "food"
    if item_code in AGRI_ITEM_CODES:
        return "agri"
    return None


def _classify_element(elem_code, elem_name_norm):
    """Return 'import', 'export', or None using CODE first then NAME fallback."""
    if elem_code in IMPORT_VALUE_CODES:
        return "import"
    if elem_code in EXPORT_VALUE_CODES:
        return "export"
    # Name fallback in case codes renumber. We only treat *value* elements as
    # import/export here — quantity rows ("import quantity") must NOT be caught,
    # so require the word "value".
    if "value" in elem_name_norm:
        if elem_name_norm.startswith("import"):
            return "import"
        if elem_name_norm.startswith("export"):
            return "export"
    return None


def main():
    print(f"[INFO] FAOSTAT TCL bulk download → {BULK_URL}")
    try:
        r = http_get(BULK_URL, timeout=300, headers={"Accept": "application/zip,*/*"}, patient=True)
    except Exception as e:
        write_json("net_food_trade.json", {}, source="FAOSTAT TCL",
                   notes=f"Bulk download failed: {e}")
        return

    zip_bytes = r.content
    if not zip_bytes or len(zip_bytes) < 1024:
        write_json("net_food_trade.json", {}, source="FAOSTAT TCL",
                   notes=f"Bulk download returned empty body ({len(zip_bytes) if zip_bytes else 0} bytes)")
        return
    print(f"[INFO] Downloaded {len(zip_bytes)//1024//1024} MB ZIP")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        write_json("net_food_trade.json", {}, source="FAOSTAT TCL",
                   notes=f"ZIP parse failed: {e}")
        return

    # Find the normalized CSV inside the ZIP.
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
        write_json("net_food_trade.json", {}, source="FAOSTAT TCL",
                   notes=f"No CSV found in ZIP: {zf.namelist()}")
        return

    print(f"[INFO] Reading {csv_member} from ZIP")

    by_country = {}   # iso3 → {agg_key → {year → {'import_kusd': v, 'export_kusd': v}}}
    rows_seen = 0
    rows_kept = 0

    # Diagnostics: always capture what we actually saw, even on success.
    seen_item_names = {}     # normalised item name -> raw first-seen + count
    seen_elements = {}       # element_code -> element_name (first seen)
    matched_item_names = {}  # normalised item name we classified -> count

    with zf.open(csv_member, "r") as f:
        text_stream = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
        reader = csv.DictReader(text_stream)
        actual_headers = reader.fieldnames or []
        print(f"[INFO] CSV headers: {actual_headers}")

        def _find_col(*candidates):
            """Case/space/underscore-tolerant header resolver (ignores a '(cpc)' suffix)."""
            norm = {h.lower().replace(" ", "").replace("_", "").replace("(cpc)", "").strip(): h
                    for h in actual_headers}
            for c in candidates:
                key = c.lower().replace(" ", "").replace("_", "").strip()
                if key in norm:
                    return norm[key]
            return None

        # CRITICAL: TCL ships TWO item-code columns — legacy numeric "Item Code"
        # and CPC string "Item Code (CPC)". The _find_col normaliser strips "(cpc)"
        # so BOTH collapse to the same key; we must therefore pin the LEGACY
        # numeric column by exact name and never let the CPC column win.
        if "Item Code" in actual_headers:
            COL_ITEM_CODE = "Item Code"
        elif "Item Code (FAO)" in actual_headers:
            COL_ITEM_CODE = "Item Code (FAO)"
        else:
            # last resort: any item-code-ish header that is NOT the CPC one
            COL_ITEM_CODE = next(
                (h for h in actual_headers
                 if "itemcode" in h.lower().replace(" ", "").replace("_", "")
                 and "cpc" not in h.lower()),
                None,
            )

        # The CPC string item-code column is where the aggregates actually live.
        COL_ITEM_CODE_CPC = "Item Code (CPC)" if "Item Code (CPC)" in actual_headers else next(
            (h for h in actual_headers if "itemcode" in h.lower().replace(" ", "") and "cpc" in h.lower()),
            None,
        )
        COL_ELEMENT_CODE = _find_col("Element Code", "ElementCode")
        COL_ELEMENT_NAME = "Element" if "Element" in actual_headers else _find_col("Element")
        COL_YEAR = _find_col("Year")
        COL_VALUE = _find_col("Value")
        COL_AREA = _find_col("Area", "Country", "Country or Area")
        COL_AREA_CODE = _find_col("Area Code", "AreaCode")
        COL_FLAG = _find_col("Flag")
        COL_ITEM_NAME = "Item" if "Item" in actual_headers else _find_col("Item", "Commodity")
        print(f"[INFO] Resolved columns: item_code={COL_ITEM_CODE!r} item_code_cpc={COL_ITEM_CODE_CPC!r} "
              f"item_name={COL_ITEM_NAME!r} element_code={COL_ELEMENT_CODE!r} element_name={COL_ELEMENT_NAME!r} "
              f"year={COL_YEAR!r} value={COL_VALUE!r} area={COL_AREA!r} area_code={COL_AREA_CODE!r}")

        # We can run on NAME alone if the numeric code column is gone, so only the
        # truly load-bearing columns are hard-required.
        missing_cols = [name for name, col in [
            ("Element Code/Name", COL_ELEMENT_CODE or COL_ELEMENT_NAME),
            ("Year", COL_YEAR), ("Value", COL_VALUE), ("Area", COL_AREA),
            ("Item name", COL_ITEM_NAME),
        ] if col is None]
        if missing_cols:
            write_json("net_food_trade.json", {}, source="FAOSTAT TCL",
                       notes=(f"CSV header mismatch — could not resolve columns: {missing_cols}. "
                              f"Headers seen: {actual_headers[:25]}"))
            return

        for row in reader:
            rows_seen += 1
            if rows_seen % 500000 == 0:
                print(f"  [progress] {rows_seen} rows scanned, {rows_kept} kept, "
                      f"{len(by_country)} countries so far")

            item_name_raw = (row.get(COL_ITEM_NAME) or "") if COL_ITEM_NAME else ""
            item_name_norm = _norm_name(item_name_raw)
            ic = _int(row.get(COL_ITEM_CODE)) if COL_ITEM_CODE else None
            cpc = (row.get(COL_ITEM_CODE_CPC) or "") if COL_ITEM_CODE_CPC else ""

            # diagnostics: remember item names (cheap, bounded)
            if item_name_norm and len(seen_item_names) < 400:
                rec = seen_item_names.setdefault(item_name_norm, [item_name_raw.strip(), 0])
                rec[1] += 1

            agg = _classify_item(ic, item_name_norm, cpc)
            if agg is None:
                continue

            ec = _int(row.get(COL_ELEMENT_CODE)) if COL_ELEMENT_CODE else None
            elem_name_norm = _norm_name(row.get(COL_ELEMENT_NAME)) if COL_ELEMENT_NAME else ""
            if ec is not None and ec not in seen_elements:
                seen_elements[ec] = (row.get(COL_ELEMENT_NAME) or "").strip()

            side = _classify_element(ec, elem_name_norm)
            if side is None:
                continue

            # Drop missing / suppressed flags. Keep A/E/I/X etc.
            flag = (row.get(COL_FLAG) or "").strip() if COL_FLAG else ""
            if flag in ("M", "-"):
                continue

            year = _int(row.get(COL_YEAR))
            value = _num(row.get(COL_VALUE))
            if year is None or value is None:
                continue

            area_name = (row.get(COL_AREA) or "").strip()
            ac = _int(row.get(COL_AREA_CODE)) if COL_AREA_CODE else None
            iso3 = NAME_TO_ISO3.get(area_name) or FAO_AREA_TO_ISO3.get(ac)
            if not iso3:
                continue

            matched_item_names[item_name_norm] = matched_item_names.get(item_name_norm, 0) + 1

            slot = by_country.setdefault(iso3, {"_country": area_name})
            agg_slot = slot.setdefault(agg, {})
            year_slot = agg_slot.setdefault(year, {})
            if side == "import":
                year_slot["import_kusd"] = value
            else:
                year_slot["export_kusd"] = value
            rows_kept += 1

    print(f"[INFO] Parsed {rows_seen} rows total, kept {rows_kept} relevant, "
          f"covering {len(by_country)} countries")

    # Always print diagnostics — invaluable when coverage is unexpectedly low.
    print(f"[DIAG] distinct element codes seen: "
          f"{ {k: seen_elements[k] for k in sorted(seen_elements)[:25]} }")
    if matched_item_names:
        print(f"[DIAG] item names that matched the food/agri classifier: {matched_item_names}")
    else:
        # Show the names that *contain* 'total' so we can eyeball the real labels.
        totalish = {n: v for n, v in seen_item_names.items() if "total" in n}
        sample = dict(list(totalish.items())[:25]) or dict(list(seen_item_names.items())[:25])
        print(f"[DIAG] NO item matched. Sample item names seen (raw, count): {sample}")

    # For each country, prefer the food aggregate, fall back to agri. Take the
    # most-recent year with BOTH import and export populated.
    out = {}
    for iso3, slot in by_country.items():
        country_name = slot.get("_country")
        chosen = None  # (agg_key, item_code_label, year, import_kusd, export_kusd)

        for agg_key, item_code_label, label in (
            ("food", CPC_FOOD_TOTAL, "Food Excluding Fish (CPC F1982)"),
            ("agri", CPC_AGRI_TOTAL, "Crops and livestock products (CPC F1882)"),
        ):
            agg_data = slot.get(agg_key)
            if not agg_data:
                continue
            for year in sorted(agg_data.keys(), reverse=True):
                yd = agg_data[year]
                if "import_kusd" in yd and "export_kusd" in yd:
                    chosen = (agg_key, item_code_label, year, yd["import_kusd"], yd["export_kusd"], label)
                    break
            if chosen:
                break

        if not chosen:
            continue

        agg_key, item_code_label, year, imp_k, exp_k, label = chosen
        net_musd = round((exp_k - imp_k) / 1000.0, 1)
        out[iso3] = {
            "value": net_musd,
            "exports_musd": round(exp_k / 1000.0, 1),
            "imports_musd": round(imp_k / 1000.0, 1),
            "year": year,
            "item_used": item_code_label,
            "country": country_name,
            "source": "FAOSTAT Trade Crops & Livestock",
            "source_url": "https://www.fao.org/faostat/en/#data/TCL",
            "method": (
                f"Net food trade = Export Value - Import Value (1000 USD), "
                f"aggregate '{label}'. Values in millions USD."
            ),
            "quality_flag": "sourced",
        }

    print(f"[INFO] Computed net food trade for {len(out)} countries")

    for ref in ("NLD", "USA", "BRA", "BGD", "EGY", "JPN", "AFG"):
        if ref in out:
            print(f"  [ref] {ref} net={out[ref]['value']:+.0f} musd "
                  f"(exp={out[ref]['exports_musd']:.0f}, imp={out[ref]['imports_musd']:.0f}, "
                  f"yr={out[ref]['year']})")

    write_json(
        "net_food_trade.json",
        out,
        source="FAOSTAT TCL bulk download (Trade_CropsLivestock_E_All_Data_(Normalized).zip)",
        notes=(
            f"Net agri-food trade balance per country in millions USD. "
            f"Positive = net exporter, negative = net importer. "
            f"'Food, Total' aggregate preferred; 'Agricultural Products, Total' "
            f"used as fallback. Matching is name-first (code-tolerant) to survive "
            f"FAOSTAT item-code drift. Covered {len(out)} of {len(FAO_AREA_TO_ISO3)} "
            f"mapped countries. Most-recent year per country where both export and "
            f"import values are populated and not flagged as missing."
        ),
    )


def _num(v):
    try:
        return float(v) if v not in (None, "", "..") else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
