"""
ReliefWeb — humanitarian situation reports tagged 'food security'.

Public API, no key required. Pulls last 50 reports for the nowcast feed.

Output: data/reliefweb_alerts.json
  {
    "events": [
      {"title": ..., "country": ..., "iso3": ..., "date": ..., "url": ..., "severity": "high|med|low"}
    ]
  }
"""
from _common import http_get, write_json

URL = "https://api.reliefweb.int/v1/reports"
PARAMS = {
    "appname": "foodshield-ai",
    "limit": 50,
    "sort[]": "date:desc",
    "filter[field]": "theme.name",
    "filter[value]": "Food and Nutrition",
    "fields[include][]": [
        "title", "date.original", "country.iso3", "country.name",
        "url_alias", "primary_country.iso3", "primary_country.name",
        "disaster.name",
    ],
}


def main():
    # requests needs params expanded for repeated keys; build a flat list of tuples
    flat = []
    for k, v in PARAMS.items():
        if isinstance(v, list):
            for item in v:
                flat.append((k, item))
        else:
            flat.append((k, v))

    r = http_get(URL, params=flat, timeout=45)
    data = r.json().get("data", [])
    events = []
    for item in data:
        f = item.get("fields", {})
        iso3 = None
        if f.get("primary_country"):
            iso3 = f["primary_country"].get("iso3")
        elif f.get("country"):
            iso3 = (f["country"][0] or {}).get("iso3")
        date = (f.get("date") or {}).get("original") if isinstance(f.get("date"), dict) else None
        title = f.get("title", "")
        url = f.get("url_alias")
        sev = _severity(title)
        events.append({
            "title": title,
            "iso3": iso3,
            "country": (f.get("primary_country") or {}).get("name"),
            "date": date,
            "url": url,
            "severity": sev,
        })

    write_json(
        "reliefweb_alerts.json",
        {"events": events},
        source="ReliefWeb (api.reliefweb.int) — theme: Food and Nutrition",
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
