"""
WFP HungerMap LIVE — adm0 (country-level) food security snapshot.

Public endpoint, no key required.
The actual API returns a GeoJSON FeatureCollection where each feature.properties
contains the country's food-security indicators.

Field mapping (verified May 2026 against api.hungermapdata.org/v2/adm0data.json):
  fcs                — % with poor/borderline food consumption (0–1 fraction)
  fcs_people_total   — absolute count
  ipcPopulation      — IPC Phase 3+ % (already a percentage, e.g. 24.63)
  undernourishment   — FAO % undernourished
  alerts             — { conflict, climateDry, climateWet, fcs, marketAccess, ndvi }

Output: data/wfp_hungermap.json
  {
    iso3: {
      "fcs_pct": <% of population>,
      "fcs_people_total": <absolute count>,
      "ipc_phase3plus_pct": <% of population>,
      "undernourishment_pct": <%>,
      "alerts": { conflict, climateDry, ... },
      "country": <name>,
    }
  }
"""
from _common import http_get, write_json

URL = "https://api.hungermapdata.org/v2/adm0data.json"


def main():
    r = http_get(URL, timeout=60)
    raw = r.json()
    body = raw.get("body") or {}
    features = body.get("features") or []
    if not features:
        # Fallback: maybe API returned a different envelope
        features = raw.get("features") or []

    out = {}
    for feat in features:
        props = feat.get("properties") or {}
        iso3 = (props.get("iso3") or "").upper()
        if not iso3 or len(iso3) != 3 or not iso3.isalpha():
            continue  # skip disputed/non-standard codes
        fcs_frac = props.get("fcs")  # 0–1 fraction
        out[iso3] = {
            "fcs_pct": round(fcs_frac * 100, 2) if isinstance(fcs_frac, (int, float)) else None,
            "fcs_people_total": _int(props.get("fcs_people_total")),
            "ipc_phase3plus_pct": _num(props.get("ipcPopulation")),
            "undernourishment_pct": _num(props.get("undernourishment")),
            "alerts": props.get("alerts") or {},
            "country": props.get("adm0_name"),
        }

    write_json(
        "wfp_hungermap.json",
        out,
        source="WFP HungerMap LIVE (api.hungermapdata.org/v2/adm0data.json)",
        notes=(
            "fcs_pct = % population with poor/borderline food consumption (from fcs fraction). "
            "ipc_phase3plus_pct = % in IPC Phase 3+. alerts = boolean flags from WFP HungerMap."
        ),
    )


def _num(v):
    try:
        if v is None or v == "":
            return None
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
