"""
FAOSTAT TCL — Net food trade balance per country (USD).

Pipeline 3 of the structural-data series. Replaces the legacy hand-curated
`c.net` field in COUNTRIES with FAO-sourced values, so the country-panel
"Net food trade" row stops being suppressed as legacy.

ARCHITECTURE NOTE (May 2026):
  The FAOSTAT v1 query API does not reliably filter the TCL domain by item or
  element code — same problem we hit on FBS. So we use the official bulk-download:

    https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip

  - ~250 MB ZIP, ~2 GB unzipped normalized CSV, no auth, no rate limit
  - One row per (Area × Item × Element × Year)
  - Stream-parse on the wire — never hold the full CSV in memory

DATA WE EXTRACT:
  Element 5622 = Import Value (1000 USD)
  Element 5922 = Export Value (1000 USD)
  Item    1842 = Food, Total                 ← preferred (food-security framing)
  Item    1841 = Agricultural Products, Total ← fallback if 1842 missing

  Net balance (millions USD) = (export_kusd - import_kusd) / 1000

  Convention: positive = net food exporter, negative = net food importer.
  This matches the existing `c.net` sign in COUNTRIES (e.g. NLD +35000, AFG -1500).

OUTPUT: data/net_food_trade.json
  {
    "_meta": {...},
    "data": {
      "NLD": {
        "value": 35421,                  # millions USD, signed
        "exports_musd": 142000,          # millions USD
        "imports_musd": 106579,
        "year": 2023,
        "item_used": 1842,               # "Food, Total"
        "country": "Netherlands (Kingdom of the)",
        "source": "FAOSTAT Trade Crops & Livestock",
        "source_url": "https://www.fao.org/faostat/en/#data/TCL",
        "method": "Net agri-food trade = Export Value (5922) - Import Value (5622), item 1842 Food Total",
        "quality_flag": "sourced"
      },
      ...
    }
  }
"""
import csv
import io
import zipfile

from _common import http_get, write_json

# URL has parentheses — keep encoded as literal; FAO serves them fine.
BULK_URL = "https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip"

# Element codes (verified May 2026 via /definitions/domain/TCL/element)
ELEMENT_IMPORT_VAL = 5622   # Import Value, 1000 USD
ELEMENT_EXPORT_VAL = 5922   # Export Value, 1000 USD

# Item codes — pre-aggregated by FAO so we don't have to sum HS lines.
# 1842 is the most honest food-security match. 1841 is fallback for countries
# where FAO publishes the broader agri total but not food-specific.
ITEM_FOOD_TOTAL = 1842   # "Food, Total"
ITEM_AGRI_TOTAL = 1841   # "Agricultural Products, Total"
ITEMS_OF_INTEREST = {ITEM_FOOD_TOTAL, ITEM_AGRI_TOTAL}

# Reuse the canonical area-code map from refresh_faostat_fbs.py.
# These are pulled from FAOSTAT /definitions/domain/TCL/area; the TCL area
# table matches FBS area-by-area (FAO uses the same area dimension across
# all domains).
from refresh_faostat_fbs import FAO_AREA_TO_ISO3, NAME_TO_ISO3


def main():
    print(f"[INFO] FAOSTAT TCL bulk download → {BULK_URL}")
    try:
        r = http_get(BULK_URL, timeout=300, headers={"Accept": "application/zip,*/*"})
    except Exception as e:
        write_json(
            "net_food_trade.json", {},
            source="FAOSTAT TCL",
            notes=f"Bulk download failed: {e}"
        )
        return

    zip_bytes = r.content
    if not zip_bytes or len(zip_bytes) < 1024:
        write_json(
            "net_food_trade.json", {},
            source="FAOSTAT TCL",
            notes=f"Bulk download returned empty body ({len(zip_bytes) if zip_bytes else 0} bytes)"
        )
        return
    print(f"[INFO] Downloaded {len(zip_bytes)//1024//1024} MB ZIP")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        write_json(
            "net_food_trade.json", {},
            source="FAOSTAT TCL",
            notes=f"ZIP parse failed: {e}"
        )
        return

    # Find the normalized CSV inside the ZIP
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
        write_json(
            "net_food_trade.json", {},
            source="FAOSTAT TCL",
            notes=f"No CSV found in ZIP: {zf.namelist()}"
        )
        return

    print(f"[INFO] Reading {csv_member} from ZIP")

    # Stream-parse: FAOSTAT TCL Normalized has columns:
    #   Area Code, Area Code (M49), Area, Item Code, Item, Element Code, Element,
    #   Year Code, Year, Unit, Value, Flag, Note
    # We need: Area Code, Item Code, Element Code, Year, Value, Flag
    # Filter on the fly to keep memory low (~2 GB CSV).
    by_country = {}   # iso3 → {item_code → {year → {'import': v, 'export': v}}}
    rows_seen = 0
    rows_kept = 0

    with zf.open(csv_member, "r") as f:
        # FAOSTAT bulk CSVs are utf-8 with possible latin-1 stragglers in country names
        text_stream = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
        reader = csv.DictReader(text_stream)
        for row in reader:
            rows_seen += 1
            if rows_seen % 500000 == 0:
                print(f"  [progress] {rows_seen} rows scanned, {rows_kept} kept, "
                      f"{len(by_country)} countries so far")

            ic = _int(row.get("Item Code"))
            if ic not in ITEMS_OF_INTEREST:
                continue
            ec = _int(row.get("Element Code"))
            if ec not in (ELEMENT_IMPORT_VAL, ELEMENT_EXPORT_VAL):
                continue

            # Drop missing / suppressed flags. Keep A (official), E (estimate),
            # I (imputed), X (international estimate). Skip M (missing), - (none).
            flag = (row.get("Flag") or "").strip()
            if flag in ("M", "-"):
                continue

            year = _int(row.get("Year"))
            value = _num(row.get("Value"))
            if year is None or value is None:
                continue

            area_name = (row.get("Area") or "").strip()
            ac = _int(row.get("Area Code"))
            iso3 = NAME_TO_ISO3.get(area_name) or FAO_AREA_TO_ISO3.get(ac)
            if not iso3:
                continue

            slot = by_country.setdefault(iso3, {"_country": area_name})
            item_slot = slot.setdefault(ic, {})
            year_slot = item_slot.setdefault(year, {})
            if ec == ELEMENT_IMPORT_VAL:
                year_slot["import_kusd"] = value
            else:
                year_slot["export_kusd"] = value
            rows_kept += 1

    print(f"[INFO] Parsed {rows_seen} rows total, kept {rows_kept} relevant, "
          f"covering {len(by_country)} countries")

    # For each country, pick the most recent year where we have BOTH
    # import and export for item 1842 (preferred). Fall back to 1841 if 1842 is missing.
    out = {}
    for iso3, slot in by_country.items():
        country_name = slot.get("_country")
        chosen = None  # (item_code, year, import_kusd, export_kusd)

        for preferred_item in (ITEM_FOOD_TOTAL, ITEM_AGRI_TOTAL):
            item_data = slot.get(preferred_item)
            if not item_data:
                continue
            # Latest year with both sides populated
            for year in sorted(item_data.keys(), reverse=True):
                yd = item_data[year]
                if "import_kusd" in yd and "export_kusd" in yd:
                    chosen = (preferred_item, year, yd["import_kusd"], yd["export_kusd"])
                    break
            if chosen:
                break

        if not chosen:
            continue

        item_code, year, imp_k, exp_k = chosen
        net_musd = round((exp_k - imp_k) / 1000.0, 1)
        out[iso3] = {
            "value": net_musd,
            "exports_musd": round(exp_k / 1000.0, 1),
            "imports_musd": round(imp_k / 1000.0, 1),
            "year": year,
            "item_used": item_code,
            "country": country_name,
            "source": "FAOSTAT Trade Crops & Livestock",
            "source_url": "https://www.fao.org/faostat/en/#data/TCL",
            "method": (
                f"Net food trade = Export Value (5922) - Import Value (5622), "
                f"item {item_code} "
                f"({'Food Total' if item_code == ITEM_FOOD_TOTAL else 'Agri Total'}). "
                f"Values in millions USD."
            ),
            "quality_flag": "sourced",
        }

    print(f"[INFO] Computed net food trade for {len(out)} countries")

    # Sanity check — log a few well-known reference values
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
            f"Item 1842 (Food, Total) preferred; 1841 (Agricultural Products, Total) "
            f"used as fallback for countries where 1842 is missing. "
            f"Covered {len(out)} of {len(FAO_AREA_TO_ISO3)} mapped countries. "
            f"Most-recent year per country where both export and import values are "
            f"populated and not flagged as missing."
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
