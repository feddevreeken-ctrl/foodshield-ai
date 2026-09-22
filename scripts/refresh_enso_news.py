#!/usr/bin/env python3
"""El Niño newswire: stories that name the event.

The Reported view used to run a keyword qualifier over data/commodity_news.json,
a feed collected for commodity headlines, and it found nothing: 69 stories in
the cycle, none about El Niño. A wire for an El Niño tab has to ask its sources
for El Niño by name. This collector does, from three places:

  1. ReliefWeb reports (api v2), query "El Niño": humanitarian reports and
     agency outlooks. The same approved appname the food-security feed uses.
  2. The publisher RSS feeds refresh_commodity_news.py already reads (wire,
     trade press, institutional), fetched here in parallel and filtered by name.
     No aggregator in between.
  3. GDELT DOC 2.0, one query, opportunistic: its per-IP limiter throttles CI
     egress, and a throttle is recorded as such, not as a failure.

Every item names El Niño, La Niña or ENSO in its headline (ReliefWeb: title or
body, which is how an agency outlook mentions it). Last-good items are kept
across runs, merged by URL inside a 21-day window, so one bad run does not
blank the wire. Country tags come from the conservative gazetteer the commodity
wire uses and are keyword matches; the page says so.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import env, http_get, write_json  # noqa: E402
from _news_corridors import detect_countries  # noqa: E402
import refresh_commodity_news as cn  # noqa: E402

OUTFILE = "enso_news.json"
DATA = Path(__file__).resolve().parent.parent / "data"

NAMED = re.compile(r"el\s*ni[ñn]o|la\s*ni[ñn]a|\benso\b|southern oscillation|ni[ñn]o costero", re.I)
MAX_AGE_DAYS = 21
MAX_ITEMS = 60

RW_URL = "https://api.reliefweb.int/v2/reports"
RW_APPNAME_DEFAULT = "vreeken-foodshield-7k3n"
RW_QUERY = '"El Niño" OR "El Nino" OR "La Niña" OR "La Nina" OR ENSO'

GDELT_QUERY = ('("El Nino" OR "El Niño" OR "La Nina") '
               '(food OR harvest OR crop OR drought OR rice OR maize OR wheat OR farmers '
               'OR fisheries OR anchoveta OR canal OR flood OR prices OR famine) sourcelang:english')


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    s = str(value).strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _item(title, source, url, published, provenance, kind, isos=None):
    title = cn.sanitize_title(title)
    url = cn.sanitize_url(url)
    if not title or not url:
        return None
    return {
        "title": title,
        "source": source,
        "url": url,
        "published_at": _iso(published),
        "provenance": provenance,
        "kind": kind,
        "countries_mentioned": list(isos) if isos is not None else detect_countries(title),
        "why": "names the event",
    }


# ── 1. ReliefWeb ──────────────────────────────────────────────────────────────
def reliefweb(since: datetime) -> list[dict]:
    appname = env("RELIEFWEB_APPNAME", RW_APPNAME_DEFAULT)
    params = [
        ("appname", appname),
        ("limit", 40),
        ("sort[]", "date:desc"),
        ("query[value]", RW_QUERY),
        ("filter[field]", "date.original"),
        ("filter[value][from]", since.strftime("%Y-%m-%dT00:00:00+00:00")),
        ("fields[include][]", "title"),
        ("fields[include][]", "date.original"),
        ("fields[include][]", "url_alias"),
        ("fields[include][]", "primary_country.iso3"),
        ("fields[include][]", "country.iso3"),
        ("fields[include][]", "source.shortname"),
        ("fields[include][]", "format.name"),
    ]
    r = http_get(RW_URL, params=params, timeout=45, retries=2)
    rows = r.json().get("data") or []
    out = []
    for row in rows:
        f = row.get("fields") or {}
        isos = []
        pc = (f.get("primary_country") or {}).get("iso3")
        if pc:
            isos.append(pc.upper())
        for c in f.get("country") or []:
            i = (c or {}).get("iso3")
            if i and i.upper() not in isos:
                isos.append(i.upper())
        srcs = [s.get("shortname") for s in (f.get("source") or []) if s and s.get("shortname")]
        fmt = [x.get("name") for x in (f.get("format") or []) if x and x.get("name")]
        date = (f.get("date") or {}).get("original")
        it = _item(f.get("title"), ", ".join(srcs[:2]) or "ReliefWeb", f.get("url_alias"), date,
                   "reliefweb", "report", isos)
        if it:
            it["format"] = fmt[0] if fmt else None
            out.append(it)
    return out


# ── 2. Publisher RSS, by name ─────────────────────────────────────────────────
def rss_named() -> tuple[list[dict], int, int]:
    items, ok, failed = [], 0, 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(cn.fetch_institutional_rss, label, url): (label, tier)
                for label, url, tier in cn.ALL_FEEDS}
        for fut in as_completed(futs):
            label, tier = futs[fut]
            try:
                raw = fut.result()
                ok += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  [rss] {label}: FAILED: {type(e).__name__}: {e}"[:160])
                continue
            for a in raw:
                if NAMED.search(a.get("title") or ""):
                    it = _item(a.get("title"), label, a.get("url"), a.get("published_at"),
                               "rss:" + tier, "news")
                    if it:
                        items.append(it)
    return items, ok, failed


# ── 3. GDELT, one query ───────────────────────────────────────────────────────
def gdelt_named() -> list[dict]:
    params = {"query": GDELT_QUERY, "mode": "ArtList", "format": "json",
              "maxrecords": 75, "timespan": "14d", "sort": "datedesc"}
    r = http_get(cn.GDELT_URL, params=params, timeout=45, retries=2, backoff=3)
    payload = cn._parse_gdelt_body(r)
    out = []
    for a in payload.get("articles") or []:
        if not isinstance(a, dict):
            continue
        title, url = (a.get("title") or "").strip(), (a.get("url") or "").strip()
        # The query matches article text; the wire's promise is that the
        # headline itself names the event, so the rest are dropped here.
        if not NAMED.search(title):
            continue
        dom = (a.get("domain") or cn._domain_of(url)).lower()
        it = _item(title, dom, url, cn._parse_gdelt_date(a.get("seendate")), "gdelt", "news")
        if it:
            out.append(it)
    return out


def _previous() -> list[dict]:
    p = DATA / OUTFILE
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text())
        return list(((d.get("data") or {}).get("items")) or [])
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=MAX_AGE_DAYS)
    fresh, status = [], {}

    try:
        rw = reliefweb(since)
        fresh.extend(rw)
        status["reliefweb"] = "ok"
        print(f"  [reliefweb] {len(rw)} reports naming the event")
    except Exception as e:  # noqa: BLE001
        status["reliefweb"] = f"failed: {type(e).__name__}: {e}"[:160]
        print(f"  [reliefweb] FAILED: {e}"[:160])

    rss, ok, failed = rss_named()
    fresh.extend(rss)
    status["rss"] = f"{ok} of {ok + failed} feeds read"
    print(f"  [rss] {len(rss)} named stories from {ok} feeds ({failed} failed)")

    try:
        gd = gdelt_named()
        fresh.extend(gd)
        status["gdelt"] = "ok"
        print(f"  [gdelt] {len(gd)} named stories")
    except cn.ThrottledError as e:
        status["gdelt"] = "throttled"
        print(f"  [gdelt] throttled: {e}"[:120])
    except Exception as e:  # noqa: BLE001
        # A 429 surfaces from http_get before the body sniff; it is the same throttle.
        throttled = "429" in str(e)
        status["gdelt"] = "throttled" if throttled else f"failed: {type(e).__name__}: {e}"[:160]
        print(f"  [gdelt] {'throttled' if throttled else 'FAILED'}: {e}"[:160])

    # Merge with last-good, newest wins per URL, then window and cap.
    merged: dict[str, dict] = {}
    for it in _previous() + fresh:
        merged[it["url"]] = it
    items = []
    for it in merged.values():
        ts = it.get("published_at")
        if not ts:
            continue
        try:
            if datetime.fromisoformat(ts) < since:
                continue
        except ValueError:
            continue
        items.append(it)
    items.sort(key=lambda i: i["published_at"], reverse=True)
    items = items[:MAX_ITEMS]

    # Half the RSS feeds down, or ReliefWeb down, means completeness is unverified.
    partial = status["reliefweb"] != "ok" or (ok + failed and failed * 2 >= ok + failed)
    write_json(
        OUTFILE,
        {"items": items, "sources": status,
         "window_days": MAX_AGE_DAYS, "fresh_this_run": len(fresh)},
        source="ReliefWeb reports; publisher RSS feeds; GDELT DOC 2.0",
        notes=("Headlines that name El Niño, La Niña or ENSO, from the humanitarian "
               "reports API, the publisher feeds the commodity wire reads, and one GDELT "
               "query. Third-party CLAIMS with links, never measured data; nothing scores "
               "off this file. Country tags are keyword matches. Last-good items are kept "
               "for 21 days so a throttled or failed run does not blank the wire."),
        status="partial" if partial else "ok",
    )
    print(f"enso_news: {len(items)} items ({len(fresh)} fresh this run) · {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
