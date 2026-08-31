"""
IMF PortWatch — daily chokepoint transit counts, measured not narrated.

WHY THIS EXISTS. data/chokepoints.json is hand-written and was last touched
2026-06-15. Its prose has since gone wrong in the two places that matter most:
it calls the Strait of Hormuz an "April 2026 tensions" story while transits
there are down ~95% year-on-year, and it describes Panama's 2023-24 drought as
resolved while Panama's DRY BULK transits — the grain-relevant segment — are
down ~40%. A hand-edited chokepoint layer cannot track a moving disruption.
This feed replaces the measurable half of that file. The editorial half
(why it matters, which commodities, what to watch) stays hand-written.

SOURCE. IMF PortWatch, anonymous ArcGIS FeatureServer, no key:
  daily    Daily_Chokepoints_Data/FeatureServer/0
  metadata PortWatch_chokepoints_database/FeatureServer/0
28 chokepoints, daily AIS-derived transit counts from 2019-01-01. IMF terms
permit redistribution with attribution.

THE QUERY. History is 78k+ rows (~32 MB) and we need 56 days of it, so this
does NOT download the archive. It sends ONE where clause covering both windows:

    (date >= DATE '2026-07-27' AND date <= DATE '2026-08-23')
 OR (date >= DATE '2025-07-27' AND date <= DATE '2025-08-23')

`date` is esriFieldTypeDateOnly and accepts the `DATE 'YYYY-MM-DD'` literal.
That is 28 chokepoints x 56 days = 1568 rows, so it still pages past the
maxRecordCount of 1000 — resultOffset/resultRecordCount pagination is real,
not decorative. The window end is not hardcoded: it is read from a max(date)
outStatistics call, because PortWatch publishes with a ~7-day lag that drifts.

YEAR-ON-YEAR, NOT ROLLING. The baseline is the SAME CALENDAR WINDOW one year
earlier, not a trailing average. Shipping seasonality is real (grain harvest
timing, monsoon, Northern Hemisphere winter), and a rolling baseline would
launder a seasonal trough into a "disruption" and, worse, would slowly absorb
a genuine collapse into its own baseline until Hormuz at -95% looked normal.

STATUS THRESHOLDS (a judgement call, stated so it can be argued with):
  collapsed  total transits down >= 50% YoY.
             Half the traffic gone is not congestion, it is rerouting or
             closure. Hormuz at -94.6% lands here and cannot land anywhere
             else — no other rule can override a collapsed verdict.
  disrupted  total down >= 10% YoY, OR any single vessel segment
             (dry bulk / tanker / container) down >= 25% YoY.
             The segment clause exists FOR Panama: total -4.8% reads calm
             while dry bulk is -39.8%, and dry bulk is the food-security
             segment. A total-only rule would have missed the thing this
             dashboard is for. 10% is set just below Bab el-Mandeb's -13.5%
             deliberately: the Red Sea diversion is a real disruption and a
             15% threshold would have called it normal.
  normal     everything else.
  unknown    no usable baseline (chokepoint absent from last year's window).
A non-normal status additionally requires a baseline of >= 1.0 transits/day.
Below that, percentage change is noise: Bering Strait averages ~0.1/day and a
single extra vessel is a "+900% surge". Those stay `normal` and carry
`low_volume: true` plus their baseline so the caller can see the denominator.

FAILS LOUDLY. Any fetch or parse failure writes an EMPTY payload with the
error in _meta.notes rather than a partial or stale-looking one.

OUTPUT: data/portwatch.json
  { "data": { "hormuz": {"name": "Strait of Hormuz",
                         "portwatch_id": "chokepoint6", "window_days": 28,
                         "transits_per_day": {"total": 5.1, ...},
                         "baseline_transits_per_day": {...},
                         "yoy": {"total_pct": -94.6, ...},
                         "window": "2026-07-27..2026-08-23",
                         "baseline_window": "2025-07-27..2025-08-23",
                         "latest_date": "2026-08-23", "data_lag_days": 7,
                         "status": "collapsed", "low_volume": false,
                         "source": "IMF PortWatch", ...} } }
"""
import json
from datetime import date, datetime, timedelta, timezone

from _common import http_get, write_json

ARCGIS = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services"
DAILY_URL = f"{ARCGIS}/Daily_Chokepoints_Data/FeatureServer/0/query"
META_URL = f"{ARCGIS}/PortWatch_chokepoints_database/FeatureServer/0/query"

SOURCE = "IMF PortWatch"
SOURCE_URL = "https://portwatch.imf.org/"

WINDOW_DAYS = 28          # four whole weeks — kills day-of-week effects
PAGE = 1000               # server maxRecordCount; do not raise
COLLAPSE_PCT = -50.0      # total YoY at or below this => "collapsed"
DISRUPT_PCT = -10.0       # total YoY at or below this => "disrupted"
SEGMENT_PCT = -25.0       # any one segment at or below this => "disrupted"
MIN_BASELINE_PER_DAY = 1.0  # below this, YoY % is noise; stay "normal"

SEGMENTS = (("total", "n_total"), ("dry_bulk", "n_dry_bulk"),
            ("tanker", "n_tanker"), ("container", "n_container"))

# portid -> the slug a human would write, so rows join to the hand-curated
# data/chokepoints.json by name. portwatch_id ships alongside so the join is
# verifiable rather than trusted.
SLUGS = {
    "chokepoint1": "suez",              "chokepoint2": "panama",
    "chokepoint3": "bosporus",          "chokepoint4": "bab_el_mandeb",
    "chokepoint5": "malacca",           "chokepoint6": "hormuz",
    "chokepoint7": "cape_of_good_hope", "chokepoint8": "gibraltar",
    "chokepoint9": "dover",             "chokepoint10": "oresund",
    "chokepoint11": "taiwan",           "chokepoint12": "korea",
    "chokepoint13": "tsugaru",          "chokepoint14": "luzon",
    "chokepoint15": "lombok",           "chokepoint16": "ombai",
    "chokepoint17": "bohai",            "chokepoint18": "torres",
    "chokepoint19": "sunda",            "chokepoint20": "makassar",
    "chokepoint21": "magellan",         "chokepoint22": "yucatan",
    "chokepoint23": "windward_passage", "chokepoint24": "mona_passage",
    "chokepoint25": "balabac",          "chokepoint26": "bering",
    "chokepoint27": "mindoro",          "chokepoint28": "kerch",
}

REFERENCE = ["suez", "panama", "bosporus", "bab_el_mandeb", "malacca", "hormuz"]


def _arcgis(url, params):
    """One ArcGIS query. Raises on a JSON-level 'error' the HTTP layer let past."""
    p = {"f": "json", "returnGeometry": "false"}
    p.update(params)
    r = http_get(url, params=p, timeout=90, retries=3)
    try:
        d = r.json()
    except Exception as e:
        raise RuntimeError(f"{url}: response was not JSON ({e}); "
                           f"first 200 bytes: {(r.text or '')[:200]!r}")
    # ArcGIS returns HTTP 200 with an {"error": {...}} body. raise_for_status
    # in http_get will never see it, so it must be checked here or a bad query
    # silently becomes "zero chokepoints".
    if isinstance(d, dict) and "error" in d:
        raise RuntimeError(f"{url}: ArcGIS error {json.dumps(d['error'])[:300]}")
    return d


def _paged(url, where, out_fields, order_by):
    """Every row matching `where`, paging past maxRecordCount."""
    rows, offset = [], 0
    while True:
        d = _arcgis(url, {"where": where, "outFields": out_fields,
                          "orderByFields": order_by,
                          "resultOffset": offset, "resultRecordCount": PAGE})
        feats = d.get("features") or []
        rows.extend(f.get("attributes", {}) for f in feats)
        if not feats or not d.get("exceededTransferLimit"):
            break
        offset += len(feats)
        if offset > 200000:  # guard against a server that always says "more"
            raise RuntimeError(f"{url}: pagination exceeded 200k rows — "
                               f"suspect a where clause the server ignored")
    return rows


def _latest_date():
    """Newest published day. Read, never assumed — the publication lag drifts."""
    d = _arcgis(DAILY_URL, {"where": "1=1", "outStatistics": json.dumps([
        {"statisticType": "max", "onStatisticField": "date",
         "outStatisticFieldName": "maxd"}])})
    feats = d.get("features") or []
    raw = (feats[0].get("attributes") or {}).get("maxd") if feats else None
    if not raw:
        raise RuntimeError("Daily_Chokepoints_Data returned no max(date)")
    # DateOnly comes back as 'YYYY-MM-DD'; epoch-ms is the legacy Date shape.
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).date()
    return date.fromisoformat(str(raw)[:10])


def _day(row):
    raw = row.get("date")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).date().isoformat()
    return str(raw)[:10] if raw else None


def _means(rows):
    """{portid: {segment: mean per day}} plus the day count actually seen."""
    acc = {}
    for r in rows:
        pid = r.get("portid")
        if not pid:
            continue
        a = acc.setdefault(pid, {"days": 0, **{k: 0 for k, _ in SEGMENTS}})
        a["days"] += 1
        for key, field in SEGMENTS:
            a[key] += (r.get(field) or 0)
    out = {}
    for pid, a in acc.items():
        n = a["days"]
        if not n:
            continue
        out[pid] = {"days": n,
                    **{k: round(a[k] / n, 2) for k, _ in SEGMENTS}}
    return out


def _pct(cur, base):
    if base is None or base <= 0:
        return None
    return round(100.0 * (cur - base) / base, 1)


def _status(yoy, base_total):
    """See the threshold block in the module docstring."""
    total = yoy.get("total_pct")
    if total is None:
        return "unknown"
    if total <= COLLAPSE_PCT:
        return "collapsed"            # nothing may override a collapse
    if base_total < MIN_BASELINE_PER_DAY:
        return "normal"               # percentage change on ~0 traffic is noise
    if total <= DISRUPT_PCT:
        return "disrupted"
    for key in ("dry_bulk_pct", "tanker_pct", "container_pct"):
        v = yoy.get(key)
        if v is not None and v <= SEGMENT_PCT:
            return "disrupted"        # Panama: calm total, gutted dry bulk
    return "normal"


def build():
    names = {}
    for row in _paged(META_URL, "1=1", "portid,portname,fullname", "portid"):
        pid = row.get("portid")
        if pid:
            names[pid] = row.get("fullname") or row.get("portname") or pid
    if not names:
        raise RuntimeError("chokepoint metadata layer returned 0 rows")

    latest = _latest_date()
    cur_start = latest - timedelta(days=WINDOW_DAYS - 1)
    base_end = latest.replace(year=latest.year - 1)
    base_start = cur_start.replace(year=cur_start.year - 1)

    where = (f"(date >= DATE '{cur_start}' AND date <= DATE '{latest}')"
             f" OR (date >= DATE '{base_start}' AND date <= DATE '{base_end}')")
    fields = "date,portid," + ",".join(f for _, f in SEGMENTS)
    rows = _paged(DAILY_URL, where, fields, "date,portid")
    if not rows:
        raise RuntimeError(f"daily layer returned 0 rows for window {where}")

    cur_rows, base_rows = [], []
    lo, hi = cur_start.isoformat(), latest.isoformat()
    for r in rows:
        d = _day(r)
        if d is None:
            continue
        (cur_rows if lo <= d <= hi else base_rows).append(r)

    cur, base = _means(cur_rows), _means(base_rows)
    lag = (datetime.now(timezone.utc).date() - latest).days

    out = {}
    for pid, c in sorted(cur.items(), key=lambda kv: int(kv[0].replace("chokepoint", ""))):
        b = base.get(pid)
        yoy = {f"{k}_pct": _pct(c[k], (b or {}).get(k)) for k, _ in SEGMENTS}
        base_total = (b or {}).get("total", 0.0)
        slug = SLUGS.get(pid) or pid
        if pid not in SLUGS:
            print(f"  [warn] {pid} ({names.get(pid)}) has no slug — keyed by portid. "
                  f"PortWatch has added a chokepoint; add it to SLUGS.")
        out[slug] = {
            "name": names.get(pid, pid),
            "portwatch_id": pid,
            "window_days": WINDOW_DAYS,
            "days_observed": c["days"],
            "transits_per_day": {k: c[k] for k, _ in SEGMENTS},
            "baseline_transits_per_day": ({k: b[k] for k, _ in SEGMENTS} if b else None),
            "yoy": yoy,
            "window": f"{cur_start}..{latest}",
            "baseline_window": f"{base_start}..{base_end}",
            "latest_date": latest.isoformat(),
            "data_lag_days": lag,
            "status": _status(yoy, base_total),
            "low_volume": base_total < MIN_BASELINE_PER_DAY,
            "source": SOURCE,
            "source_url": SOURCE_URL,
            "quality_flag": "sourced",
        }
    return out, latest, lag, cur_start, base_start, base_end, where


def main():
    try:
        out, latest, lag, cur_start, base_start, base_end, where = build()
    except Exception as e:
        print(f"[FAIL] PortWatch: {e}")
        write_json("portwatch.json", {}, source=SOURCE,
                   notes=f"PortWatch chokepoint fetch failed: {e}")
        return 1

    counts = {}
    for row in out.values():
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(f"[INFO] PortWatch: {len(out)} chokepoints | latest {latest} "
          f"(lag {lag}d) | window {WINDOW_DAYS}d | "
          + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"  [where] {where}")

    print(f"  [ref] {'slug':<14} {'total/day':>9} {'base':>7} {'YoY%':>7} "
          f"{'dry_bulk':>8} {'db YoY%':>8}  status")
    for slug in REFERENCE:
        r = out.get(slug)
        if not r:
            print(f"  [ref] {slug:<14} MISSING FROM FEED")
            continue
        b = r["baseline_transits_per_day"] or {}
        print(f"  [ref] {slug:<14} {r['transits_per_day']['total']:>9.1f} "
              f"{b.get('total', 0):>7.1f} {r['yoy']['total_pct']:>7.1f} "
              f"{r['transits_per_day']['dry_bulk']:>8.1f} "
              f"{r['yoy']['dry_bulk_pct']:>8.1f}  {r['status']}")

    write_json(
        "portwatch.json", out,
        source=f"{SOURCE} (Daily_Chokepoints_Data, ArcGIS FeatureServer)",
        notes=(
            f"Daily AIS-derived transit counts for {len(out)} maritime chokepoints. "
            f"Each row is the mean transits/day over the {WINDOW_DAYS} days ending "
            f"{latest} ({cur_start}..{latest}), compared with the SAME calendar "
            f"window one year earlier ({base_start}..{base_end}) — a calendar "
            "baseline, not a rolling average, because shipping seasonality is real. "
            f"PortWatch publishes with a lag; this run is {lag} days behind. "
            f"Status: collapsed = total down >={abs(COLLAPSE_PCT):.0f}% YoY; "
            f"disrupted = total down >={abs(DISRUPT_PCT):.0f}% or any of dry bulk / "
            f"tanker / container down >={abs(SEGMENT_PCT):.0f}%; else normal. "
            f"Chokepoints averaging under {MIN_BASELINE_PER_DAY:.0f} transits/day "
            "in the baseline are never flagged (percentage change on near-zero "
            "traffic is noise) and carry low_volume=true. "
            "Counts are transits, not tonnage or cargo value. "
            "Source: IMF PortWatch (portwatch.imf.org), redistributed with "
            "attribution under IMF terms."
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
