"""
FX rates — local-currency depreciation vs USD, per country (v23).

Feeds the FDRS v2 Economic Access component (structural 12-month depreciation =
a LEVEL of currency fragility) AND the nowcast FX signal (90-day depreciation =
an event DELTA). Same file, two cleanly separated fields, so FX is never counted
twice (structural level vs live shock — see FDRS_V2_IMPLEMENTATION_SPEC §3.4/§4).

WHY TWO SOURCES (no single free no-key source gives breadth + history):
  - open.er-api.com/v6/latest/USD  — 160+ currencies INCLUDING the FX-fragile ones
    this component exists for (EGP, LKR, PKR, NGN, ARS, GHS, TRY...). But LATEST
    ONLY, no history. → We append a daily snapshot and ACCUMULATE our own history.
  - api.frankfurter.dev (ECB reference rates) — true historical series, but only
    ~30 major currencies. → Used to BOOTSTRAP a real 12m/90d window for the majors
    on day 1, and as a cross-check.

SIGN CONVENTION: rates are quoted USD->local (e.g. EGP per USD). An INCREASE in the
rate = the local currency DEPRECIATED. depr% > 0 means weaker currency = more fragile.

OUTPUT: data/fx_rates.json (standard envelope). The script is incremental: it reads
its own prior output, appends today's snapshot to each currency's history, trims to
~400 daily points, and recomputes the structural/shock fields. Safe to run daily.

Run on the Mac (needs internet):  cd scripts && python3 refresh_fx.py
"""
import json
from datetime import datetime, timezone, date, timedelta

from _common import http_get, write_json, DATA_DIR

ER_API = "https://open.er-api.com/v6/latest/USD"
FRANKFURTER = "https://api.frankfurter.dev/v1"

HISTORY_FILE = DATA_DIR / "fx_rates.json"
MAX_HISTORY_POINTS = 400   # ~13 months of daily snapshots

# Currency code -> list of ISO3 countries that use it. Shared currencies (EUR, the
# two CFA francs, etc.) broadcast the same rate to every member country. Non-USD,
# non-pegged majors + the FX-fragile targets. USD-using/pegged countries are simply
# absent (no FX fragility by construction) and degrade to "no FX sub-input".
CCY_TO_ISO3 = {
    "EUR": ["AUT","BEL","HRV","CYP","EST","FIN","FRA","DEU","GRC","IRL","ITA","LVA",
            "LTU","LUX","MLT","NLD","PRT","SVK","SVN","ESP","AND","MCO","MNE"],
    "XOF": ["BEN","BFA","CIV","GNB","MLI","NER","SEN","TGO"],          # West CFA
    "XAF": ["CMR","CAF","TCD","COG","GNQ","GAB"],                       # Central CFA
    "EGP": ["EGY"], "LKR": ["LKA"], "PKR": ["PAK"], "NGN": ["NGA"], "ARS": ["ARG"],
    "GHS": ["GHA"], "TRY": ["TUR"], "ETB": ["ETH"], "KES": ["KEN"], "TZS": ["TZA"],
    "UGX": ["UGA"], "ZMW": ["ZMB"], "MWK": ["MWI"], "MZN": ["MOZ"], "ZWL": ["ZWE"],
    "ZAR": ["ZAF","LSO","NAM","SWZ"], "DZD": ["DZA"], "MAD": ["MAR"], "TND": ["TUN"],
    "LBP": ["LBN"], "JOD": ["JOR"], "IQD": ["IRQ"], "IRR": ["IRN"], "YER": ["YEM"],
    "AFN": ["AFG"], "BDT": ["BGD"], "INR": ["IND"], "NPR": ["NPL"], "MMK": ["MMR"],
    "KHR": ["KHM"], "LAK": ["LAO"], "VND": ["VNM"], "IDR": ["IDN"], "PHP": ["PHL"],
    "MYR": ["MYS"], "THB": ["THA"], "MNT": ["MNG"], "KZT": ["KAZ"], "UZS": ["UZB"],
    "KGS": ["KGZ"], "TJS": ["TJK"], "TMT": ["TKM"], "AZN": ["AZE"], "GEL": ["GEO"],
    "AMD": ["ARM"], "UAH": ["UKR"], "RUB": ["RUS"], "BYN": ["BLR"], "MDL": ["MDA"],
    "RSD": ["SRB"], "MKD": ["MKD"], "ALL": ["ALB"], "BAM": ["BIH"], "BGN": ["BGR"],
    "RON": ["ROU"], "HUF": ["HUN"], "PLN": ["POL"], "CZK": ["CZE"], "GBP": ["GBR"],
    "CHF": ["CHE"], "NOK": ["NOR"], "SEK": ["SWE"], "DKK": ["DNK"], "ISK": ["ISL"],
    "BRL": ["BRA"], "MXN": ["MEX"], "COP": ["COL"], "PEN": ["PER"], "CLP": ["CHL"],
    "BOB": ["BOL"], "PYG": ["PRY"], "UYU": ["URY"], "VES": ["VEN"], "GTQ": ["GTM"],
    "HNL": ["HND"], "NIO": ["NIC"], "CRC": ["CRI"], "DOP": ["DOM"], "JMD": ["JAM"],
    "HTG": ["HTI"], "BOB2": [], "CNY": ["CHN"], "JPY": ["JPN"], "KRW": ["KOR"],
    "TWD": ["TWN"], "AUD": ["AUS"], "NZD": ["NZL"], "CAD": ["CAN"], "SGD": ["SGP"],
    "HKD": ["HKG"], "ILS": ["ISR"], "SAR": ["SAU"], "AED": ["ARE"], "QAR": ["QAT"],
    "KWD": ["KWT"], "OMR": ["OMN"], "BHD": ["BHR"],
    # v33 — crisis-currency expansion (these were the FX-fragile gaps the component
    # exists for). ZWE moved from the retired ZWL to ZiG (ZWG). NOTE: SDG (Sudan) and
    # CUP (Cuba) official rates are administered and diverge badly from the parallel
    # market — the depr signal will read ~0; that follows the feed's official-rate
    # semantics (same as the GCC pegs) but understates real stress. SYP redenominated
    # in late 2025 (new pound) — pre-redenomination points must not be mixed in.
    "SDG": ["SDN"], "SSP": ["SSD"], "SYP": ["SYR"], "LYD": ["LBY"], "BIF": ["BDI"],
    "CUP": ["CUB"], "CDF": ["COD"], "GNF": ["GIN"], "LRD": ["LBR"], "SLE": ["SLE"],
    "SOS": ["SOM"], "ERN": ["ERI"], "DJF": ["DJI"], "MGA": ["MDG"], "RWF": ["RWA"],
    "PGK": ["PNG"], "KMF": ["COM"], "AOA": ["AGO"], "GMD": ["GMB"], "MRU": ["MRT"],
    "ZWG": ["ZWE"],
}


def _today():
    return date.today().isoformat()


def _load_prior():
    if HISTORY_FILE.exists():
        try:
            env = json.loads(HISTORY_FILE.read_text())
            return env.get("data", {}) or {}
        except Exception:
            pass
    return {}


def fetch_er_api():
    """Latest USD->local snapshot for 160+ currencies."""
    r = http_get(ER_API, timeout=45)
    j = r.json()
    if j.get("result") != "success":
        raise RuntimeError(f"er-api result={j.get('result')}")
    return j.get("rates", {})   # {currency: rate_per_USD}


def fetch_frankfurter_history(start, end):
    """True historical USD->local series for the ~30 ECB majors (bootstrap)."""
    try:
        r = http_get(f"{FRANKFURTER}/{start}..{end}?base=USD", timeout=45)
        return r.json().get("rates", {})   # {date: {currency: rate}}
    except Exception as e:
        print(f"[warn] Frankfurter bootstrap failed (non-fatal): {e}")
        return {}


def _depr_pct(history, days_back):
    """% depreciation of local vs USD over a trailing window. history: {date: rate}."""
    if not history:
        return None
    dates = sorted(history)
    today_rate = history[dates[-1]]
    cutoff = (date.fromisoformat(dates[-1]) - timedelta(days=days_back)).isoformat()
    # nearest snapshot on/before cutoff
    older = [d for d in dates if d <= cutoff]
    if not older or not today_rate:
        return None
    past_rate = history[older[-1]]
    if not past_rate:
        return None
    return round((today_rate - past_rate) / past_rate * 100, 1)


def main():
    prior = _load_prior()
    # prior schema: { currency: {"history": {date: rate}} , ... } kept under "_ccy"
    ccy_hist = prior.get("_ccy_history", {})

    # 1) breadth snapshot (today)
    try:
        rates = fetch_er_api()
        today = _today()
        for ccy, rate in rates.items():
            ccy_hist.setdefault(ccy, {})[today] = rate
        print(f"[INFO] er-api: appended {len(rates)} currencies for {today}")
    except Exception as e:
        print(f"[FAIL] er-api snapshot failed: {e}")

    # 2) one-time/ongoing bootstrap of majors' history from Frankfurter
    end = _today()
    start = (date.today() - timedelta(days=400)).isoformat()
    fr = fetch_frankfurter_history(start, end)
    boot = 0
    for d, day_rates in fr.items():
        for ccy, rate in day_rates.items():
            h = ccy_hist.setdefault(ccy, {})
            if d not in h:
                h[d] = rate
                boot += 1
    if boot:
        print(f"[INFO] Frankfurter: bootstrapped {boot} historical points for majors")

    # trim each currency's history to the most recent MAX_HISTORY_POINTS
    for ccy, h in ccy_hist.items():
        if len(h) > MAX_HISTORY_POINTS:
            keep = sorted(h)[-MAX_HISTORY_POINTS:]
            ccy_hist[ccy] = {d: h[d] for d in keep}

    # 3) compute structural (12m) + shock (90d) per currency, broadcast to ISO3
    by_iso3 = {}
    for ccy, isos in CCY_TO_ISO3.items():
        h = ccy_hist.get(ccy)
        if not h:
            continue
        depr_12m = _depr_pct(h, 365)
        depr_90d = _depr_pct(h, 90)
        for iso in isos:
            by_iso3[iso] = {
                "currency": ccy,
                "structural": {"depr_12m_pct": depr_12m},
                "shock": {"depr_90d_pct": depr_90d},
                "n_points": len(h),
                "quality_flag": "sourced" if depr_12m is not None else "accumulating",
            }

    print(f"[INFO] FX computed for {len(by_iso3)} countries "
          f"({sum(1 for v in by_iso3.values() if v['structural']['depr_12m_pct'] is not None)} with a 12m window)")
    for ref in ("EGY", "LKA", "TUR", "ARG", "NGA", "JPN"):
        if ref in by_iso3:
            v = by_iso3[ref]
            print(f"  [ref] {ref} {v['currency']}: 12m={v['structural']['depr_12m_pct']} "
                  f"90d={v['shock']['depr_90d_pct']}")

    payload = dict(by_iso3)
    payload["_ccy_history"] = ccy_hist   # persist accumulated history for next run
    write_json(
        "fx_rates.json", payload,
        source="open.er-api.com (latest) + api.frankfurter.dev (history)",
        notes=("USD->local quotes; increase = local-currency depreciation. "
               "structural.depr_12m_pct = slow fragility LEVEL (feeds FDRS Economic Access); "
               "shock.depr_90d_pct = fast event DELTA (feeds nowcast FX signal). "
               "History self-accumulated from daily er-api snapshots + Frankfurter bootstrap. "
               "_ccy_history is the internal rolling store, not for display."),
    )


if __name__ == "__main__":
    main()
