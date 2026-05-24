"""
USDA PSD — Production, Supply & Distribution.

Pipeline 5 of the structural-data series. Gives FoodShield observed
production / imports / exports / consumption / stocks per country per staple
per marketing year — replacing the modeled fallback in
commodityTradeDependency() for the 4 staples that matter most.

WHY PSD:
  USDA PSD is the gold standard for cross-country staple supply data. Updated
  monthly on WASDE release day. Public, no auth.

ARCHITECTURE NOTE (May 2026):
  There's no single combined PSD CSV. USDA publishes per-commodity-group ZIPs:
    psd_grains_pulses_csv.zip  (wheat, rice, corn)
    psd_oilseeds_csv.zip       (soybeans)
    psd_cotton_csv.zip
    psd_livestock_csv.zip
    ...
  We pull the two groups we need (grains+oilseeds), parse, filter, emit.

DATA WE EXTRACT (per country × commodity × latest marketing year):
  Production         (attribute 28)
  MY Imports         (attribute 57)
  MY Exports         (attribute 86)
  Domestic Consumption (attribute 130)
  Ending Stocks      (attribute 176)

COMMODITIES (PSD codes, verified May 2026):
  410000   Wheat
  422110   Rice, Milled
  440000   Corn
  2222000  Soybeans

FAS COUNTRY CODES → ISO3:
  PSD uses 2-char FAS codes (e.g. US, AR, BR, IN, CN, AU, RU). Most equal
  ISO 3166 alpha-2 — we use pycountry-style mapping for the bulk and a
  small override dict for FAS-specific aggregates (EU, FU=Former USSR, etc.).

OUTPUT: data/usda_psd.json
  {
    "_meta": {...},
    "data": {
      "IDN": {
        "wheat":    {production_kt: 0, imports_kt: 10523, exports_kt: 12, consumption_kt: 10500, stocks_kt: 1130, year: 2024, source: ...},
        "rice":     {production_kt: 34121, imports_kt: 800, ...},
        "corn":     {...},
        "soybeans": {...}
      },
      ...
    }
  }

Wired into:
  - run_all.py STEPS list
  - build_source_manifest.py (mode='trade')
  - foodshield-v19.html: LIVE.psd loader + commodityTradeDependency()
    upgrade so observed PSD values supersede modeled fallback.
"""
import csv
import io
import zipfile

from _common import http_get, write_json

# Two ZIPs — grains+pulses (wheat/rice/corn) + oilseeds (soybeans).
# Same column schema in both so we can parse them with shared code.
URLS = [
    ("grains_pulses", "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip"),
    ("oilseeds",      "https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip"),
]

# PSD commodity codes (verified via psd_grains_pulses CSV header May 2026)
COMMODITY_TO_KEY = {
    "0410000":  "wheat",
    "0422110":  "rice",
    "0440000":  "corn",
    "2222000":  "soybeans",
    # Some PSD vintages drop the leading zero in the CSV — accept both
    "410000":   "wheat",
    "422110":   "rice",
    "440000":   "corn",
}

# PSD attribute IDs we care about. Use string-match on Attribute_Description as
# the primary filter (IDs occasionally renumber); IDs kept here for reference.
ATTR_KEYS = {
    "Production":            ("production_kt", 28),
    "MY Imports":            ("imports_kt",    57),
    "MY Exports":            ("exports_kt",    86),
    "Domestic Consumption":  ("consumption_kt", 130),
    "Ending Stocks":         ("stocks_kt",     176),
}

# FAS 2-char code → ISO3. PSD uses ISO 3166-1 alpha-2 for most countries with
# a few FAS-specific deviations. This list covers the producers we care about
# (every country that ships >50kt of any staple) plus typical importers.
FAS_TO_ISO3 = {
    # Major producers + exporters
    "US": "USA", "AR": "ARG", "BR": "BRA", "AU": "AUS", "CA": "CAN",
    "RU": "RUS", "UA": "UKR", "KZ": "KAZ", "FR": "FRA", "DE": "DEU",
    "PL": "POL", "RO": "ROU", "HU": "HUN", "ES": "ESP", "IT": "ITA",
    "TR": "TUR", "IN": "IND", "CH": "CHN", "TH": "THA", "VM": "VNM",
    "MX": "MEX", "ID": "IDN", "ML": "MLI", "ET": "ETH", "EG": "EGY",
    "KR": "KOR", "JA": "JPN", "BG": "BGD", "PK": "PAK", "PH": "PHL",
    "MY": "MYS", "BD": "BGD", "NG": "NGA", "ZA": "ZAF", "RP": "PHL",
    "MM": "MMR", "LA": "LAO", "KH": "KHM",
    # FAS code "BU" is Bulgaria (NOT Burkina Faso). Burkina Faso is "UV".
    # FAS code "ES" is El Salvador (NOT Spain). Spain is "SP".
    # FAS code "RS" is Russia (NOT Serbia). Serbia is "RB" or "RI".
    # These were the source of the BGR/Bangladesh, ESP/El Salvador, SRB/Russia swap bugs.
    "BU": "BGR",  # Bulgaria
    "UV": "BFA",  # Burkina Faso (Upper Volta — historical USDA code)
    "ES": "SLV",  # El Salvador (not Spain)
    "SP": "ESP",  # Spain
    "RS": "RUS",  # Russia (USDA Foreign Agricultural Service code)
    "RB": "SRB",  # Serbia
    "RI": "SRB",  # Serbia (alternate)
    "ER": "ERI", "SO": "SOM", "SU": "SDN", "SD": "SDN",
    "YM": "YEM", "AF": "AFG", "IR": "IRN", "IZ": "IRQ", "SY": "SYR",
    "DJ": "DJI", "LY": "LBY", "MO": "MAR", "TS": "TUN", "AG": "DZA",
    "CO": "COL", "PE": "PER", "VE": "VEN", "EC": "ECU", "BO": "BOL",
    "CL": "CHL", "PA": "PAN", "GT": "GTM", "HO": "HND", "NU": "NIC",
    "CR": "CRI", "JM": "JAM", "CU": "CUB", "DR": "DOM",
    "AL": "ALB", "MK": "MKD", "BK": "BIH", "MJ": "MNE",
    "EN": "EST", "LH": "LTU", "LG": "LVA", "FI": "FIN", "SW": "SWE",
    "DA": "DNK", "NL": "NLD", "BE": "BEL", "AT": "AUT", "EZ": "CZE",
    "SI": "SVN", "SK": "SVK", "GR": "GRC", "PO": "PRT", "IC": "ISL",
    "IE": "IRL", "NO": "NOR", "SZ": "CHE", "EI": "IRL", "UK": "GBR",
    "TW": "TWN", "HK": "HKG", "KS": "KOR", "KN": "PRK", "MG": "MNG",
    "TI": "TJK", "UZ": "UZB", "KG": "KGZ", "TX": "TKM", "AJ": "AZE",
    "AM": "ARM", "GG": "GEO", "BO_": "BLR", "MD": "MDA",
    "JO": "JOR", "LE": "LBN", "SA": "SAU", "AE": "ARE", "KU": "KWT",
    "MU": "OMN", "QA": "QAT", "BA": "BHR", "IS": "ISR",
    "AO": "AGO", "MZ": "MOZ", "ZI": "ZWE", "ZA_": "ZAF", "BC": "BWA",
    "WA": "NAM", "LT": "LSO", "WZ": "SWZ", "MI": "MWI", "ZM": "ZMB",
    "TZ": "TZA", "KE": "KEN", "UG": "UGA", "RW": "RWA", "BY": "BDI",
    "CG": "COD", "CF": "COG", "GB": "GAB", "CM": "CMR", "CT": "CAF",
    "CD": "TCD", "NI": "NER", "ML_": "MLI", "MR": "MRT", "SG": "SEN",
    "GV": "GIN", "PU": "GNB", "GA": "GMB", "LI": "LBR", "SL": "SLE",
    "TO": "TGO", "BN": "BEN", "IV": "CIV", "GH": "GHA", "CV": "CPV",
    "PP": "PNG", "FJ": "FJI", "NZ": "NZL", "SH": "SHN", "MV": "MDV",
    "CE": "LKA", "NP": "NPL", "BT": "BTN", "BX": "BRN",
    # Aggregates — handled specially below
    # "E4": "EU",  # 27-country aggregate
    # "FU": "FSU", # Former Soviet Union (pre-1992)
}

# Country names we'll also match by string if FAS code missing/ambiguous.
NAME_TO_ISO3 = {
    "United States": "USA", "Argentina": "ARG", "Brazil": "BRA",
    "Australia": "AUS", "Canada": "CAN", "Russia": "RUS", "Ukraine": "UKR",
    "European Union": None,   # explicitly skip the aggregate
    "Former USSR": None,
    "Other Asia": None,
    "Hong Kong": "HKG", "Taiwan": "TWN",
    "Philippines": "PHL", "Vietnam": "VNM", "Korea, South": "KOR",
    "Korea, North": "PRK", "Burma": "MMR", "Iran": "IRN",
    "Bangladesh": "BGD", "Indonesia": "IDN", "Malaysia": "MYS",
    "Pakistan": "PAK", "Egypt": "EGY", "Turkey": "TUR",
    "China": "CHN", "India": "IND", "Mexico": "MEX",
    "Saudi Arabia": "SAU", "United Arab Emirates": "ARE",
    "United Kingdom": "GBR", "South Africa": "ZAF",
    "Nigeria": "NGA", "Ethiopia": "ETH", "Algeria": "DZA",
    "Morocco": "MAR", "Tunisia": "TUN", "Sudan": "SDN", "Yemen": "YEM",
    "Afghanistan": "AFG", "Iraq": "IRQ", "Syria": "SYR", "Lebanon": "LBN",
    "Jordan": "JOR", "Israel": "ISR",
}


def main():
    by_country = {}   # iso3 → {commodity_key → {attr_key → value, _year → int}}
    total_rows_scanned = 0
    total_rows_kept = 0

    for group_label, url in URLS:
        print(f"[INFO] USDA PSD bulk → {group_label} ({url})")
        try:
            r = http_get(url, timeout=180, headers={"Accept": "application/zip,*/*"}, retries=3)
        except Exception as e:
            print(f"  [warn] download failed for {group_label}: {e}")
            continue
        zip_bytes = r.content
        if not zip_bytes or len(zip_bytes) < 1024:
            print(f"  [warn] empty body for {group_label}: {len(zip_bytes)} bytes")
            continue
        print(f"  [INFO] {len(zip_bytes)//1024} KB ZIP")

        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as e:
            print(f"  [warn] ZIP parse failed for {group_label}: {e}")
            continue

        csv_name = next((n for n in zf.namelist() if n.endswith(".csv")), None)
        if not csv_name:
            print(f"  [warn] no CSV in ZIP for {group_label}: {zf.namelist()}")
            continue
        print(f"  [INFO] reading {csv_name}")

        # PSD CSVs use cp1252/latin-1 encoding (not UTF-8) — accented country names
        # break decoder otherwise. Use latin-1 which is a byte-safe superset.
        with zf.open(csv_name, "r") as f:
            text_stream = io.TextIOWrapper(f, encoding="latin-1", errors="replace", newline="")
            reader = csv.DictReader(text_stream)
            rows_seen, rows_kept = _parse_one_csv(reader, by_country)
            total_rows_scanned += rows_seen
            total_rows_kept += rows_kept
            print(f"  [INFO] scanned {rows_seen} rows, kept {rows_kept}")

    if not by_country:
        write_json(
            "usda_psd.json", {},
            source="USDA PSD",
            notes="Both bulk downloads failed or returned no rows.",
        )
        return

    print(f"[INFO] Total: {total_rows_scanned} rows scanned, {total_rows_kept} kept, "
          f"covering {len(by_country)} countries")

    # Sanity-check a few reference rows
    for ref in ("USA", "ARG", "BRA", "AUS", "RUS", "UKR", "IND", "CHN", "IDN", "EGY"):
        if ref in by_country:
            wheat = by_country[ref].get("wheat", {})
            corn = by_country[ref].get("corn", {})
            rice = by_country[ref].get("rice", {})
            print(f"  [ref] {ref}: "
                  f"wheat_prod={wheat.get('production_kt', 'n/a')}, "
                  f"corn_prod={corn.get('production_kt', 'n/a')}, "
                  f"rice_prod={rice.get('production_kt', 'n/a')}")

    write_json(
        "usda_psd.json",
        by_country,
        source="USDA PSD bulk (apps.fas.usda.gov/psdonline/downloads/)",
        notes=(
            f"Observed production / imports / exports / consumption / stocks per country "
            f"per staple per marketing year. Latest marketing year per (country, commodity, "
            f"attribute). Values in 1000 metric tonnes. Commodities: wheat, rice (milled), "
            f"corn, soybeans. {len(by_country)} countries covered. "
            f"Source refreshes monthly on WASDE release day; cron pulls every 6h but "
            f"upstream data rarely changes between WASDE windows."
        ),
    )


def _parse_one_csv(reader, by_country):
    """Streaming parse of one PSD CSV. Mutates by_country in place."""
    rows_seen = 0
    rows_kept = 0
    for row in reader:
        rows_seen += 1
        if rows_seen % 100000 == 0:
            print(f"    [progress] {rows_seen} rows scanned, {rows_kept} kept")

        commodity_code = (row.get("Commodity_Code") or "").strip()
        commodity_key = COMMODITY_TO_KEY.get(commodity_code)
        if not commodity_key:
            continue

        attr_desc = (row.get("Attribute_Description") or "").strip()
        if attr_desc not in ATTR_KEYS:
            continue
        attr_key, _attr_id = ATTR_KEYS[attr_desc]

        # Country resolution: FAS code first, then country name fallback.
        fas_code = (row.get("Country_Code") or "").strip()
        country_name = (row.get("Country_Name") or "").strip()
        iso3 = FAS_TO_ISO3.get(fas_code) or NAME_TO_ISO3.get(country_name)
        if not iso3:
            continue   # skip EU aggregates, Former USSR, Other Asia, unrecognized

        # Year + value
        year = _int(row.get("Market_Year"))
        value = _num(row.get("Value"))
        if year is None or value is None:
            continue

        # Keep most-recent year per (country, commodity, attribute).
        commodity_slot = by_country.setdefault(iso3, {}).setdefault(commodity_key, {
            "country": country_name,
        })
        existing_year = commodity_slot.get("_year_" + attr_key)
        if existing_year is None or year > existing_year:
            commodity_slot[attr_key] = round(value, 1)
            commodity_slot["_year_" + attr_key] = year
            # Track the overall latest year per commodity
            existing_overall = commodity_slot.get("year")
            if existing_overall is None or year > existing_overall:
                commodity_slot["year"] = year
                commodity_slot["source"] = "USDA PSD"
                commodity_slot["source_url"] = "https://apps.fas.usda.gov/psdonline/"
                commodity_slot["quality_flag"] = "sourced"
                commodity_slot["unit"] = "1000 MT"
        rows_kept += 1

    return rows_seen, rows_kept


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
