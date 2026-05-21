"""
WRI Aqueduct 4.0 — Country Water Risk Rankings.

Pipeline 8 of the structural-data series. Per-country composite water risk
scores from the World Resources Institute. Pairs with ND-GAIN (Pipeline 6)
to give a richer climate/water picture on the country panel.

WHY AQUEDUCT:
  Aqueduct is the standard reference for cross-country water risk in ESG and
  development policy. v4.0 released Aug 2023, still current May 2026.

ARCHITECTURE NOTE:
  Aqueduct country-rankings is published as a CSV in LONG format:
    one row per (country, indicator, weight, year/scenario)
  We filter to weight='Def' (default total water-use weighting) and
  year='baseline' for current scores. Each country then has rows for
  bws, bwd, drr, rfr, cfr — 5 indicators we pivot into one country payload.

DOWNLOAD:
  Primary:  https://wri-public-data.s3.amazonaws.com/Aqueduct40/country_rankings_data/Aq40_country_rankings.csv
  Mirror:   https://github.com/wri/Aqueduct40/raw/master/data/country_rankings_data/Aq40_country_rankings.csv
  WB mirror: https://data360-files.worldbank.org/wri/aqueduct40/country_rankings.csv
  We try in order. If all fail, write empty payload with a clear note.

INDICATORS:
  bws — Baseline Water Stress: ratio of withdrawals to renewable supply
  bwd — Baseline Water Depletion: groundwater consumption vs recharge
  drr — Drought Risk: composite of hazard + exposure + vulnerability
  rfr — Riverine Flood Risk
  cfr — Coastal Flood Risk

  Scores are 0-5 (continuous) plus integer category:
    -1 = arid / no data (NOT zero — distinct meaning)
     0 = Low (<10%)
     1 = Low-Medium (10-20%)
     2 = Medium-High (20-40%)
     3 = High (40-80%)
     4 = Extremely High (>80%)

OUTPUT: data/aqueduct.json
  {
    "_meta": {...},
    "data": {
      "BGD": {
        "water_stress":   {"score": 1.84, "cat": 1, "label": "Low - Medium (10-20%)"},
        "water_depletion": {"score": 0.62, "cat": 0, "label": "Low (<10%)"},
        "drought_risk":   {"score": 2.91, "cat": 2, "label": "Medium - High (20-40%)"},
        "flood_risk":     {"score": 3.87, "cat": 3, "label": "High (40-80%)"},
        "coastal_flood":  {"score": 4.21, "cat": 4, "label": "Extremely High (>80%)"},
        "year": "baseline",
        "source": "WRI Aqueduct 4.0",
        "source_url": "https://www.wri.org/data/aqueduct-40-country-rankings",
        "quality_flag": "sourced",
      },
      ...
    }
  }

Wired into:
  - run_all.py STEPS list
  - build_source_manifest.py (mode='reference')
  - foodshield-v19.html: LIVE.aqueduct loader + aqueductCardHTML(iso3) renderer
"""
import csv
import io

from _common import http_get, write_json

URLS = [
    "https://wri-public-data.s3.amazonaws.com/Aqueduct40/country_rankings_data/Aq40_country_rankings.csv",
    "https://github.com/wri/Aqueduct40/raw/master/data/country_rankings_data/Aq40_country_rankings.csv",
    "https://raw.githubusercontent.com/wri/Aqueduct40/master/data/country_rankings_data/Aq40_country_rankings.csv",
]

# Aqueduct indicator code → our shorthand key on the output
INDICATORS = {
    "bws": "water_stress",       # Baseline Water Stress
    "bwd": "water_depletion",    # Baseline Water Depletion
    "drr": "drought_risk",       # Drought Risk
    "rfr": "flood_risk",         # Riverine Flood Risk
    "cfr": "coastal_flood",      # Coastal Flood Risk
}


def main():
    text = None
    used_url = None
    for url in URLS:
        try:
            r = http_get(url, timeout=120, retries=2, patient=True)
            if r.text and len(r.text) > 1000:
                text = r.text
                used_url = url
                print(f"[OK] downloaded {len(text)//1024} KB from {url}")
                break
            print(f"  [skip] {url}: response too small")
        except Exception as e:
            print(f"  [skip] {url}: {e}")

    if not text:
        write_json(
            "aqueduct.json", {},
            source="WRI Aqueduct 4.0",
            notes=(
                "All download URLs failed. Aqueduct CSV is public but mirrors "
                "occasionally rate-limit anonymous fetches. Manual fallback: "
                "https://github.com/wri/Aqueduct40 → data/country_rankings_data/"
            ),
        )
        return

    reader = csv.DictReader(io.StringIO(text))
    # Aqueduct long-format columns: gid_0, name_0, weight, indicator_name,
    # year, scenario, score, label, cat, score_ranked
    # We want weight=Def and year=baseline only.
    out = {}
    rows_seen = 0
    rows_kept = 0
    for row in reader:
        rows_seen += 1
        weight = (row.get("weight") or "").strip()
        year = (row.get("year") or "").strip().lower()
        if weight != "Def":
            continue
        if year and year != "baseline":
            continue
        ind = (row.get("indicator_name") or "").strip().lower()
        if ind not in INDICATORS:
            continue
        iso3 = (row.get("gid_0") or "").strip().upper()
        if not iso3 or len(iso3) != 3 or not iso3.isalpha():
            continue
        score = _num(row.get("score"))
        cat = _int(row.get("cat"))
        label = (row.get("label") or "").strip()
        if score is None and cat is None:
            continue
        key = INDICATORS[ind]
        country_name = (row.get("name_0") or "").strip()
        country_slot = out.setdefault(iso3, {
            "country": country_name,
            "year": "baseline",
            "source": "WRI Aqueduct 4.0",
            "source_url": "https://www.wri.org/data/aqueduct-40-country-rankings",
            "quality_flag": "sourced",
        })
        country_slot[key] = {
            "score": round(score, 2) if score is not None else None,
            "cat": cat,
            "label": label,
        }
        rows_kept += 1

    print(f"[INFO] Parsed {rows_seen} rows, kept {rows_kept}, covering {len(out)} countries")

    # Sanity check: a few reference points
    for ref in ("USA", "NLD", "BGD", "EGY", "IND", "AFG", "AUS"):
        if ref in out:
            ws = out[ref].get("water_stress", {})
            df = out[ref].get("drought_risk", {})
            print(f"  [ref] {ref}: water_stress cat={ws.get('cat')} ({ws.get('label','')[:30]}); "
                  f"drought cat={df.get('cat')}")

    write_json(
        "aqueduct.json",
        out,
        source=f"WRI Aqueduct 4.0 ({used_url})",
        notes=(
            f"Country water risk scores from WRI Aqueduct 4.0 (Aug 2023 release). "
            f"5 indicators per country: baseline water stress, baseline water depletion, "
            f"drought risk, riverine flood risk, coastal flood risk. "
            f"Each indicator has a 0-5 continuous score and a -1 to 4 categorical bucket "
            f"(-1 = arid/no data, 0 = Low, 4 = Extremely High). "
            f"Covered {len(out)} countries. Aqueduct refreshes every 3-5 years upstream."
        ),
    )


def _num(v):
    try:
        return float(v) if v not in (None, "", "..", "NA") else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
