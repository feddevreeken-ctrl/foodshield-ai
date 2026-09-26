#!/usr/bin/env python3
"""
refresh_trade_restrictions.py — Trade-restriction monitor feed (v40, SEED STUB).

Writes data/trade_restrictions.json. v1 ships EMPTY on purpose.

WHY EMPTY: the app's restrictionExposure() engine (index.html) computes WHO IS EXPOSED
live from the atlas flows, but the restrictions themselves must be SOURCED — never
fabricated. That is the project's whole credibility proposition. So this feed ships
empty until real, cited entries are added.

SAFE-BY-DESIGN: this script PRESERVES existing entries. If you hand-populate
data/trade_restrictions.json (or a future ingest does), re-running this stub will NOT
clobber it — it only writes the empty envelope when the file is missing/empty. So it is
safe to wire into run_all.py today.

HOW TO POPULATE (owner-side, needs network + source judgment):
  Tier 1 (do first, fully public): ingest the IFPRI Food & Fertilizer Export
    Restrictions Tracker -> each item status="historical", cited to IFPRI.
  Tier 2 (later): official gazettes / ministry feeds / wire news -> status
    "official"/"reported", ONLY with a source_url. Server-side scan, same
    no-client-LLM discipline as fetchAINarrative.
  Never emit an item without a source_url + confidence tier. Rumor is dropped.

ITEM SCHEMA (exposed_importers is computed CLIENT-SIDE by restrictionExposure(),
NOT stored here):
  {
    "iso": "RUS", "country": "Russia", "commodity": "Wheat",
    "measure": "export ban" | "quota" | "licensing" | "export tax",
    "status": "official" | "reported" | "historical",
    "announced_date": "2026-06-01", "effective_date": "2026-07-01" | null,
    "source_url": "https://...", "source_name": "IFPRI tracker / Gazette / Reuters",
    "note": "short, verbatim-grounded — no embellishment"
  }
Commodity must match a canonical card key (Wheat, Rice, Corn, Soybeans, Palm Oil, ...).

See docs/superpowers/specs/2026-06-29-trade-restriction-monitor-design.md.
"""
import json

from datetime import date, datetime, timezone

from _common import DATA_DIR, write_json

FILENAME = "trade_restrictions.json"

# v1: empty until SOURCED entries are added. NO fabricated restrictions.
RESTRICTIONS = []


def _existing_count():
    """Number of restriction entries already on disk (0 if absent/empty/unreadable)."""
    path = DATA_DIR / FILENAME
    if not path.exists():
        return 0
    try:
        obj = json.loads(path.read_text())
        data = obj.get("data") if isinstance(obj, dict) else obj
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0


def expire_measures(rows, today):
    """Mark in-force measures whose recorded end date has passed as historical. Pure and idempotent.

    Only the status changes (plus a note and the date it was applied): the source, the review
    stamps and every other field stay as recorded. An expiry says the recorded window closed, not
    that exports are unrestricted: an extension may exist that no source here has recorded yet, so
    the page keeps a 45-day reminder to check (restrictionState in index.html reads auto_expired)."""
    changed = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get("status") not in ("official", "reported"):
            continue
        end = r.get("ends_date")
        try:
            end_d = date.fromisoformat(str(end)[:10]) if end else None
        except ValueError:
            end_d = None
        if end_d and end_d < today:
            r["status_before_expiry"] = r["status"]
            r["status"] = "historical"
            r["auto_expired"] = today.isoformat()
            r["expiry_note"] = f"Recorded window ended {end_d.isoformat()}; an extension is not verified."
            changed.append(f"{r.get('country') or r.get('iso')} {r.get('commodity')}")
    return changed


def _expire_on_disk(today=None):
    path = DATA_DIR / FILENAME
    if not path.exists():
        return []
    obj = json.loads(path.read_text())
    rows = obj.get("data") if isinstance(obj, dict) else obj
    changed = expire_measures(rows, today or datetime.now(timezone.utc).date())
    if changed:
        if isinstance(obj, dict):
            obj.setdefault("_meta", {})["auto_expired_at"] = (today or datetime.now(timezone.utc).date()).isoformat()
        # Written directly, not through write_json: the review stamps in _meta must not be refreshed
        # by a mechanical status change.
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
        print(f"[EXPIRE] {len(changed)} measure(s) past their end date set to historical: {', '.join(changed)}")
    return changed


def main():
    _expire_on_disk()
    # Preserve owner-curated / ingested entries — never clobber sourced restrictions.
    n = _existing_count()
    if n > 0:
        print(f"[KEEP] {FILENAME} already has {n} sourced entry(ies) — preserving (not overwriting).")
        return
    write_json(
        FILENAME,
        RESTRICTIONS,
        source="Trade-restriction monitor (seed stub — populate from IFPRI tracker / official gazettes)",
        notes=(
            "Empty v1 by design. The app's restrictionExposure() computes exposure live from "
            "the atlas; restriction entries must be sourced (IFPRI Food & Fertilizer Export "
            "Restrictions Tracker / official gazettes), each with a source_url + confidence "
            "tier. No fabricated restrictions."
        ),
    )


if __name__ == "__main__":
    main()
