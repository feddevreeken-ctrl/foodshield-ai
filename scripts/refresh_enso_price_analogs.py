"""
FAO GIEWS FPMA — southern African maize prices through past El Niños (a price analog).

Source: FAO GIEWS Food Price Monitoring and Analysis (FPMA) Tool v4, public price-module API (no key),
the same API refresh_fpma_prices.py reads. Terms: FAO database terms of use; attribute FAO GIEWS FPMA.

Southern Africa is where El Niño's harvest damage is best documented (drier December–February, maize
grain fill in February–March). This collector lines up one maize price per country around each strong
El Niño so the page can show what local prices did last time, month by month.

Method (nothing modelled, nothing filled):
  1. One series per country, named below: the longest-running national-average, capital or benchmark
     market for maize grain or maize meal. Matched by country, market, commodity and price type.
  2. FPMA's CPI-deflated price (price_value_real), so the index is after inflation.
  3. Per event, index = real price / real price in March of the El Niño year x 100, for each month from
     nine months before the December peak to fifteen after. A missing month stays missing; a country-
     event without its March base is dropped.
  4. Per event: each country's highest index and its month; the median across countries of both.
Events: 2015-16 and 2023-24 (the last two strong El Niños) and the current event, up to its latest month.

Output: data/enso_price_analogs.json
"""
import statistics

from _common import http_get, keep_last_good, write_json

API = "https://fpma.fao.org/giews/v4/global/price_module/api/v1"
TOOL_URL = "https://fpma.fao.org/giews/fpmat4/"
SOURCE = "FAO GIEWS FPMA Tool (domestic prices API), CPI-deflated series"

# iso3, market, commodity, price type (as FPMA names them)
SERIES = [
    ("ZAF", "Randfontein", "Maize (white)", "WHOLESALE"),
    ("ZMB", "National Average", "Maize (white)", "RETAIL"),
    ("MWI", "National Average", "Maize", "RETAIL"),
    ("MOZ", "Maputo", "Maize (white)", "RETAIL"),
    ("SWZ", "National Average", "Maize meal", "RETAIL"),
    ("LSO", "Maseru", "Maize meal", "RETAIL"),
    ("NAM", "Windhoek", "Maize meal", "RETAIL"),
    ("BWA", "National Average", "Maize meal", "RETAIL"),
]
EVENTS = [("2015-16", "2015-12"), ("2023-24", "2023-12"), ("2026-27", "2026-12")]
K0, K1 = -9, 15  # months from the December peak


def _mi(iso_date):
    return int(iso_date[:4]) * 12 + int(iso_date[5:7]) - 1


def _month(mi):
    return f"{mi // 12}-{mi % 12 + 1:02d}"


def _monthly_end(serie):
    for p in serie.get("periodicity") or []:
        if p.get("period") == "monthly" and p.get("end_date"):
            return p["end_date"]
    return ""


def add_estimates(countries, k1):
    """Estimate, not a forecast: this year's latest index carried along each past event's own path
    (index at month k over index at this year's latest month). Computed here so the page prints it, not derives it."""
    tops = {}
    for c in countries:
        now = c["events"].get("2026-27")
        if not now:
            continue
        lk, lv = now[-1]
        paths = []
        for lab in ("2015-16", "2023-24"):
            m = dict((k, v) for k, v in c["events"].get(lab, []))
            if not m.get(lk):
                continue
            pth = [[lk, lv]] + [[k, round(lv * m[k] / m[lk], 1)] for k in range(lk + 1, k1 + 1) if k in m]
            if len(pth) > 1:
                top = max(q[1] for q in pth[1:])
                paths.append({"label": lab, "points": pth, "top": top})
                tops.setdefault(lab, []).append(top)
        if paths:
            c["estimate"] = {"from_k": lk, "paths": paths}
    return {lab: {"median_top_pct": round(statistics.median(v) - 100, 1), "n": len(v)} for lab, v in tops.items()}


def main():
    listed = http_get(f"{API}/FpmaSerieDomestic/", params={"format": "json"},
                      timeout=120, patient=True).json().get("results") or []
    picked = {}
    for iso, market, commodity, ptype in SERIES:
        hits = [s for s in listed if (s.get("iso3_country_code") or "").upper() == iso
                and s.get("market_name") == market and s.get("commodity_name") == commodity
                and (s.get("price_type") or "").upper() == ptype]
        if hits:
            picked[iso] = max(hits, key=_monthly_end)
    if not picked:
        raise RuntimeError("FPMA series list matched none of the analog series")

    body = http_get(f"{API}/FpmaSeriePrice/", params={"uuid__in": ",".join(s["uuid"] for s in picked.values()),
                                                      "periodicity": "monthly", "format": "json"}, timeout=120).json()
    rows = {r.get("uuid"): r.get("datapoints") or [] for r in (body.get("results") if isinstance(body, dict) else body) or []}

    countries, per_event = [], {label: [] for label, _ in EVENTS}
    for iso, _, _, _ in SERIES:
        s = picked.get(iso)
        if not s or s["uuid"] not in rows:
            continue
        real = {_mi(d["date"]): d["price_value_real"] for d in rows[s["uuid"]]
                if d.get("date") and isinstance(d.get("price_value_real"), (int, float)) and d["price_value_real"] > 0}
        if not real:
            continue
        events = {}
        for label, peak in EVENTS:
            p = _mi(peak)
            base = real.get(p + K0)
            if not base:
                continue
            pts = [[k, round(real[p + k] / base * 100, 1)] for k in range(K0, K1 + 1) if (p + k) in real]
            if len(pts) < 2:
                continue
            events[label] = pts
            top = max(pts, key=lambda q: q[1])
            per_event[label].append({"iso": iso, "peak_index": top[1], "peak_k": top[0], "last": pts[-1]})
        if events:
            countries.append({
                "iso": iso, "name": s.get("country_name"), "market": s.get("market_name"),
                "commodity": s.get("commodity_name"), "price_type": (s.get("price_type") or "").lower(),
                "series_source": s.get("source_name"), "latest_month": _month(max(real)), "events": events,
            })

    summary = {}
    for label, peak in EVENTS:
        got = per_event[label]
        if not got:
            continue
        summary[label] = {
            "peak_month": peak, "n": len(got),
            "median_peak_pct": round(statistics.median(g["peak_index"] for g in got) - 100, 1),
            "median_peak_k": statistics.median_low(sorted(g["peak_k"] for g in got)),
            "range_peak_pct": [round(min(g["peak_index"] for g in got) - 100, 1), round(max(g["peak_index"] for g in got) - 100, 1)],
            # 2026-10-05: the same +-3% flat band the page's tables use ("unchanged"), so the counts agree.
            "n_below_base_now": sum(1 for g in got if g["last"][1] < 97),
            "last_k": max(g["last"][0] for g in got),
        }
    # 2026-10-04: during an FPMA outage every series came back without real prices and an empty file went out.
    if not countries:
        keep_last_good("enso_price_analogs.json", "FPMA served no CPI-deflated prices for any analog series")
    est = add_estimates(countries, K1)
    if est:
        summary["estimate"] = {"by_path": est, "method": "This year's latest index carried along each past event's own path; a replay, not a forecast."}
    print(f"[analogs] {len(countries)} countries; " + "; ".join(f"{k}: n={v['n']}, median peak {v['median_peak_pct']:+}%" for k, v in summary.items() if k != "estimate"))
    write_json("enso_price_analogs.json", {
        "window": {"from": K0, "to": K1, "base": "March of the El Niño year = 100", "peak_month": "December"},
        "events": [{"label": label, "peak_month": peak} for label, peak in EVENTS],
        "countries": countries, "summary": summary, "tool_url": TOOL_URL,
    }, source=SOURCE, notes="Real (CPI-deflated) maize price per country, indexed to March of each El Niño year; months from the December peak. Series named in scripts/refresh_enso_price_analogs.py.")


if __name__ == "__main__":
    main()
