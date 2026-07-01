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

No third-party stats libs: Spearman ρ and ROC-AUC (Mann–Whitney) are implemented here.

Usage: python3 scripts/validate_fdrs.py   → prints report, writes data/fdrs_validation.json
"""
import json
from pathlib import Path

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

    report = {"n_countries": len(fdrs), "tests": {}}

    # 1) rank correlation within IPC-monitored countries
    common = sorted(set(fdrs) & set(ipc_pct))
    rho_ipc = spearman([fdrs[i] for i in common], [ipc_pct[i] for i in common])
    report["tests"]["spearman_fdrs_vs_ipc_pct"] = {"rho": rho_ipc, "n": len(common)}

    commonf = sorted(set(fdrs) & set(fews_phase))
    rho_fews = spearman([fdrs[i] for i in commonf], [fews_phase[i] for i in commonf])
    report["tests"]["spearman_fdrs_vs_fews_phase"] = {"rho": rho_fews, "n": len(commonf)}

    # 2) discrimination: can FDRS separate crisis from non-crisis across ALL countries?
    #    crisis-positive = IPC phase3+ >= 20% of population, OR FEWS phase >= 3.
    CRISIS_IPC = 20.0
    labels, scores, isos = [], [], []
    for iso, f in fdrs.items():
        crisis = (ipc_pct.get(iso, 0) >= CRISIS_IPC) or (fews_phase.get(iso, 0) >= 3)
        labels.append(crisis)
        scores.append(f)
        isos.append(iso)
    n_pos = sum(labels)
    a = auc(scores, labels)
    report["tests"]["auc_crisis_vs_noncrisis"] = {
        "auc": a, "n_crisis": n_pos, "n_total": len(labels),
        "crisis_def": "IPC phase3+ >= 20% OR FEWS phase >= 3",
    }

    # precision/recall @ N (top-N by FDRS globally)
    ranked = sorted(zip(isos, scores, labels), key=lambda t: t[1], reverse=True)
    for N in (10, 20, 30):
        topN = ranked[:N]
        hits = sum(1 for _, _, l in topN if l)
        report["tests"][f"precision_at_{N}"] = round(hits / N, 3)
        report["tests"][f"recall_at_{N}"] = round(hits / n_pos, 3) if n_pos else None

    # 3) hits + misses (honesty: show where it fails, not just where it wins)
    crisis_isos = [iso for iso in fdrs if labels[isos.index(iso)]]
    crisis_isos.sort(key=lambda i: fdrs[i], reverse=True)
    hits = [(i, fdrs[i], round(ipc_pct.get(i, 0), 1), fews_phase.get(i)) for i in crisis_isos]

    # misses: crisis countries FDRS ranks LOW (bottom third of crisis set)
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

    (DATA / "fdrs_validation.json").write_text(json.dumps(report, indent=2))

    # ── printout ──────────────────────────────────────────────────────────────
    def pct(x):
        return "—" if x is None else f"{x:+.2f}" if isinstance(x, float) and abs(x) <= 1 else str(x)

    print("FDRS STRUCTURAL VALIDATION  (independent ground truth: IPC + FEWS NET)")
    print("=" * 74)
    t = report["tests"]
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
        print(f"  top-{N} FDRS:  precision {t[f'precision_at_{N}']:.0%}  ·  recall {t[f'recall_at_{N}']:.0%}")
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
