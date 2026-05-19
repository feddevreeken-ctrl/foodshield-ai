"""
Build a source-health manifest for the frontend.

This manifest gives the UI a truthful view of which feeds are healthy, which are
manual/slow-moving, which are degraded, and which still need setup. It prevents
the app from treating "file exists" as equivalent to "source is healthy".
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from _common import DATA_DIR, write_json

TODAY = date.today()

SOURCES = [
    {
        "key": "wfp_hungermap",
        "file": "wfp_hungermap.json",
        "label": "WFP HungerMap LIVE",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "wfp_country",
        "file": "wfp_country.json",
        "label": "WFP HungerMap per-country",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "ipc",
        "file": "ipc.json",
        "label": "IPC via HungerMap mirror",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "worldbank_wdi",
        "file": "worldbank_wdi.json",
        "label": "World Bank WDI",
        "cadence": "daily fetch / annual upstream",
        "mode": "reference",
    },
    {
        "key": "worldbank_pink_sheet",
        "file": "worldbank_pink_sheet.json",
        "label": "World Bank Pink Sheet",
        "cadence": "daily fetch / monthly upstream",
        "mode": "market",
    },
    {
        "key": "fao_ffpi",
        "file": "fao_ffpi.json",
        "label": "FAO Food Price Index",
        "cadence": "daily fetch / monthly upstream",
        "mode": "market",
    },
    {
        "key": "eurostat_food",
        "file": "eurostat_food.json",
        "label": "Eurostat food HICP",
        "cadence": "daily fetch / monthly upstream",
        "mode": "reference",
    },
    {
        "key": "faostat_food",
        "file": "faostat_food.json",
        "label": "FAOSTAT food CPI",
        "cadence": "daily fetch / quarterly upstream",
        "mode": "reference",
    },
    {
        "key": "reliefweb_alerts",
        "file": "reliefweb_alerts.json",
        "label": "ReliefWeb",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "acled",
        "file": "acled.json",
        "label": "ACLED",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "comtrade_staples",
        "file": "comtrade_staples.json",
        "label": "UN Comtrade Plus",
        "cadence": "weekly / quota-limited",
        "mode": "trade",
    },
    {
        "key": "feeding_america_states",
        "file": "feeding_america_states.json",
        "label": "Feeding America",
        "cadence": "manual annual",
        "mode": "manual",
    },
    {
        "key": "openmeteo",
        "file": "openmeteo.json",
        "label": "Open-Meteo Weather",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "openmeteo_flood",
        "file": "openmeteo_flood.json",
        "label": "Open-Meteo Flood",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "usgs_water",
        "file": "usgs_water.json",
        "label": "USGS Water Services",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "openaq",
        "file": "openaq.json",
        "label": "OpenAQ",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "nasa_firms",
        "file": "nasa_firms.json",
        "label": "NASA FIRMS",
        "cadence": "daily",
        "mode": "live",
    },
    {
        "key": "wb_wfso",
        "file": "wb_wfso.json",
        "label": "World Bank WFSO (Food Security Outlook)",
        "cadence": "daily fetch / quarterly upstream",
        "mode": "forecast",
    },
    {
        "key": "net_food_trade",
        "file": "net_food_trade.json",
        "label": "FAOSTAT TCL net food trade",
        "cadence": "daily fetch / annual upstream",
        "mode": "trade",
    },
]


def read_envelope(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def payload_count(key, payload):
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    if key == "fao_ffpi":
        return len(payload.get("series") or [])
    if key == "worldbank_pink_sheet":
        return len((payload.get("series") or {}).keys())
    if key == "reliefweb_alerts":
        return len(payload.get("events") or [])
    return len(payload)


def parse_iso_date(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except Exception:
        return None


def parse_month_token(value):
    if not value:
        return None
    try:
        if len(value) == 7 and value[4] == "-":
            return datetime.strptime(value, "%Y-%m").date()
        return datetime.strptime(value, "%b-%y").date()
    except Exception:
        return None


def infer_period(key, payload):
    if not isinstance(payload, dict):
        return None
    if key == "fao_ffpi":
        return (payload.get("latest") or {}).get("month")
    if key == "worldbank_pink_sheet":
        return payload.get("as_of_month")
    if key == "feeding_america_states":
        years = [v.get("year") for v in payload.values() if isinstance(v, dict) and v.get("year")]
        return str(max(years)) if years else None
    return None


def infer_status(spec, envelope, count, period):
    meta = (envelope or {}).get("_meta") or {}
    source = str(meta.get("source") or "").lower()
    notes = str(meta.get("notes") or "").lower()
    combined = f"{source} {notes}"

    if envelope is None:
        return "failed", "missing file"
    if any(token in combined for token in [
        "not configured",
        "set acled_api_key",
        "set comtrade_api_key",
        "registration pending",
        "appname",
        "secret to enable",
    ]):
        return "setup_required", "setup or approval required"
    if "could not obtain guest token" in combined or "403" in combined:
        return "degraded", "upstream auth or access failure"
    if count == 0:
        if spec["mode"] == "manual":
            return "degraded", "manual source has no rows"
        return "degraded", "empty payload"

    if spec["key"] == "fao_ffpi":
        period_date = parse_month_token(period)
        if period_date and (TODAY.year - period_date.year) * 12 + (TODAY.month - period_date.month) > 2:
            return "degraded", f"stale release ({period})"
    if spec["key"] == "worldbank_pink_sheet":
        period_date = parse_month_token(period)
        if period_date and (TODAY.year - period_date.year) * 12 + (TODAY.month - period_date.month) > 2:
            return "degraded", f"stale release ({period})"
    if spec["key"] == "feeding_america_states":
        try:
            period_year = int(period)
        except Exception:
            period_year = None
        if period_year is not None and period_year < TODAY.year - 2:
            return "manual", f"manual annual snapshot ({period_year})"
        return "manual", "manual annual snapshot"

    return "ok", "healthy"


def main():
    rows = {}
    summary = {
        "total_sources": len(SOURCES),
        "ok_sources": 0,
        "manual_sources": 0,
        "degraded_sources": 0,
        "setup_required_sources": 0,
        "failed_sources": 0,
        "healthy_sources": 0,
        "loaded_at": None,
    }

    newest = None
    for spec in SOURCES:
        envelope = read_envelope(DATA_DIR / spec["file"])
        meta = (envelope or {}).get("_meta") or {}
        payload = (envelope or {}).get("data")
        count = payload_count(spec["key"], payload)
        period = infer_period(spec["key"], payload)
        status, reason = infer_status(spec, envelope, count, period)
        generated_at = meta.get("generated_at")
        dt = parse_iso_date(generated_at) if generated_at else None
        if generated_at and (newest is None or generated_at > newest):
            newest = generated_at
        rows[spec["key"]] = {
            "label": spec["label"],
            "file": spec["file"],
            "status": status,
            "reason": reason,
            "cadence": spec["cadence"],
            "mode": spec["mode"],
            "generated_at": generated_at,
            "count": count,
            "source": meta.get("source"),
            "notes": meta.get("notes"),
            "latest_period": period,
            "age_days": (TODAY - dt).days if dt else None,
        }
        if status == "ok":
            summary["ok_sources"] += 1
            summary["healthy_sources"] += 1
        elif status == "manual":
            summary["manual_sources"] += 1
            summary["healthy_sources"] += 1
        elif status == "degraded":
            summary["degraded_sources"] += 1
        elif status == "setup_required":
            summary["setup_required_sources"] += 1
        else:
            summary["failed_sources"] += 1

    summary["loaded_at"] = newest
    summary["headline_status"] = (
        "healthy"
        if summary["degraded_sources"] == 0
        and summary["setup_required_sources"] == 0
        and summary["failed_sources"] == 0
        else "attention"
    )

    write_json(
        "source_manifest.json",
        {"summary": summary, "sources": rows},
        source="FoodShield source manifest",
        notes=(
            "Derived from the current data/ snapshots. Status categories: ok, manual, "
            "degraded, setup_required, failed."
        ),
    )


if __name__ == "__main__":
    main()
