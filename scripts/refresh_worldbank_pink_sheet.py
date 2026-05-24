"""
World Bank Commodity Markets "Pink Sheet" monthly benchmarks.

Official source:
  https://www.worldbank.org/en/research/commodity-markets

The page links to a monthly XLSX workbook ("CMO-Historical-Data-Monthly.xlsx")
containing benchmark prices for wheat, maize, rice, palm oil, sugar, fertilizers,
coffee, cocoa, beef, and more. We resolve the current workbook URL from the
landing page so the script keeps working as the World Bank rotates document IDs.

Output: data/worldbank_pink_sheet.json
  {
    "as_of_month": "2026-04",
    "dataset_updated_on": "May 04, 2026",
    "series": {
      "wheat": {
        "label": "Wheat, US SRW",
        "unit": "$/mt",
        "source_code": "WHEAT_US_SRW",
        "latest_month": "2026-04",
        "latest_value": 236.4,
        "previous_value": 231.1,
        "change_mom_pct": 2.29
      },
      ...
    }
  }
"""
from __future__ import annotations

from datetime import datetime
from html import unescape
from io import BytesIO
import re

from openpyxl import load_workbook

from _common import http_get, write_json

LANDING_URL = "https://www.worldbank.org/en/research/commodity-markets"
WORKBOOK_RE = re.compile(
    r"https://thedocs\.worldbank\.org/en/doc/[^\"]+/related/CMO-Historical-Data-Monthly\.xlsx",
    re.I,
)

SERIES_MAP = {
    "wheat": "WHEAT_US_SRW",
    "maize": "MAIZE",
    "rice": "RICE_05",
    "soybeans": "SOYBEANS",
    "palm_oil": "PALM_OIL",
    "sugar": "SUGAR_WLD",
    "coffee": "COFFEE_ARABIC",
    "cocoa": "COCOA",
    "urea": "UREA_EE_BULK",
    "phosphate_rock": "PHOSROCK",
    "dap": "DAP",
    "beef": "BEEF",
}


def resolve_workbook_url():
    html = http_get(LANDING_URL, headers={"Accept": "text/html,*/*"}, timeout=45).text
    match = WORKBOOK_RE.search(html)
    if not match:
        raise RuntimeError("Could not find Pink Sheet monthly workbook link on landing page")
    return unescape(match.group(0))


def load_workbook_rows(url):
    blob = http_get(
        url,
        headers={
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*"
        },
        timeout=90,
    ).content
    wb = load_workbook(BytesIO(blob), data_only=True, read_only=True)
    ws = wb["Monthly Prices"]
    rows = list(ws.iter_rows(values_only=True))
    return rows


def normalize_num(value):
    if value in (None, "", "…", "..."):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_month(token):
    if not isinstance(token, str):
        return None
    token = token.strip()
    m = re.fullmatch(r"(\d{4})M(\d{2})", token)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2))
    return f"{year:04d}-{month:02d}"


def latest_points(data_rows, col_idx):
    points = []
    for row in data_rows:
        month = parse_month(row[0] if row else None)
        if not month:
            continue
        val = normalize_num(row[col_idx] if col_idx < len(row) else None)
        if val is None:
            continue
        points.append((month, val))
    if not points:
        return None, None, []
    latest = points[-1]
    previous = points[-2] if len(points) > 1 else None
    # Retain the last 60 months (or however many exist) for sparkline rendering.
    history = points[-60:]
    return latest, previous, history


def main():
    workbook_url = resolve_workbook_url()
    rows = load_workbook_rows(workbook_url)
    if len(rows) < 8:
        raise RuntimeError("Pink Sheet workbook structure is unexpectedly short")

    dataset_updated_on = str(rows[3][0]).replace("Updated on ", "").strip() if rows[3] else None
    labels = rows[4]
    units = rows[5]
    codes = rows[6]
    data_rows = rows[7:]

    col_by_code = {}
    for idx, code in enumerate(codes):
        if isinstance(code, str) and code.strip():
            col_by_code[code.strip()] = idx

    out = {}
    as_of_months = []
    for key, source_code in SERIES_MAP.items():
        if source_code not in col_by_code:
            continue
        idx = col_by_code[source_code]
        latest, previous, history = latest_points(data_rows, idx)
        if not latest:
            continue
        latest_month, latest_value = latest
        previous_value = previous[1] if previous else None
        change = None
        if previous_value not in (None, 0):
            change = round((latest_value - previous_value) / previous_value * 100, 2)
        as_of_months.append(latest_month)
        out[key] = {
            "label": labels[idx],
            "unit": units[idx],
            "source_code": source_code,
            "latest_month": latest_month,
            "latest_value": round(latest_value, 3),
            "previous_value": round(previous_value, 3) if previous_value is not None else None,
            "change_mom_pct": change,
            # Last ≤60 monthly points for sparkline rendering in the commodity drilldown.
            "history": [
                {"month": m, "value": round(v, 3)} for (m, v) in history
            ],
        }

    if not out:
        raise RuntimeError("Pink Sheet parser found zero usable commodity benchmarks")

    write_json(
        "worldbank_pink_sheet.json",
        {
            "as_of_month": max(as_of_months) if as_of_months else None,
            "dataset_updated_on": dataset_updated_on,
            "series": out,
        },
        source=f"World Bank Commodity Markets Pink Sheet ({workbook_url})",
        notes=(
            "Monthly nominal USD benchmark prices parsed from the World Bank Pink Sheet "
            "historical workbook. Used for commodity cards and live market benchmarks."
        ),
    )


if __name__ == "__main__":
    main()
