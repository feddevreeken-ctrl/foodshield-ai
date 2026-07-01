#!/usr/bin/env python3
"""
backfill_fdrs_history.py — seed data/fdrs_history.json from REAL git history.

snapshot_fdrs.py records the FDRS score going forward. This one-time backfill reaches
BACKWARD: it walks the git history of data/countries.json, and for each committed day
extracts the structural FDRS + 2030 outlook per country into fdrs_history.json — so the
history isn't empty until forward snapshots accumulate.

HONESTY NOTE: the git history only reaches ~2026-05 (the project's start). This is REAL
point-in-time data, not reconstructed — but it does NOT extend to older documented events
(2022-2023). Those must be handled by the structural-exposure validation, not a faked
point-in-time score. No fabricated history here: a commit whose countries.json doesn't
parse (early differing schema) is skipped, not guessed.

Idempotent + preserve-safe: only fills day/country entries that aren't already present
(so it never overwrites a forward snapshot or a prior backfill).
"""
import json
import subprocess
from pathlib import Path

from _common import DATA_DIR, write_json

HISTORY_FILE = "fdrs_history.json"
REPO = Path(__file__).resolve().parent.parent


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def _score(row, field):
    v = row.get(field)
    if isinstance(v, dict):
        v = v.get("value")
    return v if isinstance(v, (int, float)) else None


def _extract(blob):
    """Return {iso: {fdrs, f2030}} from a countries.json text blob, or {} if it doesn't parse."""
    try:
        obj = json.loads(blob)
        cs = (obj.get("data") or {}).get("countries")
        if not isinstance(cs, dict):
            return {}
        out = {}
        for iso, row in cs.items():
            if not isinstance(row, dict):
                continue
            f = _score(row, "fdrs")
            if f is None:
                continue
            e = {"fdrs": f}
            f2030 = _score(row, "f2030")
            if f2030 is not None:
                e["f2030"] = f2030
            out[iso] = e
        return out
    except Exception:
        return {}


def main():
    # commit hash + date (YYYY-MM-DD) for every commit touching countries.json, oldest first
    log = _git("log", "--format=%H %ad", "--date=short", "--reverse", "--", "data/countries.json")
    seen_days = set()
    commits = []
    for line in log.splitlines():
        h, _, day = line.partition(" ")
        if day and day not in seen_days:
            seen_days.add(day)
            commits.append((h, day))

    # load existing history
    path = DATA_DIR / HISTORY_FILE
    history = {}
    if path.exists():
        try:
            history = json.loads(path.read_text()).get("data") or {}
        except Exception:
            history = {}

    filled_days = 0
    skipped = 0
    for h, day in commits:
        blob = _git("show", f"{h}:data/countries.json")
        scores = _extract(blob)
        if not scores:
            skipped += 1
            continue
        wrote = False
        for iso, entry in scores.items():
            rec = history.setdefault(iso, {})
            if day not in rec:            # preserve-safe: never overwrite existing
                rec[day] = entry
                wrote = True
        if wrote:
            filled_days += 1

    n_days = len({d for rec in history.values() for d in rec})
    write_json(
        HISTORY_FILE,
        history,
        source="FoodShield structural-FDRS history (git-backfilled + daily snapshot)",
        notes=(
            f"Backfilled {filled_days} day(s) from git history of countries.json "
            f"({skipped} early commit(s) skipped: differing schema, not fabricated). "
            f"History now spans {n_days} distinct day(s). Real point-in-time data reaches "
            f"~2026-05; older events use structural-exposure validation, not a faked score."
        ),
    )
    print(f"[backfill] filled {filled_days} day(s) from git; {skipped} skipped; {n_days} total days of history.")


if __name__ == "__main__":
    main()
