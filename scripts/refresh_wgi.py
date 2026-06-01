"""
World Bank Worldwide Governance Indicators (WGI).

v25 (Jun 2026) — the WB v2 API archived the bare *.EST codes. WGI now lives in
WB Data360 (database WB_WGI), codes GOV_WGI_{CC,GE,PV,RL,RQ,VA}.

Two gotchas the probe revealed:
  1. Data360 caps responses at 1000 rows by default; pass top=50000 for the full set.
  2. Each indicator carries THREE variants in COMP_BREAKDOWN_1:
       WGI_EST — the estimate, -2.5..+2.5  (THIS is the governance score)
       WGI_SE  — standard error
       WGI_SR  — standard/percentile rank (0-100-ish)  ← must NOT be used
     We keep only WGI_EST rows.

OUTPUT: data/wgi.json
"""
import csv
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


def _fetch(code):
    """Latest-year governance estimate per ISO3 for one indicator code.

    Most codes carry WGI_EST (-2.5..+2.5). PV carries only WGI_SC (0-100), which
    we convert to the estimate scale and flag.
    """
    use_score = code in SCORE_ONLY_CODES
    want = SCORE_VARIANT if use_score else ESTIMATE_VARIANT
    r = http_get(
        BASE,
        params={"DATABASE_ID": "WB_WGI", "INDICATOR": code, "format": "csv", "top": 50000},
        timeout=120, retries=3, patient=True,
    )
    latest = {}  # iso3 -> (year, value, converted_bool)
    for row in csv.DictReader(io.StringIO(r.text)):
        if (row.get("COMP_BREAKDOWN_1") or "").strip() != want:
            continue
        iso3 = (row.get("REF_AREA") or "").strip().upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue
        val = _num(row.get("OBS_VALUE"))
        yr = _year(row.get("TIME_PERIOD"))
        if val is None:
            continue
        if use_score:
            val = _sc_to_est(val)
        prev = latest.get(iso3)
        if prev is None or (yr or 0) >= (prev[0] or 0):
            latest[iso3] = (yr, val, use_score)
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
