"""
Data-reliability gate — deeper, content-level QA that runs AFTER validate_data.py.

WHERE THIS FITS IN THE WORKFLOW
  run_all.py            -> writes every data/*.json
  validate_data.py      -> structural gate: file exists, parses, has the
                           {_meta, data} envelope, payload is non-trivial
  qa_checks.py (THIS)   -> content gate: cross-file consistency, coverage
                           floors, freshness SLAs, provenance regression, and
                           semantic honesty checks the structural validator
                           cannot see.

validate_data.py answers "is each file shaped right and non-empty?".
qa_checks.py answers "does the content still make sense, and are we honest
about what we don't have?". The two are complementary; this one does NOT
re-implement the envelope/shape checks — it assumes they already passed.

EXIT CODES (mirrors validate_data.py)
  0  no hard FAILs (WARN-only issues still exit 0 but print loudly)
  1  one or more hard FAILs — the workflow status should reflect reality

WHAT EACH CHECK DEFENDS AGAINST
  1. Duplicate-ISO check ........ a build step double-writing a country, or the
                                  _meta coverage claim drifting from reality.
  2. Coverage thresholds ........ the "blank-data-shipped" bug — a healthy feed
                                  silently emptying (parser regression, schema
                                  drift) while still passing the non-empty
                                  structural check because it has SOME rows.
  3. Freshness SLA .............. a feed quietly going stale (cron broke, source
                                  404'd for days) without anyone noticing.
  4. Schema shape ............... a stray data/*.json that validate_data doesn't
                                  enumerate but the frontend might fetch.
  5. Provenance ratio guard ..... regression where sourced/verified values get
                                  overwritten by legacy_curated drafts. The goal
                                  is this number going DOWN; the guard catches
                                  it going UP.
  6. US-state semantic test ..... the seeded US-state rows disappearing while
                                  the app still advertises live US-state coverage.
  7. Crisis-feed honesty ........ the no-false-calm rule: if the core crisis
                                  feeds are empty, the nowcast must NOT present
                                  affected countries as high-confidence calm.

All thresholds live in the CONFIG block below so they are easy to tune as the
data improves. Hard FAILs trip exit 1; WARNs are loud but non-fatal.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from _common import DATA_DIR

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — edit these as the data improves. Nothing below this block should
# need touching for routine threshold changes.
# ─────────────────────────────────────────────────────────────────────────────

# (2) Coverage floors per high-value feed. Set from the current healthy counts
#     in source_manifest.json minus a generous margin, so a normal +/- a few
#     countries between releases is fine but a silent collapse to near-zero
#     fails loudly. Keyed by source_manifest source id (not filename).
#     Healthy counts (2026-06-01): usda_psd 151, hdi 195, wgi 216,
#     inform_risk 191, aqueduct 188, cckp 238, lpi 182, worldbank_wdi 249,
#     worldbank_bulk 201, wb_wfso 191.
COVERAGE_FLOORS = {
    "usda_psd": 140,
    "hdi": 190,
    "wgi": 200,
    "inform_risk": 185,
    "aqueduct": 180,
    "cckp": 230,
    "lpi": 175,
    "worldbank_wdi": 240,
    "worldbank_bulk": 190,
    "wb_wfso": 185,
}

# (3) Freshness SLA. For each cadence keyword, the maximum age (in hours) before
#     we WARN. Multipliers are deliberately generous so naturally-slow feeds
#     (biennial LPI, annual HDI) never warn, while a 'daily' feed that's a week
#     stale does. Cadence strings in the manifest are free text
#     ("daily fetch / annual upstream", "6h fetch / monthly upstream", ...) so
#     we match on the FETCH cadence keyword at the start of the string.
#     We only ever WARN on freshness — a stale feed is a heads-up, not a
#     reason to block the daily commit of everything else.
FRESHNESS_SLA_HOURS = {
    "6h":       72,        # 6-hourly fetch — warn after 3 days (12x)
    "daily":    96,        # daily fetch — warn after 4 days
    "weekly":   24 * 21,   # weekly — warn after 3 weeks
    "monthly":  24 * 75,   # monthly — warn after ~2.5 months
    "biennial": 24 * 365 * 3,   # biennial — warn after 3 years
    "annual":   24 * 365 * 2,   # annual — warn after 2 years
    "manual":   None,      # manual snapshots — never freshness-warn
}
# Fallback when no cadence keyword matches: a very generous ceiling so unknown
# cadences don't spuriously warn.
FRESHNESS_DEFAULT_HOURS = 24 * 30

# (5) Provenance baseline. The handoff recorded ~0.84 of countries carrying a
#     legacy_curated fdrs.quality_flag. The remediation goal is to drive this
#     DOWN as values get re-sourced, so the guard WARNs if it goes UP versus
#     this baseline (a regression where sourced values were overwritten by
#     legacy drafts). A small slack absorbs rounding / a single re-classified
#     country. NOTE: as of 2026-06-01 the live ratio is still 1.0 (no fields
#     re-sourced yet); the baseline stays at the handoff target so the guard
#     starts protecting progress the moment re-sourcing begins. Lower this as
#     the real ratio drops.
LEGACY_CURATED_BASELINE = 0.84
PROVENANCE_SLACK = 0.02

# (6) US-state semantic test. If nowcast claims live US-state coverage, this
#     many 'US-' keys must be present.
US_STATE_MIN = 50

# Crisis feeds whose emptiness must be reflected honestly in the nowcast.
CRISIS_FEED_IDS = ["wfp_hungermap", "ipc", "wfp_country"]


# ─────────────────────────────────────────────────────────────────────────────
# Result plumbing
# ─────────────────────────────────────────────────────────────────────────────

class Report:
    """Collects PASS/WARN/FAIL lines and tracks whether any hard FAIL occurred."""

    def __init__(self):
        self.had_fail = False
        self.n_pass = 0
        self.n_warn = 0
        self.n_fail = 0

    def pass_(self, check, msg):
        self.n_pass += 1
        print(f"  PASS {check:24s} — {msg}")

    def warn(self, check, msg):
        self.n_warn += 1
        print(f"  WARN {check:24s} — {msg}")

    def fail(self, check, msg):
        self.n_fail += 1
        self.had_fail = True
        print(f"  FAIL {check:24s} — {msg}")


def _load(filename):
    """Load a data/<filename> envelope. Returns (env_or_None, err_or_None)."""
    p = DATA_DIR / filename
    if not p.exists():
        return None, "missing file"
    try:
        return json.loads(p.read_text()), None
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    except OSError as e:
        return None, f"read error: {e}"


def _parse_dt(s):
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cadence_keyword(cadence):
    """Map a manifest cadence string to a FRESHNESS_SLA_HOURS key, or None."""
    if not cadence or not isinstance(cadence, str):
        return None
    low = cadence.lower()
    # Order matters: check the more specific keywords first.
    for kw in ("6h", "daily", "weekly", "biennial", "annual", "monthly", "manual"):
        if kw in low:
            return kw
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Check 1 — duplicate ISO + coverage-claim consistency
# ─────────────────────────────────────────────────────────────────────────────

def check_duplicate_iso(rep):
    env, err = _load("countries.json")
    if err:
        rep.fail("duplicate-iso", f"countries.json: {err}")
        return
    data = (env.get("data") or {})
    countries = data.get("countries")
    if not isinstance(countries, dict):
        rep.fail("duplicate-iso", "data.countries is not a dict")
        return

    keys = list(countries.keys())
    n = len(keys)

    # JSON dict keys are inherently unique, but a normalized collision
    # (whitespace / case) would mean two near-identical ISO codes shipped.
    norm_map = {}
    for k in keys:
        nk = str(k).strip().upper()
        norm_map.setdefault(nk, []).append(k)
    norm_dupes = {nk: orig for nk, orig in norm_map.items() if len(orig) > 1}
    if norm_dupes:
        rep.fail("duplicate-iso",
                 f"normalized ISO collisions: "
                 f"{', '.join(f'{nk}={orig}' for nk, orig in norm_dupes.items())}")
    else:
        rep.pass_("duplicate-iso", f"{n} unique ISO keys, no normalized collisions")

    # Cross-check any inline list of ISO codes if one exists alongside the dict.
    inline = data.get("country_list") or data.get("countries_list")
    if isinstance(inline, list):
        seen, list_dupes = set(), set()
        for c in inline:
            cu = str(c).strip().upper()
            if cu in seen:
                list_dupes.add(cu)
            seen.add(cu)
        if list_dupes:
            rep.fail("duplicate-iso",
                     f"inline country list has duplicates: {sorted(list_dupes)}")

    # Coverage-claim consistency: _meta.coverage.countries must match the
    # actual number of country keys.
    coverage = (env.get("_meta") or {}).get("coverage") or {}
    claimed = coverage.get("countries")
    if claimed is None:
        rep.warn("coverage-claim", "no _meta.coverage.countries to cross-check")
    elif claimed != n:
        rep.fail("coverage-claim",
                 f"_meta claims {claimed} countries but data.countries has {n}")
    else:
        rep.pass_("coverage-claim", f"_meta coverage ({claimed}) matches data ({n})")


# ─────────────────────────────────────────────────────────────────────────────
# Check 2 — coverage thresholds per high-value feed
# ─────────────────────────────────────────────────────────────────────────────

def _manifest_sources():
    """Return (sources_dict, err). sources_dict maps id -> source object."""
    env, err = _load("source_manifest.json")
    if err:
        return None, err
    data = env.get("data") or {}
    sources = data.get("sources")
    if not isinstance(sources, dict):
        return None, "data.sources missing or not a dict"
    return sources, None


def check_coverage_thresholds(rep):
    sources, err = _manifest_sources()
    if err:
        rep.fail("coverage-floor", f"source_manifest.json: {err}")
        return
    for sid, floor in sorted(COVERAGE_FLOORS.items()):
        src = sources.get(sid)
        if not isinstance(src, dict):
            rep.warn("coverage-floor", f"{sid}: not in manifest (skipped)")
            continue
        count = src.get("count")
        if not isinstance(count, int):
            rep.warn("coverage-floor", f"{sid}: count is {count!r} (skipped)")
            continue
        if count < floor:
            rep.fail("coverage-floor",
                     f"{sid}: count {count} < floor {floor} "
                     f"(healthy feed may have silently emptied)")
        else:
            rep.pass_("coverage-floor", f"{sid}: count {count} >= floor {floor}")


# ─────────────────────────────────────────────────────────────────────────────
# Check 3 — freshness SLA (WARN only)
# ─────────────────────────────────────────────────────────────────────────────

def check_freshness(rep):
    sources, err = _manifest_sources()
    if err:
        rep.fail("freshness", f"source_manifest.json: {err}")
        return
    now = datetime.now(timezone.utc)
    checked = 0
    for sid in sorted(sources):
        src = sources[sid]
        if not isinstance(src, dict):
            continue
        cadence = src.get("cadence")
        kw = _cadence_keyword(cadence)
        if kw == "manual":
            continue  # manual snapshots never freshness-warn
        gen = _parse_dt(src.get("generated_at"))
        if gen is None:
            # Static helpers legitimately have generated_at=null; only warn if
            # the source otherwise looks like a live/fetched feed.
            if src.get("mode") in ("static_helper",) or src.get("status") == "manual":
                continue
            rep.warn("freshness", f"{sid}: no parseable generated_at")
            continue
        sla = FRESHNESS_SLA_HOURS.get(kw, FRESHNESS_DEFAULT_HOURS)
        if sla is None:
            continue
        age_h = (now - gen).total_seconds() / 3600.0
        checked += 1
        if age_h > sla:
            rep.warn("freshness",
                     f"{sid}: {age_h:.0f}h old (cadence={kw or 'unknown'}, "
                     f"SLA {sla}h) — feed may be stale")
    rep.pass_("freshness", f"{checked} dated feeds within their freshness SLA "
                           f"(stale feeds, if any, warned above)")


# ─────────────────────────────────────────────────────────────────────────────
# Check 4 — schema shape across every data/*.json
# ─────────────────────────────────────────────────────────────────────────────

def check_schema_shape(rep):
    files = sorted(DATA_DIR.glob("*.json"))
    # v32 — scope the shape check to SHIPPED feed files only. Archives, scratch
    # worklists and intentionally different shapes (commodity atlas keyed on
    # 'commodities', scenario_spec) were producing ~70 false FAILs that buried
    # the real signal.
    SHAPE_EXEMPT = {"commodity_flows.json", "scenario_spec.json"}
    files = [p for p in files
             if not p.name.startswith((".archive", "_"))
             and not p.name.endswith((".rebuilt.json", ".bak"))
             and ".bak_" not in p.name
             and p.name not in SHAPE_EXEMPT
             and not (p.name.startswith("commodity_flows_") or p.name.startswith("_wave"))]
    if not files:
        rep.fail("schema-shape", "no data/*.json files found")
        return
    bad = []
    no_version = []
    for p in files:
        try:
            env = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            bad.append(f"{p.name}: parse error ({e})")
            continue
        if not isinstance(env, dict):
            bad.append(f"{p.name}: top-level is {type(env).__name__}")
            continue
        if "_meta" not in env:
            bad.append(f"{p.name}: missing _meta")
            continue
        if "data" not in env:
            bad.append(f"{p.name}: missing data")
            continue
        meta = env.get("_meta") or {}
        # Envelope version may live as 'version' (write_json) or 'schema_version'
        # (composite builders like countries.json).
        if not (meta.get("version") or meta.get("schema_version")):
            no_version.append(p.name)

    if bad:
        for b in bad:
            rep.fail("schema-shape", b)
    else:
        rep.pass_("schema-shape", f"all {len(files)} data/*.json parse with _meta+data")

    if no_version:
        rep.warn("schema-shape",
                 f"{len(no_version)} file(s) missing envelope version: "
                 f"{', '.join(no_version[:6])}{'...' if len(no_version) > 6 else ''}")
    elif not bad:
        rep.pass_("schema-version", f"all {len(files)} envelopes carry a version")


# ─────────────────────────────────────────────────────────────────────────────
# Check 5 — provenance ratio guard (WARN only)
# ─────────────────────────────────────────────────────────────────────────────

def check_provenance_ratio(rep):
    env, err = _load("countries.json")
    if err:
        rep.fail("provenance", f"countries.json: {err}")
        return
    countries = ((env.get("data") or {}).get("countries")) or {}
    if not isinstance(countries, dict) or not countries:
        rep.fail("provenance", "data.countries empty/non-dict")
        return

    total = 0
    legacy = 0
    for iso, body in countries.items():
        fdrs = body.get("fdrs") if isinstance(body, dict) else None
        if not isinstance(fdrs, dict):
            continue
        total += 1
        if fdrs.get("quality_flag") == "legacy_curated":
            legacy += 1

    if total == 0:
        rep.fail("provenance", "no fdrs.quality_flag values found")
        return

    ratio = legacy / total
    msg = (f"legacy_curated fdrs = {legacy}/{total} = {ratio:.3f} "
           f"(baseline {LEGACY_CURATED_BASELINE:.3f}; goal: trending DOWN)")
    if ratio > LEGACY_CURATED_BASELINE + PROVENANCE_SLACK:
        rep.warn("provenance",
                 f"{msg} — INCREASED vs baseline; sourced values may have been "
                 f"overwritten by legacy drafts")
    else:
        rep.pass_("provenance", msg)


# ─────────────────────────────────────────────────────────────────────────────
# Check 6 — US-state semantic test
# ─────────────────────────────────────────────────────────────────────────────

def check_us_states(rep):
    env, err = _load("nowcast.json")
    if err:
        rep.fail("us-states", f"nowcast.json: {err}")
        return
    meta = env.get("_meta") or {}
    coverage = meta.get("coverage") or {}
    data = env.get("data") or {}

    # Does the app claim live US-state coverage? Accept several signals:
    # an explicit coverage flag, or simply the presence of US- rows (the
    # frontend reads them, so their presence is the claim).
    claims = bool(
        coverage.get("us_state_coverage")
        or coverage.get("us_states_live")
        or any(str(k).startswith("US-") for k in data)
    )

    us_keys = [k for k in data if str(k).startswith("US-")]
    if not claims:
        rep.pass_("us-states", "no live US-state coverage claimed — skipped")
        return
    if len(us_keys) >= US_STATE_MIN:
        rep.pass_("us-states",
                  f"{len(us_keys)} US- rows present (>= {US_STATE_MIN})")
    else:
        rep.fail("us-states",
                 f"app implies US-state coverage but only {len(us_keys)} US- rows "
                 f"(expected >= {US_STATE_MIN}); seeded states may have been dropped")


# ─────────────────────────────────────────────────────────────────────────────
# Check 7 — crisis-feed honesty cross-check
# ─────────────────────────────────────────────────────────────────────────────

def check_crisis_honesty(rep):
    sources, err = _manifest_sources()
    if err:
        rep.fail("crisis-honesty", f"source_manifest.json: {err}")
        return

    # Which core crisis feeds are empty/down right now?
    down = []
    for sid in CRISIS_FEED_IDS:
        src = sources.get(sid)
        if not isinstance(src, dict):
            continue
        count = src.get("count")
        status = src.get("status")
        if count == 0 or status in ("degraded", "failed", "setup_required"):
            down.append(sid)

    if not down:
        rep.pass_("crisis-honesty", "all core crisis feeds populated")
        return

    nc, nerr = _load("nowcast.json")
    if nerr:
        rep.fail("crisis-honesty",
                 f"crisis feeds down ({', '.join(down)}) but nowcast.json: {nerr}")
        return
    meta = nc.get("_meta") or {}
    coverage = meta.get("coverage") or {}

    # (a) Minimum bar: nowcast _meta must flag the crisis feeds as not live.
    flags_down = (
        coverage.get("crisis_feeds_live") is False
        or coverage.get("ipc_feed_live") is False
        or coverage.get("wfp_hungermap_feed_live") is False
    )
    if flags_down:
        rep.pass_("crisis-honesty",
                  f"crisis feeds down ({', '.join(down)}) and nowcast _meta "
                  f"correctly flags crisis_feeds_live=false")
    else:
        rep.fail("crisis-honesty",
                 f"crisis feeds down ({', '.join(down)}) but nowcast _meta does "
                 f"NOT flag them — risks presenting absence-of-data as calm")

    # (b) Stronger bar: with the international crisis feeds empty, no country
    #     SCORED OFF THOSE FEEDS may be high-confidence. The nowcast schema (v25)
    #     sets confidence='high' for an international (ISO3) country only when
    #     IPC or WFP HungerMap backs the adjustment, so a 'high' ISO3 row with
    #     all core_signals absent would be a false-confidence bug.
    #
    #     EXCEPTION: rows backed by an independent sourced signal are legitimately
    #     high-confidence even with the international crisis feeds down. US-state
    #     rows (US-XX) draw their confidence from Feeding America's
    #     us_food_insecurity_pct, not IPC/WFP — so they must NOT be flagged here.
    #     We exclude any row that is US- prefixed or carries a populated
    #     us_food_insecurity_pct.
    data = nc.get("data") or {}
    offenders = []
    for key, row in data.items():
        if not isinstance(row, dict):
            continue
        if row.get("confidence") != "high":
            continue
        # Skip rows with an independent sourced backing (US-state insecurity).
        if str(key).startswith("US-"):
            continue
        sigs = row.get("signals") or {}
        if isinstance(sigs, dict) and sigs.get("us_food_insecurity_pct") is not None:
            continue
        core = row.get("core_signals_present") or {}
        if isinstance(core, dict) and not any(core.values()):
            offenders.append(key)
    if offenders:
        rep.fail("crisis-honesty",
                 f"{len(offenders)} nowcast row(s) marked high-confidence with NO "
                 f"core crisis signal present (e.g. {', '.join(offenders[:5])}) "
                 f"while crisis feeds are down — violates no-false-calm rule")
    else:
        rep.pass_("crisis-honesty",
                  "no high-confidence nowcast rows lack a core crisis signal")


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────

def check_formula_integrity(rep):
    """Cross-implementation formula gate (delegates to audit_formulas.py): FDRS weights
    agree across the 3 copies, the amplifier expression matches, FOOD_WEIGHT covers every
    scenario commodity, and no flow-based scenario is a dead shock vs the trade atlas."""
    import subprocess
    script = Path(__file__).with_name("audit_formulas.py")
    if not script.exists():
        rep.warn("formula-integrity", "audit_formulas.py not found (skipped)")
        return
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    if r.returncode == 0:
        rep.pass_("formula-integrity",
                  "FDRS weights/amplifier consistent across builder+runtime, FOOD_WEIGHT complete, no dead shocks")
    else:
        bad = [ln.strip() for ln in (r.stdout + r.stderr).splitlines() if "FAIL" in ln or ln.strip().startswith("!!")]
        rep.fail("formula-integrity", "; ".join(bad) or "audit_formulas.py reported a failure")


def main():
    print("=== QA content checks (runs after validate_data.py) ===")
    rep = Report()

    checks = [
        ("1. Duplicate ISO / coverage claim", check_duplicate_iso),
        ("2. Coverage thresholds",            check_coverage_thresholds),
        ("3. Freshness SLA",                  check_freshness),
        ("4. Schema shape",                   check_schema_shape),
        ("5. Provenance ratio guard",         check_provenance_ratio),
        ("6. US-state semantic test",         check_us_states),
        ("7. Crisis-feed honesty",            check_crisis_honesty),
        ("8. Formula integrity (FDRS/scenarios)", check_formula_integrity),
    ]

    for title, fn in checks:
        print(f"\n--- {title} ---")
        try:
            fn(rep)
        except Exception as e:
            # A QA check crashing is itself a FAIL — we never want a silent skip.
            rep.fail(title.split('.')[0].strip(), f"check raised {type(e).__name__}: {e}")

    print()
    print(f"=== QA summary: {rep.n_pass} pass, {rep.n_warn} warn, {rep.n_fail} fail ===")

    if rep.had_fail:
        print(f"\n{rep.n_fail} hard FAIL(s) — exiting non-zero so the workflow "
              f"status reflects reality. Fix the content issues above before "
              f"the data ships.")
        sys.exit(1)

    if rep.n_warn:
        print("\n" + "!" * 64)
        print(f"!! {rep.n_warn} WARNING(S) — not fatal, but do not ignore:")
        print("!! freshness / provenance regressions tend to precede real breakage.")
        print("!" * 64)

    sys.exit(0)


if __name__ == "__main__":
    main()
