"""
FEWS NET — IPC-style acute food insecurity classifications.

FEWS NET (Famine Early Warning Systems Network) is the USAID-funded humanitarian
forecasting system. It publishes IPC-compatible Phase 1–5 classifications
for ~35 crisis countries with current + 3-month projected (ML1) + 6-month
projected (ML2) periods. The Food Data Warehouse (FDW) is the REST API at
fdw.fews.net.

References:
  - https://help.fews.net/fdw/fews-net-api
  - https://help.fews.net/fdw/api-authentication
  - https://fdw.fews.net/api/  (DRF-style browsable API root)

Authentication — IMPORTANT:
  Per the FDW docs: "Requests without any authentication may succeed but will
  only return public data." Full access requires an FDW partner-level account.
  There is no self-service signup — accounts are granted by the FEWS NET Help
  Desk (https://fewsnet.atlassian.net/servicedesk/customer/portal/2/group/-1).

  This script supports BOTH modes:
    - PUBLIC mode (no token): try first. If FEWS returns IPC-style records for
      public data, we use them. This is the recommended path because it works
      without partner credentials.
    - TOKEN mode: if FDW_USERNAME and FDW_PASSWORD are set in environment, we
      POST to /api-token-auth/ to obtain a 12-hour JWT and authenticate every
      request. Use this if your organization has FDW credentials.

  Either way, if no data comes back the script writes an empty stub with a
  diagnostic note so the source manifest stays honest.

Output: data/fews.json
  {
    iso3: {
      "current_phase":     <int 1..5>,         # worst phase observed across the country
      "current_phase_label": "Crisis (3)",
      "current_period":    "<label>",          # e.g. "Apr–May 2026"
      "projected_phase":   <int 1..5>,         # 3-month projection (ML1)
      "projected_phase_label": "Crisis (3)",
      "projected_period":  "<label>",          # e.g. "Jun–Sep 2026"
      "projected2_phase":  <int 1..5 | null>,  # 6-month projection (ML2)
      "projected2_phase_label": "Emergency (4)",
      "projected2_period": "<label>",
      "delta":             <int>,              # projected − current
      "report_url":        "<deep-link>",      # country page on fews.net
      "as_of":             "<YYYY-MM-DD>",
    }
  }

Notes:
  - Country-level summary is the worst phase observed across any admin unit
    that period. This matches how IPC headlines a country (e.g. "Yemen Phase 4
    in northern governorates"). The per-area geometry detail is NOT flattened
    here — that's a separate per-area dataset we don't need for FDRS.
  - FEWS NET only covers ~35 countries by design. Countries absent from FEWS
    are absent from this output — that's correct, not a bug.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date

import requests  # used directly for the token POST + status-code inspection

from _common import write_json, env, UA, DEFAULT_TIMEOUT


BASE = "https://fdw.fews.net/api"
TOKEN_URL = "https://fdw.fews.net/api-token-auth/"

# v22.18 — Countries FEWS NET tracks for IPC-style outlook reports.
# ISO3 (for our internal keying) → (ISO2 used by FDW country_code filter, slug
# used in https://fews.net/<slug> country page URLs).
# Curated from FEWS NET's public country list.
FEWS_COUNTRIES = {
    "AFG": ("AF", "afghanistan"),
    "BFA": ("BF", "burkina-faso"),
    "BDI": ("BI", "burundi"),
    "CAF": ("CF", "central-african-republic"),
    "TCD": ("TD", "chad"),
    "COD": ("CD", "democratic-republic-congo"),
    "ETH": ("ET", "ethiopia"),
    "GTM": ("GT", "guatemala"),
    "HTI": ("HT", "haiti"),
    "HND": ("HN", "honduras"),
    "KEN": ("KE", "kenya"),
    "LBR": ("LR", "liberia"),
    "MDG": ("MG", "madagascar"),
    "MWI": ("MW", "malawi"),
    "MLI": ("ML", "mali"),
    "MRT": ("MR", "mauritania"),
    "MOZ": ("MZ", "mozambique"),
    "MMR": ("MM", "myanmar-burma"),
    "NER": ("NE", "niger"),
    "NGA": ("NG", "nigeria"),
    "PSE": ("PS", "west-bank-and-gaza"),
    "PAK": ("PK", "pakistan"),
    "RWA": ("RW", "rwanda"),
    "SEN": ("SN", "senegal"),
    "SLE": ("SL", "sierra-leone"),
    "SOM": ("SO", "somalia"),
    "SSD": ("SS", "south-sudan"),
    "SDN": ("SD", "sudan"),
    "SYR": ("SY", "syria"),
    "TJK": ("TJ", "tajikistan"),
    "TZA": ("TZ", "tanzania"),
    "UGA": ("UG", "uganda"),
    "YEM": ("YE", "yemen"),
    "ZMB": ("ZM", "zambia"),
    "ZWE": ("ZW", "zimbabwe"),
}

# FDW scenario codes per the API docs:
#   CS  = Current Situation
#   ML1 = Near Term projection (1-3 months)
#   ML2 = Medium Term projection (4-6 months)
SCENARIOS = ("CS", "ML1", "ML2")


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


def _try_get_token():
    """Attempt to obtain a JWT from FDW using FDW_USERNAME + FDW_PASSWORD.

    Returns the token string on success, or None if creds are missing /
    the exchange fails. Logs the outcome to stdout so the workflow surfaces
    the auth state.
    """
    user = env("FDW_USERNAME", required=False)
    pw = env("FDW_PASSWORD", required=False)
    if not user or not pw:
        print("[FEWS] no FDW_USERNAME/FDW_PASSWORD — will try unauthenticated public access")
        return None
    print(f"[FEWS] attempting token auth as {user}")
    try:
        r = requests.post(
            TOKEN_URL,
            data={"username": user, "password": pw},
            headers={"User-Agent": UA},
            timeout=15,
        )
    except Exception as e:
        print(f"[FEWS] token POST failed: {e} — falling back to unauthenticated")
        return None
    if r.status_code != 200:
        print(f"[FEWS] token POST returned {r.status_code} — falling back to unauthenticated")
        return None
    try:
        tok = r.json().get("token")
    except Exception:
        tok = None
    if not tok:
        print("[FEWS] token POST returned 200 but no token field — falling back")
        return None
    print(f"[FEWS] got JWT token (length={len(tok)})")
    return tok


def _fetch_ipcphase(token=None):
    """Pull IPC-style phase classifications for the FEWS country list.

    For each country × scenario (CS, ML1, ML2), GET /api/ipcphase.json?country_code=XX&scenario=YYY.

    Returns a flat list of row-dicts. Each row carries _iso3 and _scenario
    so we can group downstream. If the endpoint returns 401/403 we log and
    abort (no data accessible at this credential level). If it returns 200
    with empty results we still log and continue — that's the answer.
    """
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
    }
    if token:
        # The FEWS Python example sends 'JWT <token>' rather than 'Token <token>'.
        # We honour both: send JWT header (canonical) AND ?jwt= query (covers
        # tools that strip custom headers). The latter is added per-request.
        headers["Authorization"] = f"JWT {token}"

    out = []
    status_codes = []  # collect for diagnostic logging
    total_results = 0
    sample_iso = None
    for iso3, (iso2, slug) in FEWS_COUNTRIES.items():
        for scen in SCENARIOS:
            params = {"country_code": iso2, "scenario": scen, "page_size": 500}
            if token:
                params["jwt"] = token  # belt + braces
            try:
                r = requests.get(
                    f"{BASE}/ipcphase.json",
                    params=params,
                    headers=headers,
                    timeout=20,
                )
            except Exception as e:
                print(f"  [warn] FEWS {iso3}/{scen} fetch failed: {e}")
                continue
            status_codes.append(r.status_code)
            if r.status_code in (401, 403):
                # Authentication required for this country / dataset
                continue
            if r.status_code != 200:
                print(f"  [warn] FEWS {iso3}/{scen}: HTTP {r.status_code}")
                continue
            try:
                body = r.json() or {}
            except Exception as e:
                print(f"  [warn] FEWS {iso3}/{scen}: non-JSON response ({e})")
                continue
            rows = body.get("results") if isinstance(body, dict) else (body if isinstance(body, list) else [])
            total_results += len(rows)
            if rows and sample_iso is None:
                sample_iso = iso3
                # Log one row shape on first hit so the workflow log shows what FDW returned
                print(f"  [FEWS sample] {iso3}/{scen} returned {len(rows)} rows; first row keys: "
                      f"{sorted(list(rows[0].keys()))[:14]}")
            for row in rows:
                row["_iso3"] = iso3
                row["_slug"] = slug
                row["_scenario"] = scen
                out.append(row)

    # Summary logging — print HTTP-status distribution + total results
    if status_codes:
        from collections import Counter
        c = Counter(status_codes)
        print(f"[FEWS] HTTP status distribution: {dict(c)}")
    print(f"[FEWS] total rows fetched: {total_results} across {len(FEWS_COUNTRIES)} countries × {len(SCENARIOS)} scenarios")
    return out


def _summarize_country(rows):
    """Reduce per-area rows to one row per scenario, taking the worst phase.

    FEWS publishes a phase per admin-2 unit. The country-level headline is the
    worst classification observed in any unit that period. Returns a dict like:
      { 'current': (phase, period_label),
        'projected': (...), 'projected2': (...) }

    Scenarios:
      CS  → 'current'
      ML1 → 'projected'  (1-3 months)
      ML2 → 'projected2' (4-6 months)
    Phase field is named 'ipc_phase' in FDW responses. Date fields may be
    'period_date', 'start_date'+'end_date', or 'period_date_start'+'period_date_end'.
    We try them in order.
    """
    scenario_to_period = {"CS": "current", "ML1": "projected", "ML2": "projected2"}
    by_type = defaultdict(list)
    for r in rows:
        scen = r.get("_scenario") or (r.get("scenario") or "").upper()
        ptype = scenario_to_period.get(scen)
        if not ptype:
            continue
        phase = r.get("ipc_phase") or r.get("value")
        try:
            phase = int(phase)
        except (TypeError, ValueError):
            continue
        if phase < 1 or phase > 5:
            continue
        start = r.get("start_date") or r.get("period_date_start") or r.get("period_date")
        end = r.get("end_date") or r.get("period_date_end") or r.get("period_date")
        by_type[ptype].append({"phase": phase, "start": start, "end": end})
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
    # v22.19 — Option B: try unauthenticated first. Only attempt token auth if
    # FDW_USERNAME + FDW_PASSWORD are explicitly set (i.e. you have a partner
    # account). FEWS_API_TOKEN is no longer used — FDW issues short-lived JWTs
    # via username/password POST, not long-lived issued tokens.
    token = _try_get_token()
    auth_mode = "token-auth" if token else "public/unauthenticated"
    print(f"[FEWS] auth mode: {auth_mode}")

    raw = _fetch_ipcphase(token=token)

    by_iso = defaultdict(list)
    for r in raw:
        by_iso[r["_iso3"]].append(r)

    out = {}
    today = date.today().isoformat()
    for iso3, rows in by_iso.items():
        _, slug = FEWS_COUNTRIES.get(iso3, ("", ""))
        summary = _summarize_country(rows)
        cur = summary.get("current")
        proj = summary.get("projected")
        proj2 = summary.get("projected2")
        if not cur and not proj:
            continue
        delta = (proj["phase"] - cur["phase"]) if (cur and proj) else None
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

    if out:
        write_json(
            "fews.json",
            out,
            source="FEWS NET FDW · IPC-style phase classifications (fdw.fews.net/api/ipcphase/)",
            notes=(
                f"auth_mode={auth_mode}. Country-level worst-phase summary across "
                f"admin-2 units, with current (CS) + 3-month (ML1) + 6-month (ML2) "
                f"projections where available. Covered {len(out)} of {len(FEWS_COUNTRIES)} "
                f"FEWS countries. Phases: 1=Minimal, 2=Stressed, 3=Crisis, "
                f"4=Emergency, 5=Famine."
            ),
        )
    else:
        # No rows came back. Either the public API only exposes metadata (no
        # actual phase classifications) or auth was rejected. Write an honest
        # stub with diagnostic info so the source manifest shows the real state.
        # Note: token list "api key needed" and "setup required" match the
        # build_source_manifest infer_status() token patterns and flip the
        # manifest entry from 'degraded' to 'setup_required', which is the
        # truthful state when FDW requires partner credentials we don't have.
        write_json(
            "fews.json",
            {},
            source="FEWS NET FDW (fdw.fews.net/api)",
            notes=(
                f"setup required — api key needed. No phase classifications returned "
                f"(auth_mode={auth_mode}). FEWS NET FDW typically requires "
                f"partner-level credentials for IPC-style data. To enable, request an "
                f"account from the FEWS NET Help Desk "
                f"(https://fewsnet.atlassian.net/servicedesk/customer/portal/2/group/-1) "
                f"and set FDW_USERNAME + FDW_PASSWORD in GitHub repo secrets."
            ),
        )


if __name__ == "__main__":
    main()
