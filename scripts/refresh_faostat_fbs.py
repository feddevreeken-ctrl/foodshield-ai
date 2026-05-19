"""
FAOSTAT Food Balance Sheets — caloric shares of wheat / rice / maize per country.

This is the *structural* food-security pipeline that replaces the hand-curated
c.w / c.r / c.m fields in COUNTRIES with FAOSTAT-sourced caloric shares.

ARCHITECTURE NOTE (May 2026 audit):
  The FAOSTAT v1 query API does NOT reliably filter by area_code / item_code /
  element_code on the FBS domain — passing those parameters returns the entire
  domain (~350k rows). The official bulk-download ZIP is the correct interface:

    https://bulks-faostat.fao.org/production/FoodBalanceSheets_E_All_Data.zip

  - ~21 MB ZIP, no auth, no rate limit
  - Contains FoodBalanceSheets_E_All_Data.csv
  - Updated when FAO refreshes FBS (currently 2025-10-14, vintage 2010-2022)
  - Listed in the official bulkdownloads catalogue at
    https://faostatservices.fao.org/api/v1/en/bulkdownloads/FBS

We download once per refresh, parse to extract the four item-codes we need
(Grand Total + wheat + rice + maize), and compute caloric share per country.

INPUTS:
  element 664 = Food supply (kcal/capita/day)
  item   2901 = Grand Total
  item   2511 = Wheat and products
  item   2805 = Rice and products
  item   2514 = Maize and products

Caloric share % = item kcal / Grand Total kcal × 100.

Output: data/country_caloric_shares.json
  {
    "_meta": { ... },
    "data": {
      "BEL": {
        "wheat_pct": 11.2, "rice_pct": 0.9, "maize_pct": 1.7,
        "total_kcal_per_day": 3760,
        "year": 2022,
        "source": "FAOSTAT Food Balance Sheets",
        "source_url": "https://www.fao.org/faostat/en/#data/FBS",
        "quality_flag": "sourced",
      },
      ...
    }
  }

Coverage is ~180 countries (every FBS-tracked country); the legacy / hand-entered
countries.json values become a fallback only for countries that FAOSTAT does not
publish FBS data for (typically small island states + recent statehood entities).

Notes for future maintainers:
  - "Direct caloric supply" only — indirect calories via animal feed are NOT counted.
    Mexico's tortilla maize is captured here; Mexico's feed maize for cattle is not.
  - When FAO publishes a newer FBS vintage, this script picks it up automatically
    on the next refresh because we always take the most-recent year per country.
"""
import csv
import io
import zipfile

from _common import http_get, write_json

BULK_URL = "https://bulks-faostat.fao.org/production/FoodBalanceSheets_E_All_Data.zip"

# FAOSTAT FBS item codes (verified May 2026 via probe on data/FBS)
ITEMS = {
    2901: "total",
    2511: "wheat",
    2805: "rice",
    2514: "maize",
}
ELEMENT_KCAL = 664   # Food supply (kcal/capita/day)

# FAO area_code → ISO3 (reused from refresh_faostat.py — same M.49 coding)
FAO_AREA_TO_ISO3 = {
    2:"AFG",3:"ALB",4:"DZA",7:"AGO",8:"ATG",9:"ARG",1:"ARM",10:"AUS",11:"AUT",52:"AZE",
    12:"BHS",13:"BHR",16:"BGD",14:"BRB",57:"BLR",255:"BEL",23:"BLZ",53:"BEN",18:"BTN",19:"BOL",
    80:"BIH",20:"BWA",21:"BRA",26:"BRN",27:"BGR",233:"BFA",29:"BDI",115:"KHM",32:"CMR",33:"CAN",
    35:"CAF",39:"TCD",40:"CHL",41:"CHN",351:"COL",45:"COM",46:"COG",250:"COD",48:"CRI",107:"CIV",
    98:"HRV",49:"CUB",50:"CYP",167:"CZE",54:"DNK",72:"DJI",55:"DMA",56:"DOM",58:"ECU",59:"EGY",
    60:"SLV",61:"GNQ",178:"ERI",63:"EST",209:"SWZ",238:"ETH",66:"FJI",67:"FIN",68:"FRA",74:"GAB",
    75:"GMB",73:"GEO",79:"DEU",81:"GHA",84:"GRC",86:"GRD",89:"GTM",90:"GIN",175:"GNB",91:"GUY",
    93:"HTI",95:"HND",97:"HUN",99:"ISL",100:"IND",101:"IDN",102:"IRN",103:"IRQ",104:"IRL",105:"ISR",
    106:"ITA",109:"JAM",110:"JPN",112:"JOR",108:"KAZ",114:"KEN",83:"KIR",118:"KGZ",120:"LAO",119:"LBN",
    122:"LSO",123:"LBR",124:"LBY",126:"LTU",256:"LUX",129:"MDG",130:"MWI",131:"MYS",132:"MDV",133:"MLI",
    134:"MLT",136:"MRT",137:"MUS",138:"MEX",276:"MDA",141:"MNG",273:"MNE",143:"MAR",144:"MOZ",28:"MMR",
    147:"NAM",149:"NPL",150:"NLD",156:"NZL",157:"NIC",158:"NER",159:"NGA",116:"PRK",154:"NOR",221:"OMN",
    165:"PAK",166:"PAN",168:"PNG",169:"PRY",170:"PER",171:"PHL",173:"POL",174:"PRT",179:"QAT",183:"ROU",
    185:"RWA",186:"KNA",188:"LCA",191:"WSM",193:"STP",194:"SAU",195:"SEN",272:"SRB",196:"SYC",197:"SLE",
    200:"SVK",198:"SVN",25:"SOM",202:"ZAF",277:"SSD",203:"ESP",38:"LKA",207:"SUR",210:"SWE",
    211:"CHE",212:"SYR",214:"TZA",216:"THA",176:"TGO",219:"TTO",218:"TUN",223:"TUR",213:"TKM",226:"UGA",
    230:"UKR",225:"ARE",229:"GBR",231:"USA",234:"URY",235:"UZB",237:"VEN",251:"VNM",249:"YEM",181:"ZWE",
    # Some countries need disambiguation: Sudan/SDN, S. Sudan/SSD, Zambia/ZMB
    276:"SDN",   # Sudan (former code 277 = South Sudan; FAO updated mapping)
    251:"ZMB",   # Zambia (CP uses 251; same code reused for VNM in some contexts — FBS keeps these distinct via Area name; resolved at parse time)
}

# Some FAO area codes are ambiguous in older mappings. We resolve at parse time
# by also checking Area name when ISO3 collision is detected.
NAME_TO_ISO3 = {
    "Belgium": "BEL", "Netherlands (Kingdom of the)": "NLD", "Netherlands": "NLD",
    "United States of America": "USA", "United Kingdom of Great Britain and Northern Ireland": "GBR",
    "Russian Federation": "RUS", "China, mainland": "CHN", "China": "CHN",
    "Iran (Islamic Republic of)": "IRN", "Republic of Korea": "KOR", "South Korea": "KOR",
    "Democratic People's Republic of Korea": "PRK",
    "Bolivia (Plurinational State of)": "BOL", "Venezuela (Bolivarian Republic of)": "VEN",
    "Viet Nam": "VNM", "Lao People's Democratic Republic": "LAO",
    "Türkiye": "TUR", "Turkey": "TUR", "Czechia": "CZE", "Czech Republic": "CZE",
    "Republic of Moldova": "MDA", "Sudan": "SDN", "South Sudan": "SSD", "Zambia": "ZMB",
    "United Republic of Tanzania": "TZA", "Tanzania": "TZA",
    "Egypt": "EGY", "Saudi Arabia": "SAU", "Nigeria": "NGA", "Ethiopia": "ETH",
    "Bangladesh": "BGD", "Philippines": "PHL", "Mexico": "MEX", "Yemen": "YEM",
    "Somalia": "SOM", "Afghanistan": "AFG", "Haiti": "HTI", "Russian Federation": "RUS",
    "North Korea": "PRK", "Cabo Verde": "CPV", "Cape Verde": "CPV",
    "Côte d'Ivoire": "CIV", "Cote d'Ivoire": "CIV",
    "Bahamas": "BHS", "Trinidad and Tobago": "TTO",
    "United Arab Emirates": "ARE", "Bahrain": "BHR", "Oman": "OMN", "Qatar": "QAT",
    "Iraq": "IRQ", "Israel": "ISR", "Jordan": "JOR", "Kuwait": "KWT",
    "Lebanon": "LBN", "Syrian Arab Republic": "SYR", "Syria": "SYR",
    "Brunei Darussalam": "BRN", "Brunei": "BRN", "Cambodia": "KHM",
    "Hong Kong SAR": "HKG", "Singapore": "SGP", "Japan": "JPN",
}


def main():
    print(f"[INFO] FAOSTAT FBS bulk download → {BULK_URL}")
    try:
        r = http_get(BULK_URL, timeout=180, headers={"Accept": "application/zip,*/*"})
    except Exception as e:
        write_json(
            "country_caloric_shares.json", {},
            source="FAOSTAT FBS",
            notes=f"Bulk download failed: {e}"
        )
        return

    zip_bytes = r.content
    if not zip_bytes or len(zip_bytes) < 1024:
        write_json(
            "country_caloric_shares.json", {},
            source="FAOSTAT FBS",
            notes=f"Bulk download returned empty body ({len(zip_bytes) if zip_bytes else 0} bytes)"
        )
        return

    # Open ZIP, find the CSV inside (filename: FoodBalanceSheets_E_All_Data.csv)
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        write_json(
            "country_caloric_shares.json", {},
            source="FAOSTAT FBS",
            notes=f"ZIP parse failed: {e}"
        )
        return

    csv_member = None
    for name in zf.namelist():
        if name.endswith(".csv") and "All_Data" in name and "Normalized" not in name:
            csv_member = name
            break
    if not csv_member:
        # Fall back to first CSV
        for name in zf.namelist():
            if name.endswith(".csv"):
                csv_member = name
                break
    if not csv_member:
        write_json(
            "country_caloric_shares.json", {},
            source="FAOSTAT FBS",
            notes=f"No CSV found in ZIP: {zf.namelist()}"
        )
        return

    print(f"[INFO] Reading {csv_member} from ZIP ({len(zip_bytes)//1024} KB)")
    raw = zf.read(csv_member).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))

    # FAOSTAT "All Data" (wide) format columns we care about:
    #   Area Code, Area, Item Code, Item, Element Code, Element, Y2020, Y2021, ..., Y2022
    # The most recent populated year column is the one we want.
    fieldnames = reader.fieldnames or []
    year_cols = sorted(
        [f for f in fieldnames if f.startswith("Y") and f[1:].isdigit()],
        reverse=True
    )
    if not year_cols:
        write_json(
            "country_caloric_shares.json", {},
            source="FAOSTAT FBS",
            notes=f"No Y-prefix year columns in CSV header: {fieldnames[:10]}"
        )
        return
    print(f"[INFO] Year columns found: {year_cols[:5]}... (newest first)")

    # Collect raw kcal per (iso3, item_code) using the latest non-empty year per row
    raw_data = {}   # iso3 → {item_key: kcal, '_year': int, '_country': str}
    rows_seen = 0
    for row in reader:
        rows_seen += 1
        if _int(row.get("Element Code")) != ELEMENT_KCAL:
            continue
        ic = _int(row.get("Item Code"))
        if ic not in ITEMS:
            continue
        item_key = ITEMS[ic]
        area_name = (row.get("Area") or "").strip()
        ac = _int(row.get("Area Code"))
        iso3 = NAME_TO_ISO3.get(area_name) or FAO_AREA_TO_ISO3.get(ac)
        if not iso3:
            continue
        # Find the most recent year with a value
        latest_year = None
        latest_val = None
        for yc in year_cols:
            v = _num(row.get(yc))
            if v is not None and v > 0:
                latest_year = int(yc[1:])
                latest_val = v
                break
        if latest_val is None:
            continue
        slot = raw_data.setdefault(iso3, {"_country": area_name})
        # If we already have this item key from a more recent year, keep the newer one
        existing_year = slot.get("_year_" + item_key)
        if existing_year is None or latest_year > existing_year:
            slot[item_key] = latest_val
            slot["_year_" + item_key] = latest_year
        # Track the most-recent total year as the country's reference year
        if item_key == "total":
            existing = slot.get("_year")
            if existing is None or latest_year > existing:
                slot["_year"] = latest_year

    print(f"[INFO] Parsed {rows_seen} CSV rows, kept data for {len(raw_data)} countries")

    # Compute shares
    out = {}
    for iso3, slot in raw_data.items():
        total = slot.get("total")
        if not total or total <= 0:
            continue
        wheat = slot.get("wheat", 0) or 0
        rice  = slot.get("rice",  0) or 0
        maize = slot.get("maize", 0) or 0
        out[iso3] = {
            "wheat_pct": round(wheat / total * 100, 2),
            "rice_pct":  round(rice  / total * 100, 2),
            "maize_pct": round(maize / total * 100, 2),
            "total_kcal_per_day": round(total, 1),
            "wheat_kcal": round(wheat, 1),
            "rice_kcal":  round(rice, 1),
            "maize_kcal": round(maize, 1),
            "year": slot.get("_year"),
            "country": slot.get("_country"),
            "source": "FAOSTAT Food Balance Sheets",
            "source_url": "https://www.fao.org/faostat/en/#data/FBS",
            "method": "kcal/capita/day (element 664), item-share of Grand Total (item 2901)",
            "quality_flag": "sourced",
        }

    print(f"[INFO] Wrote caloric shares for {len(out)} countries")

    write_json(
        "country_caloric_shares.json",
        out,
        source=("FAOSTAT FBS bulk download "
                "(bulks-faostat.fao.org/production/FoodBalanceSheets_E_All_Data.zip)"),
        notes=(
            "Wheat / rice / maize caloric share % of total daily caloric supply per country. "
            "Direct food calories only — does NOT count indirect calories via animal feed. "
            f"Covered {len(out)} of {len(FAO_AREA_TO_ISO3)} mapped countries. "
            "Most-recent populated year per country (typically 2022 in the current FBS vintage)."
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
