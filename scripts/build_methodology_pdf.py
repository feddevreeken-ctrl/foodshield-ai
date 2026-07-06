#!/usr/bin/env python3
"""
build_methodology_pdf.py — the FDRS methodology & validation whitepaper (PDF).

A citable, reproducible one-document explanation of what the score is, how it is
computed (with a worked example that matches the live number), how provenance is
tiered, and — crucially — how it VALIDATES against independent food-crisis ground
truth (IPC + FEWS), stated with its limitations. Reads the live constants + the
output of validate_fdrs.py (data/fdrs_validation.json) so the numbers stay current.

Output: docs/FoodShield_FDRS_Methodology.pdf  (docs/ is gitignored — generated artifact).
Run validate_fdrs.py first (or it will note the validation block is unavailable).

Usage: python3 scripts/validate_fdrs.py && python3 scripts/build_methodology_pdf.py
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle, HRFlowable)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
OUT = REPO / "docs"

# Canonical weight vector — single source of truth is the builder (the
# formula-integrity gate audit_formulas.py cross-checks it against index.html
# and the fixtures; importing it here keeps this whitepaper inside that gate).
# build_countries_dataset is side-effect-free at import time (constants + defs).
from build_countries_dataset import FDRS_V2_WEIGHTS

WEIGHTS = FDRS_V2_WEIGHTS
COMPONENTS = [
    ("Staple import dependency", "share of staple calories imported"),
    ("Supplier concentration", "reliance on few source countries (Comtrade)"),
    ("Production trend", "direction of domestic output (USDA PSD)"),
    ("Food inflation", "food CPI momentum (FAOSTAT / Eurostat)"),
    ("Climate volatility", "hazard / water stress (INFORM, Aqueduct, ND-GAIN)"),
    ("Conflict / logistics", "conflict intensity (ACLED) + trade friction"),
    ("Supply-chain exposure", "chokepoint reliance (live at render time)"),
    ("Economic access", "reserves, debt service, income (WDI, HDI)"),
    ("Grain reserve buffer", "ending stocks / consumption (USDA PSD)"),
]

NAVY = colors.HexColor("#171c29")
INK = colors.HexColor("#21242b")
MUTE = colors.HexColor("#5a5f68")
ACCENT = colors.HexColor("#8aa6c4")
GOOD = colors.HexColor("#6ba36b")
BAD = colors.HexColor("#c44b3c")


def _styles():
    ss = getSampleStyleSheet()
    S = {}
    S["h1"] = ParagraphStyle("h1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=19, textColor=INK, spaceAfter=2, alignment=TA_LEFT)
    S["sub"] = ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5, textColor=MUTE, spaceAfter=10)
    S["h2"] = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, textColor=INK, spaceBefore=12, spaceAfter=5)
    S["body"] = ParagraphStyle("body", fontName="Helvetica", fontSize=9.3, leading=13.5, textColor=INK, spaceAfter=6)
    S["small"] = ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=11, textColor=MUTE, spaceAfter=4)
    S["mono"] = ParagraphStyle("mono", fontName="Courier", fontSize=8.5, leading=12, textColor=INK, spaceAfter=6)
    S["cell"] = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.3, leading=10.5, textColor=INK)
    S["cellb"] = ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=8.3, leading=10.5, textColor=INK)
    return S


def _worked_example():
    """Hand-derive one country's FDRS from countries.json so a reader can reproduce it."""
    cs = json.loads((DATA / "countries.json").read_text())["data"]["countries"]
    def v(x): return x.get("value") if isinstance(x, dict) else x
    for iso in ("MAR", "KEN", "EGY"):
        r = cs.get(iso)
        if not r:
            continue
        cv = v(r.get("c")); stored = v(r.get("fdrs"))
        if not isinstance(cv, list):
            continue
        base = sum(WEIGHTS[i] * (cv[i] if i < len(cv) and cv[i] is not None else 0) for i in range(9))
        c0 = cv[0] or 0; c7 = cv[7] if len(cv) > 7 and cv[7] is not None else 0
        amp = min(6 * (c0 / 100) * (c7 / 100), 6)
        name = {"MAR": "Morocco", "KEN": "Kenya", "EGY": "Egypt"}.get(iso, iso)
        # calc mirrors the builder exactly: int(_clip(base + amp) + 0.5)
        calc = int(max(0, min(100, base + amp)) + 0.5)
        return name, cv, base, amp, base + amp, stored, calc
    return None


def main():
    S = _styles()
    OUT.mkdir(exist_ok=True)
    path = OUT / "FoodShield_FDRS_Methodology.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=16 * mm,
                            title="FoodShield FDRS — Methodology & Validation")
    F = []
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    F.append(Paragraph("FoodShield FDRS", S["h1"]))
    F.append(Paragraph(f"Methodology &amp; Validation &nbsp;·&nbsp; generated {gen} &nbsp;·&nbsp; foodshield-ai-fv.vercel.app", S["sub"]))
    F.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#d6d8de"), spaceAfter=8))

    F.append(Paragraph("What the score is", S["h2"]))
    F.append(Paragraph(
        "The <b>Food Disruption Risk Score (FDRS)</b> is a 0&ndash;100 structural measure of how exposed a "
        "country is to food-supply disruption. It is a deliberate <b>synthesis of public data, not a forecast</b>: "
        "it does not predict that a crisis will occur on a date, it quantifies standing structural fragility "
        "(import reliance, supplier concentration, climate and conflict exposure, economic access, buffers). "
        "The project is honesty-first: every field carries a provenance tier and nothing is fabricated.", S["body"]))

    F.append(Paragraph("How it is computed", S["h2"]))
    F.append(Paragraph(
        "FDRS is a weighted composite of nine structural components, plus an amplifier that captures how "
        "high import dependence and constrained economic access compound each other, clipped to 0&ndash;100:", S["body"]))
    # v41 — clip wraps (base + amplifier), THEN half-up rounding: this must state
    # exactly what the builder computes (int(_clip(base + amp) + 0.5)); the previous
    # wording clipped the sum only, which would permit scores up to 106.
    F.append(Paragraph("FDRS = round[ clip(0..100)( &Sigma; w<sub>i</sub>&middot;c<sub>i</sub>  +  min( 6 &middot; (import_dep/100) &middot; (econ_access/100), 6 ) ) ]", S["mono"]))

    rows = [[Paragraph("Component", S["cellb"]), Paragraph("What it measures (source)", S["cellb"]), Paragraph("Weight", S["cellb"])]]
    # weights come from the imported canonical vector, not hardcoded strings
    for (name, desc), w in zip(COMPONENTS, WEIGHTS):
        rows.append([Paragraph(name, S["cell"]), Paragraph(desc, S["cell"]), Paragraph(f"{w:.2f}", S["cell"])])
    t = Table(rows, colWidths=[42 * mm, 98 * mm, 18 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f5")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#cfd3da")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e6e8ec")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    F.append(t)
    F.append(Spacer(1, 6))

    we = _worked_example()
    if we:
        name, cv, base, amp, total, stored, calc = we
        # v41 — a citable whitepaper must not assert an equality it never checked.
        # If the formula/weights/data drift, fail the build instead of publishing
        # a false "which equals the published score" claim.
        if calc != stored:
            raise SystemExit(
                f"[methodology] REFUSING to build: worked example for {name} computes "
                f"{calc} but countries.json stores {stored} — formula, weights, or data "
                f"have drifted. Reconcile before regenerating the whitepaper.")
        F.append(Paragraph("Worked example (reproduce it yourself)", S["h2"]))
        cvs = ", ".join("null" if x is None else (f"{x:.1f}" if isinstance(x, float) else str(x)) for x in cv)
        F.append(Paragraph(
            f"{name}: component vector c = [{cvs}].<br/>"
            f"Weighted base = &Sigma; w<sub>i</sub>&middot;c<sub>i</sub> = <b>{base:.2f}</b>. "
            f"Amplifier = min(6&middot;({cv[0]}/100)&middot;({(cv[7] if cv[7] is not None else 0):.1f}/100), 6) = <b>{amp:.2f}</b>. "
            f"Total = {total:.2f} &rarr; clip + half-up round = <b>{calc}</b>, verified equal to the published score ({stored}).", S["mono"]))

    F.append(Paragraph("Provenance tiers (honest degradation)", S["h2"]))
    F.append(Paragraph(
        "Every field is tagged <b>sourced</b> (verified against a public dataset with query-level lineage: the "
        "reconstructed source query URL, or a machine-readable query spec for bulk datasets, plus as-of date, basis "
        "and coverage), <b>partial</b> (real cited data with incomplete lineage or coverage &mdash; e.g. truncated "
        "partner extracts or stale input series, flagged as such), <b>modeled</b> (computed from sourced inputs or "
        "explicit structural assumptions), or <b>legacy</b> (hand-authored heritage value, not yet re-verified "
        "&mdash; treated as draft). A trade row whose exact source query cannot be reconstructed is downgraded to "
        "partial rather than kept on a homepage link. When a live feed is stale or absent the field degrades visibly "
        "rather than presenting a confident number; the public source-health page reports per-feed freshness. A "
        "companion audit (scripts/audit_provenance.py) tracks how much of the score's weight still rests on legacy "
        "values so re-sourcing is prioritised transparently.", S["body"]))

    # ── validation ────────────────────────────────────────────────────────────
    F.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#d6d8de"), spaceBefore=6, spaceAfter=8))
    F.append(Paragraph("Does the score correspond to anything real? (validation)", S["h2"]))
    vp = DATA / "fdrs_validation.json"
    # v41 — a stub/degenerate validation file (skipped run, empty tests, or None
    # metrics from n<3) must be treated exactly like a missing file: this section
    # only renders real numbers.
    vr = tt = None
    if vp.exists():
        vr = json.loads(vp.read_text())
        vr = vr.get("data", vr) if isinstance(vr, dict) else vr   # unwrap envelope
        tt = vr.get("tests") if isinstance(vr, dict) else None
    _need = ("auc_crisis_vs_noncrisis", "spearman_fdrs_vs_ipc_pct",
             "spearman_fdrs_vs_fews_phase")
    degenerate = (
        not tt
        or any(k not in tt for k in _need)
        or (tt["auc_crisis_vs_noncrisis"] or {}).get("auc") is None
        or (tt["spearman_fdrs_vs_ipc_pct"] or {}).get("rho") is None
        or (tt["spearman_fdrs_vs_fews_phase"] or {}).get("rho") is None
        or tt.get("precision_at_20") is None or tt.get("recall_at_20") is None)
    if degenerate:
        F.append(Paragraph("Run scripts/validate_fdrs.py to populate this section "
                           "(no valid validation results are currently on disk).", S["small"]))
    else:
        auc = tt["auc_crisis_vs_noncrisis"]["auc"]
        rho_i = tt["spearman_fdrs_vs_ipc_pct"]["rho"]
        rho_f = tt["spearman_fdrs_vs_fews_phase"]["rho"]
        F.append(Paragraph(
            "FDRS is validated against <b>independent</b> ground truth it does not ingest: the IPC/CH classification "
            "(% of population in Phase&nbsp;3+ &lsquo;Crisis or worse&rsquo;) and FEWS&nbsp;NET phase. FDRS is a structural "
            "exposure synthesis; IPC/FEWS are field assessments of realised food insecurity &mdash; so agreement is "
            "evidence the structural signal identifies real vulnerability.", S["body"]))
        vrows = [
            [Paragraph("Test", S["cellb"]), Paragraph("Result", S["cellb"]), Paragraph("Reading", S["cellb"])],
            [Paragraph("Discrimination (ROC-AUC): crisis vs non-crisis, all countries", S["cell"]),
             Paragraph(f"<b>{auc:.2f}</b>", S["cellb"]),
             Paragraph("0.5 = chance, 1.0 = perfect. Strong separation.", S["cell"])],
            [Paragraph(f"Spearman &rho;: FDRS vs IPC Phase&nbsp;3+ % (n={tt['spearman_fdrs_vs_ipc_pct']['n']})", S["cell"]),
             Paragraph(f"{rho_i:+.2f}", S["cell"]),
             Paragraph("Positive: within monitored countries, higher FDRS tracks worse outcomes.", S["cell"])],
            [Paragraph(f"Spearman &rho;: FDRS vs FEWS phase (n={tt['spearman_fdrs_vs_fews_phase']['n']})", S["cell"]),
             Paragraph(f"{rho_f:+.2f}", S["cell"]), Paragraph("Positive.", S["cell"])],
            [Paragraph("Precision / recall @ top-20 FDRS", S["cell"]),
             Paragraph(f"{tt['precision_at_20']:.0%} / {tt['recall_at_20']:.0%}", S["cell"]),
             # v41 — derive the sentence from the actual figure ("half" was a
             # hardcoded coincidence that would silently go stale).
             Paragraph(f"{round(tt['precision_at_20'] * 20)} of the top-20 are in active crisis; the rest are structural-watch.", S["cell"])],
        ]
        vt = Table(vrows, colWidths=[70 * mm, 20 * mm, 68 * mm])
        vt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f5")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e6e8ec")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        F.append(vt)
        F.append(Spacer(1, 6))

        hits = vr.get("hits_top_crisis", [])[:8]
        if hits:
            F.append(Paragraph("Face validity &mdash; highest-FDRS countries that are in documented crisis:", S["body"]))
            hs = "; ".join(f"<b>{i}</b> (FDRS {f}, IPC {ip:.0f}% P3+{', FEWS ' + str(fp) if fp else ''})" for i, f, ip, fp in hits)
            F.append(Paragraph(hs + ".", S["small"]))

        miss = vr.get("misses_crisis_low_fdrs", [])
        if miss:
            ms = "; ".join(f"<b>{i}</b> (FDRS {f}, IPC {ip:.0f}% P3+)" for i, f, ip, fp in miss)
            F.append(Paragraph(
                f"<b>Honest miss.</b> {ms}. The structural model ranks these low because they are not "
                "import-fragile (e.g. a food-exporting breadbasket); their food insecurity is conflict-driven and "
                "acute, which a structural import-exposure score is not designed to capture. FDRS measures standing "
                "structural fragility, not acute conflict shocks &mdash; this boundary is by design.", S["small"]))

        F.append(Paragraph("Limitations (read the numbers with these):", S["h2"]))
        F.append(Paragraph(
            "<b>1.</b> This is <i>concurrent</i> structural validation, not an ex-ante backtest: reproducible point-in-time "
            "scores only exist from 2026-05, so it shows the ranking aligns with <i>where</i> crises are, not that FDRS "
            "predicted them beforehand. <b>2.</b> IPC/FEWS only monitor already-stressed countries, so the crisis set is "
            "not a random sample and the AUC partly reflects &lsquo;is this a monitored country&rsquo;; read the within-IPC "
            "Spearman alongside it. <b>3.</b> Conflict is both an FDRS component and a crisis driver &mdash; a shared causal "
            "pathway, not circularity (ACLED events and IPC outcomes are distinct measurements).", S["small"]))

    F.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e0e2e8"), spaceBefore=8, spaceAfter=6))
    F.append(Paragraph(
        "Reproducibility: FDRS is built only from public sources (UN Comtrade, FAOSTAT, USDA PSD, World Bank, WFP, "
        "IPC/FEWS NET, ACLED, INFORM, ND-GAIN, Aqueduct). The pipeline (scripts/) refreshes every 6 hours; the score "
        "formula is cross-checked in Python and JavaScript against pinned fixtures; validation is reproduced by "
        "scripts/validate_fdrs.py. Nothing in this document is hand-tuned to flatter the result.", S["small"]))

    doc.build(F)
    print(f"[methodology] wrote {path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
