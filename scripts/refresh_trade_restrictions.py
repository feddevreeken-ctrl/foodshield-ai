#!/usr/bin/env python3
"""
refresh_trade_restrictions.py — Trade-restriction monitor feed (v40, SEED STUB).

Writes data/trade_restrictions.json. v1 is an EMPTY envelope ON PURPOSE.

WHY EMPTY: the app's restrictionExposure() engine (index.html) computes WHO IS EXPOSED
live from the atlas flows, but the restrictions themselves must be SOURCED — never
fabricated. That is the project's whole credibility proposition. So this feed ships
empty until real, cited entries are added.

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
    "confidence": "official" | "reported" | "historical",
    "announced_date": "2026-06-01", "effective_date": "2026-07-01" | null,
    "source_url": "https://...", "source_name": "IFPRI tracker / Gazette / Reuters",
    "note": "short, verbatim-grounded — no embellishment"
  }

See docs/superpowers/specs/2026-06-29-trade-restriction-monitor-design.md.
"""
from _common import write_json

# v1: empty until SOURCED entries are added. NO fabricated restrictions.
RESTRICTIONS = []


def main():
    write_json(
        "trade_restrictions.json",
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
