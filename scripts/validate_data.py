"""
Data-integrity post-flight check.

Runs AFTER run_all.py in the daily workflow. Each refresh script is supposed
to write a canonical file in data/ (see STEPS in run_all.py). This validator
opens every expected file and confirms:

  1. The file exists and is non-empty.
  2. It parses as JSON.
  3. The standard envelope ({"_meta": {...}, "data": ...}) is present.
  4. For high-stakes feeds, the data payload is non-trivial — not just
     '{}' from a safe_run failure stub. (Some files are *expected* to be
     empty during setup — e.g. ACLED before the API key arrives — so we
     classify each file's expected payload shape explicitly.)

Exit code:
  0  all checks passed
  1  one or more critical files corrupt / missing / empty when they
     should be populated

The exit code is consulted by the GitHub Actions workflow. The previous
failure mode was 'run_all exits 0 → bot commits whatever ended up on
disk → Vercel ships broken data files → users see 404s or empty cards'.
This script breaks that chain.

We deliberately allow soft failures (single API down for a day) to pass —
the criticality is set per-feed so transient outages don't tank the daily
commit. Hard structural failures (>= 4 critical files broken) trip the
non-zero exit.
"""
import json
import sys
from pathlib import Path

from _common import DATA_DIR

# Per-file validation spec:
#   filename → (criticality, expected_shape)
#   criticality:
#     'critical' — counts toward the >=4-failure threshold
#     'soft'     — log warning but don't fail (API-key-pending sources)
#   expected_shape:
#     'dict_nonempty'   — data field is a dict with at least 1 country key
#     'dict_or_empty'   — dict, may be empty (e.g. live-feed quiet day)
#     'list_nonempty'   — data field is a list with at least 1 entry
#     'object'          — data field is an object with structure (events/series/etc.)
#     'flexible'        — any non-null data is acceptable
EXPECTED_FILES = {
    # Live feeds — empty payload acceptable on quiet days
    'wfp_hungermap.json':         ('critical', 'dict_nonempty'),
    'wfp_country.json':           ('critical', 'dict_nonempty'),
    'ipc.json':                   ('critical', 'dict_nonempty'),
    'reliefweb_alerts.json':      ('soft',     'object'),
    'acled.json':                 ('soft',     'dict_or_empty'),  # API key gated
    'openaq.json':                ('soft',     'dict_or_empty'),  # API key gated
    'nasa_firms.json':            ('soft',     'dict_or_empty'),  # API key gated
    'comtrade_staples.json':      ('soft',     'dict_or_empty'),
    # Reference / structural — must be populated
    'fao_ffpi.json':              ('critical', 'flexible'),
    'worldbank_wdi.json':         ('critical', 'dict_nonempty'),
    'worldbank_pink_sheet.json':  ('critical', 'flexible'),
    'worldbank_bulk.json':        ('critical', 'dict_nonempty'),
    'feeding_america_states.json':('critical', 'dict_nonempty'),
    'eurostat_food.json':         ('critical', 'dict_nonempty'),
    'faostat_food.json':          ('critical', 'flexible'),
    'country_caloric_shares.json':('critical', 'dict_nonempty'),
    # v21 (May 21 2026) — these 6 pipelines are downgraded to 'soft' while the
    # upstream sources are unreliable / undergoing schema change. The frontend
    # already handles empty payloads gracefully (sections quietly hide). Hard-
    # failing the daily refresh on these is more harmful than helpful: it sends
    # red emails without preventing any user-visible breakage. The probe-log
    # diagnostics added in v21 stay in place so when the publishers stabilise,
    # we can re-promote each to critical.
    #
    # Specific issues being tracked:
    #   net_food_trade.json — FAOSTAT TCL ships dual Item Code cols + new
    #     CPC string codes; legacy 1841/1842 item-code-match returns 0 rows
    #   ndgain.json         — gain-new.crc.nd.edu/sites/.../resources.zip dead;
    #     IMF ArcGIS mirror returns CORS-blocked content for non-browser clients
    #   aqueduct.json       — WRI 4.0 distributed only via portal JS download;
    #     Data360 mirror endpoint requires unknown auth handshake
    #   inform_risk.json    — JRC 2025 not yet published; HDX resource UUID
    #     rotated; "header row" parse error suggests the XLSX format changed
    #   wgi.json            — WB API returns rows but value=null for most years
    #     (WGI is biennial 2002-2024; 2025-2026 rows are empty placeholders)
    #   cckp.json           — API now requires 12 path segments; old URLs return
    #     "Number of parameters mismatch. Needed: 11. Given: 10"
    'net_food_trade.json':        ('soft',     'dict_or_empty'),
    'usda_psd.json':              ('critical', 'dict_nonempty'),
    'ndgain.json':                ('soft',     'dict_or_empty'),
    'aqueduct.json':              ('soft',     'dict_or_empty'),
    'inform_risk.json':           ('soft',     'dict_or_empty'),
    'wgi.json':                   ('soft',     'dict_or_empty'),
    'lpi.json':                   ('critical', 'dict_nonempty'),
    'hdi.json':                   ('critical', 'dict_nonempty'),
    'cckp.json':                  ('soft',     'dict_or_empty'),
    'wb_wfso.json':               ('critical', 'dict_nonempty'),
    # Environmental (per-country dicts; quiet days OK)
    'openmeteo.json':             ('soft',     'dict_or_empty'),
    'openmeteo_flood.json':       ('soft',     'dict_or_empty'),
    'usgs_water.json':            ('soft',     'dict_or_empty'),
    # Composites — built from the above; must populate
    'countries.json':             ('critical', 'object'),
    'nowcast.json':               ('critical', 'dict_nonempty'),
    'daily_summary.json':         ('critical', 'object'),
    'source_manifest.json':       ('critical', 'object'),
    'companies.json':             ('soft',     'dict_or_empty'),  # built from local data/companies/*.json
}

# Threshold: this many or more 'critical' failures → exit 1
CRITICAL_FAILURE_THRESHOLD = 4


def validate_one(filename, spec):
    """Returns (ok: bool, message: str)."""
    criticality, shape = spec
    p = DATA_DIR / filename
    if not p.exists():
        return False, f"missing file"
    if p.stat().st_size < 10:
        return False, f"file is {p.stat().st_size} bytes (truncated/empty)"
    try:
        env = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return False, f"JSON parse error: {e}"

    if not isinstance(env, dict):
        return False, f"top-level is {type(env).__name__}, expected dict"
    if '_meta' not in env:
        return False, "missing _meta envelope"
    if 'data' not in env:
        return False, "missing data envelope"

    data = env['data']
    notes = (env.get('_meta') or {}).get('notes', '')

    if shape == 'dict_nonempty':
        if not isinstance(data, dict):
            return False, f"data is {type(data).__name__}, expected dict"
        if len(data) == 0:
            return False, f"data dict is empty (notes: {notes[:80] if notes else 'none'})"
    elif shape == 'list_nonempty':
        if not isinstance(data, list):
            return False, f"data is {type(data).__name__}, expected list"
        if len(data) == 0:
            return False, "data list is empty"
    elif shape == 'dict_or_empty':
        if data is None or (isinstance(data, dict) and not data):
            return True, f"empty (allowed) — {notes[:60] if notes else 'no notes'}"
    elif shape == 'object':
        if data is None:
            return False, "data is null"
    elif shape == 'flexible':
        if data is None:
            return False, "data is null"

    return True, "ok"


def main():
    print(f"=== Data integrity check ({len(EXPECTED_FILES)} files) ===")
    critical_failures = []
    soft_warnings = []
    ok_count = 0
    for filename in sorted(EXPECTED_FILES):
        criticality, shape = EXPECTED_FILES[filename]
        ok, msg = validate_one(filename, (criticality, shape))
        marker = "OK   " if ok else ("FAIL " if criticality == 'critical' else "WARN ")
        print(f"  {marker} {filename:34s} ({criticality:8s} / {shape:15s}) — {msg}")
        if ok:
            ok_count += 1
        elif criticality == 'critical':
            critical_failures.append((filename, msg))
        else:
            soft_warnings.append((filename, msg))

    print()
    print(f"=== Summary: {ok_count}/{len(EXPECTED_FILES)} files OK, "
          f"{len(critical_failures)} critical failures, "
          f"{len(soft_warnings)} soft warnings ===")

    if critical_failures:
        print("\nCRITICAL failures:")
        for filename, msg in critical_failures:
            print(f"  - {filename}: {msg}")

    if len(critical_failures) >= CRITICAL_FAILURE_THRESHOLD:
        print(f"\n{len(critical_failures)} critical failures (>= {CRITICAL_FAILURE_THRESHOLD} threshold). "
              f"Exiting non-zero so the workflow status reflects reality.")
        sys.exit(1)

    if critical_failures:
        print(f"\n{len(critical_failures)} critical failure(s) — under the {CRITICAL_FAILURE_THRESHOLD} "
              f"threshold so the workflow keeps going, but watch the next refresh.")

    sys.exit(0)


if __name__ == "__main__":
    main()
