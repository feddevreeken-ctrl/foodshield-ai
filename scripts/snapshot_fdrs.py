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

From 2026-09-25 each new entry also carries:
  method      — "<release>-js<fdrs.js sha8>-w<weights sha6>-f2030<tag>". History has
                single-day jumps (127 countries on 2026-06-04, 57 on 2026-08-06, 124 on
                2026-09-01) that are method changes, not risk changes; compare two
                entries only when their `method` is equal. Older entries have none.
  structural  — fdrs_displayed_base (structural score from the browser scorer).
  displayed   — fdrs_displayed (structural + nowcast).

Runs in run_all.py AFTER "Displayed FDRS" (needs countries.json with the displayed tier
and the re-based f2030; the structural `fdrs` itself is written by "Countries dataset").
"""
import hashlib
import json
from datetime import datetime, timezone

from _common import DATA_DIR, ROOT, write_json

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


def method_tag(envelope):
    """Short, human-diffable identifier of everything that defines the scores."""
    from build_countries_dataset import FDRS_V2_WEIGHTS
    js = hashlib.sha256((ROOT / "js" / "fdrs.js").read_bytes()).hexdigest()[:8]
    w = hashlib.sha256(json.dumps(FDRS_V2_WEIGHTS).encode()).hexdigest()[:6]
    meta = envelope.get("_meta") or {}
    rows = (envelope.get("data") or {}).get("countries") or {}
    rebased = any(isinstance((r.get("f2030") or {}).get("rebase"), dict)
                  for r in rows.values() if isinstance(r, dict))
    return (f"{meta.get('release_version') or meta.get('schema_version') or 'unknown'}"
            f"-js{js}-w{w}-f2030{'rebased1' if rebased else 'heritage'}")


def main():
    countries_path = DATA_DIR / "countries.json"
    envelope = json.loads(countries_path.read_text())
    cs = envelope["data"]["countries"]
    method = method_tag(envelope)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    history = _load_history()
    updated = 0
    for iso, row in cs.items():
        fdrs = _score(row, "fdrs")
        if fdrs is None:
            continue
        entry = {"fdrs": fdrs, "method": method}
        f2030 = _score(row, "f2030")
        if f2030 is not None:
            entry["f2030"] = f2030
        # The published displayed score (structural + live signals), so trends on the
        # page can compare like with like. Recorded from 2026-09-25 onwards.
        shown = _score(row, "fdrs_displayed")
        if shown is not None:
            entry["displayed"] = shown
        structural = _score(row, "fdrs_displayed_base")
        if structural is not None:
            entry["structural"] = structural
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
            f"day(s). Idempotent (one entry/day/country); the hindcast reads this. "
            f"Entries from 2026-09-25 carry method (compare only within one method; older "
            f"entries predate it and include method-change jumps), structural "
            f"(fdrs_displayed_base) and displayed (fdrs_displayed). Today's method: {method}."
        ),
    )
    print(f"[snapshot] {today}: recorded {updated} entities; {n_days} day(s) of history; method {method}.")


if __name__ == "__main__":
    main()
