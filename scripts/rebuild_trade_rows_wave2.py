#!/usr/bin/env python3
"""
Wave-2 trade-row rebuild: BRB, DMA, GRD, LCA, VCT, GNQ, SLB, VUT, AFG, GNB.
Source: live UN Comtrade 2024 exporter-mirror (non-reporters). netWgt kg /1e6 = kt;
partner2Code=0 only. Honesty flags carried from the verification pull:
- 5 repo single-100% supplier rows were WRONG (GNQ, VUT, AFG, GNB, VCT) -> corrected.
- 3 were right (BRB, DMA, SLB) -> kept, upgraded to sourced.
- VUT legacy "TWN 100%" unconfirmable (Taiwan reports as 'Other Asia nes' 490) -> replaced
  with the captured CHN/THA rice, flagged.
- GNB tonnage (150kt rice / ~2.1M pop) is transit -> trade_scope transit_hub + caveat.
- AFG PAK rice carries Comtrade legacyEstimationFlag=6 -> noted (modeled-soft), kept partial.
- Unverifiable India cells (AFG/GNB rice) held out of shares, noted.
- Exports: agri-food only; non-food dropped (food-only rule).
"""
import json

SRC = "data/countries.json"; CT = "https://comtradeplus.un.org/"; ASOF = "2024"

def base(value, flag, note, basis="rank_quantity_kt", unit="kt", scope=None, vtag="comtrade_2024_mirror_wave2"):
    o = {"value": value, "quality_flag": flag, "as_of": ASOF,
         "source": "UN Comtrade 2024 (HS6, exporter-mirror for non-reporter)",
         "source_url": CT, "source_dataset": "un_comtrade_hs6_2024_mirror",
         "coverage": "bilateral_hs6_mirror_reported", "basis": basis, "basis_unit": unit,
         "trade_schema_version": "v2", "_verified": vtag, "note": note}
    if scope: o["trade_scope"] = scope
    return o

# (imports[list,scope,note], suppliers[list], supPct[list], sup_note, exports[list,note], flag)
ROWS = {
 "BRB": dict(flag="sourced", scope="staple_basket",
   imp=["Maize","Soybeans","Wheat"],
   imp_note="Staple imports, Comtrade 2024 mirror: maize 27.6 kt, soybeans 13.3 kt, wheat 15.9 kt — all USA. CAN wheat tail unverifiable (endpoint flaked, not zero).",
   sup=["USA"], pct=[100],
   sup_note="USA dominates all three staples (maize 27.6 + soy 13.3 + wheat 15.9 kt). Repo's USA-100% confirmed against live Comtrade 2024.",
   exp=["Sugar","Rum"], exp_note="Agri-food exports only (sugar, rum); electronics/tourism/chemicals dropped under the food-only rule."),
 "DMA": dict(flag="sourced", scope="single_commodity_route",
   imp=["Rice"],
   imp_note="Rice 26.5 kt (Guyana), Comtrade 2024 mirror. Single-staple route; wheat clears via Trinidad and isn't captured directly.",
   sup=["GUY","USA"], pct=[100,0],
   sup_note="Guyana rice is the route (26.5 kt); trace USA rice (0.07 kt). Repo's GUY-100% confirmed.",
   exp=["Bananas","Grapefruit","Vegetables"], exp_note="Agri-food exports only; soap/bay-oil dropped under the food-only rule."),
 "GRD": dict(flag="sourced", scope="staple_basket",
   imp=["Wheat","Maize","Rice"],
   imp_note="Staple imports, Comtrade 2024 mirror: wheat 21.4 kt (USA), maize 11.8 kt (USA), rice 1.74 kt (Guyana). CAN wheat tail unverifiable.",
   sup=["USA","GUY"], pct=[95,5],
   sup_note="USA leads wheat+maize (33.2 kt); Guyana rice 1.74 kt. Real two-supplier basket, not single-source.",
   exp=["Nutmeg","Cocoa","Bananas","Fish"], exp_note="Agri-food exports only; soursop kept (fruit). No non-food in this row."),
 "LCA": dict(flag="sourced", scope="staple_basket",
   imp=["Maize","Wheat","Rice"],
   imp_note="Staple imports, Comtrade 2024 mirror: maize 8.91 kt (USA), wheat 7.65 kt (USA) +1.1 kt (CAN), rice 2.05 kt (Guyana).",
   sup=["USA","GUY","CAN"], pct=[84,10,6],
   sup_note="USA-led basket (16.6 kt) with real Guyana rice (2.05 kt) and Canada wheat (1.1 kt) slices. Not single-supplier.",
   exp=["Bananas","Cocoa","Avocados"], exp_note="Agri-food exports only; beer/garments dropped under the food-only rule."),
 "VCT": dict(flag="sourced", scope="staple_basket",
   imp=["Wheat","Rice"],
   imp_note="Staple imports, Comtrade 2024 mirror: wheat 21.8 kt (USA), rice 2.84 kt (Guyana). Maize mirror returned honest zeros on tested exporters.",
   sup=["USA","GUY"], pct=[88,12],
   sup_note="USA wheat (21.8 kt) + Guyana rice (2.84 kt). Repo's single-100% supplier was WRONG — corrected to a real two-supplier split.",
   exp=["Bananas","Arrowroot","Sweet potatoes"], exp_note="Agri-food/root-crop exports only (eddoes, tannias are roots — kept)."),
 "GNQ": dict(flag="sourced", scope="single_commodity_route",
   imp=["Rice"],
   imp_note="Rice 17.6 kt, Comtrade 2024 mirror: IND 10.85 kt + THA 6.77 kt. Wheat mirror returned honest zero on USA (likely EU-routed, uncaptured).",
   sup=["IND","THA"], pct=[62,38],
   sup_note="Repo's IND-100% was WRONG — India leads (62%) but Thailand is a real 38% slice (6.77 kt). Corrected against live Comtrade 2024.",
   exp=["Cocoa","Coffee"], exp_note="Agri-food exports only; petroleum/natural-gas/timber dropped under the food-only rule."),
 "SLB": dict(flag="sourced", scope="single_commodity_route",
   imp=["Wheat","Rice"],
   imp_note="Wheat 16.4 kt (Australia), Comtrade 2024 mirror. Rice route (likely VNM/CHN) not captured on tested exporters; wheat is the dominant captured flow.",
   sup=["AUS"], pct=[100],
   sup_note="Australia wheat dominates the captured staple basket (16.4 kt). Repo's AUS-100% confirmed; rice partner uncaptured this pass.",
   exp=["Fish","Copra","Palm oil","Cocoa"], exp_note="Agri-food exports only; timber dropped under the food-only rule."),
 "VUT": dict(flag="partial", scope="single_commodity_route",
   imp=["Rice"],
   imp_note="Rice 2.6 kt, Comtrade 2024 mirror: CHN 2.04 kt + THA 0.58 kt. Bulk wheat not captured (AUS/NZ trace only).",
   sup=["CHN","THA"], pct=[78,22],
   sup_note="Repo's legacy 'TWN 100%' is UNCONFIRMABLE — Taiwan reports to Comtrade as 'Other Asia, nes' (490), so a TWN supplier can't be verified. Replaced with the captured CHN (78%) + THA (22%) rice; flagged partial pending a fuller pull.",
   exp=["Copra","Kava","Beef","Cocoa"], exp_note="Agri-food exports only; timber dropped under the food-only rule."),
 "AFG": dict(flag="partial", scope="full_food_trade_surface",
   imp=["Wheat","Rice","Maize"],
   imp_note="Staple imports, Comtrade 2024 mirror: wheat 268.5 kt (KAZ 251.6 + UZB 16.9), rice ~611 kt (Pakistan, carries Comtrade estimation flag — treat as soft). India rice unverifiable (endpoint flaked).",
   sup=["PAK","KAZ","UZB"], pct=[68,28,2],
   sup_note="Repo's KAZ-100% was WRONG. Kazakhstan leads WHEAT (251.6 kt) but Pakistan rice (~611 kt, Comtrade legacyEstimationFlag=6, modeled-soft) is the single largest staple flow; Uzbekistan adds 16.9 kt wheat. PAK share rests on an estimated figure -> row kept partial. India rice likely material but unverifiable this pass.",
   exp=["Tomatoes & onions","Grapes & table fruit","Pulses","Apples","Nuts"], exp_note="Agri-food exports only (all already food)."),
 "GNB": dict(flag="partial", scope="transit_hub",
   imp=["Rice"],
   imp_note="Rice 150.2 kt, Comtrade 2024 mirror: PAK 113.0 + CHN 35.5 + SEN 1.53 + THA 0.16. NOTE: 150 kt into a ~2.1M-pop country (~71 kg/capita) far exceeds consumption — classic West-Africa re-export/transit; tonnage is not all GNB domestic demand. India rice unverifiable this pass.",
   sup=["PAK","CHN","SEN","THA"], pct=[75,24,1,0],
   sup_note="Repo's CHN-100% was WRONG — Pakistan (75%) is the dominant supplier, China second (24%). India likely material but unverifiable (endpoint flaked). Transit-hub tonnage caveat applies.",
   exp=["Nuts"], exp_note="Agri-food exports only (cashews — already food)."),
}

PARTIAL = {"VUT","AFG","GNB"}

def main():
    d = json.load(open(SRC)); C = d["data"]["countries"]
    for iso, x in ROWS.items():
        r = C[iso]; flag = x["flag"]
        tag = "comtrade_2024_mirror_wave2_partial" if iso in PARTIAL else "comtrade_2024_mirror_wave2"
        r["imports"]   = base(x["imp"], flag, x["imp_note"], scope=x["scope"], vtag=tag)
        r["suppliers"] = base(x["sup"], flag, x["sup_note"], vtag=tag)
        r["supPct"]    = base(x["pct"], flag, x["sup_note"], basis="share_quantity_pct", unit="pct", vtag=tag)
        if x["scope"] == "transit_hub":
            r["imports"]["_transit_hub"] = True
        exp = base(x["exp"], flag if x["exp"] else "none", x["exp_note"])
        exp["_food_only"] = True
        r["exports"] = exp
    json.dump(d, open(SRC,"w"), ensure_ascii=False, indent=1)
    print("Wave-2 rows written:", ", ".join(ROWS))

if __name__ == "__main__":
    main()
