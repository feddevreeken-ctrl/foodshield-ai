#!/usr/bin/env python3
"""
snapshot_fdrs.py — persist a daily point-in-time snapshot of the structural FDRS.

Writes data/fdrs_history.json: per country, the structural FDRS score + 2030 outlook,
keyed by date. This is the immutable history the hindcast/validation work needs — you
cannot honestly show "the score was elevated BEFORE the event" without a reproducible
record of what the score actually was on each day.

PRESERVE-SAFE + IDEMPOTENT: it loads the existing history and only sets today's entry
(overwriting today if the 6-hourly run fires again the same day → one entry per day per
country). It never wipes prior dates. Safe to run every refresh.

Structure:
  { "_meta": {...},
    "data": { "EGY": { "2026-07-01": {"fdrs": 72, "f2030": 71}, ... }, ... } }

Runs in run_all.py AFTER "Countries dataset" (needs the freshly built countries.json).
"""
import json
from datetime import datetime, timezone

from _common import DATA_DIR, write_json

HISTORY_FILE = "fdrs_history.json"


def _load_history():
    path = DATA_DIR / HISTORY_FILE
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text())
        data = obj.get("data") if isinstance(obj, dict) else obj
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _score(row, field):
    v = row.get(field)
    if isinstance(v, dict):
        v = v.get("value")
    return v if isinstance(v, (int, float)) else None


def main():
    countries_path = DATA_DIR / "countries.json"
    cs = json.loads(countries_path.read_text())["data"]["countries"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    history = _load_history()
    updated = 0
    for iso, row in cs.items():
        fdrs = _score(row, "fdrs")
        if fdrs is None:
            continue
        entry = {"fdrs": fdrs}
        f2030 = _score(row, "f2030")
        if f2030 is not None:
            entry["f2030"] = f2030
        history.setdefault(iso, {})[today] = entry
        updated += 1

    n_days = len({d for rec in history.values() for d in rec})
    write_json(
        HISTORY_FILE,
        history,
        source="FoodShield daily structural-FDRS snapshot",
        notes=(
            f"Point-in-time structural FDRS + 2030 outlook per country, keyed by date. "
            f"Snapshot for {today}: {updated} entities. History now spans {n_days} distinct "
            f"day(s). Idempotent (one entry/day/country); the hindcast reads this."
        ),
    )
    print(f"[snapshot] {today}: recorded {updated} entities; {n_days} day(s) of history.")


if __name__ == "__main__":
    main()
