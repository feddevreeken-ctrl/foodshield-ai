"""
World Bank WDI — food inflation, GDP per capita, undernourishment, food expenditure share.

Public REST API, no key required.

Indicators pulled (per Blueprint section 6.3):
  FP.CPI.TOTL.ZG          Food inflation proxy (CPI annual %)
  NY.GDP.PCAP.CD          GDP per capita (USD)
  SN.ITK.DEFC.ZS          Prevalence of undernourishment (% of pop) [FAO via WB]
  AG.PRD.FOOD.XD          Food production index
  SI.POV.NAHC             Poverty headcount ratio (national)

Output: data/worldbank_wdi.json
  {
    iso3: {indicator_code: {"value": ..., "year": ...}}
  }
"""
from _common import http_get, write_json

INDICATORS = [
    "FP.CPI.TOTL.ZG",
    "NY.GDP.PCAP.CD",
    "SN.ITK.DEFC.ZS",
    "AG.PRD.FOOD.XD",
    "SI.POV.NAHC",
]
BASE = "https://api.worldbank.org/v2/country/all/indicator/{ind}"


def fetch_indicator(ind):
    """World Bank API returns paginated; mrv=1 gives most recent value per country."""
    # patient=True so a single WB API hiccup doesn't tank the indicator (run #17, May 21 2026 — 5 of 7 critical-fail bulk feeds were WB-family).
    r = http_get(BASE.format(ind=ind), params={"format": "json", "mrv": 1, "per_page": 400}, timeout=45, patient=True)
    data = r.json()
    if not isinstance(data, list) or len(data) < 2:
        return []
    return data[1] or []


def main():
    out = {}
    for ind in INDICATORS:
        print(f"  fetching {ind}…")
        rows = fetch_indicator(ind)
        for row in rows:
            iso3 = (row.get("countryiso3code") or "").upper()
            val = row.get("value")
            year = row.get("date")
            if not iso3 or val is None:
                continue
            out.setdefault(iso3, {})[ind] = {"value": _num(val), "year": _safe_int(year)}

    write_json(
        "worldbank_wdi.json",
        out,
        source="World Bank WDI (api.worldbank.org)",
        notes="Most-recent-value per indicator per country. Indicator codes per WDI metadata.",
    )


def _num(v):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
