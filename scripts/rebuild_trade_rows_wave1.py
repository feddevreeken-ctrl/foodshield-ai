#!/usr/bin/env python3
"""
Wave-1 trade-row rebuild: COM, TLS, DJI, HTI, PSE.
Source: live UN Comtrade 2024 mirror pulls (exporters reporting shipments TO each
non-reporter country), verified to the kiloton against the June-2026 WITS audit.
Unit rule: netWgt kg / 1e6 = kt. Double-count filter: partner2Code=0 rows only.

Honesty contract:
- supplier shares = real kt shares aggregated across staples (volume basis).
- imports list = staples with material observed flow.
- exports = agri-food only; non-food legacy rows dropped (food-only rule).
  Where no material food export exists, value=[] with an honest note.
- trade_scope added per the master audit (single_commodity_route / staple_basket /
  full_food_trade_surface).
- HTI India-rice tail is audit-carried (live endpoint returned empty bodies, not a
  clean zero) -> kept out of the observed share table, noted.
- PSE is a genuine Comtrade coverage gap (Israel-intermediated imports invisible) ->
  flagged partial with a methodology caveat, NOT presented as a full surface.
"""
import json, datetime, sys

SRC = "data/countries.json"
CT_URL = "https://comtradeplus.un.org/"
ASOF = "2024"

def sourced(value, **extra):
    base = {
        "value": value,
        "quality_flag": "sourced",
        "as_of": ASOF,
        "source": "UN Comtrade 2024 (HS6, exporter-mirror for non-reporter)",
        "source_url": CT_URL,
        "source_dataset": "un_comtrade_hs6_2024_mirror",
        "coverage": "bilateral_hs6_mirror_reported",
        "basis": "rank_quantity_kt",
        "basis_unit": "kt",
        "trade_schema_version": "v2",
        "_verified": "comtrade_2024_mirror_wave1",
    }
    base.update(extra)
    return base

# ---- verified data (kt), shares whole-% of aggregated staple tonnage ----
ROWS = {
  "COM": {
    "imports": (["Rice", "Palm oil", "Sugar"], "single_commodity_route",
      "Staple imports, Comtrade 2024 mirror. Rice is ~97% of staple tonnage (51.96 kt); "
      "palm oil (IDN 1.25 kt) and sugar (EGY 0.24 kt) trace. Comoros files no Comtrade "
      "data; figures are exporter-reported shipments to COM."),
    "suppliers": (["PAK", "IND", "IDN", "THA", "EGY", "TZA"],
                  [82, 14, 2, 1, 0, 0],
      "Suppliers aggregated across staples by real kt (Comtrade 2024 mirror): PAK 43.99, "
      "IND 7.40, IDN 1.25 (palm), THA 0.51, EGY 0.24 (sugar), TZA 0.04. Rice basket: "
      "PAK 84.6% / IND 14.2% / THA 1.0% / TZA 0.1%. Replaces the false PAK-100% legacy row."),
    "exports": ([], "Comoros' exports are non-staple cash crops (vanilla, cloves, "
      "ylang-ylang) — not food staples; dropped under the food-only rule. No material "
      "agri-food staple export in Comtrade 2024."),
  },
  "TLS": {
    "imports": (["Rice", "Palm oil"], "staple_basket",
      "Staple imports, Comtrade 2024 mirror. Rice 146.0 kt + palm oil 16.1 kt both "
      "material. Replaces the partial IND/CHN-only row; adds the full rice partner set "
      "and Indonesia's palm dominance the audit flagged as missing."),
    "suppliers": (["IND", "CHN", "IDN", "THA", "PAK", "KHM", "MMR"],
                  [48, 20, 10, 10, 8, 2, 2],
      "Suppliers aggregated across rice+palm by real kt (Comtrade 2024 mirror): IND 77.44, "
      "CHN 32.20, IDN 16.03 (palm), THA 15.45, PAK 13.28, KHM 4.00, MMR 3.64. "
      "Rice partners match the WITS 2024 audit to the kt; palm = IDN 16.0 / MYS 0.06."),
    "exports": (["Coffee"], "Timor-Leste's only material agri-food export is coffee; "
      "petroleum/sandalwood/marble dropped under the food-only rule."),
  },
  "DJI": {
    "imports": (["Palm oil", "Rice", "Sorghum", "Wheat", "Sugar"], "full_food_trade_surface",
      "Staple imports, Comtrade 2024 mirror — broad re-export/transit hub. Palm 536.0 kt, "
      "rice 338.7 kt, sorghum 30.97 kt (USA), wheat 9.0 kt (TUR), sugar 1.55 kt (EGY). "
      "Replaces the palm-oil-only supplier sketch with the full food-import surface."),
    "suppliers": (["MYS", "IDN", "IND", "PAK", "USA", "TUR", "EGY"],
                  [30, 29, 23, 13, 3, 1, 0],
      "Suppliers aggregated across staples by real kt (Comtrade 2024 mirror): MYS 274.13 "
      "(palm), IDN 261.87 (palm), IND 215.09 (rice), PAK 123.61 (rice), USA 30.97 (sorghum), "
      "TUR 9.00 (wheat), EGY 1.55 (sugar). Palm MYS/IDN and rice IND/PAK match the WITS "
      "2024 audit; note Djibouti is a transit hub, so flows partly re-export to the region."),
    "exports": ([], "Djibouti re-exports rather than producing food staples; legacy "
      "'Live animals' kept out as it is transit, not domestic food output. No material "
      "domestic agri-food export in Comtrade 2024."),
  },
  "HTI": {
    "imports": (["Rice", "Wheat"], "full_food_trade_surface",
      "Staple imports, Comtrade 2024 mirror. Rice ~501 kt (USA dominant) + wheat 289.6 kt "
      "(CAN+USA). Replaces the legacy apparel/mango export row and the miscoded TWN "
      "supplier. India-rice tail (~13 kt) is audit-carried, not freshly observed (see note)."),
    "suppliers": (["USA", "CAN", "PAK", "GUY"],
                  [60, 22, 16, 1],
      "Suppliers aggregated across rice+wheat by real kt (Comtrade 2024 mirror): USA 471.5 "
      "(rice 354.7 + wheat 116.8), CAN 172.8 (wheat), PAK 126.1 (rice), GUY 6.85 (rice). "
      "USA/PAK/GUY rice + USA/CAN wheat all live-confirmed. The legacy 'TWN' supplier was "
      "wrong (absent from Comtrade) and is removed. India rice (~13 kt, audit-carried, "
      "Comtrade preview returned empty bodies on re-pull) is held out of this share table."),
    "exports": (["Mangoes", "Cocoa", "Coffee"], "Haiti's genuine agri-food exports are "
      "mangoes, cocoa and coffee; apparel/essential-oils dropped under the food-only rule."),
  },
  "PSE": {
    "imports": (["Rice", "Sugar"], "single_commodity_route",
      "PARTIAL — Comtrade 2024 mirror captures only flows shipped directly to partner "
      "code PSE (TUR rice 6.39 kt, EGY rice 2.28 kt, TUR sugar 0.46 kt). Most Palestinian "
      "food imports clear through Israeli customs and are recorded under ISR, so they are "
      "invisible here; wheat reads zero despite Palestine being a real wheat importer."),
    "suppliers": (["TUR", "EGY"],
                  [75, 25],
      "PARTIAL: only direct-to-PSE flows surface in Comtrade (TUR 6.85 kt across rice+sugar, "
      "EGY 2.28 kt rice). The dominant Israel-intermediated volume is not captured under "
      "partnerCode 275. Do NOT read this as Palestine's full supplier structure."),
    "exports": (["Olives", "Vegetables"], "Palestine's agri-food exports are olives/olive "
      "oil and vegetables; stone/marble, pharmaceuticals and clothing dropped under the "
      "food-only rule."),
  },
}

# PSE gets partial flags, not sourced
PARTIAL = {"PSE"}

def main():
    d = json.load(open(SRC))
    C = d["data"]["countries"]
    changed = []
    for iso, row in ROWS.items():
        r = C[iso]
        imp_val, scope, imp_note = row["imports"]
        sup_val, sup_pct, sup_note = row["suppliers"]
        exp_val, exp_note = row["exports"]
        flag = "partial" if iso in PARTIAL else "sourced"

        def mk(value, note, basis="rank_quantity_kt", unit="kt", scope_val=None):
            o = sourced(value, note=note)
            o["quality_flag"] = flag
            o["basis"] = basis
            o["basis_unit"] = unit
            if scope_val:
                o["trade_scope"] = scope_val
            if iso in PARTIAL:
                o["_verified"] = "comtrade_2024_mirror_wave1_partial"
            return o

        r["imports"]     = mk(imp_val, imp_note, scope_val=scope)
        r["suppliers"]   = mk(sup_val, sup_note)
        r["supPct"]      = mk(sup_pct, sup_note, basis="share_quantity_pct", unit="pct")
        # exports: agri-food only; honest blank where none
        exp = mk(exp_val, exp_note)
        exp["_food_only"] = True
        if not exp_val:
            exp["quality_flag"] = "none"
            exp["note"] = exp_note
        r["exports"] = exp
        changed.append(iso)

    json.dump(d, open(SRC, "w"), ensure_ascii=False, indent=1)
    print("Updated rows:", ", ".join(changed))

if __name__ == "__main__":
    main()
