"""
FAOSTAT — global food consumer-price indices (CP domain).

No API key per se, but the API requires a guest JWT obtained from
  https://faostatservices.fao.org/api/v1/auth/guest

This gives a renewable bearer token. We fetch it once at the start of the run
and use it for all subsequent data calls.

Dataset: CP (Consumer Price Indices).
  - element 23012 = Consumer Prices, Food Indices (2015 = 100)
  - element 23014 = Consumer Prices, General Indices (2015 = 100)
  - month code 7021 = annual average
  - Latest year typically lags by 4-12 months.

We pull the most recent two years per country and compute the YoY change in the
food CPI. This complements:
  - World Bank WDI FP.CPI.TOTL.ZG (which is *all-items*, not food, and lagged 12-18 mo)
  - Eurostat HICP food (which only covers EU)

So FAOSTAT fills the global gap for countries with no HungerMap per-country
inflation graph and outside the EU.

Output: data/faostat_food.json
  {
    iso3: {
      "food_cpi_index_latest": <e.g. 142.3, base 2015=100>,
      "food_cpi_yoy_pct": <% change vs previous year>,
      "year_latest": <year>,
      "country": <name>,
      "inflation_shock": <bool>,    # YoY > 15%
    }
  }
"""
import time

from _common import http_get, write_json

AUTH_URL = "https://faostatservices.fao.org/api/v1/auth/guest"
DATA_URL = "https://faostatservices.fao.org/api/v1/en/data/CP"

# FAOSTAT M49 / FAO area codes → ISO3 for the subset we care about.
# Full table is huge; we restrict to the ~190 countries in COUNTRY_COORDS.
# This is the mapping FAO publishes itself (area_code → ISO3).
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
}


def main():
    # Get guest token
    try:
        tok_r = http_get(AUTH_URL, timeout=20)
        token = (tok_r.json() or {}).get("token")
        if not token:
            raise RuntimeError("no token in guest response")
    except Exception as e:
        write_json("faostat_food.json", {}, source="FAOSTAT",
                   notes=f"Could not obtain guest token: {e}")
        return
    headers = {"Authorization": f"Bearer {token}"}

    # Element 23012 = Consumer Prices, Food Indices (2015=100), annual average (month 7021)
    # Latest year available varies — pull a window of 4 years and use whatever exists.
    params = {
        "element_code": "23012",
        "months_code": "7021",
        "year": "2023,2024,2025,2026",
        "format": "json",
        "page_size": 5000,
    }

    rows = []
    page = 1
    while page <= 5:
        try:
            r = http_get(DATA_URL, params={**params, "page": page}, headers=headers, timeout=45, retries=2)
            j = r.json() or {}
            chunk = j.get("data") or []
            rows.extend(chunk)
            total_pages = (j.get("metadata") or {}).get("pages") or 1
            if page >= total_pages or not chunk:
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  [warn] FAOSTAT page {page} failed: {e}")
            break

    # Group by country, find latest two years
    by_iso = {}
    for row in rows:
        area_code = _int(row.get("Area Code"))
        iso3 = FAO_AREA_TO_ISO3.get(area_code) if area_code is not None else None
        if not iso3:
            continue
        year = _int(row.get("Year"))
        val  = _num(row.get("Value"))
        if year is None or val is None:
            continue
        by_iso.setdefault(iso3, {})[year] = val
        # keep country name too
        by_iso[iso3]["_name"] = row.get("Area")

    out = {}
    for iso3, ys in by_iso.items():
        years_data = sorted([(y, v) for y, v in ys.items() if isinstance(y, int)], reverse=True)
        if not years_data:
            continue
        latest_y, latest_v = years_data[0]
        yoy = None
        if len(years_data) > 1:
            prev_y, prev_v = years_data[1]
            if prev_v and prev_v > 0:
                yoy = round((latest_v - prev_v) / prev_v * 100, 2)
        out[iso3] = {
            "food_cpi_index_latest": round(latest_v, 1),
            "food_cpi_yoy_pct": yoy,
            "year_latest": latest_y,
            "country": ys.get("_name"),
            "inflation_shock": bool(yoy is not None and yoy > 15),
        }

    write_json(
        "faostat_food.json",
        out,
        source="FAOSTAT CP (faostatservices.fao.org/api/v1/en/data/CP, element 23012)",
        notes=(
            "Consumer Prices Food Index (2015=100), annual average. "
            "food_cpi_yoy_pct = % change vs prior year. inflation_shock = >15% YoY. "
            f"Covered {len(out)} countries."
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
