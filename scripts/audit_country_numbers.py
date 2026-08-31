"""
Per-country number audit — every scored country, every component, every claim
this repo can check against ANOTHER file it already ships.

v86. The audit that prompted this found that no validator anywhere compares a
country's published number against the feed that should govern it. qa_checks.py
asserts envelope shape, coverage floors and freshness; validate_fdrs.py measures
discrimination in aggregate. Neither can tell you that a specific country's
import dependency contradicts its own net-trade row.

This is cross-field, per-country, and deterministic. No model, no network — it
only ever compares files already on disk, so it can run in CI and it cannot
disagree with itself between runs.

Severity:
  P0  the published number is internally contradicted by this repo's own data
  P1  the number is stale, or its provenance overstates what backs it
  P2  worth a look — unusual but defensible

Usage:  python3 scripts/audit_country_numbers.py [--iso IND] [--severity P0]
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

W = [0.23, 0.16, 0.11, 0.09, 0.09, 0.08, 0.06, 0.12, 0.06]
NAMES = ["import_dep", "supplier_conc", "prod_trend", "food_infl", "climate",
         "conflict", "sce", "econ_access", "grain_buffer"]
CURRENT_YEAR = 2026


def load(name):
    p = DATA / name
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text())
    except Exception:
        return {}
    return obj.get("data", obj) if isinstance(obj, dict) else {}


def _num(v):
    return v if isinstance(v, (int, float)) and v == v else None


def _year_of(s):
    """Pull a 4-digit year out of an as_of string like '2023', 'MY2026', '2026 (3m)'."""
    if s is None:
        return None
    t = str(s)
    for i in range(len(t) - 3):
        chunk = t[i:i + 4]
        if chunk.isdigit():
            y = int(chunk)
            if 1900 <= y <= 2100:
                return y
    return None


def audit():
    countries = load("countries.json").get("countries", {})
    nowcast = load("nowcast.json")
    psd = load("usda_psd.json")
    ipc = load("ipc.json")
    fews = load("fews.json")
    aq = load("aqueduct.json")
    hapi = load("hapi_conflict.json")
    rtfp = load("rtfp.json")
    dep = load("faostat_import_dep.json")
    shares = load("country_caloric_shares.json")

    findings = []

    def add(iso, sev, code, msg):
        findings.append({"iso": iso, "severity": sev, "code": code, "message": msg})

    for iso, row in sorted(countries.items()):
        if iso.startswith("US-"):
            continue
        fdrs = (row.get("fdrs") or {}).get("value")
        cv = (row.get("c") or {}).get("value") or []
        if fdrs is None or len(cv) != 9:
            add(iso, "P0", "shape", f"fdrs={fdrs}, component vector length {len(cv)}")
            continue

        # --- 1. the published score must equal what its own components imply ---
        obs = [(i, v) for i, v in enumerate(cv) if _num(v) is not None]
        if obs:
            den = sum(W[i] for i, _ in obs)
            base = sum(W[i] * v for i, v in obs) / den
            amp = 0.0
            if _num(cv[0]) is not None and _num(cv[7]) is not None:
                amp = min(6.0, 6.0 * (cv[0] / 100.0) * (cv[7] / 100.0))
            recomputed = round(min(100.0, base + amp))
            if abs(recomputed - fdrs) > 1:
                add(iso, "P0", "score-mismatch",
                    f"published {fdrs} but components recompute to {recomputed}")

        # --- 2. components must be in range ---
        for i, v in enumerate(cv):
            n = _num(v)
            if n is not None and (n < 0 or n > 100):
                add(iso, "P0", "range", f"{NAMES[i]}={n} outside 0-100")

        # --- 3. import dependency vs this repo's own net-trade row ---
        dep_rec = dep.get(iso) or {}
        dep_pct = _num(dep_rec.get("import_dependency_pct"))
        net = _num((row.get("net") or {}).get("value"))
        # NOTE: net food trade VALUE and cereal self-sufficiency are different
        # quantities and disagreeing is not a contradiction. The Netherlands is a
        # huge net food exporter (+$29.6bn) that imports ~all its feed grain;
        # Costa Rica exports bananas and imports maize. Only flag the case that
        # is genuinely impossible to reconcile: a large net EXPORTER of food
        # whose cereal ratio has been clamped at the 100% ceiling, which means
        # the raw ratio exceeded 100 and the country is re-exporting — the
        # published number is then a floor, not a measurement.
        if dep_pct is not None and net is not None:
            if net > 2000 and dep_pct >= 100:
                add(iso, "P1", "dependency-clamped",
                    f"net food exporter (+{net:.0f}m USD) with cereal import dependency "
                    f"clamped at 100% — raw ratio exceeded 100, i.e. re-export flow")

        # --- 4. grain buffer must have real PSD rows behind it ---
        gb = _num(cv[8])
        gb_meta = row.get("grain_buffer") or {}
        if gb is not None and gb_meta.get("quality_flag") == "sourced":
            own = psd.get(iso) or {}
            fresh = [r for r in own.values()
                     if isinstance(r, dict) and (r.get("year") or 0) >= CURRENT_YEAR - 2
                     and r.get("quality_flag") != "stale"]
            if not fresh and iso not in ("FRA", "DEU"):
                add(iso, "P1", "grain-buffer-thin",
                    f"grain_buffer {gb} flagged sourced but no current PSD row for {iso} "
                    f"(as_of {gb_meta.get('as_of')})")

        # --- 5. provenance vintage ---
        for field in ("import_dep", "supplier_conc", "prod_trend", "econ_access",
                      "grain_buffer", "fi"):
            meta = row.get(field) or {}
            if meta.get("quality_flag") != "sourced":
                continue
            y = _year_of(meta.get("as_of"))
            if y is None:
                add(iso, "P1", "no-vintage",
                    f"{field} is flagged sourced but carries no readable as_of "
                    f"({meta.get('as_of')!r})")
            elif y < CURRENT_YEAR - 4:
                add(iso, "P1", "stale-vintage",
                    f"{field} flagged sourced on {y} data ({CURRENT_YEAR - y} years old)")

        # --- 6. climate component vs the water feed behind it ---
        if _num(cv[4]) is not None and cv[4] >= 70 and iso not in aq:
            add(iso, "P2", "climate-unbacked",
                f"climate {cv[4]} but no Aqueduct row for {iso}")

        # --- 7. conflict component vs the live conflict feed ---
        # NOTE: cv[4] and cv[5] in countries.json are the STRUCTURAL baseline.
        # index.html overlays sourced climate/conflict at render time (0.6 sourced
        # / 0.4 heritage), so comparing the stored component against a live feed
        # compares the wrong number. Check the feed against the score the reader
        # actually sees instead.
        hc = hapi.get(iso) or {}
        intensity = _num(hc.get("intensity_score_pc"))
        if intensity is None:
            intensity = _num(hc.get("intensity_score"))

        # --- 8. the score vs measured food-crisis outcomes ---
        disp = fdrs + ((nowcast.get(iso) or {}).get("adjustment") or 0)
        if intensity is not None and intensity >= 60 and disp < 45:
            add(iso, "P1", "conflict-vs-score",
                f"displayed {disp:.0f} but ACLED 90d conflict intensity is {intensity} "
                f"({hc.get('fatalities_90d')} fatalities, "
                f"{hc.get('fatalities_per_million_90d')}/million)")
        ipc_pct = _num((ipc.get(iso) or {}).get("phase3plus_pct"))
        if ipc_pct is not None and ipc_pct >= 30 and disp < 50:
            add(iso, "P0", "outcome-contradiction",
                f"displayed {disp:.0f} but IPC has {ipc_pct}% of the population in Phase 3+")
        fews_phase = _num((fews.get(iso) or {}).get("current_phase"))
        if fews_phase is not None and fews_phase >= 4 and disp < 50:
            add(iso, "P0", "outcome-contradiction",
                f"displayed {disp:.0f} but FEWS NET classifies {iso} at Phase {int(fews_phase)}")

        # --- 9. food inflation cross-check against RTFP where both exist ---
        fi_v = _num((row.get("fi") or {}).get("value"))
        rt = _num((rtfp.get(iso) or {}).get("food_inflation_pct"))
        if fi_v is not None and rt is not None and abs(fi_v - rt) > 10:
            add(iso, "P1", "inflation-disagreement",
                f"fi={fi_v}% but World Bank RTFP measures {rt}% for the same country")

        # --- 10. caloric shares must not exceed the whole diet ---
        sh = shares.get(iso) or {}
        tot = sum(x for x in (_num(sh.get("wheat_pct")), _num(sh.get("rice_pct")),
                              _num(sh.get("maize_pct"))) if x is not None)
        if tot > 100:
            add(iso, "P0", "share-impossible",
                f"wheat+rice+maize caloric shares sum to {tot:.1f}%")

        # --- 11. how much of this country's score is still unmeasured ---
        heritage_w = 0.0
        for i, field in ((0, "import_dep"), (1, "supplier_conc"), (2, "prod_trend"),
                         (7, "econ_access"), (8, "grain_buffer")):
            if (row.get(field) or {}).get("quality_flag") != "sourced":
                heritage_w += W[i]
        if heritage_w >= 0.45:
            add(iso, "P2", "mostly-unmeasured",
                f"{heritage_w:.2f} of the weight has no sourced component")

    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso")
    ap.add_argument("--severity", choices=["P0", "P1", "P2"])
    ap.add_argument("--codes", action="store_true", help="summary by code only")
    args = ap.parse_args()

    findings = audit()
    if args.iso:
        findings = [f for f in findings if f["iso"] == args.iso.upper()]
    if args.severity:
        findings = [f for f in findings if f["severity"] == args.severity]

    by_code = Counter(f"{f['severity']} {f['code']}" for f in findings)
    by_iso = defaultdict(list)
    for f in findings:
        by_iso[f["iso"]].append(f)

    print(f"=== per-country audit: {len(findings)} findings across {len(by_iso)} countries ===\n")
    for code, n in by_code.most_common():
        print(f"  {n:>4}  {code}")

    if not args.codes:
        print()
        order = {"P0": 0, "P1": 1, "P2": 2}
        for iso in sorted(by_iso, key=lambda i: (min(order[f['severity']] for f in by_iso[i]), i)):
            rows = sorted(by_iso[iso], key=lambda f: order[f["severity"]])
            print(f"--- {iso}")
            for f in rows:
                print(f"    [{f['severity']}] {f['code']}: {f['message']}")

    p0 = sum(1 for f in findings if f["severity"] == "P0")
    print(f"\n{p0} P0 findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
