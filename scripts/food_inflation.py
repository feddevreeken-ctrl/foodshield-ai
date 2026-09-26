"""One food-inflation reading per country, in the same order the page uses.

The country panel (index.html calibrationAnchors) and the structural score used to
pick from different chains: the panel showed RTFP > Eurostat > IMF > FAOSTAT monthly,
while the score took a "WFP per-country" field that had been empty since HungerMap
went behind a login and then FAOSTAT's partial-year mean (a 3-month average against
a full prior year). A country could show one figure and be scored on another.

Order, first available wins:
  1. World Bank RTFP: monitored markets, monthly, dated.
  2. Eurostat food HICP (EU/EEA), monthly.
  3. IMF food CPI, only when fresh (< 400 days) and no older than FAOSTAT's month.
  4. FAOSTAT food CPI, latest month against the same month a year earlier, fresh.
  5. FAOSTAT annual mean, only for a complete year no older than last year.
All-items CPI is not food inflation and is not used here.
"""
from datetime import date

FI_MIN, FI_MAX = -50.0, 1000.0


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and FI_MIN <= f <= FI_MAX else None


def _fresh(month, today=None):
    try:
        y, m = int(str(month)[:4]), int(str(month)[5:7])
    except (TypeError, ValueError):
        return False
    today = today or date.today()
    return (today - date(y, m, 1)).days < 400


def pick(iso, rtfp, eurostat, imf, faostat, today=None):
    """-> {"value", "source", "as_of", "label"} or None."""
    rt = rtfp.get(iso) or {}
    v = _num(rt.get("food_inflation_pct"))
    if v is not None:
        return {"value": round(v, 1), "source": "World Bank Real-Time Food Prices (RTFP) via HDX",
                "as_of": rt.get("as_of"), "label": "food inflation, monitored markets",
                "markets": rt.get("markets"), "confidence": rt.get("confidence")}
    es = eurostat.get(iso) or {}
    v = _num(es.get("food_hicp_yoy_pct"))
    if v is not None:
        return {"value": round(v, 1), "source": "Eurostat food HICP (yoy %)",
                "as_of": es.get("month"), "label": "food HICP YoY"}
    fs = faostat.get(iso) or {}
    im = imf.get(iso) or {}
    v = _num(im.get("food_cpi_yoy_pct"))
    if v is not None and _fresh(im.get("month"), today) and str(im.get("month")) >= str(fs.get("food_cpi_latest_month") or ""):
        return {"value": round(v, 1), "source": "IMF CPI, food and non-alcoholic beverages (yoy %)",
                "as_of": im.get("month"), "label": "food CPI YoY"}
    v = _num(fs.get("food_cpi_yoy_month_pct"))
    if v is not None and v != 0 and _fresh(fs.get("food_cpi_latest_month"), today):
        return {"value": round(v, 1), "source": "FAOSTAT Consumer Price Indices (food CPI, month yoy)",
                "as_of": fs.get("food_cpi_latest_month"), "label": "food CPI YoY"}
    v = _num(fs.get("food_cpi_yoy_pct"))
    months = fs.get("months_in_latest_year")
    complete = not (isinstance(months, int) and 0 < months < 12)
    if v is not None and complete and (fs.get("year_latest") or 0) >= (today or date.today()).year - 1:
        return {"value": round(v, 1), "source": "FAOSTAT Consumer Price Indices (food CPI, annual mean)",
                "as_of": str(fs.get("year_latest")), "label": "food CPI, annual mean"}
    return None
