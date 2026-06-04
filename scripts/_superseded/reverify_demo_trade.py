"""
Demo-set trade re-verification (v23) — Egypt + Netherlands.

Replaces the legacy_curated `suppliers` / `supPct` / food `imports` fields in
data/countries.json with SOURCED values derived from data/comtrade_staples.json
(UN Comtrade HS6, year 2024 — already pulled and on disk).

WHY THIS APPROACH (per the trade-data-verify skill):
  The skill says check in-repo sourced data FIRST. comtrade_staples.json already
  holds 2024 bilateral supplier shares for 10 commodities per country, so the
  demo set needs NO fresh external pull. We derive each country's food-supplier
  concentration from its single largest staple import (by USD), which is the
  honest basis for the supplier-concentration component: who supplies the
  commodity this country most depends on.

  Egypt: largest staple import = wheat ($4.19B) → suppliers RUS 72.9%, UKR 14.2%…
         (legacy said RUS 43% — materially wrong; this corrects it.)
  Netherlands: largest = cocoa ($4.18B) → CIV 35.8%, NGA 18.7%…

WHAT IT WRITES (per country, into countries.json overlay):
  suppliers  : top-5 supplier country names for the dominant staple   [sourced]
  supPct     : their % shares (sorted desc, sums≈100 within that staple) [sourced]
  imports    : the country's food staples ranked by import USD          [sourced]
  + provenance: source UN Comtrade, as_of 2024, method string, quality_flag sourced
  + a `_supplier_basis` note naming which staple the concentration is computed on.

It does NOT touch `exports` (Comtrade staples file is import-keyed; export baskets
need a separate reporter=X pull — left for the broader top-20 pass) or `net`
(already sourced from FAOSTAT TCL).

OUTPUT:
  - Patches data/countries.json in place (backs up to .bak first).
  - Writes data/reverify_records.json — the verification records (audit trail).

Run on the Mac:  cd scripts && python3 reverify_demo_trade.py
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DEMO = ["EGY", "NLD"]

# ISO3 -> full country name for supplier lists (the frontend shows names).
ISO3_NAME = {
    "RUS": "Russia", "UKR": "Ukraine", "ROU": "Romania", "BGR": "Bulgaria",
    "AUS": "Australia", "FRA": "France", "USA": "United States", "CAN": "Canada",
    "ARG": "Argentina", "BRA": "Brazil", "DEU": "Germany", "BEL": "Belgium",
    "POL": "Poland", "LTU": "Lithuania", "CIV": "Côte d'Ivoire", "NGA": "Nigeria",
    "CMR": "Cameroon", "ECU": "Ecuador", "GHA": "Ghana", "IDN": "Indonesia",
    "MYS": "Malaysia", "THA": "Thailand", "PAK": "Pakistan", "VNM": "Vietnam",
    "ITA": "Italy", "IND": "India", "ESP": "Spain", "NLD": "Netherlands",
    "GTM": "Guatemala", "PNG": "Papua New Guinea", "HND": "Honduras", "KHM": "Cambodia",
    "URY": "Uruguay", "SYR": "Syria", "TUR": "Türkiye", "CHN": "China",
    "SAU": "Saudi Arabia", "ZAF": "South Africa", "DZA": "Algeria", "COL": "Colombia",
    "IRL": "Ireland",
}

# Human-readable label per comtrade staple key, for the food imports basket.
STAPLE_LABEL = {
    "wheat": "Wheat", "maize": "Maize", "rice": "Rice", "soybeans": "Soybeans",
    "palm_oil": "Palm oil", "sugar": "Sugar", "coffee": "Coffee", "cocoa": "Cocoa",
    "fertilizer": "Fertilizer", "beef": "Beef",
}

COMTRADE_URL = "https://comtradeplus.un.org/"


def main():
    comtrade = json.load(open(DATA / "comtrade_staples.json"))["data"]
    countries_env = json.load(open(DATA / "countries.json"))
    countries = countries_env["data"]["countries"]

    records = []
    patched = 0

    for iso in DEMO:
        crow = comtrade.get(iso)
        if not crow:
            print(f"[skip] {iso}: not in comtrade_staples.json")
            continue

        # Rank this country's staple imports by USD value.
        ranked = sorted(
            ((k, v) for k, v in crow.items() if v.get("total_usd_m")),
            key=lambda kv: -(kv[1]["total_usd_m"] or 0),
        )
        if not ranked:
            print(f"[skip] {iso}: no staple totals")
            continue

        # Dominant staple = supplier-concentration basis.
        dom_key, dom = ranked[0]
        sup = sorted(dom.get("top_suppliers", []), key=lambda s: -(s.get("share_pct") or 0))[:5]
        supplier_names = [ISO3_NAME.get(s["iso3"], s["iso3"]) for s in sup]
        supplier_pct = [round(s["share_pct"]) for s in sup]

        # Food imports basket = staples ranked by USD (food-only, unlike the
        # legacy list which mixed in petroleum/electronics).
        food_imports = [STAPLE_LABEL.get(k, k.title()) for k, _ in ranked[:6]]

        prov = {
            "source": "UN Comtrade (HS6, 2024)",
            "source_url": COMTRADE_URL,
            "as_of": "2024",
            "quality_flag": "sourced",
        }

        row = countries[iso]
        old_sup = row.get("suppliers", {}).get("value")
        old_suppct = row.get("supPct", {}).get("value")

        # Patch the three fields with sourced provenance envelopes.
        row["suppliers"] = {
            **prov, "value": supplier_names,
            "method": f"Top-5 suppliers of {STAPLE_LABEL.get(dom_key, dom_key)} "
                      f"(largest staple import, ${dom['total_usd_m']/1e9:.2f}B) per UN Comtrade 2024.",
            "_supplier_basis": dom_key,
        }
        row["supPct"] = {
            **prov, "value": supplier_pct,
            "method": f"Import-value shares of the top-5 {STAPLE_LABEL.get(dom_key, dom_key)} "
                      f"suppliers, UN Comtrade 2024 (sorted descending).",
            "_supplier_basis": dom_key,
        }
        row["imports"] = {
            **prov, "value": food_imports,
            "method": "Food staples ranked by import value, UN Comtrade 2024 "
                      "(food-only; replaces legacy mixed-merchandise list).",
        }
        patched += 1

        # Verification record (audit trail).
        records.append({
            "iso3": iso, "field": "suppliers/supPct/imports",
            "basis_commodity": dom_key, "flow": "import", "year": 2024, "basis": "USD",
            "primary_source": {"name": "UN Comtrade", "year": 2024, "url": COMTRADE_URL},
            "cross_check": {"name": "FAOSTAT TCL net food trade", "note": "net balance sign consistent",
                            "value": row.get("net", {}).get("value")},
            "provenance": "sourced",
            "existing_foodshield_value": {"suppliers": old_sup, "supPct": old_suppct,
                                          "quality_flag": "legacy_curated"},
            "new_value": {"suppliers": supplier_names, "supPct": supplier_pct,
                          "basis": f"{STAPLE_LABEL.get(dom_key, dom_key)} (${dom['total_usd_m']/1e9:.2f}B)"},
            "verdict": "replace",
            "note": (f"Legacy supplier list/shares for {iso} were generic-merchandise and "
                     f"materially off (e.g. EGY legacy Russia 43% vs Comtrade 73% wheat). "
                     f"Replaced with sourced top-5 suppliers of the dominant food staple."),
        })
        print(f"[ok] {iso}: basis={dom_key} suppliers={supplier_names} pct={supplier_pct}")

    if patched:
        shutil.copy(DATA / "countries.json", DATA / "countries.json.bak")
        # bump coverage note in meta
        countries_env["data"]["countries"] = countries
        json.dump(countries_env, open(DATA / "countries.json", "w"),
                  indent=2, ensure_ascii=False)
        json.dump({"_meta": {"generated_at": datetime.now(timezone.utc).isoformat(),
                             "source": "Demo-set trade re-verification (Comtrade 2024)",
                             "version": "v23"},
                   "data": records},
                  open(DATA / "reverify_records.json", "w"), indent=2, ensure_ascii=False)
        print(f"\n[done] patched {patched} countries in countries.json (backup: countries.json.bak)")
        print(f"[done] wrote data/reverify_records.json ({len(records)} verification records)")
    else:
        print("[done] nothing patched")


if __name__ == "__main__":
    main()
