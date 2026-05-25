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
#
# WHY MULTIPLE KEYS PER METRIC (May 2026 fix):
# The USDA PSD bulk CSV inconsistently labels imports/exports. For grains the
# header is `MY Imports` / `MY Exports`, but for rice/oilseeds many rows use
# bare `Imports` / `Exports` or `Total Imports` / `Total Exports`. Earlier
# versions of this script only matched `MY Imports`/`MY Exports`, which caused
# imports_kt/exports_kt to be zero across all countries — forcing the frontend
# into heuristic fallback in commodityTradeDependency(). Each entry below
# resolves to the same internal key, so any label captures the value.
ATTR_KEYS = {
    # Production
    "Production":                       ("production_kt", 28),
    # Imports — accept every label USDA emits for imported volume
    "MY Imports":                       ("imports_kt",    57),
    "Imports":                          ("imports_kt",    57),
    "Total Imports":                    ("imports_kt",    57),
    "Imports for Domestic Consumption": ("imports_kt",    57),
    "Imports - Calendar Year":          ("imports_kt",    57),
    "TY Imports":                       ("imports_kt",    57),
    # Exports — same treatment
    "MY Exports":                       ("exports_kt",    86),
    "Exports":                          ("exports_kt",    86),
    "Total Exports":                    ("exports_kt",    86),
    "Exports - Calendar Year":          ("exports_kt",    86),
    "TY Exports":                       ("exports_kt",    86),
    # Consumption — only true aggregate consumption labels (not sub-components
    # like Feed Dom. Consumption or FSI Consumption, which would shadow the
    # full total)
    "Domestic Consumption":             ("consumption_kt", 130),
    "Total Consumption":                ("consumption_kt", 130),
    "Domestic Use":                     ("consumption_kt", 130),
    # Stocks
    "Ending Stocks":                    ("stocks_kt",     176),
}

# FAS 2-char code → ISO3. PSD uses ISO 3166-1 alpha-2 for most countries with
# a few FAS-specific deviations. This list covers the producers we care about
# (every country that ships >50kt of any staple) plus typical importers.
# Canonical USDA Foreign Agricultural Service 2-letter codes → ISO 3166-1 alpha-3.
#
# IMPORTANT: USDA FAS codes are NOT the same as ISO 3166-1 alpha-2. The FAS code
# table is a legacy USDA scheme — e.g. Germany is "GM" (not "DE"), Belarus is
# "BY" (not "BL"), Bulgaria is "BU" (not "BG"). Confusion in this dict has
# caused several country-swap bugs in the served JSON (BGR↔Bangladesh,
# ESP↔El Salvador, SRB↔Russia, BOL↔Belarus, NER↔Nigeria, NGA↔Niger, DEU absent).
# Every entry below should be cross-referenced against the USDA FAS country
# code list before editing — do NOT add entries by ISO-alpha-2 intuition.
FAS_TO_ISO3 = {
    # ── Major producers + exporters (canonical FAS) ────────────────────────
    "US": "USA",  # United States
    "AR": "ARG",  # Argentina
    "BR": "BRA",  # Brazil
    "AU": "AUS",  # Australia
    "CA": "CAN",  # Canada
    "RS": "RUS",  # Russia (FAS code, NOT "RU")
    "UP": "UKR",  # Ukraine (FAS uses "UP", not "UA")
    "KZ": "KAZ",  # Kazakhstan
    "FR": "FRA",  # France
    "GM": "DEU",  # Germany (FAS uses "GM", NOT "DE")
    "PL": "POL",  # Poland
    "RO": "ROU",  # Romania
    "HU": "HUN",  # Hungary
    "SP": "ESP",  # Spain (FAS uses "SP", NOT "ES")
    "IT": "ITA",  # Italy
    "TU": "TUR",  # Turkey (FAS uses "TU", NOT "TR")
    "IN": "IND",  # India
    "CH": "CHN",  # China (FAS uses "CH", NOT "CN")
    "TH": "THA",  # Thailand
    "VM": "VNM",  # Vietnam
    "MX": "MEX",  # Mexico
    "ID": "IDN",  # Indonesia
    "ML": "MLI",  # Mali
    "ET": "ETH",  # Ethiopia
    "EG": "EGY",  # Egypt
    "KS": "KOR",  # Korea, South (FAS uses "KS")
    "KN": "PRK",  # Korea, North (FAS uses "KN")
    "JA": "JPN",  # Japan (FAS uses "JA", NOT "JP")
    "BG": "BGD",  # Bangladesh (FAS uses "BG", NOT "BD")
    "PK": "PAK",  # Pakistan
    "RP": "PHL",  # Philippines (FAS uses "RP", NOT "PH")
    "MY": "MYS",  # Malaysia
    "NI": "NGA",  # Nigeria (FAS uses "NI" for Nigeria — common confusion source)
    "NG": "NER",  # Niger (FAS uses "NG" for Niger — common confusion source)
    "SF": "ZAF",  # South Africa (FAS uses "SF", NOT "ZA")
    "BM": "MMR",  # Burma/Myanmar
    "LA": "LAO",  # Laos
    "CB": "KHM",  # Cambodia (FAS uses "CB", NOT "KH")
    # ── Europe ─────────────────────────────────────────────────────────────
    "BU": "BGR",  # Bulgaria (FAS — NOT Burkina Faso, which is "UV")
    "UV": "BFA",  # Burkina Faso (Upper Volta — historical USDA code)
    "ES": "SLV",  # El Salvador (FAS uses "ES" — NOT Spain, which is "SP")
    "RB": "SRB",  # Serbia
    "RI": "SRB",  # Serbia (alternate)
    "ER": "ERI",  # Eritrea
    "SO": "SOM",  # Somalia
    "SU": "SDN",  # Sudan (current)
    "OD": "SSD",  # South Sudan (FAS uses "OD")
    "YM": "YEM",  # Yemen
    "AF": "AFG",  # Afghanistan
    "IR": "IRN",  # Iran
    "IZ": "IRQ",  # Iraq
    "SY": "SYR",  # Syria
    "DJ": "DJI",  # Djibouti
    "LY": "LBY",  # Libya
    "MO": "MAR",  # Morocco
    "TS": "TUN",  # Tunisia
    "AG": "DZA",  # Algeria
    # ── Americas ───────────────────────────────────────────────────────────
    "CO": "COL",  # Colombia
    "PE": "PER",  # Peru
    "VE": "VEN",  # Venezuela
    "EC": "ECU",  # Ecuador
    "BL": "BOL",  # Bolivia (FAS uses "BL", NOT "BO" — BO is Belarus)
    "BO": "BLR",  # Belarus (FAS uses "BO" — common confusion source)
    "CI": "CHL",  # Chile (FAS uses "CI", NOT "CL")
    "PM": "PAN",  # Panama (FAS uses "PM", NOT "PA")
    "PA": "PRY",  # Paraguay (FAS uses "PA" for Paraguay — common confusion source)
    "GT": "GTM",  # Guatemala
    "HO": "HND",  # Honduras
    "NU": "NIC",  # Nicaragua
    "CS": "CRI",  # Costa Rica (FAS uses "CS")
    "JM": "JAM",  # Jamaica
    "CU": "CUB",  # Cuba
    "DR": "DOM",  # Dominican Republic
    # ── Eastern Europe / Baltics ───────────────────────────────────────────
    "AL": "ALB",  # Albania
    "MK": "MKD",  # North Macedonia
    "BK": "BIH",  # Bosnia and Herzegovina
    "MJ": "MNE",  # Montenegro
    "EN": "EST",  # Estonia
    "LH": "LTU",  # Lithuania
    "LG": "LVA",  # Latvia
    "FI": "FIN",  # Finland
    "SW": "SWE",  # Sweden
    "DA": "DNK",  # Denmark
    "NL": "NLD",  # Netherlands
    "BE": "BEL",  # Belgium
    "AU_AT": "AUT",  # Austria placeholder (FAS uses "AU" but that's Australia)
    "EZ": "CZE",  # Czech Republic
    "SI": "SVN",  # Slovenia
    "LO": "SVK",  # Slovakia (FAS uses "LO")
    "GR": "GRC",  # Greece
    "PO": "PRT",  # Portugal (FAS uses "PO")
    "IC": "ISL",  # Iceland
    "EI": "IRL",  # Ireland (FAS uses "EI")
    "NO": "NOR",  # Norway
    "SZ": "CHE",  # Switzerland
    "UK": "GBR",  # United Kingdom
    # ── Asia ───────────────────────────────────────────────────────────────
    "TW": "TWN",  # Taiwan
    "HK": "HKG",  # Hong Kong
    "MG": "MNG",  # Mongolia
    "TI": "TJK",  # Tajikistan
    "UZ": "UZB",  # Uzbekistan
    "KG": "KGZ",  # Kyrgyzstan
    "TX": "TKM",  # Turkmenistan
    "AJ": "AZE",  # Azerbaijan
    "AM": "ARM",  # Armenia
    "GG": "GEO",  # Georgia
    "MD": "MDA",  # Moldova
    "JO": "JOR",  # Jordan
    "LE": "LBN",  # Lebanon
    "SA": "SAU",  # Saudi Arabia
    "AE": "ARE",  # United Arab Emirates
    "KU": "KWT",  # Kuwait
    "MU": "OMN",  # Oman
    "QA": "QAT",  # Qatar
    "BA": "BHR",  # Bahrain
    "IS": "ISR",  # Israel
    # ── Africa ─────────────────────────────────────────────────────────────
    "AO": "AGO",  # Angola
    "MZ": "MOZ",  # Mozambique
    "ZI": "ZWE",  # Zimbabwe
    "BC": "BWA",  # Botswana
    "WA": "NAM",  # Namibia
    "LT": "LSO",  # Lesotho
    "WZ": "SWZ",  # Eswatini
    "MI": "MWI",  # Malawi
    "ZA": "ZMB",  # Zambia (FAS uses "ZA" — NOT South Africa, which is "SF")
    "TZ": "TZA",  # Tanzania
    "KE": "KEN",  # Kenya
    "UG": "UGA",  # Uganda
    "RW": "RWA",  # Rwanda
    "BY": "BDI",  # Burundi (FAS uses "BY" for Burundi — NOT Belarus, which is "BO")
    "CG": "COD",  # Congo (Kinshasa) / DRC
    "CF": "COG",  # Congo (Brazzaville)
    "GB": "GAB",  # Gabon
    "CM": "CMR",  # Cameroon
    "CT": "CAF",  # Central African Republic
    "CD": "TCD",  # Chad
    "MR": "MRT",  # Mauritania
    "SG": "SEN",  # Senegal
    "GV": "GIN",  # Guinea
    "PU": "GNB",  # Guinea-Bissau
    "GA": "GMB",  # Gambia
    "LI": "LBR",  # Liberia
    "SL": "SLE",  # Sierra Leone
    "TO": "TGO",  # Togo
    "BN": "BEN",  # Benin
    "IV": "CIV",  # Cote d'Ivoire
    "GH": "GHA",  # Ghana
    "CV": "CPV",  # Cabo Verde
    # ── Oceania + small states ─────────────────────────────────────────────
    "PP": "PNG",  # Papua New Guinea
    "FJ": "FJI",  # Fiji
    "NZ": "NZL",  # New Zealand
    "SH": "SHN",  # Saint Helena
    "MV": "MDV",  # Maldives
    "CE": "LKA",  # Sri Lanka
    "NP": "NPL",  # Nepal
    "BT": "BTN",  # Bhutan
    "BX": "BRN",  # Brunei
    # Aggregates — explicitly NOT mapped so they're dropped:
    # "E4": EU 27-country aggregate
    # "FU": Former Soviet Union (pre-1992)
}

# Country names we'll also match by string if FAS code missing/ambiguous.
# This is the safety net for when the FAS_TO_ISO3 dict is missing a code —
# the country_name column in PSD bulk CSV always carries the canonical name.
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
    # Countries previously misrouted (May 2026 dict swap fix). These are the
    # name-based safety nets that catch the row even if a future dict edit
    # drops/renames the FAS code. Names below MUST match the country_name
    # string USDA emits in the CSV — verified May 2026.
    "Germany": "DEU",
    "Belarus": "BLR",
    "Bolivia": "BOL",
    "Niger": "NER",
    "Bulgaria": "BGR",
    "Burkina Faso": "BFA",
    "El Salvador": "SLV",
    "Spain": "ESP",
    "Serbia": "SRB",
    "Paraguay": "PRY",
    "Panama": "PAN",
    "Chile": "CHL",
    "Slovakia": "SVK",
    "Austria": "AUT",
    "France": "FRA", "Italy": "ITA", "Poland": "POL", "Romania": "ROU",
    "Hungary": "HUN", "Netherlands": "NLD", "Belgium": "BEL",
    "Czech Republic": "CZE", "Slovenia": "SVN", "Greece": "GRC",
    "Portugal": "PRT", "Sweden": "SWE", "Denmark": "DNK", "Finland": "FIN",
    "Norway": "NOR", "Switzerland": "CHE", "Ireland": "IRL", "Iceland": "ISL",
    "Estonia": "EST", "Latvia": "LVA", "Lithuania": "LTU",
    "Albania": "ALB", "North Macedonia": "MKD", "Bosnia and Herzegovina": "BIH",
    "Montenegro": "MNE", "Moldova": "MDA",
    "Kazakhstan": "KAZ", "Uzbekistan": "UZB", "Tajikistan": "TJK",
    "Kyrgyzstan": "KGZ", "Turkmenistan": "TKM",
    "Azerbaijan": "AZE", "Armenia": "ARM", "Georgia": "GEO",
    "Cambodia": "KHM", "Laos": "LAO", "Mongolia": "MNG", "Nepal": "NPL",
    "Sri Lanka": "LKA", "Bhutan": "BTN", "Brunei": "BRN", "Maldives": "MDV",
    "Kenya": "KEN", "Uganda": "UGA", "Tanzania": "TZA", "Rwanda": "RWA",
    "Burundi": "BDI", "Zambia": "ZMB", "Malawi": "MWI", "Mozambique": "MOZ",
    "Zimbabwe": "ZWE", "Namibia": "NAM", "Botswana": "BWA", "Lesotho": "LSO",
    "Eswatini": "SWZ", "Angola": "AGO", "Madagascar": "MDG",
    "Mali": "MLI", "Senegal": "SEN", "Guinea": "GIN", "Guinea-Bissau": "GNB",
    "Liberia": "LBR", "Sierra Leone": "SLE", "Togo": "TGO", "Benin": "BEN",
    "Ghana": "GHA", "Cote d'Ivoire": "CIV", "Cabo Verde": "CPV",
    "Cameroon": "CMR", "Central African Republic": "CAF", "Chad": "TCD",
    "Congo (Kinshasa)": "COD", "Congo (Brazzaville)": "COG", "Gabon": "GAB",
    "Mauritania": "MRT", "Gambia, The": "GMB",
    "Cuba": "CUB", "Jamaica": "JAM", "Dominican Republic": "DOM",
    "Honduras": "HND", "Guatemala": "GTM", "Nicaragua": "NIC",
    "Costa Rica": "CRI", "Colombia": "COL", "Peru": "PER", "Ecuador": "ECU",
    "Venezuela": "VEN",
    "Eritrea": "ERI", "Somalia": "SOM", "Djibouti": "DJI",
    "South Sudan": "SSD",
    "Papua New Guinea": "PNG", "Fiji": "FJI", "New Zealand": "NZL",
    "Kuwait": "KWT", "Oman": "OMN", "Qatar": "QAT", "Bahrain": "BHR",
    "Thailand": "THA", "Japan": "JPN",
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

    # Pipeline-health counters — surfaced in workflow log so we notice when
    # the parser stops capturing imports/exports (the May-2026 regression).
    n_imp = sum(
        1 for c in by_country.values()
        if any(isinstance(v, dict) and "imports_kt" in v for v in c.values())
    )
    n_exp = sum(
        1 for c in by_country.values()
        if any(isinstance(v, dict) and "exports_kt" in v for v in c.values())
    )
    n_prod = sum(
        1 for c in by_country.values()
        if any(isinstance(v, dict) and "production_kt" in v for v in c.values())
    )
    print(f"[INFO] PSD captured imports for {n_imp} countries, exports for {n_exp} countries")
    print(f"[INFO] PSD captured production for {n_prod} countries (total countries: {len(by_country)})")
    if n_imp == 0 or n_exp == 0:
        print("[WARN] imports_kt or exports_kt count is ZERO — frontend will fall back to "
              "heuristic dependency. Check ATTR_KEYS labels vs current PSD CSV header.")

    write_json(
        "usda_psd.json",
        by_country,
        source="USDA PSD bulk (apps.fas.usda.gov/psdonline/downloads/)",
        notes=(
            f"Observed production / imports / exports / consumption / stocks per country "
            f"per staple per marketing year. Latest marketing year per (country, commodity, "
            f"attribute). Values in 1000 metric tonnes. Commodities: wheat, rice (milled), "
            f"corn, soybeans. {len(by_country)} countries covered; "
            f"imports captured for {n_imp}, exports for {n_exp}. "
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
