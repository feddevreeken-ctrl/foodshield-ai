"""
World Bank Logistics Performance Index (LPI).

Pipeline 14. Composite logistics score + 6 sub-dimensions per country.
Pairs with WGI to feed the conflict/logistics dimension of the structural
FDRS — both were heuristic before.

WHY LPI:
  LPI is the standard cross-country logistics benchmark. Used by ECB, IMF,
  WTO, every supply-chain risk index. Released biennially (next 2025/2026).
  Score range 1-5, higher = better.

ARCHITECTURE NOTE (May 2026):
  WB LPI series live in WDI source_id=2, so they ARE accessible via the
  standard Indicators API. Latest round was 2023 (WB skipped 2020/2022
  due to COVID disruption). 7 indicators, 6 REST calls = ~10-15s total.

INDICATORS WE PULL (per country, most recent year per indicator):
  LP.LPI.OVRL.XQ  Overall LPI
  LP.LPI.CUST.XQ  Customs
  LP.LPI.INFR.XQ  Infrastructure
  LP.LPI.ITRN.XQ  International shipments
  LP.LPI.LOGS.XQ  Logistics quality & competence
  LP.LPI.TRAC.XQ  Tracking & tracing
  LP.LPI.TIME.XQ  Timeliness

OUTPUT: data/lpi.json
  {
    "_meta": {...},
    "data": {
      "BGD": {
        "overall":              {"value": 2.6, "year": 2023},
        "customs":              {"value": 2.3, "year": 2023},
        "infrastructure":       {"value": 2.5, "year": 2023},
        "international_shipments": {"value": 2.8, "year": 2023},
        "logistics_quality":    {"value": 2.6, "year": 2023},
        "tracking_tracing":     {"value": 2.7, "year": 2023},
        "timeliness":           {"value": 3.0, "year": 2023},
        "year": 2023,
        "source": "World Bank Logistics Performance Index",
        ...
      },
      ...
    }
  }

Wired into:
  - run_all.py STEPS list
  - build_source_manifest.py (mode='reference')
  - foodshield-v19.html: LIVE.lpi loader + lpiCardHTML(iso3) renderer
"""
from _common import http_get, write_json

BASE = "https://api.worldbank.org/v2/country/all/indicator/{code}"

INDICATORS = {
    "LP.LPI.OVRL.XQ": ("overall",                "Overall LPI"),
    "LP.LPI.CUST.XQ": ("customs",                "Customs"),
    "LP.LPI.INFR.XQ": ("infrastructure",         "Infrastructure"),
    "LP.LPI.ITRN.XQ": ("international_shipments","International shipments"),
    "LP.LPI.LOGS.XQ": ("logistics_quality",      "Logistics quality"),
    "LP.LPI.TRAC.XQ": ("tracking_tracing",       "Tracking & tracing"),
    "LP.LPI.TIME.XQ": ("timeliness",             "Timeliness"),
}


def main():
    out = {}
    failures = []
    for code, (key, label) in INDICATORS.items():
        print(f"[INFO] LPI {code} — {label}")
        try:
            r = http_get(
                BASE.format(code=code),
                params={"format": "json", "mrv": 1, "per_page": 500},
                timeout=45,
                retries=3,
            )
        except Exception as e:
            print(f"  [warn] {code} fetch failed: {e}")
            failures.append(code)
            continue
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            print(f"  [warn] {code} unexpected response shape")
            failures.append(code)
            continue
        rows = data[1] or []
        kept = 0
        for row in rows:
            iso3 = (row.get("countryiso3code") or "").strip().upper()
            val = row.get("value")
            year = row.get("date")
            if not iso3 or len(iso3) != 3 or val is None:
                continue
            try:
                v = round(float(val), 2)
                y = int(year) if year else None
            except (TypeError, ValueError):
                continue
            country_slot = out.setdefault(iso3, {
                "source": "World Bank Logistics Performance Index",
                "source_url": "https://lpi.worldbank.org",
                "quality_flag": "sourced",
            })
            country_slot[key] = {"value": v, "year": y, "label": label}
            existing_year = country_slot.get("year")
            if existing_year is None or (y is not None and y > existing_year):
                country_slot["year"] = y
            kept += 1
        print(f"  [OK] {code}: {kept} country rows")

    if failures:
        print(f"[WARN] {len(failures)} indicator(s) failed: {failures}")

    out = {iso: row for iso, row in out.items()
           if any(k for k in row if k in [v[0] for v in INDICATORS.values()])}

    print(f"[INFO] Wrote LPI scores for {len(out)} countries")

    for ref in ("USA", "NLD", "DEU", "SGP", "BGD", "AFG", "ETH"):
        if ref in out:
            row = out[ref]
            o = row.get("overall", {}).get("value")
            c = row.get("customs", {}).get("value")
            i = row.get("infrastructure", {}).get("value")
            print(f"  [ref] {ref}: overall={o}, customs={c}, infra={i}")

    write_json(
        "lpi.json",
        out,
        source="World Bank Logistics Performance Index (api.worldbank.org/v2)",
        notes=(
            f"7 logistics dimensions per country, latest year per indicator. "
            f"Score range 1 (worst) to 5 (best). Biennial release; latest 2023 "
            f"(WB skipped 2020 and 2022 due to COVID). "
            f"Covered {len(out)} countries."
        ),
    )


if __name__ == "__main__":
    main()
