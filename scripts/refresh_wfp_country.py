"""
WFP HungerMap per-country deep data — FX, inflation, nutrition, PDC hazards.

Per HungerMap docs (docs-wfp-hungermap.netlify.app/docs/chatbot/data_retrievals/),
the v2 API exposes much richer endpoints than the global adm0data sweep:

  • /v2/adm0/{adm0_code}/countryData.json  — FCS history, import dependency, news
  • /v2/iso3/{ISO3}/countryIso3Data.json   — FX graph, inflation graphs, nutrition
  • /v2/pdc.json                            — Pacific Disaster Center hazard feed
  • /v2/ipc.json                            — IPC phase classifications (replaces
                                              the IPC scraper we couldn't authenticate)

We pull a subset focused on signals the nowcast actually uses:
  - importDependency           → already in structural FDRS, useful for sanity check
  - fcsGraph latest 3 months   → short-term consumption trajectory
  - currencyExchangeGraph      → 90-day FX change vs USD
  - inflationGraphs.food       → latest food inflation %
  - nutrition.wasting/stunting → chronic vulnerability for under-5s

We restrict to countries already in our COUNTRY_COORDS (~190) so the request count
stays reasonable (one call per country, ~2-3 minutes total).

Output: data/wfp_country.json (replaces nothing — augments wfp_hungermap.json).
"""
import time

from _common import http_get, write_json, COUNTRY_COORDS

URL_TPL = "https://api.hungermapdata.org/v2/iso3/{iso3}/countryIso3Data.json"


def main():
    out = {}
    isos = sorted(COUNTRY_COORDS.keys())
    print(f"[INFO] fetching per-country HungerMap data for {len(isos)} ISO3 codes")

    for i, iso in enumerate(isos):
        try:
            r = http_get(URL_TPL.format(iso3=iso), timeout=25, retries=2)
            d = r.json() or {}
            # The endpoint sometimes wraps payload in `body`
            if isinstance(d.get("body"), dict):
                d = d["body"]

            fx = _extract_fx(d.get("currencyExchangeGraph"))
            food_infl = _extract_inflation(d.get("inflationGraphs"), key="food")
            head_infl = _extract_inflation(d.get("inflationGraphs"), key="headline")
            nut = d.get("nutrition") or {}

            row = {
                "fx_currency": (d.get("currencyExchangeGraph") or {}).get("name"),
                "fx_latest": fx["latest"],
                "fx_90d_change_pct": fx["change_pct"],
                "fx_currency_shock": bool(fx["change_pct"] is not None and fx["change_pct"] < -10),
                "food_inflation_pct": food_infl,
                "headline_inflation_pct": head_infl,
                "inflation_shock": bool(food_infl is not None and food_infl > 15),
                "wasting_pct": _num(nut.get("wasting")),
                "stunting_pct": _num(nut.get("stunting")),
            }
            # Don't store completely empty rows
            if any(v not in (None, False) for v in row.values()):
                out[iso] = row
        except Exception as e:
            # Country-level endpoint missing for small/disputed territories is normal
            continue

        if (i + 1) % 30 == 0:
            print(f"  [progress] {i+1}/{len(isos)} ({len(out)} written)")
            time.sleep(0.4)

    write_json(
        "wfp_country.json",
        out,
        source="WFP HungerMap per-country (api.hungermapdata.org/v2/iso3/{iso3}/countryIso3Data.json)",
        notes=(
            "Per-country FX (90d change vs USD), food + headline inflation, "
            "child wasting/stunting. Flags: fx_currency_shock = local currency "
            "fell >10% vs USD in 90d; inflation_shock = food inflation >15%. "
            f"Covered {len(out)} of {len(COUNTRY_COORDS)} requested countries."
        ),
    )


def _extract_fx(g):
    """currencyExchangeGraph: {name, source, updated, data:[{x, y}]}"""
    if not isinstance(g, dict):
        return {"latest": None, "change_pct": None}
    data = g.get("data") or []
    pts = [p for p in data if isinstance(p, dict) and isinstance(p.get("y"), (int, float))]
    if not pts:
        return {"latest": None, "change_pct": None}
    pts.sort(key=lambda p: p.get("x") or "")
    latest = pts[-1]["y"]
    # 90 days ago point — assume daily-ish density, fall back to oldest available
    baseline = pts[max(0, len(pts) - 90)]["y"]
    pct = round((latest - baseline) / baseline * 100, 1) if baseline else None
    return {"latest": round(latest, 4), "change_pct": pct}


def _extract_inflation(g, key="food"):
    """inflationGraphs: {headline: {data: [{x,y}]}, food: {data: [{x,y}]}}"""
    if not isinstance(g, dict):
        return None
    sub = g.get(key) or {}
    data = sub.get("data") or []
    pts = [p.get("y") for p in data if isinstance(p, dict) and isinstance(p.get("y"), (int, float))]
    if not pts:
        return None
    return round(pts[-1], 2)


def _num(v):
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
