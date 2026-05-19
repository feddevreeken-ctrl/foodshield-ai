"""
FAO Food Price Index (FFPI) — monthly global commodity price indices.

Official landing page:
  https://www.fao.org/worldfoodsituation/foodpricesindex/en/

FAO's current page links to a downloadable CSV that contains nominal monthly
indices from 1990 onwards. We resolve the live CSV URL from the page first,
then fall back to the legacy static location if needed.
"""
import csv
from datetime import datetime
from html import unescape
import io
import re

from _common import http_get, write_json

PAGE_URL = "https://www.fao.org/worldfoodsituation/foodpricesindex/en/"
CSV_RE = re.compile(
    r"https://www\.fao\.org/media/docs/worldfoodsituationlibraries/default-document-library/food_price_indices_data\.csv[^\"]*download=true",
    re.I,
)
LEGACY_FALLBACK = "https://www.fao.org/fileadmin/templates/worldfood/Reports_and_docs/Food_price_indices_data.csv"


def resolve_csv_url():
    page = http_get(PAGE_URL, headers={"Accept": "text/html,*/*"}, timeout=45).text
    match = CSV_RE.search(page)
    if match:
        return unescape(match.group(0))
    return LEGACY_FALLBACK


def parse_series(text):
    rows = list(csv.reader(io.StringIO(text)))
    series = []
    for row in rows:
        if not row or len(row) < 7:
            continue
        date = (row[0] or "").strip()
        try:
            fpi = float(row[1])
        except (TypeError, ValueError):
            continue
        series.append(
            {
                "month": date,
                "fpi": round(fpi, 2),
                "meat": _num(row[2]),
                "dairy": _num(row[3]),
                "cereals": _num(row[4]),
                "oils": _num(row[5]),
                "sugar": _num(row[6]),
            }
        )
    return series


def month_key(token):
    try:
        if re.fullmatch(r"\d{4}-\d{2}", token):
            return datetime.strptime(token, "%Y-%m")
        return datetime.strptime(token, "%b-%y")
    except Exception:
        return None


def main():
    candidates = [resolve_csv_url(), LEGACY_FALLBACK]
    best = None
    for u in candidates:
        if not u:
            continue
        try:
            r = http_get(u, headers={"Accept": "text/csv,*/*"}, timeout=45)
            series = parse_series(r.text)
            latest = series[-1]["month"] if series else None
            latest_key = month_key(latest) if latest else None
            if series and (best is None or (latest_key and latest_key > best["latest_key"])):
                best = {"url": u, "series": series, "latest_key": latest_key}
        except Exception as e:
            print(f"  miss {u}: {e}")
    if not best:
        raise RuntimeError("All FAO FFPI URLs failed")
    series = best["series"]
    if not series:
        raise RuntimeError("FAO FFPI parser returned zero rows")

    series = series[-24:]
    latest = series[-1]
    prev = series[-2] if len(series) > 1 else None
    payload = {
        "latest": latest,
        "change_mom_pct": (
            round((latest["fpi"] - prev["fpi"]) / prev["fpi"] * 100, 2)
            if prev and prev["fpi"]
            else None
        ),
        "series": series,
    }
    write_json(
        "fao_ffpi.json",
        payload,
        source=f"FAO Food Price Index ({best['url']})",
        notes="Freshest official monthly nominal FFPI CSV resolved from the FAO Food Price Index page; legacy fallback retained only if newer source is unavailable.",
    )


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
