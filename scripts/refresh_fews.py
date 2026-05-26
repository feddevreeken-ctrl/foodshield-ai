"""
FEWS NET — IPC-style acute food insecurity classifications.

FEWS NET (Famine Early Warning Systems Network) is the USAID-funded humanitarian
forecasting system run by Chemonics. It publishes IPC-compatible Phase 1–5
classifications for ~35 crisis countries with quarterly current + 3-month
projected + 8-month projected periods. The Food Data Warehouse (FDW) is the
public-facing REST API.

References:
  - https://help.fews.net/fdw/fews-net-api
  - https://fdw.fews.net/api/  (DRF-style browsable API root)

Authentication:
  FDW requires an account-issued token. Register at help.fews.net and put the
  token into the GitHub repository secret FEWS_API_TOKEN. Without it this script
  writes an empty stub so the workflow still completes and the source manifest
  flips to 'setup_required'.

Output: data/fews.json
  {
    iso3: {
      "current_phase":    <int 1..5>,           # highest classification across the country
      "projected_phase":  <int 1..5>,           # next-quarter projection
      "projected2_phase": <int 1..5 | null>,    # 6-8 month projection if available
      "delta":            <int>,                # projected − current
      "current_period":   "<label>",            # e.g. "Apr–May 2026"
      "projected_period": "<label>",            # e.g. "Jun–Sep 2026"
      "report_url":       "<deep-link>",        # latest country page on fews.net
      "as_of":            "<YYYY-MM-DD>",
    }
  }

Notes:
  - Country-level summary is the worst phase observed across any admin unit
    that period. This matches how IPC headlines a country (e.g. "Yemen Phase 4
    in northern governorates"). The country admin geometry detail is NOT
    flattened here — that's a separate per-area dataset we don't need for FDRS.
  - FEWS NET only covers ~35 countries by design (no Switzerland, no Australia).
    Countries absent from FEWS are absent from this output — that's correct.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from _common import http_get, write_json, env


BASE = "https://fdw.fews.net/api"

# v22.18 — Country slugs FEWS NET tracks for IPC-style outlook reports.
# Curated from FEWS NET's public country list. ISO3 → FEWS short slug used
# in their report URLs. Used to build deep links into the country page.
FEWS_COUNTRY_SLUGS = {
    "AFG": "afghanistan",
    "BFA": "burkina-faso",
    "BDI": "burundi",
    "CAF": "central-african-republic",
    "TCD": "chad",
    "COD": "democratic-republic-congo",
    "ETH": "ethiopia",
    "GTM": "guatemala",
    "HTI": "haiti",
    "HND": "honduras",
    "KEN": "kenya",
    "LBR": "liberia",
    "MDG": "madagascar",
    "MWI": "malawi",
    "MLI": "mali",
    "MRT": "mauritania",
    "MOZ": "mozambique",
    "MMR": "myanmar-burma",
    "NER": "niger",
    "NGA": "nigeria",
    "PSE": "west-bank-and-gaza",
    "PAK": "pakistan",
    "RWA": "rwanda",
    "SEN": "senegal",
    "SLE": "sierra-leone",
    "SOM": "somalia",
    "SSD": "south-sudan",
    "SDN": "sudan",
    "SYR": "syria",
    "TJK": "tajikistan",
    "TZA": "tanzania",
    "UGA": "uganda",
    "YEM": "yemen",
    "ZMB": "zambia",
    "ZWE": "zimbabwe",
}


def _phase_label(p):
    return {
        1: "Minimal (1)", 2: "Stressed (2)", 3: "Crisis (3)",
        4: "Emergency (4)", 5: "Famine (5)",
    }.get(p, "—")


def _period_label(start, end):
    """Convert (YYYY-MM-DD, YYYY-MM-DD) → 'Mon YYYY – Mon YYYY' display."""
    if not start or not end:
        return None
    try:
        a = date.fromisoformat(start[:10])
        b = date.fromisoformat(end[:10])
    except Exception:
        return None
    if a.year == b.year:
        return f"{a.strftime('%b')}–{b.strftime('%b %Y')}"
    return f"{a.strftime('%b %Y')} – {b.strftime('%b %Y')}"


def _fetch_classifications(token):
    """Pull current + projected IPC-style phase classifications country-wide.

    The FDW endpoint we use:
      GET /api/ipcphase/?format=json&country_code=ETH&period_date__gte=YYYY-MM-DD

    Returns a list of dicts with: country_code, ipc_phase, period_date_start,
    period_date_end, period_type ('current' | 'projected' | 'projected2'),
    fnid (FEWS NET admin ID).
    """
    headers = {"Authorization": f"Token {token}"}
    out = []
    for iso3, slug in FEWS_COUNTRY_SLUGS.items():
        try:
            r = http_get(
                f"{BASE}/ipcphase/",
                params={
                    "format": "json",
                    "country_code": iso3,
                    "limit": 200,
                },
                headers=headers,
                timeout=20,
                retries=2,
            )
        except Exception as e:
            print(f"  [warn] FEWS {iso3} fetch failed: {e}")
            continue
        body = r.json() or {}
        rows = body.get("results") or body if isinstance(body, list) else body.get("results", [])
        for row in rows:
            row["_iso3"] = iso3
            row["_slug"] = slug
            out.append(row)
    return out


def _summarize_country(rows):
    """Reduce per-area rows to one row per period_type, taking the worst phase.

    FEWS publishes a phase per admin-2 unit. The country-level headline is the
    worst classification observed in any unit that period. Returns a dict like:
      { 'current': (phase, period_label, period_end),
        'projected': (...), 'projected2': (...) }
    """
    by_type = defaultdict(list)
    for r in rows:
        ptype = (r.get("period_type") or "").lower()
        if ptype not in ("current", "projected", "projected2"):
            continue
        phase = r.get("ipc_phase")
        if not isinstance(phase, int):
            try:
                phase = int(phase)
            except (TypeError, ValueError):
                continue
        by_type[ptype].append({
            "phase": phase,
            "start": r.get("period_date_start") or r.get("start_date"),
            "end": r.get("period_date_end") or r.get("end_date"),
        })
    out = {}
    for ptype, lst in by_type.items():
        if not lst:
            continue
        worst = max(lst, key=lambda x: x["phase"])
        out[ptype] = {
            "phase": worst["phase"],
            "period_label": _period_label(worst["start"], worst["end"]),
            "end": worst["end"],
        }
    return out


def main():
    token = env("FEWS_API_TOKEN", required=False)
    if not token:
        write_json(
            "fews.json",
            {},
            source="FEWS NET FDW (fdw.fews.net/api)",
            notes=(
                "FEWS_API_TOKEN not configured — empty stub. Register at "
                "https://help.fews.net/fdw/fews-net-api and add the token "
                "to GitHub repo secrets to enable the live feed."
            ),
        )
        return

    raw = _fetch_classifications(token)
    by_iso = defaultdict(list)
    for r in raw:
        by_iso[r["_iso3"]].append(r)

    out = {}
    today = date.today().isoformat()
    for iso3, rows in by_iso.items():
        slug = FEWS_COUNTRY_SLUGS.get(iso3)
        summary = _summarize_country(rows)
        cur = summary.get("current")
        proj = summary.get("projected")
        proj2 = summary.get("projected2")
        if not cur and not proj:
            continue  # nothing usable
        delta = None
        if cur and proj:
            delta = proj["phase"] - cur["phase"]
        out[iso3] = {
            "current_phase": cur["phase"] if cur else None,
            "current_phase_label": _phase_label(cur["phase"]) if cur else None,
            "current_period": cur["period_label"] if cur else None,
            "projected_phase": proj["phase"] if proj else None,
            "projected_phase_label": _phase_label(proj["phase"]) if proj else None,
            "projected_period": proj["period_label"] if proj else None,
            "projected2_phase": proj2["phase"] if proj2 else None,
            "projected2_phase_label": _phase_label(proj2["phase"]) if proj2 else None,
            "projected2_period": proj2["period_label"] if proj2 else None,
            "delta": delta,
            "report_url": f"https://fews.net/{slug}" if slug else "https://fews.net/",
            "as_of": today,
        }

    write_json(
        "fews.json",
        out,
        source="FEWS NET FDW · IPC-style phase classifications (fdw.fews.net/api/ipcphase/)",
        notes=(
            f"Country-level worst-phase summary across admin-2 units, with current + "
            f"3-month projection + 8-month projection where available. "
            f"Covered {len(out)} of {len(FEWS_COUNTRY_SLUGS)} FEWS countries. "
            f"Phases: 1=Minimal, 2=Stressed, 3=Crisis, 4=Emergency, 5=Famine."
        ),
    )


if __name__ == "__main__":
    main()
