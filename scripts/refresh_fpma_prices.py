"""
FAO GIEWS FPMA — domestic staple-food price change per country.

Source: FAO GIEWS Food Price Monitoring and Analysis (FPMA) Tool v4, public
price-module API (no key). The same API backs https://fpma.fao.org/giews/fpmat4/.
  series list : https://fpma.fao.org/giews/v4/global/price_module/api/v1/FpmaSerieDomestic/
  prices      : https://fpma.fao.org/giews/v4/global/price_module/api/v1/FpmaSeriePrice/?uuid__in=<ids>&periodicity=monthly
Series are compiled by FAO GIEWS from national sources, WFP VAM and FEWS NET;
each carries its own source_name. Terms: FAO database terms of use
(https://www.fao.org/contact-us/terms/db-terms-of-use/), attribute FAO GIEWS FPMA.

What this adds: the site's only per-country MARKET price signal was the World
Bank RTFP index (37 countries). FPMA carries current (<= ~4 months old) monthly
staple prices for ~125 countries.

Method (nothing modelled, nothing filled):
  1. Keep domestic series whose commodity is a staple (cereal, flour, bread,
     cassava/gari) and whose monthly series ended within MAX_SERIES_AGE_DAYS.
  2. Per series: latest monthly price vs the same month one year earlier ->
     nominal year-on-year % (local currency). If FPMA publishes a CPI-deflated
     value (price_value_real) for both months, the real YoY % too.
  3. Per country: RETAIL series if the country has any, else WHOLESALE. Only
     series whose latest month is within 2 months of the country's newest
     month are pooled, so an old series cannot dilute the current reading.
     Country value = MEDIAN across series (a single blown-out market cannot
     set it).
A series without a same-month-last-year observation is skipped, not imputed.

Output: data/fpma_prices.json
  { iso3: { staple_price_yoy_pct, staple_price_real_yoy_pct, latest_month,
            price_type, n_series, n_markets, commodities, series_sources,
            source, source_url } }
"""
import re
import statistics
import time
from collections import defaultdict
from datetime import date

from _common import http_get, write_json

API = "https://fpma.fao.org/giews/v4/global/price_module/api/v1"
TOOL_URL = "https://fpma.fao.org/giews/fpmat4/"
SOURCE = "FAO GIEWS FPMA Tool (domestic prices API)"

STAPLE_RE = re.compile(
    r"\b(rice|wheat|maize|sorghum|millet|teff|barley|bread|flour|cassava|gari|corn)\b", re.I)
NOT_STAPLE_RE = re.compile(r"\boil\b", re.I)   # "Maize oil (imported)" is not a staple grain
MAX_SERIES_AGE_DAYS = 150   # a monthly series that stopped >5 months ago is not "current"
POOL_WINDOW_MONTHS = 2      # pool series within 2 months of the country's newest month
BATCH = 80                  # uuids per FpmaSeriePrice request (~1 MB, ~4 s)
SOFT_BUDGET_S = 210         # stay well inside safe_run's 300 s wall-clock cap


def _month_index(iso_date):
    y, m = int(iso_date[:4]), int(iso_date[5:7])
    return y * 12 + (m - 1)


def _monthly_end(serie):
    for p in serie.get("periodicity") or []:
        if p.get("period") == "monthly" and p.get("end_date"):
            return p["end_date"]
    return None


def _series_yoy(datapoints):
    """(latest_month 'YYYY-MM', nominal_yoy, real_yoy_or_None) or None."""
    by_m = {}
    for dp in datapoints or []:
        d = dp.get("date")
        v = dp.get("price_value")
        if not d or not isinstance(v, (int, float)) or v <= 0:
            continue
        by_m[_month_index(d)] = (v, dp.get("price_value_real"), d[:7])
    if not by_m:
        return None
    last = max(by_m)
    prev = by_m.get(last - 12)
    if not prev:
        return None
    (v1, r1, month), (v0, r0, _) = by_m[last], prev
    nominal = (v1 / v0 - 1.0) * 100.0
    real = None
    if isinstance(r1, (int, float)) and isinstance(r0, (int, float)) and r0 > 0 and r1 > 0:
        real = (r1 / r0 - 1.0) * 100.0
    return month, nominal, real


def main():
    started = time.time()
    today = date.today()
    series = http_get(f"{API}/FpmaSerieDomestic/", params={"format": "json"},
                      timeout=120, patient=True).json().get("results") or []
    print(f"[FPMA] {len(series)} domestic series listed")

    keep = {}
    for s in series:
        iso3 = (s.get("iso3_country_code") or "").upper()
        end = _monthly_end(s)
        name = s.get("commodity_name") or ""
        if len(iso3) != 3 or not end or not STAPLE_RE.search(name) or NOT_STAPLE_RE.search(name):
            continue
        try:
            age = (today - date.fromisoformat(end[:10])).days
        except ValueError:
            continue
        if age > MAX_SERIES_AGE_DAYS:
            continue
        keep[s["uuid"]] = s
    print(f"[FPMA] {len(keep)} current staple series in "
          f"{len({s['iso3_country_code'] for s in keep.values()})} countries")
    if not keep:
        raise RuntimeError("FPMA series list parsed to zero current staple series")

    ids = list(keep)
    prices = {}
    truncated = False
    for i in range(0, len(ids), BATCH):
        if time.time() - started > SOFT_BUDGET_S:
            truncated = True
            print(f"[FPMA] soft budget reached after {i} of {len(ids)} series; stopping")
            break
        chunk = ids[i:i + BATCH]
        try:
            body = http_get(f"{API}/FpmaSeriePrice/",
                            params={"uuid__in": ",".join(chunk), "periodicity": "monthly",
                                    "format": "json"},
                            timeout=90).json()
        except Exception as e:
            print(f"[FPMA] batch {i // BATCH} failed: {e}")
            truncated = True
            continue
        for row in (body.get("results") if isinstance(body, dict) else body) or []:
            if isinstance(row, dict) and row.get("uuid") in keep:
                prices[row["uuid"]] = row.get("datapoints") or []

    per_country = defaultdict(list)
    for uid, dps in prices.items():
        res = _series_yoy(dps)
        if not res:
            continue
        s = keep[uid]
        per_country[s["iso3_country_code"].upper()].append({
            "month": res[0], "yoy": res[1], "real": res[2],
            "type": (s.get("price_type") or "").upper(),
            "commodity": s.get("commodity_name"), "market": s.get("market"),
            "src": s.get("source_name"),
        })

    out = {}
    for iso3, rows in sorted(per_country.items()):
        retail = [r for r in rows if r["type"] == "RETAIL"]
        pool = retail or [r for r in rows if r["type"] == "WHOLESALE"]
        if not pool:
            continue
        newest = max(_month_index(r["month"] + "-01") for r in pool)
        pool = [r for r in pool if newest - _month_index(r["month"] + "-01") <= POOL_WINDOW_MONTHS]
        reals = [r["real"] for r in pool if r["real"] is not None]
        latest = max(r["month"] for r in pool)
        srcs = defaultdict(int)
        for r in pool:
            srcs[r["src"]] += 1
        out[iso3] = {
            "staple_price_yoy_pct": round(statistics.median(r["yoy"] for r in pool), 1),
            "staple_price_real_yoy_pct": round(statistics.median(reals), 1) if reals else None,
            "n_real": len(reals),
            "latest_month": latest,
            "price_type": "retail" if retail else "wholesale",
            "n_series": len(pool),
            "n_markets": len({r["market"] for r in pool}),
            "commodities": sorted({r["commodity"] for r in pool})[:12],
            "series_sources": [k for k, _ in sorted(srcs.items(), key=lambda kv: -kv[1])][:3],
            "method": "median across series of latest-month vs same-month-last-year price",
            "source": SOURCE,
            "source_url": TOOL_URL,
        }

    if not out:
        raise RuntimeError("FPMA produced zero countries with a year-on-year staple price")
    n_series = sum(v["n_series"] for v in out.values())
    months = sorted({v["latest_month"] for v in out.values()})
    status = "degraded_partial" if truncated else "ok"
    write_json(
        "fpma_prices.json", out,
        source=f"{SOURCE} ({API})",
        status=status,
        notes=(
            "Staple food (cereals, flour, bread, cassava/gari) price change, NOMINAL local "
            "currency, latest month vs the same month a year earlier; median across series; "
            "retail series preferred, wholesale only where a country has no retail series. "
            "staple_price_real_yoy_pct uses FPMA's CPI-deflated price where published. "
            "In high-inflation or redenominated economies the nominal figure mostly measures "
            "currency depreciation; read it with the real figure and FX. "
            f"Covered {len(out)} countries from {n_series} series; latest months "
            f"{months[0]}..{months[-1]}. Series without a same-month-last-year observation "
            "are skipped, never imputed."
            + (" PARTIAL: some price batches failed or the time budget ran out." if truncated else "")
        ),
    )


if __name__ == "__main__":
    main()
