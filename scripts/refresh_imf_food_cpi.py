"""
IMF CPI — monthly FOOD consumer-price inflation per country (COICOP CP01).

Source: IMF Statistics Department CPI dataset (IMF.STA:CPI), public SDMX 3.0
API, no key: https://api.imf.org/external/sdmx/3.0/
  key  = COUNTRY . INDEX_TYPE . COICOP_1999 . TYPE_OF_TRANSFORMATION . FREQUENCY
       = *       . CPI        . CP01        . YOY_PCH_PA_PT          . M
  CP01 = "Food and non-alcoholic beverages"; YOY_PCH_PA_PT = IMF's own
  year-over-year % change of the period-average index (we do not compute it).
Terms: IMF copyright and usage (free reuse with attribution),
https://www.imf.org/en/About/copyright-and-terms

What this adds: the site's food-CPI inputs are FAOSTAT (162 countries, but the
newest month on 2026-09-25 is 2026-03, six months old) and Eurostat HICP (EU/EEA
only). The IMF series is the same national-CPI family FAOSTAT redistributes,
at source, so roughly 60 countries have a 2026-06..08 reading here while
FAOSTAT still shows March. Coverage is narrower (~90 countries); consumers
should prefer whichever of FAOSTAT / IMF carries the newer month, per country.

Output: data/imf_food_cpi.json
  { iso3: { food_cpi_yoy_pct, month, food_cpi_yoy_pct_3m_earlier, month_3m_earlier,
            acceleration_pp, source, source_url } }
acceleration_pp = latest YoY minus the YoY three months earlier (only when both
exist); a positive number means food inflation is speeding up.
"""
import csv
import io
from datetime import date

from _common import http_get, write_json

API = ("https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/CPI/+/"
       "*.CPI.CP01.YOY_PCH_PA_PT.M")
PORTAL = "https://data.imf.org/en/datasets/IMF.STA:CPI"
SOURCE = "IMF CPI dataset (IMF.STA:CPI), food and non-alcoholic beverages, YoY %"
# IMF economy codes that are not the ISO3 keys used across data/countries.json.
IMF_TO_ISO3 = {"WBG": "PSE", "KOS": "XKX"}


def _month_index(token):
    # IMF periods look like "2026-M08"
    y, m = token.split("-M")
    return int(y) * 12 + int(m) - 1


def main():
    start = date(date.today().year - 1, 1, 1).strftime("%Y-%m")
    r = http_get(API, params={"c[TIME_PERIOD]": f"ge:{start}"},
                 headers={"Accept": "application/vnd.sdmx.data+csv;version=2.0.0"},
                 timeout=120, patient=True)
    rows = list(csv.DictReader(io.StringIO(r.text)))
    series = {}
    for row in rows:
        iso3 = (row.get("COUNTRY") or "").upper()
        iso3 = IMF_TO_ISO3.get(iso3, iso3)
        t = row.get("TIME_PERIOD") or ""
        v = row.get("OBS_VALUE")
        if len(iso3) != 3 or not iso3.isalpha() or "-M" not in t or v in (None, ""):
            continue
        try:
            series.setdefault(iso3, {})[_month_index(t)] = (t.replace("-M", "-"), float(v))
        except ValueError:
            continue

    out = {}
    for iso3, s in sorted(series.items()):
        last = max(s)
        month, val = s[last]
        prev = s.get(last - 3)
        out[iso3] = {
            "food_cpi_yoy_pct": round(val, 2),
            "month": month,
            "food_cpi_yoy_pct_3m_earlier": round(prev[1], 2) if prev else None,
            "month_3m_earlier": prev[0] if prev else None,
            "acceleration_pp": round(val - prev[1], 2) if prev else None,
            "source": SOURCE,
            "source_url": PORTAL,
        }
    if not out:
        raise RuntimeError(f"IMF CPI returned {len(rows)} rows but zero usable country series")
    months = sorted(v["month"] for v in out.values())
    cutoff = date.fromordinal(date.today().toordinal() - 120).strftime("%Y-%m")
    recent = sum(1 for m in months if m >= cutoff)
    write_json(
        "imf_food_cpi.json", out,
        source=f"{SOURCE} ({API})",
        status="ok",
        notes=(
            "Food and non-alcoholic beverages CPI (COICOP CP01), year-over-year % change as "
            "published by the IMF (period average). One row per country at its own latest "
            f"month. Covered {len(out)} countries; newest month {months[-1]}, oldest "
            f"latest-month {months[0]}; {recent} countries report {cutoff} or later "
            "(i.e. within ~4 months). Countries whose statistics office has not reported recently keep "
            "their last published month — gate on `month` before scoring."
        ),
    )


if __name__ == "__main__":
    main()
