"""
Open-Meteo Flood API — river-discharge anomalies on major rivers in flood-prone countries.

No API key required.
Endpoint: https://flood-api.open-meteo.com/v1/flood

For each gauge point, fetches past-30-day daily river discharge (m³/s) and computes:
  - discharge_7d_mean
  - discharge_anomaly_pct (7d vs 30d)
  - flood_flag (anomaly > +75%)

Output: data/openmeteo_flood.json
  {
    iso3: {
      "river": <name>,
      "discharge_7d_m3s": <mean>,
      "discharge_anomaly_pct": <%>,
      "flood_flag": <bool>,
    }
  }

Coverage: 30 flood-vulnerable countries chosen for food-system exposure.
"""
from _common import http_get, write_json
import time

URL = "https://flood-api.open-meteo.com/v1/flood"

# (iso3, river_name, lat, lon) — coordinates near major downstream gauging points
RIVER_GAUGES = [
    ("BGD", "Ganges/Padma",   23.85, 89.95),
    ("PAK", "Indus",           25.40, 68.30),
    ("IND", "Ganges (Patna)",  25.60, 85.10),
    ("MMR", "Irrawaddy",       21.95, 96.05),
    ("VNM", "Mekong delta",    10.45, 106.00),
    ("KHM", "Mekong (Phnom Penh)", 11.55, 104.92),
    ("LAO", "Mekong (Vientiane)",  17.97, 102.60),
    ("THA", "Chao Phraya",     13.90, 100.55),
    ("CHN", "Yangtze (Wuhan)", 30.58, 114.30),
    ("SDN", "Nile (Khartoum)", 15.60, 32.55),
    ("EGY", "Nile (Cairo)",    30.05, 31.25),
    ("ETH", "Blue Nile (Bahir Dar)", 11.59, 37.39),
    ("SSD", "White Nile (Juba)",     4.85, 31.58),
    ("MOZ", "Zambezi",         -17.85, 35.97),
    ("ZMB", "Zambezi (Victoria Falls)", -17.92, 25.85),
    ("NGA", "Niger (Niamey downstream)", 9.10, 5.30),
    ("NER", "Niger (Niamey)",  13.51, 2.11),
    ("MLI", "Niger (Mopti)",   14.50, -4.20),
    ("USA", "Mississippi (Baton Rouge)", 30.45, -91.18),
    ("BRA", "Amazon (Manaus)", -3.10, -60.00),
    ("PER", "Amazon (Iquitos)", -3.75, -73.25),
    ("COL", "Magdalena",       4.50, -74.90),
    ("ARG", "Paraná",          -27.45, -58.83),
    ("PRY", "Paraguay",        -25.30, -57.58),
    ("VEN", "Orinoco",         8.62, -62.69),
    ("HTI", "Artibonite",      19.12, -72.55),
    ("PHL", "Cagayan",         17.62, 121.72),
    ("IDN", "Solo",            -7.55, 112.00),
    ("MWI", "Shire",           -16.05, 35.13),
    ("MDG", "Mangoky",         -21.42, 43.66),
]


def main():
    out = {}
    for iso3, river, lat, lon in RIVER_GAUGES:
        try:
            r = http_get(URL, params={
                "latitude": lat,
                "longitude": lon,
                "daily": "river_discharge",
                "past_days": 30,
                "forecast_days": 1,
            }, timeout=20, retries=2)
            d = (r.json().get("daily") or {}).get("river_discharge") or []
            vals = [v for v in d if isinstance(v, (int, float))]
            if len(vals) < 14:
                continue
            d7 = sum(vals[-7:]) / 7
            d30 = sum(vals[-30:]) / min(30, len(vals))
            anom = round((d7 - d30) / d30 * 100, 1) if d30 > 0 else None
            out[iso3] = {
                "river": river,
                "discharge_7d_m3s": round(d7, 1),
                "discharge_anomaly_pct": anom,
                "flood_flag": bool(anom is not None and anom > 75),
            }
        except Exception as e:
            print(f"  [warn] {iso3}/{river} skipped: {e}")
        time.sleep(0.2)

    write_json(
        "openmeteo_flood.json",
        out,
        source="Open-Meteo Flood API (flood-api.open-meteo.com/v1/flood)",
        notes=(
            "River discharge anomalies for 30 flood-exposed countries. "
            "flood_flag = 7d mean discharge >+75% vs 30d baseline."
        ),
    )


if __name__ == "__main__":
    main()
