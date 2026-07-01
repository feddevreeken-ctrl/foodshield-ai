#!/usr/bin/env python3
"""
check_core_freshness.py — core-crisis-feed freshness gate for the refresh heartbeat.

Exit 0 if the core crisis feeds are within their freshness SLA, 1 if any is stale.

WHY: qa_checks.py only WARNs on freshness (correctly — a stale feed shouldn't block the
commit of everything else). But that means a run can go GREEN while the data silently rots,
and a solo maintainer never hears about it. This script lets the CI heartbeat
(.github/workflows/refresh-data.yml) ping the healthcheck SUCCESS url only when the core
feeds are fresh, and the /fail url when they're stale — so a green-but-stale run still
alerts the owner, WITHOUT blocking the data commit (honest degradation is preserved: fresh
feeds still ship, stale ones are flagged low-confidence in the UI).

Core crisis feeds = the ones the "no false calm" honesty guarantee depends on.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CORE = ["wfp_hungermap", "ipc", "fews", "reliefweb_alerts"]
# Same SLA thresholds qa_checks.py WARNs at (fetch-cadence keyword -> max age hours).
SLA_HOURS = {"6h": 72, "daily": 96, "weekly": 24 * 21, "monthly": 24 * 75}
DEFAULT_HOURS = 24 * 7


def main():
    path = Path(__file__).resolve().parent.parent / "data" / "source_manifest.json"
    try:
        m = json.loads(path.read_text())
    except Exception as e:
        print(f"[HEARTBEAT] cannot read source_manifest.json: {e}", file=sys.stderr)
        return 1
    sources = (m.get("data") or m).get("sources") or m.get("sources") or {}
    now = datetime.now(timezone.utc)
    stale = []
    for feed in CORE:
        s = sources.get(feed)
        if not s:
            stale.append(f"{feed}: absent from manifest")
            continue
        cad = (s.get("cadence") or "").split()[0].lower()
        if cad == "manual":
            continue
        limit = SLA_HOURS.get(cad, DEFAULT_HOURS)
        ts = s.get("generated_at")
        if not ts:
            stale.append(f"{feed}: no generated_at")
            continue
        try:
            age_h = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
        except Exception:
            stale.append(f"{feed}: unparseable generated_at {ts!r}")
            continue
        if age_h > limit:
            stale.append(f"{feed}: {age_h:.0f}h old (SLA {limit}h)")
    if stale:
        print("[HEARTBEAT] core crisis feeds STALE:\n  " + "\n  ".join(stale))
        return 1
    print(f"[HEARTBEAT] core crisis feeds fresh: {', '.join(CORE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
