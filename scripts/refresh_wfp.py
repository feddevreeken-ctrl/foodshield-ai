"""
WFP HungerMap LIVE — adm0 (country-level) food security snapshot.

Public endpoint, no key required.
Provides FCS (Food Consumption Score) and rCSI (reduced Coping Strategy Index)
for ~90 monitored countries, updated daily.

Output: data/wfp_hungermap.json
  {
    iso3: {
      "fcs_pct": <% of population with poor/borderline food consumption>,
      "rcsi_pct": <% using crisis coping strategies>,
      "ipc_phase3plus_pct": <if available>,
      "updated": <date>
    }
  }
"""
from _common import http_get, write_json

URL = "https://api.hungermapdata.org/v2/adm0data.json"


def main():
    r = http_get(URL, timeout=45)
    raw = r.json()
    # WFP response shape: list of country objects with adm0_code, iso3, fcs, rcsi, etc.
    countries = raw.get("body") or raw.get("data") or raw
    if isinstance(countries, dict):
        countries = countries.get("countries", list(countries.values()))

    out = {}
    for c in countries:
        iso3 = c.get("iso3") or c.get("country_code") or c.get("adm0_code")
        if not iso3 or not isinstance(iso3, str) or len(iso3) != 3:
            continue
        fcs = c.get("fcs", {}) or {}
        rcsi = c.get("rcsi", {}) or {}
        out[iso3.upper()] = {
            "fcs_pct": _num(fcs.get("prevalence") or fcs.get("value") or c.get("fcs_pct")),
            "rcsi_pct": _num(rcsi.get("prevalence") or rcsi.get("value") or c.get("rcsi_pct")),
            "ipc_phase3plus_pct": _num(c.get("ipc_phase_3plus") or c.get("ipc_p3plus_pct")),
            "updated": c.get("updated") or c.get("date"),
        }

    write_json(
        "wfp_hungermap.json",
        out,
        source="WFP HungerMap LIVE (api.hungermapdata.org)",
        notes="FCS = food consumption score; rCSI = reduced coping strategy index; both are %-of-population.",
    )


def _num(v):
    try:
        if v is None or v == "":
            return None
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
