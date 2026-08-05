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


# v35 (Jun 2026) — WFP retired the public adm0data aggregate (its own upstream
# v1/alerts/country 500s) and moved FCS predictions behind authenticated ew-tool
# endpoints. Until WFP exposes public FCS again (or an EW-tool key is configured),
# we keep this file alive with the public IPC layer so the nowcast never starves:
# fcs_* / alerts are written as null — signal-absent, never fabricated.
URL_IPC_EWTOOL = "https://ew-tool-api.hungermapdata.org/ew/v1/ipc/food/insecurity/global/recent"


def _fetch_ipc_layer():
    r = http_get(URL_IPC_EWTOOL, timeout=45)
    rows = r.json()
    if isinstance(rows, dict) and isinstance(rows.get("body"), list):
        rows = rows["body"]
    out = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        iso3 = (row.get("iso3Alpha3") or "").upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue
        pct = row.get("phase35Percentage")
        out[iso3] = {
            "fcs_pct": None, "fcs_people_total": None,
            "ipc_phase3plus_pct": round(pct * 100, 2) if isinstance(pct, (int, float)) else None,
            "ipc_people": int(row.get("phase35Population") or 0) or None,
            "undernourishment_pct": None, "alerts": {},
            "country": None, "period": row.get("referencePeriod"),
            "analysis_date": row.get("analysisDate"),
        }
    if not out:
        raise RuntimeError("ew-tool IPC layer parsed to zero countries")
    return out


def main():
    out = {}
    used_url = None
    try:
        out = _fetch(URL_PRIMARY)
        used_url = URL_PRIMARY
    except Exception as e:
        print(f"[WARN] primary HungerMap endpoint failed ({e}); trying fallback")
        try:
            out = _fetch(URL_FALLBACK)
            used_url = URL_FALLBACK
        except Exception as e2:
            print(f"[WARN] legacy endpoints dead ({e2}); writing public IPC layer (fcs null)")
            out = _fetch_ipc_layer()
            used_url = URL_IPC_EWTOOL

    # v79 — DECLARE THE DEGRADATION. fcs_pct is the only field of this feed the
    # nowcast actually scores (wfp_pressure). When the public FCS endpoints are
    # dead we fall back to the IPC layer, which writes fcs_pct=null for every
    # country — so wfp_pressure was 0 for all 264 countries while the manifest
    # still reported status "ok" and both validators passed on shape alone.
    # A file whose only scored field is 100% null is not healthy; say so.
    n_fcs = sum(1 for v in out.values()
                if isinstance(v, dict) and isinstance(v.get("fcs_pct"), (int, float)))
    n_ipc = sum(1 for v in out.values()
                if isinstance(v, dict) and isinstance(v.get("ipc_phase3plus_pct"), (int, float)))
    status = "ok" if n_fcs else "degraded_fallback"
    print(f"[INFO] WFP coverage: fcs_pct {n_fcs}/{len(out)}, "
          f"ipc_phase3plus_pct {n_ipc}/{len(out)} -> status={status}")

    write_json(
        "wfp_hungermap.json",
        out,
        source=f"WFP HungerMap LIVE ({used_url})",
        status=status,
        notes=(
            "fcs_pct = % population with poor/borderline food consumption (from fcs fraction). "
            "ipc_phase3plus_pct = % in IPC Phase 3+. alerts = boolean flags from WFP HungerMap. "
            f"Parsed {len(out)} countries; fcs_pct present on {n_fcs}, "
            f"ipc_phase3plus_pct on {n_ipc}."
            + ("" if n_fcs else
               " DEGRADED: the public FCS endpoints are unavailable, so this build carries the "
               "IPC layer only and the nowcast's wfp_pressure signal contributes 0 for every "
               "country. Not fabricated — absent, and declared absent.")
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
