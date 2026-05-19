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

Robustness (May 2026 fixes):
  - HungerMap occasionally returns `properties` as a stringified JSON blob on
    null/disputed territories. We safely json.loads if so; if that fails we skip.
  - Multiple envelope shapes are tried (raw FeatureCollection, body wrapper,
    or flat dict keyed by ISO3) so a small response-format change doesn't kill
    the daily refresh.
  - Each feature is parsed inside its own try/except — one bad row never aborts
    the whole script.
"""
import json as _json

from _common import http_get, write_json

# Primary endpoint — GeoJSON FeatureCollection by ADM0.
URL_PRIMARY = "https://api.hungermapdata.org/v2/adm0data.json"
# Fallback endpoint — flatter shape used by HungerMap dashboard widgets.
URL_FALLBACK = "https://api.hungermapdata.org/v2/adm0summary.json"


def main():
    out = {}
    used_url = None
    try:
        out = _fetch(URL_PRIMARY)
        used_url = URL_PRIMARY
    except Exception as e:
        print(f"[WARN] primary HungerMap endpoint failed ({e}); trying fallback")
        out = _fetch(URL_FALLBACK)
        used_url = URL_FALLBACK

    write_json(
        "wfp_hungermap.json",
        out,
        source=f"WFP HungerMap LIVE ({used_url})",
        notes=(
            "fcs_pct = % population with poor/borderline food consumption (from fcs fraction). "
            "ipc_phase3plus_pct = % in IPC Phase 3+. alerts = boolean flags from WFP HungerMap. "
            f"Parsed {len(out)} countries."
        ),
    )


def _fetch(url):
    r = http_get(url, timeout=60)
    raw = r.json()

    # Try every envelope shape we have seen in the wild
    features = _extract_features(raw)

    out = {}
    skipped = 0
    for feat in features:
        try:
            props = _coerce_dict(feat.get("properties") if isinstance(feat, dict) else None)
            if not props:
                # Some endpoints return the country row directly without a wrapping `properties`.
                props = _coerce_dict(feat)
            if not props:
                skipped += 1
                continue

            iso3 = (props.get("iso3") or props.get("adm0_code") or "").upper().strip()
            if not iso3 or len(iso3) != 3 or not iso3.isalpha():
                skipped += 1
                continue

            fcs_frac = props.get("fcs")
            out[iso3] = {
                "fcs_pct": round(fcs_frac * 100, 2) if isinstance(fcs_frac, (int, float)) else None,
                "fcs_people_total": _int(props.get("fcs_people_total")),
                "ipc_phase3plus_pct": _num(props.get("ipcPopulation") or props.get("ipc_phase3plus")),
                "undernourishment_pct": _num(props.get("undernourishment")),
                "alerts": _coerce_dict(props.get("alerts")) or {},
                "country": props.get("adm0_name") or props.get("name"),
            }
        except Exception as e:
            # Never let one bad feature kill the whole refresh.
            skipped += 1
            print(f"[WARN] skipped a HungerMap row: {e}")
            continue

    print(f"[OK] WFP HungerMap parsed {len(out)} countries, skipped {skipped}")
    if not out:
        raise RuntimeError("HungerMap returned zero usable rows")
    return out


def _extract_features(raw):
    """Try every envelope we have seen — GeoJSON, body wrapper, or flat dict."""
    if isinstance(raw, dict):
        body = raw.get("body")
        if isinstance(body, dict):
            feats = body.get("features")
            if isinstance(feats, list) and feats:
                return feats
        feats = raw.get("features")
        if isinstance(feats, list) and feats:
            return feats
        # Last resort: maybe `raw` itself is a flat dict keyed by ISO3
        if all(isinstance(k, str) and len(k) == 3 for k in list(raw.keys())[:5]):
            return [{"properties": {**v, "iso3": k}} if isinstance(v, dict) else {} for k, v in raw.items()]
    if isinstance(raw, list):
        return raw
    return []


def _coerce_dict(v):
    """HungerMap sometimes ships `properties` as a JSON-encoded string. Parse if so."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = _json.loads(v)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


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
