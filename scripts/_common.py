"""
FoodShield AI — Common utilities for data refresh scripts.

All refresh scripts share these helpers for HTTP fetching, retries, and JSON output.
Designed to run in GitHub Actions (Ubuntu, Python 3.11+).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Repo root: scripts/_common.py -> ../
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

UA = "FoodShield-AI/20 (+https://foodshield-ai-fv.vercel.app)"
DEFAULT_TIMEOUT = 30


def http_get(url, *, params=None, headers=None, timeout=DEFAULT_TIMEOUT, retries=3, backoff=2):
    """GET with retries and a polite User-Agent. Returns requests.Response or raises."""
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last_exc = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_exc}")


def write_json(filename, payload, *, source=None, notes=None):
    """Write JSON to data/<filename> with a standard envelope."""
    out = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source or "unknown",
            "notes": notes or "",
            "version": "v20",
        },
        "data": payload,
    }
    path = DATA_DIR / filename
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"[OK] wrote {path} ({len(json.dumps(payload))} bytes payload)")
    return path


def safe_run(label, fn, output_name=None):
    """Run a refresh function and never crash the workflow.

    Args:
        label: human-readable step name printed in workflow logs
        fn:    the refresh function to run
        output_name: canonical data file the script is supposed to produce
                     (e.g. 'lpi.json'). If the function raises, we still
                     write an empty envelope to data/<output_name> so the
                     frontend gets a graceful empty payload instead of a
                     404. If omitted, falls back to the legacy label-based
                     stub name — but every step in run_all.py should now
                     pass an explicit output_name.

    Why this matters: prior versions wrote 'wb_lpi_logistics_FAILED.json'
    when refresh_lpi.py crashed, but the frontend fetches 'lpi.json'. Users
    saw silent 404s instead of an empty 'no data yet' state.
    """
    print(f"\n=== {label} ===")
    try:
        fn()
    except Exception as e:
        print(f"[FAIL] {label}: {e}", file=sys.stderr)
        if output_name:
            write_json(output_name, {}, source=label, notes=f"Last refresh failed: {e}")
        else:
            # Legacy fallback — keeps older callers working but logs a warning
            stub_name = label.lower().replace(" ", "_").replace("/", "_") + "_FAILED.json"
            print(f"[WARN] {label}: no output_name passed to safe_run; "
                  f"writing legacy stub {stub_name} (frontend will still 404 on "
                  f"the real path)", file=sys.stderr)
            write_json(stub_name, {}, source=label, notes=f"Last refresh failed: {e}")


def env(key, default=None, required=False):
    val = os.environ.get(key, default)
    if required and not val:
        print(f"[SKIP] {key} not set in environment; skipping")
        return None
    return val


# ISO3 → (lat, lon) of the capital. Used by Open-Meteo, NASA FIRMS, OpenAQ for
# country-level point queries. Coordinates are intentionally simple — capital city
# centroids — not crop-belt centroids. Good enough for nowcast signals.
COUNTRY_COORDS = {
    "AFG": (34.53, 69.17), "ALB": (41.33, 19.82), "DZA": (36.75, 3.06), "AND": (42.51, 1.52),
    "AGO": (-8.84, 13.23), "ATG": (17.12, -61.85), "ARG": (-34.61, -58.38), "ARM": (40.18, 44.51),
    "AUS": (-35.28, 149.13), "AUT": (48.21, 16.37), "AZE": (40.41, 49.87), "BHS": (25.06, -77.35),
    "BHR": (26.23, 50.59), "BGD": (23.81, 90.41), "BRB": (13.10, -59.62), "BLR": (53.90, 27.57),
    "BEL": (50.85, 4.35), "BLZ": (17.25, -88.77), "BEN": (6.50, 2.60), "BTN": (27.47, 89.63),
    "BOL": (-16.50, -68.15), "BIH": (43.86, 18.41), "BWA": (-24.65, 25.91), "BRA": (-15.79, -47.88),
    "BRN": (4.90, 114.94), "BGR": (42.70, 23.32), "BFA": (12.37, -1.52), "BDI": (-3.38, 29.36),
    "KHM": (11.56, 104.92), "CMR": (3.85, 11.50), "CAN": (45.42, -75.70), "CPV": (14.92, -23.51),
    "CAF": (4.36, 18.56), "TCD": (12.13, 15.04), "CHL": (-33.45, -70.65), "CHN": (39.90, 116.41),
    "COL": (4.71, -74.07), "COM": (-11.70, 43.26), "COG": (-4.27, 15.27), "COD": (-4.32, 15.31),
    "CRI": (9.93, -84.08), "CIV": (6.83, -5.28), "HRV": (45.81, 15.98), "CUB": (23.13, -82.38),
    "CYP": (35.17, 33.37), "CZE": (50.08, 14.44), "DNK": (55.68, 12.57), "DJI": (11.59, 43.15),
    "DMA": (15.31, -61.39), "DOM": (18.49, -69.90), "ECU": (-0.18, -78.47), "EGY": (30.04, 31.23),
    "SLV": (13.69, -89.22), "GNQ": (3.75, 8.78), "ERI": (15.32, 38.93), "EST": (59.44, 24.75),
    "SWZ": (-26.31, 31.13), "ETH": (9.03, 38.74), "FJI": (-18.12, 178.42), "FIN": (60.17, 24.94),
    "FRA": (48.85, 2.35), "GAB": (0.41, 9.45), "GMB": (13.45, -16.58), "GEO": (41.72, 44.78),
    "DEU": (52.52, 13.41), "GHA": (5.60, -0.19), "GRC": (37.98, 23.73), "GRD": (12.06, -61.75),
    "GTM": (14.63, -90.51), "GIN": (9.51, -13.71), "GNB": (11.86, -15.59), "GUY": (6.81, -58.16),
    "HTI": (18.55, -72.34), "HND": (14.07, -87.19), "HUN": (47.50, 19.04), "ISL": (64.15, -21.94),
    "IND": (28.61, 77.21), "IDN": (-6.21, 106.85), "IRN": (35.69, 51.39), "IRQ": (33.31, 44.37),
    "IRL": (53.35, -6.26), "ISR": (31.78, 35.22), "ITA": (41.90, 12.50), "JAM": (17.97, -76.79),
    "JPN": (35.68, 139.69), "JOR": (31.95, 35.93), "KAZ": (51.16, 71.47), "KEN": (-1.29, 36.82),
    "KIR": (1.45, 173.04), "KWT": (29.38, 47.99), "KGZ": (42.87, 74.59), "LAO": (17.97, 102.60),
    "LVA": (56.95, 24.11), "LBN": (33.89, 35.50), "LSO": (-29.31, 27.49), "LBR": (6.30, -10.80),
    "LBY": (32.89, 13.19), "LIE": (47.14, 9.52), "LTU": (54.69, 25.28), "LUX": (49.61, 6.13),
    "MDG": (-18.88, 47.51), "MWI": (-13.96, 33.79), "MYS": (3.14, 101.69), "MDV": (4.18, 73.51),
    "MLI": (12.64, -8.00), "MLT": (35.90, 14.51), "MHL": (7.09, 171.38), "MRT": (18.07, -15.98),
    "MUS": (-20.16, 57.50), "MEX": (19.43, -99.13), "FSM": (6.92, 158.16), "MDA": (47.01, 28.84),
    "MCO": (43.74, 7.42), "MNG": (47.92, 106.92), "MNE": (42.44, 19.26), "MAR": (34.02, -6.83),
    "MOZ": (-25.97, 32.58), "MMR": (19.74, 96.08), "NAM": (-22.56, 17.07), "NRU": (-0.55, 166.92),
    "NPL": (27.71, 85.32), "NLD": (52.37, 4.89), "NZL": (-41.29, 174.78), "NIC": (12.11, -86.27),
    "NER": (13.51, 2.11), "NGA": (9.06, 7.49), "PRK": (39.02, 125.75), "MKD": (41.99, 21.43),
    "NOR": (59.91, 10.75), "OMN": (23.59, 58.41), "PAK": (33.69, 73.05), "PLW": (7.50, 134.62),
    "PSE": (31.78, 35.22), "PAN": (8.98, -79.52), "PNG": (-9.44, 147.18), "PRY": (-25.26, -57.58),
    "PER": (-12.05, -77.04), "PHL": (14.60, 120.98), "POL": (52.23, 21.01), "PRT": (38.72, -9.14),
    "QAT": (25.29, 51.53), "ROU": (44.43, 26.10), "RUS": (55.75, 37.62), "RWA": (-1.94, 30.06),
    "KNA": (17.30, -62.72), "LCA": (14.01, -60.99), "VCT": (13.16, -61.22), "WSM": (-13.83, -171.77),
    "SMR": (43.94, 12.45), "STP": (0.34, 6.73), "SAU": (24.71, 46.68), "SEN": (14.69, -17.45),
    "SRB": (44.79, 20.45), "SYC": (-4.62, 55.45), "SLE": (8.46, -13.23), "SGP": (1.35, 103.82),
    "SVK": (48.15, 17.11), "SVN": (46.06, 14.51), "SLB": (-9.43, 159.96), "SOM": (2.04, 45.34),
    "ZAF": (-25.75, 28.19), "KOR": (37.57, 126.98), "SSD": (4.85, 31.58), "ESP": (40.42, -3.70),
    "LKA": (6.93, 79.86), "SDN": (15.50, 32.56), "SUR": (5.85, -55.20), "SWE": (59.33, 18.07),
    "CHE": (46.95, 7.45), "SYR": (33.51, 36.28), "TWN": (25.04, 121.56), "TJK": (38.56, 68.79),
    "TZA": (-6.79, 39.21), "THA": (13.75, 100.50), "TLS": (-8.56, 125.58), "TGO": (6.13, 1.22),
    "TON": (-21.14, -175.20), "TTO": (10.66, -61.52), "TUN": (36.81, 10.18), "TUR": (39.92, 32.85),
    "TKM": (37.95, 58.38), "TUV": (-8.52, 179.20), "UGA": (0.32, 32.58), "UKR": (50.45, 30.52),
    "ARE": (24.45, 54.38), "GBR": (51.51, -0.13), "USA": (38.91, -77.04), "URY": (-34.90, -56.19),
    "UZB": (41.30, 69.24), "VUT": (-17.73, 168.32), "VEN": (10.50, -66.92), "VNM": (21.03, 105.85),
    "YEM": (15.37, 44.19), "ZMB": (-15.42, 28.28), "ZWE": (-17.83, 31.05),
}
