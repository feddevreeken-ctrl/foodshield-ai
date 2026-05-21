"""
World Bank Worldwide Governance Indicators (WGI).

Pipeline 13 of the structural-data series. Six governance dimensions per
country, biennial through 2002 then annual. Feeds the INFORM 'Lack of
coping capacity' dimension (institutional/infrastructure side) and the
Conflict/Logistics component of the structural FDRS — both of which were
heuristic before.

WHY WGI:
  WGI is the standard cross-country governance benchmark, used by IMF, ADB,
  EBRD, and most major risk indices. Released annually each September by
  the World Bank Development Research Group.

ARCHITECTURE NOTE (May 2026):
  WGI lives in WB source_id=3 (not WDI's source_id=2), so the WDI_CSV.zip
  bulk we pull in Pipeline 4 doesn't contain WGI series. Two reasonable
  paths: pull WGI_CSV.zip (~5 MB) or use the per-indicator REST API
  (6 calls × ~2 MB JSON each). REST is simpler and fast enough.

WHAT WE PULL (per country, latest year per indicator):
  CC.EST — Control of Corruption
  GE.EST — Government Effectiveness
  PV.EST — Political Stability and Absence of Violence/Terrorism
  RQ.EST — Regulatory Quality
  RL.EST — Rule of Law
  VA.EST — Voice and Accountability

  Score range: -2.5 (worst) to +2.5 (best). Standard interpretation:
  scores > 0 mean above-median governance globally; scores below -1.5 are
  failing-state territory.

OUTPUT: data/wgi.json
  {
    "_meta": {...},
    "data": {
      "BGD": {
        "control_corruption":   {"value": -0.85, "year": 2023},
        "gov_effectiveness":    {"value": -0.65, "year": 2023},
        "political_stability":  {"value": -1.12, "year": 2023},
        "regulatory_quality":   {"value": -0.78, "year": 2023},
        "rule_of_law":          {"value": -0.91, "year": 2023},
        "voice_accountability": {"value": -0.45, "year": 2023},
        "year": 2023,
        "source": "World Bank Worldwide Governance Indicators",
        "source_url": "https://www.worldbank.org/en/publication/worldwide-governance-indicators",
        "quality_flag": "sourced",
      },
      ...
    }
  }

Wired into:
  - run_all.py STEPS list
  - build_source_manifest.py (mode='reference')
  - foodshield-v19.html: LIVE.wgi loader + wgiCardHTML(iso3) renderer
"""
from _common import http_get, write_json

BASE = "https://api.worldbank.org/v2/country/all/indicator/{code}"

# Indicator code → our shorthand key. Names match what wgiCardHTML expects.
INDICATORS = {
    "CC.EST": ("control_corruption",    "Control of Corruption"),
    "GE.EST": ("gov_effectiveness",     "Government Effectiveness"),
    "PV.EST": ("political_stability",   "Political Stability"),
    "RQ.EST": ("regulatory_quality",    "Regulatory Quality"),
    "RL.EST": ("rule_of_law",           "Rule of Law"),
    "VA.EST": ("voice_accountability",  "Voice & Accountability"),
}


def main():
    out = {}
    failures = []
    for code, (key, label) in INDICATORS.items():
        print(f"[INFO] WGI {code} — {label}")
        try:
            # v20.29 — pull 15 years instead of just the most-recent value.
            # That powers the sparkline + lets us show 'change since 2010'
            # context on country panels. WGI data is annual since 2002.
            r = http_get(
                BASE.format(code=code),
                params={"format": "json", "date": "2010:2026", "per_page": 5000},
                timeout=45,
                retries=3,
                patient=True,  # ride out WB API hiccups (run #17, May 21 2026)
            )
        except Exception as e:
            print(f"  [warn] {code} fetch failed: {e}")
            failures.append(code)
            continue
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            print(f"  [warn] {code} unexpected response shape")
            failures.append(code)
            continue
        rows = data[1] or []
        # v20.29 — group by ISO3 then sort by year asc so we can store a
        # compact (year,value) series for sparklines.
        per_iso = {}
        for row in rows:
            iso3 = (row.get("countryiso3code") or "").strip().upper()
            val = row.get("value")
            year = row.get("date")
            if not iso3 or len(iso3) != 3 or val is None:
                continue
            try:
                v = round(float(val), 2)
                y = int(year) if year else None
            except (TypeError, ValueError):
                continue
            if y is None:
                continue
            per_iso.setdefault(iso3, {"_country_name": None, "obs": [], "_country_row": row}).get("obs").append((y, v))
            if not per_iso[iso3]["_country_name"]:
                cn = row.get("country", {})
                if isinstance(cn, dict):
                    per_iso[iso3]["_country_name"] = cn.get("value")

        kept = 0
        for iso3, payload in per_iso.items():
            obs = sorted(payload["obs"], key=lambda x: x[0])
            if not obs:
                continue
            latest_year, latest_val = obs[-1]
            country_slot = out.setdefault(iso3, {
                "country": payload["_country_name"],
                "source": "World Bank Worldwide Governance Indicators",
                "source_url": "https://www.worldbank.org/en/publication/worldwide-governance-indicators",
                "quality_flag": "sourced",
            })
            country_slot[key] = {
                "value": latest_val,
                "year": latest_year,
                "label": label,
                # Compact series: list of [year, value]. Trim to last 15 entries
                # to keep the JSON small.
                "series": obs[-15:],
            }
            existing_year = country_slot.get("year")
            if existing_year is None or latest_year > existing_year:
                country_slot["year"] = latest_year
            kept += 1
        print(f"  [OK] {code}: {kept} country rows (with series)")

    if failures:
        print(f"[WARN] {len(failures)} indicator(s) failed: {failures}")

    # Drop any countries with zero indicators (shouldn't happen but be safe)
    out = {iso: row for iso, row in out.items()
           if any(k for k in row if k in [v[0] for v in INDICATORS.values()])}

    print(f"[INFO] Wrote WGI scores for {len(out)} countries")

    # Sanity check on well-known reference points
    for ref in ("USA", "DNK", "NLD", "DEU", "JPN", "BGD", "AFG", "SOM", "YEM", "VEN"):
        if ref in out:
            row = out[ref]
            rl = row.get("rule_of_law", {}).get("value")
            cc = row.get("control_corruption", {}).get("value")
            ge = row.get("gov_effectiveness", {}).get("value")
            print(f"  [ref] {ref}: rule_of_law={rl}, control_corruption={cc}, gov_effectiveness={ge}")

    write_json(
        "wgi.json",
        out,
        source="World Bank Worldwide Governance Indicators (api.worldbank.org/v2)",
        notes=(
            f"6 governance dimensions per country, latest year. "
            f"Score range -2.5 (worst) to +2.5 (best). Annual release each September. "
            f"Covered {len(out)} countries. "
            f"Indicators: Control of Corruption, Government Effectiveness, "
            f"Political Stability, Regulatory Quality, Rule of Law, "
            f"Voice & Accountability. "
            f"{len(failures)} indicator(s) failed: {failures}" if failures else
            f"6 governance dimensions per country, latest year. "
            f"Score range -2.5 (worst) to +2.5 (best). Annual release each September. "
            f"Covered {len(out)} countries."
        ),
    )


if __name__ == "__main__":
    main()
