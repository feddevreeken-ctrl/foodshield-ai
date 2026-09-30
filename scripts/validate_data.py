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
from trade_schema import summarize_trade_surface, validate_trade_surface

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
    'wfp_country.json':           ('critical', 'dict_nonempty'),
    'ipc.json':                   ('critical', 'dict_nonempty'),
    'reliefweb_alerts.json':      ('soft',     'object'),
    'openaq.json':                ('soft',     'dict_or_empty'),  # API key gated
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
    #   net_food_trade.json — FIXED Jun 2026: FAOSTAT moved the trade aggregates
    #     to the CPC string column (F1982 "Food Excluding Fish"); parser now
    #     matches on CPC code. Returns ~180 countries. Promoted to 'critical'.
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
    'net_food_trade.json':        ('critical', 'dict_nonempty'),
    'usda_psd.json':              ('critical', 'dict_nonempty'),
    'ndgain.json':                ('soft',     'dict_or_empty'),
    'aqueduct.json':              ('soft',     'dict_or_empty'),
    'inform_risk.json':           ('soft',     'dict_or_empty'),
    'wgi.json':                   ('soft',     'dict_or_empty'),
    'lpi.json':                   ('critical', 'dict_nonempty'),
    'hdi.json':                   ('critical', 'dict_nonempty'),
    'cckp.json':                  ('soft',     'dict_or_empty'),
    'fews.json':                  ('soft',     'dict_or_empty'),  # API token gated
    'wb_wfso.json':               ('critical', 'dict_nonempty'),
    # Environmental (per-country dicts; quiet days OK)
    'openmeteo.json':             ('soft',     'dict_or_empty'),
    'openmeteo_flood.json':       ('soft',     'dict_or_empty'),
    'usgs_water.json':            ('soft',     'dict_or_empty'),
    # Composites — built from the above; must populate
    'countries.json':             ('critical', 'object'),
    # v79 — fx_rates.json had no validator at all despite being a scored input
    # (build_nowcast fx_shock + FDRS economic access). An unwatched scored feed
    # is exactly how the FX signal sat dead at 0 for every country unnoticed.
    'fx_rates.json':              ('critical', 'dict_nonempty'),
    'nowcast.json':               ('critical', 'dict_nonempty'),
    'daily_summary.json':         ('critical', 'object'),
    'source_manifest.json':       ('critical', 'object'),
    'companies.json':             ('soft',     'dict_or_empty'),  # built from local data/companies/*.json
    # v46 — commodity news headlines. SOFT: GDELT throttles per-IP under CI
    # egress, so an empty payload is a routine upstream condition, not a break.
    # Deliberately NOT in MUST_HAVE_CRISIS_FEEDS — this feed carries third-party
    # CLAIMS with links, never measured crisis data, and nothing downstream
    # scores off it. The shape check is backed by validate_commodity_news()
    # below, which enforces the item schema hard.
    'commodity_news.json':        ('soft',     'dict_or_empty'),
    # El Niño wire: the same kind of feed (third-party claims with links); the
    # El Niño tab's Reported view reads it. Empty on a quiet cycle is honest.
    'enso_news.json':             ('soft',     'dict_or_empty'),
    'enso_outlook.json':          ('soft',     'dict_or_empty'),
    'enso_gauges.json':           ('soft',     'dict_or_empty'),
    'enso_ports.json':            ('soft',     'dict_or_empty'),
    'enso_freight.json':          ('soft',     'dict_or_empty'),
    'enso_price_analogs.json':    ('soft',     'dict_or_empty'),
    'enso_price_outlook.json':    ('soft',     'enso_price_outlook'),  # derived from FPMA + the harvest model
    'enso_price_risk.json':       ('soft',     'enso_price_risk'),     # grey price-risk bands + their out-of-sample test
    'enso_price_forecast_log.json': ('soft',   'enso_price_forecast_log'),  # append-only frozen bands
    'enso_hindcast.json':         ('soft',     'dict_or_empty'),
    'enso_situation.json':        ('soft',     'dict_or_empty'),
    'enso_strengths.json':        ('soft',     'dict_or_empty'),
    'seasonal_outlook.json':      ('soft',     'dict_or_empty'),  # NMME outlook maps, monthly upstream
    # Observed rain now (CPC gauge analysis, 30 and 7 days vs 1991-2020) for the El Nino map.
    # SOFT: on an upstream failure run_all keeps the last good file, which goes stale, not wrong.
    'rain_anomaly.json':          ('soft',     'rain_grid'),
    # The six observed months of the same map (refresh_rain_months.py), and marks picked by rule from the Reported
    # feeds for its 30-day and week stops (refresh_enso_auto_events.py; empty on a quiet cycle is honest).
    'rain_months.json':           ('soft',     'dict_or_empty'),
    'enso_auto_events.json':      ('soft',     'dict_or_empty'),
    # Official seasonal forecasts marked on the outlook months (hand-curated, like enso_recent_events.json).
    'enso_outlook_events.json':   ('soft',     'dict_or_empty'),
    'enso_forecast_skill.json':   ('soft',     'dict_or_empty'),  # hand-run: scripts/build_enso_forecast_skill.py
    # Weekly archives for the same map (refresh_rain_weeks.py, refresh_sst_weeks.py): calendar weeks, up to 12.
    # SOFT for the same reason; a failed step keeps the last good archive.
    'rain_weeks.json':            ('soft',     'rain_weeks'),
    'sst_weeks.json':             ('soft',     'sst_weeks'),
    # The sea for the same map's month, 30-day and 14-day stops (refresh_sst_months.py; the 7-day stop is
    # sst_anomaly.json). SOFT for the same reason as the rain.
    'sst_months.json':            ('soft',     'sst_recent'),
    # Build-time commodity interpretation. SOFT, and additionally listed in
    # OPTIONAL_FILES below: on a repo that has never run the step (no provider key
    # and no prior build) the file legitimately does not exist yet.
    'commodity_interpretation.json': ('soft',  'dict_or_empty'),
}

# Files whose complete absence is an expected, non-noteworthy state (key-gated
# build steps that skip rather than write). Missing → reported as skipped, not
# as a warning. If the file DOES exist it is validated normally.
OPTIONAL_FILES = {'commodity_interpretation.json'}

# Threshold: this many or more 'critical' failures → exit 1
#
# This tolerance exists for ONE reason: transient feed outages. A single upstream
# API down for a day must not block the daily commit of every other healthy feed
# — that would fail the pipeline routinely and is the exact failure mode this
# gate was built to prevent.
CRITICAL_FAILURE_THRESHOLD = 4

# HONESTY FAILURES BLOCK ON THEIR OWN (count 1).
#
# The tolerance above is wrong for a different class of problem. A country-swap in
# PSD, a scoreable number published in the news feed, or LLM prose that failed its
# own numeric validation being shipped as sourced commentary are not transient —
# they are the site making a false claim, and one is enough. Under the shared
# threshold an isolated honesty violation exited 0 and got committed to a public
# repo, where committing IS republication.
#
# Deliberately narrow. Missing files, empty feeds, coverage gaps and below-band
# unit prices all stay under the tolerant threshold, because none of them make the
# site assert something untrue — they make it show less.
HONESTY_BLOCKING = True

# v25 (audit fix) — "must-have" crisis feeds. These are the primary humanitarian
# signals that drive the nowcast. When they are empty, the site is NOT showing
# live crisis data, even if the overall failure count is under the threshold.
# We report this loudly and separately so it is never silently tolerated. We do
# NOT hard-exit on these alone (that would block the daily commit of every OTHER
# healthy feed), but the report makes the gap impossible to miss in CI logs and
# the nowcast itself now flags affected countries as low/no-confidence.
MUST_HAVE_CRISIS_FEEDS = ["ipc.json", "wfp_country.json"]  # v90: wfp_hungermap retired


# v79 — {filename: (scored_field, minimum_non_null_share)}. Only list fields a
# downstream builder actually SCORES, so this stays a signal-integrity gate and
# not a general completeness nag. A collector that declares _meta.status != 'ok'
# is allowed to fall below its floor; an undeclared collapse fails.
# The field may be a dotted path into a nested object ("shock.depr_90d_pct").
SCORED_FIELD_COVERAGE = {
    'ipc.json':           ('phase3plus_pct', 0.50),         # -> nowcast ipc_pressure
    'fx_rates.json':      ('shock.depr_90d_pct', 0.50),     # -> nowcast fx_shock (v79)
    'inform_risk.json':   ('inform_risk', 0.50),            # -> nowcast inform_amp
}


def _dig(row, path):
    """Follow a dotted path into nested dicts; return None if any hop is absent."""
    cur = row
    for part in path.split('.'):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _weeks_chain(data, meta, max_age_days=16):
    """Shared checks of rain_weeks.json / sst_weeks.json: 1..12 calendar weeks (Monday..Sunday), oldest first, no
    overlap, the newest ending within max_age_days of the file's generation. Returns (weeks, error or None)."""
    from datetime import date, datetime
    weeks = data.get('weeks') if isinstance(data, dict) else None
    if not isinstance(weeks, list) or not 1 <= len(weeks) <= 12:
        return None, f"weeks is not a list of 1..12 ({type(weeks).__name__})"
    prev = None
    for w in weeks:
        try:
            s, e = date.fromisoformat(w['start']), date.fromisoformat(w['end'])
        except (KeyError, TypeError, ValueError):
            return None, f"a week without ISO start/end: {str(w)[:60]}"
        if e.weekday() != 6 or (e - s).days != 6:
            return None, f"week {s}..{e} is not Monday..Sunday"
        if prev and s <= prev:
            return None, f"week {s}..{e} overlaps or precedes the one before (end {prev})"
        prev = e
    try:
        made = datetime.fromisoformat(str((meta or {}).get('generated_at'))).date()
    except ValueError:
        return None, "no _meta.generated_at"
    if (made - prev).days > max_age_days:
        return None, f"newest week ends {prev}, {(made - prev).days} days before the file was written: stale"
    return weeks, None


def _check_rain_layer(tag, w, n, max_days, fill_days):
    """One sparse CPC layer of rain_weeks.json (a week, or its d30) and its optional CHIRPS fill: error or None."""
    blocks = [('', w)] + ([('fill.', w['fill'])] if w.get('fill') is not None else [])
    seen = set()
    for pre, b in blocks:
        cells = b.get('cells')
        if not isinstance(cells, list) or cells != sorted(set(cells)) or any(type(k) is not int or not 0 <= k < n
                                                                             for k in cells):
            return f"{tag}: {pre}cells is not an ascending list of grid indexes"
        if seen & set(cells):
            return f"{tag}: fill.cells repeats cells CPC already paints"
        seen |= set(cells)
        if not isinstance(b.get('days'), int) or not 1 <= b['days'] <= (fill_days if pre else max_days):
            return f"{tag}: {pre}days is {b.get('days')}"
        for key in ('anom', 'mm', 'norm_mm', 'spi'):
            arr = b.get(key)
            if arr is None and key == 'spi':
                continue
            if not isinstance(arr, list) or len(arr) != len(cells):
                return f"{tag}: {pre}{key} is not parallel to {pre}cells"
        if any(v is None or type(v) is not int or v < 0 for key in ('mm', 'norm_mm') for v in b[key]):
            return f"{tag}: {pre}mm / norm_mm hold a value that is not a whole mm >= 0"
        if any(v is not None and (type(v) is not int or v < -100) for v in b['anom']):
            return f"{tag}: {pre}anom holds a percent below -100 or not an integer"
        if b.get('spi') is not None and any(v is not None and (type(v) is not int or not -300 <= v <= 300)
                                            for v in b['spi']):
            return f"{tag}: {pre}spi is not an integer in -300..300"
    return None


def _check_rain_weeks(data, meta):
    """refresh_rain_weeks.py: per week, sparse CPC cells and a sparse CHIRPS fill on the rain_anomaly.json grid, and the
    same for the 30 days ending on the week's Sunday (d30)."""
    from datetime import date, timedelta
    weeks, err = _weeks_chain(data, meta)
    if err:
        return False, err
    g = data.get('grid') or {}
    n = (g.get('nlat') or 0) * (g.get('nlon') or 0)
    if n != 52 * 144:
        return False, f"grid is {g.get('nlat')}x{g.get('nlon')}, not the 52x144 rain grid"
    for w in weeks:
        tag = f"week to {w['end']}"
        err = _check_rain_layer(tag, w, n, 7, 6)
        if err:
            return False, err
        if w.get('n_valid', 0) < 300:
            return False, f"{tag}: only {w.get('n_valid')} CPC cells with a percent (the live week has ~800)"
        d = w.get('d30')
        if not isinstance(d, dict):
            return False, f"{tag}: no d30 (the 30 days ending on its Sunday)"
        if d.get('end') != w['end'] or d.get('start') != (date.fromisoformat(w['end']) - timedelta(days=29)).isoformat():
            return False, f"{tag}: d30 runs {d.get('start')}..{d.get('end')}, not the 30 days ending on the week's Sunday"
        err = _check_rain_layer(f"{tag} d30", d, n, 30, 31)
        if err:
            return False, err
        if d.get('n_valid', 0) < 300:
            return False, f"{tag}: only {d.get('n_valid')} d30 CPC cells with a percent (the live 30 days have ~900)"
    last = weeks[-1]
    return True, (f"ok — {len(weeks)} weeks {weeks[0]['start']}..{last['end']}, newest {len(last['cells'])} CPC + "
                  f"{len((last.get('fill') or {}).get('cells') or [])} CHIRPS cells, d30 {len(last['d30']['cells'])} + "
                  f"{len((last['d30'].get('fill') or {}).get('cells') or [])}, "
                  f"{sum(1 for w in weeks if w.get('final'))} final")


def _check_sst_weeks(data, meta):
    """refresh_sst_weeks.py: per week, a full OISST anomaly grid in tenths of a degree C and the Nino box means."""
    weeks, err = _weeks_chain(data, meta)
    if err:
        return False, err
    g = data.get('grid') or {}
    n = (g.get('nlat') or 0) * (g.get('nlon') or 0)
    if n < 10000:
        return False, f"grid is {g.get('nlat')}x{g.get('nlon')}"
    for w in weeks:
        a = w.get('anom')
        if not isinstance(a, list) or len(a) != n:
            return False, f"week to {w['end']}: anom is not a {g.get('nlat')}x{g.get('nlon')} grid"
        vals = [v for v in a if v is not None]
        if len(vals) < 0.5 * n or any(type(v) is not int or not -150 <= v <= 150 for v in vals):
            return False, f"week to {w['end']}: {len(vals)} sea cells, or a value outside +-15 C / not tenths"
        if not isinstance(w.get('days_averaged'), int) or not 6 <= w['days_averaged'] <= 7:
            return False, f"week to {w['end']}: {w.get('days_averaged')} days averaged"
        b = w.get('box_means_c') or {}
        if any(not isinstance(b.get(k), (int, float)) or abs(b[k]) > 6 for k in ('nino34', 'nino12', 'nino3', 'nino4')):
            return False, f"week to {w['end']}: Nino box means missing or implausible ({b})"
    last = weeks[-1]
    return True, (f"ok — {len(weeks)} weeks {weeks[0]['start']}..{last['end']}, newest nino34 "
                  f"{last['box_means_c']['nino34']:+.2f} C ({last.get('dataset')}), "
                  f"{sum(1 for w in weeks if w.get('final'))} final")


def _check_sst_recent(data):
    """refresh_sst_months.py: last30 and last14, each a full OISST anomaly grid in tenths of a degree C ending on the
    same newest day, with the Nino box means; and the months list."""
    from datetime import date, timedelta
    blocks, out = [], []
    for key, n_days, need in (('last30', 30, 27), ('last14', 14, 13)):
        b = data.get(key)
        if not isinstance(b, dict) or not b:
            return False, f"no {key}"
        g = b.get('grid') or {}
        n = (g.get('nlat') or 0) * (g.get('nlon') or 0)
        a = b.get('anom')
        if n < 10000 or not isinstance(a, list) or len(a) != n:
            return False, f"{key}.anom is not a {g.get('nlat')}x{g.get('nlon')} grid"
        vals = [v for v in a if v is not None]
        if len(vals) < 0.5 * n or any(type(v) is not int or not -150 <= v <= 150 for v in vals):
            return False, f"{key}: {len(vals)} sea cells, or a value outside +-15 C / not tenths"
        try:
            s0, e0 = date.fromisoformat(b['start']), date.fromisoformat(b['end'])
        except (KeyError, TypeError, ValueError):
            return False, f"{key} has no ISO start/end"
        if (e0 - s0).days != n_days - 1 or not need <= (b.get('days_averaged') or 0) <= n_days:
            return False, f"{key} runs {s0}..{e0} with {b.get('days_averaged')} days averaged"
        bm = b.get('box_means_c') or {}
        if any(not isinstance(bm.get(k), (int, float)) or abs(bm[k]) > 6 for k in ('nino34', 'nino12', 'nino3', 'nino4')):
            return False, f"{key}: Nino box means missing or implausible ({bm})"
        blocks.append(b)
        out.append(f"{key} {s0}..{e0} nino34 {bm['nino34']:+.2f} C")
    if blocks[0]['end'] != blocks[1]['end'] or blocks[0]['grid'] != blocks[1]['grid']:
        return False, "last30 and last14 do not end on the same day on the same grid"
    if not data.get('months'):
        return False, "no months"
    return True, f"ok — {len(data['months'])} months, " + ", ".join(out)


def _check_enso_price_outlook(text, data):
    """build_enso_price_outlook.py: rows with the fields the Prices lens reads, no NaN, and no model numbers
    unless the model passed its skill gate (a no_skill model must not leak a forecast)."""
    try:
        json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except ValueError as e:
        return False, f"non-finite number in the file: {e}"
    if not isinstance(data, dict) or not isinstance(data.get('rows'), list) or not data['rows']:
        return False, "no rows"
    status = data.get('model_status')
    if status not in ('ok', 'no_skill', 'insufficient_data'):
        return False, f"model_status is {status!r}"
    need = ('iso3', 'country', 'commodity', 'series', 'latest', 'aftermath', 'analog', 'model', 'skill')
    for r in data['rows']:
        miss = [k for k in need if k not in r]
        if miss:
            return False, f"{r.get('iso3')} {r.get('commodity')} lacks {miss}"
        if not (r['latest'] or {}).get('month') or not isinstance((r['latest'] or {}).get('real_value'), (int, float)):
            return False, f"{r['iso3']} {r['commodity']}: no latest month/value"
        if status != 'ok' and r['model'] is not None:
            return False, f"{r['iso3']} {r['commodity']}: model numbers published while model_status is {status}"
    if status != 'ok' and data.get('coefficients') is not None:
        return False, f"coefficients published while model_status is {status}"
    n_model = sum(r['model'] is not None for r in data['rows'])
    return True, f"ok — {len(data['rows'])} rows, model {status} ({n_model} rows with model numbers)"


def _check_enso_price_risk(text, data):
    """build_price_risk_band.py: finite numbers, ordered quantiles, a band only for a series that passed its test, the
    spec hash, and a verdict on the ONI test (the page prints the ONI sentence only when the verdict says adds_nothing)."""
    try:
        json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except ValueError as e:
        return False, f"non-finite number in the file: {e}"
    if not isinstance(data, dict) or not isinstance(data.get('series'), list) or not data['series']:
        return False, "no series"
    if not data.get('spec_hash') or (data.get('oni_test') or {}).get('verdict') not in ('adds_nothing', 'improves'):
        return False, "no spec hash or no ONI verdict"
    n_pass = 0
    for e in data['series']:
        for h, x in (e.get('h') or {}).items():
            q = [x.get(k) for k in ('p10', 'p50', 'p90')]
            if all(isinstance(v, (int, float)) for v in q) and not q[0] <= q[1] <= q[2]:
                return False, f"{e.get('key')} h{h}: quantiles out of order {q}"
            if x.get('pass') and not all(isinstance(v, (int, float)) for v in q):
                return False, f"{e.get('key')} h{h}: passes but has no numbers"
            n_pass += bool(x.get('pass')) and h == str(data.get('default_horizon'))
        d = e.get('decomposition')
        if d and abs(d['world_pct'] + d['fx_pct'] + d['local_pct'] - d['total_pct']) > 0.35:
            return False, f"{e.get('key')}: decomposition does not add up"
    return True, f"ok — {len(data['series'])} series, {n_pass} with a {data.get('default_horizon')}-month band, ONI {data['oni_test']['verdict']}"


def _check_enso_price_forecast_log(text, data):
    """Append-only log: dated entries in order, one per run date, each carrying the spec hash."""
    es = (data or {}).get('entries') if isinstance(data, dict) else None
    if not isinstance(es, list) or not es:
        return False, "no entries"
    dates = [e.get('run_date') for e in es]
    if dates != sorted(set(dates)):
        return False, "entries are not one per run date in date order"
    if any(not e.get('spec_hash') or not e.get('series') for e in es):
        return False, "an entry lacks its spec hash or series"
    return True, f"ok — {len(es)} entries, first {dates[0]}, last {dates[-1]}"


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
    elif shape == 'rain_grid':
        # refresh_rain_anomaly.py: the page paints anom and week.anom cell by cell on the grid.
        if not isinstance(data, dict) or not data:
            return False, f"no rain grid yet ({notes[:60] if notes else 'no notes'})"
        g, wk, d14 = data.get('grid') or {}, data.get('week') or {}, data.get('d14') or {}
        n = (g.get('nlat') or 0) * (g.get('nlon') or 0)
        for key, arr in (('anom', data.get('anom')), ('week.anom', wk.get('anom')), ('week.mm', wk.get('mm')),
                         ('d14.anom', d14.get('anom')), ('d14.mm', d14.get('mm')), ('d14.norm_mm', d14.get('norm_mm'))):
            if not isinstance(arr, list) or len(arr) != n or n == 0:
                return False, f"{key} is not a {g.get('nlat')}x{g.get('nlon')} grid"
        valid = sorted(v for v in data['anom'] if isinstance(v, (int, float)))
        # CPC cells only (the CHIRPS fill may be absent): ~900 since MIN_GAUGES went from 1 to 3 on 2026-09-28.
        if len(valid) < 600 or sum(v is not None for v in wk['anom']) < 600:
            return False, f"only {len(valid)} valid 30-day CPC cells (expected ~900)"
        med = valid[len(valid) // 2]
        if abs(med) > 40:
            return False, f"median 30-day change over all cells is {med:+d}%: a biased base?"
        # A kept last-good file passes every shape test forever; the live window must also be recent.
        from datetime import date, datetime, timedelta, timezone
        end = (data.get('window') or {}).get('end')
        try:
            age = (datetime.now(timezone.utc).date() - date.fromisoformat(end)).days
        except (TypeError, ValueError):
            return False, "no window.end"
        if age > 6:
            return False, f"30-day window ends {end}, {age} days ago: the CPC step has not refreshed"
        # The 14 days (added 2026-09-29): the 14 days ending on the same newest day as the week and the 30 days.
        if d14.get('end') != end or wk.get('end') != end or d14.get('start') != (
                date.fromisoformat(end) - timedelta(days=13)).isoformat():
            return False, f"d14 runs {d14.get('start')}..{d14.get('end')}, not the 14 days ending {end}"
        if sum(v is not None for v in d14['anom']) < 600:
            return False, f"only {sum(v is not None for v in d14['anom'])} valid 14-day CPC cells"
        # chirps_rain_fill.py: sparse CHIRPS block, parallel to fill.cells, only where CPC has no value.
        fl, extra = data.get('fill'), ''
        if fl is not None:
            cells, fw, fd = fl.get('cells') or [], fl.get('week') or {}, fl.get('d14')
            pairs = [('fill.anom', fl.get('anom'), data['anom']), ('fill.week.anom', fw.get('anom'), wk['anom']),
                     ('fill.mm', fl.get('mm'), None), ('fill.week.mm', fw.get('mm'), None)]
            if fd is not None:  # optional: the fill is written without it if its extra CHC file fails
                pairs += [('fill.d14.anom', fd.get('anom'), d14['anom']), ('fill.d14.mm', fd.get('mm'), d14['mm']),
                          ('fill.d14.norm_mm', fd.get('norm_mm'), None)]
            for key, arr, cpc in pairs:
                if not isinstance(arr, list) or len(arr) != len(cells):
                    return False, f"{key} is not parallel to fill.cells"
                if cpc is not None and any(not 0 <= k < n or (v is not None and cpc[k] is not None)
                                           for k, v in zip(cells, arr)):
                    return False, f"{key} sits on a cell CPC already paints (or off the grid)"
            extra = f", CHIRPS fill {fl.get('n_valid')} cells to {(fl.get('window') or {}).get('end')}"
        # SPI (scripts/spi.py, added 2026-09-28), optional: a collector writes the percent layers without it when a
        # parameter file cannot be read. Where present: SPI x 100 as integers in -300..300, the same length as its
        # layer, and only on cells with a reading (mm). Since 2026-09-28 (evening) arid cells keep their SPI where the
        # percent is blank, so the rule is mm, not anom.
        fl, fw = fl or {}, (fl or {}).get('week') or {}
        fd, spi_n = fl.get('d14') or {}, []
        for key, arr, mm, size in (('spi', data.get('spi'), data.get('mm'), n), ('week.spi', wk.get('spi'), wk['mm'], n),
                                   ('d14.spi', d14.get('spi'), d14['mm'], n),
                                   ('fill.spi', fl.get('spi'), fl.get('mm'), len(fl.get('cells') or [])),
                                   ('fill.week.spi', fw.get('spi'), fw.get('mm'), len(fl.get('cells') or [])),
                                   ('fill.d14.spi', fd.get('spi'), fd.get('mm'), len(fl.get('cells') or []))):
            if arr is None:
                continue
            if not isinstance(arr, list) or len(arr) != size or not isinstance(mm, list) or len(mm) != size:
                return False, f"{key} is not parallel to its layer"
            if any(v is not None and (type(v) is not int or not -300 <= v <= 300 or w is None) for v, w in zip(arr, mm)):
                return False, f"{key} holds a value that is not an integer in -300..300, or sits on a cell without a reading"
            spi_n.append(f"{key} {sum(v is not None for v in arr)}")
        extra += f", SPI cells: {', '.join(spi_n)}" if spi_n else ", no SPI"
        return True, (f"ok — {(data.get('window') or {}).get('start')}..{(data.get('window') or {}).get('end')}, "
                      f"{len(valid)} cells, median {med:+d}%{extra}")
    elif shape == 'enso_price_outlook':
        return _check_enso_price_outlook(p.read_text(), data)
    elif shape == 'enso_price_risk':
        return _check_enso_price_risk(p.read_text(), data)
    elif shape == 'enso_price_forecast_log':
        return _check_enso_price_forecast_log(p.read_text(), data)
    elif shape == 'sst_recent':
        if not isinstance(data, dict) or not data:
            return False, f"no sea months yet ({notes[:60] if notes else 'no notes'})"
        return _check_sst_recent(data)
    elif shape in ('rain_weeks', 'sst_weeks'):
        if not isinstance(data, dict) or not data:
            return False, f"no weekly archive yet ({notes[:60] if notes else 'no notes'})"
        return (_check_rain_weeks if shape == 'rain_weeks' else _check_sst_weeks)(data, env.get('_meta'))
    elif shape == 'object':
        if data is None:
            return False, "data is null"
    elif shape == 'flexible':
        if data is None:
            return False, "data is null"

    # v79 — SCORED-FIELD COVERAGE. Shape checks alone certified wfp_hungermap.json
    # as healthy while every one of its fcs_pct values was null, which silently
    # zeroed the nowcast's wfp_pressure signal for all 264 countries. A file can
    # be perfectly shaped and carry no usable signal. For feeds whose output is
    # actually scored, require a minimum share of non-null readings — unless the
    # collector already declared itself degraded, in which case the honest
    # declaration is accepted and reported rather than failed.
    rule = SCORED_FIELD_COVERAGE.get(filename)
    if rule and isinstance(data, dict) and data:
        field, min_share = rule
        # Keys starting with "_" are internal stores (e.g. fx_rates' _ccy_history),
        # not country rows — they must not dilute the coverage share.
        rows = [v for k, v in data.items()
                if isinstance(v, dict) and not str(k).startswith('_')]
        if rows:
            present = sum(1 for v in rows if isinstance(_dig(v, field), (int, float)))
            share = present / len(rows)
            declared = (env.get('_meta') or {}).get('status')
            if share < min_share:
                if declared and declared != 'ok':
                    return True, (f"{field} on {present}/{len(rows)} rows ({share:.0%} < "
                                  f"{min_share:.0%}) — collector declared '{declared}', "
                                  f"accepted as an honest degradation")
                return False, (f"{field} present on only {present}/{len(rows)} rows "
                               f"({share:.0%} < {min_share:.0%}) and _meta.status does not "
                               f"declare a degradation — a scored field this sparse "
                               f"contributes nothing yet reads as healthy")

    return True, "ok"


def validate_displayed_scores():
    from build_countries_dataset import displayed_snapshot
    from datetime import datetime
    failures = []
    try:
        snapshot = displayed_snapshot()
        rows = json.loads((DATA_DIR / "countries.json").read_text())["data"]["countries"]
        for iso, row in rows.items():
            expected = snapshot["scores"][iso]
            displayed, base, delta = (row.get(k) for k in
                ("fdrs_displayed", "fdrs_displayed_base", "fdrs_nowcast_delta"))
            if not isinstance(displayed, (int, float)) or not 0 <= displayed <= 100:
                failures.append(f"{iso}: displayed score outside 0..100")
            if displayed != expected["displayed"] or base != expected["base"]:
                failures.append(f"{iso}: displayed/base differs from shared scorer")
            if not isinstance(delta, (int, float)) or not isinstance(base, (int, float)) or delta != displayed - base:
                failures.append(f"{iso}: delta differs from displayed minus base")
            if row.get("fdrs_displayed_inputs") != snapshot["inputs"]:
                failures.append(f"{iso}: displayed input versions differ from current files")
            try:
                if datetime.fromisoformat(row.get("fdrs_displayed_at", "")).tzinfo is None:
                    raise ValueError("timezone required")
            except (TypeError, ValueError):
                failures.append(f"{iso}: displayed timestamp is not an ISO datetime with timezone")
    except Exception as exc:
        failures.append(f"displayed scorer failed: {exc}")
    print(f"Displayed score parity/range/delta/versions: {len(failures)} failures")
    return failures


def main():
    print(f"=== Data integrity check ({len(EXPECTED_FILES)} files) ===")
    critical_failures = []
    # Honesty violations: any single one blocks. See HONESTY_BLOCKING above.
    honesty_failures = [("countries.json", message) for message in validate_displayed_scores()]
    soft_warnings = []
    ok_count = 0
    for filename in sorted(EXPECTED_FILES):
        criticality, shape = EXPECTED_FILES[filename]
        if filename in OPTIONAL_FILES and not (DATA_DIR / filename).exists():
            print(f"  SKIP  {filename:34s} (optional / not generated this build)")
            continue
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

    # Cross-file content checks (May 2026): a corrupt envelope can still be
    # valid JSON, so we run targeted semantic checks for known-bug-prone files.
    psd_failures = validate_usda_psd()
    if psd_failures:
        print("\nUSDA PSD content failures:")
        for msg in psd_failures:
            print(f"  - {msg}")
        # Treat any PSD content failure as critical — these are silent-data-
        # corruption bugs that have re-emerged 3+ times during 2026.
        critical_failures.extend(("usda_psd.json", m) for m in psd_failures)
        # A country swap publishes one country's balance sheet under another
        # country's name. That is a false claim about a named place, not a
        # coverage gap, so it blocks on its own. The row-count thresholds in the
        # same function are coverage checks and stay under the tolerant threshold.
        honesty_failures.extend(
            ("usda_psd.json", m) for m in psd_failures
            if 'known-bad swap' in m or 'canonical ISO' in m)

    # v45 — implied unit price vs USDA PSD tonnage. Catches the row-duplication
    # class of bug that a shape check cannot see (see validate_comtrade_unit_prices).
    #
    # v58 — IF YOU ARE HERE BECAUSE THIS IS FAILING WITH ~17 OVER-COUNTED PAIRS,
    # DO NOT WIDEN THE BANDS. Measured 2026-07-19, same validator, same PSD file,
    # only the comtrade input swapped:
    #     produced by main's refresh_comtrade.py   ->  17 over-counted
    #     produced by this branch's version        ->   2 over-counted
    # The difference is the v45 dedup in refresh_comtrade.py. Comtrade returns
    # per-mode and partner2 breakout rows that already sum to the motCode=0
    # total, so summing every returned row counts the same trade several times.
    # Main's script lacks that dedup; this branch's has it — grep for
    # "motCode=0 AND partner2Code=0".
    #
    # A large over-counted number therefore means the DATA came from an
    # un-deduped run, not that this check is too strict. run_all.py regenerates
    # comtrade_staples.json BEFORE this validator runs (refresh-data.yml:73 then
    # :87), so CI self-clears on the first run after the fixed script lands. It
    # persists only locally, where regenerating needs COMTRADE_API_KEY.
    unit_price_failures = validate_comtrade_unit_prices()
    if unit_price_failures:
        # Severity split. An implied price ABOVE the band means the USD side is
        # over-counted -- the v45 row-duplication regression -- and must block.
        # BELOW the band is a different, pre-existing condition: partner coverage
        # gaps or a PSD/Comtrade vintage mismatch. Those are real and worth
        # printing, but they are long-standing and making them critical would
        # fail the pipeline on every run and block the daily commit of every
        # other feed, which is the exact failure mode run_all's gate exists to
        # avoid.
        over = [m for m in unit_price_failures if 'over-counted' in m]
        under = [m for m in unit_price_failures if m not in over]

        if over:
            print("\nComtrade implied unit-price failures (over-counted — blocking):")
            for msg in over[:30]:
                print(f"  - {msg}")
            if len(over) > 30:
                print(f"  - ... plus {len(over) - 30} more")
            critical_failures.extend(("comtrade_staples.json", m) for m in over)

        if under:
            print(f"\nComtrade implied unit-price warnings (below band, "
                  f"pre-existing coverage/vintage gaps — not blocking): {len(under)}")
            for msg in under[:30]:
                print(f"  - {msg}")
            if len(under) > 30:
                print(f"  - ... plus {len(under) - 30} more")

    trade_failures = validate_countries_trade_surface()
    if trade_failures:
        print("\nCountry trade-surface failures:")
        for msg in trade_failures[:30]:
            print(f"  - {msg}")
        if len(trade_failures) > 30:
            print(f"  - ... plus {len(trade_failures) - 30} more")
        critical_failures.extend(("countries.json", m) for m in trade_failures)

    # v46 — commodity-news item schema. Hard-fails on schema drift even though
    # the file itself is registered 'soft': an empty news feed is tolerable, a
    # news feed that has grown a stored article body or a renderable score is
    # not (see validate_commodity_news).
    news_failures = validate_commodity_news()
    if news_failures:
        print("\nCommodity-news schema failures:")
        for msg in news_failures[:30]:
            print(f"  - {msg}")
        if len(news_failures) > 30:
            print(f"  - ... plus {len(news_failures) - 30} more")
        critical_failures.extend(("commodity_news.json", m) for m in news_failures)
        # Publishing a stored article body, or a ranking score rendered as if it
        # measured the world, is a publication-honesty violation on a public repo.
        # One is enough.
        honesty_failures.extend(
            ("commodity_news.json", m) for m in news_failures
            if 'disallowed key' in m or 'is numeric' in m or 'quality_flag' in m)

    # v47 — commodity_interpretation.json semantic guard. EVERY failure here is
    # an honesty failure: they are all forms of "the file claims something about
    # its own prose that is not true".
    interp_failures = validate_commodity_interpretation()
    if interp_failures:
        print("\nCommodity-interpretation honesty failures:")
        for msg in interp_failures[:30]:
            print(f"  - {msg}")
        if len(interp_failures) > 30:
            print(f"  - ... plus {len(interp_failures) - 30} more")
        critical_failures.extend(("commodity_interpretation.json", m) for m in interp_failures)
        honesty_failures.extend(("commodity_interpretation.json", m) for m in interp_failures)

    # A measure shown as in force after its recorded end date misstates the trade situation.
    # refresh_trade_restrictions.expire_measures runs first in run_all, so this only fires if it did not.
    restr_failures = validate_trade_restrictions()
    if restr_failures:
        print("\nTrade-restriction failures:")
        for msg in restr_failures:
            print(f"  - {msg}")
        critical_failures.extend(("trade_restrictions.json", m) for m in restr_failures)
        honesty_failures.extend(("trade_restrictions.json", m) for m in restr_failures)

    # v25 — loud, separate report on the must-have crisis feeds.
    failed_names = {fn for fn, _ in critical_failures}
    crisis_down = [f for f in MUST_HAVE_CRISIS_FEEDS if f in failed_names]
    if crisis_down:
        print("\n" + "!" * 64)
        print(f"!! CRISIS FEEDS EMPTY: {', '.join(crisis_down)}")
        print("!! The nowcast has NO live crisis input for these. Affected")
        print("!! country scores are flagged low/no-confidence in nowcast.json.")
        print("!! The site must not present these as confirmed live crisis data.")
        print("!" * 64)

    # Honesty gate — blocks on ONE. Checked before the tolerant threshold so the
    # reason for a non-zero exit is never ambiguous in CI.
    if honesty_failures:
        print("\n" + "=" * 64)
        print(f"HONESTY VIOLATIONS: {len(honesty_failures)} (any single one blocks)")
        for filename, msg in honesty_failures:
            print(f"  - {filename}: {msg}")
        print("These are false claims, not coverage gaps or transient outages.")
        print("The repo is public, so committing is publishing. Exiting non-zero.")
        print("=" * 64)
        sys.exit(1)

    if len(critical_failures) >= CRITICAL_FAILURE_THRESHOLD:
        print(f"\n{len(critical_failures)} critical failures (>= {CRITICAL_FAILURE_THRESHOLD} threshold). "
              f"Exiting non-zero so the workflow status reflects reality.")
        sys.exit(1)

    if critical_failures:
        print(f"\n{len(critical_failures)} critical failure(s) — under the {CRITICAL_FAILURE_THRESHOLD} "
              f"threshold so the workflow keeps going, but watch the next refresh.")

    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# USDA PSD content-integrity checks
# ─────────────────────────────────────────────────────────────────────────────
#
# Background: refresh_usda_psd.py uses a FAS_TO_ISO3 dict that has been
# repeatedly mis-edited, producing country-swap bugs in the served JSON:
#   - BOL key contained Belarus data (BO is FAS for Belarus, not Bolivia)
#   - NER key contained Nigeria data (NI is FAS for Nigeria, not Niger)
#   - NGA key contained Niger data (NG is FAS for Niger, not Nigeria)
#   - PAN key contained Paraguay data (PA is FAS for Paraguay)
#   - DEU absent (GM is FAS for Germany, dict had only "DE")
# These checks fail loudly if any of those swaps reappear, plus a coarse
# coverage check so we notice if the parser stops capturing imports/exports.

# Canonical country_name → ISO3 for high-stakes cross-checks. Names taken
# directly from USDA PSD bulk CSV.
PSD_NAME_TO_ISO = {
    "Bolivia": "BOL",
    "Belarus": "BLR",
    "Niger": "NER",
    "Nigeria": "NGA",
    "Germany": "DEU",
    "Bulgaria": "BGR",
    "Bangladesh": "BGD",
    "El Salvador": "SLV",
    "Spain": "ESP",
    "Serbia": "SRB",
    "Russia": "RUS",
    "Paraguay": "PRY",
    "Panama": "PAN",
    "Burkina Faso": "BFA",
    "Philippines": "PHL",
    "Burundi": "BDI",
    "Burma": "MMR",
    "Korea, South": "KOR",
    "Korea, North": "PRK",
}


def _get_country_name(body):
    if not isinstance(body, dict):
        return None
    for cmd, cval in body.items():
        if isinstance(cval, dict) and 'country' in cval:
            return cval['country']
    return None


# v45 — implied unit-value bands, USD per tonne. 2024 CIF reference prices with
# roughly 2x headroom either side, so only gross errors trip them.
COMTRADE_UNIT_PRICE_BOUNDS = {
    'wheat':    (150, 600),
    'maize':    (120, 550),
    'rice':     (250, 1200),
    'soybeans': (300, 900),
}
# Comtrade commodity key -> USDA PSD commodity key (PSD calls maize "corn").
_COMTRADE_TO_PSD = {'wheat': 'wheat', 'maize': 'corn', 'rice': 'rice', 'soybeans': 'soybeans'}

# ── Why the other six Comtrade commodities have no band ──────────────────────
# refresh_comtrade.py writes ten commodities (v21 expansion): the four above plus
# palm_oil, sugar, coffee, cocoa, fertilizer and beef. Only four are checkable.
#
# This check divides Comtrade USD by USDA PSD import TONNAGE. refresh_usda_psd.py
# pulls exactly four PSD commodity codes — 410000 wheat, 422110 rice, 440000 corn,
# 2222000 soybeans (grains+pulses and oilseeds bulk ZIPs). Confirmed against the
# live file on 2026-07-18: imports_kt exists for corn (131 rows), rice (121),
# wheat (128) and soybeans (83), and for nothing else.
#
# So for the six below there is NO denominator on disk. A band without a
# denominator cannot fire, and writing one would imply a check that does not
# exist. Inventing plausible-looking price bands for them would be exactly the
# kind of decorative validation this repo exists to avoid, so they are listed
# here instead, with what each would need before a band means anything:
#
#   palm_oil    needs PSD oilseeds "Palm Oil" (code 4243000) added to
#               refresh_usda_psd.py COMMODITY_TO_KEY
#   sugar       needs the PSD sugar bulk ZIP (psd_sugar_csv.zip)
#   coffee      needs the PSD coffee bulk ZIP (psd_coffee_csv.zip)
#   cocoa       no PSD coverage at all — would need ICCO grindings/imports
#   fertilizer  no PSD coverage (not an agricultural commodity in PSD); a band
#               would need IFA or World Bank Pink Sheet urea/DAP tonnage
#   beef        needs the PSD livestock ZIP (psd_livestock_csv.zip)
#
# Until a denominator exists, these ship unchecked by this particular test, and
# saying so plainly is the honest state.
_NO_PSD_TONNAGE = ('palm_oil', 'sugar', 'coffee', 'cocoa', 'fertilizer', 'beef')
# Below this tonnage the denominator is too small to imply a meaningful price.
_MIN_IMPORT_KT = 50.0
# The Comtrade pull is a fixed year (see its _meta.source). PSD rows carry their
# own vintage, and 54 of 463 import rows are pre-2020 -- some from 1979/1990/1998.
# Dividing 2024 USD by 1990 tonnage produces a meaningless unit price and a false
# failure (this fired on ITA/ESP soybeans before the guard existed). Only compare
# when the two vintages are close.
_COMTRADE_YEAR = 2024
_MAX_YEAR_GAP = 2


def validate_comtrade_unit_prices():
    """Catch double-counted Comtrade rows by checking implied USD per tonne.

    Why this exists (v45): the Comtrade public-preview endpoint returns the same
    trade several times over -- once as an aggregate row, again split by
    transport mode, and again by second partner. refresh_comtrade.py summed every
    returned row, overstating USD totals for a subset of importers by 3-11x
    (GBR 4.00x, DEU 3.05x, ESP ~11x, while EGY/ITA/NLD were unaffected).

    Nothing caught it. The registry treats comtrade_staples.json as
    ('soft', 'dict_or_empty') -- a shape check, which passes happily when every
    value is 11x too large.

    Dividing the USD total by USDA PSD import tonnage gives an implied unit price
    that lands far outside any real market band when duplication is present. On
    the pre-fix file this fired on TUR (1559 $/t), GBR (1419), NGA (676) and
    AGO (649) for wheat -- four hard failures where there had been zero.

    Two independent sources, both already on disk. No network access needed.
    """
    failures = []
    forecast_skips = 0   # v73: pairs skipped because PSD vintage is a forecast year
    cp = DATA_DIR / 'comtrade_staples.json'
    pp = DATA_DIR / 'usda_psd.json'
    if not cp.exists() or not pp.exists():
        return failures  # covered by the registry's existence check

    try:
        comtrade = (json.loads(cp.read_text()).get('data') or {})
        psd = (json.loads(pp.read_text()).get('data') or {})
    except json.JSONDecodeError as e:
        return [f"unit-price check skipped, parse error: {e}"]

    if not comtrade or not psd:
        return failures

    for iso, commodities in comtrade.items():
        if not isinstance(commodities, dict):
            continue
        psd_body = psd.get(iso) or {}
        if not isinstance(psd_body, dict):
            continue
        for cmd, entry in commodities.items():
            bounds = COMTRADE_UNIT_PRICE_BOUNDS.get(cmd)
            if not bounds or not isinstance(entry, dict):
                continue
            psd_row = psd_body.get(_COMTRADE_TO_PSD[cmd]) or {}
            kt = psd_row.get('imports_kt')
            usd = entry.get('total_value_usd')
            if not isinstance(kt, (int, float)) or kt < _MIN_IMPORT_KT:
                continue
            # Vintage guard: only compare when the PSD tonnage is from roughly the
            # same year as the Comtrade pull. Without this, pre-2020 PSD rows
            # (54 of 463, some from 1979/1990/1998) produce meaningless unit
            # prices and false failures.
            psd_year = psd_row.get('_year_imports_kt')
            # Each pair carries the year it was pulled for (2025 where complete, else 2024).
            ct_year = entry.get('year') if isinstance(entry.get('year'), int) else _COMTRADE_YEAR
            if not isinstance(psd_year, int) or abs(psd_year - ct_year) > _MAX_YEAR_GAP:
                continue
            # v73 — forecast-vintage guard. In July, PSD rolls its latest vintage
            # to the NEW marketing year (e.g. 2026/27), whose early-season import
            # forecasts are partial (IDN rice: 500 kt forecast vs ~4,000 kt of
            # 2024 actuals). Dividing 2024 Comtrade dollars by a small forecast
            # tonnage produces implied prices far above band — 17 false
            # "over-counted" failures that blocked every commit after the July
            # rollover. Only compare against actual-ish vintages (<= Comtrade
            # year + 1); count skips so a fully-dormant check is visible in CI.
            if psd_year > ct_year + 1:
                forecast_skips += 1
                continue
            if not isinstance(usd, (int, float)) or usd <= 0:
                continue
            implied = usd / (kt * 1000.0)
            lo, hi = bounds
            if implied < lo or implied > hi:
                # Direction matters: too high means the USD side is over-counted
                # (the v45 row-duplication bug). Too low means the USD side is
                # short or the tonnage is overstated -- a coverage/vintage gap,
                # a different failure entirely. Do not conflate them.
                if implied > hi:
                    why = "USD over-counted (duplicated Comtrade rows?)"
                else:
                    why = "USD short or PSD tonnage overstated (coverage/year mismatch?)"
                failures.append(
                    f"{iso}/{cmd}: implied {implied:,.0f} USD/t outside [{lo}, {hi}] "
                    f"(comtrade {usd:,.0f} USD vs PSD {kt:,.0f} kt) - {why}"
                )

    # Make the LIMITS of this check visible, not just its results. A validator
    # that silently covers 4 of 10 commodities reads in CI like a validator that
    # covers all 10.
    present = set()
    for commodities in comtrade.values():
        if isinstance(commodities, dict):
            present.update(commodities)
    unchecked = sorted(present & set(_NO_PSD_TONNAGE))
    print()
    print("=== Comtrade implied unit-price checks ===")
    print(f"  checked commodities:   {sorted(COMTRADE_UNIT_PRICE_BOUNDS)}")
    print(f"  UNCHECKED (no USDA PSD tonnage denominator): {unchecked}")
    print(f"  vintage guard:         PSD year within +/-{_MAX_YEAR_GAP} of "
          f"Comtrade {_COMTRADE_YEAR}, and not a forecast vintage "
          f"(> {_COMTRADE_YEAR + 1})")
    print(f"  forecast-vintage skips: {forecast_skips}"
          + (" — check is DORMANT until PSD carries actuals or Comtrade year advances"
             if forecast_skips and not failures else ""))
    print(f"  failures:              {len(failures)}")
    return failures


def validate_usda_psd():
    """Returns list of failure messages (empty list = all good)."""
    p = DATA_DIR / 'usda_psd.json'
    failures = []
    if not p.exists():
        return ["usda_psd.json missing"]

    try:
        env = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return [f"usda_psd.json parse error: {e}"]

    data = env.get('data') or {}
    if not isinstance(data, dict) or not data:
        return ["usda_psd.json data dict empty/non-dict"]

    # Check 1 — no known ISO/name swaps. Critical-bug list from May 2026.
    KNOWN_BAD = {
        ('BOL', 'Belarus'),
        ('BLR', 'Bolivia'),
        ('NER', 'Nigeria'),
        ('NGA', 'Niger'),
        ('PAN', 'Paraguay'),
        ('PRY', 'Panama'),
        ('BGR', 'Bangladesh'),
        ('BGD', 'Bulgaria'),
        ('SLV', 'Spain'),
        ('ESP', 'El Salvador'),
        ('SRB', 'Russia'),
        ('RUS', 'Serbia'),
        ('BDI', 'Belarus'),
        ('DEU', 'Germany'),  # this would actually be OK; never flagged as a bug
    }
    KNOWN_BAD.discard(('DEU', 'Germany'))  # explicit allow

    swap_failures = []
    for iso, body in data.items():
        name = _get_country_name(body)
        if not name:
            continue
        if (iso, name) in KNOWN_BAD:
            swap_failures.append(f"key {iso} contains country '{name}' (known-bad swap)")

    if swap_failures:
        failures.extend(swap_failures)

    # Check 2 — for every ISO that points at a canonical-mapped name, ISO
    # must equal the canonical ISO for that name.
    name_mismatches = []
    for iso, body in data.items():
        name = _get_country_name(body)
        if not name:
            continue
        expected_iso = PSD_NAME_TO_ISO.get(name)
        if expected_iso and expected_iso != iso:
            name_mismatches.append(
                f"key {iso} stores country '{name}' but canonical ISO is {expected_iso}"
            )
    if name_mismatches:
        failures.extend(name_mismatches)

    # Check 3 — imports_kt and exports_kt non-null for at least 50 rows total.
    # If the parser regresses (May 2026 attribute-label drift), these go to
    # zero across the board and the frontend falls back to heuristics.
    imports_rows = 0
    exports_rows = 0
    imports_zero_iso = []
    for iso, body in data.items():
        if not isinstance(body, dict):
            continue
        for cmd, cval in body.items():
            if not isinstance(cval, dict):
                continue
            if cval.get('imports_kt') is not None:
                imports_rows += 1
            if cval.get('exports_kt') is not None:
                exports_rows += 1

    if imports_rows < 50:
        failures.append(
            f"only {imports_rows} country×commodity rows have non-null imports_kt "
            f"(threshold: 50). Parser may have regressed on Attribute_Description label."
        )
    if exports_rows < 50:
        failures.append(
            f"only {exports_rows} country×commodity rows have non-null exports_kt "
            f"(threshold: 50). Parser may have regressed on Attribute_Description label."
        )

    # Print summary (always, success or failure)
    print()
    print("=== USDA PSD content checks ===")
    print(f"  countries:        {len(data)}")
    print(f"  imports rows:     {imports_rows} (threshold >=50)")
    print(f"  exports rows:     {exports_rows} (threshold >=50)")
    print(f"  swap checks:      {'PASS' if not swap_failures else 'FAIL'}")
    print(f"  name-match check: {'PASS' if not name_mismatches else 'FAIL'}")
    for k in ('BOL', 'BLR', 'NER', 'NGA', 'DEU', 'PAN', 'PRY'):
        name = _get_country_name(data.get(k)) if k in data else None
        marker = name if name else "(absent)"
        print(f"  {k}: {marker}")

    return failures


def validate_countries_trade_surface():
    """Hard-fail if countries.json trade fields drift back into ambiguous metadata."""
    p = DATA_DIR / "countries.json"
    if not p.exists():
        return ["countries.json missing"]
    try:
        env = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return [f"countries.json parse error: {e}"]

    countries = (((env.get("data") or {}).get("countries")) if isinstance(env, dict) else None) or {}
    if not isinstance(countries, dict) or not countries:
        return ["countries.json data.countries missing/empty"]

    failures = []
    raw_failures = validate_trade_surface(countries)
    for item in raw_failures:
        failures.append(
            f"{item['iso3']}.{item['field']}: {item['problem']} "
            f"(quality={item.get('quality_flag')}, source={item.get('source')})"
        )

    # v41 — trade_scope is REQUIRED on populated sourced/partial supplier panels
    # (audit 2026-07-01: 176 scope-less panels let one-commodity routes render as
    # whole-country supplier surfaces). Remediated by
    # trade_pipeline/honesty_remediation.py; this guard keeps regressions loud.
    for iso3, c in countries.items():
        if not isinstance(c, dict):
            continue
        for field in ("suppliers", "supPct"):
            row = c.get(field)
            if (isinstance(row, dict) and row.get("quality_flag") in ("sourced", "partial")
                    and row.get("value") and not row.get("trade_scope")):
                failures.append(
                    f"{iso3}.{field}: missing trade_scope on populated "
                    f"{row.get('quality_flag')} supplier panel")

    print()
    print("=== Country trade schema checks ===")
    summary = summarize_trade_surface(countries)
    for field, counts in summary.items():
        print(
            f"  {field:12s} total={counts['total']:3d} "
            f"sourced={counts['sourced']:3d} partial={counts['partial']:3d} legacy={counts['legacy']:3d}"
        )
    print(f"  metadata failures: {len(failures)}")
    return failures


# ─────────────────────────────────────────────────────────────────────────────
# Commodity-news schema guard (v46)
# ─────────────────────────────────────────────────────────────────────────────
#
# Two things must never happen to this file, and neither is visible to a shape
# check:
#
#   1. AN EXTRA KEY. The repo is public, so committing commodity_news.json is
#      publication. Storing an article body, RSS <description>, summary,
#      snippet or socialimage alongside the headline turns a link into a
#      reproduction. The allow-list below is closed for exactly that reason —
#      a well-meaning "just add a summary field" edit has to fail CI, not ship.
#
#   2. A RENDERABLE SCORE. Relevance / trust / recency are internal ranking
#      inputs computed from our own keyword list. A number in a public JSON gets
#      rendered, and a rendered "relevance: 7.4" reads to a user as a
#      measurement of the world. Any numeric-valued field on an item fails here.
#
# Kept deliberately strict and file-local. Mirrors ITEM_KEYS in
# refresh_commodity_news.py; the two lists must stay in sync.
NEWS_ITEM_ALLOWED_KEYS = {
    'title', 'source', 'url', 'published_at', 'published_label',
    'matched', 'provenance', 'quality_flag', 'dedup_key',
    # v46.1 corridor attribution. Still no article text, no snippet, no score:
    # these are a modeled country list plus SOURCED tonnages from the flow
    # atlas. The guard's purpose — keep reproduced content and internal scores
    # out of a public file — is unchanged.
    'countries_mentioned', 'exposed', 'exposure_kt', 'attribution',
    # Publisher-DECLARED feed image URL only (media:thumbnail / media:content /
    # enclosure). A scraped og:image is still forbidden — the ban exists
    # because that was never offered for syndication; a feed element was.
    'image',
    # v46.2 — why a company/chokepoint-qualified item is present at all.
    'companies', 'chokepoints', 'disruption',
}
# Every item must assert it is a third-party claim, not measured data.
NEWS_ALLOWED_QUALITY_FLAGS = {'claim'}


def validate_commodity_news():
    """Returns list of failure messages (empty list = all good)."""
    p = DATA_DIR / 'commodity_news.json'
    if not p.exists():
        return []  # existence is the registry's job; absence is not a schema fault

    try:
        env = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return [f"commodity_news.json parse error: {e}"]

    data = (env or {}).get('data') or {}
    if not isinstance(data, dict):
        return [f"data is {type(data).__name__}, expected dict"]

    items = data.get('items')
    if items is None:
        items = []
    if not isinstance(items, list):
        return [f"data.items is {type(items).__name__}, expected list"]

    failures = []
    numeric_fields = 0
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            failures.append(f"item[{i}] is {type(item).__name__}, expected object")
            continue

        extra = set(item) - NEWS_ITEM_ALLOWED_KEYS
        if extra:
            failures.append(
                f"item[{i}] carries disallowed key(s) {sorted(extra)} — the stored "
                f"shape is headline+link only; bodies, summaries, snippets, images "
                f"and scores must not be published"
            )

        missing = NEWS_ITEM_ALLOWED_KEYS - set(item)
        if missing:
            failures.append(f"item[{i}] missing required key(s) {sorted(missing)}")

        # No scoreable numeric field, anywhere on the item. bool is a subclass
        # of int in Python, so exclude it explicitly — a flag is not a score.
        #
        # v46.1 — NARROWED, not relaxed. The rule exists to stop OUR internal
        # relevance/trust/recency scores from being published as renderable
        # numbers, because "relevance: 7.4" reads as a measurement of the world
        # when it is a measurement of our keyword list. `exposure_kt` is a
        # different animal: a tonnage read straight out of commodity_flows.json,
        # i.e. sourced third-party data that the page exists to show. Banning it
        # would be the guard misfiring on the thing it was meant to protect.
        # The allow-list is explicit and deliberately short — anything not named
        # here is still rejected.
        SOURCED_NUMERIC_KEYS = {'exposure_kt'}
        for k, v in item.items():
            if isinstance(v, bool) or k in SOURCED_NUMERIC_KEYS:
                continue
            if isinstance(v, (int, float)):
                numeric_fields += 1
                failures.append(
                    f"item[{i}].{k} = {v!r} is numeric — relevance/trust/recency "
                    f"scores are internal ranking inputs and must never be published "
                    f"as a renderable number"
                )

        qf = item.get('quality_flag')
        if qf not in NEWS_ALLOWED_QUALITY_FLAGS:
            failures.append(
                f"item[{i}].quality_flag = {qf!r}; must be one of "
                f"{sorted(NEWS_ALLOWED_QUALITY_FLAGS)} — every item is a third-party "
                f"claim, never measured or sourced data"
            )

        matched = item.get('matched')
        if not isinstance(matched, list):
            failures.append(f"item[{i}].matched must be a list of commodities")
        elif not matched:
            # v46.2 — an EMPTY matched list is now legal, but ONLY when the
            # item earned its place some other way. The rule this replaces
            # existed to stop unattributed items appearing; that still holds.
            # What changed is that "no commodity named" is a truthful state
            # for a Cargill or Red Sea story, and inventing a commodity tag to
            # satisfy a schema would be exactly the fabrication the rule was
            # written to prevent.
            if not (item.get('companies') or item.get('chokepoints')):
                failures.append(
                    f"item[{i}].matched is empty and no companies/chokepoints "
                    f"explain why the item qualified — unattributed item")

    print()
    print("=== Commodity news schema checks ===")
    print(f"  items:            {len(items)}")
    print(f"  key allow-list:   {'PASS' if not any('disallowed key' in f for f in failures) else 'FAIL'}")
    print(f"  numeric fields:   {numeric_fields} (must be 0)")
    print(f"  claim flag:       {'PASS' if not any('quality_flag' in f for f in failures) else 'FAIL'}")

    # ── Per-commodity coverage state ─────────────────────────────────────────
    # The schema check above passes on an empty feed, so a run where five of six
    # commodities were rate-limited scored a clean PASS and printed the status
    # dict as an undifferentiated blob. "Every headline we shipped is well-formed"
    # is not the same claim as "we have news coverage", and a mostly-throttled run
    # is a coverage gap the site must not present as a quiet news day.
    #
    # Deliberately a WARNING, not a failure: GDELT throttles per-IP under CI
    # egress, so this is a routine upstream condition. It must be loud, not fatal.
    status = (data.get('commodity_status') or {})
    if isinstance(status, dict) and status:
        per_commodity = {k: v for k, v in status.items() if not k.startswith('_')}
        bad = {k: v for k, v in per_commodity.items() if v != 'ok'}
        counts = {}
        for c in items:
            if isinstance(c, dict):
                for m in (c.get('matched') or []):
                    counts[m] = counts.get(m, 0) + 1
        print(f"  upstream status:  {len(per_commodity) - len(bad)}/{len(per_commodity)} ok")
        for k in sorted(per_commodity):
            print(f"    {k:12s} {per_commodity[k]:10s} items={counts.get(k, 0)}")
        for k, v in sorted(status.items()):
            if k.startswith('_'):
                print(f"    {k:12s} {v}")
        if bad:
            print("  " + "!" * 60)
            print(f"  !! NEWS COVERAGE GAP: {len(bad)}/{len(per_commodity)} commodities "
                  f"did not complete this pull: "
                  f"{', '.join(f'{k}={v}' for k, v in sorted(bad.items()))}")
            print("  !! Their headline counts are a floor, not a measurement, and must")
            print("  !! not be presented as 'no news'. Non-blocking: upstream throttling")
            print("  !! is routine and failing the commit over it helps nobody.")
            print("  " + "!" * 60)
        zero_but_ok = sorted(k for k, v in per_commodity.items()
                             if v == 'ok' and not counts.get(k))
        if zero_but_ok:
            print(f"  [warn] completed pulls that returned zero items: "
                  f"{', '.join(zero_but_ok)} (genuinely quiet, or the relevance "
                  f"filter is over-tight)")
    else:
        print("  [warn] no commodity_status block — per-commodity coverage is "
              "unknown, so an empty feed cannot be distinguished from a failed one")

    return failures


# ─────────────────────────────────────────────────────────────────────────────
# Commodity-interpretation semantic guard
# ─────────────────────────────────────────────────────────────────────────────
#
# commodity_interpretation.json is the one published file that can contain
# LLM-written prose. build_news_interpretation.py validates its own output, but a
# validator that only runs inside the producing script checks the code, not the
# artefact — a partial run, a hand-edit, or a restored older file all bypass it.
# This reads the file as shipped and asserts the honesty properties that make the
# prose safe to publish at all. Every failure here is an HONESTY failure and
# blocks on its own (see honesty_failures in main).
INTERPRETATION_CONTENT_TYPES = {
    True:  'ai_interpretation',
    False: 'deterministic_template',
}


def validate_trade_restrictions(today=None):
    """In-force (official/reported) measures must not be past their recorded end date."""
    from datetime import date as _date
    today = today or _date.today()
    path = DATA_DIR / "trade_restrictions.json"
    if not path.exists():
        return []
    try:
        obj = json.loads(path.read_text())
    except Exception as e:
        return [f"unreadable: {e}"]
    rows = obj.get("data") if isinstance(obj, dict) else obj
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get("status") not in ("official", "reported") or not r.get("ends_date"):
            continue
        try:
            if _date.fromisoformat(str(r["ends_date"])[:10]) < today:
                out.append(f"{r.get('country') or r.get('iso')} {r.get('commodity')}: ended {r['ends_date']} but still marked {r['status']}")
        except ValueError:
            out.append(f"{r.get('country') or r.get('iso')} {r.get('commodity')}: unreadable ends_date {r['ends_date']!r}")
    return out


def validate_commodity_interpretation():
    """Returns list of failure messages (empty list = all good)."""
    p = DATA_DIR / 'commodity_interpretation.json'
    if not p.exists():
        return []  # optional build step; absence is handled by the registry

    try:
        env = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return [f"commodity_interpretation.json parse error: {e}"]

    data = (env or {}).get('data') or {}
    if not isinstance(data, dict):
        return [f"data is {type(data).__name__}, expected dict"]

    failures = []
    ai_count = template_count = 0
    for key, row in data.items():
        if not isinstance(row, dict):
            failures.append(f"{key} is {type(row).__name__}, expected object")
            continue

        # 1. Nothing here is ever a forecast. The flag is what the site renders
        #    the disclaimer from; a false or missing flag ships unqualified prose.
        if row.get('not_a_forecast') is not True:
            failures.append(
                f"{key}.not_a_forecast = {row.get('not_a_forecast')!r}; must be true — "
                f"this project maps structural exposure and never claims prediction")

        # 2. The label must match the thing. A template labelled AI overstates
        #    what happened; AI prose labelled template hides it. Both mislead, in
        #    opposite directions.
        is_ai = row.get('is_ai_generated_interpretation')
        if not isinstance(is_ai, bool):
            failures.append(
                f"{key}.is_ai_generated_interpretation = {is_ai!r}; must be a bool")
        else:
            expected = INTERPRETATION_CONTENT_TYPES[is_ai]
            if row.get('content_type') != expected:
                failures.append(
                    f"{key}.content_type = {row.get('content_type')!r} but "
                    f"is_ai_generated_interpretation={is_ai} implies {expected!r} — "
                    f"a template must not be labelled AI, nor AI prose a template")
            ai_count += int(is_ai)
            template_count += int(not is_ai)
            # Provider/model attribution must follow the same flag.
            if not is_ai and (row.get('provider') or row.get('model')):
                failures.append(
                    f"{key} is a deterministic template but carries "
                    f"provider={row.get('provider')!r}/model={row.get('model')!r}")

        # 3. THE LOAD-BEARING ONE. If validation rejected the model text, the
        #    published prose must be the substituted template — not the rejected
        #    text with a failure note bolted onto it.
        v = row.get('validation')
        if not isinstance(v, dict):
            failures.append(f"{key}.validation missing or not an object")
            continue
        if v.get('rejected') and is_ai:
            failures.append(
                f"{key}: validation.rejected is true yet the row is still published as "
                f"AI-generated prose. Rejected text must be REPLACED in the output, "
                f"not merely flagged")
        # v47 — 'field_names' added. build_news_interpretation.find_field_names
        # catches a raw FACTS identifier pasted into prose ("as the
        # price_prev_month_value was 216.2"): the figure is correctly licensed,
        # so every numeric check passes and the leak reaches readers unflagged.
        # Omitting it here would leave the new validator unenforced in CI.
        problems = [k for k in ('unsupported_numbers', 'sign_inversions',
                                'word_quantities', 'field_names', 'advice',
                                'unlisted_outlets') if v.get(k)]
        if problems and is_ai:
            failures.append(
                f"{key}: published as AI prose while validation recorded "
                f"{ {k: v.get(k) for k in problems} } — a recorded numeric/direction "
                f"violation must force the deterministic template")
        if v.get('model_used') is not is_ai:
            failures.append(
                f"{key}: validation.model_used={v.get('model_used')!r} disagrees with "
                f"is_ai_generated_interpretation={is_ai!r}")

    print()
    print("=== Commodity interpretation honesty checks ===")
    print(f"  commodities:      {len(data)}")
    print(f"  ai / template:    {ai_count} / {template_count}")
    print(f"  not_a_forecast:   {'PASS' if not any('not_a_forecast' in f for f in failures) else 'FAIL'}")
    print(f"  label match:      {'PASS' if not any('content_type' in f for f in failures) else 'FAIL'}")
    print(f"  rejected-not-published: "
          f"{'PASS' if not any('rejected' in f or 'violation' in f for f in failures) else 'FAIL'}")

    return failures


if __name__ == "__main__":
    main()
