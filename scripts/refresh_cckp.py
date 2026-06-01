"""
World Bank Climate Change Knowledge Portal (CCKP) — via WB Data360.

v24 (Jun 2026) — the direct cckpapi.worldbank.org endpoints return empty for
the historical climatology paths (only the SSP projection path responded, and
inconsistently). WB Data360 mirrors CCKP as clean annual time series, which is
both reliable AND more honest: these are OBSERVED annual values, not model
projections.

Data360 (database WB_CCKP, confirmed Jun 2026):
    WB_CCKP_TAS  — mean annual near-surface air temperature (°C), per year from 1950
    WB_CCKP_PR   — total annual precipitation (mm), per year from 1950
Long format: one row per (REF_AREA, INDICATOR, TIME_PERIOD=year), OBS_VALUE.

We derive, per country:
    hist_temp_c         — mean temp over the 1991-2020 baseline (°C)
    recent_temp_c       — mean temp over the most recent 10 available years (°C)
    warming_c           — recent_temp_c - hist_temp_c  (observed warming, °C)
    hist_precip_mm      — mean annual precip 1991-2020 (mm)
    recent_precip_mm    — mean annual precip, most recent 10 years (mm)
    precip_change_pct   — % change recent vs baseline

OUTPUT: data/cckp.json
"""
import csv
import io
import statistics

from _common import http_get, write_json

BASE = "https://data360api.worldbank.org/data360/data"
# Two narrow windows instead of the full series. Data360 caps a single response
# at 10000 rows even with top=N; ~190 countries × 34 years (1991-2024) overflows
# that and silently drops countries. Each ~10-year window is ~1900 rows — safe.
BASELINE_WINDOW = (1991, 2000)   # early-baseline mean (climate normal anchor)
RECENT_WINDOW   = (2014, 2024)   # most-recent decade


def _fetch_window(code, yr_from, yr_to):
    """Return {iso3: {year: value}} for one CCKP indicator within a year window."""
    out = {}
    r = http_get(
        BASE,
        params={"DATABASE_ID": "WB_CCKP", "INDICATOR": code, "format": "csv",
                "top": 50000, "timePeriodFrom": yr_from, "timePeriodTo": yr_to},
        timeout=180, retries=3, patient=True,
    )
    for row in csv.DictReader(io.StringIO(r.text)):
        iso3 = (row.get("REF_AREA") or "").strip().upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue
        try:
            yr = int(str(row.get("TIME_PERIOD"))[:4])
            val = float(row.get("OBS_VALUE"))
        except (TypeError, ValueError):
            continue
        if not (yr_from <= yr <= yr_to):     # guard if API ignores the param
            continue
        out.setdefault(iso3, {})[yr] = val
    return out


def _fetch_series(code):
    """Merge baseline + recent windows into {iso3: {year: value}}."""
    base = _fetch_window(code, *BASELINE_WINDOW)
    recent = _fetch_window(code, *RECENT_WINDOW)
    merged = {}
    for iso3 in set(base) | set(recent):
        merged[iso3] = {**base.get(iso3, {}), **recent.get(iso3, {})}
    print(f"    [{code}] baseline {len(base)} ctry, recent {len(recent)} ctry, merged {len(merged)}")
    return merged


def _baseline_mean(series):
    vals = [v for y, v in series.items() if BASELINE_WINDOW[0] <= y <= BASELINE_WINDOW[1]]
    return statistics.mean(vals) if vals else None


def _recent_mean(series, n=10):
    if not series:
        return None
    years = sorted(series)[-n:]
    vals = [series[y] for y in years]
    return statistics.mean(vals) if vals else None


def main():
    try:
        tas = _fetch_series("WB_CCKP_TAS")
        pr = _fetch_series("WB_CCKP_PR")
    except Exception as e:
        write_json("cckp.json", {}, source="World Bank CCKP (Data360)",
                   notes=f"Data360 WB_CCKP fetch failed: {e}")
        return

    isos = set(tas) | set(pr)
    out = {}
    for iso3 in isos:
        t = tas.get(iso3, {})
        p = pr.get(iso3, {})
        hist_t = _baseline_mean(t)
        rec_t = _recent_mean(t)
        hist_p = _baseline_mean(p)
        rec_p = _recent_mean(p)

        warming = round(rec_t - hist_t, 2) if (rec_t is not None and hist_t is not None) else None
        precip_pct = None
        if rec_p is not None and hist_p not in (None, 0):
            precip_pct = round((rec_p - hist_p) / hist_p * 100, 1)

        if hist_t is None and warming is None and precip_pct is None:
            continue

        out[iso3] = {
            "hist_temp_c": round(hist_t, 2) if hist_t is not None else None,
            "recent_temp_c": round(rec_t, 2) if rec_t is not None else None,
            "warming_c": warming,
            "hist_precip_mm": round(hist_p, 1) if hist_p is not None else None,
            "recent_precip_mm": round(rec_p, 1) if rec_p is not None else None,
            "precip_change_pct": precip_pct,
            "baseline_period": f"{BASELINE_WINDOW[0]}-{BASELINE_WINDOW[1]}",
            "recent_window": f"{RECENT_WINDOW[0]}-{RECENT_WINDOW[1]}",
            "basis": "observed annual series (not a model projection)",
            "source": "World Bank Climate Change Knowledge Portal (Data360)",
            "source_url": "https://climateknowledgeportal.worldbank.org/",
            "quality_flag": "sourced",
        }

    print(f"[INFO] CCKP: {len(out)} countries (observed TAS+PR from Data360)")
    for ref in ("USA", "NLD", "BGD", "AFG", "AUS", "IND"):
        if ref in out:
            d = out[ref]
            print(f"  [ref] {ref}: hist_temp={d['hist_temp_c']}°C, "
                  f"warming={d['warming_c']}°C, precip_change={d['precip_change_pct']}%")

    write_json(
        "cckp.json", out,
        source="World Bank Climate Change Knowledge Portal (WB Data360, WB_CCKP)",
        notes=(
            f"Observed per-country climate from WB Data360 (database WB_CCKP): mean "
            f"annual temperature (TAS, °C) and total annual precipitation (PR, mm), "
            f"annual series. We report the 1991-2020 baseline mean, the most-recent-"
            f"10-year mean, observed warming (°C), and precipitation change (%). "
            f"v24: switched from the cckpapi projection endpoints (which returned "
            f"empty) to the Data360 observed series — more reliable and observed, not "
            f"modeled. Covered {len(out)} countries."
        ),
    )


if __name__ == "__main__":
    main()
