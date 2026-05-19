"""
ReliefWeb — humanitarian situation reports tagged 'Food and Nutrition'.

API v2 (v1 was decommissioned). v2 requires an *approved* appname (not a key — just
register your appname with ReliefWeb so they can rate-limit per-app).

To register: https://apidoc.reliefweb.int/parameters#appname
  Email apidoc@reliefweb.int requesting an appname for your tool.
  Until approved, this script writes an empty stub and the frontend degrades gracefully.

Override via env var: RELIEFWEB_APPNAME=your-approved-name

Output: data/reliefweb_alerts.json
  {
    "events": [
      {"title": ..., "iso3": ..., "country": ..., "date": ..., "url": ..., "severity": ...}
    ]
  }
"""
from _common import env, http_get, write_json

URL = "https://api.reliefweb.int/v2/reports"
DEFAULT_APPNAME = "foodshield-ai-fv"  # Replace with your approved appname


def main():
    appname = env("RELIEFWEB_APPNAME", DEFAULT_APPNAME)
    params = [
        ("appname", appname),
        ("limit", 50),
        ("sort[]", "date:desc"),
        ("filter[field]", "theme.name"),
        ("filter[value]", "Food and Nutrition"),
        ("fields[include][]", "title"),
        ("fields[include][]", "date.original"),
        ("fields[include][]", "country.iso3"),
        ("fields[include][]", "country.name"),
        ("fields[include][]", "url_alias"),
        ("fields[include][]", "primary_country.iso3"),
        ("fields[include][]", "primary_country.name"),
    ]

    try:
        r = http_get(URL, params=params, timeout=45)
        data = r.json().get("data", [])
    except Exception as e:
        print(f"  ReliefWeb v2 fetch failed: {e}")
        print(f"  This is usually because the appname '{appname}' is not yet approved by ReliefWeb.")
        print(f"  Email apidoc@reliefweb.int to register, or set RELIEFWEB_APPNAME secret with your approved name.")
        write_json(
            "reliefweb_alerts.json",
            {"events": [], "_status": f"appname '{appname}' not approved — see comment in script header"},
            source="ReliefWeb (api v2) — appname registration pending",
            notes="No data yet. Register your appname per https://apidoc.reliefweb.int/parameters#appname",
        )
        return

    events = []
    for item in data:
        f = item.get("fields", {})
        iso3 = None
        if f.get("primary_country"):
            iso3 = f["primary_country"].get("iso3")
        elif f.get("country"):
            iso3 = (f["country"][0] or {}).get("iso3")
        date_field = f.get("date")
        date = date_field.get("original") if isinstance(date_field, dict) else None
        events.append({
            "title": f.get("title", ""),
            "iso3": (iso3 or "").upper() or None,
            "country": (f.get("primary_country") or {}).get("name"),
            "date": date,
            "url": f.get("url_alias"),
            "severity": _severity(f.get("title", "")),
        })

    write_json(
        "reliefweb_alerts.json",
        {"events": events},
        source=f"ReliefWeb v2 (api.reliefweb.int) — appname={appname} — theme: Food and Nutrition",
    )


def _severity(title):
    t = (title or "").lower()
    if any(w in t for w in ["famine", "ipc 5", "catastrophe", "starvation"]):
        return "high"
    if any(w in t for w in ["crisis", "emergency", "ipc 4", "acute"]):
        return "med"
    return "low"


if __name__ == "__main__":
    main()
