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
        # A pair that survives on its own but NOT against the Indian Ocean Dipole
        # control is not evidence of an ENSO effect. Four such pairs (Australian
        # barley, Brazilian wheat, Indonesian maize, Uruguayan rice) used to sit
        # inside the production-weighted sum while the map called the result "what
        # ENSO implies". They are excluded from the ENSO aggregate now and
        # reported separately, because the honest answer for a country whose only
        # modelled crop is one of them is "no ENSO signal", not a number.
        def enso_specific(e):
            return e.get("enso_specific") is not False

        def counted(e):
            return e.get("signal") and enso_specific(e)

        signal_prod = sum(e.get("mean_production_kt") or 0.0
                          for e in commodities.values() if counted(e))
        shared_prod = sum(e.get("mean_production_kt") or 0.0
                          for e in commodities.values()
                          if e.get("signal") and not enso_specific(e))

        consumption = sum(
            (psd_c.get(k) or {}).get("consumption_kt") or 0.0
            for k in ("wheat", "corn", "rice", "soybeans")
        )

        def shock_for(oni, predicate):
            kt = 0.0
            for e in commodities.values():
                if not predicate(e):
                    continue
                prod = e.get("mean_production_kt") or 0.0
                if prod <= 0:
                    continue
                b = (e["yield_pct_per_oni_nino"] if oni >= 0
                     else e["yield_pct_per_oni_nina"])
                kt += prod * (b / 100.0) * oni
            return kt

        levels: dict = {}
        for label, oni in LEVELS.items():
            shock_kt = 0.0
            for e in commodities.values():
                if not counted(e):
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

        # Kept, clearly separated, never summed into the ENSO figure above.
        shared_levels = {}
        if shared_prod > 0:
            for label, oni in LEVELS.items():
                kt = shock_for(oni, lambda e: e.get("signal") and not enso_specific(e))
                shared_levels[label] = {
                    "oni": oni,
                    "production_shock_kt": round(kt, 1),
                    "production_shock_pct": round(100.0 * kt / total_prod, 2),
                }

        out[iso3] = {
            "coverage": round(signal_prod / total_prod, 3),
            "modelled_commodities": sorted(
                k for k, e in commodities.items() if counted(e)),
            "shared_iod_commodities": sorted(
                k for k, e in commodities.items()
                if e.get("signal") and not enso_specific(e)),
            "shared_iod_coverage": round(shared_prod / total_prod, 3),
            "unmodelled_commodities": sorted(
                k for k, e in commodities.items() if not e.get("signal")),
            "total_production_kt": round(total_prod, 1),
            "staple_consumption_kt": round(consumption, 1),
            "levels": levels,
            "shared_iod_levels": shared_levels,
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
                "ONE limitation is real and unresolved: NO OUT-OF-SAMPLE VALIDATION. The "
                "aggregate has never been scored against harvests it was not fitted on, so "
                "its calibration is unknown even where its direction is not. Until that is "
                "settled the aggregate is a direction, not a magnitude, and the UI renders "
                "it rounded to the whole percent and paints it in banded steps for that "
                "reason. The second limitation is now RESOLVED: the four pairs that survive "
                "on their own but not against the Indian Ocean Dipole control (Australian "
                "barley, Brazilian wheat, Indonesian maize, Uruguayan rice) are no longer "
                "inside the production-weighted sum. They are reported separately under "
                "shared_iod_levels / shared_iod_commodities and are never added to the ENSO "
                "figure. A country whose only modelled crop was one of them now correctly "
                "reports no ENSO coverage rather than a number."),
            "blocking_superseded": (
                "PREVIOUS TEXT, KEPT FOR AUDIT -- every claim in it was false against the "
                "current model: \"Inherits data/enso_model.json's unresolved seasonal "
                "alignment: coefficients are selected by fit rather than pre-registered "
                "from a crop calendar, and 9 of the 15 surviving pairs flip sign between "
                "the two candidate alignments. NOT wired into the UI.\" Alignment is fixed "
                "from published harvest calendars and never chosen by fit; there are 28 "
                "surviving pairs, not 15; and the file has been wired into the UI for some "
                "time."),
            "ready_when": (
                "Flip production_ready to true only when the aggregate has been scored "
                "out-of-sample. The non-ENSO-specific pairs are already excluded."),
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
