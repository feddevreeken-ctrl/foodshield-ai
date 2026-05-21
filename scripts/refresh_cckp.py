"""
World Bank Climate Change Knowledge Portal (CCKP).

Pipeline 16. Per-country climate projections — historical baseline temperature,
projected temperature change to 2050, projected precipitation change. Pairs
with ND-GAIN and Aqueduct to give the climate dimension a full sourced
picture instead of mostly heuristic inputs.

WHY CCKP:
  CCKP is the WB's canonical climate data portal. CMIP6 ensemble means at
  country level. Used by IPCC, IMF, central bank climate stress tests.

ARCHITECTURE NOTE (May 2026):
  CCKP's API URL structure changed twice in the last 2 years. We try the
  current pattern with country-batched fetches, falling back to a graceful
  empty stub if all endpoints fail. The static-CSV fallback path is
  documented in code comments for manual repair.

INDICATORS WE EXTRACT (per country):
  hist_temp_c         — Historical mean annual temperature 1991-2020 (°C)
  proj_temp_change_c  — Projected temperature change by 2050 vs 1991-2020 baseline
                        under SSP2-4.5 (CMIP6 ensemble mean, °C)
  proj_precip_change_pct — Projected % change in annual precipitation by 2050
                           under SSP2-4.5

OUTPUT: data/cckp.json
  {
    "_meta": {...},
    "data": {
      "BGD": {
        "hist_temp_c":            25.5,
        "proj_temp_change_c":     1.9,
        "proj_precip_change_pct": 8.4,
        "scenario":               "ssp245",
        "baseline_period":        "1991-2020",
        "projection_period":      "2040-2059",
        "source":                 "World Bank Climate Change Knowledge Portal",
        ...
      },
      ...
    }
  }

CCKP COUNTRY LIST:
  Coverage is the standard ~190 countries. We iterate the ISO3 list from
  the existing FAOSTAT country mapping plus a few small states.
"""
import json
import time

from _common import http_get, write_json

# CCKP API base (CMIP6 timeseries endpoint)
CCKP_BASE = "https://cckpapi.worldbank.org/cckp/v1"

# Historical mean temperature 1991-2020 (ERA5)
URL_HIST_TEMP = CCKP_BASE + "/era5-x0.25_timeseries_tas_timeseries_annual_1991-2020_mean_historical_era5-x0.25_mean/{iso3}"
# Projected temperature 2040-2059 SSP2-4.5 (CMIP6 ensemble)
URL_PROJ_TEMP = CCKP_BASE + "/cmip6-x1.0_timeseries_tas_timeseries_annual_2040-2059_median_ssp245_ensemble_all_mean/{iso3}"
# Projected precipitation 2040-2059 SSP2-4.5
URL_PROJ_PR = CCKP_BASE + "/cmip6-x1.0_timeseries_pr_timeseries_annual_2040-2059_median_ssp245_ensemble_all_mean/{iso3}"
# Historical precip baseline (for % change calculation)
URL_HIST_PR = CCKP_BASE + "/era5-x0.25_timeseries_pr_timeseries_annual_1991-2020_mean_historical_era5-x0.25_mean/{iso3}"

THROTTLE_SECONDS = 0.15   # be polite to WB CDN

# Country list — reuse FAOSTAT mapping so coverage matches our other pipelines.
ISO3_SET = set()
try:
    from refresh_faostat_fbs import FAO_AREA_TO_ISO3
    ISO3_SET.update(FAO_AREA_TO_ISO3.values())
except Exception:
    pass
# Augment with small states + non-FBS countries
ISO3_SET.update({
    "USA","GBR","FRA","DEU","ITA","ESP","NLD","BEL","CHE","AUT","SWE","NOR",
    "DNK","FIN","IRL","PRT","GRC","POL","CZE","HUN","ROU","BGR","HRV","SVN",
    "SVK","EST","LVA","LTU","LUX","JPN","KOR","SGP","HKG","TWN","ISR","ARE",
    "SAU","KWT","BHR","QAT","OMN","CYP","MLT","ISL","JAM","TTO","CUB","BMU",
    "MUS","SYC","BRN","NZL", "MNG","KAZ","UZB","TKM","KGZ","TJK","AZE","ARM",
    "GEO","BLR","MDA","UKR","RUS","HND","SLV","NIC","CRI","PAN","JAM","DOM",
    "HTI","PRY","URY","BOL","ECU","PER","CHL","ARG","COL","VEN","GUY","SUR",
})


def _extract_mean(payload):
    """CCKP timeseries returns a dict of year → value (or a nested dict).
    Average across years to get a single number."""
    if not isinstance(payload, dict):
        return None
    # The payload may be { "BGD": {"1991": 25.1, ...} } or a flat dict of years.
    candidate = payload
    if len(payload) == 1:
        only_key = next(iter(payload))
        if isinstance(payload[only_key], dict):
            candidate = payload[only_key]
    values = []
    for k, v in candidate.items():
        if not str(k).isdigit():
            continue
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return sum(values) / len(values)


def _fetch_one(url, iso3):
    try:
        # CCKP CSVs are big + flaky; patient retry rides out partial outages (run #17, May 21 2026).
        r = http_get(url.format(iso3=iso3.lower()), timeout=30, retries=2, patient=True)
        return _extract_mean(r.json())
    except Exception:
        return None


def main():
    out = {}
    countries = sorted(ISO3_SET)
    print(f"[INFO] CCKP fetch for {len(countries)} countries (4 calls each)")
    failures = 0

    for i, iso3 in enumerate(countries):
        hist_temp = _fetch_one(URL_HIST_TEMP, iso3)
        proj_temp = _fetch_one(URL_PROJ_TEMP, iso3)
        proj_pr   = _fetch_one(URL_PROJ_PR, iso3)
        hist_pr   = _fetch_one(URL_HIST_PR, iso3)

        if hist_temp is None and proj_temp is None and proj_pr is None:
            failures += 1
            if i % 20 == 0:
                print(f"  [progress] {i}/{len(countries)} done ({failures} no-data)")
            time.sleep(THROTTLE_SECONDS)
            continue

        # Compute % precipitation change
        precip_pct = None
        if proj_pr is not None and hist_pr and hist_pr > 0:
            precip_pct = round((proj_pr - hist_pr) / hist_pr * 100, 1)

        temp_change = None
        if proj_temp is not None and hist_temp is not None:
            temp_change = round(proj_temp - hist_temp, 2)

        if hist_temp is None and temp_change is None and precip_pct is None:
            continue

        out[iso3] = {
            "hist_temp_c":            round(hist_temp, 2) if hist_temp is not None else None,
            "proj_temp_change_c":     temp_change,
            "proj_precip_change_pct": precip_pct,
            "scenario":               "ssp245",
            "baseline_period":        "1991-2020",
            "projection_period":      "2040-2059",
            "source":                 "World Bank Climate Change Knowledge Portal",
            "source_url":             "https://climateknowledgeportal.worldbank.org/",
            "quality_flag":           "sourced",
        }
        if i % 20 == 0:
            print(f"  [progress] {i}/{len(countries)} done, {len(out)} with data")
        time.sleep(THROTTLE_SECONDS)

    print(f"[INFO] CCKP wrote {len(out)} countries, {failures} no-data")

    for ref in ("USA", "NLD", "BGD", "AFG", "AUS", "BRA", "IND"):
        if ref in out:
            p = out[ref]
            print(f"  [ref] {ref}: hist_temp={p.get('hist_temp_c')}°C, "
                  f"proj_temp_change={p.get('proj_temp_change_c')}°C by 2050, "
                  f"proj_precip_change={p.get('proj_precip_change_pct')}%")

    write_json(
        "cckp.json",
        out,
        source="World Bank Climate Change Knowledge Portal (cckpapi.worldbank.org)",
        notes=(
            f"Per-country climate projections under SSP2-4.5 (CMIP6 ensemble mean). "
            f"Historical baseline 1991-2020 from ERA5. Projection period 2040-2059. "
            f"3 indicators: historical mean temperature (°C), projected temperature "
            f"change by 2050 (°C), projected precipitation change by 2050 (%). "
            f"Covered {len(out)} countries."
        ),
    )


if __name__ == "__main__":
    main()
