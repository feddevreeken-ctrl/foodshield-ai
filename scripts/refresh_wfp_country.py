"""
Per-country FX shock + child-nutrition signals.

HISTORY / WHY THIS FILE NO LONGER CALLS WFP
-------------------------------------------
This feed used to mirror WFP HungerMap's per-country endpoint
(api.hungermapdata.org/v2/iso3/{ISO3}/countryIso3Data.json) for FX, food
inflation and child wasting/stunting. WFP has since closed the whole v2 tree to
anonymous clients — /v2/iso3/{iso3}/countryIso3Data.json, /v2/adm0data.json,
/v2/ipc.json and /v2/pdc.json all answer 403/503, with or without browser
headers, from any IP. It is not a transient outage and not a code fault: there
is no anonymous v2 endpoint left to call. (The ew-tool IPC path that
refresh_ipc.py uses is the one survivor, and it carries IPC only.)

Rather than keep a dead pipeline on the board, this script now rebuilds the same
payload shape from feeds that ARE live in this repo:

  fx_currency, fx_90d_change_pct, fx_currency_shock  <-  data/fx_rates.json
  wasting_pct                                        <-  worldbank_bulk SH.STA.WAST.ZS
  stunting_pct                                       <-  worldbank_bulk SH.STA.STNT.ZS
  headline_inflation_pct                             <-  worldbank_bulk FP.CPI.TOTL.ZG

food_inflation_pct is deliberately left null. WFP supplied a monthly per-country
FOOD inflation series; nothing else here does. World Bank FP.CPI.TOTL.ZG is
all-items annual CPI, which is a different measurement, and writing it into the
food-inflation field would relabel it as something it is not. index.html already
falls back Eurostat food HICP -> FAOSTAT food CPI -> WB all-items and labels
whichever it used, so leaving this null keeps that chain honest instead of
short-circuiting it with a mislabelled number.

SIGN CONVENTION — read before editing.
fx_rates.json quotes USD->local, so a POSITIVE shock.depr_90d_pct means the
local currency DEPRECIATED. The consumers of this file (index.html
countryLiveSignals, build_daily_summary) were written against WFP's convention,
where fx_90d_change_pct is the change in the local currency's value and a
NEGATIVE number means depreciation. The sign is flipped on write so every
downstream reader keeps working untouched.

Output: data/wfp_country.json
"""
import json

from _common import DATA_DIR, write_json, COUNTRY_COORDS

# Local currency lost >10% against USD over 90 days. Same threshold the WFP-fed
# version used, so the flag means what it has always meant.
FX_SHOCK_DEPRECIATION_PCT = 10.0


def _load(name):
    p = DATA_DIR / name
    if not p.exists():
        return {}
    try:
        return (json.loads(p.read_text()) or {}).get("data") or {}
    except Exception:
        return {}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def main():
    fx = _load("fx_rates.json")
    wb = _load("worldbank_bulk.json")

    if not fx and not wb:
        # Both inputs missing means the upstream refreshers failed earlier in the
        # run. Write an explicit failure rather than an empty dict that reads as
        # "no countries had a shock this week".
        write_json(
            "wfp_country.json",
            {},
            source="derived: data/fx_rates.json + data/worldbank_bulk.json",
            notes=("UPSTREAM INPUTS MISSING: neither fx_rates.json nor "
                   "worldbank_bulk.json had a readable payload, so no per-country "
                   "FX or nutrition signal could be derived this run."),
            status="degraded",
        )
        return

    out = {}
    for iso in sorted(COUNTRY_COORDS.keys()):
        fx_row = fx.get(iso) or {}
        wb_row = wb.get(iso) or {}

        depr = _num((fx_row.get("shock") or {}).get("depr_90d_pct"))
        # WFP convention: change in the local currency's value, negative = fell.
        change_pct = round(-depr, 2) if depr is not None else None

        def wbval(code):
            return _num((wb_row.get(code) or {}).get("value"))

        row = {
            "fx_currency": fx_row.get("currency"),
            "fx_latest": None,          # fx_rates stores deltas, not spot levels
            "fx_90d_change_pct": change_pct,
            "fx_currency_shock": bool(depr is not None and depr > FX_SHOCK_DEPRECIATION_PCT),
            "food_inflation_pct": None,   # see module docstring — not available live
            "headline_inflation_pct": wbval("FP.CPI.TOTL.ZG"),
            "inflation_shock": False,     # requires food inflation; cannot be derived
            "wasting_pct": wbval("SH.STA.WAST.ZS"),
            "stunting_pct": wbval("SH.STA.STNT.ZS"),
        }
        # Don't store rows that carry no signal at all.
        if any(v not in (None, False) for v in row.values()):
            out[iso] = row

    n_fx = sum(1 for r in out.values() if r["fx_90d_change_pct"] is not None)
    n_shock = sum(1 for r in out.values() if r["fx_currency_shock"])
    n_nut = sum(1 for r in out.values() if r["wasting_pct"] is not None or r["stunting_pct"] is not None)
    print(f"[INFO] derived {len(out)} rows — {n_fx} with FX, {n_shock} in FX shock, {n_nut} with nutrition")

    write_json(
        "wfp_country.json",
        out,
        source="derived: data/fx_rates.json (FX) + data/worldbank_bulk.json (nutrition, headline CPI)",
        notes=(
            "Per-country FX 90d change vs USD, headline CPI, child wasting/stunting. "
            "REBUILT FROM LIVE FEEDS: WFP closed its HungerMap v2 API to anonymous "
            "clients (every /v2/ path now refuses unauthenticated requests), so "
            "this is no longer a WFP mirror. "
            "fx_currency_shock = local currency fell >"
            f"{FX_SHOCK_DEPRECIATION_PCT:.0f}% vs USD in 90d. food_inflation_pct is "
            "null by design — no live per-country FOOD inflation source exists here, "
            "and the frontend falls back to Eurostat/FAOSTAT/WB with an explicit "
            "label rather than presenting all-items CPI as food inflation. "
            f"Covered {len(out)} of {len(COUNTRY_COORDS)} countries "
            f"({n_fx} FX, {n_nut} nutrition)."
        ),
    )


if __name__ == "__main__":
    main()
