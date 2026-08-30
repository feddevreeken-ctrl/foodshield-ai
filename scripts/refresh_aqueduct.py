"""
WRI Aqueduct 4.0 — Country Water Risk Rankings.

v81 (Aug 2026) — three bugs fixed, one indicator recovered.

The v23 rewrite pointed at the World Bank Data360 mirror and got the endpoint
right, but the query and the parse were both wrong:

  1. SILENT TRUNCATION. It requested the whole DATABASE_ID with no INDICATOR
     filter and no `top`, and Data360 caps that at 1000 rows. The response was
     RFR 516 + DRR 484 — i.e. drought was cut off mid-indicator. That, not the
     upstream, is why drought risk covered 96 countries against flood's 188.

  2. MIXED UNITS. Every (country, weight) appears three times, as
     UNIT_MEASURE ∈ {SCORE (0-5 float), RANK, RISK_CAT (0-4 int)}. Nothing
     filtered on it, so last-write-wins left RISK_CAT integers sitting in
     fields the app reads as scores. Verified wrong before this fix:
     USA flood 0.0 (true 0.83), NLD 0.0 (true 0.16), SOM 4.0 (true 5.0).

  3. A DOCSTRING THAT WAS SIMPLY FALSE. It claimed water stress "is NOT in this
     mirror". Baseline Water Stress is there — WRI_AQDT_BASELINE_BWS, 164
     countries — and its absence is why the `waterstress` scenario returned
     NO DATA for 118 of 214 countries, including Somalia, Sudan, Yemen, Chad,
     Niger, Mali, Mozambique, Egypt and Iraq: the countries the shock is for.

Weighting differs per indicator and must not be assumed. Verified 2026-08-30:
  BWS  6 weights (TOT/DOM/IND/IRR/LIV/ONE), no POP  -> 164 countries at SCORE
  DRR  6 weights (same set), no POP                 -> 138 countries at SCORE
  RFR  POP only                                      -> 164 countries at SCORE
So the preference order is per-indicator, falling back rather than assuming.

OUTPUT: data/aqueduct.json
  { "data": { "SOM": {"water_stress": {"score": 1.67}, "drought_risk": {...},
              "flood_risk": {...}, "source": "...", "quality_flag": "sourced"} } }
"""
import csv
import io

from _common import http_get, write_json

BASE = ("https://data360api.worldbank.org/data360/data"
        "?DATABASE_ID=WRI_AQDT&INDICATOR={ind}&format=csv&top=50000")

# Data360 INDICATOR code -> our shorthand key, in output order.
INDICATORS = {
    "WRI_AQDT_BASELINE_BWS": "water_stress",   # Baseline Water Stress
    "WRI_AQDT_BASELINE_DRR": "drought_risk",   # Drought Risk
    "WRI_AQDT_BASELINE_RFR": "flood_risk",     # Riverine Flood Risk
}

# Aggregation weight, most-preferred first. TOT (total water withdrawal) is
# WRI's all-sector default; POP is the only weight RFR publishes; ONE is the
# unweighted arithmetic mean and the last honest resort.
WEIGHT_PREFERENCE = (
    "WRI_AQDT_WEIGHT_TOT",
    "WRI_AQDT_WEIGHT_POP",
    "WRI_AQDT_WEIGHT_ONE",
)


def _fetch_indicator(ind):
    """Return {iso3: score} for one indicator, SCORE rows only."""
    r = http_get(BASE.format(ind=ind), timeout=120, retries=3, patient=True)
    text = r.text or ""
    if len(text) < 500:
        raise RuntimeError(f"{ind}: mirror returned {len(text)} bytes")
    by_weight = {}
    rows_seen = 0
    for row in csv.DictReader(io.StringIO(text)):
        rows_seen += 1
        # SCORE is the 0-5 continuous risk value. RANK and RISK_CAT share the
        # same OBS_VALUE column and are NOT interchangeable with it.
        if (row.get("UNIT_MEASURE") or "").strip() != "SCORE":
            continue
        iso3 = (row.get("REF_AREA") or "").strip().upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue
        score = _num(row.get("OBS_VALUE"))
        if score is None:
            continue
        weight = (row.get("COMP_BREAKDOWN_1") or "").strip()
        by_weight.setdefault(weight, {})[iso3] = score

    if rows_seen >= 1000 and rows_seen % 1000 == 0:
        print(f"  [warn] {ind}: {rows_seen} rows is a suspicious round number — "
              f"check for a server-side row cap")

    out = {}
    for weight in WEIGHT_PREFERENCE:
        for iso3, score in (by_weight.get(weight) or {}).items():
            out.setdefault(iso3, score)
    # Anything published under a weight we did not rank, rather than dropping it.
    for weight, vals in by_weight.items():
        if weight in WEIGHT_PREFERENCE:
            continue
        for iso3, score in vals.items():
            out.setdefault(iso3, score)
    print(f"  [ok] {ind}: {rows_seen} rows -> {len(out)} countries "
          f"(weights present: {', '.join(sorted(by_weight)) or 'none'})")
    return out


def main():
    per_indicator = {}
    failures = []
    for ind, key in INDICATORS.items():
        try:
            per_indicator[key] = _fetch_indicator(ind)
        except Exception as e:
            failures.append(f"{ind}: {e}")
            print(f"  [warn] {ind} failed: {e}")

    if not per_indicator:
        write_json("aqueduct.json", {}, source="WRI Aqueduct 4.0",
                   notes="WB Data360 mirror fetch failed: " + "; ".join(failures))
        return

    out = {}
    for key, scores in per_indicator.items():
        for iso3, score in scores.items():
            country = out.setdefault(iso3, {
                "year": "baseline",
                "source": "WRI Aqueduct 4.0 (World Bank Data360 mirror)",
                "source_url": "https://www.wri.org/data/aqueduct-40-country-rankings",
                "quality_flag": "sourced",
            })
            country[key] = {"score": round(score, 2)}

    counts = {k: len(v) for k, v in per_indicator.items()}
    print(f"[INFO] Aqueduct: {len(out)} countries | " +
          " ".join(f"{k}={v}" for k, v in counts.items()))
    for ref in ("USA", "NLD", "SOM", "IND", "YEM", "EGY", "SDN", "TCD"):
        if ref in out:
            d = out[ref]
            print(f"  [ref] {ref}: stress={d.get('water_stress',{}).get('score')}, "
                  f"drought={d.get('drought_risk',{}).get('score')}, "
                  f"flood={d.get('flood_risk',{}).get('score')}")

    write_json(
        "aqueduct.json", out,
        source="WRI Aqueduct 4.0 (World Bank Data360 mirror)",
        notes=(
            "Country water-risk scores from WRI Aqueduct 4.0 via the World Bank "
            "Data360 mirror (WRI's own S3/GitHub CSVs are permanently offline). "
            "Three indicators, 0-5 scale, SCORE rows only — RANK and RISK_CAT "
            "share the same value column and are excluded. "
            f"Coverage: water_stress {counts.get('water_stress', 0)}, "
            f"drought_risk {counts.get('drought_risk', 0)}, "
            f"flood_risk {counts.get('flood_risk', 0)} countries. "
            "Aggregation weight is total water withdrawal where published, "
            "population-weighted for riverine flood (the only weight it carries). "
            "Baseline reflects 1979-2019 hydrology — structural, not current-season. "
            "Coastal flood and water depletion remain absent from this mirror."
            + (f" Partial run: {'; '.join(failures)}" if failures else "")
        ),
    )


def _num(v):
    try:
        return float(v) if v not in (None, "", "..", "NA", "_Z") else None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
