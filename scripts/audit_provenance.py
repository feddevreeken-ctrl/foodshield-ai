#!/usr/bin/env python3
"""
audit_provenance.py — FDRS component re-sourcing worklist (read-only audit).

The FDRS score is a weighted composite of 9 structural components. Some are already
re-sourced in build_countries_dataset.py (econ_access ← WDI+HDI, grain_buffer ← USDA PSD,
food inflation ← FAOSTAT/Eurostat); the rest still carry heritage values from the embedded
May-2026 country dataset. This script maps each component to (weight, current sourcing
status, a public feed already present in data/ that could upgrade it, that feed's coverage)
and ranks the remaining work by IMPACT = weight × still-legacy.

It CHANGES NOTHING. Wiring a sourced feed into the score is a methodology decision — the
owner's call — so this only produces the prioritized worklist to inform that decision.
No tiers are promoted and no sources are invented here.

Usage: python3 scripts/audit_provenance.py
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"

WEIGHTS = [0.23, 0.16, 0.11, 0.09, 0.09, 0.08, 0.06, 0.12, 0.06]

# component index -> (name, current status, candidate feed file, what the feed provides)
COMPONENTS = [
    (0, "import_dep",            "heritage",    "net_food_trade.json",  "net food trade balance / FBS import share"),
    (1, "supplier_conc",         "heritage",    "comtrade_staples.json", "top-supplier shares (already sourced in suppliers/supPct)"),
    (2, "prod_trend",            "heritage",    "usda_psd.json",        "production/consumption trend by commodity"),
    (3, "food_infl",             "partial",     "faostat_food.json",    "food CPI yr/yr (also eurostat_food.json for EU)"),
    (4, "climate",               "heritage",    "inform_risk.json",     "climate/hazard risk (also aqueduct.json, cckp.json)"),
    (5, "conflict",              "heritage",    "acled.json",           "conflict event intensity"),
    (6, "supply_chain_exposure", "unscored",    None,                   "deliberately null (SCE placeholder, unscored)"),
    (7, "econ_access",           "sourced",     "worldbank_wdi.json",   "reserves/debt-service (WDI) + HDI — DONE in builder"),
    (8, "grain_buffer",          "sourced",     "usda_psd.json",        "ending stocks / consumption (USDA PSD) — DONE in builder"),
]

STATUS_ORDER = {"heritage": 0, "partial": 1, "unscored": 2, "sourced": 3}
# still-legacy fraction used for the impact score
LEGACY_FRAC = {"heritage": 1.0, "partial": 0.5, "unscored": 0.0, "sourced": 0.0}


def feed_coverage(fname):
    """Rough coverage read: (exists, n_entries, bytes). n_entries best-effort."""
    if not fname:
        return (False, 0, 0)
    p = DATA / fname
    if not p.exists():
        return (False, 0, 0)
    b = p.stat().st_size
    try:
        o = json.loads(p.read_text())
        d = o.get("data") if isinstance(o, dict) else o
        if isinstance(d, dict):
            # count leaf country-ish entries
            n = len(d.get("countries") or d.get("rows") or d)
        elif isinstance(d, list):
            n = len(d)
        else:
            n = 0
    except Exception:
        n = 0
    return (True, n, b)


def main():
    print("FDRS COMPONENT RE-SOURCING WORKLIST")
    print("=" * 72)
    print("Read-only. Wiring any feed into the score is the owner's methodology call.\n")
    rows = []
    for idx, name, status, feed, provides in COMPONENTS:
        w = WEIGHTS[idx]
        impact = w * LEGACY_FRAC[status]
        exists, n, b = feed_coverage(feed)
        rows.append((impact, idx, name, status, w, feed, provides, exists, n, b))

    # ranked worklist: highest re-sourcing impact first
    rows.sort(key=lambda r: (-r[0], STATUS_ORDER[r[3]]))

    print(f"{'#':>2} {'component':<22}{'wt':>5} {'status':<9}{'impact':>7}  candidate feed")
    print("-" * 72)
    total_impact = 0.0
    for impact, idx, name, status, w, feed, provides, exists, n, b in rows:
        total_impact += impact
        feed_note = "—"
        if feed:
            if exists and b > 1000:
                feed_note = f"{feed} ({n} entries, {b//1024}KB) ✓ present"
            elif exists:
                feed_note = f"{feed} ({b}B) ⚠ looks like a stub"
            else:
                feed_note = f"{feed} ⚠ MISSING"
        flag = "→ upgrade" if impact > 0 else ("· done" if status == "sourced" else "· n/a")
        print(f"c{idx:<2}{name:<22}{w:>5.2f} {status:<9}{impact:>7.3f}  {flag}")
        print(f"     {feed_note}")
        print(f"     provides: {provides}\n")

    legacy = [r for r in rows if r[0] > 0]
    print("-" * 72)
    print(f"Remaining re-sourceable weight (Σ impact): {total_impact:.3f} of 1.00 total FDRS weight.")
    print(f"{len(legacy)} component(s) still (partly) on heritage values, all with a public "
          f"feed already in data/ that could source them.")
    print("\nSuggested order (highest score-impact first, feed already present):")
    for impact, idx, name, status, w, feed, provides, exists, n, b in legacy:
        ready = "feed ready" if (exists and b > 1000) else ("feed is a stub — refresh first" if exists else "feed missing")
        print(f"  {impact:.3f}  c{idx} {name:<22} via {feed}  [{ready}]")
    print("\nEach upgrade recomputes the score → validate against test_fdrs_v2 fixtures and "
          "review rank shifts (scripts/report_fdrs_rank_diffs.py) before shipping.")
    _semantic_caveats()


def _semantic_caveats():
    """A re-sourcing is NOT a mechanical swap. Demonstrated with real data: the heritage
    supplier_conc component is NOT raw supplier concentration — it is contextualised by
    import-dependency, so exporters with concentrated (but irrelevant) import shares are
    correctly scored low. Swapping in raw concentration would inflate their risk."""
    import statistics as st
    cs = json.loads((DATA / "countries.json").read_text())["data"]["countries"]
    examples, divs = [], []
    for iso, r in cs.items():
        if iso.startswith("US-"):
            continue
        c = r.get("c", {}).get("value") if isinstance(r.get("c"), dict) else None
        sp = r.get("supPct", {}).get("value") if isinstance(r.get("supPct"), dict) else None
        if not (isinstance(c, list) and len(c) > 1 and isinstance(c[1], (int, float))):
            continue
        if not (isinstance(sp, list) and sp and isinstance(sp[0], (int, float))):
            continue
        divs.append(abs(c[1] - sp[0]))
        # exporters whose heritage conc is far BELOW their raw top-1 share
        if sp[0] - c[1] >= 40:
            examples.append((iso, c[1], sp[0]))
    print("\n" + "=" * 72)
    print("CAVEAT — re-sourcing is methodology work, not a mechanical swap:")
    if divs:
        print(f"  heritage supplier_conc vs raw top-1 supplier share: median gap "
              f"{st.median(divs):.0f} pts across {len(divs)} countries.")
    if examples:
        ex = ", ".join(f"{iso} ({conc}→{top1})" for iso, conc, top1 in sorted(examples, key=lambda t: t[2]-t[1], reverse=True)[:5])
        print(f"  {len(examples)} EXPORTERS are scored LOW despite concentrated imports "
              f"(heritage → raw top-1): {ex}.")
    print("  Heritage supplier_conc contextualises concentration by IMPORT-DEPENDENCY — a "
          "self-sufficient\n  exporter's concentrated (but immaterial) imports must stay low-risk. "
          "A correct re-source is\n  ~ concentration × import-dependency, NOT raw supPct. This is a "
          "methodology decision (owner's\n  call); the same care applies to every component above.")


if __name__ == "__main__":
    main()
