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
        # v79j — no longer a WFP mirror. WFP closed the HungerMap v2 tree to
        # anonymous clients, so this file is now derived from fx_rates.json +
        # worldbank_bulk.json. The key and filename are kept so the six existing
        # consumers stay untouched; the label is what readers actually see.
        "label": "Per-country FX shock + child nutrition (derived)",
        "cadence": "6h derived",
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
    {
        "key": "worldbank_bulk",
        "file": "worldbank_bulk.json",
        "label": "World Bank WDI bulk (22 indicators)",
        "cadence": "6h fetch / quarterly upstream",
        "mode": "reference",
    },
    {
        "key": "fx_rates",
        "file": "fx_rates.json",
        "label": "FX rates (ER-API + ECB Frankfurter)",
        "cadence": "6h fetch / daily upstream",
        "mode": "market",
    },
    {
        "key": "usda_psd",
        "file": "usda_psd.json",
        "label": "USDA PSD staples (wheat/rice/corn/soybeans)",
        "cadence": "6h fetch / monthly upstream (WASDE)",
        "mode": "trade",
    },
    {
        "key": "ndgain",
        "file": "ndgain.json",
        "label": "ND-GAIN climate vulnerability + readiness",
        "cadence": "6h fetch / annual upstream",
        "mode": "reference",
    },
    {
        "key": "aqueduct",
        "file": "aqueduct.json",
        "label": "WRI Aqueduct 4.0 water risk",
        "cadence": "6h fetch / 3-5yr upstream",
        "mode": "reference",
    },
    # v83 — the feeds that give the score a present tense, plus the two that
    # replace hand-authored FDRS components. Registered here so Data Status can
    # date them: an audit found the trade atlas had been running as an
    # unregistered static snapshot for twelve weeks precisely because nothing
    # outside the manifest can be freshness-checked.
    {
        "key": "asap",
        "file": "asap.json",
        "label": "JRC ASAP crop-stress hotspots",
        "cadence": "daily fetch / monthly upstream",
        "mode": "live",
    },
    {
        "key": "hapi_conflict",
        "file": "hapi_conflict.json",
        "label": "ACLED conflict events (HDX HAPI)",
        "cadence": "daily fetch / weekly upstream",
        "mode": "live",
    },
    {
        "key": "rtfp",
        "file": "rtfp.json",
        "label": "World Bank Real-Time Food Prices",
        "cadence": "daily fetch / weekly upstream",
        "mode": "live",
    },
    {
        "key": "portwatch",
        "file": "portwatch.json",
        "label": "IMF PortWatch chokepoint transits",
        "cadence": "daily fetch / daily upstream (~7d lag)",
        "mode": "live",
    },
    {
        "key": "faostat_import_dep",
        "file": "faostat_import_dep.json",
        "label": "FAOSTAT cereal import dependency",
        "cadence": "daily fetch / annual upstream",
        "mode": "reference",
    },
    {
        "key": "faostat_prod_index",
        "file": "faostat_prod_index.json",
        "label": "FAOSTAT gross food production index",
        "cadence": "daily fetch / annual upstream",
        "mode": "reference",
    },
    {
        "key": "inform_risk",
        "file": "inform_risk.json",
        "label": "EU JRC INFORM Risk Index",
        "cadence": "6h fetch / annual upstream",
        "mode": "reference",
    },
    {
        "key": "wgi",
        "file": "wgi.json",
        "label": "WB Worldwide Governance Indicators",
        "cadence": "6h fetch / annual upstream",
        "mode": "reference",
    },
    {
        "key": "daily_summary",
        "file": "daily_summary.json",
        "label": "FoodShield daily 'what changed' summary",
        "cadence": "6h derived",
        "mode": "live",
    },
    {
        "key": "lpi",
        "file": "lpi.json",
        "label": "WB Logistics Performance Index",
        "cadence": "6h fetch / biennial upstream",
        "mode": "reference",
    },
    {
        "key": "hdi",
        "file": "hdi.json",
        "label": "UNDP Human Development Index",
        "cadence": "6h fetch / annual upstream",
        "mode": "reference",
    },
    {
        "key": "cckp",
        "file": "cckp.json",
        "label": "WB Climate Change Knowledge Portal",
        "cadence": "6h fetch / multi-year upstream",
        "mode": "reference",
    },
    # ── Static deep-link helpers ────────────────────────────────────────────
    # These four sources don't have automated refresh pipelines — they are
    # curated static lookups + per-country deep links into the upstream
    # publications. They're surfaced in the country panel as "Read the
    # official brief" buttons. Tracked here so the manifest's total_sources
    # count matches the "33 public pipelines" claim used across the UI.
    {
        "key": "fao_giews",
        "file": "fao_giews.json",
        "label": "FAO GIEWS Country Briefs (static helper)",
        "cadence": "manual / quarterly upstream",
        "mode": "static_helper",
    },
    {
        "key": "fews",
        "file": "fews.json",
        "label": "FEWS NET IPC Outlook (live FDW API)",
        "cadence": "6h fetch / quarterly upstream",
        "mode": "forecast",
    },
    {
        "key": "wfp_acr",
        "file": "wfp_acr.json",
        "label": "WFP Annual Country Reports (static helper)",
        "cadence": "manual / annual upstream",
        "mode": "static_helper",
    },
    {
        "key": "usda_gain",
        "file": "usda_gain.json",
        "label": "USDA GAIN Attaché Reports (static helper)",
        "cadence": "manual / rolling upstream",
        "mode": "static_helper",
    },
    # v46 — commodity headlines from GDELT DOC 2.0 + the European Commission
    # agriculture RSS. Both keyless. mode='live' because it refreshes every cron,
    # but note the payload is third-party CLAIMS with links, not measured data —
    # the count below is HEADLINES, not countries or commodities.
    {
        "key": "commodity_news",
        "file": "commodity_news.json",
        "label": "Commodity news (GDELT DOC 2.0 + EC agriculture RSS)",
        "cadence": "6h fetch / continuous upstream",
        "mode": "live",
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
    # v46 — mirrors the reliefweb_alerts case: the payload is an object holding a
    # list plus per-commodity status, so the generic key count would report "3"
    # (items/commodity_status/sources) instead of the number of HEADLINES. The
    # Data Status page counts stories here, not commodities.
    if key == "commodity_news":
        return len(payload.get("items") or [])
    # Ignore internal underscore-prefixed keys (e.g. fx_rates' _ccy_history rolling
    # store) so they don't inflate the per-source country count on the Data Status page.
    return len([k for k in payload if not str(k).startswith("_")])


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

    # Static deep-link helpers don't have a data file — they're just curated
    # per-country links into the upstream publication. Always render as manual.
    if spec.get("mode") == "static_helper":
        return "manual", "static deep-link helper (curated lookup)"

    if envelope is None:
        return "failed", "missing file"

    # v43 — an EXPLICIT _meta.status from the refresh script wins over the
    # note-sniffing below. Inferring state by string-matching prose is how
    # three feeds sat at a generic "empty payload" for months while the real
    # causes were entirely different: a rejected API key, a schema misread,
    # and a stale download URL. A script that knows why it failed should say
    # so in a field, and that field should be believed.
    explicit = str(meta.get("status") or "").lower()
    if explicit == "auth_failed":
        return "setup_required", "API key rejected by upstream — re-provision the secret"
    if explicit == "degraded_fallback":
        return "degraded", "serving a fallback tier, not the primary source"
    if explicit == "degraded_feeds":
        # A multi-source feed where a large share of sources failed. The item
        # count stays healthy because the survivors fill the page, which is
        # exactly why the count cannot be trusted as a health signal here.
        return "degraded", "a significant share of upstream sources failed this run"
    if explicit == "degraded_partial":
        return "degraded", "pagination truncated — the result set is incomplete"
    # Only treat as setup_required when the meta explicitly signals missing
    # credentials / pending approval. The earlier "appname" token matched
    # ReliefWeb's healthy source string (which embeds "appname=foodshield"
    # in the URL) and falsely flagged it. Now we require explicit setup
    # language, and only allow the "appname" token through the notes field
    # paired with a stronger phrase like "API key needed".
    setup_tokens = [
        "not configured",
        "set acled_api_key",
        "set comtrade_api_key",
        "registration pending",
        "secret to enable",
        "api key needed",
        "setup required",
        "key not provided",
    ]
    if any(token in combined for token in setup_tokens):
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
        "stale_sources": 0,
        "loaded_at": None,
    }

    # v38 — freshness threshold. Feeds that haven't refreshed in this many days are
    # flagged stale so the headline can fold freshness in (previously the pill counted
    # only status buckets, so a 12-day-old feed still read "healthy"). Manual annual
    # snapshots are exempt — they're SUPPOSED to be old.
    STALE_AFTER_DAYS = 5

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
        # v38 — flag stale (refresh overdue). Exempt manual/annual snapshots and
        # static deep-link helpers, which are expected to be old by design.
        _age = rows[spec["key"]]["age_days"]
        _is_manual = (status == "manual") or (spec.get("mode") == "manual")
        _is_stale = (_age is not None) and (_age > STALE_AFTER_DAYS) and not _is_manual
        rows[spec["key"]]["stale"] = bool(_is_stale)
        if _is_stale:
            summary["stale_sources"] += 1
            # v79i — a stale feed is not a healthy feed. Until now `stale` was
            # computed and stored but never allowed to touch `status`, so a feed
            # that had not refreshed in four weeks still rendered as
            # "LIVE · Healthy" in the Data Status table and still counted toward
            # the 32/34 healthy pill in the header. Open-Meteo Weather — cadence
            # "daily", last successful run 2026-08-03 — was doing exactly that.
            # Demote it to degraded and say why, so the age the row already
            # displays and the status next to it stop contradicting each other.
            if status == "ok":
                status = "degraded"
                reason = f"stale — no successful refresh in {_age}d (cadence: {spec['cadence']})"
                rows[spec["key"]]["status"] = status
                rows[spec["key"]]["reason"] = reason
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
    # v38 — headline now folds in freshness: stale feeds also raise "attention" so the
    # top-line signal can't read healthy while feeds are days overdue for a refresh.
    summary["headline_status"] = (
        "healthy"
        if summary["degraded_sources"] == 0
        and summary["setup_required_sources"] == 0
        and summary["failed_sources"] == 0
        and summary["stale_sources"] == 0
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
