"""
ACLED (Armed Conflict Location & Event Data) — conflict events.

v23 — REWRITTEN for ACLED's new OAuth API (the old key+email query-param auth on
api.acleddata.com was retired). The new flow:
  1. POST acleddata.com/oauth/token with username/password/grant_type=password/
     client_id=acled/scope=authenticated  → Bearer access_token (valid 24h).
  2. GET  acleddata.com/api/acled/read with header Authorization: Bearer <token>.

REQUIRES (free myACLED account at https://acleddata.com/register/). Set GitHub
Actions secrets:
  ACLED_EMAIL       (the email you registered with)
  ACLED_PASSWORD    (your myACLED password)
  (legacy ACLED_API_KEY is no longer used; kept tolerated for back-compat.)

Output: data/acled.json  (schema UNCHANGED so downstream code is unaffected)
  {
    iso3: {
      "events_last_30d": <count>,
      "events_last_90d": <count>,
      "fatalities_last_30d": <count>,
      "intensity_score": <0-100 normalized>,
    }
  }
"""
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from _common import env, write_json, UA

# ACLED's new API returns `iso` as a NUMERIC M49 code (e.g. 268 = Georgia), not
# ISO3. Reuse the canonical M49→ISO3 map from the trade pipeline so we key our
# output by ISO3 like the rest of FoodShield. Fall back to a tiny inline map.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "trade_pipeline"))
    from config import M49_TO_ISO3 as _M49_TO_ISO3
except Exception:
    _M49_TO_ISO3 = {}

TOKEN_URL = "https://acleddata.com/oauth/token"
READ_URL = "https://acleddata.com/api/acled/read"


def _get_access_token(email, password):
    """OAuth password-grant → 24h Bearer access token."""
    print(f"[acled] requesting token for {email} at {TOKEN_URL} ...")
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": UA},
        data={
            "username": email,
            "password": password,
            "grant_type": "password",
            "client_id": "acled",
            "scope": "authenticated",
        },
        timeout=60,
    )
    # Loud diagnostics — print the actual HTTP status + body so a failed login
    # tells us WHY (bad password, account not yet approved, wrong client_id, etc.)
    print(f"[acled] token HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(f"[acled] token response body: {resp.text[:400]}")
        resp.raise_for_status()
    tok = resp.json().get("access_token")
    if not tok:
        raise RuntimeError(f"ACLED token response missing access_token: {resp.text[:200]}")
    return tok


def main():
    email = env("ACLED_EMAIL", required=False)
    password = env("ACLED_PASSWORD", required=False)
    if not email or not password:
        write_json(
            "acled.json", {}, source="ACLED (not configured)",
            notes=("Set ACLED_EMAIL + ACLED_PASSWORD secrets (free myACLED account) "
                   "to enable. v23: ACLED moved to OAuth; the old ACLED_API_KEY is "
                   "no longer used."),
        )
        return

    try:
        token = _get_access_token(email, password)
    except Exception as e:
        write_json("acled.json", {}, source="ACLED (auth failed)",
                   notes=f"OAuth token request failed: {e}")
        return

    now = datetime.now(timezone.utc).date()
    # ACCESS-TIER NOTE (v23): this myACLED tier is restricted to data ≥12 months
    # old (data_query_restrictions.date_recency = "12 Months"). So a live last-90-day
    # query returns 0 rows. We instead pull the most-recent ALLOWED 12-month window
    # (ending at the 12-month cutoff) and treat ACLED as a STRUCTURAL conflict
    # baseline, not a live nowcast signal. If the account is later upgraded to live
    # access, set ACLED_LIVE=1 to query the last 90 days instead.
    live = bool(env("ACLED_LIVE", required=False))
    if live:
        window_end = now
        window_start = now - timedelta(days=90)
        window_days = 90
    else:
        window_end = now - timedelta(days=365)            # the 12-month cutoff
        window_start = window_end - timedelta(days=365)   # a full year before it
        window_days = 365
    since = window_start.isoformat()
    until = window_end.isoformat()
    print(f"[acled] window {since} → {until} ({'LIVE' if live else 'structural, 12m-lagged tier'})")

    # Paginate to be safe (default row cap is 5000; pagination calls don't count
    # toward rate limits). page=1.. until a short page is returned.
    headers = {"Authorization": f"Bearer {token}", "User-Agent": UA}
    rows = []
    page = 1
    while True:
        try:
            r = requests.get(
                READ_URL,
                params={
                    "_format": "json",
                    "event_date": f"{since}|{until}",
                    "event_date_where": "BETWEEN",
                    "fields": "iso|event_date|fatalities|event_type|country",
                    "limit": 5000,
                    "page": page,
                },
                headers=headers, timeout=90,
            )
            r.raise_for_status()
        except Exception as e:
            print(f"  [warn] ACLED page {page} failed: {e}")
            break
        j = r.json() or {}
        if page == 1:
            # Diagnostics — show what the API actually returned so a 0-row result
            # is debuggable (wrong field name, status message, count, etc.).
            print(f"  [acled] read HTTP {r.status_code} · url={r.url}")
            print(f"  [acled] count={j.get('count')} total_count={j.get('total_count')}")
            print(f"  [acled] messages={j.get('messages')}")
            print(f"  [acled] data_query_restrictions={j.get('data_query_restrictions')}")
        batch = j.get("data", []) or []
        rows.extend(batch)
        if len(batch) < 5000:
            break
        page += 1
        if page > 40:   # safety stop (~200k events)
            break
    print(f"  fetched {len(rows)} ACLED events across {page} page(s)")

    # Aggregate events + fatalities per country across the fetched window.
    events_total, fatalities_total = Counter(), Counter()
    for row in rows:
        # New API `iso` is a NUMERIC M49 code; older exports used alpha iso3.
        # Resolve numeric → ISO3 via the canonical map; accept a 3-letter code as-is.
        raw = str(row.get("iso3") or row.get("iso") or "").strip().upper()
        if raw.isdigit():
            iso3 = _M49_TO_ISO3.get(int(raw))
        elif raw.isalpha() and len(raw) == 3:
            iso3 = raw
        else:
            iso3 = None
        if not iso3:
            continue
        events_total[iso3] += 1
        try:
            fatalities_total[iso3] += int(row.get("fatalities") or 0)
        except (ValueError, TypeError):
            pass

    # Per-country output. The `mode` flag is the honesty switch: on a 12-month-lagged
    # tier ACLED is STRUCTURAL (a conflict baseline for the FDRS conflict component),
    # NOT live — so the frontend must NOT show it in the Live Disturbances feed.
    mode = "live" if live else "structural"
    # Normalise yearly event count to 0-100 (≥6000 events/yr ≈ 100, i.e. ~500/mo).
    annual_cap = 6000 if not live else 1500   # live window is 90d, so a lower cap
    out = {}
    for iso3 in events_total:
        ev = events_total[iso3]
        out[iso3] = {
            "events_window": ev,
            "fatalities_window": fatalities_total[iso3],
            "window_start": since,
            "window_end": until,
            "window_days": window_days,
            "intensity_score": min(100, round((ev / annual_cap) * 100, 1)),
            "mode": mode,                 # 'structural' (lagged) or 'live'
            "is_live": live,              # frontend gate for the Live Disturbances feed
        }

    write_json(
        "acled.json", out,
        source="ACLED (acleddata.com/api, OAuth)",
        notes=(
            (f"LIVE last-90-day conflict events normalized to 0-100. "
             if live else
             f"STRUCTURAL conflict baseline — this myACLED tier is restricted to data "
             f"≥12 months old, so events are a 12-month window ending {until} (NOT live). "
             f"Feeds the FDRS conflict/logistics component as a structural signal only; "
             f"is_live=false so the frontend must exclude it from the Live Disturbances feed. ")
            + f"Covered {len(out)} countries from {len(rows)} events "
              f"({since}→{until}). Intensity = events ÷ {annual_cap} cap × 100."
        ),
    )
    print(f"[acled] wrote {len(out)} countries · mode={mode} · is_live={live}")


if __name__ == "__main__":
    main()
