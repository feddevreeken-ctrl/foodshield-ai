"""
OpenAQ — global air quality (PM2.5) nowcast.

Requires a free API key. Register at https://api.openaq.org/register and set:
  OPENAQ_API_KEY in GitHub repository secrets.

Endpoint: https://api.openaq.org/v3/locations (v3 schema)

PM2.5 is a leading indicator of agricultural labour productivity and
photosynthesis stress in heavy-haze episodes. We pull country-level
latest PM2.5 readings; countries with persistent >35 µg/m³ background
get an air-quality flag.

Output: data/openaq.json
  {
    iso3: {
      "pm25_latest": <µg/m³>,
      "stations_reporting": <int>,
      "pm25_flag": <bool>,    # true when latest > 35 µg/m³
    }
  }

If OPENAQ_API_KEY is unset the script writes an empty stub instead of failing.
"""
from collections import defaultdict
from _common import http_get, write_json, env

URL = "https://api.openaq.org/v3/locations"


def main():
    key = env("OPENAQ_API_KEY", required=False)
    if not key:
        write_json("openaq.json", {}, source="OpenAQ", notes="OPENAQ_API_KEY not configured")
        return

    headers = {"X-API-Key": key}
    out_pm = defaultdict(list)

    # Pull up to ~5000 PM2.5-capable stations globally (paginated)
    # parameter id 2 = PM2.5 in OpenAQ v3
    page = 1
    fetched = 0
    while page <= 50:  # 50 pages × 100 = max 5000 stations
        try:
            r = http_get(URL, params={
                "parameters_id": 2,
                "limit": 100,
                "page": page,
            }, headers=headers, timeout=25, retries=2)
        except Exception as e:
            print(f"  [warn] OpenAQ page {page} failed: {e}")
            break
        rows = (r.json() or {}).get("results") or []
        if not rows:
            break
        for loc in rows:
            iso = ((loc.get("country") or {}).get("code") or "").upper()
            if not iso or len(iso) != 2:
                continue
            iso3 = _alpha2_to_alpha3(iso)
            if not iso3:
                continue
            # Each location has its latest measurement under "sensors"
            sensors = loc.get("sensors") or []
            for s in sensors:
                if (s.get("parameter") or {}).get("id") != 2:
                    continue
                val = s.get("latest") or {}
                v = val.get("value")
                if isinstance(v, (int, float)) and v >= 0:
                    out_pm[iso3].append(v)
        fetched += len(rows)
        if len(rows) < 100:
            break
        page += 1

    out = {}
    for iso3, vals in out_pm.items():
        if not vals:
            continue
        latest = sum(vals) / len(vals)
        out[iso3] = {
            "pm25_latest": round(latest, 1),
            "stations_reporting": len(vals),
            "pm25_flag": bool(latest > 35),
        }

    write_json(
        "openaq.json",
        out,
        source="OpenAQ v3 (api.openaq.org/v3/locations)",
        notes=(
            "Country-level mean of latest PM2.5 station readings. "
            "pm25_flag = country-mean above WHO interim target-2 (35 µg/m³). "
            f"Covered {len(out)} countries from {fetched} stations sampled."
        ),
    )


# Minimal ISO alpha2→alpha3 for OpenAQ — only the entries that show up in
# country.code from their v3 schema. Anything missing is dropped silently.
_A2_TO_A3 = {
    "AF":"AFG","AL":"ALB","DZ":"DZA","AS":"ASM","AD":"AND","AO":"AGO","AG":"ATG","AR":"ARG",
    "AM":"ARM","AU":"AUS","AT":"AUT","AZ":"AZE","BS":"BHS","BH":"BHR","BD":"BGD","BB":"BRB",
    "BY":"BLR","BE":"BEL","BZ":"BLZ","BJ":"BEN","BT":"BTN","BO":"BOL","BA":"BIH","BW":"BWA",
    "BR":"BRA","BN":"BRN","BG":"BGR","BF":"BFA","BI":"BDI","CV":"CPV","KH":"KHM","CM":"CMR",
    "CA":"CAN","CF":"CAF","TD":"TCD","CL":"CHL","CN":"CHN","CO":"COL","KM":"COM","CG":"COG",
    "CD":"COD","CR":"CRI","CI":"CIV","HR":"HRV","CU":"CUB","CY":"CYP","CZ":"CZE","DK":"DNK",
    "DJ":"DJI","DM":"DMA","DO":"DOM","EC":"ECU","EG":"EGY","SV":"SLV","GQ":"GNQ","ER":"ERI",
    "EE":"EST","SZ":"SWZ","ET":"ETH","FJ":"FJI","FI":"FIN","FR":"FRA","GA":"GAB","GM":"GMB",
    "GE":"GEO","DE":"DEU","GH":"GHA","GR":"GRC","GD":"GRD","GT":"GTM","GN":"GIN","GW":"GNB",
    "GY":"GUY","HT":"HTI","HN":"HND","HU":"HUN","IS":"ISL","IN":"IND","ID":"IDN","IR":"IRN",
    "IQ":"IRQ","IE":"IRL","IL":"ISR","IT":"ITA","JM":"JAM","JP":"JPN","JO":"JOR","KZ":"KAZ",
    "KE":"KEN","KI":"KIR","KP":"PRK","KR":"KOR","KW":"KWT","KG":"KGZ","LA":"LAO","LV":"LVA",
    "LB":"LBN","LS":"LSO","LR":"LBR","LY":"LBY","LI":"LIE","LT":"LTU","LU":"LUX","MG":"MDG",
    "MW":"MWI","MY":"MYS","MV":"MDV","ML":"MLI","MT":"MLT","MH":"MHL","MR":"MRT","MU":"MUS",
    "MX":"MEX","FM":"FSM","MD":"MDA","MC":"MCO","MN":"MNG","ME":"MNE","MA":"MAR","MZ":"MOZ",
    "MM":"MMR","NA":"NAM","NR":"NRU","NP":"NPL","NL":"NLD","NZ":"NZL","NI":"NIC","NE":"NER",
    "NG":"NGA","MK":"MKD","NO":"NOR","OM":"OMN","PK":"PAK","PW":"PLW","PS":"PSE","PA":"PAN",
    "PG":"PNG","PY":"PRY","PE":"PER","PH":"PHL","PL":"POL","PT":"PRT","QA":"QAT","RO":"ROU",
    "RU":"RUS","RW":"RWA","KN":"KNA","LC":"LCA","VC":"VCT","WS":"WSM","SM":"SMR","ST":"STP",
    "SA":"SAU","SN":"SEN","RS":"SRB","SC":"SYC","SL":"SLE","SG":"SGP","SK":"SVK","SI":"SVN",
    "SB":"SLB","SO":"SOM","ZA":"ZAF","SS":"SSD","ES":"ESP","LK":"LKA","SD":"SDN","SR":"SUR",
    "SE":"SWE","CH":"CHE","SY":"SYR","TW":"TWN","TJ":"TJK","TZ":"TZA","TH":"THA","TL":"TLS",
    "TG":"TGO","TO":"TON","TT":"TTO","TN":"TUN","TR":"TUR","TM":"TKM","TV":"TUV","UG":"UGA",
    "UA":"UKR","AE":"ARE","GB":"GBR","US":"USA","UY":"URY","UZ":"UZB","VU":"VUT","VE":"VEN",
    "VN":"VNM","YE":"YEM","ZM":"ZMB","ZW":"ZWE",
}


def _alpha2_to_alpha3(a2):
    return _A2_TO_A3.get(a2.upper())


if __name__ == "__main__":
    main()
