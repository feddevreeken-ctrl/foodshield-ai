"""
IPC (Integrated Food Security Phase Classification) — acute food insecurity.

Public API at api.ipcinfo.org (no key for read endpoints).
Provides national + sub-national IPC Phase 3+ population counts.

Output: data/ipc.json
  {
    iso3: {
      "phase3plus_pct": <% of analysed pop in Phase 3 or worse>,
      "phase3plus_count": <people>,
      "phase4_count": <people in Emergency>,
      "phase5_count": <people in Catastrophe/Famine>,
      "period": "Mon YYYY - Mon YYYY",
      "subnational": [{"area": "...", "phase": 3, "pop_pct": ...}, ...]
    }
  }
"""
from collections import defaultdict
from _common import http_get, write_json

URL = "https://api.ipcinfo.org/population"


def main():
    # Pull country-level summaries for current and prior year
    out = {}
    for year in (2026, 2025):
        try:
            r = http_get(URL, params={"type": "A", "year": year, "format": "json"})
        except Exception as e:
            print(f"  year {year} failed: {e}")
            continue
        for row in r.json():
            iso3 = (row.get("country") or row.get("iso3") or "").upper()
            if not iso3 or iso3 in out:  # prefer most-recent year
                continue
            out[iso3] = {
                "phase3plus_pct": _num(row.get("phase3plus_percentage") or row.get("p3_plus_pct")),
                "phase3plus_count": _int(row.get("phase3plus_population")),
                "phase4_count": _int(row.get("phase4_population")),
                "phase5_count": _int(row.get("phase5_population") or row.get("famine_population")),
                "period": row.get("period_dates") or row.get("period"),
                "analysis_year": year,
            }

    # Sub-national breakdown (admin-1 areas) — pulled separately for current year
    try:
        r = http_get(URL, params={"type": "C", "year": 2026, "format": "json"})
        by_country = defaultdict(list)
        for row in r.json():
            iso3 = (row.get("country") or "").upper()
            if not iso3:
                continue
            by_country[iso3].append({
                "area": row.get("area_name") or row.get("title"),
                "phase": _int(row.get("overall_phase") or row.get("phase")),
                "pop_count": _int(row.get("population")),
                "pop_pct": _num(row.get("phase3plus_percentage")),
            })
        for iso3, areas in by_country.items():
            out.setdefault(iso3, {})["subnational"] = areas
    except Exception as e:
        print(f"  sub-national fetch failed: {e}")

    write_json(
        "ipc.json",
        out,
        source="IPC Info (api.ipcinfo.org)",
        notes="Phase 3 = Crisis, Phase 4 = Emergency, Phase 5 = Catastrophe/Famine.",
    )


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
