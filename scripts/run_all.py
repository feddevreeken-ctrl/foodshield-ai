"""
Orchestrator — run all refresh scripts and build nowcast.

Used by .github/workflows/refresh-data.yml on a 6h cron.
Failures in any individual feed are caught and logged so the workflow keeps going.

v20.32 — each STEP now carries an explicit `output` filename. safe_run uses
that to write a graceful empty payload on failure (instead of a label-based
stub like `wb_lpi_logistics_FAILED.json` that nothing fetches). After all
steps run, we audit which expected outputs actually carry non-trivial data
on disk and report counts. If too many steps failed (safe_run returns
False per step) or a critical set of outputs is missing/empty, exit
non-zero so the workflow status reflects reality instead of always being
green.
"""
import os
import sys
import time
import traceback
from pathlib import Path

from _common import DATA_DIR, DEFAULT_STEP_TIMEOUT, _has_existing_data, safe_run

# Import each refresh module
import refresh_wfp
import refresh_wfp_country
import refresh_worldbank
import refresh_worldbank_pink_sheet
import refresh_fao_ffpi
import refresh_reliefweb
import refresh_ipc
import refresh_acled
import refresh_comtrade
import refresh_feeding_america
import refresh_openmeteo
import refresh_openmeteo_flood
import refresh_usgs_water
import refresh_openaq
import refresh_eurostat
import refresh_faostat
import refresh_faostat_fbs
import refresh_net_food_trade
import refresh_wb_wfso
import refresh_worldbank_bulk
import refresh_fx
import build_company_overlay
# Trade-field re-verification (sources suppliers/supPct/imports/exports from the
# Comtrade pulls). Lives in the trade_pipeline/ package; import its build().
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "trade_pipeline"))
import build_fields as _trade_build_fields
# v41 — re-derive query-level trade lineage (source_url + trade_scope) that the
# Trade re-verify step clobbers back to homepage URLs. Must run right after it.
import honesty_remediation as _trade_honesty_remediation
import refresh_usda_psd
import refresh_ndgain
import refresh_aqueduct
import refresh_inform
import refresh_wgi
import refresh_lpi
import refresh_hdi
import refresh_cckp
import refresh_fews
import refresh_hapi_idps   # v43 — HDX HAPI internal displacement (new nowcast signal)
import refresh_trade_restrictions
import refresh_commodity_news   # v46 — GDELT + EC RSS commodity headlines (claims, not data)
import build_countries_dataset
import snapshot_fdrs
import validate_fdrs
import build_nowcast
import build_source_manifest
import build_daily_summary
import build_companies
# Build-time AI interpretation per commodity. Consumes commodity_news.json, so it
# MUST stay after the commodity-news ingest step. Provider-agnostic: NEWS_LLM_PROVIDER
# + GROQ_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY. With no key at all it still
# writes a complete deterministic (zero-cost) output and exits 0.
import build_news_interpretation
import build_scenario_profiles


# v20.32 — (label, fn, expected_output_file). The third field is what the
# frontend fetches; safe_run writes an empty envelope there on failure.
STEPS = [
    ("WFP HungerMap",          refresh_wfp.main,                "wfp_hungermap.json"),
    ("WFP per-country",        refresh_wfp_country.main,        "wfp_country.json"),
    ("World Bank WDI",         refresh_worldbank.main,          "worldbank_wdi.json"),
    ("World Bank Pink Sheet",  refresh_worldbank_pink_sheet.main, "worldbank_pink_sheet.json"),
    ("FAO FFPI",               refresh_fao_ffpi.main,           "fao_ffpi.json"),
    ("ReliefWeb",              refresh_reliefweb.main,          "reliefweb_alerts.json"),
    ("IPC",                    refresh_ipc.main,                "ipc.json"),
    ("ACLED",                  refresh_acled.main,              "acled.json"),
    ("Comtrade",               refresh_comtrade.main,           "comtrade_staples.json"),
    ("Feeding America",        refresh_feeding_america.main,    "feeding_america_states.json"),
    ("Open-Meteo Weather",     refresh_openmeteo.main,          "openmeteo.json"),
    ("Open-Meteo Flood",       refresh_openmeteo_flood.main,    "openmeteo_flood.json"),
    ("USGS Water",             refresh_usgs_water.main,         "usgs_water.json"),
    ("OpenAQ Air Quality",     refresh_openaq.main,             "openaq.json"),
    ("Eurostat food HICP",     refresh_eurostat.main,           "eurostat_food.json"),
    ("FAOSTAT food CPI",       refresh_faostat.main,            "faostat_food.json"),
    ("FAOSTAT FBS shares",     refresh_faostat_fbs.main,        "country_caloric_shares.json"),
    ("FAOSTAT net food trade", refresh_net_food_trade.main,     "net_food_trade.json"),
    ("USDA PSD",               refresh_usda_psd.main,           "usda_psd.json"),
    ("ND-GAIN climate",        refresh_ndgain.main,             "ndgain.json"),
    ("WRI Aqueduct water",     refresh_aqueduct.main,           "aqueduct.json"),
    ("INFORM risk index",      refresh_inform.main,             "inform_risk.json"),
    ("WB WGI governance",      refresh_wgi.main,                "wgi.json"),
    ("WB LPI logistics",       refresh_lpi.main,                "lpi.json"),
    ("UNDP HDI",               refresh_hdi.main,                "hdi.json"),
    ("WB CCKP climate",        refresh_cckp.main,               "cckp.json"),
    ("FEWS NET IPC outlook",   refresh_fews.main,               "fews.json"),
    ("HDX HAPI displacement",  refresh_hapi_idps.main,          "hapi_idps.json"),
    ("WB WDI bulk",            refresh_worldbank_bulk.main,     "worldbank_bulk.json"),
    ("WB WFSO",                refresh_wb_wfso.main,            "wb_wfso.json"),
    ("FX rates (v23)",         refresh_fx.main,                 "fx_rates.json"),
    # v40 — trade-restriction monitor. Preserve-safe stub: ships empty and never
    # clobbers owner-curated cited entries. restrictionExposure() in the app computes
    # who's exposed live from the atlas; restrictions must be sourced, never fabricated.
    ("Trade restrictions",     refresh_trade_restrictions.main, "trade_restrictions.json"),
    # v46 — commodity news headlines. Third-party CLAIMS with links, never data:
    # nothing from this feed drives the nowcast or any score. Ships empty when
    # GDELT throttles (routine under CI egress), so it's in EMPTY_OK; the script
    # preserves last-good items rather than blanking them on a zero-item run.
    ("Commodity news",         refresh_commodity_news.main,     "commodity_news.json"),
    ("Countries dataset",      build_countries_dataset.main,    "countries.json"),
    # v40 — daily point-in-time snapshot of the structural FDRS (idempotent/preserve-safe);
    # builds the immutable history the hindcast/validation needs. Reads the fresh countries.json.
    ("FDRS snapshot",          snapshot_fdrs.main,              "fdrs_history.json"),
    # v40 — structural validation of FDRS against independent IPC/FEWS crisis ground
    # truth (ROC-AUC / Spearman / hits+misses). Keeps data/fdrs_validation.json fresh so
    # the methodology whitepaper and any in-app credibility surface stay current.
    ("FDRS validation",        validate_fdrs.main,              "fdrs_validation.json"),
    # v23 — re-verify trade fields (suppliers/supPct/imports/exports) from the
    # Comtrade pulls, patching countries.json AFTER it's built. Must run after
    # "Countries dataset". build_fields.build() is the entrypoint.
    ("Trade re-verify (v23)",  _trade_build_fields.build,       "reverify_records.json"),
    # v41 — Trade re-verify above rewrites supplier/import panels with the Comtrade
    # homepage URL and no trade_scope; this step re-derives the query-level source_url
    # and trade_scope from each row's own metadata so the displayed lineage stays honest
    # every cron (not just after a manual --apply). Refuses to write on assertion failure;
    # the downstream validate_data gate then catches any missing trade_scope.
    ("Trade lineage honesty (v41)", _trade_honesty_remediation.build, "countries.json"),
    ("Nowcast build",          build_nowcast.main,              "nowcast.json"),
    ("Daily summary",          build_daily_summary.main,        "daily_summary.json"),
    ("Source manifest",        build_source_manifest.main,      "source_manifest.json"),
    ("Companies aggregate",    build_companies.main,            "companies.json"),
    # v23 — modeled origin overlay (USDA PSD × disclosed footprint). After companies.
    ("Company overlay (v23)",  build_company_overlay.main,      "company_overlay.json"),
    # Build-time commodity interpretation. MUST run AFTER the commodity-news ingest
    # step (it reads data/commodity_news.json) and after the price/balance feeds it
    # summarises. No client-side key: the provider key lives in Actions secrets and
    # only the finished text is committed. Every number in the output is computed in
    # Python and the model's prose is regex-validated against it, so a cheap free-tier
    # model cannot introduce a figure.
    ("Commodity interpretation", build_news_interpretation.main, "commodity_interpretation.json"),
    ("Scenario profiles",      build_scenario_profiles.main,    "scenario_profiles.json"),
]


# Strict failure mode kicks in only when a substantial chunk of the pipeline is
# broken — one or two failures (e.g. an upstream API hiccup; feed isolation
# preserves last-good data) shouldn't tank the daily commit + Vercel redeploy.
# More than this means something structural is broken and the workflow status
# should reflect that.
FAIL_THRESHOLD = 4

# Feeds that legitimately ship an empty payload — key-gated or upstream-blocked —
# so the post-flight audit must not count their empty envelope as "missing":
#   openaq.json             OPENAQ_API_KEY-gated
#   nasa_firms.json         NASA_FIRMS_MAP_KEY-gated
#   acled.json              ACLED_EMAIL/ACLED_PASSWORD-gated
#   ndgain.json             upstream bulk download sits behind a browser-session
#                           redirect (see the feed's own _meta.notes)
#   trade_restrictions.json ships-empty-by-design preserve-safe stub (v40 note
#                           on the step above); entries are owner-curated
#   commodity_news.json     GDELT throttles per-IP under CI egress; a fully
#                           throttled run legitimately yields zero items (v46)
EMPTY_OK = {"openaq.json", "acled.json", "ndgain.json",
            "trade_restrictions.json", "commodity_news.json"}

# Outputs that legitimately do not exist at all on a first run. v46: the
# interpretation step does NOT skip when no provider key is set — it degrades
# to deterministic templates and still writes the file with
# status=deterministic_only. Keys are resolved GROQ_API_KEY -> GEMINI_API_KEY
# -> ANTHROPIC_API_KEY (build_news_interpretation.PROVIDER_PRIORITY), so a
# missing key changes the prose, never the file's presence.
OPTIONAL_OUTPUTS = {"commodity_interpretation.json"}

# v73 — per-step wall-clock overrides. The Comtrade retry-queue (v45) makes
# ~290 sequential calls at 1.5s throttle plus 30s backoffs on every 429, so the
# default 900s cap kills it before the queue drains — every CI run since the
# dedup fix landed hit [TIMEOUT] and kept serving the pre-dedup (over-counted)
# file. 2700s covers the observed worst case (~55 rate-limited calls × up to
# 3 × 30s backoff) with headroom; all other steps keep the 900s default.
STEP_TIMEOUTS = {"Comtrade": 2700}


# v79 — GLOBAL WALL-CLOCK BUDGET.
#
# The refresh had not committed since 2026-08-03: every scheduled run failed and
# the whole site went stale, news included. The cause is arithmetic, not a
# broken feed. There are 46 steps and each may burn the per-step timeout before
# safe_run gives up, so a run where a handful of upstreams hang can spend
# 9 x 15min = 135 min just waiting — past the workflow's 120-minute ceiling.
# GitHub then kills the job mid-pipeline, so validate / QA / commit never run.
# Nothing names a bad feed; the run simply vanishes and every dataset ages
# together. Exactly the silent-staleness failure this project keeps designing
# against.
#
# Per-step caps alone cannot bound a 46-step total, so this caps the SUM. Once
# the budget is spent the remaining feeds are skipped rather than started.
# safe_run already preserves last-good data, so a skipped feed goes honestly
# stale instead of empty — and, critically, the run always reaches the gates and
# the commit step, so whatever DID refresh actually ships.
#
# Skipped steps still count as failures, so the exit gate and the workflow email
# fire as before. The goal is not to hide the problem; it is to stop one slow
# upstream from silently freezing every other feed on the site.
RUN_BUDGET_SECONDS = int(os.environ.get("FOODSHIELD_RUN_BUDGET", "5400"))  # 90 min


def main():
    failures = 0
    failed_labels = []
    skipped_labels = []
    run_started = time.monotonic()
    for step in STEPS:
        elapsed = time.monotonic() - run_started
        if elapsed > RUN_BUDGET_SECONDS:
            label_only = step[0]
            skipped_labels.append(label_only)
            failures += 1
            failed_labels.append(label_only)
            print(f"[SKIP] {label_only} — run budget ({RUN_BUDGET_SECONDS}s) "
                  f"exhausted at {elapsed:.0f}s; keeping last-good data so the "
                  f"run still reaches validation and commit")
            continue
        # Tolerate the legacy 2-tuple form during the rollout, but new code uses 3-tuples.
        if len(step) == 3:
            label, fn, output = step
        else:
            label, fn = step
            output = None
        # safe_run catches everything internally and returns True/False; the
        # except arms are belt-and-braces for anything that escapes it.
        #
        # v79 — clamp this step's cap to whatever budget is left. Checking the
        # budget only BEFORE a step is not sufficient: Comtrade carries a 2700s
        # override, so starting it at minute 89 would run to minute 134 and blow
        # the 120-min ceiling anyway — the exact failure the budget exists to
        # prevent. Clamping makes the total genuinely bounded rather than
        # bounded-until-the-last-long-step.
        remaining = max(30, int(RUN_BUDGET_SECONDS - (time.monotonic() - run_started)))
        step_timeout = min(STEP_TIMEOUTS.get(label, DEFAULT_STEP_TIMEOUT), remaining)
        try:
            ok = safe_run(label, fn, output_name=output, timeout=step_timeout)
        except SystemExit:
            ok = False
        except Exception:
            ok = False
            traceback.print_exc()
        if not ok:
            failures += 1
            failed_labels.append(label)

    # v20.32 — Post-flight audit. Every step is supposed to leave a NON-TRIVIAL
    # payload at data/<output> (safe_run preserves last-good data on failure, so
    # an empty stub means the feed has never worked). Mere file existence is not
    # enough — a stub envelope would pass that while the frontend shows nothing.
    # Feeds in EMPTY_OK legitimately ship empty and are exempt from the
    # non-trivial requirement (but must still exist).
    expected_files = [s[2] for s in STEPS
                      if len(s) == 3 and s[2] not in OPTIONAL_OUTPUTS]
    missing = [f for f in expected_files
               if not (DATA_DIR / f).exists()
               or (f not in EMPTY_OK and not _has_existing_data(f))]
    present = len(expected_files) - len(missing)
    print(f"\n=== Done — {len(STEPS) - failures}/{len(STEPS)} steps succeeded ===")
    if failed_labels:
        print(f"=== Failed steps: {failed_labels}")
    if skipped_labels:
        print(f"=== Skipped (run budget exhausted): {skipped_labels}")
        print("=== These kept their previous data. If this list is non-empty on "
              "consecutive runs, a slow upstream is crowding out the tail of the "
              "pipeline — reorder STEPS or raise FOODSHIELD_RUN_BUDGET.")
    print(f"=== Output audit — {present}/{len(expected_files)} expected files present with data ===")
    if missing:
        print(f"=== Missing/empty files: {missing}")
    if failures > FAIL_THRESHOLD:
        print(f"=== {failures} steps failed (> {FAIL_THRESHOLD}); exiting non-zero ===")
        sys.exit(1)
    if len(missing) > 4:
        print(f"=== >4 expected outputs missing/empty; exiting non-zero ===")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
