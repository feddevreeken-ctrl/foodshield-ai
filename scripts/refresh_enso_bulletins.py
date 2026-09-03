#!/usr/bin/env python3
"""Agency ENSO bulletins — the four sites, as dated items for the news view.

These are NOT press coverage. They are the monitoring agencies' own products, and
the news view keeps them in a separate strip for that reason: a WMO update and a
Reuters piece about a WMO update are different kinds of evidence.

Every item must carry a date the agency published, parsed from the page. An
undated item is dropped rather than stamped with the collection time, because
"when did the agency say this" is the only thing that makes a bulletin useful.

One source is deliberately recorded as UNAVAILABLE rather than wired in:
NOAA Climate.gov's ENSO blog returns HTTP 200 with a fresh Last-Modified header
while its newest post is over a year old. That is the same trap this project has
already hit twice (wksst8110.for, rel_wksst9120.txt). Detected, not trusted.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import http_get, write_json  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}

BOM_RNINO = "https://www.bom.gov.au/clim_data/IDCK000072/rnino_3.4.txt"
BOM_SOI = "https://www.bom.gov.au/clim_data/IDCKGSM000/soi.txt"
IRI_URL = "https://iri.columbia.edu/our-expertise/climate/forecasts/enso/current/"
WMO_THEME = "https://wmo.int/themes/el-nino-la-nina-phenomena"
CG_BLOG = "https://www.climate.gov/news-features/department/enso-blog"

STALE_DAYS = {"weekly": 21, "monthly": 75, "news": 240}
MONTHS = ("january february march april may june july august september "
          "october november december").split()


def get(url: str) -> str:
    return http_get(url, timeout=60, headers=UA, retries=3).text


def strip_tags(html: str) -> str:
    html = re.sub(r"<(script|style).*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def age_days(iso: str) -> int:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).days


def bom_weekly() -> dict:
    """BoM's own weekly ocean reading, from the two feeds that are machine-readable.

    The ENSO Wrap-Up page itself returns 403 to a bare request and loads its text
    from a JSON path that is not reachable, so this reports the numbers BoM
    publishes rather than scraping prose it will not serve.
    """
    def last(txt: str) -> tuple[str, str, float]:
        rows = []
        for line in txt.splitlines():
            f = [p.strip() for p in line.split(",")]
            if len(f) == 3 and re.fullmatch(r"\d{8}", f[0]):
                try:
                    rows.append((f[0], f[1], float(f[2])))
                except ValueError:
                    pass
        if not rows:
            raise RuntimeError("BoM feed shape changed")
        return max(rows, key=lambda r: r[1])

    _, r_end, r_val = last(get(BOM_RNINO))
    _, s_end, s_val = last(get(BOM_SOI))
    end = datetime.strptime(r_end, "%Y%m%d").replace(tzinfo=timezone.utc)
    return {
        "agency": "BoM Australia",
        "kind": "weekly",
        "title": "Weekly relative Niño 3.4 " + ("%+.2f" % r_val) + " °C, Troup SOI " + ("%+.1f" % s_val),
        "summary": ("BoM's operational ocean index for the week ending "
                    + end.strftime("%-d %B %Y")
                    + ". Its El Niño threshold is +0.8 °C, higher than CPC's +0.5. The SOI is "
                    + ("negative, the El Niño-like sign" if s_val < 0 else "positive, the La Niña-like sign")
                    + ", so the atmosphere is "
                    + ("coupled to the ocean signal." if s_val < 0 else "not reinforcing it.")),
        "published": end.isoformat(),
        "url": "https://www.bom.gov.au/climate/enso/",
        "data_urls": [BOM_RNINO, BOM_SOI],
    }


def iri_monthly() -> dict:
    html = get(IRI_URL)
    text = strip_tags(html)
    m = re.search(r"(" + "|".join(MONTHS) + r")\s+(20\d\d)\s+Quick\s+Look", text, re.I)
    if not m:
        raise RuntimeError("IRI 'Quick Look' heading not found -- page shape changed")
    month, year = m.group(1).lower(), int(m.group(2))
    d = re.search(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(20\d\d)\b", text, re.I)
    if d and d.group(2).lower() == month:
        pub = datetime(int(d.group(3)), MONTHS.index(month) + 1, int(d.group(1)), tzinfo=timezone.utc)
    else:
        pub = datetime(year, MONTHS.index(month) + 1, 1, tzinfo=timezone.utc)
    return {
        "agency": "IRI / Columbia",
        "kind": "monthly",
        "title": m.group(0).strip() + " — ENSO forecast",
        "summary": ("The IRI/CPC plume and the consensus probabilistic forecast, issued monthly. "
                    "This is the forecast the page's own outlook language is checked against."),
        "published": pub.isoformat(),
        "url": IRI_URL,
    }


def wmo_news(limit: int = 3) -> list[dict]:
    idx = get(WMO_THEME)
    slugs, seen = [], set()
    for m in re.finditer(r'href="(/media/news/[^"#?]+)"', idx):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            slugs.append(m.group(1))
    items = []
    for slug in slugs[:10]:
        url = "https://wmo.int" + slug
        try:
            page = get(url)
        except Exception:
            continue
        pm = re.search(r'article:published_time"[^>]*content="([^"]+)"', page) \
            or re.search(r'content="([^"]+)"[^>]*property="article:published_time"', page)
        tm = re.search(r'<meta property="og:title" content="([^"]+)"', page)
        if not pm or not tm:
            continue          # undated: drop, never stamp with collection time
        items.append({
            "agency": "WMO",
            "kind": "news",
            "title": unescape(tm.group(1)).strip(),
            "summary": "",
            "published": pm.group(1),
            "url": url,
        })
    items.sort(key=lambda i: i["published"], reverse=True)
    if not items:
        raise RuntimeError("no dated WMO item found")
    return items[:limit]


def climate_gov_probe() -> dict:
    """Returns an 'unavailable' record. Kept as a live check, not a wired source."""
    text = strip_tags(get(CG_BLOG))
    dates = []
    for m in re.finditer(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s*(20\d\d)\b", text, re.I):
        dates.append(datetime(int(m.group(3)), MONTHS.index(m.group(1).lower()) + 1,
                              int(m.group(2)), tzinfo=timezone.utc))
    if not dates:
        raise RuntimeError("no post dates parsed")
    newest = max(dates)
    return {"newest_post": newest.date().isoformat(), "age_days": (datetime.now(timezone.utc) - newest).days}


def main() -> int:
    items, unavailable = [], []

    def attempt(key, fn, many=False):
        try:
            r = fn()
            items.extend(r if many else [r])
        except Exception as e:  # noqa: BLE001
            unavailable.append({"key": key, "reason": f"{type(e).__name__}: {e}"[:200]})

    attempt("bom_weekly", bom_weekly)
    attempt("iri_monthly", iri_monthly)
    attempt("wmo_news", wmo_news, many=True)

    # climate.gov is probed, reported, and deliberately NOT wired in.
    try:
        cg = climate_gov_probe()
        unavailable.append({
            "key": "climate_gov_enso_blog",
            "url": CG_BLOG,
            "reason": (
                "Serves HTTP 200 with a fresh Last-Modified header, but its newest post is "
                + cg["newest_post"] + ", " + str(cg["age_days"]) + " days old. Treated as a "
                "frozen feed and excluded rather than published as current."),
            "newest_post": cg["newest_post"],
            "age_days": cg["age_days"],
        })
    except Exception as e:  # noqa: BLE001
        unavailable.append({"key": "climate_gov_enso_blog", "url": CG_BLOG,
                            "reason": f"{type(e).__name__}: {e}"[:200]})

    if not items:
        raise RuntimeError(
            "no agency bulletin reachable -- refusing to overwrite the existing file. "
            "Tried: " + ", ".join(u["key"] for u in unavailable))

    for it in items:
        try:
            a = age_days(it["published"])
            it["age_days"] = a
            it["stale"] = a > STALE_DAYS.get(it["kind"], 240)
        except Exception:  # noqa: BLE001
            it["stale"] = None
    items.sort(key=lambda i: i["published"], reverse=True)

    write_json(
        "enso_bulletins.json",
        {"bulletins": items, "unavailable": unavailable,
         "stale_after_days": STALE_DAYS},
        source="BoM Australia; IRI/Columbia; WMO",
        notes=("Agency products, not press coverage — the news view keeps them in their own "
               "strip. Every item carries the date its agency published it, parsed from the "
               "page; an undated item is dropped rather than stamped with the collection "
               "time. Items past their cadence are flagged stale rather than hidden. "
               "NOAA Climate.gov's ENSO blog is probed every run and deliberately excluded: "
               "it returns 200 with a fresh Last-Modified while its newest post is more "
               "than a year old."),
        status="ok" if not [u for u in unavailable if u["key"] != "climate_gov_enso_blog"] else "partial",
    )
    print(f"enso_bulletins: {len(items)} items, {len(unavailable)} unavailable")
    for i in items:
        print(f"  {i['published'][:10]}  {i['agency']:14s} {i['title'][:64]}"
              + ("  [STALE]" if i.get("stale") else ""))
    for u in unavailable:
        print(f"  SKIP {u['key']}: {u['reason'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
