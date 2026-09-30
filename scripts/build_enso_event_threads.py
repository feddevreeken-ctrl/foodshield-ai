"""
El Niño event threads: reports of the same event merged into one thread, with two separate measures.

Reads (no network): data/enso_recent_events.json (hand-curated, one cited report per event), data/gdacs.json,
data/reliefweb_alerts.json, data/enso_news.json (ReliefWeb reports, trade press, GDELT) and data/enso_auto_events.json
(machine picks; used only to carry its pattern flag onto the GDACS / wire report it was picked from, never as a
second report).

Raw report = one dated item from one publisher. Item that is not an event report is dropped and counted:
  curated events all count; GDACS drought, flood, cyclone entries count; ReliefWeb and wire items count only with a
  hazard word in the headline (same word lists as refresh_enso_auto_events.py), a country, and no "about what may
  come" wording (looms, could, prepares...).

Merge rule (conservative; a wrong merge hides an event, a missed merge only shows two lines). Reports are taken
curated first, then GDACS, ReliefWeb, wire, each by date, and a report joins the first existing thread for which ALL hold:
  1. same hazard (flood, drought, cyclone, wildfire, heat, crop, water, fishery: never merged across kinds);
  2. a country in common (GDACS events spanning more than four countries count their primary country only);
  3. dates: two dated events overlap within 3 days; a headline or ReliefWeb item (one publication date) falls from 3 days
     before the thread starts to 14 days after it ends;
  4. place: two located events are within 350 km, or share a distinctive place word; an unlocated headline must share
     at least two distinctive words (not hazard, country or El Niño words) with a report already in the thread.
No score, no fuzzy model: each test is a yes or no, and the failed test is what keeps two reports apart.

Two measures per thread, never combined:
  event_confidence  independent sources. A source is a publishing organisation (text after "via" and "republishing"
                    dropped; a ReliefWeb item counts as its named publisher; ReliefWeb itself, or an unnamed one,
                    counts once). Class: agency (UN, EU, government, hydromet, civil protection, GDACS, FEWS NET),
                    ngo, press. Level: low = 1 source; medium = 2 or more in one class; high = 2 or more across two classes.
                    Syndicated wire copy is only caught where the credit says so (AFP, AP, Reuters, "via").
  el_nino_attribution  attributed = a hand-curated entry whose cited source ties the event to El Niño; pattern_consistent =
                    a curated entry marked consistent, or the machine rule in enso_auto_events (flagged unchecked);
                    not_assessed = neither. A person sets it in enso_recent_events.json. Merging a report never raises it,
                    and a headline that names El Niño is counted as a mention, not as attribution.

Lifecycle. Silence is not resolution: droughts, crop failures and food crises run for months after the last headline.
A thread is in exactly one state (first match wins):
  resolved   ONLY with positive evidence that the event ended, from one of these fields, and no later report contradicting it:
               a) a GDACS report whose window has closed: gdacs.json is_current == false (todate more than 7 days ago, see
                  refresh_gdacs.py; GDACS's own iscurrent flag is NOT used), evidence date = its to_date;
               b) a curated entry in enso_recent_events.json carrying an `ended` object {"date", "publisher", "url"} that a person
                  sets from an agency closing statement (none exist today);
               c) an agency-class (UN, EU, government, hydromet) ReliefWeb or wire headline with ended wording (lifted, called off,
                  declared over, has ended, closing statement, appeal closed), evidence date = its publication date.
             A curated, ReliefWeb or wire report dated more than 7 days after the evidence date contradicts it (a GDACS record's own
             date_modified is an edit stamp, not a new report, and does not) and the thread is not resolved.
             Age alone never resolves a thread.
  stale      no dated report (publication date) for longer than the window of its hazard, stated per type in STALE_DAYS:
             drought 120, crop 120, water 90, fishery 90, wildfire 45, flood 30, flash flood 10 (headline or place says flash),
             heat 21, cyclone 14, other 60 days. Stale means "no news", not "over": it can return to active on a new report.
  worsening / easing  two or more dated reports give the same measure (deaths, people, hectares, GDACS alert level) and the
             latest is 10 percent or more above / below the earliest; nothing else counts as a trend;
  confirmed  two or more independent sources;
  new        first report within 7 days and one source;
  active     one source, reported within its stale window, first report older than 7 days.
Materiality (decides only the default view on the page, never the state). A low-confidence thread is material when ANY holds:
  people      a report gives a count of people, displaced, affected or deaths above zero (in its text or impact line), or GDACS
              gives population_affected above zero;
  ipc_fews    a thread country has FEWS NET current phase 4 or higher (fews.json) or IPC people in phase 4 or 5 above zero
              (ipc.json), or a report text names IPC or FEWS NET (phase 3 is too common to single an event out);
  export      a trade_restrictions.json export measure covers a thread country and has not ended, or a report names an export ban,
              restriction, quota or duty.
High and medium confidence threads are always shown.

Output: data/enso_event_threads.json
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, stamp_inputs, write_json  # noqa: E402
from _news_corridors import detect_countries  # noqa: E402
import refresh_enso_auto_events as ae  # noqa: E402

SLACK_STRUCT, SLACK_BEFORE, SLACK_AFTER = 3, 3, 14
KM_SAME_PLACE = 350
MIN_SHARED_WORDS = 2
NEW_DAYS, TREND_PCT, END_CONTRADICT_DAYS = 7, 10, 7
STALE_DAYS = {"drought": 120, "crop": 120, "water": 90, "fishery": 90, "wildfire": 45, "flood": 30, "flash flood": 10,
              "heat": 21, "cyclone": 14, "other": 60}
ENDED_WORDS = re.compile(r"\b(lifted|called off|declared over|has ended|have ended|is over|closing statement|appeal closed|operation closed)\b", re.I)
EXPORT_WORDS = re.compile(r"\bexport (ban|restriction|quota|duty|tax|curb)s?\b", re.I)
IPC_WORDS = re.compile(r"\b(ipc|fews net|fewsnet)\b", re.I)
PEOPLE_UNITS = ("people", "deaths", "displaced", "affected")
MAX_PRIMARY_ONLY = 4

STOP = set("""the and for with from that this into over after amid under than more less near across its their have has been
will were was are not but out off new say says said up down may could would about during while since year years week weeks
month months day days per cent percent people state states region regions province provinces parts part area areas
national government local rain rains rainfall drought droughts flood floods flooding dry water weather el nino niño nina
hits hit strikes strike leaves leave pushes push falls fall rises rise declares declare deepens deepen spreads spread
crop crops farm farms harvest season seasonal heat fire fires wildfire wildfires cyclone hurricane typhoon storm
severe heavy worst first driest record emergency alert warns warning news report reports update situation
""".split())
AGENCY = re.compile(r"\b(echo|ocha|un\b|united nations|fao|wfp|unicef|who\b|paho|ifrc|igad|fews|gdacs|giews|government|govt|ministry|"
                    r"department|bureau|authority|protecci[oó]n civil|civil protection|pagasa|abares|noaa|met office|meteorolog\w*|"
                    r"commission|agency for|reliefweb|gredo|fsnau|ipc\b)", re.I)
NGO = re.compile(r"\b(care\b|save the children|international rescue|irc\b|oxfam|world vision|red cross|mercy corps|concern|action against hunger)", re.I)
WIRE_OK = re.compile(r"\b(afp|associated press|ap\b|reuters|xinhua|bloomberg|anadolu|aap|australian associated press)\b", re.I)
ALERT_RANK = {"green": 1, "orange": 2, "red": 3}
MEASURE = re.compile(r"(\d[\d,\.]*)\s*(million|billion|m\b|k\b)?\s*(people|persons|deaths|dead|killed|displaced|hectares|ha\b|municipalities|provinces|affected)", re.I)


def _load(name):
    p = DATA_DIR / name
    return json.loads(p.read_text()) if p.exists() else {}


def _d(s):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def km(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    h = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


WIRES = [("australian associated press", "aap"), ("associated press", "associated press"), (r"\bap\b", "associated press"), (r"\bafp\b", "afp"),
         ("reuters", "reuters"), ("xinhua", "xinhua"), ("bloomberg", "bloomberg"), ("anadolu", "anadolu")]
ALIAS = {"irc": "international rescue committee", "echo": "european commission echo", "european commission echo": "european commission echo",
         "un ocha": "ocha", "fao giews": "fao", "gdacs european commission jrc and un ocha": "gdacs"}


def origin_names(publisher: str) -> list[str]:
    """Publishing organisation(s) behind a credit line: 'AFP (via Eyewitness News)' and 'France 24 (AFP)' are AFP;
    'Al Jazeera and AP' is two. Wire credits map to one name; known aliases are folded."""
    p = re.split(r",?\s+(?:via|republishing)\s+", re.sub(r"\((?:via|republishing)[^)]*\)", "", publisher or "", flags=re.I), flags=re.I)[0]
    out = []
    parts = [x.strip() for x in re.split(r"\s+and\s+", p) if x.strip()]
    if not any(re.search(pat, x.lower()) for x in parts for pat, _ in WIRES):
        parts = [p.strip()]          # 'Fisheries and Forestry' is one name; only a credit that names a wire is split
    for part in parts:
        low = part.lower()
        w = next((c for pat, c in WIRES if re.search(pat, low)), None)
        if w:
            out.append(w)
            continue
        low = re.sub(r"[^\w ]+", "", re.sub(r"\(.*?\)", "", low)).strip()
        out.append(ALIAS.get(low, low))
    return out or [""]


def klass(publisher: str) -> str:
    if NGO.search(publisher):
        return "ngo"
    if AGENCY.search(publisher):
        return "agency"
    return "press"


def words(text: str, drop: set[str]) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ÿ]{4,}", (text or "").lower()) if w not in STOP and w not in drop}


def measures(text: str) -> dict:
    out = {}
    for n, mult, unit in MEASURE.findall(text or ""):
        try:
            v = float(n.replace(",", ""))
        except ValueError:
            continue
        v *= {"million": 1e6, "m": 1e6, "billion": 1e9, "k": 1e3}.get((mult or "").lower(), 1)
        u = {"dead": "deaths", "killed": "deaths", "persons": "people", "ha": "hectares"}.get(unit.lower(), unit.lower())
        out[u] = max(out.get(u, 0), v)
    return out


def gather(today: date):
    """Every raw item in the four feeds, as report dicts, plus the counts dropped and why."""
    reps, drop = [], {}

    def dropped(why):
        drop[why] = drop.get(why, 0) + 1

    cnames = {}
    for ev in ((_load("enso_recent_events.json").get("data") or {}).get("events") or []):
        s = ev.get("source") or {}
        reps.append({"feed": "curated", "id": ev["id"], "title": ev.get("title") or "", "url": s.get("url") or "",
                     "publisher": s.get("publisher") or "", "src_title": s.get("title") or "",
                     "date": _d(s.get("date")) or _d(ev.get("date_end")), "start": _d(ev.get("date_start")),
                     "end": _d(ev.get("date_end")) or _d(ev.get("date_start")), "hazard": ev.get("type"),
                     "iso3": list(ev.get("iso3") or []), "loc": (ev["lat"], ev["lon"]) if ev.get("lat") is not None else None,
                     "place": ev.get("place") or "", "text": (ev.get("title") or "") + " " + (ev.get("impact") or ""),
                     "link": ev.get("enso_link"), "impact": ev.get("impact") or "",
                     "ended": _d((ev.get("ended") or {}).get("date")), "ended_src": ev.get("ended") or None})
    for key, v in (_load("gdacs.json").get("data") or {}).items():
        kind = ae.GDACS_TYPE.get(v.get("event_type")) if isinstance(v, dict) else None
        a = _d(v.get("from_date")) if kind else None
        if not kind or not a:
            dropped("GDACS entry that is not a drought, flood, cyclone or wildfire")
            continue
        isos = [v.get("iso3")] + list(v.get("affected_iso3") or [])
        isos = [i for i in dict.fromkeys(isos) if i]
        if len(isos) > MAX_PRIMARY_ONLY:
            isos = isos[:1]
        reps.append({"feed": "gdacs", "id": key, "title": v.get("title") or "", "url": v.get("url") or "",
                     "publisher": "GDACS (European Commission JRC and UN OCHA)", "src_title": v.get("title") or "",
                     "date": _d(v.get("date_modified")) or a, "start": a, "end": _d(v.get("to_date")) or today,
                     "hazard": kind, "iso3": isos, "loc": (v["lat"], v["lng"]) if v.get("lat") is not None else None,
                     "place": v.get("country") or "", "text": (v.get("title") or "") + " " + (v.get("severity_text") or ""),
                     "link": None, "impact": v.get("severity_text") or "", "alert": ALERT_RANK.get(str(v.get("alert_level")).lower()),
                     "current": bool(v.get("is_current")), "pop": v.get("population_affected") or 0})
        cnames[v.get("iso3")] = v.get("country")
    for e in ((_load("reliefweb_alerts.json").get("data") or {}).get("events") or []):
        t, d = e.get("title") or "", _d(e.get("date"))
        hz = ae._hazard(t)
        if not d or not e.get("iso3") or e["iso3"] == "WLD":
            dropped("ReliefWeb item without a date or a country")
        elif not hz:
            dropped("ReliefWeb item with no hazard word in the headline")
        elif ae.AHEAD.search(t):
            dropped("headline about what may come, not what happened")
        else:
            reps.append({"feed": "reliefweb", "id": e.get("url") or t, "title": t, "url": e.get("url") or "", "publisher": "ReliefWeb",
                         "src_title": t, "date": d, "start": d, "end": d, "hazard": hz, "iso3": [e["iso3"]], "loc": None,
                         "place": e.get("country") or "", "text": t, "link": None, "impact": ""})
    for e in ((_load("enso_news.json").get("data") or {}).get("items") or []):
        t, d = e.get("title") or "", _d(e.get("published_at"))
        hz, isos = ae._hazard(t), [i for i in (e.get("countries_mentioned") or []) if i != "WLD"]
        if not d:
            dropped("wire item without a date")
        elif not hz:
            dropped("wire item with no hazard word in the headline")
        elif not isos:
            dropped("wire item with no country")
        elif ae.AHEAD.search(t):
            dropped("headline about what may come, not what happened")
        else:
            reps.append({"feed": "news", "id": e.get("url") or t, "title": t, "url": e.get("url") or "",
                         "publisher": e.get("source") or "", "src_title": t, "date": d, "start": d, "end": d, "hazard": hz,
                         "iso3": isos, "loc": None, "place": "", "text": t, "link": None, "impact": "",
                         "el_nino": bool(ae.EL_NINO.search(t))})
    n_raw = len(reps) + sum(drop.values())
    # the machine picks: the pattern flag rides on the report it was picked from
    by_url = {r["url"]: r for r in reps if r["url"]}
    for e in ((_load("enso_auto_events.json").get("data") or {}).get("events") or []):
        r = by_url.get((e.get("source") or {}).get("url"))
        if r:
            r["pattern"] = e.get("region_id") or "machine rule"
            r["pattern_why"] = e.get("why") or ""
            if e.get("iso3"):
                r["iso3"] = list(e["iso3"])
    return reps, drop, n_raw, cnames


def same_thread(r, th, cn) -> bool:
    if r["hazard"] != th["hazard"]:
        return False
    if r["feed"] == "gdacs" and any(m["feed"] == "gdacs" for m in th["members"]):
        return False     # GDACS already resolves its own events: two GDACS entries are two events
    if not set(r["iso3"]) & th["iso3"]:
        return False
    structured = r["feed"] in ("curated", "gdacs")
    if structured:
        if r["start"] > th["end"] + timedelta(days=SLACK_STRUCT) or r["end"] < th["start"] - timedelta(days=SLACK_STRUCT):
            return False
    elif not (th["start"] - timedelta(days=SLACK_BEFORE) <= r["date"] <= th["end"] + timedelta(days=SLACK_AFTER)):
        return False
    drop = set(w for i in (r["iso3"] + list(th["iso3"])) for w in re.findall(r"[a-z]{4,}", (cn.get(i) or "").lower()))
    if structured:
        if r["loc"] and th["loc"] and km(r["loc"], th["loc"]) <= KM_SAME_PLACE:
            return True
        a = words(r["place"] + " " + r["title"], drop)
        return any(len(a & words(m["place"] + " " + m["title"], drop)) >= 1 and m["feed"] != "gdacs" and r["feed"] != "gdacs"
                   for m in th["members"])
    a = words(r["text"], drop)
    return any(len(a & words(m["text"] + " " + m["place"], drop)) >= MIN_SHARED_WORDS for m in th["members"])


def build(today: date | None = None):
    today = today or date.today()
    reps, dropped, n_raw, cnames = gather(today)
    try:
        from _common import COUNTRY_COORDS  # noqa: F401
    except Exception:
        pass
    names = {}
    try:
        from _news_gazetteer import COUNTRY_NAMES
        names = dict(COUNTRY_NAMES) if isinstance(COUNTRY_NAMES, dict) else {}
    except Exception:
        pass
    cn = {**names, **{k: v for k, v in cnames.items() if k}}
    order = {"curated": 0, "gdacs": 1, "reliefweb": 2, "news": 3}
    reps.sort(key=lambda r: (order[r["feed"]], r["start"] or date.min, r["date"] or date.min))
    threads = []
    for r in reps:
        if not r["date"] or not r["start"]:
            dropped["no usable date"] = dropped.get("no usable date", 0) + 1
            continue
        for th in threads:
            if same_thread(r, th, cn):
                th["members"].append(r)
                th["iso3"] |= set(r["iso3"]) if r["feed"] in ("curated", "gdacs") else set()
                th["start"], th["end"] = min(th["start"], r["start"]), max(th["end"], r["end"])
                if not th["loc"] and r["loc"]:
                    th["loc"] = r["loc"]
                break
        else:
            threads.append({"hazard": r["hazard"], "iso3": set(r["iso3"]), "start": r["start"], "end": r["end"], "loc": r["loc"], "members": [r]})
    out = [finish(th, today) for th in threads]
    rank = {"high": 2, "medium": 1, "low": 0}
    out.sort(key=lambda t: (-rank[t["event_confidence"]["level"]], -t["event_confidence"]["n_independent"], -t["n_reports"], t["last_reported"]), reverse=False)
    out.sort(key=lambda t: (-rank[t["event_confidence"]["level"]], -t["event_confidence"]["n_independent"], -t["n_reports"],
                            -date.fromisoformat(t["last_reported"]).toordinal()))
    kept = len(reps)
    stats = {"raw_items": n_raw, "dropped": dropped, "event_reports": kept, "threads": len(out),
             "threads_with_2_or_more_reports": sum(1 for t in out if t["n_reports"] >= 2),
             "reports_in_those_threads": sum(t["n_reports"] for t in out if t["n_reports"] >= 2),
             "by_state": {s: sum(1 for t in out if t["state"] == s) for s in ("new", "active", "confirmed", "worsening", "easing", "stale", "resolved")},
             "shown_by_default": sum(1 for t in out if t["event_confidence"]["level"] != "low" or t["material"]),
             "low_confidence_material": sum(1 for t in out if t["event_confidence"]["level"] == "low" and t["material"]),
             "by_confidence": {k: sum(1 for t in out if t["event_confidence"]["level"] == k) for k in ("low", "medium", "high")},
             "by_attribution": {k: sum(1 for t in out if t["el_nino_attribution"]["status"] == k) for k in ("attributed", "pattern_consistent", "not_assessed")},
             "by_feed": {f: sum(1 for r in reps if r["feed"] == f) for f in order}}
    return {"as_of": today.isoformat(), "rule": RULE, "stats": stats, "threads": out}


RULE = {
    "merge": "Same hazard, a shared country, overlapping dates (3 days for two dated events; 3 days before to 14 days after for a headline), and either "
             "two located events within 350 km or a shared place word, or two shared distinctive words for an unlocated headline. Every test is yes or no.",
    "confidence": "Independent publishing organisations and their class (agency, ngo, press). low = 1; medium = 2 or more in one class; high = 2 or more across two classes. "
                  "Syndication is caught only where the credit names the wire or says 'via'.",
    "attribution": "attributed, pattern_consistent or not_assessed, taken from the hand-curated entry (or the flagged machine pattern rule). Merging never raises it; a headline naming El Niño is a mention only.",
    "lifecycle": "resolved only with positive evidence of an end (GDACS window closed, a curated entry marked ended, or an agency headline saying it ended); silence is never resolution. "
                 "stale: no dated report for longer than the hazard's window (drought and crop 120 days, water and fishery 90, wildfire 45, flood 30, flash flood 10, heat 21, cyclone 14). "
                 "worsening or easing: the same measure in 2 or more dated reports moved 10 percent or more. confirmed: 2 or more independent sources. new: first report within 7 days, one source. active: one source, inside its window.",
    "stale_days": STALE_DAYS,
    "material": "A low-confidence thread is shown by default only if material: a count of people, displaced, affected or deaths above zero; FEWS NET phase 4+ or IPC phase 4 or 5 people in a thread country (or a report naming IPC or FEWS NET); or an unexpired export measure on a thread country (or a report naming one).",
}


def finish(th, today):
    m = sorted(th["members"], key=lambda r: (r["date"], r["feed"]))
    anchor = next((r for r in th["members"] if r["feed"] == "curated"), m[0])
    srcs, seen_title = {}, {}
    for r in sorted(m, key=lambda r: (r["feed"] != "curated", r["date"])):
        tkey = re.sub(r"[^a-z0-9]+", "", (r["src_title"] or r["title"]).lower())[:50]
        if tkey in seen_title:
            r["duplicate_of"] = seen_title[tkey]      # the same item carried by a second feed: one source, not two
            continue
        seen_title[tkey] = r["id"]
        for o in origin_names(r["publisher"]):
            key = "reliefweb" if o in ("reliefweb", "") else o
            srcs.setdefault(key, klass(r["publisher"]) if key != "reliefweb" else "agency")
    n_items = len(seen_title)
    n = min(len(srcs), n_items)          # one report, however many names its credit line carries, is one source
    classes = sorted(set(srcs.values()))
    level = "low" if n < 2 else ("high" if len(classes) >= 2 else "medium")
    chain = [{"date": r["date"].isoformat(), "publisher": r["publisher"], "class": klass(r["publisher"]), "feed": r["feed"],
              "title": (r["src_title"] or r["title"])[:160], "url": r["url"]} for r in m if not r.get("duplicate_of")]
    # attribution: people only; the machine rule is shown flagged
    cur = [r for r in th["members"] if r["feed"] == "curated" and r.get("link")]
    att, basis, checked = "not_assessed", None, None
    if cur:
        weakest = "attributed" if all(r["link"] == "attributed" for r in cur) else "pattern_consistent"
        att, checked = weakest, True
        b = next(r for r in cur if (r["link"] == "attributed") == (weakest == "attributed"))
        basis = {"id": b["id"], "publisher": b["publisher"], "url": b["url"], "note": "set by a person in data/enso_recent_events.json"}
    else:
        p = next((r for r in th["members"] if r.get("pattern")), None)
        if p:
            att, checked = "pattern_consistent", False
            basis = {"id": p["id"], "pattern": p["pattern"], "note": "machine rule in data/enso_auto_events.json, not checked by a person"}
    # lifecycle
    last = max(max(r["date"], r["end"]) for r in th["members"])
    first = min(r["date"] for r in th["members"])
    trend = None
    series = {}
    for r in m:
        ms = measures(r["impact"] or r["text"])
        if r.get("alert"):
            ms["gdacs alert level"] = r["alert"]
        for u, v in ms.items():
            series.setdefault(u, []).append((r["date"], v))
    for u, pts in series.items():
        if len(pts) >= 2 and pts[0][1] > 0 and pts[0][0] < pts[-1][0]:
            ch = (pts[-1][1] / pts[0][1] - 1) * 100
            if abs(ch) >= TREND_PCT:
                trend = {"measure": u, "from": pts[0][1], "to": pts[-1][1], "pct": round(ch)}
                break
    # positive evidence of an end (see header); none of these is age
    evid = None
    for r in th["members"]:
        if r["feed"] == "gdacs" and not r.get("current") and r["end"] and r["end"] < today:
            cand = (r["end"], "GDACS window closed (is_current false, to_date " + r["end"].isoformat() + ")", r["url"])
        elif r["feed"] == "curated" and r.get("ended"):
            cand = (r["ended"], "curated entry marked ended by a person", (r.get("ended_src") or {}).get("url") or "")
        elif r["feed"] in ("reliefweb", "news") and klass(r["publisher"]) == "agency" and ENDED_WORDS.search(r["title"]):
            cand = (r["date"], "agency headline says the event ended", r["url"])
        else:
            continue
        if not evid or cand[0] > evid[0]:
            evid = cand
    if evid and any(r["feed"] != "gdacs" and r["date"] > evid[0] + timedelta(days=END_CONTRADICT_DAYS) for r in th["members"]):
        evid = None
    hz_key = "flash flood" if th["hazard"] == "flood" and re.search(r"flash", " ".join(r["title"] + " " + r["place"] for r in th["members"]), re.I) else th["hazard"]
    stale_days = STALE_DAYS.get(hz_key, STALE_DAYS["other"])
    last_report = max(r["date"] for r in th["members"])
    silent = (today - last_report).days
    if evid:
        state = "resolved"
    elif silent > stale_days:
        state = "stale"
    elif trend:
        state = "worsening" if trend["pct"] > 0 else "easing"
    elif n >= 2:
        state = "confirmed"
    elif (today - first).days <= NEW_DAYS:
        state = "new"
    else:
        state = "active"
    # materiality, for the default view only
    why = []
    if any(any(ms.get(u, 0) > 0 for u in PEOPLE_UNITS) for ms in (measures(r["impact"] or r["text"]) for r in th["members"])) \
            or any(r.get("pop") for r in th["members"]):
        why.append("people")
    fe, ip, tr = _load("fews.json").get("data") or {}, _load("ipc.json").get("data") or {}, _load("trade_restrictions.json").get("data") or []
    if any(((fe.get(i) or {}).get("current_phase") or 0) >= 4 or ((ip.get(i) or {}).get("phase4_count") or 0) + ((ip.get(i) or {}).get("phase5_count") or 0) > 0 for i in th["iso3"]) \
            or any(IPC_WORDS.search(r["text"] + " " + r["src_title"]) for r in th["members"]):
        why.append("ipc_fews")
    if any(t.get("iso") in th["iso3"] and (_d(t.get("ends_date")) or today) >= today for t in tr) \
            or any(EXPORT_WORDS.search(r["text"]) for r in th["members"]):
        why.append("export")
    tid = "thr-" + hashlib.sha1(anchor["id"].encode()).hexdigest()[:8]
    return {
        "id": tid, "title": anchor["title"], "hazard": th["hazard"], "iso3": sorted(th["iso3"]), "place": anchor["place"],
        "start": th["start"].isoformat(), "end": th["end"].isoformat(), "first_reported": first.isoformat(), "last_reported": last.isoformat(),
        "n_reports": len(m), "n_same_item_in_two_feeds": len(m) - len(chain), "state": state, "trend": trend,
        "last_dated_report": last_report.isoformat(), "days_silent": silent, "stale_after_days": stale_days,
        "end_evidence": ({"date": evid[0].isoformat(), "what": evid[1], "url": evid[2]} if evid else None),
        "material": bool(why), "material_because": why,
        "event_confidence": {"level": level, "n_independent": n, "classes": classes, "sources": sorted(srcs)},
        "el_nino_attribution": {"status": att, "checked_by_person": checked, "basis": basis,
                                "mentions_in_headlines": sum(1 for r in th["members"] if r.get("el_nino"))},
        "chain": chain,
    }


def main():
    out = build()
    s = out["stats"]
    print(f"[threads] {s['raw_items']} raw items, {s['event_reports']} event reports -> {s['threads']} threads "
          f"({s['threads_with_2_or_more_reports']} with 2+ reports holding {s['reports_in_those_threads']}); states {s['by_state']}; "
          f"confidence {s['by_confidence']}; attribution {s['by_attribution']}")
    write_json("enso_event_threads.json", out, source="data/enso_recent_events.json, gdacs.json, reliefweb_alerts.json, enso_news.json, enso_auto_events.json (no network)",
               status="ok", notes="Reports of the same event merged by rule, with event confidence and El Niño attribution kept as two separate measures. See data.rule.")
    stamp_inputs("enso_event_threads.json")


if __name__ == "__main__":
    main()
