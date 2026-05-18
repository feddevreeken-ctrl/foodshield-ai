"""
ACLED (Armed Conflict Location & Event Data) — conflict events.

REQUIRES API KEY: register at https://developer.acleddata.com (free for non-commercial).
Set these GitHub Actions secrets:
  ACLED_API_KEY    (your API key)
  ACLED_EMAIL      (the email you registered with)

Output: data/acled.json
  {
    iso3: {
      "events_last_30d": <count>,
      "events_last_90d": <count>,
      "fatalities_last_30d": <count>,
      "intensity_score": <0-100 normalized>,
    }
  }
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from _common import env, http_get, write_json

URL = "https://api.acleddata.com/acled/read"


def main():
    key = env("ACLED_API_KEY", required=True)
    email = env("ACLED_EMAIL", required=True)
    if not key or not email:
        # Write empty stub so frontend can still load
        write_json("acled.json", {}, source="ACLED (not configured)", notes="Set ACLED_API_KEY + ACLED_EMAIL secrets to enable.")
        return

    now = datetime.now(timezone.utc).date()
    since_90 = (now - timedelta(days=90)).isoformat()
    r = http_get(URL, params={
        "key": key,
        "email": email,
        "event_date": f"{since_90}|{now.isoformat()}",
        "event_date_where": "BETWEEN",
        "fields": "iso3|event_date|fatalities|event_type",
        "limit": 100000,
    }, timeout=60)
    payload = r.json()
    rows = payload.get("data", [])
    print(f"  fetched {len(rows)} ACLED events")

    cutoff_30 = (now - timedelta(days=30)).isoformat()
    events_30 = Counter()
    events_90 = Counter()
    fatalities_30 = Counter()
    for row in rows:
        iso3 = (row.get("iso3") or "").upper()
        if not iso3:
            continue
        date = row.get("event_date", "")
        events_90[iso3] += 1
        if date >= cutoff_30:
            events_30[iso3] += 1
            try:
                fatalities_30[iso3] += int(row.get("fatalities") or 0)
            except ValueError:
                pass

    # Normalize 30-day events to 0-100 intensity (cap at 500 events/month -> 100)
    out = {}
    all_iso = set(events_30) | set(events_90)
    for iso3 in all_iso:
        e30 = events_30[iso3]
        intensity = min(100, round((e30 / 500) * 100, 1))
        out[iso3] = {
            "events_last_30d": e30,
            "events_last_90d": events_90[iso3],
            "fatalities_last_30d": fatalities_30[iso3],
            "intensity_score": intensity,
        }

    write_json(
        "acled.json",
        out,
        source="ACLED (api.acleddata.com)",
        notes="Last-30-day event count normalized to 0-100 (500 events/mo = 100).",
    )


if __name__ == "__main__":
    main()
