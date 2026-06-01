"""
WRI Aqueduct 4.0 — Country Water Risk Rankings.

v23 (Jun 2026) — REWRITTEN for the World Bank Data360 mirror.
The old WRI S3 + GitHub CSV URLs are permanently 404. WB Data360 mirrors the
WRI_AQDT dataset and is the only stable machine-readable source, BUT it exposes
only TWO of the original five indicators:
    WRI_AQDT_BASELINE_RFR  — Riverine Flood Risk  -> flood_risk
    WRI_AQDT_BASELINE_DRR  — Drought Risk         -> drought_risk
Water stress / depletion / coastal flood are NOT in this mirror. We populate
what's available (flood + drought — the two most food-relevant), honestly, and
note the missing indicators in _meta.

Data360 CSV is LONG format: one row per (REF_AREA, INDICATOR, weight-combo).
We keep COMP_BREAKDOWN_1 == 'WRI_AQDT_WEIGHT_POP' (population-weighted, the
default presentation) and read OBS_VALUE (0-5 score) per REF_AREA (ISO3).

OUTPUT: data/aqueduct.json
  { "data": { "SOM": {"drought_risk": {"score": 4.84}, "flood_risk": {...},
              "source": "...", "quality_flag": "sourced"} } }
"""
import csv
import io

from _common import http_get, write_json

# WB Data360 mirror — the only stable source as of Jun 2026.
URL = "https://data360api.worldbank.org/data360/data?DATABASE_ID=WRI_AQDT&format=csv"

# Data360 INDICATOR code -> our shorthand key
INDICATORS = {
    "WRI_AQDT_BASELINE_RFR": "flood_risk",     # Riverine Flood Risk
    "WRI_AQDT_BASELINE_DRR": "drought_risk",   # Drought Risk
}
# Prefer population-weighted aggregation (matches WRI's headline presentation)
PREFERRED_WEIGHT = "WRI_AQDT_WEIGHT_POP"


def main():
    try:
        r = http_get(URL, timeout=120, retries=3, patient=True)
    except Exception as e:
        write_json("aqueduct.json", {}, source="WRI Aqueduct 4.0",
                   notes=f"WB Data360 mirror fetch failed: {e}")
        return

    text = r.text
    if not text or len(text) < 1000:
        write_json("aqueduct.json", {}, source="WRI Aqueduct 4.0",
                   notes="WB Data360 mirror returned too little data.")
        return

    reader = csv.DictReader(io.StringIO(text))
    # Two passes: prefer population-weighted rows, but fall back to any weight
    # if a country only has non-pop-weighted rows for an indicator.
    primary = {}   # (iso3, key) -> score from preferred weight
    fallback = {}  # (iso3, key) -> score from any weight
    rows_seen = 0
    for row in reader:
        rows_seen += 1
        ind = (row.get("INDICATOR") or "").strip()
        key = INDICATORS.get(ind)
        if not key:
            continue
        iso3 = (row.get("REF_AREA") or "").strip().upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue
        score = _num(row.get("OBS_VALUE"))
        if score is None:
            continue
        weight = (row.get("COMP_BREAKDOWN_1") or "").strip()
        slot = (iso3, key)
        if weight == PREFERRED_WEIGHT:
            primary[slot] = score
        else:
            fallback.setdefault(slot, score)

    merged = dict(fallback)
    merged.update(primary)  # preferred weight wins

    out = {}
    for (iso3, key), score in merged.items():
        country = out.setdefault(iso3, {
            "year": "baseline",
            "source": "WRI Aqueduct 4.0 (World Bank Data360 mirror)",
            "source_url": "https://www.wri.org/data/aqueduct-40-country-rankings",
            "quality_flag": "sourced",
        })
        country[key] = {"score": round(score, 2)}

    print(f"[INFO] Aqueduct: parsed {rows_seen} rows, {len(out)} countries "
          f"(indicators available in mirror: flood_risk, drought_risk)")
    for ref in ("USA", "NLD", "SOM", "IND", "AFG", "AUS", "EGY"):
        if ref in out:
            d = out[ref]
            print(f"  [ref] {ref}: drought={d.get('drought_risk',{}).get('score')}, "
                  f"flood={d.get('flood_risk',{}).get('score')}")

    write_json(
        "aqueduct.json", out,
        source="WRI Aqueduct 4.0 (World Bank Data360 mirror)",
        notes=(
            f"Country water-risk scores from WRI Aqueduct 4.0, via the World Bank "
            f"Data360 mirror (the WRI S3/GitHub CSVs are permanently offline as of "
            f"Jun 2026). This mirror exposes only 2 of the original 5 indicators: "
            f"riverine flood risk and drought risk (0-5 scale, population-weighted). "
            f"Water stress, water depletion, and coastal flood are NOT available in "
            f"this mirror. Covered {len(out)} countries."
        ),
    )


def _num(v):
    try:
        return float(v) if v not in (None, "", "..", "NA", "_Z") else None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
