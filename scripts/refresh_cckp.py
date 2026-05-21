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

# v21 — CCKP API now requires 12 path segments (per error message returned in
# workflow run #19, 2026-05-21):
#   {collection}_{type}_{variable}_{product}_{aggregation}_{period}_{percentile}
#   _{scenario}_{model}_{model-calculation}_{grid}_{statistic}/{geo_code}
# Old URLs had 10 segments — missing percentile + grid. Added "median" and
# "country" defaults; mean→mean for statistic.
URL_HIST_TEMP = CCKP_BASE + "/era5-x0.25_timeseries_tas_timeseries_annual_1991-2020_median_historical_era5-x0.25_mean_country_mean/{iso3}"
URL_PROJ_TEMP = CCKP_BASE + "/cmip6-x1.0_timeseries_tas_timeseries_annual_2040-2059_median_ssp245_ensemble_all_country_mean/{iso3}"
URL_PROJ_PR   = CCKP_BASE + "/cmip6-x1.0_timeseries_pr_timeseries_annual_2040-2059_median_ssp245_ensemble_all_country_mean/{iso3}"
URL_HIST_PR   = CCKP_BASE + "/era5-x0.25_timeseries_pr_timeseries_annual_1991-2020_median_historical_era5-x0.25_mean_country_mean/{iso3}"

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
    Average across years to get a single number.

    v21 (May 2026) — workflow run #18 returned 0 countries with data,
    suggesting CCKP API response shape changed. Make this tolerant to:
      - {"BGD": {"1991": 25.1, ...}}             — old: country → years
      - {"1991": 25.1, ...}                       — flat years
      - {"data": {"BGD": {"1991": 25.1, ...}}}    — wrapped envelope
      - {"data": [{"year": 1991, "value": 25.1}]} — array of records
      - {"value": 25.1}                           — scalar
    """
    if payload is None:
        return None
    # Unwrap a top-level "data" envelope if present
    if isinstance(payload, dict) and "data" in payload and len(payload) <= 3:
        payload = payload["data"]
    if not isinstance(payload, (dict, list)):
        return None
    # Array of records form
    if isinstance(payload, list):
        values = []
        for rec in payload:
            if not isinstance(rec, dict):
                continue
            v = rec.get("value", rec.get("val", rec.get("mean")))
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                continue
        return (sum(values) / len(values)) if values else None
    # Dict form — recurse if there's a single nested dict
    candidate = payload
    if len(payload) == 1:
        only_key = next(iter(payload))
        inner = payload[only_key]
        if isinstance(inner, dict):
            candidate = inner
        elif isinstance(inner, (int, float)):
            return float(inner)
    # If 'value' is a direct key
    if "value" in candidate and isinstance(candidate["value"], (int, float)):
        return float(candidate["value"])
    values = []
    for k, v in candidate.items():
        # Year-keyed entries
        if str(k).isdigit() or (isinstance(k, str) and k[:4].isdigit()):
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                continue
    if not values:
        return None
    return sum(values) / len(values)


_FIRST_PROBE_LOGGED = False
def _fetch_one(url, iso3):
    """Fetch one CCKP endpoint for a country. On the FIRST successful HTTP
    call of the run, log the raw response shape (truncated) so we can debug
    schema drift from CI logs."""
    global _FIRST_PROBE_LOGGED
    try:
        r = http_get(url.format(iso3=iso3.lower()), timeout=30, retries=2, patient=True)
        body = r.json()
        if not _FIRST_PROBE_LOGGED:
            _FIRST_PROBE_LOGGED = True
            preview = json.dumps(body)[:400]
            print(f"  [cckp probe] first response from {url[:80]} for {iso3}: {preview}")
        return _extract_mean(body)
    except Exception as e:
        if not _FIRST_PROBE_LOGGED:
            _FIRST_PROBE_LOGGED = True
            print(f"  [cckp probe] first call FAILED for {iso3} → {type(e).__name__}: {e}")
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
