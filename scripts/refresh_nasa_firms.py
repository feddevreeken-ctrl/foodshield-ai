"""
NASA FIRMS — active fire detection (VIIRS / MODIS) per country, last 7 days.

Requires a free MAP_KEY. Register at https://firms.modaps.eosdis.nasa.gov/api/
Set NASA_FIRMS_MAP_KEY in GitHub repository secrets.

Endpoint: https://firms.modaps.eosdis.nasa.gov/api/country/csv/{MAP_KEY}/VIIRS_SNPP_NRT/{country}/7

We don't filter to cropland here — that would need a land-cover overlay.
Instead we report total fire detections last 7 days per country, which is a
strong proxy for harvest-loss risk in fire-prone regions (Brazil cerrado, SSA
savannah, Indonesia, Australia). Step-changes vs the country's own baseline
matter more than absolute count.

Output: data/nasa_firms.json
  {
    iso3: {
      "fires_7d": <int>,
      "fires_high_confidence": <int>,    # confidence==h
      "fire_flag": <bool>                # >2x typical for that country
    }
  }

If NASA_FIRMS_MAP_KEY is unset the script writes an empty stub instead of failing.
"""
import csv
import io

from _common import http_get, write_json, env

URL_TPL = "https://firms.modaps.eosdis.nasa.gov/api/country/csv/{key}/VIIRS_SNPP_NRT/{c}/7"

# Country codes per FIRMS — alpha-3 ISO. We restrict to fire-prone food-producers
# to stay within rate limits (5000 lines/request, 1000 requests/10min on free tier).
FIRE_COUNTRIES = [
    "BRA","COL","VEN","BOL","PRY","PER","ARG",       # LATAM cerrado/amazon
    "AGO","COD","ZMB","MOZ","TZA","KEN","UGA","SSD","SDN","ETH","SOM",  # SSA
    "NGA","CMR","CIV","GHA","BFA","MLI","SEN",       # West Africa
    "IDN","MYS","PHL","KHM","MMR","LAO","THA","VNM", # SE Asia
    "AUS","ZAF","MWI","ZWE","MDG",                   # Australia + Southern Africa
    "USA","CAN","RUS","KAZ","UKR",                   # Northern crop belts
    "IND","PAK","NPL","BGD",                         # South Asia post-harvest burning
]

# Country baselines (typical 7d fire count) — used to compute fire_flag.
# These are crude long-term medians; recalibrate annually.
BASELINES_7D = {
    "BRA": 8000, "COL": 1500, "VEN": 800, "BOL": 2500, "PRY": 1200, "PER": 1000, "ARG": 700,
    "AGO": 3000, "COD": 5000, "ZMB": 1500, "MOZ": 3000, "TZA": 4000, "KEN": 400, "UGA": 600,
    "SSD": 1200, "SDN": 2000, "ETH": 700, "SOM": 200,
    "NGA": 1500, "CMR": 1500, "CIV": 800, "GHA": 700, "BFA": 1500, "MLI": 1000, "SEN": 500,
    "IDN": 1500, "MYS": 300, "PHL": 200, "KHM": 800, "MMR": 1500, "LAO": 1000, "THA": 600, "VNM": 400,
    "AUS": 1500, "ZAF": 1500, "MWI": 800, "ZWE": 700, "MDG": 600,
    "USA": 1500, "CAN": 500, "RUS": 1500, "KAZ": 400, "UKR": 200,
    "IND": 2500, "PAK": 600, "NPL": 200, "BGD": 200,
}


def main():
    key = env("NASA_FIRMS_MAP_KEY", required=False)
    if not key:
        write_json("nasa_firms.json", {}, source="NASA FIRMS", notes="NASA_FIRMS_MAP_KEY not configured")
        return

    # v43 — the previous loop treated "your MAP_KEY is invalid" and "this
    # country genuinely had no fires" as the same event: a bare `continue`.
    # The payload then read "Covered 0 of 47 requested countries", which is
    # indistinguishable from a quiet upstream outage — and was in fact
    # misdiagnosed as one for months. The key had simply expired.
    #
    # FIRMS answers an invalid key with a short body containing "Invalid API
    # call.", so we detect that explicitly, stop hammering the endpoint with a
    # key we already know is rejected, and write a payload naming the cause.
    out = {}
    rejected = 0
    empty_but_ok = 0
    for iso3 in FIRE_COUNTRIES:
        try:
            r = http_get(URL_TPL.format(key=key, c=iso3), timeout=30, retries=2,
                         headers={"Accept": "text/csv"})
            txt = r.text
            low = txt[:200].lower()
            if "invalid" in low or "unauthor" in low or "expired" in low:
                rejected += 1
                if rejected >= 3:
                    # Three rejections in a row is the key, not the countries.
                    print(f"  [ERROR] FIRMS rejected the MAP_KEY on {rejected} countries. "
                          f"Aborting rather than issuing "
                          f"{len(FIRE_COUNTRIES) - rejected} more doomed requests.")
                    write_json(
                        "nasa_firms.json", {},
                        source="NASA FIRMS VIIRS NRT (firms.modaps.eosdis.nasa.gov)",
                        status="auth_failed",
                        notes=(
                            "NASA_FIRMS_MAP_KEY is set but REJECTED by FIRMS — the response "
                            "body was an 'Invalid API call' error, not an empty result set. "
                            "That means an expired or unregistered MAP_KEY, NOT an upstream "
                            "outage and NOT a quiet week for fires. Re-provision at "
                            "https://firms.modaps.eosdis.nasa.gov/api/map_key/ and update "
                            "the NASA_FIRMS_MAP_KEY repository secret."
                        ),
                    )
                    return
                continue
            if len(txt) < 50:
                # Header-only CSV: a real, honest zero for this country.
                empty_but_ok += 1
                continue
            rows = list(csv.DictReader(io.StringIO(txt)))
            total = len(rows)
            high = sum(1 for r2 in rows if (r2.get("confidence") or "").lower() in ("h", "high"))
            base = BASELINES_7D.get(iso3, 500)
            out[iso3] = {
                "fires_7d": total,
                "fires_high_confidence": high,
                "fire_flag": bool(total > 2 * base),
            }
        except Exception as e:
            # FIRMS answers a bad MAP_KEY with HTTP 400 + body "Invalid API
            # call." — verified 18 Jul 2026 for both a malformed key and a
            # well-formed-but-unregistered one. Because that is a 4xx, it
            # raises inside http_get and lands HERE, not in the body check
            # above. Detecting it only in the body check was the mistake that
            # let an expired key masquerade as "no fires this week".
            msg = str(e)
            if any(code in msg for code in ("400", "401", "403")):
                rejected += 1
                if rejected >= 3:
                    print(f"  [ERROR] FIRMS rejected the MAP_KEY on {rejected} countries "
                          f"(HTTP 4xx). Aborting rather than issuing "
                          f"{len(FIRE_COUNTRIES) - rejected} more doomed requests.")
                    write_json(
                        "nasa_firms.json", {},
                        source="NASA FIRMS VIIRS NRT (firms.modaps.eosdis.nasa.gov)",
                        status="auth_failed",
                        notes=(
                            "NASA_FIRMS_MAP_KEY is set but REJECTED by FIRMS (HTTP 4xx, "
                            "body 'Invalid API call.'). That is an expired or unregistered "
                            "MAP_KEY — NOT an upstream outage and NOT a quiet week for "
                            "fires. Re-provision at "
                            "https://firms.modaps.eosdis.nasa.gov/api/map_key/ and update "
                            "the NASA_FIRMS_MAP_KEY repository secret."
                        ),
                    )
                    return
                continue
            print(f"  [warn] FIRMS {iso3} skipped: {e}")
            continue

    write_json(
        "nasa_firms.json",
        out,
        source="NASA FIRMS VIIRS NRT (firms.modaps.eosdis.nasa.gov)",
        status="ok" if out else "empty",
        notes=(
            "Total active fire detections last 7 days per country. "
            "fire_flag = >2x baseline. "
            f"Covered {len(out)} of {len(FIRE_COUNTRIES)} requested countries "
            f"({empty_but_ok} returned a genuine zero, {rejected} were rejected by "
            f"the API). Coverage below ~30 countries with 0 rejections usually means "
            f"an upstream VIIRS outage rather than a key problem."
        ),
    )


if __name__ == "__main__":
    main()
