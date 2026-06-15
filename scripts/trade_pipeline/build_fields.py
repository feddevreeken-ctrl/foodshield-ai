"""
trade_pipeline/build_fields.py — JOB: turn sourced data into country fields.

Reads the sourced inputs already on disk (comtrade_staples.json + usda_psd.json)
and writes data/countries.json's trade-facing fields with honest provenance. It
does NOT fetch anything — pull.py does that. Run this after a pull (or any time
the sourced files change) to refresh the country fields.

Three tiers, honest by construction:
  TIER 1  country in comtrade_staples → suppliers+supPct+imports sourced (Comtrade)
  TIER 2  country in usda_psd only    → imports sourced (PSD tonnes); suppliers
                                         left legacy + flagged (PSD has no partners)
  TIER 3  neither                      → left legacy; recorded unavailable

Outputs:
  - patches data/countries.json (backup .bak)
  - writes data/reverify_records.json (audit trail, every country + verdict)
"""
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trade_schema import COMTRADE_URL, PSD_URL, normalize_trade_surface

STAPLE_LABEL = {
    "wheat": "Wheat", "maize": "Maize", "corn": "Maize", "rice": "Rice",
    "soybeans": "Soybeans", "palm_oil": "Palm oil", "sugar": "Sugar",
    "coffee": "Coffee", "cocoa": "Cocoa", "fertilizer": "Fertilizer", "beef": "Beef",
}


def _name_map(countries):
    m = {}
    for iso, row in countries.items():
        nm = row.get("name")
        if isinstance(nm, dict):
            nm = nm.get("value")
        if nm:
            m[iso] = nm
    m.update({"RUS": "Russia", "UKR": "Ukraine", "USA": "United States",
              "CIV": "Côte d'Ivoire"})
    return m


def _total_value_usd(rec):
    return rec.get("total_value_usd") or rec.get("total_usd_m") or 0


def _load_optional(path):
    p = DATA / path
    if p.exists():
        try:
            return json.loads(p.read_text()).get("data", {}) or {}
        except Exception:
            pass
    return {}


def build():
    # Defensive load — if the sourced inputs are missing/empty (e.g. a failed
    # Comtrade run on CI), do NOT touch countries.json. Re-patching from empty
    # data would silently wipe the sourced supplier fields back to nothing.
    comtrade = _load_optional("comtrade_staples.json")
    psd = _load_optional("usda_psd.json")
    exports = _load_optional("comtrade_exports.json")   # optional; may not exist yet
    if not comtrade and not psd:
        print("[build_fields] SKIP — both comtrade_staples.json and usda_psd.json are "
              "empty/missing; leaving countries.json untouched to avoid wiping sourced fields.")
        return 0, 0, 0
    try:
        env = json.loads((DATA / "countries.json").read_text())
        countries = env["data"]["countries"]
    except Exception as e:
        print(f"[build_fields] SKIP — countries.json unreadable ({e}); nothing patched.")
        return 0, 0, 0
    names = _name_map(countries)

    records = []
    t1 = t2 = t3 = 0

    exp_sourced = 0
    for iso, row in countries.items():
        if iso.startswith("US-"):
            continue

        # ---- EXPORTS (independent of the import tiers): source from comtrade_exports
        # if a reporter-side pull has been run (pull.py --flow X). exports = staples
        # this country exports ranked by value; exportDests = top destinations of its
        # largest export staple. Left legacy if no export pull yet for this country.
        erow = exports.get(iso)
        if erow and any(_total_value_usd(v) for v in erow.values()):
            eranked = sorted(((k, v) for k, v in erow.items() if _total_value_usd(v)),
                             key=lambda kv: -_total_value_usd(kv[1]))
            edom_key, edom = eranked[0]
            dests = sorted(edom.get("top_destinations", []),
                           key=lambda s: -(s.get("share_pct") or 0))[:5]
            eprov = {"source": "UN Comtrade (HS6, 2024, exports)", "source_url": COMTRADE_URL,
                     "as_of": "2024", "quality_flag": "sourced"}
            row["exports"] = {**eprov,
                "value": [STAPLE_LABEL.get(k, k.title()) for k, _ in eranked[:6]],
                "method": "Food staples ranked by export value, UN Comtrade 2024."}
            row["exportDests"] = {**eprov,
                "value": [names.get(s["iso3"], s["iso3"]) for s in dests],
                "_export_basis": edom_key,
                "method": f"Top-5 destinations of {STAPLE_LABEL.get(edom_key, edom_key)} "
                          f"exports (largest export staple), UN Comtrade 2024."}
            exp_sourced += 1

        crow = comtrade.get(iso)
        has_ct = crow and any(_total_value_usd(v) for v in crow.values())

        if has_ct:
            ranked = sorted(((k, v) for k, v in crow.items() if _total_value_usd(v)),
                            key=lambda kv: -_total_value_usd(kv[1]))
            dom_key, dom = ranked[0]
            sup = sorted(dom.get("top_suppliers", []),
                         key=lambda s: -(s.get("share_pct") or 0))[:5]
            sup_names = [names.get(s["iso3"], s["iso3"]) for s in sup]
            sup_pct = [round(s["share_pct"]) for s in sup if s.get("share_pct") is not None]
            food_imports = [STAPLE_LABEL.get(k, k.title()) for k, _ in ranked[:6]]
            prov = {"source": "UN Comtrade (HS6, 2024)", "source_url": COMTRADE_URL,
                    "as_of": "2024", "quality_flag": "sourced"}
            lbl = STAPLE_LABEL.get(dom_key, dom_key)
            row["suppliers"] = {**prov, "value": sup_names, "_supplier_basis": dom_key,
                "method": f"Top-5 suppliers of {lbl} (largest staple import, "
                          f"${_total_value_usd(dom)/1e9:.2f}B), UN Comtrade 2024."}
            row["supPct"] = {**prov, "value": sup_pct, "_supplier_basis": dom_key,
                "method": f"Import-value shares of top-5 {lbl} suppliers, UN Comtrade 2024."}
            row["imports"] = {**prov, "value": food_imports,
                "method": "Food staples ranked by import value, UN Comtrade 2024."}
            t1 += 1
            records.append({"iso3": iso, "tier": 1, "provenance": "sourced",
                "basis_commodity": dom_key, "verdict": "replace",
                "new_value": {"suppliers": sup_names, "supPct": sup_pct}})

        elif iso in psd:
            staples = sorted(((k, c.get("imports_kt") or 0)
                              for k, c in psd[iso].items()
                              if isinstance(c, dict) and (c.get("imports_kt") or 0) > 0),
                             key=lambda kv: -kv[1])
            if staples:
                row["imports"] = {"source": "USDA PSD", "source_url": PSD_URL,
                    "as_of": "2026 MY", "quality_flag": "sourced",
                    "value": [STAPLE_LABEL.get(k, k.title()) for k, _ in staples],
                    "method": "Staples imported (imports_kt>0) ranked by tonnage, USDA PSD."}
                verdict, note = "replace", ("Imports sourced from USDA PSD; suppliers need a "
                                            "Comtrade pull (run pull.py --only " + iso + ").")
            else:
                verdict, note = "flag_for_review", "PSD present, no positive imports_kt."
            t2 += 1
            records.append({"iso3": iso, "tier": 2, "provenance": "partial",
                "verdict": verdict, "suppliers_status": "needs Comtrade pull", "note": note})
        else:
            t3 += 1
            records.append({"iso3": iso, "tier": 3, "provenance": "unavailable",
                "verdict": "flag_for_review",
                "note": "No Comtrade/PSD coverage; trade fields remain legacy_curated."})

    changes = normalize_trade_surface(countries)
    shutil.copy(DATA / "countries.json", DATA / "countries.json.bak")
    env["data"]["countries"] = countries
    (DATA / "countries.json").write_text(json.dumps(env, indent=2, ensure_ascii=False))
    (DATA / "reverify_records.json").write_text(json.dumps(
        {"_meta": {"generated_at": datetime.now(timezone.utc).isoformat(),
                   "source": "trade_pipeline/build_fields.py (Comtrade 2024 + USDA PSD)",
                   "version": "v23",
                   "tiers": {"tier1_comtrade_full": t1, "tier2_psd_imports": t2,
                             "tier3_unavailable": t3}},
         "data": records}, indent=2, ensure_ascii=False))

    print(f"[build_fields] Tier1 Comtrade full: {t1} · Tier2 PSD imports: {t2} · "
          f"Tier3 none: {t3}")
    print(f"[build_fields] exports sourced (reporter-side pull): {exp_sourced} countries"
          + ("" if exp_sourced else " — run pull.py --flow X to populate"))
    print(f"[build_fields] trade schema normalized: {len(changes)} field updates")
    print(f"[build_fields] patched countries.json (.bak saved) + wrote reverify_records.json")
    return t1, t2, t3


if __name__ == "__main__":
    build()
