#!/usr/bin/env python3
"""
validate_fdrs.py — honest structural validation of the FDRS score.

QUESTION: does the STRUCTURAL FDRS (built from trade / climate / economic / conflict
data) line up with INDEPENDENTLY-assessed, on-the-ground food-crisis severity?

GROUND TRUTH (not an FDRS input — verified: build_countries_dataset.py ingests none of
these, so this is not circular):
  - IPC (data/ipc.json): % of population in IPC/CH Phase 3+ ("Crisis" or worse).
  - FEWS NET (data/fews.json): current IPC-compatible phase 1–5.
Both are field assessments of realised food insecurity; FDRS is a structural exposure
synthesis. Agreement between them is evidence the structural signal identifies real
vulnerability — NOT proof of ex-ante prediction (see LIMITATIONS in the printout).

WHICH SCORE IS BEING VALIDATED — the awkward part, stated up front.
There is not one FDRS per country, there are three, and they disagree:
  1. DISPLAYED       — what a user actually reads off the site. index.html computes
                       fdrsV2(c.c, liveSCE) and then applies the nowcast overlay at
                       index.html:16168 (`c.fdrs = clip(round(structural + adj), 0, 100)`).
                       NOT REPRODUCIBLE OFFLINE: the live supply-chain-exposure input
                       (component c[6]) is computed in the browser and never persisted
                       to countries.json, so no script in this repo can recompute it.
  2. STRUCTURAL      — countries.json `fdrs`. Clean, persisted, reproducible. This is
                       what earlier versions of this script validated, and it stays the
                       headline number for continuity.
  3. STRUCTURAL+NOWCAST — structural + data/nowcast.json[iso].adjustment, clipped and
                       rounded exactly as index.html does. This is an APPROXIMATION of
                       the displayed score, off by roughly 2–7 points on crisis countries
                       (because of the missing c[6]), not a reconstruction of it.
This script reports tiers 2 and 3 with full metrics and labels tier 3 honestly as an
approximation. Tier 1 is out of reach offline and is NOT silently substituted.

It also reports PER-COMPONENT rank correlation against IPC, so the reader can see which
of the nine weighted inputs actually carry signal against realised crisis — including the
ones that carry none. Read the caveats before doing anything with those numbers.

No third-party stats libs: Spearman ρ and ROC-AUC (Mann–Whitney) are implemented here.

Usage: python3 scripts/validate_fdrs.py   → prints report, writes data/fdrs_validation.json
"""
import json
from pathlib import Path

from _common import DATA_DIR, write_json

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"


def _v(x):
    return x.get("value") if isinstance(x, dict) else x


def _load(name):
    p = DATA / f"{name}.json"
    if not p.exists():
        return {}
    o = json.loads(p.read_text())
    return (o.get("data") if isinstance(o, dict) else o) or {}


def _ranks(xs):
    """Average (fractional) ranks, ties averaged."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = sum((a[i] - ma) ** 2 for i in range(n)) ** 0.5
    db = sum((b[i] - mb) ** 2 for i in range(n)) ** 0.5
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def spearman(x, y):
    return _pearson(_ranks(x), _ranks(y))


def auc(scores, labels):
    """ROC-AUC via Mann–Whitney: P(score_pos > score_neg), ties count 0.5."""
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else (0.5 if p == n else 0.0)
    return wins / (len(pos) * len(neg))


# The nine FDRS v2 components and their weights, mirrored from index.html:15968-15980.
# MIRRORED, NOT IMPORTED — if the weights in index.html ever change, this list silently
# goes stale. It is here only to LABEL the diagnostics; nothing in this script feeds back
# into scoring, and this script must never be the place a weight is edited.
COMPONENTS = [
    ("c0", "import-dependency", 0.23),
    ("c1", "supplier-concentration", 0.16),
    ("c2", "production-trend", 0.11),
    ("c3", "food-inflation", 0.09),
    ("c4", "climate", 0.09),
    ("c5", "conflict/logistics", 0.08),
    ("c6", "supply-chain-exposure", 0.06),
    ("c7", "economic-access", 0.12),
    ("c8", "grain-reserve-buffer", 0.06),
]

CRISIS_IPC = 20.0
CRISIS_DEF = "IPC phase3+ >= 20% OR FEWS phase >= 3"


def _crisis(iso, ipc_pct, fews_phase):
    return (ipc_pct.get(iso, 0) >= CRISIS_IPC) or (fews_phase.get(iso, 0) >= 3)


def tier_metrics(fdrs, ipc_pct, fews_phase):
    """All headline metrics for ONE score tier. Same definitions for every tier, so the
    tiers are directly comparable — that comparability is the whole point of computing
    more than one. Returns (tests_dict, ranked_list, labels_by_iso)."""
    tests = {}

    common = sorted(set(fdrs) & set(ipc_pct))
    tests["spearman_fdrs_vs_ipc_pct"] = {
        "rho": spearman([fdrs[i] for i in common], [ipc_pct[i] for i in common]),
        "n": len(common),
    }
    commonf = sorted(set(fdrs) & set(fews_phase))
    tests["spearman_fdrs_vs_fews_phase"] = {
        "rho": spearman([fdrs[i] for i in commonf], [fews_phase[i] for i in commonf]),
        "n": len(commonf),
    }

    labels = {iso: _crisis(iso, ipc_pct, fews_phase) for iso in fdrs}
    n_pos = sum(labels.values())
    isos = sorted(fdrs)
    tests["auc_crisis_vs_noncrisis"] = {
        "auc": auc([fdrs[i] for i in isos], [labels[i] for i in isos]),
        "n_crisis": n_pos, "n_total": len(isos), "crisis_def": CRISIS_DEF,
    }

    ranked = sorted(((i, fdrs[i], labels[i]) for i in isos), key=lambda t: t[1], reverse=True)
    for N in (10, 20, 30):
        hits = sum(1 for _, _, l in ranked[:N] if l)
        tests[f"precision_at_{N}"] = round(hits / N, 3)
        tests[f"recall_at_{N}"] = round(hits / n_pos, 3) if n_pos else None
    return tests, ranked, labels


def component_diagnostics(countries, ipc_pct):
    """Per-component Spearman vs IPC phase3+ %. Diagnostic only — see the CAVEATS block
    for why this is emphatically NOT a reweighting instruction."""
    out = []
    for idx, (key, name, weight) in enumerate(COMPONENTS):
        xs, ys = [], []
        for iso, row in countries.items():
            if iso.startswith("US-") or iso not in ipc_pct:
                continue
            cv = _v(row.get("c"))
            if not isinstance(cv, list) or idx >= len(cv):
                continue
            v = cv[idx]
            if isinstance(v, (int, float)):
                xs.append(v)
                ys.append(ipc_pct[iso])
        rho = spearman(xs, ys) if len(xs) >= 3 else None
        out.append({"component": key, "name": name, "weight": weight,
                    "rho_vs_ipc_pct": None if rho is None else round(rho, 4),
                    "n": len(xs)})
    return out


# The caveats that must travel with every number this script emits. Kept as data so the
# printout and the JSON `notes` cannot drift apart — a caveat that only exists in the
# terminal is a caveat nobody downstream ever reads.
CAVEATS = [
    "TIER: the headline metrics describe the STRUCTURAL score (countries.json fdrs). "
    "That is NOT the number the site displays. The displayed score adds live supply-chain "
    "exposure and the nowcast overlay, and every country differs between the two.",
    "The structural_plus_nowcast tier is an APPROXIMATION of the displayed score, not a "
    "reconstruction: component c[6] (supply-chain exposure) is computed in the browser and "
    "never persisted to countries.json, so the displayed score cannot be reproduced offline. "
    "Expect a residual of roughly 2-7 points on high-scoring countries.",
    "c7 (economic access, weight 0.12) has approximately ZERO-to-NEGATIVE rank correlation "
    "with realised IPC severity within monitored countries. The third-largest weight in the "
    "composite carries no measurable signal against this ground truth. State that plainly "
    "rather than quoting the composite AUC alone.",
    "Per-component rho vs IPC is NOT a mandate to reweight. FDRS measures STRUCTURAL "
    "exposure; IPC measures REALISED acute crisis. They are different quantities. Fitting "
    "the weights to IPC on n~54 monitored countries would overfit, and would convert FDRS "
    "into an IPC nowcast — a different product, and one that already exists publicly.",
]


def main():
    countries = _load("countries").get("countries") or {}
    ipc = _load("ipc")
    fews = _load("fews")

    # structural FDRS per ISO (countries.json fdrs is the structural baseline; the live
    # nowcast overlay is applied only at render time, so this is the clean structural score)
    fdrs = {}
    for iso, row in countries.items():
        if iso.startswith("US-"):
            continue
        f = _v(row.get("fdrs"))
        if isinstance(f, (int, float)):
            fdrs[iso] = f

    # IPC phase3+ % per ISO
    ipc_pct = {iso: d.get("phase3plus_pct") for iso, d in ipc.items()
               if isinstance(d, dict) and isinstance(d.get("phase3plus_pct"), (int, float))}
    fews_phase = {iso: d.get("current_phase") for iso, d in fews.items()
                  if isinstance(d, dict) and isinstance(d.get("current_phase"), (int, float))}

    # Honest early bail: with no FDRS scores or no ground truth at all, none of the
    # metrics below are computable (and the top-N/percentile code would crash on
    # empty lists). Write an explicit skipped envelope instead of a fake result.
    if not fdrs or (not ipc_pct and not fews_phase):
        reason = ("no FDRS scores loaded from countries.json" if not fdrs
                  else "no IPC/FEWS ground truth loaded (ipc.json / fews.json empty or missing)")
        report = {"n_countries": len(fdrs), "tests": {},
                  "status": "insufficient_ground_truth", "reason": reason}
        write_json(
            "fdrs_validation.json", report,
            source="FoodShield FDRS structural validation vs IPC + FEWS NET",
            notes=f"Validation SKIPPED — insufficient ground truth: {reason}. No metrics computed.",
            status="insufficient_ground_truth",
        )
        print("FDRS STRUCTURAL VALIDATION — SKIPPED")
        print(f"  insufficient ground truth: {reason}")
        print("  Wrote data/fdrs_validation.json with _meta.status=insufficient_ground_truth.")
        return

    report = {"n_countries": len(fdrs), "tests": {}, "status": "ok"}

    # ── TIER 1: structural (countries.json). Headline, for continuity. ────────
    # Crisis-positive = IPC phase3+ >= 20% of population, OR FEWS phase >= 3.
    # These land in report["tests"] exactly as before — every existing key keeps its
    # position and its meaning, so anything already reading this file keeps working.
    tests, ranked, labels = tier_metrics(fdrs, ipc_pct, fews_phase)
    report["tests"] = tests
    n_pos = tests["auc_crisis_vs_noncrisis"]["n_crisis"]

    # ── TIER 2: structural + nowcast — approximation of the DISPLAYED score. ──
    # Reproduces index.html:16168 (`clip(round(structural + adj), 0, 100)`) as closely as
    # offline data allows. It is NOT the displayed score: c[6] is missing (see CAVEATS).
    # Countries with no nowcast row keep their structural score, matching the site's
    # `else` branch, so this tier covers the same country set as the structural tier.
    nowcast = _load("nowcast")
    fdrs_nc, n_adjusted = {}, 0
    for iso, f in fdrs.items():
        adj = (nowcast.get(iso) or {}).get("adjustment") if isinstance(nowcast.get(iso), dict) else None
        if isinstance(adj, (int, float)):
            fdrs_nc[iso] = max(0, min(100, round(f + adj)))
            n_adjusted += 1
        else:
            fdrs_nc[iso] = f
    tests_nc, _, _ = tier_metrics(fdrs_nc, ipc_pct, fews_phase)

    deltas = [abs(fdrs_nc[i] - fdrs[i]) for i in fdrs]
    n_diff = sum(1 for d in deltas if d)

    report["tiers"] = {
        "structural": {
            "label": "STRUCTURAL — countries.json fdrs (headline; reproducible)",
            "is_displayed_score": False,
            "tests": tests,
        },
        "structural_plus_nowcast": {
            "label": ("STRUCTURAL + NOWCAST — APPROXIMATION of the displayed score, "
                      "accurate to within ~2-7 points"),
            "is_displayed_score": False,
            "approximates_displayed": True,
            "approximation_error_note": (
                "Live supply-chain exposure (component c[6]) is computed in the browser and "
                "never persisted to countries.json, so the displayed score cannot be "
                "reproduced exactly offline."),
            "n_countries_with_nowcast_adjustment": n_adjusted,
            "n_countries_differing_from_structural": n_diff,
            "mean_abs_delta_vs_structural": round(sum(deltas) / len(deltas), 2) if deltas else None,
            "tests": tests_nc,
        },
        "displayed": {
            "label": "DISPLAYED — the score users actually see on the site",
            "is_displayed_score": True,
            "status": "not_computable_offline",
            "reason": ("index.html computes fdrsV2(c.c, liveSCE) with a browser-computed "
                       "supply-chain-exposure input that is never written to countries.json, "
                       "then applies the nowcast overlay at index.html:16168. No script in "
                       "this repo can recompute it; structural_plus_nowcast is the closest "
                       "offline proxy and is reported above as an approximation."),
            "tests": None,
        },
    }

    # ── PER-COMPONENT DIAGNOSTICS ────────────────────────────────────────────
    # Which of the nine weighted inputs actually track realised crisis severity — and
    # which do not. Reported alongside each component's weight precisely so a large
    # weight sitting on a zero-signal component cannot hide inside the composite.
    report["component_diagnostics"] = {
        "note": ("Spearman rho of each raw component vs IPC phase3+ %, within IPC-monitored "
                 "countries. DIAGNOSTIC ONLY — see caveats; this is not a reweighting brief."),
        "components": component_diagnostics(countries, ipc_pct),
    }
    report["caveats"] = CAVEATS

    # ── hits + misses (honesty: show where it fails, not just where it wins) ──
    # Computed on the structural tier, consistent with the headline metrics.
    isos = [i for i, _, _ in ranked]
    crisis_isos = [iso for iso in fdrs if labels[iso]]
    crisis_isos.sort(key=lambda i: fdrs[i], reverse=True)
    hits = [(i, fdrs[i], round(ipc_pct.get(i, 0), 1), fews_phase.get(i)) for i in crisis_isos]

    # misses: crisis countries whose FDRS ranks LOW globally. lo_cut is the 33rd
    # percentile of FDRS across ALL countries (not the crisis set) — a crisis
    # country scoring at/below the global bottom-third cutoff is an honest miss.
    fdrs_sorted = sorted(fdrs.values())
    lo_cut = fdrs_sorted[len(fdrs_sorted) // 3]
    misses = [(i, fdrs[i], round(ipc_pct.get(i, 0), 1), fews_phase.get(i))
              for i in crisis_isos if fdrs[i] <= lo_cut]

    # high-FDRS but NOT in a current crisis — structural early-warning candidates (not
    # necessarily false positives: high structural fragility that hasn't yet realised)
    watch = [(iso, f) for iso, f, l in ranked[:20] if not l][:8]

    report["hits_top_crisis"] = hits[:15]
    report["misses_crisis_low_fdrs"] = misses
    report["structural_watch_high_fdrs_no_current_crisis"] = watch

    def _na(x):
        return "n/a" if x is None else x

    write_json(
        "fdrs_validation.json", report,
        source="FoodShield FDRS structural validation vs IPC + FEWS NET",
        notes=(f"HEADLINE TIER = STRUCTURAL (countries.json fdrs), not the displayed score. "
               f"ROC-AUC {_na(report['tests']['auc_crisis_vs_noncrisis']['auc'])} (crisis vs non-crisis); "
               f"Spearman vs IPC {_na(report['tests']['spearman_fdrs_vs_ipc_pct']['rho'])}. "
               f"structural_plus_nowcast tier: AUC {_na(tests_nc['auc_crisis_vs_noncrisis']['auc'])}, "
               f"Spearman vs IPC {_na(tests_nc['spearman_fdrs_vs_ipc_pct']['rho'])}. "
               "Independent ground truth (not an FDRS input). Concurrent structural validation, "
               "not an ex-ante backtest — see LIMITATIONS in scripts/validate_fdrs.py. CAVEATS: "
               + " ".join(CAVEATS)),
        status="ok",
    )

    # ── printout ──────────────────────────────────────────────────────────────
    # Everything that can compute has computed and the JSON is written by this
    # point; every metric below may legitimately be None (e.g. no crisis-positive
    # countries → recall undefined; n<3 → rho undefined), so every format is
    # guarded — the printout must never be able to crash.
    def pct(x):
        return "—" if x is None else f"{x:+.2f}" if isinstance(x, float) and abs(x) <= 1 else str(x)

    def as_pct(x):
        return "—" if x is None else f"{x:.0%}"

    def rho2(x):
        return "   —  " if x is None else f"{x:+.4f}"

    print("FDRS VALIDATION  (independent ground truth: IPC + FEWS NET)")
    print("=" * 74)
    t = report["tests"]

    print("WHICH SCORE IS THIS?  Three FDRS values exist per country. They disagree.")
    print("  DISPLAYED             what users see. NOT COMPUTABLE OFFLINE (live c[6] not persisted).")
    print("  STRUCTURAL            countries.json fdrs. Headline below, for continuity.")
    print("  STRUCTURAL+NOWCAST    approximation of DISPLAYED, off by ~2-7 pts. Reported below.")
    print(f"  structural vs structural+nowcast: {n_diff}/{len(fdrs)} countries differ, "
          f"mean |delta| {report['tiers']['structural_plus_nowcast']['mean_abs_delta_vs_structural']}")

    print("\nPER-TIER METRICS")
    print(f"  {'tier':<22} {'AUC':>8} {'rho_IPC':>9} {'rho_FEWS':>9} {'p@10':>6} {'p@20':>6} {'p@30':>6}")
    for key in ("structural", "structural_plus_nowcast"):
        tt = report["tiers"][key]["tests"]
        print(f"  {key:<22} {rho2(tt['auc_crisis_vs_noncrisis']['auc']):>8} "
              f"{rho2(tt['spearman_fdrs_vs_ipc_pct']['rho']):>9} "
              f"{rho2(tt['spearman_fdrs_vs_fews_phase']['rho']):>9} "
              f"{as_pct(tt['precision_at_10']):>6} {as_pct(tt['precision_at_20']):>6} "
              f"{as_pct(tt['precision_at_30']):>6}")
    print(f"  {'displayed':<22} {'not computable offline — see caveats'}")
    print("  recall@N (structural / structural+nowcast):")
    for N in (10, 20, 30):
        print(f"    top-{N}: {as_pct(t[f'recall_at_{N}'])} / {as_pct(tests_nc[f'recall_at_{N}'])}")

    print("\nHEADLINE TIER — STRUCTURAL (countries.json fdrs)")
    print(f"Rank correlation (Spearman rho), higher = FDRS tracks realised crisis severity:")
    print(f"  FDRS vs IPC phase3+ %:  rho = {pct(t['spearman_fdrs_vs_ipc_pct']['rho'])}  "
          f"(n={t['spearman_fdrs_vs_ipc_pct']['n']} IPC-monitored countries)")
    print(f"  FDRS vs FEWS phase:     rho = {pct(t['spearman_fdrs_vs_fews_phase']['rho'])}  "
          f"(n={t['spearman_fdrs_vs_fews_phase']['n']})")
    print(f"\nDiscrimination — can FDRS tell crisis from non-crisis across all {t['auc_crisis_vs_noncrisis']['n_total']} countries?")
    print(f"  ROC-AUC = {pct(t['auc_crisis_vs_noncrisis']['auc'])}   "
          f"(0.5=chance, 1.0=perfect; {t['auc_crisis_vs_noncrisis']['n_crisis']} crisis countries)")
    print(f"  crisis def: {t['auc_crisis_vs_noncrisis']['crisis_def']}")
    for N in (10, 20, 30):
        print(f"  top-{N} FDRS:  precision {as_pct(t[f'precision_at_{N}'])}  ·  recall {as_pct(t[f'recall_at_{N}'])}")

    print("\nPER-COMPONENT SIGNAL — Spearman rho of each raw component vs IPC phase3+ %")
    print("  (diagnostic only — read the CAVEATS below before touching any weight)")
    print(f"  {'':<4} {'component':<24} {'weight':>7} {'rho_vs_IPC':>11} {'n':>5}")
    for row in report["component_diagnostics"]["components"]:
        r = row["rho_vs_ipc_pct"]
        flag = ""
        if r is None:
            flag = "  ← NO DATA"
        elif abs(r) < 0.10 and row["weight"] >= 0.10:
            flag = "  ← heavy weight, no measurable signal"
        print(f"  {row['component']:<4} {row['name']:<24} {row['weight']:>7.2f} "
              f"{rho2(r):>11} {row['n']:>5}{flag}")

    print(f"\nHITS — highest-FDRS countries that ARE in documented crisis (top 10):")
    for iso, f, ip, fp in hits[:10]:
        print(f"  {iso}  FDRS {f:>3}  ·  IPC phase3+ {ip:>4}%  ·  FEWS {fp if fp else '—'}")
    print(f"\nMISSES — crisis countries FDRS ranks LOW (honest failure cases): "
          f"{'none' if not misses else ''}")
    for iso, f, ip, fp in misses:
        print(f"  {iso}  FDRS {f:>3}  ·  IPC phase3+ {ip:>4}%  ·  FEWS {fp if fp else '—'}")
    print(f"\nSTRUCTURAL WATCH — high FDRS, no current IPC/FEWS crisis (early-warning candidates,")
    print(f"  NOT false positives — structural fragility not yet realised):")
    for iso, f in watch:
        print(f"  {iso}  FDRS {f}")
    print("\nCAVEATS (these travel with the numbers — do not quote a metric without them):")
    for c in CAVEATS:
        words, line = c.split(), "  •"
        for w in words:
            if len(line) + len(w) + 1 > 88:
                print(line)
                line = "   "
            line += " " + w
        print(line)

    print("\nLIMITATIONS (state these wherever the numbers are shown):")
    print("  • This is CONCURRENT structural validation, not an ex-ante backtest: the git")
    print("    history only reaches ~2026-05, so no point-in-time score exists for older")
    print("    crises. It shows the structural ranking aligns with WHERE crises are, not")
    print("    that FDRS predicted them before they happened.")
    print("  • IPC/FEWS only monitor already-stressed countries, so the crisis set is not a")
    print("    random sample; AUC across all countries partly reflects 'is this a monitored")
    print("    country'. Read the within-IPC Spearman alongside the AUC.")
    print("  • Conflict is both an FDRS component and a crisis driver — shared causal pathway,")
    print("    not circularity (ACLED events vs IPC outcomes are distinct measurements).")
    print("\nWrote data/fdrs_validation.json")


if __name__ == "__main__":
    main()
