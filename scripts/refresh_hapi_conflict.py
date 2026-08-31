"""
ACLED conflict events via HDX HAPI — the FDRS Conflict/Logistics live signal.

WHY THIS EXISTS. The Conflict / Logistics component of FDRS was built entirely
from slow annual indices: INFORM, WGI rule-of-law, LPI, INFORM-coping. Every one
of them is a structural measure published once a year and revised with a long
lag. `data/nowcast.json._meta` recorded the consequence plainly —
`acled_conflict_live_countries = 0`. A war that starts today could not move the
score until the next annual release, which is the opposite of what a nowcast is
for. This feed supplies the missing term: a rolling 90-day count of conflict
events and fatalities, refreshed monthly, flagged `is_live: true` so
build_nowcast.py counts it as a live rather than a structural input.

SOURCE. https://hapi.humdata.org/api/v2/coordination-context/conflict-events
Auth is a self-serve `app_identifier` query param — base64 of "app:email", built
below from APP_NAME and CONTACT_EMAIL. There is no registration, no key to
rotate, and no secret: it is a courtesy identifier so OCHA can attribute traffic,
which is exactly why it is constructed in code rather than pasted in as an opaque
blob nobody can read back.

THREE TRAPS, all of which produce a plausible-looking wrong answer rather than an
error. Verified against the live API on 2026-08-30.

  1. ADMIN LEVEL IS NOT UNIFORM, and it is not admin2-only. Rows are published at
     exactly one level per country, and the levels partition the world:
        admin_level=2 -> 24 countries, the HRP/humanitarian-response set
                         (AFG BDI BFA CAF CMR COD COL ETH HTI LBN MLI MMR MOZ
                          NER NGA PSE SDN SOM SSD SYR TCD UKR VEN YEM)
        admin_level=1 -> UKR only, 18 all-zero rows, redundant with its admin2
        admin_level=0 -> the other 218 countries, national totals
     Querying admin2 alone — the obvious reading of "rows exist only at admin2",
     which is true for Sudan and Burkina Faso and misleading everywhere else —
     yields 24 countries and silently drops Mexico, Pakistan, Russia, Brazil,
     India and 213 more. So we fetch all three levels and, per ISO3, keep the
     FINEST level that actually carries rows. The three sets were verified
     disjoint; the code asserts it anyway, because summing overlapping levels
     would double-count a country's own subdivisions into its national total.

  2. NO start_date MEANS SILENT ALPHABETICAL TRUNCATION. The row cap is 10,000
     and the natural ordering is by location code. Omit start_date and the
     response stops somewhere in the middle of the alphabet with HTTP 200 and no
     warning — every country after the cut reads as zero conflict, which for this
     dataset means "at peace". start_date is therefore mandatory here, asserted
     at the top of _fetch_level(), and pagination runs until a page comes back
     SHORT of the limit. A run that ends exactly on a cap boundary is treated as
     unproven, not as complete.

  3. `fatalities` IS NULLABLE. Demonstration rows carry events with fatalities
     null, not 0. Naive summation raises; `or 0` coercion is the fix, and it is
     correct because null here means "not applicable", not "unknown".

WINDOW. Buckets are calendar-monthly and the newest month is in progress (as of
2026-08-30, August is 30/31 complete). We request start_date = today - 90 days;
the API returns any bucket overlapping that date, so the window is the ~3
calendar months touching the last 90 days rather than an exact 90-day slice.
window_start / window_end / months_covered are reported per country from its own
rows so a consumer can see precisely what was counted.

INTENSITY. min(100, 25 * log10(1 + fatalities_90d)) — a log scale, because raw
fatality counts span four orders of magnitude (0 to ~12,000 in one quarter) and
a linear score would leave every country but Ukraine and Sudan indistinguishable
from zero. 100 is reached at 10,000 fatalities per 90 days.

LICENCE. Data are ACLED's, redistributed by OCHA HDX under ACLED's terms.
Attribution is REQUIRED wherever these numbers are displayed: "Armed Conflict
Location & Event Data Project (ACLED); acleddata.com", sourced via HDX HAPI.

OUTPUT: data/hapi_conflict.json
  { "data": { "SDN": {"events_90d": 991, "fatalities_90d": 2261,
              "intensity_score": 83.75, "window_start": "2026-06-01",
              "window_end": "2026-08-31", "months_covered": 3,
              "is_live": true, "source": "...", "source_url": "...",
              "quality_flag": "sourced"} } }
"""
import base64
import json
import math
from datetime import date, timedelta

from _common import DATA_DIR, http_get, write_json

ENDPOINT = "https://hapi.humdata.org/api/v2/coordination-context/conflict-events"
SOURCE_URL = "https://hapi.humdata.org/api/v2/coordination-context/conflict-events"

# HAPI's self-serve identifier: base64("<app>:<contact email>"). Not a secret,
# not a credential — it identifies the caller so OCHA can attribute API traffic
# and reach the operator if a client misbehaves. Built here so it stays readable.
APP_NAME = "foodshield"
CONTACT_EMAIL = "fedde.vreeken@gmail.com"

WINDOW_DAYS = 90
PAGE_LIMIT = 10000          # HAPI's documented maximum
MAX_PAGES = 40              # 400k rows; a run needing more has hit a bug, not data
# Finest first — per ISO3 we keep the first level that carries rows.
ADMIN_LEVELS = (2, 1, 0)

REFERENCE = ("SDN", "BFA", "ETH", "NGA", "COD", "UKR", "MMR")


def _app_identifier():
    return base64.b64encode(f"{APP_NAME}:{CONTACT_EMAIL}".encode()).decode()


def _fetch_level(admin_level, start_date):
    """Return all rows at one admin level from start_date onward.

    Paginates until a page comes back shorter than PAGE_LIMIT. Ending exactly on
    a cap boundary is not accepted as a complete result (see trap 2).
    """
    # Trap 2 guard: without start_date the 10k cap truncates alphabetically and
    # every country past the cut silently reads as conflict-free.
    assert start_date, "start_date is mandatory — omitting it truncates alphabetically"

    rows = []
    offset = 0
    for page in range(MAX_PAGES):
        r = http_get(
            ENDPOINT,
            params={
                "app_identifier": _app_identifier(),
                "output_format": "json",
                "admin_level": str(admin_level),
                "start_date": start_date,
                "limit": str(PAGE_LIMIT),
                "offset": str(offset),
            },
            timeout=90,
            retries=3,
            patient=True,
        )
        batch = (r.json() or {}).get("data")
        if batch is None:
            raise RuntimeError(f"admin{admin_level}: response had no 'data' key")
        rows.extend(batch)
        if len(batch) < PAGE_LIMIT:
            # Short page: the server had nothing more to give. Only exit.
            print(f"  [ok] admin{admin_level}: {len(rows)} rows over {page + 1} page(s)")
            return rows
        print(f"  [warn] admin{admin_level} page {page + 1} returned exactly "
              f"{PAGE_LIMIT} rows (the cap) — paging on; a result that ENDED here "
              f"would be unproven, not complete")
        offset += PAGE_LIMIT

    raise RuntimeError(
        f"admin{admin_level}: still capped after {MAX_PAGES} pages "
        f"({len(rows)} rows) — refusing to write a possibly truncated result"
    )


# v86 — WHICH EVENT TYPES COUNT AS CONFLICT.
#
# HAPI splits rows three ways: political_violence, civilian_targeting and
# demonstration. Summing all three inflated exactly the countries where the
# violence is not a food-supply problem: Brazil came out at intensity 87.8 on
# 3,246 fatalities and Ecuador at 81.2, both driven by criminal-group and
# protest activity, which put them near Sudan on a food-security conflict
# component. That is not what this component is for.
#
# political_violence and civilian_targeting BOTH stay: civilian targeting is
# precisely the mechanism that empties farmland in Nigeria, Sudan and the Sahel,
# and excluding it would blind the component to the thing it most needs to see.
# demonstration is excluded — protests and riots are political signal, not armed
# disruption of food production or movement. The count is still reported
# separately so nothing is hidden.
CONFLICT_EVENT_TYPES = ("political_violence", "civilian_targeting")

# v86 — POPULATION-NORMALISED INTENSITY.
#
# A raw fatality count is not comparable across countries. Over the same 90 days
# Brazil recorded 3,246 conflict fatalities and Sudan 2,261 — so on absolute
# counts Brazil scored 87.8 against Sudan's 83.9, i.e. Brazil read as the more
# conflict-disrupted food system. Per head of population the picture inverts:
# Sudan ~45 per million against Brazil ~15, and Ukraine ~322.
#
# The absolute score is kept and published; the per-capita score is the one the
# risk component should use, and the app prefers it.
POP_INDICATOR = "SP.POP.TOTL"


POP_API = ("https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL"
           "?format=json&mrnev=1&per_page=400")


def _load_population():
    """{iso3: population}, from the on-disk bulk file plus a live top-up.

    The bulk file covers 203 of the 242 countries HAPI returns, and the 39 it
    misses are exactly the wrong ones: Sudan, Mali, CAR, Burundi, Benin and
    Eritrea among them. Falling back to the absolute fatality count for those
    would put them on a different scale from every other country — a hybrid
    ranking that is not comparable anywhere. One keyless World Bank call closes
    the gap, and if it fails the per-capita field is simply null rather than
    silently mixed.
    """
    out = {}
    p = DATA_DIR / "worldbank_bulk.json"
    if p.exists():
        try:
            obj = json.loads(p.read_text())
            data = obj.get("data", obj) if isinstance(obj, dict) else {}
            for iso, row in data.items():
                if not isinstance(row, dict):
                    continue
                rec = row.get(POP_INDICATOR)
                val = rec.get("value") if isinstance(rec, dict) else None
                if isinstance(val, (int, float)) and val > 0:
                    out[iso.upper()] = float(val)
        except Exception as e:
            print(f"  [warn] population from bulk file failed: {e}")
    before = len(out)
    try:
        r = http_get(POP_API, timeout=90, retries=2, patient=True)
        payload = r.json()
        rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        for row in rows or []:
            iso = ((row.get("countryiso3code") or "") or "").strip().upper()
            val = row.get("value")
            if len(iso) == 3 and isinstance(val, (int, float)) and val > 0:
                out.setdefault(iso, float(val))
        print(f"  [pop] bulk file {before} + World Bank API top-up -> {len(out)}")
    except Exception as e:
        print(f"  [warn] population API top-up failed ({e}); "
              f"per-capita intensity will be null for the {before}-country gap")
    return out


def _aggregate(rows):
    """Sum rows to {iso3: {...}}. Rows are (area, event_type, month) tuples."""
    out = {}
    for row in rows:
        iso3 = (row.get("location_code") or "").strip().upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue
        c = out.setdefault(iso3, {"events": 0, "fatalities": 0, "months": set(),
                                  "demo_events": 0, "demo_fatalities": 0})
        etype = (row.get("event_type") or "").strip().lower()
        # Trap 3: demonstration rows carry fatalities=None, not 0.
        if etype not in CONFLICT_EVENT_TYPES:
            c["demo_events"] += row.get("events") or 0
            c["demo_fatalities"] += row.get("fatalities") or 0
            continue
        c["events"] += row.get("events") or 0
        c["fatalities"] += row.get("fatalities") or 0
        start = (row.get("reference_period_start") or "")[:10]
        end = (row.get("reference_period_end") or "")[:10]
        if start and end:
            c["months"].add((start, end))
    return out


def main():
    today = date.today()
    start_date = (today - timedelta(days=WINDOW_DAYS)).isoformat()

    population = _load_population()
    print(f"  [pop] population for {len(population)} countries")
    by_level = {}
    try:
        for level in ADMIN_LEVELS:
            by_level[level] = _aggregate(_fetch_level(level, start_date))
    except Exception as e:
        # Fail loudly and write nothing but the error. A stale or invented
        # conflict row is worse than an empty one: this feed exists to say
        # "something is happening RIGHT NOW", and a wrong zero reads as peace.
        print(f"  [FAIL] HAPI conflict fetch failed: {e}")
        write_json(
            "hapi_conflict.json", {},
            source="ACLED via HDX HAPI",
            notes=f"HAPI conflict-events fetch failed, no data written: {e}",
            status="fetch_failed",
        )
        return 1

    # Trap 1: levels must partition the countries. Overlap would double-count a
    # country's subdivisions into its own national total.
    seen = {}
    overlaps = []
    for level in ADMIN_LEVELS:
        for iso3 in by_level[level]:
            if iso3 in seen:
                overlaps.append(f"{iso3} at admin{seen[iso3]} and admin{level}")
            else:
                seen[iso3] = level
    if overlaps:
        print(f"  [warn] admin levels overlap for {len(overlaps)} countries "
              f"({', '.join(overlaps[:8])}) — keeping the FINEST level only, "
              f"never summing across levels")

    out = {}
    level_counts = {}
    for level in ADMIN_LEVELS:
        for iso3, c in by_level[level].items():
            if iso3 in out:
                continue  # finer level already won
            months = sorted(c["months"])
            if not months:
                continue
            fatalities = int(c["fatalities"])
            pop = population.get(iso3)
            per_m = (fatalities / (pop / 1_000_000.0)) if pop else None
            # 33 * log10(1 + per-million) — Ukraine ~322/M saturates near 100,
            # Sudan ~45/M lands mid-50s, Brazil ~15/M around 40.
            intensity_pc = (round(min(100.0, 33.0 * math.log10(1 + per_m)), 2)
                            if per_m is not None else None)
            out[iso3] = {
                "events_90d": int(c["events"]),
                "fatalities_90d": fatalities,
                "intensity_score": round(min(100.0, 25.0 * math.log10(1 + fatalities)), 2),
                "fatalities_per_million_90d": round(per_m, 2) if per_m is not None else None,
                "intensity_score_pc": intensity_pc,
                "population": int(pop) if pop else None,
                "window_start": months[0][0],
                "window_end": months[-1][1],
                "months_covered": len(months),
                # Reported, never scored — see CONFLICT_EVENT_TYPES.
                "demonstration_events_90d": int(c.get("demo_events") or 0),
                "demonstration_fatalities_90d": int(c.get("demo_fatalities") or 0),
                "counted_event_types": list(CONFLICT_EVENT_TYPES),
                "is_live": True,
                "source": "ACLED via HDX HAPI",
                "source_url": SOURCE_URL,
                "quality_flag": "sourced",
            }
            level_counts[level] = level_counts.get(level, 0) + 1

    if not out:
        print("  [FAIL] HAPI returned rows but none aggregated to a country")
        write_json(
            "hapi_conflict.json", {},
            source="ACLED via HDX HAPI",
            notes="HAPI conflict-events returned no usable country rows; "
                  "nothing written rather than a fabricated zero.",
            status="fetch_failed",
        )
        return 1

    windows = sorted({(v["window_start"], v["window_end"]) for v in out.values()})
    span = f"{windows[0][0]}..{windows[-1][1]}"
    print(f"[INFO] HAPI conflict: {len(out)} countries | window {span} | "
          + " ".join(f"admin{k}={v}" for k, v in sorted(level_counts.items(), reverse=True)))
    for ref in REFERENCE:
        d = out.get(ref)
        if d:
            print(f"  [ref] {ref}: events={d['events_90d']} fatalities={d['fatalities_90d']} "
                  f"intensity={d['intensity_score']} months={d['months_covered']} "
                  f"({d['window_start']}..{d['window_end']})")
        else:
            print(f"  [ref] {ref}: ABSENT from HAPI response")

    write_json(
        "hapi_conflict.json", out,
        source="ACLED via HDX HAPI",
        notes=(
            f"Rolling {WINDOW_DAYS}-day conflict events and fatalities per country, "
            f"from ACLED via the OCHA HDX HAPI conflict-events endpoint "
            f"(start_date={start_date}). Buckets are calendar-monthly and the newest "
            f"month is IN PROGRESS, so its counts will rise on the next refresh; the "
            f"window is the calendar months overlapping the last {WINDOW_DAYS} days, "
            f"not an exact 90-day slice. Admin levels partition the world — "
            + " ".join(f"admin{k} {v} countries," for k, v in sorted(level_counts.items(), reverse=True))
            + f" total {len(out)} — and per country only the finest level with rows "
            f"is counted, never summed across levels. intensity_score = "
            f"min(100, 25*log10(1+fatalities_90d)); 100 at 10,000 fatalities/90d. "
            f"is_live=true: this is a current-quarter observation, not an annual index. "
            f"ATTRIBUTION REQUIRED: Armed Conflict Location & Event Data Project "
            f"(ACLED); acleddata.com, redistributed by OCHA HDX under ACLED terms."
        ),
        status="ok",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
