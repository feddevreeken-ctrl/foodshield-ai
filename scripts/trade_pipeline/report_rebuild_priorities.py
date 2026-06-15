"""
Rank countries for trade-field rebuild priority.

This is intentionally focused on the import/export trade surface:
  imports, suppliers, supPct, exports, exportDests

Priority combines:
  - metadata readiness (sourced vs partial vs legacy)
  - whether the row still relies on the internal commodity-flow layer
  - structural exposure (import dependence + FDRS)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_countries_dataset as countries_builder
from trade_schema import TRADE_FIELDS, normalize_trade_surface

FIELD_WEIGHTS = {
    "imports": 3.0,
    "suppliers": 3.0,
    "supPct": 3.0,
    "exports": 2.0,
    "exportDests": 2.0,
}

STATUS_SEVERITY = {
    "sourced": 0.0,
    "manual": 0.5,
    "partial": 3.0,
    "legacy_curated": 2.0,
    "legacy_import_dependency": 2.0,
    "heritage": 2.0,
    None: 4.0,
}


def _load_countries():
    env = json.loads((DATA / "countries.json").read_text())
    countries = (((env.get("data") or {}).get("countries")) if isinstance(env, dict) else None) or {}
    if not isinstance(countries, dict) or not countries:
        raise RuntimeError("data/countries.json has no data.countries payload")
    return env, countries


def _load_names():
    try:
        countries_builder._extract_legacy_rows()
        return getattr(countries_builder._extract_legacy_rows, "names", {}) or {}
    except Exception:
        return {}


def _scalar_value(meta):
    if isinstance(meta, dict):
        return meta.get("value")
    return meta


def _field_reason(field, meta):
    qf = meta.get("quality_flag")
    src = meta.get("source") or "unknown source"
    if qf == "partial":
        return f"{field}: partial ({src})"
    if qf and qf.startswith("legacy"):
        return f"{field}: legacy"
    if qf == "manual":
        return f"{field}: manual snapshot"
    if qf == "sourced":
        return f"{field}: sourced"
    return f"{field}: missing/unknown"


def _score_country(iso, row):
    score = 0.0
    drivers = []
    outstanding = []
    field_status = {}
    basis = {}

    for field in TRADE_FIELDS:
        meta = row.get(field)
        qf = meta.get("quality_flag") if isinstance(meta, dict) else None
        sev = STATUS_SEVERITY.get(qf, 2.5)
        weight = FIELD_WEIGHTS[field]
        field_score = sev * weight
        source = meta.get("source", "") if isinstance(meta, dict) else ""
        if isinstance(meta, dict) and source.startswith("FoodShield commodity-flow dataset"):
            field_score += 1.0
        if isinstance(meta, dict) and not meta.get("source_dataset"):
            field_score += 1.0
        if field_score > 0:
            outstanding.append(field)
            drivers.append(_field_reason(field, meta if isinstance(meta, dict) else {}))
        field_status[field] = qf or "missing"
        basis[field] = meta.get("basis") if isinstance(meta, dict) else None
        score += field_score

    c_meta = row.get("c") or {}
    c_vec = _scalar_value(c_meta) or []
    import_dep = c_vec[0] if isinstance(c_vec, list) and len(c_vec) > 0 else None
    fdrs = _scalar_value(row.get("fdrs") or {})
    if isinstance(import_dep, (int, float)):
        if import_dep >= 70:
            score += 2.0
        elif import_dep >= 55:
            score += 1.0
    if isinstance(fdrs, (int, float)):
        if fdrs >= 75:
            score += 2.0
        elif fdrs >= 60:
            score += 1.0

    if score >= 24:
        band = "critical"
    elif score >= 15:
        band = "high"
    elif score >= 8:
        band = "medium"
    else:
        band = "low"

    return {
        "iso3": iso,
        "score": round(score, 1),
        "priority_band": band,
        "field_status": field_status,
        "basis": basis,
        "outstanding_fields": outstanding,
        "drivers": drivers[:5],
        "exposure": {
            "import_dep_component": import_dep,
            "fdrs": fdrs,
        },
    }


def _write_markdown(path: Path, rows: list[dict]):
    band_counts = {}
    for row in rows:
        band_counts[row["priority_band"]] = band_counts.get(row["priority_band"], 0) + 1
    lines = [
        "# Trade Rebuild Priority Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Priority formula:",
        "- Higher score = weaker trade metadata and more urgent rebuild need.",
        "- Imports/suppliers/supPct are weighted more heavily than exports/exportDests.",
        "- Partial rows and internal commodity-flow rows rank above legacy rows.",
        "- High import dependence and high FDRS add a small exposure bonus.",
        "",
        "Band counts:",
        *(f"- {band}: {band_counts.get(band, 0)}" for band in ("critical", "high", "medium", "low")),
        "",
        "| Rank | ISO3 | Country | Score | Band | Outstanding | Drivers |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in rows:
        outstanding = ", ".join(row["outstanding_fields"]) if row["outstanding_fields"] else "none"
        drivers = "; ".join(row["drivers"]) if row["drivers"] else "fully sourced"
        lines.append(
            f"| {row['rank']} | {row['iso3']} | {row['name']} | {row['score']} | {row['priority_band']} | {outstanding} | {drivers} |"
        )
    path.write_text("\n".join(lines) + "\n")


def main():
    _, countries = _load_countries()
    normalize_trade_surface(countries)
    names = _load_names()

    ranked = []
    for iso, row in countries.items():
        item = _score_country(iso, row)
        item["name"] = names.get(iso, iso)
        ranked.append(item)

    ranked.sort(key=lambda item: (-item["score"], item["iso3"]))
    for idx, item in enumerate(ranked, start=1):
        item["rank"] = idx

    out_json = DATA / "trade_rebuild_priorities.json"
    out_md = ROOT / "TRADE_REBUILD_PRIORITY_REPORT.md"
    payload = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "trade_pipeline/report_rebuild_priorities.py",
            "version": "v23",
            "formula": {
                "field_weights": FIELD_WEIGHTS,
                "status_severity": STATUS_SEVERITY,
                "exposure_bonus": "import_dep>=55/+1, >=70/+2; fdrs>=60/+1, >=75/+2",
            },
        },
        "data": ranked,
    }
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    _write_markdown(out_md, ranked)
    print(f"[trade-priority] wrote {out_json}")
    print(f"[trade-priority] wrote {out_md}")
    print("[trade-priority] top 15:")
    for row in ranked[:15]:
        print(
            f"  {row['rank']:>3}. {row['iso3']} {row['name']:<28} "
            f"{row['score']:>4} {row['priority_band']:<8} "
            f"{', '.join(row['outstanding_fields'][:3])}"
        )


if __name__ == "__main__":
    main()
