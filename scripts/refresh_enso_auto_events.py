#!/usr/bin/env python3
"""El Niño map marks picked by rule from the Reported feeds (no network).

The Ocean map's 7-day and 30-day stops show hand-checked headlines from
data/enso_recent_events.json. That list only changes when a person edits it, so
the 7-day stop went stale between edits. This step reads the three automated
Reported feeds that run_all.py has just refreshed and writes the hazard reports
of the last 45 days that fit El Niño to data/enso_auto_events.json:

  * data/gdacs.json            GDACS drought, flood and tropical cyclone events
  * data/reliefweb_alerts.json ReliefWeb food and nutrition reports (titles)
  * data/enso_news.json        headlines that name El Niño

An item is kept only when it is a drought, flood, tropical cyclone, wildfire or
heat report, it touches the last 45 days, and either
  (a) El Niño conditions hold (ONI >= 0.5 in data/enso.json) and the hazard hits
      a country and months where El Niño is known to bring it (PATTERNS below,
      each row with its published reference), enso_link "consistent"; or
  (b) its headline names El Niño, enso_link "named". A headline that names
      El Niño is not proof that El Niño caused the event: the page must say so.
Headlines about what may come (risk, threat, could, prepare, forecast...) are
dropped: the marks are for things that happened.

Nothing here is invented: titles are the feed's words, dates and positions are
the feed's (a headline without coordinates sits at its country's capital, from
_common.COUNTRY_COORDS, and its place says so), and "checked" is always false.
Countries already covered by a hand-checked event of the same hazard with
overlapping dates are dropped. Items from earlier runs are kept while they still
touch the 45 days, so a feed that rolls an item off does not blank the map.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import COUNTRY_COORDS, DATA_DIR, stamp_inputs, write_json  # noqa: E402
from _news_gazetteer import COUNTRY_NAMES  # noqa: E402

OUT = "enso_auto_events.json"
LOOKBACK = 45          # days
NEWS_SPAN = 7          # a headline without event dates covers the 7 days up to its publication
ONI_MIN = 0.5          # El Niño conditions, NOAA CPC's threshold

# Where and when El Niño is known to bring each hazard. Months are calendar months of the El Niño
# year (1 = January). "box" limits a large country to the part the pattern covers (lat_s, lat_n,
# lon_w, lon_e; lon_w > lon_e crosses the dateline) and is tested on the event's own position.
REFS = {
    "RH87": ("Ropelewski and Halpert 1987, Global and regional scale precipitation patterns associated "
             "with the El Nino/Southern Oscillation, Monthly Weather Review 115", "https://doi.org/10.1175/1520-0493(1987)115<1606:GARSPP>2.0.CO;2"),
    "MG01": ("Mason and Goddard 2001, Probabilistic precipitation anomalies associated with ENSO, "
             "Bulletin of the American Meteorological Society 82 (IRI)", "https://doi.org/10.1175/1520-0477(2001)082<0619:PPAAWE>2.3.CO;2"),
    "D14": ("Davey, Brookshaw and Ineson 2014, The probability of the impact of ENSO on precipitation and "
            "near-surface temperature, Climate Risk Management 1", "https://doi.org/10.1016/j.crm.2013.12.002"),
    "F16": ("Field and others 2016, Indonesian fire activity and smoke pollution in 2015 show persistent "
            "nonlinear sensitivity to El Nino-induced drought, PNAS 113", "https://doi.org/10.1073/pnas.1524888113"),
    "BOM": ("Bureau of Meteorology, Australian climate influences: El Nino", "http://www.bom.gov.au/climate/about/australian-climate-influences.shtml?bookmark=enso"),
    "PCCSP": ("Australian Bureau of Meteorology and CSIRO 2011, Climate Change in the Pacific: Scientific "
              "Assessment and New Research, Volume 1", "https://www.pacificclimatechangescience.org/"),
    "K06": ("Kumar and others 2006, Unraveling the mystery of Indian monsoon failure during El Nino, "
            "Science 314", "https://doi.org/10.1126/science.1131152"),
    "KB07": ("Korecha and Barnston 2007, Predictability of June-September rainfall in Ethiopia, Monthly "
             "Weather Review 135", "https://doi.org/10.1175/MWR3304.1"),
    "I00": ("Indeje, Semazzi and Ogallo 2000, ENSO signals in East African rainfall seasons, International "
            "Journal of Climatology 20", "https://doi.org/10.1002/(SICI)1097-0088(200001)20:1<19::AID-JOC449>3.0.CO;2-0"),
    "G00": ("Giannini, Kushnir and Cane 2000, Interannual variability of Caribbean rainfall, ENSO, and the "
            "Atlantic Ocean, Journal of Climate 13", "https://doi.org/10.1175/1520-0442(2000)013<0297:IVOCRE>2.0.CO;2"),
    "P01": ("Poveda and others 2001, Seasonality in ENSO-related precipitation, river discharges, soil "
            "moisture, and vegetation index in Colombia, Water Resources Research 37", "https://doi.org/10.1029/2000WR900395"),
    "GBD00": ("Grimm, Barros and Doyle 2000, Climate variability in southern South America associated with "
              "El Nino and La Nina events, Journal of Climate 13", "https://doi.org/10.1175/1520-0442(2000)013<0035:CVISSA>2.0.CO;2"),
    "RH86": ("Ropelewski and Halpert 1986, North American precipitation and temperature patterns associated "
             "with the El Nino/Southern Oscillation, Monthly Weather Review 114", "https://doi.org/10.1175/1520-0493(1986)114<2352:NAPATP>2.0.CO;2"),
    "CW97": ("Chu and Wang 1997, Tropical cyclone occurrences in the vicinity of Hawaii, Journal of Climate 10",
             "https://doi.org/10.1175/1520-0442(1997)010<2683:TCOITV>2.0.CO;2"),
    "NOAA14": ("NOAA Climate.gov ENSO blog 2014, Impacts of El Nino and La Nina on the hurricane season",
               "https://www.climate.gov/news-features/blogs/enso/impacts-el-ni%C3%B1o-and-la-ni%C3%B1a-hurricane-season"),
}
SHORT = {"RH87": "Ropelewski and Halpert 1987", "MG01": "Mason and Goddard 2001", "D14": "Davey and others 2014",
         "F16": "Field and others 2016", "BOM": "Bureau of Meteorology", "PCCSP": "Bureau of Meteorology and CSIRO 2011",
         "K06": "Kumar and others 2006", "KB07": "Korecha and Barnston 2007", "I00": "Indeje and others 2000",
         "G00": "Giannini and others 2000", "P01": "Poveda and others 2001", "GBD00": "Grimm and others 2000",
         "RH86": "Ropelewski and Halpert 1986", "CW97": "Chu and Wang 1997", "NOAA14": "NOAA Climate.gov 2014"}
R = range
PATTERNS = [
    {"id": "maritime_continent_dry", "hazards": {"drought", "heat"}, "iso3": {"IDN", "MYS", "BRN", "TLS", "PNG"},
     "months": set(R(6, 12)), "label": "Indonesia, Malaysia, Timor-Leste and Papua New Guinea", "refs": ["RH87", "D14"]},
    {"id": "maritime_continent_fire", "hazards": {"wildfire"}, "iso3": {"IDN", "MYS", "BRN"},
     "months": set(R(8, 12)), "label": "Indonesia and Malaysia", "refs": ["F16"]},
    {"id": "philippines_dry", "hazards": {"drought", "heat"}, "iso3": {"PHL"},
     "months": {10, 11, 12, 1, 2, 3, 4}, "label": "the Philippines", "refs": ["RH87", "MG01"]},
    {"id": "eastern_australia_dry", "hazards": {"drought", "heat", "wildfire"}, "iso3": {"AUS"},
     "box": {"AUS": (-45, -10, 135, 155)}, "months": set(R(6, 13)),
     "label": "eastern and south-eastern Australia", "refs": ["BOM", "RH87"]},
    {"id": "southwest_pacific_dry", "hazards": {"drought"}, "iso3": {"SLB", "VUT", "FJI", "TON", "NCL"},
     "months": set(R(7, 13)) | {1, 2, 3}, "label": "the south-west Pacific islands", "refs": ["PCCSP"]},
    {"id": "central_pacific_wet", "hazards": {"flood"}, "iso3": {"KIR", "TUV", "NRU"},
     "months": set(R(1, 13)), "label": "the central Pacific islands", "refs": ["PCCSP"]},
    {"id": "india_monsoon_dry", "hazards": {"drought", "heat"}, "iso3": {"IND"},
     "months": set(R(6, 10)), "label": "India", "refs": ["K06", "RH87"]},
    {"id": "ethiopia_sudan_jjas_dry", "hazards": {"drought"}, "iso3": {"ETH", "ERI", "SDN", "SSD"},
     "months": set(R(6, 10)), "label": "Ethiopia, Eritrea, Sudan and South Sudan", "refs": ["KB07"]},
    {"id": "east_africa_short_rains_wet", "hazards": {"flood"}, "iso3": {"KEN", "SOM", "TZA", "UGA", "ETH"},
     "box": {"ETH": (3, 9, 38, 48), "TZA": (-7, -1, 29, 41)}, "months": {10, 11, 12},
     "label": "East Africa", "refs": ["I00", "MG01"]},
    {"id": "southern_africa_dry", "hazards": {"drought", "heat"},
     "iso3": {"ZAF", "ZWE", "ZMB", "MWI", "MOZ", "BWA", "NAM", "LSO", "SWZ", "MDG"},
     "months": {11, 12, 1, 2, 3}, "label": "southern Africa", "refs": ["RH87", "MG01"]},
    {"id": "central_america_caribbean_dry", "hazards": {"drought", "heat"},
     "iso3": {"GTM", "HND", "SLV", "NIC", "CRI", "PAN", "BLZ", "MEX", "CUB", "DOM", "HTI", "JAM", "BHS", "PRI"},
     "box": {"MEX": (5, 22, -120, -85)}, "months": set(R(6, 11)),
     "label": "Central America, southern Mexico and the Caribbean", "refs": ["G00", "RH87"]},
    {"id": "northern_south_america_dry", "hazards": {"drought", "wildfire", "heat"},
     "iso3": {"COL", "VEN", "GUY", "SUR", "BRA"}, "box": {"BRA": (-10, 6, -75, -34)},
     "months": set(R(7, 13)) | {1, 2, 3}, "label": "northern South America and the Amazon", "refs": ["P01", "RH87"]},
    {"id": "peru_ecuador_coast_wet", "hazards": {"flood"}, "iso3": {"PER", "ECU"},
     "box": {"PER": (-10, 0, -82, -77), "ECU": (-5, 2, -82, -78.5)}, "months": {12, 1, 2, 3, 4},
     "label": "the coast of Peru and Ecuador", "refs": ["RH87", "MG01"]},
    {"id": "southeast_south_america_wet", "hazards": {"flood"}, "iso3": {"ARG", "URY", "PRY", "BRA"},
     "box": {"BRA": (-34, -22, -58, -47), "ARG": (-40, -22, -66, -53)}, "months": {10, 11, 12, 1, 2},
     "label": "south-eastern South America", "refs": ["GBD00"]},
    {"id": "southern_us_mexico_wet", "hazards": {"flood"}, "iso3": {"USA", "MEX"},
     "box": {"USA": (25, 37, -125, -75), "MEX": (22, 33, -118, -97)}, "months": {11, 12, 1, 2, 3},
     "label": "the southern United States and northern Mexico", "refs": ["RH86"]},
    {"id": "east_central_pacific_cyclones", "hazards": {"cyclone"}, "iso3": None,
     "box": {"*": (5, 35, -180, -85)}, "months": set(R(6, 12)),
     "label": "the eastern and central North Pacific, from Mexico's Pacific coast to Hawaii", "refs": ["NOAA14", "CW97"]},
    {"id": "south_pacific_cyclones", "hazards": {"cyclone"}, "iso3": None,
     "box": {"*": (-25, -5, 170, -130)}, "months": {11, 12, 1, 2, 3, 4},
     "label": "the central South Pacific islands", "refs": ["PCCSP"]},
]
MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
         "October", "November", "December"]

# Hazard words, in priority order ("Hurricane Nolo floods roads" is a cyclone).
HAZARD_WORDS = [
    ("cyclone", r"cyclone|hurricane|typhoon|tropical storm|hurac[aá]n|ouragan|tormenta tropical"),
    ("flood", r"flood|flooding|inundat|inondation|inundaci|inunda[çc][ãa]o|enchente|heavy rains?|torrential"),
    ("wildfire", r"wildfire|bushfire|forest fires?|peat fires?|peatland fire|incendio|inc[êe]ndio florestal|queimadas?|feux de for[eê]t"),
    ("heat", r"heat ?wave|extreme heat|record heat|ola de calor|onda de calor|canicule"),
    ("drought", r"drought|dry spell|abnormally dry|rainfall deficit|poor rains|below[- ]average rain|parched|sequ[ií]a|"
                r"\bseca\b|estiagem|s[eé]cheresse"),
]
# A headline about what may come, not what happened.
AHEAD = re.compile(r"\b(could|may|might|will|would|threat\w*|looms?|looming|risks?|prepar\w*|ahead|forecasts?|"
                   r"outlooks?|expected|warns?|warning|explainer|what is|how will|about to|possible|potential|"
                   r"set to|likely|anticipat\w*|plans?|brace\w*|urges?|"
                   # the regional outlets write in Spanish and Portuguese too
                   r"podr[íi]a|podr[áa]|prev[ée]|previs[ãa]o|pron[óo]stico|amenaza|amea[çc]a|riesgo|risco|alerta|"
                   r"se espera|posible|probable|poss[íi]vel|prepar[ae]\w*)\b", re.I)
# Case matters: Spanish "del niño" is "of the child" (refresh_enso_news.NAMED says the same).
EL_NINO = re.compile(r"\b(?:[Ee]l|EL)\s+(?:Ni[nñ]o|NI[NÑ]O)\b")
FEED_NAME = {"gdacs": "GDACS", "reliefweb": "ReliefWeb", "enso_news": "El Niño news"}
GDACS_TYPE = {"DR": "drought", "FL": "flood", "TC": "cyclone", "WF": "wildfire"}
HAZARD_WORD = {"drought": "Drought", "flood": "Flooding", "cyclone": "A tropical cyclone", "wildfire": "Wildfire",
               "heat": "Heat"}


def _load(name: str) -> dict:
    p = DATA_DIR / name
    return json.loads(p.read_text()) if p.exists() else {}


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _hazard(text: str) -> str | None:
    t = text.lower()
    for kind, pat in HAZARD_WORDS:
        if re.search(pat, t):
            return kind
    return None


def _in_box(box, lat, lon) -> bool:
    s, n, w, e = box
    if lat is None or lon is None or not s <= lat <= n:
        return False
    return w <= lon <= e if w <= e else (lon >= w or lon <= e)


def _months(a: date, b: date) -> set[int]:
    out, d = set(), date(a.year, a.month, 1)
    while d <= b:
        out.add(d.month)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def _match(kind: str, iso: str, lat, lon, located: bool, months: set[int]) -> dict | None:
    """The first PATTERNS row that covers this hazard, country, position and months."""
    for p in PATTERNS:
        if kind not in p["hazards"] or not months & p["months"]:
            continue
        box = (p.get("box") or {}).get("*") or (p.get("box") or {}).get(iso)
        if p["iso3"] is not None and iso not in p["iso3"]:
            continue
        # A box is tested on the event's own position; a headline placed at a capital cannot pass one.
        if box and not (located and _in_box(box, lat, lon)):
            continue
        return p
    return None


def _covered(iso: str, kind: str, a: date, b: date, hand: list) -> bool:
    return any(iso in (e.get("iso3") or []) and e.get("type") == kind and _d(e.get("date_start")) and
               _d(e["date_start"]) <= b and _d(e.get("date_end") or e["date_start"]) >= a for e in hand)


def _candidates(today: date) -> list[dict]:
    """(feed, key, title, kind, iso3 list, lat, lon, located, start, end, impact, source) from the three feeds."""
    out = []
    g = (_load("gdacs.json").get("data") or {})
    for key, v in g.items():
        kind = GDACS_TYPE.get(v.get("event_type"))
        a, b = _d(v.get("from_date")), _d(v.get("to_date")) or today
        if not kind or not a:
            continue
        isos = list(dict.fromkeys([v.get("iso3")] + list(v.get("affected_iso3") or [])))
        out.append({"feed": "gdacs", "key": key, "title": v.get("title") or "", "kind": kind,
                    "iso3": [i for i in isos if i], "lat": v.get("lat"), "lon": v.get("lng"), "located": True,
                    "names": {v.get("iso3"): v.get("country")} if v.get("country") else {},
                    "start": a, "end": min(b, today), "impact": v.get("severity_text") or "",
                    "source": {"title": v.get("title") or "", "publisher": "GDACS (European Commission JRC and UN OCHA)",
                               "url": v.get("url") or "https://www.gdacs.org/", "date": (v.get("date_modified") or "")[:10]}})
    for e in ((_load("reliefweb_alerts.json").get("data") or {}).get("events") or []):
        t, d = e.get("title") or "", _d(e.get("date"))
        if d and e.get("iso3") and e["iso3"] != "WLD":
            out.append({"feed": "reliefweb", "key": e.get("url") or t, "title": t, "kind": None, "iso3": [e["iso3"]],
                        "start": d - timedelta(days=NEWS_SPAN - 1), "end": d, "impact": "", "located": False,
                        "source": {"title": t, "publisher": "ReliefWeb", "url": e.get("url") or "", "date": d.isoformat()}})
    for e in ((_load("enso_news.json").get("data") or {}).get("items") or []):
        t, d = e.get("title") or "", _d(e.get("published_at"))
        isos = [i for i in (e.get("countries_mentioned") or []) if i != "WLD"]
        # A national outlet's hazard report that names no place is about its own country (the hazard and
        # forward-looking tests below still apply); the place text says the country came from the outlet.
        outlet = not isos and bool(e.get("outlet_iso3"))
        if outlet:
            isos = [e["outlet_iso3"]]
        if d:
            out.append({"feed": "enso_news", "key": e.get("url") or t, "title": t, "kind": None, "iso3": isos, "outlet": outlet,
                        "start": d - timedelta(days=NEWS_SPAN - 1), "end": d, "impact": "", "located": False,
                        "source": {"title": t, "publisher": e.get("source") or "", "url": e.get("url") or "",
                                   "date": d.isoformat()}})
    return out


def build(today: date | None = None) -> tuple[dict, dict]:
    today = today or date.today()
    lo = today - timedelta(days=LOOKBACK - 1)
    hand = ((_load("enso_recent_events.json").get("data") or {}).get("events") or [])
    oni = ((_load("enso.json").get("data") or {}).get("latest") or {})
    nino = isinstance(oni.get("anom"), (int, float)) and oni["anom"] >= ONI_MIN
    skipped: dict[str, int] = {}
    skip = lambda why: skipped.__setitem__(why, skipped.get(why, 0) + 1)
    events = []
    for c in _candidates(today):
        if c["end"] < lo or c["start"] > today:
            skip("outside the 45 days")
            continue
        text = c["title"] + " " + c["impact"]
        kind = c["kind"] or _hazard(c["title"])
        if not kind:
            skip("not a drought, flood, cyclone, wildfire or heat report")
            continue
        if c["feed"] != "gdacs" and AHEAD.search(c["title"]):
            skip("about what may come, not what happened")
            continue
        named = bool(EL_NINO.search(text))
        a, b = max(c["start"], lo), c["end"]
        keep, pats = [], []
        for iso in c["iso3"]:
            if _covered(iso, kind, c["start"], c["end"], hand):
                continue
            ll = COUNTRY_COORDS.get(iso) if not c["located"] else (c.get("lat"), c.get("lon"))
            p = _match(kind, iso, ll[0] if ll else None, ll[1] if ll else None, c["located"], _months(a, b)) if nino else None
            if named or p:
                keep.append(iso)
                if p and p not in pats:
                    pats.append(p)
        if not c["iso3"]:
            skip("no country to place it")
            continue
        if not keep:
            skip("already in the checked list" if all(_covered(i, kind, c["start"], c["end"], hand) for i in c["iso3"])
                 else "not in a place and season El Niño is known to hit, and does not name El Niño")
            continue
        name = lambda i: c.get("names", {}).get(i) or COUNTRY_NAMES.get(i, i)
        if c["located"] and (c["feed"] != "gdacs" or c["iso3"][0] in keep):
            lat, lon, where = c["lat"], c["lon"], ", ".join(name(i) for i in keep)
        else:
            cap = COUNTRY_COORDS.get(keep[0])
            if not cap:
                skip("no country to place it")
                continue
            lat, lon = cap
            where = ", ".join(name(i) for i in keep) + (" (marked at the capital: the headline names no place; the country is the outlet's)" if c.get("outlet")
                                                       else " (marked at the capital: the feed names the country only)" if not c["located"]
                                                       else " (marked at the capital: the feed's position is for the whole area)")
        if named:
            why = "Picked by rule from the " + FEED_NAME[c["feed"]] + " feed, not checked by a person. The headline names El Niño."
        else:
            p = pats[0]
            mo = sorted(p["months"] & _months(a, b), key=lambda m: (m - a.month) % 12)
            why = ("Picked by rule from the " + FEED_NAME[c["feed"]] + " feed, not checked by a person. The feed does not "
                   "name El Niño. " + HAZARD_WORD[kind] + " in " + p["label"] + " in " + " and ".join(MONTH[m - 1] for m in mo) +
                   " fits what El Niño usually brings there (" + "; ".join(SHORT[r] for r in p["refs"]) + ").")
        slug = hashlib.sha1(c["key"].encode()).hexdigest()[:10] if c["feed"] != "gdacs" else c["key"]
        events.append({
            "id": "auto-" + c["feed"] + "-" + slug, "title": c["title"],
            "date_start": c["start"].isoformat(), "date_end": c["end"].isoformat(), "type": kind, "iso3": keep,
            "lat": round(lat, 2) if lat is not None else None, "lon": round(lon, 2) if lon is not None else None,
            "place": where, "impact": c["impact"], "enso_link": "named" if named else "consistent",
            "region_id": pats[0]["id"] if pats else None, "why": why, "source": c["source"],
            "checked": False, "feed": c["feed"]})
    # Last-good: items an earlier run found and the feeds have since rolled off stay while they touch the 45 days.
    fresh = {e["id"] for e in events}
    prev = ((_load(OUT).get("data") or {}).get("events") or [])
    kept = [e for e in prev if e.get("id") not in fresh and (_d(e.get("date_end")) or lo) >= lo
            and not any(_covered(i, e.get("type"), _d(e["date_start"]), _d(e["date_end"]), hand) for i in e.get("iso3") or [])]
    events = sorted(events + kept, key=lambda e: (e["date_end"], e["id"]), reverse=True)
    payload = {
        "schema": 1, "window": {"start": lo.isoformat(), "end": today.isoformat(), "days": LOOKBACK},
        "el_nino_conditions": {"oni": oni.get("anom"), "season": f"{oni.get('season')} {oni.get('year')}",
                               "pattern_rule_on": nino, "threshold": ONI_MIN, "from": "data/enso.json"},
        "rule": ("Drought, flood, tropical cyclone, wildfire and heat reports from GDACS, ReliefWeb and the El Niño "
                 "news wire that touch the last 45 days and either hit a place and season El Niño is known to hit "
                 "(patterns, while El Niño conditions hold) or name El Niño in the headline. Picked by rule, never "
                 "checked by a person. Countries already in data/enso_recent_events.json with the same hazard and "
                 "overlapping dates are left out."),
        "patterns": [{"id": p["id"], "hazards": sorted(p["hazards"]), "iso3": sorted(p["iso3"]) if p["iso3"] else None,
                      "box": p.get("box"), "months": sorted(p["months"]), "label": p["label"],
                      "refs": [{"title": REFS[r][0], "url": REFS[r][1]} for r in p["refs"]]} for p in PATTERNS],
        "events": events, "carried_over": len(kept), "skipped": skipped,
    }
    return payload, {"fresh": len(fresh), "kept": len(kept)}


def main() -> int:
    payload, n = build()
    write_json(OUT, payload, source="GDACS, ReliefWeb and the El Niño news wire (data/gdacs.json, "
               "data/reliefweb_alerts.json, data/enso_news.json), filtered by rule; no network",
               notes="Reported hazards that fit El Niño, picked by rule for the El Niño map's 7-day and 30-day "
                     "stops. checked is false on every item: none was opened by a person.", status="ok")
    stamp_inputs(OUT)
    print(f"[OK] {len(payload['events'])} auto El Niño marks ({n['fresh']} this run, {n['kept']} carried over) | "
          f"skipped {payload['skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
