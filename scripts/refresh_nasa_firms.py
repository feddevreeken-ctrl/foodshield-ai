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

    out = {}
    for iso3 in FIRE_COUNTRIES:
        try:
            r = http_get(URL_TPL.format(key=key, c=iso3), timeout=30, retries=2,
                         headers={"Accept": "text/csv"})
            txt = r.text
            if "Invalid" in txt[:200] or len(txt) < 50:
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
            print(f"  [warn] FIRMS {iso3} skipped: {e}")
            continue

    write_json(
        "nasa_firms.json",
        out,
        source="NASA FIRMS VIIRS NRT (firms.modaps.eosdis.nasa.gov)",
        notes=(
            "Total active fire detections last 7 days per country. "
            "fire_flag = >2x baseline. "
            f"Covered {len(out)} of {len(FIRE_COUNTRIES)} requested countries."
        ),
    )


if __name__ == "__main__":
    main()
