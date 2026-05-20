"""
Orchestrator — run all refresh scripts and build nowcast.

Used by .github/workflows/refresh-data.yml on a 6h cron.
Failures in any individual feed are caught and logged so the workflow keeps going.

v20.32 — each STEP now carries an explicit `output` filename. safe_run uses
that to write a graceful empty payload on failure (instead of a label-based
stub like `wb_lpi_logistics_FAILED.json` that nothing fetches). After all
steps run, we audit which expected outputs actually exist on disk and
report counts. If a critical set is missing, exit non-zero so the workflow
status reflects reality instead of always being green.
"""
import sys
import traceback
from pathlib import Path

from _common import DATA_DIR, safe_run

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
import refresh_nasa_firms
import refresh_eurostat
import refresh_faostat
import refresh_faostat_fbs
import refresh_net_food_trade
import refresh_wb_wfso
import refresh_worldbank_bulk
import refresh_usda_psd
import refresh_ndgain
import refresh_aqueduct
import refresh_inform
import refresh_wgi
import refresh_lpi
import refresh_hdi
import refresh_cckp
import build_countries_dataset
import build_nowcast
import build_source_manifest
import build_daily_summary


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
    ("NASA FIRMS Fires",       refresh_nasa_firms.main,         "nasa_firms.json"),
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
    ("WB WDI bulk",            refresh_worldbank_bulk.main,     "worldbank_bulk.json"),
    ("WB WFSO",                refresh_wb_wfso.main,            "wb_wfso.json"),
    ("Countries dataset",      build_countries_dataset.main,    "countries.json"),
    ("Nowcast build",          build_nowcast.main,              "nowcast.json"),
    ("Daily summary",          build_daily_summary.main,        "daily_summary.json"),
    ("Source manifest",        build_source_manifest.main,      "source_manifest.json"),
]


def main():
    failures = 0
    for step in STEPS:
        # Tolerate the legacy 2-tuple form during the rollout, but new code uses 3-tuples.
        if len(step) == 3:
            label, fn, output = step
        else:
            label, fn = step
            output = None
        try:
            safe_run(label, fn, output_name=output)
        except SystemExit:
            failures += 1
        except Exception:
            failures += 1
            traceback.print_exc()

    # v20.32 — Post-flight audit. Every step is supposed to leave a file at
    # data/<output>. Report which expected outputs are missing, and exit
    # non-zero if more than 4 are absent so the workflow status doesn't lie.
    expected_files = [s[2] for s in STEPS if len(s) == 3]
    missing = [f for f in expected_files if not (DATA_DIR / f).exists()]
    present = len(expected_files) - len(missing)
    print(f"\n=== Done — {len(STEPS) - failures}/{len(STEPS)} steps succeeded ===")
    print(f"=== Output audit — {present}/{len(expected_files)} expected files present ===")
    if missing:
        print(f"=== Missing files: {missing}")
    # Strict failure mode kicks in only when a substantial chunk is missing —
    # one or two missing files (e.g. an upstream API hiccup) shouldn't tank
    # the daily commit + Vercel redeploy. >4 missing means something structural
    # is broken and the workflow status should reflect that.
    if len(missing) > 4:
        print(f"=== >4 expected outputs missing; exiting non-zero ===")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
