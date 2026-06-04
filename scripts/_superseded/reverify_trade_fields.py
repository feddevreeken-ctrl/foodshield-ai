"""
General trade-field re-verification (v23) — ALL countries, not a demo subset.

Replaces legacy_curated `suppliers` / `supPct` / `imports` in data/countries.json
with SOURCED values wherever the project's already-pulled, sourced data supports
it. Honest by construction: a field is only flipped to `sourced` when a real
public dataset backs it; otherwise it is left legacy and (for suppliers) flagged.

THREE TIERS (per the trade-data-verify skill's "check in-repo data first" rule):

  TIER 1 — Comtrade-backed (full).  Countries present in comtrade_staples.json
    (UN Comtrade HS6, 2024) get:
      suppliers/supPct ← top-5 suppliers of their largest staple import (USD)
      imports          ← their food staples ranked by import value
    All flagged `sourced`, as_of 2024, with a method string + _supplier_basis.

  TIER 2 — PSD-backed (imports only).  Countries with USDA PSD rows but no
    Comtrade record get:
      imports          ← the staples they actually import (PSD imports_kt > 0),
                         ranked by import tonnage; flagged `sourced` (USDA PSD).
    suppliers/supPct are NOT sourced from PSD (PSD has no partner breakdown) —
    they are left legacy and marked `flag_for_review` in the audit so a future
    Comtrade pull can fill them. We do NOT invent supplier shares.

  TIER 3 — neither.  Left fully legacy; recorded as `unavailable` in the audit.

To raise Tier-2 countries to full supplier sourcing, run comtrade_pull.py for
them (quota-limited) and re-run this script — it will pick up the new
comtrade_staples rows automatically.

OUTPUT:
  - Patches data/countries.json in place (backs up to .bak).
  - Writes data/reverify_records.json — full audit trail (every country, every verdict).

Run on the Mac:  cd scripts && python3 reverify_trade_fields.py
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

COMTRADE_URL = "https://comtradeplus.un.org/"
PSD_URL = "https://apps.fas.usda.gov/psdonline/"

STAPLE_LABEL = {
    "wheat": "Wheat", "maize": "Maize", "corn": "Maize", "rice": "Rice",
    "soybeans": "Soybeans", "palm_oil": "Palm oil", "sugar": "Sugar",
    "coffee": "Coffee", "cocoa": "Cocoa", "fertilizer": "Fertilizer", "beef": "Beef",
}

# ISO3 -> display name for supplier lists. Loaded from countries.json names so we
# don't maintain a parallel map; fallback to ISO3 if a partner isn't a tracked country.


def load_name_map(countries):
    m = {}
    for iso, row in countries.items():
        nm = row.get("name")
        if isinstance(nm, dict):
            nm = nm.get("value")
        if nm:
            m[iso] = nm
    # common partners that may not be keys
    m.setdefault("RUS", "Russia"); m.setdefault("UKR", "Ukraine")
    m.setdefault("USA", "United States"); m.setdefault("CIV", "Côte d'Ivoire")
    return m


def main():
    comtrade = json.load(open(DATA / "comtrade_staples.json"))["data"]
    psd = json.load(open(DATA / "usda_psd.json"))["data"]
    env = json.load(open(DATA / "countries.json"))
    countries = env["data"]["countries"]
    name_map = load_name_map(countries)

    records = []
    tier1 = tier2 = tier3 = 0

    for iso, row in countries.items():
        if iso.startswith("US-"):
            continue

        crow = comtrade.get(iso)
        has_comtrade = crow and any(v.get("total_usd_m") for v in crow.values())

        if has_comtrade:
            # ---- TIER 1: full supplier + imports from Comtrade ----
            ranked = sorted(
                ((k, v) for k, v in crow.items() if v.get("total_usd_m")),
                key=lambda kv: -(kv[1]["total_usd_m"] or 0),
            )
            dom_key, dom = ranked[0]
            sup = sorted(dom.get("top_suppliers", []),
                         key=lambda s: -(s.get("share_pct") or 0))[:5]
            names = [name_map.get(s["iso3"], s["iso3"]) for s in sup]
            pct = [round(s["share_pct"]) for s in sup if s.get("share_pct") is not None]
            food_imports = [STAPLE_LABEL.get(k, k.title()) for k, _ in ranked[:6]]
            prov = {"source": "UN Comtrade (HS6, 2024)", "source_url": COMTRADE_URL,
                    "as_of": "2024", "quality_flag": "sourced"}
            dom_lbl = STAPLE_LABEL.get(dom_key, dom_key)
            row["suppliers"] = {**prov, "value": names, "_supplier_basis": dom_key,
                "method": f"Top-5 suppliers of {dom_lbl} (largest staple import, "
                          f"${dom['total_usd_m']/1e9:.2f}B) per UN Comtrade 2024."}
            row["supPct"] = {**prov, "value": pct, "_supplier_basis": dom_key,
                "method": f"Import-value shares of top-5 {dom_lbl} suppliers, UN Comtrade 2024 (desc)."}
            row["imports"] = {**prov, "value": food_imports,
                "method": "Food staples ranked by import value, UN Comtrade 2024."}
            tier1 += 1
            records.append({"iso3": iso, "tier": 1, "provenance": "sourced",
                "basis_commodity": dom_key, "year": 2024,
                "new_value": {"suppliers": names, "supPct": pct},
                "verdict": "replace",
                "note": f"Supplier concentration sourced from {dom_lbl} imports (UN Comtrade 2024)."})

        elif iso in psd:
            # ---- TIER 2: imports basket from USDA PSD (tonnes); suppliers flagged ----
            staples_imported = []
            for commodity, cval in psd[iso].items():
                if isinstance(cval, dict) and (cval.get("imports_kt") or 0) > 0:
                    staples_imported.append((commodity, cval.get("imports_kt") or 0))
            staples_imported.sort(key=lambda kv: -kv[1])
            if staples_imported:
                food_imports = [STAPLE_LABEL.get(k, k.title()) for k, _ in staples_imported]
                row["imports"] = {
                    "source": "USDA PSD", "source_url": PSD_URL, "as_of": "2026 MY",
                    "quality_flag": "sourced",
                    "value": food_imports,
                    "method": "Staples imported (imports_kt>0) ranked by import tonnage, USDA PSD."}
                verdict = "replace"
                note = ("Imports basket sourced from USDA PSD (tonnes). suppliers/supPct "
                        "NOT sourced — PSD has no partner breakdown; needs a Comtrade pull.")
            else:
                verdict = "flag_for_review"
                note = "USDA PSD present but no positive imports_kt; left legacy."
            tier2 += 1
            records.append({"iso3": iso, "tier": 2, "provenance": "partial",
                "year": "2026 MY", "verdict": verdict,
                "suppliers_status": "flag_for_review — needs Comtrade pull", "note": note})

        else:
            # ---- TIER 3: neither source covers this country ----
            tier3 += 1
            records.append({"iso3": iso, "tier": 3, "provenance": "unavailable",
                "verdict": "flag_for_review",
                "note": "No Comtrade or USDA PSD coverage; trade fields remain legacy_curated."})

    shutil.copy(DATA / "countries.json", DATA / "countries.json.bak")
    env["data"]["countries"] = countries
    json.dump(env, open(DATA / "countries.json", "w"), indent=2, ensure_ascii=False)
    json.dump({"_meta": {"generated_at": datetime.now(timezone.utc).isoformat(),
                         "source": "General trade-field re-verification (Comtrade 2024 + USDA PSD)",
                         "version": "v23",
                         "tiers": {"tier1_comtrade_full": tier1, "tier2_psd_imports": tier2,
                                   "tier3_unavailable": tier3}},
               "data": records},
              open(DATA / "reverify_records.json", "w"), indent=2, ensure_ascii=False)

    print(f"[done] Tier 1 (Comtrade full suppliers+imports): {tier1} countries")
    print(f"[done] Tier 2 (USDA PSD imports; suppliers flagged): {tier2} countries")
    print(f"[done] Tier 3 (no coverage; left legacy):           {tier3} countries")
    print(f"[done] patched countries.json (backup: countries.json.bak)")
    print(f"[done] wrote data/reverify_records.json ({len(records)} records)")
    print(f"\nNext: to source suppliers for the {tier2} Tier-2 countries, run")
    print(f"  python3 ../skills/trade-data-verify/scripts/comtrade_pull.py --reporters <ISO3s>")
    print(f"  then re-run this script — it picks up new comtrade_staples rows automatically.")


if __name__ == "__main__":
    main()
