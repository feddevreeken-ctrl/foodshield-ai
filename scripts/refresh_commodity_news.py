"""
Commodity news — headline-level supply/policy signal for the six staples.

WHAT THIS IS
  A ranked, de-duplicated list of HEADLINES that plausibly bear on the supply
  of wheat, maize, rice, soybeans, palm oil or fertilizer. It is an editorial
  pointer surface, not a measurement. Every stored item is flagged
  quality_flag="claim": a third party asserted something, we linked to it.
  Nothing here is observed data and nothing here should ever be charted.

PUBLISHER FEEDS, ALL KEYLESS  (v46.1)
  We read the outlets' OWN syndication feeds — 29 of them, listed further
  down in TRADE_PRESS_FEEDS / WIRE_FEEDS / INSTITUTIONAL_FEEDS — plus GDELT
  as a supplementary source. None is a search-engine aggregator. Each feed is
  an unqueried firehose, so all commodity filtering happens locally in
  classify(); no feed gets to assert what a headline is about.

  NO GOOGLE NEWS. Its feed terms forbid this use. Owner decision, final —
  do not "just add one more source" here. (v46.1: an adapter was written and
  then removed rather than shipped disabled. Going to the newsrooms directly
  turned out to be both cleaner and better sourced, so the question is moot.)

  NO WTO, despite its feed serving fine: wto.org/robots.txt is
  `Disallow: *` with a Googlebot-only carve-out. Serving a feed is not the
  same as permitting a bot to take it.

CORRIDOR ATTRIBUTION  (v46.1, see _news_corridors.py)
  Each headline is joined to the bilateral flow atlas so the page can say who
  a story actually reaches: "Russia caps wheat exports" becomes EGY 8,526 kt,
  TUR 4,500 kt, BGD 2,600 kt. The country match is MODELED (keywords); the
  tonnage is SOURCED (commodity_flows.json). Those are different kinds of
  claim and the UI must keep them visibly apart.

LEGAL CONSTRAINT ON WHAT WE STORE  (read before editing the item shape)
  This repo is PUBLIC. Committing data/commodity_news.json is publication, not
  caching. So we store the headline verbatim plus a link, and nothing else:
  no article body, no RSS <description>, no summary, no snippet, no
  socialimage. The RSS description is dropped at PARSE time — it is never held
  in a variable that could leak into the payload. Headline + attribution +
  link is the reference-and-link pattern; a stored summary is a reproduction.

  Item shape is EXACTLY these keys, no others:
    title, source, url, published_at, published_label,
    matched, provenance, quality_flag, dedup_key,
    countries_mentioned, exposed, exposure_kt, attribution
  validate_data.py hard-fails on any extra key, so adding one here without
  updating the allow-list there will break the workflow (deliberately).

SCORES ARE INTERNAL
  Relevance, publisher trust and recency decay all feed the ordering of the
  list. None of them is written to the JSON. A number in a public file gets
  rendered, and a rendered "relevance: 7.4" reads as a measurement of the
  world when it is really a measurement of our keyword list.

GDELT RATE LIMITING (the trap this script exists to survive)
  GDELT throttles hard and its throttle response is PLAIN TEXT, not JSON —
  often with HTTP 200. r.json() then raises json.JSONDecodeError, which is not
  an HTTPError, so a naive `except requests.HTTPError` misses it and the cron
  dies on an unhandled exception. We sleep 15s between calls, and we check
  status_code AND sniff the body before parsing. A throttled commodity is
  recorded as status "throttled" and the run continues.

LAST-GOOD PRESERVATION (the v44 pattern, see refresh_usda_psd.py)
  If every commodity yields zero items AND data/commodity_news.json already
  holds items, we print [KEEP] and return WITHOUT writing. A failed run must
  never replace good items with []. GDELT throttling under CI load makes this
  a routine occurrence, not an edge case.

OUTPUT: data/commodity_news.json
  {"_meta": {...}, "data": {
      "items": [ {...}, ... ],
      "commodity_status": {"wheat": "ok", "rice": "failed", ...},
      "sources": [ ... ]
  }}
"""
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from _common import _has_existing_data, http_get, write_json

from _news_corridors import annotate, exposure_leaderboard, load_flows

from _news_taxonomy import (
    COMMODITIES,
    COMMODITY_QUERIES,
    DEDUP_HAMMING,
    DEMOTE,
    HALF_LIFE_HOURS,
    MAX_AGE_HOURS,
    MAX_ITEMS_PER_COMMODITY,
    MAX_ITEMS_TOTAL,
    MIN_RELEVANCE,
    NEGATIVE_GLOBAL,
    NEWS_COMMODITIES,
    NEWS_NEGATIVE,
    NEWS_SYNONYMS,
    COMPANY_TERMS,
    COMPANY_AMBIGUOUS,
    CHOKEPOINT_TERMS,
    ENERGY_DOMINANT,
    FOOD_CONTEXT,
    DISRUPTION_TERMS,
    POSITIVE,
    SIMHASH_BITS,
    contains_term,
    dedup_text,
    domain_trust,
    normalize_title,
    tokenize,
)

OUTFILE = "commodity_news.json"

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_TIMESPAN = "14d"
GDELT_MAXRECORDS = 75
# GDELT throttles aggressively and its limiter is per-IP. 15s between calls is
# the interval that has held up under GitHub Actions' shared egress IPs.
GDELT_SLEEP_S = 15
# v46.1 — GDELT is Tier 3 now. Two calls, not six: under CI egress its
# per-IP limiter 429s regardless of spacing, and six doomed calls cost 75s of
# sleep to buy nothing.
GDELT_MAX_COMMODITIES = 2

EC_RSS_URL = "https://agriculture.ec.europa.eu/node/2/rss_en"
EC_DOMAIN = "agriculture.ec.europa.eu"

# The only keys allowed in a stored item. Mirrored in validate_data.py; the two
# lists must stay in sync.
ITEM_KEYS = (
    "title", "source", "url", "published_at", "published_label",
    "matched", "provenance", "quality_flag", "dedup_key",
    # v46.1 corridor attribution. `exposed` carries SOURCED tonnages hung off a
    # MODELED country match — see the note in _news_corridors.py before
    # rendering either as if it were the other.
    "countries_mentioned", "exposed", "exposure_kt", "attribution",
    # Publisher-DECLARED feed image only (media:thumbnail / media:content /
    # enclosure). Never a scraped og:image — see _feed_image().
    "image",
    "companies", "chokepoints", "disruption",
)


# ─────────────────────────────────────────────────────────────────────────────
# Adapter 1 — GDELT DOC 2.0
# ─────────────────────────────────────────────────────────────────────────────
def fetch_gdelt(commodity):
    """Return a list of RAW dicts {title, domain, url, published_at} for one commodity.

    Raises on anything the caller should record as a failed commodity.
    """
    params = {
        "query": COMMODITY_QUERIES[commodity],
        "mode": "ArtList",
        "format": "json",
        "maxrecords": GDELT_MAXRECORDS,
        "timespan": GDELT_TIMESPAN,
        "sort": "datedesc",
    }
    # retries=2: GDELT's limiter does not clear in a couple of seconds, so a
    # long retry ladder here just burns the step's wall-clock budget. Throttled
    # commodities are meant to be skipped this run, not fought.
    r = http_get(GDELT_URL, params=params, timeout=45, retries=2, backoff=3)

    payload = _parse_gdelt_body(r)
    articles = payload.get("articles") or []

    out = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        title = (a.get("title") or "").strip()
        url = (a.get("url") or "").strip()
        if not title or not url:
            continue
        # a.get("socialimage") and every other field are deliberately IGNORED.
        # Only headline, link, domain and timestamp are ever read.
        out.append({
            "title": title,
            "domain": (a.get("domain") or _domain_of(url)).lower(),
            "url": url,   # already allow-listed at parse time
            "published_at": _parse_gdelt_date(a.get("seendate")),
        })
    return out


def _parse_gdelt_body(r):
    """Parse a GDELT response defensively.

    GDELT signals throttling and malformed queries with a PLAIN-TEXT body, and
    frequently does so with HTTP 200. requests never raises, r.json() raises a
    JSONDecodeError, and an unguarded caller dies. Check the status code AND
    sniff the body before parsing.
    """
    status = getattr(r, "status_code", None)
    if status != 200:
        raise RuntimeError(f"GDELT returned HTTP {status}")

    body = (r.text or "").strip()
    if not body:
        raise RuntimeError("GDELT returned an empty body")

    # Real ArtList JSON always starts with '{'. Anything else is GDELT's
    # plain-text error channel, e.g.
    #   "Your query was rate limited. Please try again shortly."
    if not body.startswith("{"):
        snippet = body[:160].replace("\n", " ")
        low = body.lower()
        if "rate limit" in low or "too many" in low or "throttl" in low:
            raise ThrottledError(f"GDELT rate limited: {snippet}")
        raise RuntimeError(f"GDELT returned non-JSON body: {snippet}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        # Body opened with '{' but is still not valid JSON — truncated response.
        raise RuntimeError(f"GDELT JSON decode error: {e}")

    if not isinstance(payload, dict):
        raise RuntimeError(f"GDELT payload is {type(payload).__name__}, expected object")
    return payload


class ThrottledError(RuntimeError):
    """GDELT rate-limited this query. Expected under CI load; not a code fault."""


def _parse_gdelt_date(seendate):
    """GDELT seendate is 'YYYYMMDDTHHMMSSZ'. Returns aware datetime or None."""
    s = (seendate or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Adapter 2 — European Commission agriculture RSS
# ─────────────────────────────────────────────────────────────────────────────
def fetch_ec_rss():
    """Return RAW dicts from the EC agriculture feed.

    The feed takes no query parameter, so this is one unfiltered pull and all
    commodity selection happens locally in classify().

    The <description> element is READ AND DISCARDED here — it never leaves this
    function. See the legal note in the module docstring.
    """
    r = http_get(EC_RSS_URL, timeout=45, retries=2,
                 headers={"Accept": "application/rss+xml, application/xml, text/xml"})
    if getattr(r, "status_code", None) != 200:
        raise RuntimeError(f"EC RSS returned HTTP {r.status_code}")

    body = (r.text or "").strip()
    if not body.startswith("<"):
        raise RuntimeError(f"EC RSS returned non-XML body: {body[:160]}")

    root = ET.fromstring(body)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        # NOTE: item.findtext("description") is intentionally never called.
        out.append({
            "title": title,
            "domain": _domain_of(link) or EC_DOMAIN,
            "url": link,
            "published_at": _parse_rss_date(item.findtext("pubDate")),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# v46.1 — broader sourcing
#
# WHY: with GDELT alone this feed yielded 3 items. GDELT's throttle is
# IP-level and stateful (measured 18 Jul 2026: after a burst, even calls
# spaced 10-12s apart keep returning HTTP 429 with a plain-text body) — which
# is exactly the CI-egress symptom. It is now supplementary, never primary.
#
# Google News RSS is unauthenticated, returns up to 100 items per query, and
# carries a <source url="..."> element giving the publisher cleanly, so no
# title-splitting is needed. Measured 200 application/xml on every call.
#
# LEGAL POSTURE: we store headline + link + publisher + timestamp, nothing
# else. No <description>, no article body, and the Google redirect link is
# stored verbatim and never resolved to extract text. That is the line between
# headline aggregation and reproduction, and this pipeline stays on the
# correct side of it.
# ─────────────────────────────────────────────────────────────────────────────
# PUBLISHER FEEDS — the outlets' own syndication feeds, not a search engine.
#
# This is the morning-dashboard pattern: go to the newsroom, not to an
# aggregator. It also settles the Google News question the docstring raises —
# we do not need it. Every URL below was fetched and verified on 18 Jul 2026;
# item counts and freshness were observed, not assumed.
#
# DELIBERATELY EXCLUDED, with reasons, so nobody "helpfully" re-adds them:
#   Google News   — owner decision, and its terms don't sanction this use.
#   WTO           — robots.txt is `Disallow: *` with a Googlebot-only Allow.
#                   The feed serves fine; that is not the same as permission.
#   ACM Australia — Farm Weekly / Queensland Country Life / Farm Online.
#                   robots disallows the RSS paths; the intent is unambiguous.
#   Reuters       — genuinely dead. feeds.reuters.com fails DNS, rssFeed/*
#                   returns 401, the sitemap has zero items. Not a bug to fix.
#   AgWeb, Farm Futures, Successful Farming, Western Producer, RealAgriculture,
#   AgWeek, Agri-Pulse, DTN, Farmers Weekly UK, IFPRI, USDA, World Bank, IGC,
#   FEWS NET, Devex, CGIAR — 402/403/404, or 200 with zero items. All verified.
#
# CONTENT SIGNALS: the Sosland titles (World Grain, Food Business News,
# MEAT+POULTRY) and FreshPlaza publish `Content-Signal: ai-train=no`
# (FreshPlaza also `ai-input=no`). We store a headline, a link, a publisher
# and a timestamp — syndication use, not training — and never <description>
# or article text. Recorded here because the distinction matters if this file
# is ever questioned.
#
# Several declare Crawl-delay: 5-10s. A 6-hourly poll sits well inside that.

# Some publishers 403 the default python-requests UA.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Tier 1 — dedicated agriculture / commodity trade press. Near-100% on-topic,
# so these carry the page. No commodity hint is passed for any feed here:
# they are unqueried firehoses like the EC one, so a headline still has to
# earn its commodity match on keywords rather than inheriting one.
TRADE_PRESS_FEEDS = [
    ("The Poultry Site",    "https://www.thepoultrysite.com/news.rss"),
    ("The Pig Site",        "https://www.thepigsite.com/news.rss"),
    ("World Grain",         "https://www.world-grain.com/rss/articles"),
    ("Food Business News",  "https://www.foodbusinessnews.net/rss/articles"),
    ("MEAT+POULTRY",        "https://www.meatpoultry.com/rss/articles"),
    ("Farm Progress",       "https://www.farmprogress.com/rss.xml"),
    ("Feedstuffs",          "https://www.feedstuffs.com/rss.xml"),
    ("National Hog Farmer", "https://www.nationalhogfarmer.com/rss.xml"),
    ("BEEF Magazine",       "https://www.beefmagazine.com/rss.xml"),
    ("Brownfield Ag News",  "https://www.brownfieldagnews.com/feed/"),
    ("Grain Central",       "https://www.graincentral.com/feed/"),
    ("Beef Central",        "https://www.beefcentral.com/feed/"),
    ("FreshPlaza",          "https://www.freshplaza.com/rss.xml"),
    ("Undercurrent News",   "https://www.undercurrentnews.com/feed/"),
    ("FoodNavigator",       "https://www.foodnavigator.com/arc/outboundfeeds/rss/"),
    ("Food Dive",           "https://www.fooddive.com/feeds/news/"),
    ("AgFunderNews",        "https://agfundernews.com/feed"),
    ("Mongabay",            "https://news.mongabay.com/feed/"),
]

# Tier 2 — general wires. Low relevance density (5-30%) but high authority and
# volume; classify() does the filtering, which is exactly what it is for.
WIRE_FEEDS = [
    ("FT Commodities",      "https://www.ft.com/commodities?format=rss"),
    ("FT Companies",        "https://www.ft.com/companies?format=rss"),
    ("FT World",            "https://www.ft.com/world?format=rss"),
    ("Guardian Environment", "https://www.theguardian.com/environment/rss"),
    ("Guardian Global Development", "https://www.theguardian.com/global-development/rss"),
    ("Guardian Business",   "https://www.theguardian.com/business/rss"),
    ("Guardian World",      "https://www.theguardian.com/world/rss"),
    ("BBC Business",        "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("BBC Sci/Environment", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
    ("BBC World",           "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("DW Business",         "https://rss.dw.com/rdf/rss-en-bus"),
    ("DW All",              "https://rss.dw.com/xml/rss-en-all"),
    ("Al Jazeera",          "https://www.aljazeera.com/xml/rss/all.xml"),
    ("NPR Business",        "https://feeds.npr.org/1006/rss.xml"),
    ("NPR World",           "https://feeds.npr.org/1004/rss.xml"),
    ("CBC Business",        "https://www.cbc.ca/webfeed/rss/rss-business"),
    ("SCMP Business",       "https://www.scmp.com/rss/92/feed"),
    ("CNA Business",        "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"),
    ("IPS News",            "https://www.ipsnews.net/feed/"),
    ("The Conversation",    "https://theconversation.com/global/environment/articles.atom"),
]

# Tier 3 — institutional. Low cadence, high citability.
INSTITUTIONAL_FEEDS = [
    ("FAO Newsroom",        "https://www.fao.org/feeds/fao-newsroom-rss"),
    ("ReliefWeb",           "https://reliefweb.int/updates/rss.xml?view=headlines"),
    ("European Commission", "https://ec.europa.eu/commission/presscorner/api/rss?language=en"),
]

ALL_FEEDS = ([(l, u, "trade_press") for l, u in TRADE_PRESS_FEEDS]
             + [(l, u, "wire") for l, u in WIRE_FEEDS]
             + [(l, u, "institutional") for l, u in INSTITUTIONAL_FEEDS])



def fetch_institutional_rss(label, url):
    """Return RAW dicts from one institutional RSS/Atom feed."""
    r = http_get(url, timeout=45, retries=2,
                 headers={"User-Agent": BROWSER_UA,
                          "Accept": "application/rss+xml, application/xml, text/xml"})
    body = (r.text or "").strip()
    if not body.startswith("<"):
        raise RuntimeError(f"{label} returned non-XML: {body[:120]}")

    root = ET.fromstring(body)
    out = []
    # RSS <item> and Atom <entry> both appear across these five feeds.
    # Namespace-agnostic. Three shapes in play across these feeds:
    #   RSS 2.0  <item>                      (bare tag)
    #   RSS 1.0  <item> in purl.org/rss/1.0  (namespaced — DW; a bare
    #            iter("item") silently returned ZERO here)
    #   Atom     <entry> in w3.org/2005/Atom
    def _local(tag):
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag
    nodes = [e for e in root.iter() if _local(e.tag) == "item"]
    if not nodes:
        nodes = [e for e in root.iter() if _local(e.tag) == "entry"]
    for node in nodes:
        title = (node.findtext("title") or "").strip()
        if not title:
            for child in node:
                if child.tag.endswith("}title"):
                    title = (child.text or "").strip()
                    break
        link = (node.findtext("link") or "").strip()
        if not link:
            for child in node:
                if child.tag.endswith("}link"):
                    link = (child.get("href") or child.text or "").strip()
                    break
        if not title or not link:
            continue
        raw_date = node.findtext("pubDate") or node.findtext("published") or ""
        if not raw_date:
            for child in node:
                if child.tag.endswith("}published") or child.tag.endswith("}updated"):
                    raw_date = (child.text or "").strip()
                    break
        safe_link = sanitize_url(link)
        safe_img = sanitize_url(_feed_image(node) or "")
        if not safe_link:
            # A feed item whose link is not plain http(s) is not usable and is
            # not worth storing. Dropping it is cheaper than defending it.
            continue
        out.append({
            "title": sanitize_title(title),
            "domain": _domain_of(safe_link),
            "url": safe_link,
            "published_at": _parse_rss_date(raw_date),
            "image": safe_img or None,
        })
    return out


def _feed_image(node):
    """Return a publisher-DECLARED image URL, or None.

    THE DISTINCTION THAT MATTERS (see the legal note in the module docstring,
    which bans `socialimage`):
      * <media:thumbnail>, <media:content type="image/*"> and <enclosure> are
        elements a publisher puts in its OWN feed. Offering them is the point
        of a syndication feed, and using them is ordinary syndication.
      * Scraping og:image off the article page — which is what GDELT's
        `socialimage` is — is taking something that was never offered. That
        remains banned and this function never does it.

    We store the URL only. The image is hot-linked at render time and never
    copied, rehosted or resized by us, so nothing leaves the publisher's
    server and their analytics and cache headers still apply.
    """
    for child in node:
        tag = child.tag.lower()
        if tag.endswith("}thumbnail") or tag.endswith("}content"):
            url = (child.get("url") or "").strip()
            ctype = (child.get("type") or "").lower()
            medium = (child.get("medium") or "").lower()
            if url and (medium == "image" or ctype.startswith("image/")
                        or not (ctype or medium)):
                return url
        if tag == "enclosure":
            url = (child.get("url") or "").strip()
            if url and (child.get("type") or "").lower().startswith("image/"):
                return url
    return None


_RSS_DATE_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %Z",
    "%d %b %Y %H:%M:%S %z",
)


def _parse_rss_date(value):
    s = (value or "").strip()
    if not s:
        return None
    for fmt in _RSS_DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# v46.2 — INGESTION-LAYER ALLOW-LIST (defence in depth).
#
# The browser-side sanitisers are not the right place for this to be the ONLY
# check: data/commodity_news.json is committed to a PUBLIC repo and read by
# several consumers, and a security review found two render paths that had
# forgotten to call them. A hostile or compromised feed should never get a
# javascript:/data: URL or a control character INTO the file in the first
# place. One choke point, applied before anything is stored.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
MAX_TITLE_LEN = 400


def sanitize_url(url):
    """Return the URL only if it is plainly http(s); otherwise ''."""
    u = (url or "").strip()
    if not u:
        return ""
    if _CONTROL_CHARS.search(u):
        return ""
    low = u.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return ""
    return u


def sanitize_title(title):
    """Strip control characters and cap length. Headline text is kept verbatim
    otherwise — we do NOT HTML-escape here, because the file is data, not
    markup, and double-escaping in the renderer is its own bug."""
    t = _CONTROL_CHARS.sub("", (title or "").strip())
    return t[:MAX_TITLE_LEN]


def _domain_of(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# ─────────────────────────────────────────────────────────────────────────────
# Relevance
# ─────────────────────────────────────────────────────────────────────────────
def detect_companies(norm):
    """Return display names of agri-trading companies named in a headline."""
    hits = []
    for name, terms in COMPANY_TERMS.items():
        for term in terms:
            if not contains_term(norm, term):
                continue
            guards = COMPANY_AMBIGUOUS.get(term)
            # "ADM" and "LDC" are common letter strings; require corroboration
            # rather than tagging every headline containing them.
            if guards and not any(contains_term(norm, g) for g in guards):
                continue
            hits.append(name)
            break
    return hits


def detect_disruption(norm):
    """Return the disruption terms a headline names, or []."""
    return [t for t in DISRUPTION_TERMS if contains_term(norm, t)]


def detect_chokepoints(norm):
    """Return corridor/chokepoint phrases named in a headline.

    Energy-dominated headlines are excluded unless they also carry food or
    trade-logistics context. Hormuz and Suez are written about mostly as oil
    stories; without this the route returned a run of crude-price headlines,
    which is a different publication.
    """
    hits = [t for t in CHOKEPOINT_TERMS if contains_term(norm, t)]
    if not hits:
        return []
    if any(contains_term(norm, e) for e in ENERGY_DOMINANT) and \
       not any(contains_term(norm, f) for f in FOOD_CONTEXT):
        return []
    return hits

def classify(title, hint=None):
    """Return (matched_commodities, relevance_score) for a headline.

    `hint` is the commodity whose query produced the item (GDELT) or None (RSS,
    which is unqueried). The hint never bypasses the negative lists — a headline
    returned by the maize query that says "Corn Exchange" is still rejected.

    Matching is whole-word via contains_term(); plain substring matching is what
    finds 'corn' inside 'Cornwall' and 'rice' inside 'Rice University'.
    """
    norm = normalize_title(title)
    if not norm:
        return [], 0.0

    for bad in NEGATIVE_GLOBAL:
        if contains_term(norm, bad):
            return [], 0.0

    matched = []
    # v46.1 — the WIDE table: 34 commodities, matching the flow atlas rather
    # than only the six staples the interpretation layer covers.
    for commodity in NEWS_COMMODITIES:
        if not any(contains_term(norm, syn) for syn in NEWS_SYNONYMS[commodity]):
            continue
        if any(contains_term(norm, bad) for bad in NEWS_NEGATIVE.get(commodity, ())):
            continue
        matched.append(commodity)

    # v46.2 — companies and chokepoints are a SECOND route to relevance.
    # A general business wire writing "Cargill warns on crush margins" or
    # "Red Sea diversions add two weeks" is squarely on topic and names no
    # commodity, so the commodity-only gate dropped both. These do NOT invent
    # a `matched` commodity — that would be fabricating an attribution — they
    # let the item through to be scored on its merits.
    companies = detect_companies(norm)
    chokepoints = detect_chokepoints(norm)

    if not matched and not companies and not chokepoints:
        return [], 0.0

    score = 1.0
    # A named trader or chokepoint is concrete evidence of topicality, worth
    # the same as a commodity match rather than more.
    if companies:
        score += 1.0
    if chokepoints:
        score += 1.0
    # A hinted commodity that also survived the negative list is corroborated
    # by the upstream engine as well as our own match.
    if hint and hint in matched:
        score += 1.0

    for term, weight in POSITIVE.items():
        if contains_term(norm, term):
            score += weight
    for term, penalty in DEMOTE.items():
        if contains_term(norm, term):
            score -= penalty

    return matched, score


def rank_score(relevance, trust, published_at, now):
    """Internal ordering score. Never written to the payload."""
    if published_at is None:
        age_h = MAX_AGE_HOURS
    else:
        age_h = max(0.0, (now - published_at).total_seconds() / 3600.0)
    decay = 0.5 ** (age_h / HALF_LIFE_HOURS)
    return max(relevance, 0.0) * (0.4 + trust) * decay


# ─────────────────────────────────────────────────────────────────────────────
# Dedup — simhash over headline tokens
# ─────────────────────────────────────────────────────────────────────────────
def simhash(tokens):
    """64-bit simhash of a token list. Near-identical headlines land within a
    Hamming distance of DEDUP_HAMMING of each other."""
    if not tokens:
        return 0
    vector = [0] * SIMHASH_BITS
    mask = (1 << SIMHASH_BITS) - 1
    for tok in tokens:
        # Python's builtin hash() is salted per-process (PYTHONHASHSEED), which
        # would make dedup_key unstable across runs and produce phantom diffs in
        # every commit. Use a deterministic FNV-1a instead.
        h = _fnv1a(tok) & mask
        for i in range(SIMHASH_BITS):
            vector[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(SIMHASH_BITS):
        if vector[i] > 0:
            out |= (1 << i)
    return out


def _fnv1a(text):
    h = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def _hamming(a, b):
    return bin(a ^ b).count("1")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)
    oldest = now - timedelta(hours=MAX_AGE_HOURS)

    raw = []            # (raw_item, hint, adapter)
    commodity_status = {}

    # --- TIERS 1-3: publisher feeds, straight from the newsrooms -----------
    # 29 feeds, no aggregator in the middle. Each is isolated: one dead feed
    # costs its own items and nothing else. Failures are counted rather than
    # merely logged, because a slow drift from 29 working feeds to 6 is the
    # kind of decay that otherwise shows up as "the news page looks quiet".
    feed_ok = feed_failed = 0
    for label, url, tier in ALL_FEEDS:
        try:
            items = fetch_institutional_rss(label, url)
            commodity_status[f"_feed:{label}"] = "ok"
            feed_ok += 1
            print(f"  [{tier[:5]}] {label}: {len(items)} raw items (filtered locally)")
            raw.extend((a, None, tier) for a in items)
        except Exception as e:
            commodity_status[f"_feed:{label}"] = "failed"
            feed_failed += 1
            print(f"  [{tier[:5]}] {label}: FAILED — {type(e).__name__}: {e}")
    print(f"[INFO] feeds: {feed_ok} ok, {feed_failed} failed of {len(ALL_FEEDS)}")

    # --- TIER 3: GDELT, opportunistic only ---------------------------------
    # Demoted in v46.1. Its limiter is per-IP and stateful, so under CI egress
    # it returns 429 no matter how politely we space calls. Two commodities
    # only, and a throttle here is a non-event rather than a failure.
    for idx, commodity in enumerate(COMMODITIES[:GDELT_MAX_COMMODITIES]):
        if idx > 0:
            print(f"  [wait] sleeping {GDELT_SLEEP_S}s before next GDELT call "
                  f"(their limiter is per-IP and unforgiving)")
            time.sleep(GDELT_SLEEP_S)
        try:
            articles = fetch_gdelt(commodity)
            commodity_status[commodity] = "ok"
            print(f"  [gdelt] {commodity}: {len(articles)} raw articles")
            raw.extend((a, commodity, "gdelt") for a in articles)
        except ThrottledError as e:
            # Expected under load. Distinct from a real failure so the manifest
            # and the logs don't cry wolf.
            commodity_status[commodity] = "throttled"
            print(f"  [gdelt] {commodity}: THROTTLED — {e}")
        except Exception as e:
            # One bad commodity must never abort the run.
            # http_get raises RuntimeError on a real HTTP 429 (it calls
            # raise_for_status), so the throttle also arrives here, wrapped —
            # classify it as throttled rather than failed so a routine rate
            # limit doesn't read as a broken pipeline.
            msg = str(e)
            throttled = "429" in msg or "Too Many Requests" in msg
            commodity_status[commodity] = "throttled" if throttled else "failed"
            label = "THROTTLED" if throttled else "FAILED"
            print(f"  [gdelt] {commodity}: {label} — {type(e).__name__}: {e}")

    # --- EC agriculture RSS, one unfiltered pull ---------------------------
    try:
        ec_items = fetch_ec_rss()
        commodity_status["_ec_rss"] = "ok"
        print(f"  [ec-rss] {len(ec_items)} raw items (filtered locally)")
        raw.extend((a, None, "ec_rss") for a in ec_items)
    except Exception as e:
        commodity_status["_ec_rss"] = "failed"
        print(f"  [ec-rss] FAILED — {type(e).__name__}: {e}")

    # --- Score, filter, shape ----------------------------------------------
    scored = []
    seen_urls = set()
    rejected_no_match = 0
    rejected_low_score = 0
    for rawitem, hint, adapter in raw:
        url = rawitem["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)

        published_at = rawitem.get("published_at")
        if published_at is not None and published_at < oldest:
            continue

        matched, relevance = classify(rawitem["title"], hint=hint)
        # v46.1 — DOMAIN CREDIT. MIN_RELEVANCE was tuned against GDELT items,
        # which arrive with a commodity hint worth +1.0 because they came back
        # from a commodity-specific query. Publisher feeds are unqueried, so
        # they never collect that point and were being held to a floor a full
        # point higher than the source the floor was calibrated on — which is
        # why 1,903 raw items yielded 4. A dedicated agriculture trade feed is
        # evidence about topic in exactly the way a commodity query is, so it
        # earns the same credit. General wires and institutional feeds do NOT:
        # they are firehoses, and their items must clear the bar unaided.
        if adapter == "trade_press":
            relevance += 1.0
        # v46.2 — classify() now admits company- and chokepoint-qualified
        # items with an empty `matched`, so this gate has to ask the same
        # question classify() asks, not the narrower commodity-only one it
        # asked before. Without this the whole second route was dead code:
        # detect_companies() fired, the score cleared the floor, and the item
        # was dropped one line later.
        _norm = normalize_title(rawitem["title"])
        if not matched and not detect_companies(_norm) and not detect_chokepoints(_norm):
            rejected_no_match += 1
            continue
        if relevance < MIN_RELEVANCE:
            # Counted and reported: a silent drop rate is how a taxonomy bug
            # hides. If this number is ~100% of raw, the keyword lists are
            # broken, not the news.
            rejected_low_score += 1
            continue

        domain = rawitem.get("domain") or _domain_of(url)
        trust = domain_trust(domain)
        # dedup_text(), not normalize_title(): the masthead suffix has to come
        # off or the same syndicated story hashes differently per mirror.
        key = simhash(tokenize(dedup_text(rawitem["title"])))

        item = {
            "title": sanitize_title(rawitem["title"]),   # verbatim, minus control chars
            "source": domain,
            "url": url,   # already allow-listed at parse time
            "published_at": published_at.isoformat() if published_at else None,
            "published_label": _label(published_at, now),
            "matched": matched,
            # Provenance is a STRING, not a metrics object — no number in an
            # item may ever be renderable as a score.
            "provenance": _provenance(adapter),
            # Every item is a third-party CLAIM. Not measured, not verified.
            # Commodity attribution is our own keyword match: modeled, never
            # sourced.
            "quality_flag": "claim",
            "dedup_key": f"{key:016x}",
            # Publisher-declared feed image, or None. Hot-linked at render
            # time, never copied or rehosted. See _feed_image().
            "image": rawitem.get("image"),
            # v46.2 — a company or chokepoint match is why some items qualify
            # at all. Stored so the UI can show WHY an item is here when no
            # commodity is named, rather than showing an unexplained headline.
            "companies": detect_companies(_norm),
            "chokepoints": detect_chokepoints(_norm),
            # v46.2 — which concrete disruptive action the headline names, if
            # any. Empty for ordinary market commentary. Only non-empty items
            # with a resolved country are eligible for the Disturbances board.
            "disruption": detect_disruption(_norm),
        }
        scored.append((rank_score(relevance, trust, published_at, now), key, item))

    # --- Near-duplicate collapse -------------------------------------------
    scored.sort(key=lambda t: t[0], reverse=True)
    kept = []
    kept_keys = []
    for score, key, item in scored:
        if any(_hamming(key, k) <= DEDUP_HAMMING for k in kept_keys):
            continue
        kept_keys.append(key)
        kept.append(item)

    # --- Per-commodity cap, then global cap --------------------------------
    # defaultdict, not a fixed dict: `matched` now carries any of the 34
    # NEWS_COMMODITIES, and a KeyError here would kill the run over a
    # taxonomy widening rather than degrade.
    per_commodity = defaultdict(int)
    capped = []
    for item in kept:
        # v46.2 — `matched` can legitimately be EMPTY now (company- or
        # chokepoint-qualified items name no commodity). all() over an empty
        # list is True, so the old guard silently dropped every one of them.
        if item["matched"] and all(
                per_commodity[c] >= MAX_ITEMS_PER_COMMODITY for c in item["matched"]):
            continue
        for c in item["matched"]:
            per_commodity[c] += 1
        capped.append(item)
        if len(capped) >= MAX_ITEMS_TOTAL:
            break

    print(f"[INFO] {len(raw)} raw → {len(scored)} relevant → {len(kept)} after dedup "
          f"→ {len(capped)} after caps")
    print(f"[INFO] rejected: {rejected_no_match} no commodity match "
          f"(or hit a negative keyword), {rejected_low_score} below relevance floor "
          f"{MIN_RELEVANCE}")
    for c in sorted(per_commodity, key=lambda k: -per_commodity[k])[:14]:
        print(f"  [count] {c:12s} {per_commodity[c]:3d}")

    # --- Last-good preservation (v44 pattern, refresh_usda_psd.py) ---------
    if not capped and _has_existing_data(OUTFILE):
        print(f"[KEEP] commodity_news: every source returned zero usable items — "
              f"preserving existing {OUTFILE} (goes honestly stale rather than blank). "
              f"Status: {commodity_status}")
        return

    # --- Corridor attribution (v46.1) --------------------------------------
    # Turns a headline into "who does this actually reach" by joining the
    # named countries to the bilateral flow atlas. This is the only part of
    # the page a feed reader could not produce, and it is why the page exists.
    annotated = annotate(capped)
    leaderboard = exposure_leaderboard(capped)

    # v46.2 — FEED ATTRITION MUST BE VISIBLE.
    # The per-feed results were recorded in commodity_status but nothing read
    # them: write_json passed no status, so build_source_manifest fell through
    # to its count>0 check and rendered "healthy" even if 40 of 41 sources were
    # dead. One surviving feed produced enough items to hide a 97% outage.
    # A count of items is not a measure of source health.
    feed_total = len(ALL_FEEDS)
    feed_ratio = (feed_failed / feed_total) if feed_total else 0.0
    if feed_ratio >= 0.5:
        feed_state, feed_status = "MAJOR", "degraded_feeds"
    elif feed_ratio >= 0.2:
        feed_state, feed_status = "PARTIAL", "degraded_feeds"
    else:
        feed_state, feed_status = "ok", None
    dead = sorted(k.split(":", 1)[1] for k, v in commodity_status.items()
                  if k.startswith("_feed:") and v != "ok")
    feed_note = (
        f" SOURCE HEALTH: {feed_ok} of {feed_total} publisher feeds returned data"
        + (f"; {feed_failed} FAILED ({feed_state}): {', '.join(dead)}." if dead else ".")
        + (" A headline count cannot show this — one working feed still fills the"
           " page — so the ratio is reported explicitly." if dead else "")
    )
    print(f"[INFO] corridors: {annotated}/{len(capped)} items joined to a trade "
          f"corridor; {len(leaderboard)} countries on the exposure board")

    # If the flow atlas failed to load, EVERY item renders "No mapped corridor"
    # — visually identical to a genuine quiet day. That silently disables the
    # one feature distinguishing this page from a feed reader, so the state
    # goes in the payload rather than only into a CI log nobody tails.
    _atlas_ok = bool(load_flows())
    corridor_note = (
        f" Corridor attribution ACTIVE: {annotated} of {len(capped)} items joined."
        if _atlas_ok else
        " Corridor attribution DISABLED this run — commodity_flows.json could not be "
        "loaded, so every item shows 'no mapped corridor' regardless of content. That "
        "is a load failure, NOT a quiet news day."
    )

    write_json(
        OUTFILE,
        {
            "items": capped,
            "commodity_status": commodity_status,
            "exposure_leaderboard": leaderboard,
            "sources": (
                [{"adapter": "publisher_rss", "url": url, "label": label, "tier": tier}
                 for label, url, tier in ALL_FEEDS]
                + [{"adapter": "gdelt_doc_v2", "url": GDELT_URL,
                    "label": "GDELT DOC 2.0 (ArtList)", "tier": "supplementary"},
                   {"adapter": "ec_agriculture_rss", "url": EC_RSS_URL,
                    "label": "European Commission — Agriculture and rural development",
                    "tier": "institutional"}]
            ),
        },
        source=f"{len(ALL_FEEDS)} publisher RSS feeds + GDELT DOC 2.0 (supplementary)",
        status=feed_status,
        notes=(
            "Third-party headlines only. Every item is a CLAIM by its publisher, "
            "not measured data — quality_flag='claim' on all items. Commodity AND "
            "country attribution are MODELED (our keyword match against the "
            "headline), never sourced; corridor TONNAGES are sourced from "
            "commodity_flows.json. Those two are different kinds of claim and the "
            "UI must not blur them: a modeled country match pointing at a sourced "
            "tonnage is still a modeled claim about relevance. Exposure means a "
            "headline TOUCHES a corridor — no causal claim is made about prices. "
            "Headlines are stored verbatim with a link; no article body, summary, "
            "snippet or image is stored. Relevance, publisher-trust and recency "
            "scores are internal ranking inputs and are deliberately not published. "
            "Sources are the publishers' own syndication feeds — no search-engine "
            "aggregator. No Google News: its feed terms forbid this use. No WTO: "
            "its robots.txt disallows all non-Google automated fetching."
            + feed_note
            + corridor_note
        ),
    )


_PROVENANCE_BY_TIER = {
    "gdelt": "GDELT DOC 2.0 ArtList",
    "trade_press": "Publisher RSS (agriculture/commodity trade press)",
    "wire": "Publisher RSS (general news wire)",
    "institutional": "Publisher RSS (institutional/IGO newsroom)",
    "ec_rss": "European Commission agriculture RSS",
}


def _provenance(adapter):
    base = _PROVENANCE_BY_TIER.get(adapter, "Publisher RSS")
    return (f"{base}; commodity and country attribution modeled "
            f"(headline keyword match), corridor tonnage sourced from the "
            f"bilateral flow atlas")


def _label(published_at, now):
    """Human-readable recency label. A string, not a number — nothing here is
    meant to be plotted."""
    if published_at is None:
        return "date unknown"
    delta = now - published_at
    hours = delta.total_seconds() / 3600.0
    if hours < 1:
        return "within the hour"
    if hours < 24:
        return f"{int(hours)}h ago"
    days = int(hours // 24)
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    return published_at.strftime("%d %b %Y")


if __name__ == "__main__":
    main()
