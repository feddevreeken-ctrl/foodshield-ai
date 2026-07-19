"""
OpenAQ — global air quality (PM2.5) nowcast.

Requires a free API key. Register at https://api.openaq.org/register and set:
  OPENAQ_API_KEY in GitHub repository secrets.

ARCHITECTURE NOTE (v43, 18 Jul 2026) — WHY THIS SHIPPED ZERO ROWS FOR MONTHS:
  This looked like an auth failure and was not. The key was valid and 5,000
  locations came back fine — the payload note even said so ("Covered 0
  countries from 5000 stations sampled"). The bug was a schema misread.

  Verified against the live spec (api.openaq.org/openapi.json, version 3.0.0):
  `/v3/locations` types its `sensors[]` entries as **SensorBase**, which is
  `{id, name, parameter}` and has NO `latest` key. `latest` exists only on the
  full **Sensor** schema. So `sensor["latest"]["value"]` yielded nothing on
  every row, and the feed reported "0 countries" without ever raising.

  Latest values must come from `/v3/parameters/{id}/latest`, whose `Latest`
  objects are `{datetime, value, coordinates, sensorsId, locationsId}` — note
  there is NO country field, so a join back to /v3/locations on
  `locationsId -> Location.id` is mandatory. Hence the two-phase design.

  Also fixed here: `limit` was 100 against a documented max of 1000, so this
  now issues ~10x fewer requests. And `datetime_min` is now sent — without it,
  a sensor whose "latest" reading is two years old is ingested as current and
  quietly poisons the country mean.

  Aggregation changed from mean to MEDIAN. PM2.5 is heavy-tailed; one sensor
  parked next to a fire skewed a whole country's background reading.

Endpoints (v3; v1/v2 retired 2025-01-31, they return HTTP 410):
  Phase A  GET /v3/locations?parameters_id=2&limit=1000&page=N
  Phase B  GET /v3/parameters/2/latest?limit=1000&page=N&datetime_min=...

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
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from _common import http_get, write_json, env

LOCATIONS_URL = "https://api.openaq.org/v3/locations"
LATEST_URL = "https://api.openaq.org/v3/parameters/2/latest"   # 2 = PM2.5
PAGE_LIMIT = 1000          # documented max; default is 100
MAX_PAGES = 60             # backstop against a pagination bug looping forever
FRESH_HOURS = 48           # ignore "latest" readings older than this


def _paginate(url, headers, params, label, stats):
    """Yield result rows from a v3 endpoint.

    Terminates on a short page rather than on meta.found: the spec types
    `found` as integer|string|null (OpenAQ has emitted ">1000" style values),
    so it is not safe to int() it.

    TRUNCATION IS REPORTED, NOT SWALLOWED. `stats` is a caller-owned dict that
    this records completeness into. The first version simply `return`ed on a
    mid-pagination failure, so a run that died on page 3 of 12 produced a
    payload the caller then described with confident counts — indistinguishable
    from a complete fetch. On a project whose whole claim is that it reports
    its own breakage, a partial pull presented as a full one is the worst
    possible failure mode, not a minor one.
    """
    page = 1
    while page <= MAX_PAGES:
        q = dict(params, limit=PAGE_LIMIT, page=page)
        try:
            r = http_get(url, params=q, headers=headers, timeout=45, retries=2)
        except Exception as e:
            print(f"  [ERROR] {label} page {page} failed: {e} — result set is PARTIAL")
            stats["complete"] = False
            stats["failed_page"] = page
            stats["error"] = str(e)[:200]
            return
        rows = (r.json() or {}).get("results") or []
        if not rows:
            return
        for row in rows:
            yield row
        if len(rows) < PAGE_LIMIT:
            return
        page += 1
    print(f"  [ERROR] {label} hit the {MAX_PAGES}-page backstop — result set is PARTIAL")
    stats["complete"] = False
    stats["failed_page"] = MAX_PAGES
    stats["error"] = f"hit {MAX_PAGES}-page backstop"


def main():
    key = env("OPENAQ_API_KEY", required=False)
    if not key:
        write_json("openaq.json", {}, source="OpenAQ", status="empty",
                   notes="OPENAQ_API_KEY not configured")
        return

    headers = {"X-API-Key": key, "Accept": "application/json"}

    # ── Phase A: locationsId -> ISO3. The latest endpoint carries no country. ──
    loc_stats = {"complete": True}
    loc_country = {}
    for loc in _paginate(LOCATIONS_URL, headers, {"parameters_id": 2}, "locations", loc_stats):
        lid = loc.get("id")
        iso2 = ((loc.get("country") or {}).get("code") or "").upper()
        if lid is None or len(iso2) != 2:
            continue
        iso3 = _alpha2_to_alpha3(iso2)
        if iso3:
            loc_country[lid] = iso3
    print(f"[INFO] Phase A: mapped {len(loc_country)} PM2.5 locations to countries")

    if not loc_country:
        write_json(
            "openaq.json", {}, source="OpenAQ v3", status="empty",
            notes=("Phase A returned no usable locations. Either the key was rejected "
                   "or /v3/locations changed shape — check country.code is still ISO "
                   "alpha-2 on the Location schema."),
        )
        return

    # ── Phase B: latest PM2.5 readings, joined back on locationsId. ──
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FRESH_HOURS)
    by_country = defaultdict(list)
    seen = unmatched = stale = 0
    latest_stats = {"complete": True}
    for row in _paginate(LATEST_URL, headers,
                         {"datetime_min": cutoff.isoformat()}, "latest", latest_stats):
        seen += 1
        v = row.get("value")
        if not isinstance(v, (int, float)) or v < 0:
            continue
        iso3 = loc_country.get(row.get("locationsId"))
        if not iso3:
            # A location created between phase A and phase B. Expected at
            # trace levels; a large count means something else is wrong.
            unmatched += 1
            continue
        ts = ((row.get("datetime") or {}).get("utc")) or ""
        if ts:
            try:
                if datetime.fromisoformat(ts.replace("Z", "+00:00")) < cutoff:
                    stale += 1
                    continue
            except ValueError:
                pass
        by_country[iso3].append(float(v))

    print(f"[INFO] Phase B: {seen} latest rows, {unmatched} unmatched, {stale} stale")
    if seen and unmatched / seen > 0.05:
        print(f"  [warn] {unmatched/seen:.1%} of latest rows had no location match "
              f"— the join key may have changed")

    # Median, not mean: PM2.5 is heavy-tailed and a single sensor beside a fire
    # used to drag a whole country's background figure with it.
    out = {}
    for iso3, vals in by_country.items():
        if not vals:
            continue
        med = statistics.median(vals)
        out[iso3] = {
            "pm25_latest": round(med, 1),
            "stations_reporting": len(vals),
            "pm25_flag": bool(med > 35),
        }

    flagged = sum(1 for r in out.values() if r["pm25_flag"])
    complete = loc_stats.get("complete", True) and latest_stats.get("complete", True)
    print(f"[INFO] Compiled PM2.5 for {len(out)} countries ({flagged} above 35 µg/m³); "
          f"fetch {'complete' if complete else 'PARTIAL'}")

    partial_note = ""
    if not complete:
        which = []
        if not loc_stats.get("complete", True):
            which.append(f"locations (page {loc_stats.get('failed_page')}: "
                         f"{loc_stats.get('error')})")
        if not latest_stats.get("complete", True):
            which.append(f"latest (page {latest_stats.get('failed_page')}: "
                         f"{latest_stats.get('error')})")
        partial_note = (
            f" PARTIAL FETCH — pagination failed on {'; '.join(which)}. The country "
            f"list below is INCOMPLETE and the per-country medians are computed from "
            f"fewer stations than a healthy run. Do not read a country's absence as "
            f"an absence of pollution."
        )

    write_json(
        "openaq.json",
        out,
        source="OpenAQ v3 (parameters/2/latest joined to locations)",
        # A partial pull is NOT "ok". Downstream reads this field, and a
        # truncated run that reports ok is exactly the silent degradation this
        # feed already suffered once.
        status=("ok" if complete else "degraded_partial") if out else "empty",
        notes=(
            f"Country-level MEDIAN of latest PM2.5 station readings, restricted to "
            f"readings from the last {FRESH_HOURS}h. "
            f"pm25_flag = country median above WHO interim target-2 (35 µg/m³). "
            f"Covered {len(out)} countries from {seen} sensor readings across "
            f"{len(loc_country)} mapped locations; {flagged} flagged."
            + partial_note
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
