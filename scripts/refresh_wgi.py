"""
World Bank Worldwide Governance Indicators (WGI).

v25 (Jun 2026) — the WB v2 API archived the bare *.EST codes. WGI now lives in
WB Data360 (database WB_WGI), codes GOV_WGI_{CC,GE,PV,RL,RQ,VA}.

v81 — `top=50000` does NOT lift the cap. Data360 truncates the response at
10,000 rows regardless, and it truncates AFTER interleaving the six
COMP_BREAKDOWN_1 variants across all years, so a bare per-indicator pull
returns a partial, year-skewed slice: measured 2026-08-30, GOV_WGI_RL came back
as 5,476 WGI_SR + 4,523 WGI_EST + 1 WGI_SE, and the WGI_EST rows that survived
were the older years. That is why data/wgi.json was 100% pre-2023 — 93 rows at
2019 and 122 at 2020 — while its own top-level `year` claimed 2024.

The fix is to ask for one year at a time. `timePeriodFrom`/`timePeriodTo`
narrows the response to ~1,290 rows (215 areas × 6 variants), well inside the
cap, and 2024 is fully published: verified 215 countries carrying WGI_EST.

Two gotchas the probe revealed:
  1. Data360 caps responses at 10,000 rows; pass an explicit year window.
  2. Each indicator carries THREE variants in COMP_BREAKDOWN_1:
       WGI_EST — the estimate, -2.5..+2.5  (THIS is the governance score)
       WGI_SE  — standard error
       WGI_SR  — standard/percentile rank (0-100-ish)  ← must NOT be used
     We keep only WGI_EST rows.

OUTPUT: data/wgi.json
"""
import csv
import datetime as _dt
import io

from _common import http_get, write_json

BASE = "https://data360api.worldbank.org/data360/data"

INDICATORS = {
    "GOV_WGI_CC": ("control_corruption",   "Control of Corruption"),
    "GOV_WGI_GE": ("gov_effectiveness",    "Government Effectiveness"),
    "GOV_WGI_PV": ("political_stability",  "Political Stability"),
    "GOV_WGI_RL": ("rule_of_law",          "Rule of Law"),
    "GOV_WGI_RQ": ("regulatory_quality",   "Regulatory Quality"),
    "GOV_WGI_VA": ("voice_accountability", "Voice & Accountability"),
}
ESTIMATE_VARIANT = "WGI_EST"   # -2.5..+2.5 estimate (not SE, not rank)
SCORE_VARIANT = "WGI_SC"       # 0-100 governance score (used only where EST is absent)
# Political Stability (PV) is published on Data360 ONLY as the 0-100 score
# (WGI_SC), not the -2.5..+2.5 estimate. We convert SC→estimate scale so all 6
# dimensions are comparable: est ≈ (score/100)*5 - 2.5.
SCORE_ONLY_CODES = {"GOV_WGI_PV"}


def _sc_to_est(score):
    return round((score / 100.0) * 5.0 - 2.5, 2)


def _fetch_year(code, year, want, use_score):
    """One indicator, one year. Returns {iso3: (year, value, converted)}."""
    r = http_get(
        BASE,
        params={"DATABASE_ID": "WB_WGI", "INDICATOR": code, "format": "csv",
                "timePeriodFrom": year, "timePeriodTo": year, "top": 50000},
        timeout=120, retries=3, patient=True,
    )
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if len(rows) >= 10000:
        print(f"  [warn] {code} {year}: {len(rows)} rows hit the Data360 cap — "
              f"result may be truncated")
    got = {}
    for row in rows:
        if (row.get("COMP_BREAKDOWN_1") or "").strip() != want:
            continue
        iso3 = (row.get("REF_AREA") or "").strip().upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue
        val = _num(row.get("OBS_VALUE"))
        if val is None:
            continue
        if use_score:
            val = _sc_to_est(val)
        got[iso3] = (year, val, use_score)
    return got


def _fetch(code):
    """Latest-year governance estimate per ISO3 for one indicator code.

    Walks back year by year from the current one and stops at the first year
    that is actually published, rather than pulling every year at once and
    letting the server's row cap decide which ones survive (see module
    docstring). Earlier years then backfill only the countries the latest year
    is missing, so a country that stopped reporting keeps its last real value
    with its own honest year attached.

    Most codes carry WGI_EST (-2.5..+2.5). PV carries only WGI_SC (0-100), which
    we convert to the estimate scale and flag.
    """
    use_score = code in SCORE_ONLY_CODES
    want = SCORE_VARIANT if use_score else ESTIMATE_VARIANT
    this_year = _dt.date.today().year
    latest = {}
    newest_seen = None
    # WGI publishes with roughly an 18-month lag, so start one year back and
    # allow a decade of backfill for countries that dropped out.
    for year in range(this_year, this_year - 11, -1):
        try:
            got = _fetch_year(code, year, want, use_score)
        except Exception as e:
            print(f"  [warn] {code} {year}: {e}")
            continue
        if not got:
            continue
        if newest_seen is None:
            newest_seen = year
            print(f"  [ok] {code}: latest published year {year} "
                  f"({len(got)} countries)")
        added = 0
        for iso3, rec in got.items():
            if iso3 not in latest:
                latest[iso3] = rec
                added += 1
        # Once the newest year is in, one backfill pass is enough to catch the
        # long-lapsed reporters; keep walking only while it still finds any.
        if newest_seen is not None and year < newest_seen and added == 0:
            break
    return latest


def main():
    out = {}
    failures = []
    for code, (key, label) in INDICATORS.items():
        print(f"[INFO] WGI {code} — {label}")
        try:
            latest = _fetch(code)
        except Exception as e:
            print(f"  [warn] {code} fetch failed: {e}")
            failures.append(code)
            continue
        if not latest:
            print(f"  [warn] {code}: 0 rows after WGI_EST filter")
            failures.append(code)
            continue
        for iso3, (yr, val, converted) in latest.items():
            slot = out.setdefault(iso3, {
                "source": "World Bank Worldwide Governance Indicators (Data360)",
                "source_url": "https://www.worldbank.org/en/publication/worldwide-governance-indicators",
                "quality_flag": "sourced",
            })
            cell = {"value": round(val, 2), "year": yr, "label": label}
            if converted:
                cell["note"] = "Converted from WGI 0-100 score (estimate variant not published for this dimension)."
            slot[key] = cell
            if slot.get("year") is None or (yr and yr > slot["year"]):
                slot["year"] = yr
        print(f"  [OK] {code}: {len(latest)} countries"
              + ("  [SC→EST converted]" if code in SCORE_ONLY_CODES else ""))

    out = {iso: row for iso, row in out.items()
           if any(k in row for k in (v[0] for v in INDICATORS.values()))}

    if failures:
        print(f"[WARN] {len(failures)} indicator(s) failed: {failures}")
    print(f"[INFO] WGI: {len(out)} countries")
    for ref in ("USA", "DNK", "NLD", "BGD", "AFG", "SOM", "YEM"):
        if ref in out:
            r = out[ref]
            print(f"  [ref] {ref}: rule_of_law={r.get('rule_of_law',{}).get('value')}, "
                  f"control_corruption={r.get('control_corruption',{}).get('value')}, "
                  f"gov_eff={r.get('gov_effectiveness',{}).get('value')}")

    write_json(
        "wgi.json", out,
        source="World Bank Worldwide Governance Indicators (WB Data360, WB_WGI)",
        notes=(
            f"6 governance dimensions per country, latest year, -2.5..+2.5 estimate "
            f"(COMP_BREAKDOWN_1=WGI_EST; percentile-rank and std-error variants excluded). "
            f"v25: pulled from WB Data360 because the WB v2 API archived the bare *.EST "
            f"codes. Covered {len(out)} countries."
            + (f" {len(failures)} indicator(s) failed: {failures}." if failures else "")
        ),
    )


def _num(v):
    try:
        return float(v) if v not in (None, "", "..", "NA", "_Z") else None
    except (TypeError, ValueError):
        return None


def _year(v):
    try:
        return int(str(v)[:4])
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
