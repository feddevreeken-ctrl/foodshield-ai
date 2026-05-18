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
import refresh_worldbank
import refresh_fao_ffpi
import refresh_reliefweb
import refresh_ipc
import refresh_acled
import refresh_comtrade
import refresh_feeding_america
import build_nowcast


STEPS = [
    ("WFP HungerMap",    refresh_wfp.main),
    ("World Bank WDI",   refresh_worldbank.main),
    ("FAO FFPI",         refresh_fao_ffpi.main),
    ("ReliefWeb",        refresh_reliefweb.main),
    ("IPC",              refresh_ipc.main),
    ("ACLED",            refresh_acled.main),
    ("Comtrade",         refresh_comtrade.main),
    ("Feeding America",  refresh_feeding_america.main),
    ("Nowcast build",    build_nowcast.main),
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
