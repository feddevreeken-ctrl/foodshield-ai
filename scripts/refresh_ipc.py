"""
IPC (Integrated Food Security Phase Classification) — acute food insecurity.

May 2026: IPC restricted their public `api.ipcinfo.org/population` endpoint to
authenticated callers. WFP's HungerMap re-publishes the IPC table at
`api.hungermapdata.org/v2/ipc.json`, no key required (per HungerMap docs).

We use HungerMap as the primary source; if it's down, we attempt the official
IPC endpoint as a fallback (may still 401 without IPC_API_KEY).

Output: data/ipc.json
  {
    iso3: {
      "phase3plus_pct": <%>,
      "phase3plus_count": <people>,
      "phase4_count": <people>,
      "phase5_count": <people>,
      "period": "Mon YYYY - Mon YYYY",
      "analysis_date": "YYYY-MM-DD",
      "source_via": "hungermap" or "ipcinfo"
    }
  }
"""
from _common import http_get, write_json, env

URL_HUNGERMAP = "https://api.hungermapdata.org/v2/ipc.json"
URL_OFFICIAL = "https://api.ipcinfo.org/population"


def main():
    out = {}
    try:
        out = _fetch_hungermap()
        source_label = "WFP HungerMap re-publishes IPC (api.hungermapdata.org/v2/ipc.json)"
    except Exception as e:
        print(f"  [warn] HungerMap IPC failed ({e}); falling back to official endpoint")
        try:
            out = _fetch_official()
            source_label = "IPC Info official (api.ipcinfo.org)"
        except Exception as e2:
            print(f"  [warn] Official IPC also failed: {e2}")
            out = {}
            source_label = "IPC sources unavailable"

    write_json(
        "ipc.json",
        out,
        source=source_label,
        notes=(
            "Phase 3 = Crisis, Phase 4 = Emergency, Phase 5 = Catastrophe/Famine. "
            f"Covered {len(out)} countries."
        ),
    )


def _fetch_hungermap():
    r = http_get(URL_HUNGERMAP, timeout=45)
    raw = r.json()
    if isinstance(raw, dict) and isinstance(raw.get("body"), (list, dict)):
        raw = raw["body"]
    rows = raw if isinstance(raw, list) else (raw.get("countries") or [])

    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        iso3 = (row.get("iso3") or row.get("adm0_code") or "").upper()
        if not iso3 or len(iso3) != 3 or not iso3.isalpha():
            continue
        pop_aff = _int(row.get("ipc_population_affected") or row.get("phase3plus_population"))
        ipc_pct = _num(row.get("ipc_percent") or row.get("phase3plus_percentage"))
        out[iso3] = {
            "phase3plus_pct": ipc_pct,
            "phase3plus_count": pop_aff,
            "phase4_count": _int(row.get("phase_4_plus_population")),
            "phase5_count": _int(row.get("phase_5_population")),
            "period": row.get("analysis_period") or row.get("period"),
            "analysis_date": row.get("date_of_analysis"),
            "country": row.get("adm0_name") or row.get("country"),
            "source_via": "hungermap",
        }
    if not out:
        raise RuntimeError("HungerMap IPC returned zero rows")
    return out


def _fetch_official():
    """Original IPC endpoint — gated by IPC_API_KEY since May 2026."""
    key = env("IPC_API_KEY", required=False)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    out = {}
    for year in (2026, 2025):
        try:
            r = http_get(URL_OFFICIAL, params={"type": "A", "year": year, "format": "json"}, headers=headers)
        except Exception:
            continue
        for row in r.json():
            iso3 = (row.get("country") or row.get("iso3") or "").upper()
            if not iso3 or iso3 in out:
                continue
            out[iso3] = {
                "phase3plus_pct": _num(row.get("phase3plus_percentage") or row.get("p3_plus_pct")),
                "phase3plus_count": _int(row.get("phase3plus_population")),
                "phase4_count": _int(row.get("phase4_population")),
                "phase5_count": _int(row.get("phase5_population") or row.get("famine_population")),
                "period": row.get("period_dates") or row.get("period"),
                "analysis_year": year,
                "source_via": "ipcinfo",
            }
    if not out:
        raise RuntimeError("Official IPC returned zero rows")
    return out


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
