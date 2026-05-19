"""
Eurostat — EU 27 food price index (HICP) monthly annual rate of change.

No API key required.
Endpoint: https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data

Dataset: prc_hicp_manr (HICP - monthly data, annual rate of change)
  filtered to coicop=CP011 (Food and non-alcoholic beverages).

This gives the official EU food-inflation indicator for every EU member state,
month by month, updated mid-month. Strictly better than the WB headline CPI we
were using for EU countries since:
  - WB FP.CPI.TOTL.ZG is *all-items* inflation, not food-specific.
  - WB lags by 6–18 months. Eurostat lags by ~6 weeks.

Output: data/eurostat_food.json
  {
    iso3: {
      "food_hicp_yoy_pct": <% change vs same month last year>,
      "month": "YYYY-MM",
      "country": <name>,
      "inflation_shock": <bool>,    # food_hicp > 8% threshold (EU is lower-baseline)
    }
  }

Coverage: 27 EU member states + UK (kept for continuity though no longer EU) + Norway/Switzerland.
"""
from _common import http_get, write_json

URL = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
       "prc_hicp_manr?format=JSON&lang=EN&coicop=CP011&lastTimePeriod=2")

# Eurostat uses 2-letter codes (mostly ISO 3166-1 alpha-2, with EL=Greece, UK=United Kingdom)
A2_TO_A3 = {
    "AT":"AUT","BE":"BEL","BG":"BGR","CY":"CYP","CZ":"CZE","DE":"DEU","DK":"DNK","EE":"EST",
    "EL":"GRC","ES":"ESP","FI":"FIN","FR":"FRA","HR":"HRV","HU":"HUN","IE":"IRL","IT":"ITA",
    "LT":"LTU","LU":"LUX","LV":"LVA","MT":"MLT","NL":"NLD","PL":"POL","PT":"PRT","RO":"ROU",
    "SE":"SWE","SI":"SVN","SK":"SVK",
    "UK":"GBR","NO":"NOR","CH":"CHE","IS":"ISL",
}

INFLATION_THRESHOLD = 8.0  # %; lower than the 15% used globally because EU baseline is lower


def main():
    r = http_get(URL, timeout=45)
    j = r.json()
    geo_dim   = (j.get("dimension") or {}).get("geo") or {}
    time_dim  = (j.get("dimension") or {}).get("time") or {}
    geo_idx   = (geo_dim.get("category") or {}).get("index") or {}
    geo_label = (geo_dim.get("category") or {}).get("label") or {}
    time_idx  = (time_dim.get("category") or {}).get("index") or {}
    time_label = (time_dim.get("category") or {}).get("label") or {}
    n_time = len(time_idx)
    values = j.get("value") or {}

    # JSON-stat encodes positions in a single dict keyed by a flattened linear index.
    # For prc_hicp_manr the order of dims is freq, unit, coicop, geo, time and we filtered
    # freq/unit/coicop to one value each, so effective shape is (geo, time).
    # Linear position = geo_pos * n_time + time_pos.

    # Pick the most-recent month that has at least one value
    times_sorted = sorted(time_idx.items(), key=lambda kv: kv[1], reverse=True)

    out = {}
    for geo_code, geo_pos in geo_idx.items():
        iso3 = A2_TO_A3.get(geo_code)
        if not iso3:
            continue
        latest_val = None
        latest_month = None
        for tcode, tpos in times_sorted:
            lin = geo_pos * n_time + tpos
            v = values.get(str(lin))
            if isinstance(v, (int, float)):
                latest_val = v
                latest_month = tcode
                break
        if latest_val is None:
            continue
        out[iso3] = {
            "food_hicp_yoy_pct": round(latest_val, 2),
            "month": latest_month,
            "country": geo_label.get(geo_code, iso3),
            "inflation_shock": bool(latest_val > INFLATION_THRESHOLD),
        }

    write_json(
        "eurostat_food.json",
        out,
        source="Eurostat HICP food (ec.europa.eu/eurostat/api · prc_hicp_manr, CP011)",
        notes=(
            "Annual rate of change in food + non-alcoholic beverages CPI per EU member state. "
            f"inflation_shock = food HICP > {INFLATION_THRESHOLD}% (lower EU threshold). "
            f"Covered {len(out)} countries."
        ),
    )


if __name__ == "__main__":
    main()
