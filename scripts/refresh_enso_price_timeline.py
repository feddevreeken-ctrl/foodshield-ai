"""
FAO GIEWS FPMA — staple prices month by month in every El Niño country (the Prices map timeline).

Source: FAO GIEWS FPMA Tool v4 price-module API (no key), as refresh_fpma_prices.py reads it.

What it builds, per El Niño country (the union of data/enso_regions.json regions[].iso3):
  1. yoy: for each month since January 2024, the median across the country's staple series of the real
     (CPI-deflated) price against the same month a year earlier, with the number of series behind it.
     A month enters only if at least one series has both months; nothing is filled or interpolated.
  2. events: the same series lined up around the last two strong El Niños and this one, as an index
     (March of the El Niño year = 100), month by month from nine months before the December peak to
     fifteen after; median across the series that have the March base. These are the paths the page
     replays for its estimate (this year carried along each past path), never a forecast of its own.
Staple = cereal, flour, bread, cassava/gari series (the same rule as refresh_fpma_prices.py); retail
series are used where a country has any, wholesale otherwise.

Output: data/enso_price_timeline.json
"""
import json
import re
import statistics
import time
from pathlib import Path

from _common import http_get, write_json

API = "https://fpma.fao.org/giews/v4/global/price_module/api/v1"
SOURCE = "FAO GIEWS FPMA Tool (domestic prices API), CPI-deflated series"
DATA = Path(__file__).resolve().parent.parent / "data"
STAPLE_RE = re.compile(r"\b(rice|wheat|maize|sorghum|millet|teff|barley|bread|flour|cassava|gari|corn)\b", re.I)
NOT_STAPLE_RE = re.compile(r"\boil\b", re.I)
EVENTS = [("2015-16", "2015-12"), ("2023-24", "2023-12"), ("2026-27", "2026-12")]
K0, K1 = -9, 15
YOY_FROM = "2024-01"
BATCH = 80
SOFT_BUDGET_S = 240


def _mi(s):
    return int(s[:4]) * 12 + int(s[5:7]) - 1


def _month(i):
    return f"{i // 12}-{i % 12 + 1:02d}"


def _monthly_span(serie):
    for p in serie.get("periodicity") or []:
        if p.get("period") == "monthly" and p.get("start_date") and p.get("end_date"):
            return p["start_date"][:7], p["end_date"][:7]
    return None


def main():
    started = time.time()
    regions = json.loads((DATA / "enso_regions.json").read_text())["data"]["regions"]
    tele = sorted({i for r in regions for i in (r.get("iso3") or [])})
    listed = http_get(f"{API}/FpmaSerieDomestic/", params={"format": "json"}, timeout=120, patient=True).json().get("results") or []
    keep = {}
    for s in listed:
        iso = (s.get("iso3_country_code") or "").upper()
        name = s.get("commodity_name") or ""
        span = _monthly_span(s)
        if iso not in tele or not span or not STAPLE_RE.search(name) or NOT_STAPLE_RE.search(name):
            continue
        if span[1] < "2016-01":  # ended before the 2015-16 window closed: no use to either part
            continue
        keep[s["uuid"]] = {"iso": iso, "type": (s.get("price_type") or "").upper(), "span": span}
    # Retail where a country has any retail series, wholesale otherwise.
    has_retail = {v["iso"] for v in keep.values() if v["type"] == "RETAIL"}
    keep = {u: v for u, v in keep.items() if v["type"] == "RETAIL" or v["iso"] not in has_retail}
    print(f"[timeline] {len(keep)} staple series in {len({v['iso'] for v in keep.values()})} of {len(tele)} El Niño countries")

    series = {}
    ids = list(keep)
    for i in range(0, len(ids), BATCH):
        if time.time() - started > SOFT_BUDGET_S:
            print(f"[timeline] soft budget reached after {i} of {len(ids)} series")
            break
        chunk = ids[i:i + BATCH]
        # The API pages its results ("next"); follow every page or most series in a batch are lost.
        url, params = f"{API}/FpmaSeriePrice/", {"uuid__in": ",".join(chunk), "periodicity": "monthly", "format": "json"}
        while url:
            try:
                body = http_get(url, params=params, timeout=120).json()
            except Exception as e:
                print(f"[timeline] batch {i // BATCH} failed: {e}")
                break
            for row in (body.get("results") if isinstance(body, dict) else body) or []:
                if row.get("uuid") in keep:
                    real = {_mi(d["date"]): d["price_value_real"] for d in row.get("datapoints") or []
                            if d.get("date") and isinstance(d.get("price_value_real"), (int, float)) and d["price_value_real"] > 0}
                    if real:
                        series[row["uuid"]] = real
            url, params = (body.get("next") if isinstance(body, dict) else None), None

    by_iso = {}
    for u, real in series.items():
        by_iso.setdefault(keep[u]["iso"], []).append(real)

    countries = {}
    y0 = _mi(YOY_FROM + "-01")
    for iso, reals in sorted(by_iso.items()):
        last = max(max(r) for r in reals)
        yoy = []
        for m in range(y0, last + 1):
            vals = [(r[m] / r[m - 12] - 1) * 100 for r in reals if m in r and (m - 12) in r]
            if vals:
                yoy.append([_month(m), round(statistics.median(vals), 1), len(vals)])
        events = {}
        for label, peak in EVENTS:
            p = _mi(peak + "-01")
            pts = []
            for k in range(K0, K1 + 1):
                vals = [r[p + k] / r[p + K0] * 100 for r in reals if (p + K0) in r and (p + k) in r]
                if vals:
                    pts.append([k, round(statistics.median(vals), 1), len(vals)])
            if len(pts) >= 2:
                events[label] = pts
        if yoy or events:
            countries[iso] = {"yoy": yoy, "events": events, "n_series": len(reals), "latest_month": _month(last)}
    print(f"[timeline] {len(countries)} countries; with 2015-16 path: {sum(1 for c in countries.values() if '2015-16' in c['events'])}, "
          f"with 2023-24 path: {sum(1 for c in countries.values() if '2023-24' in c['events'])}")
    write_json("enso_price_timeline.json", {
        "window": {"from": K0, "to": K1, "base": "March of the El Niño year = 100", "peak_month": "December", "yoy_from": YOY_FROM},
        "events": [{"label": label, "peak_month": peak} for label, peak in EVENTS],
        "countries": countries,
    }, source=SOURCE, notes="Median across each country's staple series (retail where any exist) of the CPI-deflated price: year on year by month, and indexed around each El Niño. Nothing filled or interpolated.")


if __name__ == "__main__":
    main()
