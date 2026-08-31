#!/usr/bin/env python3
"""
build_enso_exposure.py — turn fitted ENSO yield responses into per-country exposure.

Reads data/enso_model.json (coefficients measured by build_enso_model.py, net of
the Indian Ocean Dipole) and data/usda_psd.json (current production and
consumption), and emits data/enso_exposure.json: for each country, at each
scenario ONI level, the production-weighted crop shock in percent AND in
thousand tonnes, plus the resulting change in net import requirement.

THE HEADLINE NUMBER IS TONNES, NOT PRICE
----------------------------------------
The obvious thing to model is a price. The evidence says don't:

  - Of six El Nino episodes 1980-2014, world agricultural prices rose in ONE.
  - The strongest event on record (2015-16) coincided with the FAO Food Price
    Index bottoming at a seven-year low in January 2016 -- the month the ONI
    peaked. 1997-98 the same: wheat and maize fell ~30% year on year.
  - Both modern price spikes peaked during LA NINA (2007-08, ONI -1.76;
    2010-11, -1.57). Ubilava (2017, World Development) finds wheat prices rise
    after La Nina and fall after El Nino -- the opposite of the folk story --
    and no causal ENSO linkage at all for maize, barley or sorghum.
  - Iizumi et al. find El Nino is largely REDISTRIBUTIVE globally: it hurts
    22-24% of harvested area and helps 30-36%.

So a modelled world-price rise would be the most confidently wrong number we
could publish. What ENSO demonstrably does move is PHYSICAL AVAILABILITY in
specific countries, and that is computable from the fitted coefficients and PSD
tonnages with no elasticity assumption at all. Local price transmission is real
(South African white maize rose ~33% in two months of 2024 while world grain was
soft) but it runs through domestic market structure and trade policy, not
through a world price -- so it is left to the country panel rather than faked
with a global elasticity.

COVERAGE IS REPORTED, NOT ASSUMED
---------------------------------
Only 69 of 428 country x commodity pairs carry a detectable signal. A country's
shock is therefore diluted by its UNMODELLED production: the weighted mean uses
every commodity in the denominator but only signal-bearing ones in the
numerator. A country whose one significant crop is a rounding error in its food
supply gets a small shock and a low `coverage`, rather than being presented as
fully characterised off a minor crop. Coverage is published per country so the
UI can grey out thin cases instead of drawing a confident bar over nothing.

SCENARIO LEVELS
---------------
Keyed to ONI as published in CPC's oni.ascii.txt (ERSSTv6), which is the series
the coefficients were fit on. This matters: CPC's official index is now RONI,
and BoM uses relative Nino3.4 -- the same July 2026 ocean reads +1.4 (CPC
relative), +2.03 (IRI traditional) and +2.20 (BoM relative). A slider labelled
only "+2.0" would be meaningless, so the index and version travel with the data.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ONI levels to precompute. Named for CPC's conventional strength bands.
LEVELS = {
    "la_nina_strong": -1.5,
    "la_nina_moderate": -1.0,
    "la_nina_weak": -0.5,
    "neutral": 0.0,
    "el_nino_weak": 0.5,
    "el_nino_moderate": 1.0,
    "el_nino_strong": 1.5,
    "el_nino_very_strong": 2.0,
    "el_nino_extreme": 2.5,
}


def load(name: str) -> dict:
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    model = load("enso_model.json")
    psd = load("usda_psd.json")["data"]

    out: dict = {}
    for iso3, commodities in model["data"].items():
        psd_c = psd.get(iso3, {})

        # Denominator: ALL production we know about, modelled or not.
        total_prod = sum(e.get("mean_production_kt") or 0.0 for e in commodities.values())
        if total_prod <= 0:
            continue
        signal_prod = sum(e.get("mean_production_kt") or 0.0
                          for e in commodities.values() if e.get("signal"))

        consumption = sum(
            (psd_c.get(k) or {}).get("consumption_kt") or 0.0
            for k in ("wheat", "corn", "rice", "soybeans")
        )

        levels: dict = {}
        for label, oni in LEVELS.items():
            shock_kt = 0.0
            for e in commodities.values():
                if not e.get("signal"):
                    continue
                prod = e.get("mean_production_kt") or 0.0
                if prod <= 0:
                    continue
                b = (e["yield_pct_per_oni_nino"] if oni >= 0
                     else e["yield_pct_per_oni_nina"])
                shock_kt += prod * (b / 100.0) * oni
            entry = {
                "oni": oni,
                "production_shock_kt": round(shock_kt, 1),
                # Diluted by unmodelled production -- see module docstring.
                "production_shock_pct": round(100.0 * shock_kt / total_prod, 2),
            }
            if consumption > 0:
                # A production loss must be made up by imports (or by eating
                # less). Expressed against consumption this is directly
                # comparable across countries of very different size.
                entry["import_need_pct_of_consumption"] = round(
                    -100.0 * shock_kt / consumption, 2)
            levels[label] = entry

        out[iso3] = {
            "coverage": round(signal_prod / total_prod, 3),
            "modelled_commodities": sorted(
                k for k, e in commodities.items() if e.get("signal")),
            "unmodelled_commodities": sorted(
                k for k, e in commodities.items() if not e.get("signal")),
            "total_production_kt": round(total_prod, 1),
            "staple_consumption_kt": round(consumption, 1),
            "levels": levels,
        }

    strong = "el_nino_strong"
    ranked = sorted(
        (v["levels"][strong]["production_shock_pct"], k)
        for k, v in out.items() if v["coverage"] > 0
    )
    payload = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
            "production_ready": False,
            "blocking": (
                "Inherits data/enso_model.json's unresolved seasonal alignment: "
                "coefficients are selected by fit rather than pre-registered from a crop "
                "calendar, and 9 of the 15 surviving pairs flip sign between the two "
                "candidate alignments. NOT wired into the UI."),
            "index": "ONI (CPC oni.ascii.txt, ERSSTv6 lineage)",
            "index_note": (
                "Scenario levels are ONI as published in CPC's oni.ascii.txt, the series "
                "the coefficients were fit on. CPC's OFFICIAL index is now RONI and BoM "
                "uses relative Nino3.4; the same July 2026 ocean reads +1.4 (CPC "
                "relative), +2.03 (IRI traditional) and +2.20 (BoM relative). Do not "
                "compare these levels against a number from another index."
            ),
            "method": (
                "Per country, the production-weighted sum of fitted per-commodity yield "
                "responses at each ONI level. Numerator covers only commodities with a "
                "detected signal; denominator covers all commodities with production, so "
                "the percentage is deliberately diluted by unmodelled output and "
                "`coverage` reports how much of production is actually characterised."
            ),
            "why_no_price": (
                "No world price is modelled. World agricultural prices rose in only one "
                "of six El Nino episodes 1980-2014; the record 2015-16 event coincided "
                "with the FAO Food Price Index at a seven-year low in the month the ONI "
                "peaked; and both modern price spikes peaked under La Nina. Ubilava "
                "(2017) finds wheat prices FALL after El Nino. The defensible ENSO "
                "impact is physical availability, which needs no elasticity assumption."
            ),
            "caveats": [
                "ENSO shifts the odds of a yield outcome; it does not determine any "
                "single country-season.",
                "Coefficients are net of the Indian Ocean Dipole. Australian wheat "
                "carries NO coefficient because its apparent ENSO signal does not "
                "survive that control -- and Australia 2026 is in fact running wet "
                "against the El Nino script.",
                "Fitted on yield. Where ENSO transmits through AREA harvested instead "
                "-- Indonesian and Philippine rice are the documented cases -- this "
                "model is blind by construction and reports no signal.",
                "National aggregates can cancel real sub-national effects. El Nino "
                "wets Ethiopia's Oct-Dec Deyr rains while drying the JJAS kiremt that "
                "supplies most of its food.",
            ],
            "countries": len(out),
        },
        "data": out,
    }
    path = os.path.join(ROOT, "data", "enso_exposure.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")

    print(f"[OK] {len(out)} countries -> data/enso_exposure.json")
    print(f"[INFO] at ONI +1.5 (strong El Nino), worst production shocks:")
    for pct, iso in ranked[:10]:
        v = out[iso]
        lv = v["levels"][strong]
        print(f"   {iso}  {pct:+7.2f}%  ({lv['production_shock_kt']:+10.0f} kt, "
              f"import need {lv.get('import_need_pct_of_consumption', float('nan')):+6.2f}% of "
              f"consumption, coverage {v['coverage']:.2f})")
    print(f"[INFO] largest beneficiaries:")
    for pct, iso in ranked[-5:]:
        print(f"   {iso}  {pct:+7.2f}%  (coverage {out[iso]['coverage']:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
