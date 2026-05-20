"""
Orchestrator — run all refresh scripts and build nowcast.

Used by .github/workflows/refresh-data.yml on a daily cron.
Failures in any individual feed are caught and logged so the workflow keeps going.
"""
import sys
import traceback

from _common import safe_run

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
import build_countries_dataset
import build_nowcast
import build_source_manifest


STEPS = [
    ("WFP HungerMap",       refresh_wfp.main),
    ("WFP per-country",     refresh_wfp_country.main),
    ("World Bank WDI",      refresh_worldbank.main),
    ("World Bank Pink Sheet", refresh_worldbank_pink_sheet.main),
    ("FAO FFPI",            refresh_fao_ffpi.main),
    ("ReliefWeb",           refresh_reliefweb.main),
    ("IPC",                 refresh_ipc.main),
    ("ACLED",               refresh_acled.main),
    ("Comtrade",            refresh_comtrade.main),
    ("Feeding America",     refresh_feeding_america.main),
    ("Open-Meteo Weather",  refresh_openmeteo.main),
    ("Open-Meteo Flood",    refresh_openmeteo_flood.main),
    ("USGS Water",          refresh_usgs_water.main),
    ("OpenAQ Air Quality",  refresh_openaq.main),
    ("NASA FIRMS Fires",    refresh_nasa_firms.main),
    ("Eurostat food HICP",  refresh_eurostat.main),
    ("FAOSTAT food CPI",    refresh_faostat.main),
    ("FAOSTAT FBS shares",  refresh_faostat_fbs.main),
    ("FAOSTAT net food trade", refresh_net_food_trade.main),
    ("USDA PSD",            refresh_usda_psd.main),
    ("ND-GAIN climate",     refresh_ndgain.main),
    ("WB WDI bulk",         refresh_worldbank_bulk.main),
    ("WB WFSO",             refresh_wb_wfso.main),
    ("Countries dataset",   build_countries_dataset.main),
    ("Nowcast build",       build_nowcast.main),
    ("Source manifest",     build_source_manifest.main),
]


def main():
    failures = 0
    for label, fn in STEPS:
        try:
            safe_run(label, fn)
        except SystemExit:
            failures += 1
        except Exception:
            failures += 1
            traceback.print_exc()
    print(f"\n=== Done — {len(STEPS) - failures}/{len(STEPS)} steps succeeded ===")
    # Always exit 0 so the workflow commits whatever was produced
    sys.exit(0)


if __name__ == "__main__":
    main()
