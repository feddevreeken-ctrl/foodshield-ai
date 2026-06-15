"""
Trade-schema normalization helpers for FoodShield country trade fields.

Why this exists:
  - countries.json currently mixes value-ranked and quantity-ranked trade rows
    under the same field names.
  - some rows are labeled "sourced" without enough provenance to audit them.
  - multiple builders touch the same trade surface, so the normalization rules
    need to live in exactly one place.

This module adds canonical metadata for the trade-facing fields:
  imports, exports, exportDests, suppliers, supPct

The normalizer is intentionally conservative:
  - direct official rows (Comtrade, PSD, WITS) stay "sourced" once they have
    basis/unit/source metadata
  - internal composite rows (FoodShield commodity-flow layer) are downgraded to
    "partial" unless they can point to a directly auditable public row
  - legacy structural rows stay legacy, but still get basis/unit tags so the
    frontend and QA can tell what they are
"""
from __future__ import annotations

from copy import deepcopy

TRADE_FIELDS = ("imports", "exports", "exportDests", "suppliers", "supPct")
SHARE_FIELDS = {"supPct", "exportDests"}

COMTRADE_URL = "https://comtradeplus.un.org/"
PSD_URL = "https://apps.fas.usda.gov/psdonline/"
WITS_URL = "https://wits.worldbank.org/"
FAOSTAT_URL = "https://faostat.fao.org/"


def _source_rule(source: str | None):
    src = (source or "").strip()
    if src == "UN Comtrade (HS6, 2024)":
        return {
            "dataset": "un_comtrade_hs6_2024_imports",
            "coverage": "bilateral_hs6_reported_trade",
            "basis_family": "value_usd",
            "source_url": COMTRADE_URL,
            "source_urls": [COMTRADE_URL],
            "default_as_of": "2024",
            "provenance_mode": "direct_official",
        }
    if src == "UN Comtrade (HS6, 2024, exports)":
        return {
            "dataset": "un_comtrade_hs6_2024_exports",
            "coverage": "bilateral_hs6_reported_trade",
            "basis_family": "value_usd",
            "source_url": COMTRADE_URL,
            "source_urls": [COMTRADE_URL],
            "default_as_of": "2024",
            "provenance_mode": "direct_official",
        }
    if src == "USDA PSD":
        return {
            "dataset": "usda_psd_supply_balance",
            "coverage": "national_supply_balance",
            "basis_family": "quantity_1000_mt",
            "source_url": PSD_URL,
            "source_urls": [PSD_URL],
            "default_as_of": "2026 MY",
            "provenance_mode": "direct_official",
        }
    if src == "UN Comtrade (via World Bank WITS)":
        return {
            "dataset": "world_bank_wits_comtrade_interface",
            "coverage": "bilateral_trade_interface",
            "basis_family": "quantity_kt",
            "source_url": WITS_URL,
            "source_urls": [WITS_URL],
            "default_as_of": "2024",
            "provenance_mode": "direct_official",
        }
    if src == "USDA PSD / Comtrade — Sudan top wheat origins":
        return {
            "dataset": "sudan_manual_patch_psd_comtrade_2024",
            "coverage": "manual_country_patch",
            "basis_family": "quantity_kt",
            "source_url": COMTRADE_URL,
            "source_urls": [COMTRADE_URL, PSD_URL],
            "default_as_of": "2024",
            "provenance_mode": "manual_patch",
        }
    if src == "USDA PSD / Comtrade — Sudan supplier mix":
        return {
            "dataset": "sudan_manual_patch_psd_comtrade_2024",
            "coverage": "manual_country_patch",
            "basis_family": "quantity_kt",
            "source_url": COMTRADE_URL,
            "source_urls": [COMTRADE_URL, PSD_URL],
            "default_as_of": "2024",
            "provenance_mode": "manual_patch",
        }
    if src.startswith("FoodShield commodity-flow dataset"):
        return {
            "dataset": "foodshield_commodity_flow_layer",
            "coverage": "internal_composite_bilateral_flows",
            "basis_family": "quantity_kt",
            "source_url": None,
            "source_urls": [FAOSTAT_URL, COMTRADE_URL],
            "default_as_of": "2024",
            "provenance_mode": "internal_composite",
        }
    if src == "FoodShield embedded country dataset":
        return {
            "dataset": "foodshield_embedded_country_dataset",
            "coverage": "embedded_structural_snapshot",
            "basis_family": "mixed_unknown",
            "source_url": None,
            "source_urls": [],
            "default_as_of": "2026-05",
            "provenance_mode": "legacy",
        }
    return None


def _basis_meta(field: str, basis_family: str):
    if field in SHARE_FIELDS:
        if basis_family == "value_usd":
            return {"basis": "share_value_pct", "basis_unit": "pct", "underlying_basis": "value_usd"}
        if basis_family == "quantity_kt":
            return {"basis": "share_quantity_pct", "basis_unit": "pct", "underlying_basis": "quantity_kt"}
        if basis_family == "quantity_1000_mt":
            return {"basis": "share_quantity_pct", "basis_unit": "pct", "underlying_basis": "quantity_1000_mt"}
        return {"basis": "share_mixed_unknown", "basis_unit": "pct", "underlying_basis": "unknown"}

    if basis_family == "value_usd":
        return {"basis": "rank_value_usd", "basis_unit": "USD"}
    if basis_family == "quantity_kt":
        return {"basis": "rank_quantity_kt", "basis_unit": "kt"}
    if basis_family == "quantity_1000_mt":
        return {"basis": "rank_quantity_1000_mt", "basis_unit": "1000 MT"}
    return {"basis": "rank_mixed_unknown", "basis_unit": "unknown"}


def _append_unique_note(meta: dict, text: str):
    note = (meta.get("note") or "").strip()
    if text in note:
        return
    meta["note"] = f"{note} {text}".strip() if note else text


def normalize_trade_meta(field: str, meta: dict | None):
    """Return (normalized_meta, issues[]) for one trade field envelope."""
    if not isinstance(meta, dict):
        return meta, []

    out = deepcopy(meta)
    out["trade_schema_version"] = "v1"

    source = out.get("source")
    rule = _source_rule(source)

    if rule:
        out.setdefault("source_dataset", rule["dataset"])
        out.setdefault("coverage", rule["coverage"])
        if rule.get("default_as_of") and not out.get("as_of"):
            out["as_of"] = rule["default_as_of"]
        basis_meta = _basis_meta(field, rule["basis_family"])
        for key, value in basis_meta.items():
            out.setdefault(key, value)
        source_urls = list(out.get("source_urls") or [])
        for url in rule.get("source_urls") or []:
            if url and url not in source_urls:
                source_urls.append(url)
        if source_urls:
            out["source_urls"] = source_urls
        if not out.get("source_url") and rule.get("source_url"):
            out["source_url"] = rule["source_url"]
    else:
        out.setdefault("source_dataset", "unknown_trade_source")
        out.setdefault("coverage", "unknown")
        basis_meta = _basis_meta(field, "mixed_unknown")
        for key, value in basis_meta.items():
            out.setdefault(key, value)

    issues = []
    if not out.get("basis"):
        issues.append("missing_basis")
    if not out.get("basis_unit"):
        issues.append("missing_basis_unit")
    if not out.get("source_dataset"):
        issues.append("missing_source_dataset")
    if not out.get("coverage"):
        issues.append("missing_coverage")

    qf = out.get("quality_flag")
    if qf == "sourced":
        if not out.get("source_url"):
            issues.append("missing_source_url")
        if not out.get("as_of"):
            issues.append("missing_as_of")
        if rule and rule.get("provenance_mode") == "internal_composite":
            issues.append("internal_composite_not_directly_auditable")
        if issues:
            out["quality_flag"] = "partial"
            if "internal_composite_not_directly_auditable" in issues:
                _append_unique_note(
                    out,
                    "Trade schema v1 downgraded this row from sourced to partial because it is an internal composite, not a directly auditable public extract.",
                )
            elif "missing_source_url" in issues or "missing_as_of" in issues:
                _append_unique_note(
                    out,
                    "Trade schema v1 downgraded this row from sourced to partial because direct provenance metadata was incomplete.",
                )
    elif qf in {"legacy_curated", "legacy_import_dependency"}:
        _append_unique_note(
            out,
            "Trade schema v1 tagged this legacy trade row with an explicit basis/unit so downstream code does not compare it directly with sourced customs data.",
        )

    return out, issues


def normalize_trade_surface(countries: dict):
    """Normalize trade metadata across all country rows.

    Returns a list of change records that can be logged or written to a report.
    """
    changes = []
    for iso, row in (countries or {}).items():
        if not isinstance(row, dict):
            continue
        for field in TRADE_FIELDS:
            before = row.get(field)
            if not isinstance(before, dict):
                continue
            after, issues = normalize_trade_meta(field, before)
            row[field] = after
            if issues or after != before:
                changes.append(
                    {
                        "iso3": iso,
                        "field": field,
                        "quality_flag_before": before.get("quality_flag"),
                        "quality_flag_after": after.get("quality_flag"),
                        "source": after.get("source"),
                        "basis": after.get("basis"),
                        "basis_unit": after.get("basis_unit"),
                        "issues": issues,
                    }
                )
    return changes


def validate_trade_surface(countries: dict):
    """Return a list of hard validation failures for trade metadata."""
    failures = []
    for iso, row in (countries or {}).items():
        if not isinstance(row, dict):
            continue
        for field in TRADE_FIELDS:
            meta = row.get(field)
            if not isinstance(meta, dict):
                continue
            for key in ("trade_schema_version", "basis", "basis_unit", "source_dataset", "coverage"):
                if not meta.get(key):
                    failures.append(
                        {
                            "iso3": iso,
                            "field": field,
                            "problem": f"missing_{key}",
                            "quality_flag": meta.get("quality_flag"),
                            "source": meta.get("source"),
                        }
                    )
            if meta.get("quality_flag") == "sourced":
                for key in ("source_url", "as_of"):
                    if not meta.get(key):
                        failures.append(
                            {
                                "iso3": iso,
                                "field": field,
                                "problem": f"missing_{key}_for_sourced",
                                "quality_flag": meta.get("quality_flag"),
                                "source": meta.get("source"),
                            }
                        )
    return failures


def summarize_trade_surface(countries: dict):
    """Return compact per-field counts for reports and CI logs."""
    summary = {}
    for field in TRADE_FIELDS:
        field_counts = {"total": 0, "sourced": 0, "partial": 0, "legacy": 0}
        for row in (countries or {}).values():
            meta = row.get(field) if isinstance(row, dict) else None
            if not isinstance(meta, dict):
                continue
            field_counts["total"] += 1
            qf = meta.get("quality_flag")
            if qf == "sourced":
                field_counts["sourced"] += 1
            elif qf == "partial":
                field_counts["partial"] += 1
            elif qf and qf.startswith("legacy"):
                field_counts["legacy"] += 1
        summary[field] = field_counts
    return summary
