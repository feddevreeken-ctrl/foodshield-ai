#!/usr/bin/env python3
"""The El Niño data pipeline as an explicit graph (stdlib only).

One place that says which file is built from which, which builders run on the cron and which are
hand-run, and how old each may be. Builders call `stamp_file(name)` after writing their output so
`_meta.inputs` records `{input file: its generated_at when this file was built}`; the checker
(scripts/check_consistency.py) reads this graph. Discovered by reading each builder, not typed from memory.

Edge kinds
  inputs      values are read: the derived file must not be older than any of them
  structural  only the SET of pairs or keys is read (hindcast and portfolio test the pairs the outlook shows);
              checked by content, not by timestamp, because the outlook is rebuilt every cron run
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# Two files built from inputs further apart than this (automated feeds only) are not one picture.
WINDOW_DAYS = 3

# Source feeds: refreshed by scripts/run_all.py on the cron, never built from other files here.
SOURCES = [
    "enso.json", "enso_indices.json", "enso_bulletins.json", "enso_news.json", "enso_strengths.json",
    "enso_gauges.json", "enso_ports.json", "enso_freight.json", "enso_price_analogs.json",
    "usda_psd.json", "worldbank_pink_sheet.json", "asap.json", "gdacs.json",
    "rtfp.json", "reliefweb_alerts.json", "rain_anomaly.json", "ipc.json", "countries.json",
    "hapi_food_security_adm0.json",
]

# Curated by hand (a person edits them); each has a review window in days. The page uses the same numbers (REVIEW_DAYS).
CURATED = {
    "enso_lanes.json": 30, "enso_regions.json": 30, "enso_published_effects.json": 120, "enso_situation.json": 30,
    "enso_mechanism.json": 30, "enso_corridors.json": 365, "enso_econ.json": 30, "enso_recent_events.json": 30,
    "crop_calendars.json": 365,
    "trade_restrictions.json": 30,   # a person adds measures; run_all only expires lapsed ones
}

CRON = "cron"
HAND = "hand"

# name -> builder, mode, inputs (values read), structural (keys read), review_days (hand-run), why (hand-run)
NODES = {
    "enso_model.json": dict(
        builder="build_enso_model.py", mode=HAND, inputs=["crop_calendars.json"], review_days=30,
        why="needs scipy and the 34 MB FAOSTAT QCL bulk"),
    "enso_neutral.json": dict(
        builder="build_enso_neutral.py", mode=HAND, inputs=["enso_model.json"], review_days=30,
        why="needs the FAOSTAT QCL and USDA PSD bulks"),
    "enso_exposure.json": dict(
        builder="build_enso_exposure.py", mode=CRON, inputs=["enso_model.json", "usda_psd.json"]),
    "enso_outlook.json": dict(
        builder="build_enso_outlook.py", mode=CRON,
        inputs=["enso.json", "enso_model.json", "enso_neutral.json", "enso_regions.json", "crop_calendars.json",
                "usda_psd.json", "worldbank_pink_sheet.json", "enso_strengths.json", "enso_news.json",
                "trade_restrictions.json"]),
    "enso_distribution.json": dict(
        builder="build_enso_distribution.py", mode=CRON,
        inputs=["enso_strengths.json", "enso_outlook.json", "enso_model.json"]),
    "enso_replacement.json": dict(
        builder="build_enso_replacement.py", mode=CRON,
        inputs=["enso_outlook.json", "usda_psd.json", "trade_restrictions.json", "enso_published_effects.json",
                "enso_lanes.json", "enso_gauges.json", "enso_freight.json"]),
    "enso_changes.json": dict(
        builder="build_enso_changes.py", mode=CRON,
        inputs=["enso.json", "enso_strengths.json", "enso_lanes.json", "enso_gauges.json", "enso_outlook.json",
                "enso_price_outlook.json", "enso_recent_events.json", "enso_situation.json", "enso_regions.json",
                "rain_anomaly.json", "ipc.json", "trade_restrictions.json"]),
    "enso_hindcast.json": dict(
        builder="build_enso_hindcast.py", mode=HAND, structural=["enso_outlook.json"], review_days=30,
        why="imports build_enso_model (scipy) and reads the FAOSTAT QCL bulk"),
    "enso_portfolio_hindcast.json": dict(
        builder="build_enso_portfolio_hindcast.py", mode=HAND, structural=["enso_outlook.json", "enso_hindcast.json"],
        review_days=30, why="scipy and the FAOSTAT QCL bulk"),
    "enso_forecast_skill.json": dict(
        builder="build_enso_forecast_skill.py", mode=HAND, inputs=["enso.json"], review_days=365,
        why="scrapes one IRI plume figure per past year; it only changes each September"),
    "ref/access_thresholds.json": dict(
        builder="build_access_thresholds.py", mode=HAND, inputs=["countries.json", "hapi_food_security_adm0.json"],
        review_days=90, why="needs scipy and the HAPI food-security pull"),
    "enso_price_outlook.json": dict(
        builder="build_enso_price_outlook.py", mode=CRON,
        inputs=["enso.json", "enso_model.json", "enso_outlook.json", "enso_regions.json", "enso_published_effects.json"]),
    "enso_price_counterfactual.json": dict(builder="build_enso_price_counterfactual.py", mode=CRON, inputs=["enso.json"]),
    "enso_price_risk.json": dict(builder="build_price_risk_band.py", mode=CRON, inputs=[]),
    "enso_event_threads.json": dict(
        builder="build_enso_event_threads.py", mode=CRON,
        inputs=["enso_auto_events.json", "enso_news.json", "enso_recent_events.json", "gdacs.json", "reliefweb_alerts.json"]),
    "enso_auto_events.json": dict(
        builder="refresh_enso_auto_events.py", mode=CRON,
        inputs=["gdacs.json", "reliefweb_alerts.json", "enso_news.json", "enso_recent_events.json", "enso.json"]),
}

# Files the El Niño tab shows (the page's loader names). Used for the single "data as of" stamp.
PAGE_FEEDS = [
    "enso.json", "enso_exposure.json", "enso_model.json", "enso_regions.json", "enso_lanes.json", "enso_corridors.json",
    "enso_econ.json", "enso_mechanism.json", "enso_indices.json", "enso_bulletins.json", "enso_news.json",
    "enso_outlook.json", "enso_gauges.json", "enso_situation.json", "enso_strengths.json", "enso_ports.json",
    "enso_hindcast.json", "enso_freight.json", "enso_price_analogs.json", "enso_published_effects.json",
    "enso_past_events.json", "enso_recent_events.json", "enso_auto_events.json", "enso_outlook_events.json",
    "enso_price_outlook.json", "enso_price_risk.json", "enso_price_forecast_log.json", "enso_changes.json",
    "enso_replacement.json", "enso_price_counterfactual.json", "enso_event_threads.json", "enso_distribution.json",
    "enso_portfolio_hindcast.json", "enso_neutral.json",
]
# Not part of the collection stamp: one-off records whose own date is the point.
STAMP_EXCLUDE = {"enso_forecast_skill.json", "enso_price_forecast_log.json", "enso_past_events.json", "enso_outlook_events.json"}


def is_hand_kept(name: str, meta: dict | None = None) -> bool:
    """Hand-run builder output or a curated file: judged by its review date, not by the cron window."""
    n = NODES.get(name)
    return name in CURATED or bool(n and n["mode"] == HAND) or bool((meta or {}).get("hand_run"))


def review_days(name: str) -> int | None:
    if name in CURATED:
        return CURATED[name]
    n = NODES.get(name)
    return n.get("review_days") if n and n["mode"] == HAND else None


# ---- reading files ----------------------------------------------------------------------------------------
def _path(name: str) -> Path:
    return DATA / name


def read(name: str):
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except Exception:
        return None


def meta_of(doc) -> dict:
    return (doc or {}).get("_meta") or {}


def body_of(doc):
    return (doc or {}).get("data", doc)


def parse_ts(v) -> datetime | None:
    """generated_at as an aware UTC datetime. A bare date counts as that day's midnight UTC."""
    if not v or not isinstance(v, str):
        return None
    s = v.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def generated_at(name: str, doc=None) -> str | None:
    m = meta_of(doc if doc is not None else read(name))
    return m.get("generated_at") or m.get("generated") or m.get("loaded_at") or m.get("fetched_at")


def stamps_of(names) -> dict:
    return {n: generated_at(n) for n in names}


def all_inputs(name: str) -> list[str]:
    n = NODES.get(name) or {}
    return list(n.get("inputs") or [])


# ---- builders call this after writing their file -----------------------------------------------------------
def stamp_file(name: str, today: date | None = None, inputs: bool = True) -> None:
    """`inputs=False` adds only the hand-run fields (for a file not rebuilt: its real input stamps are unknown).
    Add `_meta.inputs` ({input: its generated_at now}), and for hand-run files `hand_run` and `next_review_due`.
    Keeps the file's own indent and trailing newline so the diff is only the _meta."""
    p = _path(name)
    raw = p.read_text(encoding="utf-8")
    doc = json.loads(raw)
    n = NODES.get(name) or {}
    m = doc.setdefault("_meta", {})
    ins = all_inputs(name)
    if inputs and (ins or n.get("structural")):
        m["inputs"] = stamps_of(ins + list(n.get("structural") or []))
    if n.get("mode") == HAND:
        base = (parse_ts(m.get("generated_at")) or datetime.now(timezone.utc)).date() if today is None else today
        m["hand_run"] = True
        m.setdefault("builder", "scripts/" + n["builder"])
        m["hand_run_why"] = n.get("why", "")
        m["next_review_due"] = (base + timedelta(days=n["review_days"])).isoformat()
    lines = raw.split("\n")
    indent = len(lines[1]) - len(lines[1].lstrip()) if len(lines) > 1 and lines[1].startswith(" ") else 1
    text = json.dumps(doc, indent=indent, ensure_ascii=False)
    p.write_text(text + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")


# ---- the one "data as of" stamp (the page mirrors this in ensoUpdated()) ----------------------------------
def data_as_of(now: datetime | None = None, window_days: int = WINDOW_DAYS) -> dict:
    """Newest and oldest generated_at among the El Niño feeds the page shows, and whether the spread is wide.

    The window applies to feeds the cron is meant to keep fresh. Hand-run and curated files are judged by their
    own review date (validate_data warns when it passes), so they are listed but do not widen the cron spread."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for n in PAGE_FEEDS:
        if n in STAMP_EXCLUDE:
            continue
        doc = read(n)
        d = parse_ts(generated_at(n, doc))
        if d:
            rows.append((n, d, is_hand_kept(n, meta_of(doc))))
    if not rows:
        return {"newest": None, "oldest": None, "spread_days": None, "wide": False, "window_days": window_days}
    auto = [r for r in rows if not r[2]] or rows
    newest = max(rows, key=lambda r: r[1])
    oldest = min(auto, key=lambda r: r[1])
    oldest_any = min(rows, key=lambda r: r[1])
    spread = (newest[1] - oldest[1]).total_seconds() / 86400
    return {
        "newest": newest[1].isoformat(), "newest_file": newest[0],
        "oldest": oldest[1].isoformat(), "oldest_file": oldest[0],
        "oldest_hand_kept": oldest_any[1].isoformat(), "oldest_hand_kept_file": oldest_any[0],
        "spread_days": round(spread, 2), "window_days": window_days, "wide": spread > window_days,
    }


if __name__ == "__main__":
    print(json.dumps(data_as_of(), indent=1))
