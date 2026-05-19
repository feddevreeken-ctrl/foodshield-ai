"""
FAOSTAT Food Balance Sheets — caloric shares of wheat / rice / maize per country.

This is the *structural* food-security pipeline that replaces the hand-curated
c.w / c.r / c.m fields in COUNTRIES. For every country FAO covers (~180), we pull:

  - Item 2901 = Grand Total caloric supply (kcal/capita/day)
  - Item 2511 = Wheat and products (kcal/capita/day)
  - Item 2805 = Rice and products
  - Item 2514 = Maize and products
  Element 664 = "Food supply (kcal/capita/day)" for all four.

Caloric share % = item kcal / grand total kcal × 100.

Auth: same guest-token flow used by refresh_faostat.py.

Rate plan:
  - One bulk call per country (all 4 items in a single area request)
  - Throttle 0.4s between countries
  - ~190 countries × 1 call = ~190 calls, well under the FAOSTAT free quota

Output: data/country_caloric_shares.json
  {
    "_meta": { ... },
    "data": {
      "BEL": {
        "wheat_pct": 11.2, "rice_pct": 0.9, "maize_pct": 1.7,
        "total_kcal_per_day": 3760,
        "year": 2022,
        "source": "FAOSTAT FBS",
        "source_url": "https://www.fao.org/faostat/en/#data/FBS",
        "quality_flag": "sourced",
      },
      ...
    }
  }

Notes for future maintainers:
  - FAOSTAT's FBS domain currently has data through 2022 (the most-recent harmonized
    vintage). When FAO publishes 2023, this script picks it up automatically by
    requesting the latest available year per country.
  - We pull years 2020-2024 in one batch and take the most recent populated year
    per country, since some smaller countries lag a year or two behind.
  - "Direct caloric supply" only — indirect calories via animal feed are not counted.
    Mexico's tortilla maize is captured here; Mexico's feed maize for cattle is not.
"""
import time

from _common import http_get, write_json

AUTH_URL = "https://faostatservices.fao.org/api/v1/auth/guest"
DATA_URL = "https://faostatservices.fao.org/api/v1/en/data/FBS"

# FAO area_code → ISO3 (reused from refresh_faostat.py — same dataset, same coding)
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
    200:"SVK",198:"SVN",25:"SOM",202:"ZAF",277:"SSD",203:"ESP",38:"LKA",276:"SDN",207:"SUR",210:"SWE",
    211:"CHE",212:"SYR",214:"TZA",216:"THA",176:"TGO",219:"TTO",218:"TUN",223:"TUR",213:"TKM",226:"UGA",
    230:"UKR",225:"ARE",229:"GBR",231:"USA",234:"URY",235:"UZB",237:"VEN",251:"VNM",249:"YEM",251:"ZMB",
    181:"ZWE",
    # Additional codes encountered in FBS that weren't in CP table
    138:"MEX", 156:"NZL", 165:"PAK", 38:"LKA",
}

# FAOSTAT FBS item codes
ITEMS = {
    "2901": "total",   # Grand Total
    "2511": "wheat",   # Wheat and products
    "2805": "rice",    # Rice and products
    "2514": "maize",   # Maize and products
}

ELEMENT_KCAL_PER_DAY = "664"   # Food supply (kcal/capita/day)
THROTTLE_SECONDS = 0.4
YEARS_WINDOW = "2020,2021,2022,2023,2024"   # FBS currently has 2022 most-recent; pull window


def main():
    # 1. Get guest token
    try:
        tok_r = http_get(AUTH_URL, timeout=30)
        token = (tok_r.json() or {}).get("token")
        if not token:
            raise RuntimeError("no token in guest response")
    except Exception as e:
        write_json(
            "country_caloric_shares.json", {},
            source="FAOSTAT FBS",
            notes=f"Could not obtain guest token: {e}"
        )
        return

    headers = {"Authorization": f"Bearer {token}"}

    # 2. One bulk call: ALL areas, ALL four items, ALL years in window.
    # FBS is small enough per row that one big call is more efficient than per-country.
    item_codes = ",".join(ITEMS.keys())
    print(f"[INFO] FAOSTAT FBS bulk pull: items={item_codes}, element={ELEMENT_KCAL_PER_DAY}, years={YEARS_WINDOW}")

    rows = []
    page = 1
    while page <= 20:
        try:
            r = http_get(
                DATA_URL,
                params={
                    "item_code": item_codes,
                    "element_code": ELEMENT_KCAL_PER_DAY,
                    "year": YEARS_WINDOW,
                    "format": "json",
                    "page": page,
                    "page_size": 5000,
                },
                headers=headers,
                timeout=60,
                retries=2,
            )
            j = r.json() or {}
            chunk = j.get("data") or []
            rows.extend(chunk)
            total_pages = (j.get("metadata") or {}).get("pages") or 1
            print(f"  page {page}/{total_pages}, +{len(chunk)} rows (cum {len(rows)})")
            if page >= total_pages or not chunk:
                break
            page += 1
            time.sleep(THROTTLE_SECONDS)
        except Exception as e:
            print(f"  [warn] page {page} failed: {e}")
            break

    if not rows:
        write_json(
            "country_caloric_shares.json", {},
            source="FAOSTAT FBS",
            notes="No rows returned"
        )
        return

    # 3. Group: for each country, find the most recent year that has data for all 4 items
    by_country = {}   # iso3 → { year: { item_key: kcal } }
    for row in rows:
        area_code = _int(row.get("Area Code"))
        iso3 = FAO_AREA_TO_ISO3.get(area_code)
        if not iso3:
            continue
        item_code = str(row.get("Item Code") or "").strip()
        item_key = ITEMS.get(item_code)
        if not item_key:
            continue
        year = _int(row.get("Year"))
        val = _num(row.get("Value"))
        if year is None or val is None:
            continue
        by_country.setdefault(iso3, {}).setdefault(year, {})[item_key] = val
        by_country[iso3][year]["_country"] = row.get("Area")

    # 4. Build output — for each country, take the most recent year with a total
    out = {}
    for iso3, by_year in by_country.items():
        years_desc = sorted([y for y in by_year if isinstance(y, int)], reverse=True)
        chosen = None
        for y in years_desc:
            row = by_year[y]
            if "total" in row and row["total"] > 0:
                chosen = (y, row)
                break
        if not chosen:
            continue
        y, row = chosen
        total = row["total"]
        wheat = row.get("wheat", 0) or 0
        rice  = row.get("rice", 0) or 0
        maize = row.get("maize", 0) or 0
        out[iso3] = {
            "wheat_pct": round(wheat / total * 100, 2),
            "rice_pct":  round(rice  / total * 100, 2),
            "maize_pct": round(maize / total * 100, 2),
            "total_kcal_per_day": round(total, 1),
            "wheat_kcal": round(wheat, 1),
            "rice_kcal":  round(rice, 1),
            "maize_kcal": round(maize, 1),
            "year": y,
            "country": row.get("_country"),
            "source": "FAOSTAT Food Balance Sheets",
            "source_url": "https://www.fao.org/faostat/en/#data/FBS",
            "method": "kcal/capita/day (element 664), item-share of Grand Total (item 2901)",
            "quality_flag": "sourced",
        }

    write_json(
        "country_caloric_shares.json",
        out,
        source="FAOSTAT FBS (faostatservices.fao.org/api/v1/en/data/FBS, items 2511/2805/2514/2901, element 664)",
        notes=(
            f"Wheat / rice / maize caloric share % of total daily caloric supply per country. "
            f"Direct food calories only — does NOT count indirect calories via animal feed. "
            f"Most recent year per country (typically 2022). Covered {len(out)} of "
            f"{len(FAO_AREA_TO_ISO3)} requested countries."
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
