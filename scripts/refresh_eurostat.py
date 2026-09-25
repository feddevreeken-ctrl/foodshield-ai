"""
Eurostat — EU 27 food price index (HICP) monthly annual rate of change.

No API key required.
Endpoint: https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data

Dataset: prc_hicp_minr (HICP - ECOICOP ver.2 - indices and rates of change, monthly),
  filtered to unit=RCH_A (annual rate of change) and coicop18=CP011 ("Food").
  Falls back to coicop18=CP01 ("Food and non-alcoholic beverages") only if CP011
  returns no data; the series actually used is recorded per row and in _meta.

  prc_hicp_manr (the ECOICOP v1 table this collector used to read) was
  discontinued after the 2025-12 release — its title became "(1997-2025)" — so it
  froze every EU food-inflation reading at 2025-12. prc_hicp_minr is Eurostat's
  live successor. Note: CP011 is "Food" only; the old docstring mislabelled it as
  "Food and non-alcoholic beverages", which is CP01.

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
      "coicop": "CP011" | "CP01",   # which ECOICOP v2 series the value is
    }
  }

Coverage: 27 EU member states + UK (kept for continuity though no longer EU) + Norway/Switzerland.
"""
from _common import http_get, write_json

DATASET = "prc_hicp_minr"
URL = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
       + DATASET + "?format=JSON&lang=EN&unit=RCH_A&coicop18={coicop}&lastTimePeriod=3")
# Preferred series first. CP011 = "Food"; CP01 = "Food and non-alcoholic beverages".
SERIES = [("CP011", "Food"), ("CP01", "Food and non-alcoholic beverages")]

# Eurostat uses 2-letter codes (mostly ISO 3166-1 alpha-2, with EL=Greece, UK=United Kingdom)
A2_TO_A3 = {
    "AT":"AUT","BE":"BEL","BG":"BGR","CY":"CYP","CZ":"CZE","DE":"DEU","DK":"DNK","EE":"EST",
    "EL":"GRC","ES":"ESP","FI":"FIN","FR":"FRA","HR":"HRV","HU":"HUN","IE":"IRL","IT":"ITA",
    "LT":"LTU","LU":"LUX","LV":"LVA","MT":"MLT","NL":"NLD","PL":"POL","PT":"PRT","RO":"ROU",
    "SE":"SWE","SI":"SVN","SK":"SVK",
    "UK":"GBR","NO":"NOR","CH":"CHE","IS":"ISL",
}

INFLATION_THRESHOLD = 8.0  # %; lower than the 15% used globally because EU baseline is lower


def _latest_by_geo(j):
    """Latest non-null value per geo from a JSON-stat 2.0 response.

    Positions are computed from the response's own `id`/`size` arrays rather than an
    assumed dimension order, so a reordered or widened response cannot silently
    misalign values. Every non-geo/non-time dimension must be filtered to one value.
    """
    ids = j.get("id") or []
    sizes = j.get("size") or []
    dims = j.get("dimension") or {}
    if "geo" not in ids or "time" not in ids:
        raise ValueError(f"{DATASET}: response lacks geo/time dimensions ({ids})")
    for d, n in zip(ids, sizes):
        if d not in ("geo", "time") and n != 1:
            raise ValueError(f"{DATASET}: dimension {d} not filtered to one value (size {n})")
    strides = {}
    stride = 1
    for d, n in reversed(list(zip(ids, sizes))):
        strides[d] = stride
        stride *= n
    geo_idx = dims["geo"]["category"]["index"]
    geo_label = dims["geo"]["category"].get("label") or {}
    time_idx = dims["time"]["category"]["index"]
    times_sorted = sorted(time_idx.items(), key=lambda kv: kv[1], reverse=True)
    values = j.get("value") or {}
    out = {}
    for geo_code, geo_pos in geo_idx.items():
        for tcode, tpos in times_sorted:
            v = values.get(str(geo_pos * strides["geo"] + tpos * strides["time"]))
            if isinstance(v, (int, float)):
                out[geo_code] = (v, tcode, geo_label.get(geo_code, geo_code))
                break
    return out


def main():
    latest, coicop, coicop_label = {}, None, None
    for code, label in SERIES:
        latest = _latest_by_geo(http_get(URL.format(coicop=code), timeout=45).json())
        if latest:
            coicop, coicop_label = code, label
            break
    if not latest:
        raise RuntimeError(f"{DATASET}: no food HICP values for {', '.join(c for c, _ in SERIES)}")

    out = {}
    for geo_code, (val, month, name) in latest.items():
        iso3 = A2_TO_A3.get(geo_code)
        if not iso3:
            continue
        out[iso3] = {
            "food_hicp_yoy_pct": round(val, 2),
            "month": month,
            "country": name,
            "inflation_shock": bool(val > INFLATION_THRESHOLD),
            "coicop": coicop,
        }

    months = sorted({r["month"] for r in out.values()})
    write_json(
        "eurostat_food.json",
        out,
        source=f"Eurostat HICP (ec.europa.eu/eurostat/api · {DATASET}, ECOICOP v2, RCH_A, {coicop} {coicop_label})",
        notes=(
            f"Annual rate of change in the HICP '{coicop_label}' index ({coicop}, ECOICOP v2) per "
            f"EU/EEA country. Replaces prc_hicp_manr, discontinued after 2025-12. "
            f"inflation_shock = food HICP > {INFLATION_THRESHOLD}% (lower EU threshold). "
            f"Covered {len(out)} countries; latest month(s): {', '.join(months[-2:])}."
        ),
    )


if __name__ == "__main__":
    main()
