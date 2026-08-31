"""
GDACS — Global Disaster Alert and Coordination System live hazard events.

WHY THIS FEED EXISTS
--------------------
The map's "Live disturbances" layer had no working hazard source.
data/nasa_firms.json has carried `"status": "auth_failed"` since 2026-08-03
(expired NASA_FIRMS_MAP_KEY) and its refresher was never wired into
run_all.py at all, so wildfire contributed exactly nothing. GDACS is the
replacement: free, keyless, no registration, run by the JRC/UN, and it covers
the hazard classes that actually move food security rather than the one that
happened to have an API key.

WHAT IS INCLUDED, AND WHY
-------------------------
Default event types (INCLUDED_TYPES):
  DR  Drought  — the single most direct production shock. Source GDO,
                 which reports affected agricultural area in km2.
  FL  Flood    — destroys standing crops, drowns seed beds, cuts feeder
                 roads to markets. Source GLOFAS.
  TC  Tropical cyclone — flattens standing crops in the days before harvest
                 and closes the ports that grain moves through. Source JTWC.

Off by default (OPTIONAL_TYPES), enable with FOODSHIELD_GDACS_TYPES:
  EQ  Earthquake — deliberately OFF. Earthquakes are devastating, but their
                 food-security transmission runs through logistics and market
                 disruption, which this dashboard already reads from LPI,
                 ACLED and the trade layers. They are also the single most
                 frequent GDACS Orange/Red type (13 in a 90-day window against
                 5 droughts), so switching them on visually swamps the
                 hazards that actually change production. Turn on with
                 FOODSHIELD_GDACS_TYPES="DR;FL;TC;EQ" when a specific quake
                 is under analysis.
  WF  Wildfire — off. GDACS WF measures burned forest hectares, which is a
                 poor proxy for cropland loss; this is a coverage gap, not a
                 solved problem, and it is NOT a like-for-like replacement for
                 what NASA FIRMS was meant to provide.
  VO  Volcano  — off. Rare, and its food impact is local and slow.

ALERT LEVELS: Orange + Red only. GDACS Green means "no significant impact
expected" and is the overwhelming majority of the feed — 82 Green floods
against 4 Orange and 1 Red in a single two-month probe. Plotting Green would
bury the signal and blow past the API's row cap for nothing.

TRAPS HIT WHILE BUILDING THIS — every one of these is load-bearing
------------------------------------------------------------------
 1. HTTP 204, NOT AN EMPTY FeatureCollection. A query with zero matches
    returns 204 No Content with a zero-byte body. `raise_for_status()` is
    happy (204 is 2xx) and then `.json()` explodes on an empty string. Every
    call site must check `status_code == 204` BEFORE parsing. This is the
    normal, expected response for e.g. VO in a quiet month.

 2. HARD 100-ROW CAP WITH SILENT TRUNCATION. Any list query returns at most
    100 features and says nothing about it. The undocumented-looking
    lowercase `pagesize` / `limit` / `count` params are all ignored. The
    OpenAPI spec at /gdacsapi/swagger/v1/swagger.json documents the real
    ones: `pageSize` (max 100) and `pageNumber` (1-based), camelCase. Paging
    verified: pages 1/2/3 of a 300-event query returned zero overlapping
    eventids. We paginate, and we still raise if MAX_PAGES is exhausted.

 3. THE DATE FILTER IS ON todate, NOT fromdate. `fromDate=2026-08-01`
    returns a drought whose fromdate is 2025-12-21, because its todate is
    current. Results are ordered by todate desc (per the OpenAPI summary),
    so the 100-row cap silently drops the OLDEST events first. This is
    actually the behaviour we want — LOOKBACK_DAYS means "ended within N
    days, or still running" — but it is not what the parameter name says.

 4. severitydata IS NOT COMPARABLE ACROSS HAZARD TYPES. Verified units in
    one response: DR = km2 of drought-affected agricultural land
    (1,412,468), EQ = moment magnitude (7.8), TC = max wind km/h (213),
    WF = burned hectares (18,310), FL = literally 0.0 / "Magnitude 0"
    because GLOFAS publishes no scalar severity at all. Anything that reads
    `severity` as a cross-hazard number is wrong. `severity_score` here is
    therefore derived from GDACS's own cross-hazard ALERT BAND, and the raw
    figure is carried separately and unmixed as severity_value/unit/text.

 5. GDACS's OWN `iscurrent` FLAG IS NOT "IS THIS HAPPENING NOW". Verified
    2026-08-31: drought events 1018332 and 1018431 both carry
    todate = 2026-08-31 (today) and iscurrent = "false". It appears to mean
    "is in the live homepage feed", which for GDO's dekadal drought product
    is false most of the time. We derive `is_current` from the dates instead
    and carry their flag verbatim as `gdacs_iscurrent` so the disagreement
    stays visible rather than being quietly resolved.

 6. `country` IS SOMETIMES 25 COUNTRY NAMES IN ONE STRING, AND `iso3` IS
    JUST THE FIRST ONE. Event DR/1018332 has
    country = "Austria, Bosnia & Herzegovina, Belgium, ... Ukraine, , "
    (note the trailing empty segments) and iso3 = "AUT". Never render
    `country` as a label. We take the single primary country from
    `affectedcountries` and publish the full ISO3 list as `affected_iso3`.
    Note also that iso3 != affectedcountries[0].iso3 in 24 of 100 sampled
    events, so the array is NOT ordered with the primary first.

 7. SOME EVENTS HAVE NO COUNTRY AT ALL, LEGITIMATELY. Open-ocean cyclones
    (TC/1001314, TC/1001315) and mid-ocean quakes ("South Of Fiji Islands")
    come back with iso3 = "" and affectedcountries = []. These are NOT
    mapping failures and must not be dropped — they have valid coordinates
    and a cyclone three days off the coast is exactly what the map is for.
    They are printed under "no country" and kept with iso3 = null.

 8. NO USABLE population_affected ANYWHERE IN THE PUBLIC API. Three routes
    were checked and all three are contaminated:
      - severitydata: see trap 4, wrong units per type.
      - geteventdata `sendai`: records tagged sendainame="affected" include
        entries whose own description reads "826 [people] Out of contact in
        Bagmati Province" — that is not an affected-population count, and
        summing them would invent a number.
      - the RSS feed's <gdacs:population>: unit attribute varies across
        "in MMI VI", "people affected in the area" and "Population Affected"
        while the text reads "3 deaths and 9618 displaced". Also mixed.
    So `population_affected` is null on every row, on purpose, and the note
    says so. It is kept in the schema so the field can be filled later if
    GDACS ever publishes a consistent figure. Fetching 40+ detail endpoints
    to produce a number that means five different things would be worse than
    publishing nothing.

OUTPUT: data/gdacs.json
  { "data": { "FL_1104124": {"event_type": "FL", "alert_level": "Orange",
              "severity_score": 62, "iso3": "NPL", "lat": ..., "lng": ...,
              "from_date": "2026-08-26", "to_date": "2026-08-28",
              "is_current": true, "title": "...", "url": "...", ...} } }
"""
import json
import os
from datetime import date, datetime, timedelta

from _common import COUNTRY_COORDS, DATA_DIR, http_get, write_json

BASE = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
OUTPUT = "gdacs.json"

# See module docstring for the reasoning behind each of these.
INCLUDED_TYPES = ("DR", "FL", "TC")
OPTIONAL_TYPES = ("EQ", "WF", "VO")
ALERT_LEVELS = "Orange;Red"

TYPE_LABELS = {
    "DR": "Drought", "FL": "Flood", "TC": "Tropical Cyclone",
    "EQ": "Earthquake", "WF": "Wildfire", "VO": "Volcano",
}

# The list query filters and orders on todate (trap 3), so this reads as
# "ended within the last 90 days, or has not ended yet". 90 days is chosen to
# match a single cropping-season quarter: long enough that a flood in the
# sowing window is still on the map when its effect shows up in the harvest,
# short enough that the file stays small and nothing from last season
# masquerades as news.
LOOKBACK_DAYS = 90

# is_current window. An event counts as current if its todate has not yet
# passed, or passed within the last CURRENT_GRACE_DAYS.
#
# Why 7 days, and not "todate >= today":
#   - the upstream models close an event window when they stop flagging it,
#     which for GLOFAS is same-day but for GDO's drought product runs on a
#     ~10-day dekadal cycle, so a hard cutoff would flicker droughts on and
#     off between runs;
#   - the food-security effect of a flood does not end when the water does.
#     A crop drowned on Tuesday is still lost on Sunday.
# Why not longer: the whole point is that the layer must not resurrect a
# cyclone from three months ago as current. Seven days cannot do that;
# thirty could start to.
#
# Note there is deliberately NO lower bound on fromdate. A cyclone whose
# forecast window is entirely in the future is current — that is precisely
# what a live-disturbance layer is for.
CURRENT_GRACE_DAYS = 7

PAGE_SIZE = 100          # API maximum; larger values are silently ignored.
MAX_PAGES = 10           # 1000 events per type is far past any plausible run.

# --- completeness guards (see _guard_completeness) -------------------------
# A 90-day Orange+Red window has never been observed thinner than ~16 events
# for DR+FL+TC. Six is a floor low enough to survive a genuinely quiet
# northern winter and high enough to catch a half-broken upstream.
MIN_TOTAL_EVENTS = 6
# If a previous good file exists, a run returning less than this fraction of
# it is treated as a partial fetch, not as a quiet world. A sibling script in
# this repo already overwrote a complete file with a thin one because the
# partial run "succeeded"; that must not happen here.
MIN_FRACTION_OF_PREVIOUS = 0.4

# Alert band -> base severity_score. GDACS's alert level is the only figure
# in the feed that is comparable across hazard types (trap 4), so it is the
# only honest basis for a 0-100 number. Bands do not overlap after the
# intra-band refinement below, so sorting by severity_score never reorders
# an event above one with a higher alert level.
ALERT_BASE = {"Green": 20, "Orange": 55, "Red": 85}
ALERT_SPAN = 15  # points of intra-band refinement from episodealertscore


def _fetch_type(event_type, from_date):
    """Return the raw feature list for one event type, paginating fully.

    Queried one type at a time on purpose: a single noisy type (FL in monsoon
    season, EQ always) would otherwise crowd every other type out of the
    100-row page, and because results are ordered by todate desc that
    crowding-out would be completely invisible in the response.
    """
    features = []
    for page in range(1, MAX_PAGES + 1):
        params = {
            "eventlist": event_type,
            "alertlevel": ALERT_LEVELS,
            "fromDate": from_date.isoformat(),
            "pageSize": PAGE_SIZE,
            "pageNumber": page,
        }
        r = http_get(BASE, params=params, timeout=60, retries=3)
        # Trap 1: zero matches is 204 with an empty body, not an empty
        # FeatureCollection. Parsing this would raise a JSONDecodeError that
        # reads like an upstream outage.
        if r.status_code == 204 or not (r.text or "").strip():
            break
        batch = (r.json() or {}).get("features") or []
        features.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
    else:
        # Loop ran MAX_PAGES times without a short page — we are still being
        # truncated. Refuse rather than publish an unknown fraction of the feed.
        raise RuntimeError(
            f"{event_type}: still full at page {MAX_PAGES} "
            f"({len(features)} events) — pagination did not terminate"
        )
    print(f"  [ok] {event_type} ({TYPE_LABELS.get(event_type, '?')}): "
          f"{len(features)} events at {ALERT_LEVELS}")
    return features


def _iso3_and_country(props, unmapped, no_country):
    """Resolve one authoritative (iso3, country_name) pair. Never drops a row.

    GDACS supplies ISO3 directly, so this is a validation step rather than a
    name-matching step — but `iso3` is the first of up to 25 countries
    (trap 6) and is sometimes absent entirely (trap 7), so both cases have to
    be handled explicitly and reported rather than silently coerced.
    """
    affected = [c for c in (props.get("affectedcountries") or []) if c]
    affected_iso3 = [
        (c.get("iso3") or "").strip().upper() for c in affected
        if (c.get("iso3") or "").strip()
    ]

    iso3 = (props.get("iso3") or "").strip().upper()
    if not (len(iso3) == 3 and iso3.isalpha()):
        # Fall back to the array before giving up.
        iso3 = affected_iso3[0] if affected_iso3 else ""

    label = f"{props.get('eventtype')}/{props.get('eventid')}"
    raw_country = (props.get("country") or "").strip()

    if not iso3:
        # Legitimately country-less: open ocean cyclones, mid-ocean quakes.
        # Kept, with iso3 null, and reported so the count is never a surprise.
        no_country.append(f"{label} {raw_country or '(no country given)'}")
        return None, (raw_country or None), affected_iso3

    # Prefer the clean single name from the array over the comma-joined blob.
    name = None
    for c in affected:
        if (c.get("iso3") or "").strip().upper() == iso3:
            name = (c.get("countryname") or "").strip() or None
            break
    if not name and raw_country:
        # First non-empty comma segment. The blob ends in ", , " for the
        # 25-country European drought, so empty segments must be skipped.
        name = next((s.strip() for s in raw_country.split(",") if s.strip()), None)

    # COUNTRY_COORDS is this repo's canonical sovereign-state ISO3 set. An
    # ISO3 outside it is usually a dependent territory (PRI, GUM, REU) which
    # is fine to plot but will not join to any country-level dataset — so it
    # is printed rather than dropped.
    if iso3 not in COUNTRY_COORDS:
        unmapped.append(f"{label} iso3={iso3} name={name!r}")

    return iso3, name, affected_iso3


def _severity_score(props):
    """0-100 rendering weight derived from GDACS's own alert band.

    NOT an independent severity model, and explicitly NOT derived from
    `severitydata.severity` — see trap 4. The alert band gives the coarse
    0-100 position; episodealertscore (a continuous 0-3 figure for the most
    recent episode) refines within the band so that the "most severe" sort is
    stable instead of arbitrary among the dozens of events sharing a band.
    """
    base = ALERT_BASE.get((props.get("alertlevel") or "").strip(), 20)
    try:
        eas = float(props.get("episodealertscore") or 0.0)
    except (TypeError, ValueError):
        eas = 0.0
    eas = min(max(eas, 0.0), 3.0)
    return int(round(base + ALERT_SPAN * eas / 3.0))


def _title(props, country, affected_iso3):
    """Short map label. See the comment at the call site for why."""
    raw = (props.get("name") or props.get("description") or "").strip()
    # Only rewrite when the name really is the pasted country blob. The
    # length test matters: a cyclone's name is "Tropical Cyclone SAUDEL-26",
    # multi-country but short and carrying the storm designation, which a
    # blind rewrite would throw away. 60 chars separates every observed real
    # name from every observed blob.
    if len(affected_iso3) > 1 and country and len(raw) > 60:
        label = TYPE_LABELS.get(
            (props.get("eventtype") or "").strip().upper(), "Event")
        others = len(affected_iso3) - 1
        return (f"{label} in {country} "
                f"+{others} more {'country' if others == 1 else 'countries'}")
    return raw or None


def _iso_day(value):
    """'2026-08-26T01:00:00' -> date(2026, 8, 26). None on anything else."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _row(feature, today, unmapped, no_country):
    props = (feature or {}).get("properties") or {}
    geom = (feature or {}).get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None, None
    lng, lat = float(coords[0]), float(coords[1])

    event_type = (props.get("eventtype") or "").strip().upper()
    event_id = props.get("eventid")
    if not event_type or event_id is None:
        return None, None

    iso3, country, affected_iso3 = _iso3_and_country(props, unmapped, no_country)
    from_day, to_day = _iso_day(props.get("fromdate")), _iso_day(props.get("todate"))

    # See CURRENT_GRACE_DAYS. One rule, dates only, no dependence on GDACS's
    # own iscurrent (trap 5) — which is carried alongside for comparison.
    is_current = bool(to_day and to_day >= today - timedelta(days=CURRENT_GRACE_DAYS))

    # GLOFAS publishes no scalar severity, but GDACS still emits a filled-in
    # placeholder for it: severity 0.0, unit "", text "Magnitude 0 ". That
    # text is worse than nothing — it reads like a measured zero. Where the
    # unit is blank and the value is 0, treat the whole block as absent.
    sev = props.get("severitydata") or {}
    sev_unit = (sev.get("severityunit") or "").strip() or None
    sev_value = sev.get("severity")
    sev_text = (sev.get("severitytext") or "").strip() or None
    if sev_unit is None and not sev_value:
        sev_value, sev_text = None, None

    urls = props.get("url") or {}

    key = f"{event_type}_{event_id}"
    row = {
        "event_type": event_type,
        "event_type_label": TYPE_LABELS.get(event_type, event_type),
        "alert_level": (props.get("alertlevel") or "").strip() or None,
        "severity_score": _severity_score(props),
        "iso3": iso3,
        "country": country,
        "affected_iso3": affected_iso3,
        "lat": round(lat, 4),
        "lng": round(lng, 4),
        "from_date": from_day.isoformat() if from_day else None,
        "to_date": to_day.isoformat() if to_day else None,
        "is_current": is_current,
        # Always null — see trap 8. Kept in the schema, not invented.
        "population_affected": None,
        # GDACS's own per-hazard figure, carried raw and unmixed. The unit
        # differs by event_type (km2 / M / km/h / ha) and these must never be
        # compared across types.
        "severity_value": sev_value,
        "severity_unit": sev_unit,
        "severity_text": sev_text,
        # GDACS builds `name` by pasting the whole country blob after the
        # hazard word, so a 25-country drought's name is a 300-character
        # sentence ending in ", , " — unusable as a map label (trap 6). For
        # multi-country events we rebuild it from the primary country and a
        # count; single-country names are left exactly as GDACS wrote them.
        "title": _title(props, country, affected_iso3),
        "url": urls.get("report") or (
            f"https://www.gdacs.org/report.aspx?eventid={event_id}"
            f"&eventtype={event_type}"
        ),
        "glide": (props.get("glide") or "").strip() or None,
        "episode_id": props.get("episodeid"),
        "feed_source": (props.get("source") or "").strip() or None,
        "gdacs_iscurrent": str(props.get("iscurrent")).lower() == "true",
        "date_modified": props.get("datemodified"),
        "source": "GDACS",
        "source_url": "https://www.gdacs.org/",
        "quality_flag": "sourced",
    }
    return key, row


def _previous_row_count():
    """How many rows the last good data/gdacs.json carried, or 0."""
    try:
        obj = json.loads((DATA_DIR / OUTPUT).read_text())
    except Exception:
        return 0
    payload = obj.get("data") if isinstance(obj, dict) else None
    return len(payload) if isinstance(payload, dict) else 0


def _guard_completeness(out, per_type):
    """Raise rather than overwrite a good file with a thin one.

    A refresh that returns three events is far more likely to be a
    half-answering upstream than a quiet planet, and safe_run preserves the
    previous file when this raises. Failing loudly beats publishing a map
    that looks calm because the fetch broke.
    """
    if not out:
        raise RuntimeError(
            "GDACS returned zero events across all requested types — "
            "that is an upstream failure, not a quiet week"
        )

    # DR and FL are the two continuously-populated products (GDO runs a
    # standing drought assessment, GLOFAS a daily global flood model). Both
    # empty at once has no natural cause.
    always_on = [t for t in ("DR", "FL") if t in per_type]
    if always_on and all(per_type[t] == 0 for t in always_on):
        raise RuntimeError(
            f"GDACS returned zero events for every always-on type "
            f"({', '.join(always_on)}) — upstream is not answering properly"
        )

    if len(out) < MIN_TOTAL_EVENTS:
        raise RuntimeError(
            f"GDACS returned only {len(out)} events over {LOOKBACK_DAYS} days "
            f"(floor is {MIN_TOTAL_EVENTS}) — refusing to publish a thin file"
        )

    previous = _previous_row_count()
    if previous >= 10 and len(out) < previous * MIN_FRACTION_OF_PREVIOUS:
        raise RuntimeError(
            f"GDACS returned {len(out)} events against {previous} in the "
            f"existing file (<{int(MIN_FRACTION_OF_PREVIOUS * 100)}%) — "
            f"treating as a partial fetch and refusing to overwrite"
        )


def _print_reference(out, per_type, unmapped, no_country):
    by_type, by_alert = {}, {}
    for row in out.values():
        by_type[row["event_type"]] = by_type.get(row["event_type"], 0) + 1
        by_alert[row["alert_level"]] = by_alert.get(row["alert_level"], 0) + 1

    current = [r for r in out.values() if r["is_current"]]
    print(f"\n[INFO] GDACS: {len(out)} events over the last {LOOKBACK_DAYS} days "
          f"| {len(current)} current (todate within {CURRENT_GRACE_DAYS}d)")
    print("  by type:  " + "  ".join(
        f"{t}={by_type.get(t, 0)}" for t in sorted(by_type)))
    print("  by alert: " + "  ".join(
        f"{a}={by_alert[a]}" for a in sorted(by_alert, key=lambda x: x or "")))
    print("  queried:  " + "  ".join(
        f"{t}:{n}" for t, n in per_type.items()))

    print("  [ref] 5 most severe CURRENT events:")
    top = sorted(current, key=lambda r: (-r["severity_score"], r["to_date"] or ""))[:5]
    for r in top:
        where = r["country"] or "(no country)"
        if r["iso3"]:
            where = f"{where} [{r['iso3']}]"
        print(f"    {r['severity_score']:3d} {r['alert_level']:<6} "
              f"{r['event_type']} {where} — {r['title']} "
              f"({r['from_date']} to {r['to_date']})")
    if not top:
        print("    (none current)")

    # Trap 6/7 reporting: never a silent drop.
    if no_country:
        print(f"  [note] {len(no_country)} event(s) with no country — kept with "
              f"iso3=null (open ocean / regional, this is normal):")
        for line in no_country:
            print(f"    - {line}")
    if unmapped:
        print(f"  [warn] {len(unmapped)} event(s) whose ISO3 is not in the core "
              f"country set (dependent territory — plots fine, will not join "
              f"country datasets):")
        for line in unmapped:
            print(f"    - {line}")
    if not unmapped and not no_country:
        print("  [ok] every event resolved to a known ISO3")


def main():
    types_env = (os.environ.get("FOODSHIELD_GDACS_TYPES") or "").strip()
    if types_env:
        types = tuple(t.strip().upper() for t in types_env.replace(",", ";").split(";") if t.strip())
        unknown = [t for t in types if t not in TYPE_LABELS]
        if unknown:
            raise RuntimeError(f"unknown GDACS event type(s): {unknown}")
    else:
        types = INCLUDED_TYPES

    today = date.today()
    from_date = today - timedelta(days=LOOKBACK_DAYS)
    print(f"[INFO] GDACS: types={';'.join(types)} alert={ALERT_LEVELS} "
          f"fromDate={from_date} (filters on event todate — see trap 3)")

    out, per_type, unmapped, no_country = {}, {}, [], []
    try:
        for event_type in types:
            features = _fetch_type(event_type, from_date)
            per_type[event_type] = len(features)
            for feature in features:
                key, row = _row(feature, today, unmapped, no_country)
                if key:
                    out[key] = row
        _guard_completeness(out, per_type)
    except Exception as e:
        # safe_run preserves the last-good file when this raises, but ONLY if
        # a non-trivial file is on disk. Writing an empty envelope
        # unconditionally would destroy exactly the data safe_run is trying to
        # protect (_has_existing_data would then see an empty payload), so the
        # stub is written only when there is nothing to preserve — which is
        # also what makes this correct when run standalone, outside safe_run.
        if _previous_row_count() == 0:
            write_json(OUTPUT, {}, source="GDACS",
                       notes=f"GDACS refresh failed: {e}", status="fetch_failed")
        else:
            print(f"[KEEP] preserving existing {OUTPUT} "
                  f"({_previous_row_count()} events) — not overwriting with a "
                  f"failed run")
        raise

    _print_reference(out, per_type, unmapped, no_country)

    included = ", ".join(f"{t} ({TYPE_LABELS[t]})" for t in types)
    omitted = ", ".join(f"{t} ({TYPE_LABELS[t]})" for t in TYPE_LABELS if t not in types)
    write_json(
        OUTPUT, out,
        source="GDACS (Global Disaster Alert and Coordination System, JRC/UN)",
        status="ok",
        notes=(
            f"Live hazard events from the keyless GDACS event API, one row per "
            f"event. Included: {included} — the hazard classes that move food "
            f"security directly, through lost production (drought), drowned "
            f"crops and cut feeder roads (flood), and flattened pre-harvest "
            f"crops and closed ports (tropical cyclone). "
            f"Omitted: {omitted} — earthquake is available behind "
            f"FOODSHIELD_GDACS_TYPES but is off by default because its food "
            f"impact runs through logistics already covered by LPI and the "
            f"trade layers, and it is frequent enough to swamp the map; "
            f"wildfire measures burned forest hectares, a poor cropland proxy, "
            f"and remains a genuine coverage gap left by the dead NASA FIRMS "
            f"feed. "
            f"Alert levels Orange and Red only — GDACS Green means no "
            f"significant impact expected and outnumbers the rest ~15:1. "
            f"Window: events whose end date falls within the last "
            f"{LOOKBACK_DAYS} days, or which have not ended. is_current is "
            f"derived here from the event dates (end date not more than "
            f"{CURRENT_GRACE_DAYS} days past, or still in the future), NOT "
            f"from GDACS's own iscurrent flag, which means 'in the live "
            f"homepage feed' and reads false for droughts that are running "
            f"today; their flag is preserved as gdacs_iscurrent. "
            f"severity_score (0-100) is a rendering weight derived from the "
            f"GDACS alert band, the only cross-hazard-comparable figure in "
            f"the feed; the raw severity_value carries incompatible units per "
            f"type (km2 for drought, magnitude for quake, km/h for cyclone, "
            f"hectares for fire, and nothing at all for flood) and must never "
            f"be compared across types. population_affected is null on every "
            f"row: GDACS publishes no consistent affected-population figure "
            f"in this API — the Sendai records mix 'out of contact' and "
            f"'rescued' counts into the 'affected' tag and the RSS population "
            f"field mixes deaths, displaced and shaking-intensity exposure — "
            f"so no number is published rather than an invented one. "
            f"Counts: " + ", ".join(f"{t}={per_type.get(t, 0)}" for t in types)
            + f". {len([r for r in out.values() if r['is_current']])} current."
        ),
    )


if __name__ == "__main__":
    main()
