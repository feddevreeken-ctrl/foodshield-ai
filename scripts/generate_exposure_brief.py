#!/usr/bin/env python3
"""
generate_exposure_brief.py — one-command shareable PDF exposure brief for a country.

Reads the SAME live data the dashboard ships (data/countries.json, trade_restrictions.json,
fdrs_history.json) plus the canonical ISO->name map embedded in index.html, and renders a
clean 1-page PDF: the FDRS score + band, the structural risk drivers (with weights), supply
exposure (top suppliers + imported commodities), any export restrictions on record affecting
the country, and the score trajectory from the recorded history.

HONESTY: every number traces to a shipped data file. Nothing is invented for the brief.
The score is a structural synthesis (weighted composite), NOT a forecast — the footer says so.
Restriction linkage is a STRUCTURAL proxy (country imports the restricted commodity AND the
restricting exporter is a top supplier), framed as exposure, not realized impact.

Usage:
    python3 scripts/generate_exposure_brief.py EGY
    python3 scripts/generate_exposure_brief.py EGY SDN NGA      # several at once
    python3 scripts/generate_exposure_brief.py --all-top 10     # top-N by FDRS

Output: briefs/FoodShield_Exposure_Brief_<ISO>.pdf
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as _canvas

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
OUT = REPO / "briefs"

# ── FDRS bands — MUST match index.html riskLabel()/riskColor() ──────────────────
BANDS = [
    (25, "Resilient",  (0.42, 0.64, 0.42)),
    (50, "Exposed",    (0.79, 0.66, 0.34)),
    (75, "Dependent",  (0.82, 0.48, 0.24)),
    (88, "Vulnerable", (0.90, 0.54, 0.47)),
    (100, "Severe",    (0.88, 0.47, 0.43)),
]
WEIGHTS = [0.23, 0.16, 0.11, 0.09, 0.09, 0.08, 0.06, 0.12, 0.06]
COMP_LABELS = [
    "Import dependency", "Supplier concentration", "Production trend", "Food inflation",
    "Climate stress", "Conflict", "Supply-chain exposure", "Economic access", "Grain buffer",
]

NAVY = (0.09, 0.11, 0.16)
INK = (0.13, 0.14, 0.17)
MUTE = (0.42, 0.45, 0.50)
HAIR = (0.85, 0.86, 0.88)
TRACK = (0.92, 0.93, 0.94)


def band(score):
    for hi, label, col in BANDS:
        if score <= hi:
            return label, col
    return BANDS[-1][1], BANDS[-1][2]


def load_names():
    """ISO -> (name, region) from the canonical COUNTRIES seed in index.html."""
    html = (REPO / "index.html").read_text(encoding="utf-8")
    names = {}
    for m in re.finditer(r'\{iso:"([A-Z][A-Z0-9\-]+)",name:"([^"]+)",region:"([^"]+)"', html):
        names[m.group(1)] = (m.group(2), m.group(3))
    return names


def _v(x):
    return x.get("value") if isinstance(x, dict) else x


def load_country(iso):
    obj = json.loads((DATA / "countries.json").read_text())
    row = obj["data"]["countries"].get(iso)
    return row


def load_restrictions():
    p = DATA / "trade_restrictions.json"
    if not p.exists():
        return []
    o = json.loads(p.read_text())
    d = o.get("data") if isinstance(o, dict) else o
    return d if isinstance(d, list) else []


def load_history(iso):
    p = DATA / "fdrs_history.json"
    if not p.exists():
        return []
    o = json.loads(p.read_text())
    d = (o.get("data") if isinstance(o, dict) else o) or {}
    rec = d.get(iso) or {}
    pts = [(k, rec[k].get("fdrs")) for k in sorted(rec) if isinstance(rec[k].get("fdrs"), (int, float))]
    return pts


def restrictions_affecting(iso, row, names):
    """Structural proxy: country imports the restricted commodity AND the restricting
    exporter is among its top suppliers. Honest exposure linkage, not realized impact."""
    out = []
    imports = [str(x).lower() for x in (_v(row.get("imports")) or [])]
    suppliers = [str(x).lower() for x in (_v(row.get("suppliers")) or [])]
    for r in load_restrictions():
        if not isinstance(r, dict):
            continue
        commodity = str(r.get("commodity") or "").lower()
        ex_iso = r.get("iso") or r.get("exporter")
        ex_name = names.get(ex_iso, (ex_iso or "", ""))[0].lower() if ex_iso else ""
        if not commodity:
            continue
        imports_it = any(commodity in imp or imp in commodity for imp in imports)
        from_them = bool(ex_iso) and (str(ex_iso).lower() in suppliers or (ex_name and ex_name in suppliers))
        if imports_it and (from_them or not ex_iso):
            out.append(r)
    return out


# ── drawing ─────────────────────────────────────────────────────────────────────
def draw(c, iso, row, names):
    W, H = A4
    name, region = names.get(iso, (iso, ""))
    fdrs = int(_v(row.get("fdrs")) or 0)
    f2030 = _v(row.get("f2030"))
    label, col = band(fdrs)
    cv = _v(row.get("c")) or []

    def text(x, y, s, size=9, color=INK, font="Helvetica", align="l"):
        c.setFont(font, size)
        c.setFillColorRGB(*color)
        if align == "r":
            c.drawRightString(x, y, s)
        elif align == "c":
            c.drawCentredString(x, y, s)
        else:
            c.drawString(x, y, s)

    ML, MR = 18 * mm, W - 18 * mm

    # header band
    c.setFillColorRGB(*NAVY)
    c.rect(0, H - 24 * mm, W, 24 * mm, fill=1, stroke=0)
    text(ML, H - 12 * mm, "FoodShield AI", 13, (1, 1, 1), "Helvetica-Bold")
    text(ML, H - 17.5 * mm, "COUNTRY EXPOSURE BRIEF", 8, (0.68, 0.72, 0.80), "Helvetica-Bold")
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    text(MR, H - 12 * mm, "Structural food-disruption synthesis", 8, (0.68, 0.72, 0.80), align="r")
    text(MR, H - 17.5 * mm, f"Generated {gen} UTC", 8, (0.68, 0.72, 0.80), align="r")

    y = H - 38 * mm
    text(ML, y, name, 22, INK, "Helvetica-Bold")
    text(ML, y - 6.5 * mm, region, 10, MUTE)

    # score block (right)
    c.setFillColorRGB(*col)
    c.setFont("Helvetica-Bold", 40)
    c.drawRightString(MR, y - 3 * mm, str(fdrs))
    text(MR - 26 * mm, y - 1 * mm, "FDRS", 8, MUTE, "Helvetica-Bold", "r")
    # band chip
    cw = 30 * mm
    c.setFillColorRGB(col[0], col[1], col[2])
    c.setFillAlpha(0.14); c.roundRect(MR - cw, y - 12 * mm, cw, 6 * mm, 2, fill=1, stroke=0); c.setFillAlpha(1)
    text(MR - cw / 2, y - 10 * mm, label.upper(), 8, col, "Helvetica-Bold", "c")
    if isinstance(f2030, (int, float)):
        d = int(f2030) - fdrs
        arrow = "▲" if d > 0 else ("▼" if d < 0 else "▬")
        text(MR, y - 16.5 * mm, f"2030 modeled {int(f2030)}  {arrow}{'+' if d>0 else ''}{d}", 8.5, MUTE, align="r")

    y -= 24 * mm
    c.setStrokeColorRGB(*HAIR); c.setLineWidth(0.6); c.line(ML, y, MR, y)
    y -= 8 * mm

    # ── structural risk drivers ──
    text(ML, y, "STRUCTURAL RISK DRIVERS", 9, INK, "Helvetica-Bold")
    text(MR, y, "value · weight", 7.5, MUTE, align="r")
    y -= 6 * mm
    rows = []
    for i in range(min(9, len(cv))):
        v = cv[i]
        if v is None:
            continue
        rows.append((COMP_LABELS[i], float(v), WEIGHTS[i], WEIGHTS[i] * float(v)))
    rows.sort(key=lambda t: t[3], reverse=True)
    barx, barw = ML + 46 * mm, 78 * mm
    for lab, v, w, _contrib in rows:
        text(ML, y, lab, 8.5, INK)
        c.setFillColorRGB(*TRACK); c.roundRect(barx, y - 1 * mm, barw, 3 * mm, 1, fill=1, stroke=0)
        fillc = band(v)[1]
        c.setFillColorRGB(*fillc); c.roundRect(barx, y - 1 * mm, barw * min(v, 100) / 100, 3 * mm, 1, fill=1, stroke=0)
        text(MR, y, f"{v:.0f}  ·  {w:.2f}", 8, MUTE, "Helvetica", "r")
        y -= 6.4 * mm
    # amplifier
    c0 = cv[0] if len(cv) > 0 and cv[0] is not None else 0
    c7 = cv[7] if len(cv) > 7 and cv[7] is not None else 0
    amp = min(6 * (c0 / 100) * (c7 / 100), 6)
    text(ML, y, f"+ import×access amplifier: +{amp:.1f}  (import-dep {c0:.0f} × econ-access {c7:.0f})", 7.5, MUTE, "Helvetica-Oblique")
    y -= 9 * mm

    # ── supply exposure ──
    text(ML, y, "SUPPLY EXPOSURE", 9, INK, "Helvetica-Bold")
    y -= 6 * mm
    sup = _v(row.get("suppliers")) or []
    pct = _v(row.get("supPct")) or []
    text(ML, y, "Top suppliers", 8, MUTE, "Helvetica-Bold")
    y -= 5.5 * mm
    sbx, sbw = ML + 40 * mm, 60 * mm
    for i, s in enumerate(sup[:5]):
        sname = names.get(s, (s, ""))[0] if isinstance(s, str) else str(s)
        share = pct[i] if i < len(pct) else None
        text(ML, y, sname, 8.5, INK)
        if isinstance(share, (int, float)):
            c.setFillColorRGB(*TRACK); c.roundRect(sbx, y - 1 * mm, sbw, 3 * mm, 1, fill=1, stroke=0)
            fc = (0.82, 0.30, 0.24) if share >= 50 else (0.82, 0.48, 0.24)
            c.setFillColorRGB(*fc); c.roundRect(sbx, y - 1 * mm, sbw * min(share, 100) / 100, 3 * mm, 1, fill=1, stroke=0)
            text(MR, y, f"{int(share)}%", 8, MUTE, "Helvetica", "r")
        y -= 6 * mm
    imports = _v(row.get("imports")) or []
    if imports:
        y -= 1 * mm
        text(ML, y, "Key imports: " + ", ".join(str(x) for x in imports[:8]), 8, MUTE)
        y -= 8 * mm

    # ── restrictions on record ──
    text(ML, y, "EXPORT RESTRICTIONS ON RECORD · STRUCTURAL EXPOSURE", 9, INK, "Helvetica-Bold")
    y -= 6 * mm
    affecting = restrictions_affecting(iso, row, names)
    if affecting:
        for r in affecting[:4]:
            who = r.get("country") or names.get(r.get("iso"), (r.get("iso", ""), ""))[0]
            meas = r.get("measure") or "export restriction"
            comm = r.get("commodity") or ""
            status = (r.get("status") or "on record").upper().replace("HISTORICAL", "ON RECORD")
            text(ML, y, f"⚠  {who} {meas} on {comm}  [{status}]", 8.5, (0.72, 0.30, 0.24))
            y -= 5.5 * mm
    else:
        text(ML, y, "None on record affecting this country's cited supply.", 8.5, MUTE, "Helvetica-Oblique")
        y -= 5.5 * mm
    y -= 4 * mm

    # ── trajectory sparkline ──
    text(ML, y, "SCORE TRAJECTORY · RECORDED HISTORY", 9, INK, "Helvetica-Bold")
    y -= 6 * mm
    pts = load_history(iso)
    if len(pts) >= 2:
        vs = [p[1] for p in pts]
        lo, hi = min(vs), max(vs)
        span = (hi - lo) or 1
        sw, sh = 90 * mm, 12 * mm
        sx, sy = ML, y - sh
        n = len(pts)
        c.setStrokeColorRGB(*band(vs[-1])[1]); c.setLineWidth(1.3)
        path = c.beginPath()
        for i, (_d, v) in enumerate(pts):
            px = sx + i * sw / (n - 1)
            py = sy + (v - lo) / span * sh
            path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
        c.drawPath(path, stroke=1, fill=0)
        delta = vs[-1] - vs[0]
        arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "▬")
        text(sx + sw + 6 * mm, y - sh / 2 - 1 * mm, f"{n} days  {arrow}{'+' if delta>0 else ''}{delta}", 8.5, MUTE)
        text(ML, sy - 4 * mm, f"{pts[0][0]} → {pts[-1][0]}  ·  reproducible point-in-time snapshots (data/fdrs_history.json)", 7, MUTE)
        y = sy - 10 * mm
    else:
        text(ML, y, "History accumulates daily from 2026-05; <2 days on record for this country.", 8, MUTE, "Helvetica-Oblique")
        y -= 8 * mm

    # ── footer / methodology ──
    fy = 20 * mm
    c.setStrokeColorRGB(*HAIR); c.setLineWidth(0.6); c.line(ML, fy + 6 * mm, MR, fy + 6 * mm)
    foot = ("FDRS = 9-component weighted structural composite (weights shown) + an import×access "
            "amplifier, clipped 0–100. It is a SYNTHESIS of public data, not a forecast. Sources: "
            "UN Comtrade, FAOSTAT, USDA PSD, World Bank, WFP, IPC/FEWS, IFPRI. Figures trace to the "
            "same JSON the live dashboard ships; nothing is invented for this brief.")
    c.setFont("Helvetica", 6.8); c.setFillColorRGB(*MUTE)
    # simple wrap
    words, line, ly = foot.split(), "", fy
    maxw = MR - ML
    for wd in words:
        t = (line + " " + wd).strip()
        if c.stringWidth(t, "Helvetica", 6.8) > maxw:
            c.drawString(ML, ly, line); ly -= 3.4 * mm; line = wd
        else:
            line = t
    if line:
        c.drawString(ML, ly, line)
    text(ML, 8 * mm, "foodshield-ai-fv.vercel.app", 7, NAVY, "Helvetica-Bold")


def build(iso, names):
    row = load_country(iso)
    if not row:
        print(f"[brief] {iso}: not in countries.json — skipped.")
        return None
    OUT.mkdir(exist_ok=True)
    path = OUT / f"FoodShield_Exposure_Brief_{iso}.pdf"
    c = _canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"FoodShield Exposure Brief — {names.get(iso, (iso,''))[0]}")
    draw(c, iso, row, names)
    c.showPage()
    c.save()
    print(f"[brief] wrote {path.relative_to(REPO)}")
    return path


def main(argv):
    names = load_names()
    if not argv:
        print(__doc__); return 2
    if argv[0] == "--all-top":
        n = int(argv[1]) if len(argv) > 1 else 10
        obj = json.loads((DATA / "countries.json").read_text())["data"]["countries"]
        ranked = sorted(((iso, _v(r.get("fdrs")) or 0) for iso, r in obj.items()),
                        key=lambda t: t[1], reverse=True)
        isos = [iso for iso, _ in ranked if not iso.startswith("US-")][:n]
    else:
        isos = [a.upper() for a in argv]
    for iso in isos:
        build(iso, names)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
