"""
Build-time AI interpretation per commodity — "what moved, and what could plausibly follow".

WHY THIS IS SAFE (read docs/handoffs/DECISIONS.md first)
--------------------------------------------------------
v20.32 removed a live, client-side LLM feature because any browser-visible API key
is a leaked API key. That rule stands. This script is a DIFFERENT thing: it runs
server-side inside GitHub Actions at BUILD time, using a key from Actions secrets,
and bakes plain text into data/commodity_interpretation.json. The published static
site renders that text. No key ever reaches a browser.

PROVIDER-AGNOSTIC BY DESIGN
---------------------------
Every provider sits behind one thin function, `call_llm(prompt) -> str`, spoken
over plain REST with `requests` — no provider SDKs, matching this repo's
stdlib+requests stack. Swapping providers is a one-line change to PROVIDERS.

  NEWS_LLM_PROVIDER = groq | gemini | anthropic | none
  keys:               GROQ_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY

If NEWS_LLM_PROVIDER is unset, we auto-detect by key presence in the order
groq -> gemini -> anthropic. With NO key at all (the zero-cost default) the
deterministic template path runs on its own and still produces an honest,
fully-sourced output file. The LLM only ever adds connective prose.

THE LOAD-BEARING RULE — NO NUMBER IS EVER WRITTEN BY THE MODEL
--------------------------------------------------------------
Every figure is computed here, in Python, from the committed source files
(worldbank_pink_sheet.json, fao_ffpi.json, usda_psd.json, commodity_news.json).
Those figures are passed to the model as a locked JSON block. The model is
instructed to write connective prose only.

Instructions are not enforcement. So the returned text is VALIDATED on three
independent axes (validate_text):

  1. MAGNITUDE — every numeric token must match a value in the locked block at
     the token's own precision. Dates license only their own verbatim
     reproduction plus the year; they are not a blanket digit allowlist.
  2. DIRECTION — a magnitude drawn from a signed change fact must not be paired
     with the opposite direction word. A fact of -8.8 cannot license "up 8.8%".
  3. WRITTEN QUANTITIES — "doubled", "halved", "twice", "a third" and friends are
     quantitative claims with no digits; no fact can license them.

Any hit rejects the whole interpretation and the commodity falls back to the
deterministic template. The substitution happens in the OUTPUT, not merely in a
`validation` field: publishable() re-validates the exact text about to be
written, so rejected prose cannot reach the published file even if an earlier
stage misbehaves.

Degradation (every path exits 0 — this must never crash run_all):
  * no key / NEWS_LLM_PROVIDER=none -> deterministic templates for all commodities;
                                the file is still written, complete and honest.
  * commodity_news.json absent -> news facts are null; the prompt says so and the
                                prose must say inputs are thin.
  * API error / rate limit / empty reply -> deterministic template for that commodity.
  * numeric validation failure -> deterministic template, rejection recorded in output.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from _common import DATA_DIR, write_json
from _news_taxonomy import COMMODITIES as NEWS_COMMODITIES

OUTPUT = "commodity_interpretation.json"

# Provider registry. Model IDs verified against official docs on 2026-07-18:
#   groq      console.groq.com/docs/models       -> llama-3.3-70b-versatile (production)
#   gemini    ai.google.dev/gemini-api/docs/models -> gemini-2.5-flash (stable, free tier)
#   anthropic claude-api skill model catalogue   -> claude-opus-4-8
# Re-verify before changing any of these; do not write model IDs from memory.
PROVIDERS = {
    "groq": {
        "key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
        "free_tier": "30 req/min, 1000 req/day, 12k tokens/min, 100k tokens/day",
    },
    "gemini": {
        "key_env": "GEMINI_API_KEY",
        "model": "gemini-2.5-flash",
        # Google no longer publishes per-model free-tier numbers in the docs; they
        # are account-specific and shown in AI Studio. Do not quote a figure here.
        "free_tier": "free tier available; exact quota is account-specific (see AI Studio)",
    },
    "anthropic": {
        "key_env": "ANTHROPIC_API_KEY",
        "model": "claude-opus-4-8",
        "free_tier": "paid — no free tier",
    },
}

# Order used when NEWS_LLM_PROVIDER is unset: cheapest-free first.
PROVIDER_PRIORITY = ("groq", "gemini", "anthropic")

# This job runs 4x/day over 6 commodities = 24 calls/day, comfortably inside
# Groq's 1000 req/day. The 12k tokens/min ceiling is the binding one, so we
# space the per-commodity calls out rather than bursting all six.
CALL_SPACING_SECONDS = float(os.environ.get("NEWS_LLM_SPACING", "6"))
REQUEST_TIMEOUT = 60

# SINGLE SOURCE OF TRUTH FOR *WHICH* COMMODITIES EXIST
# ----------------------------------------------------
# The commodity list lives in ONE place: _news_taxonomy.COMMODITIES. It is the
# list the news pull actually queries, so it is the only list that can honestly
# describe what news coverage exists. This module previously kept its own
# six-entry dict which had drifted (it carried `cocoa`, which the news feed never
# queries, and lacked `fertilizer`, which it does) — so `cocoa` shipped with
# news_available=true and zero headlines forever, and fertilizer news was
# collected but never interpreted. Both were visible in live data.
#
# SOURCE_MAP only says WHERE each commodity's numbers come from. Adding a
# commodity means adding it to _news_taxonomy.COMMODITIES; the assertion below
# then forces a matching SOURCE_MAP entry rather than letting the two silently
# diverge again.
#
# None means that source has no series for this commodity; the fact is emitted as
# null rather than substituted from something adjacent. In particular there is no
# single "fertilizer" Pink Sheet series — urea is the closest single benchmark and
# is labelled as urea in the facts (`price_series`), never as "fertilizer".
# v47 — wheat and rice were pinned to None because refresh_worldbank_pink_sheet
# silently dropped both series (a comma in 'Wheat, US SRW' survived code
# normalisation and missed the alias). That was a parser bug, not a real gap in
# the Pink Sheet, and it is why the wheat interpretation used to open with "the
# absence of price data makes it difficult to assess". Both series now parse.
SOURCE_MAP = {
    "wheat":      {"pink": "wheat",     "psd": "wheat",    "ffpi": "cereals", "label": "Wheat"},
    "maize":      {"pink": "maize",     "psd": "corn",     "ffpi": "cereals", "label": "Maize"},
    "rice":       {"pink": "rice",      "psd": "rice",     "ffpi": "cereals", "label": "Rice"},
    "soybeans":   {"pink": "soybeans",  "psd": "soybeans", "ffpi": "oils",    "label": "Soybeans"},
    "palm_oil":   {"pink": "palm_oil",  "psd": None,       "ffpi": "oils",    "label": "Palm oil"},
    "fertilizer": {"pink": "urea",      "psd": None,       "ffpi": None,      "label": "Fertilizer (urea benchmark)"},
    # v47 — added once the Pink Sheet parser stopped dropping beef and sugar.
    # No USDA PSD series for any of the three, so their facts carry price and
    # FFPI only; _psd_facts emits nulls and the prose says the input is
    # unavailable rather than inventing a production figure.
    "beef":       {"pink": "beef",      "psd": None,       "ffpi": "meat",    "label": "Beef"},
    "dairy":      {"pink": None,        "psd": None,       "ffpi": "dairy",   "label": "Dairy"},
    "sugar":      {"pink": "sugar",     "psd": None,       "ffpi": "sugar",   "label": "Sugar"},
}

_missing = set(NEWS_COMMODITIES) - set(SOURCE_MAP)
_extra = set(SOURCE_MAP) - set(NEWS_COMMODITIES)
if _missing or _extra:
    raise RuntimeError(
        "SOURCE_MAP is out of sync with _news_taxonomy.COMMODITIES "
        f"(missing: {sorted(_missing)}, extra: {sorted(_extra)}). The commodity "
        "list has exactly one home; fix _news_taxonomy.COMMODITIES and mirror the "
        "source mapping here."
    )

# Iteration order follows the taxonomy so the two files cannot disagree on order
# either (the news counts printed by refresh_commodity_news.py line up 1:1).
COMMODITIES = {k: SOURCE_MAP[k] for k in NEWS_COMMODITIES}

SYSTEM_PROMPT = """You write short interpretive notes for a food-security dashboard.

ABSOLUTE RULE: you must never write a number, percentage, date, month, year, or
quantity that is not already present in the FACTS block you are given. Do not
compute, derive, round differently, average, or infer any figure. If a figure is
null in FACTS, say the input is unavailable — never estimate it.

Style and substance rules:
- Never reverse a direction. If a change figure is negative, it fell; if
  positive, it rose. Do not describe a fall as a rise or vice versa.
- Never use a written-out quantity either: no "doubled", "halved", "twice",
  "a third", "tenfold", "ten percent". Those are numbers too.
- 2 to 3 sentences. No more.
- Describe what moved (using only FACTS figures), then what could PLAUSIBLY follow.
- Forward-looking statements must be explicitly hedged pathways, never predictions
  or forecasts. This project maps structural exposure; it does not claim predictive
  accuracy. Never imply it does.
- You may name a news source (e.g. "Reuters reporting") but must not assert a
  headline's claim as established fact — attribute it.
- If the inputs are thin or missing, say so plainly instead of inventing narrative.

Formatting rules (these change how a figure is WRITTEN, never its value):
- Field NAMES are not prose. Never write a field name, and never write the words
  "per unit". Quote a price as the value followed by price_unit verbatim — e.g.
  197.1 ($/mt). If price_unit is null, give the bare number with no unit words.
  Say it in English instead. The three most often got wrong:
    ffpi_index      -> "the FAO <ffpi_component> price index"
    news_available  -> do not mention it at all; it is a build flag, not a fact
    headline_count  -> "cited headlines" or just describe them
  A sentence containing an underscore is always wrong. Rewrite it.
- A field whose name contains "_pct" is a percentage. Attach a % sign to it:
  write 33.3%, never a bare 33.3.
- A field whose name ends in "_kt" is thousand tonnes. Write it with comma
  thousands separators and the unit kt: 1,294,926 kt. Use commas, never spaces
  or periods, as the group separator.
- No preamble, no headings, no bullet points, no closing offer of further help.
  Respond with the 2-3 sentences only."""


# ---------------------------------------------------------------- fact building

# Input-load outcomes, recorded so a missing or corrupt input degrades VISIBLY.
# Before this, `_load` swallowed every exception and returned None, so a deleted
# or truncated worldbank_pink_sheet.json produced a normal-looking output file
# with silently-null price facts and no trace anywhere that an input had failed.
INPUT_STATUS = {}


def _load(name):
    """Load data/<name>. Records the outcome in INPUT_STATUS; never raises."""
    path = DATA_DIR / name
    if not path.exists():
        INPUT_STATUS[name] = "absent"
        print(f"[FAIL] input data/{name} is ABSENT — every fact derived from it "
              f"will be null and is recorded as unavailable, not as zero.")
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception as e:
        INPUT_STATUS[name] = f"unreadable: {type(e).__name__}: {e}"
        print(f"[FAIL] input data/{name} is UNREADABLE ({type(e).__name__}: {e}) — "
              f"every fact derived from it will be null.")
        return None
    INPUT_STATUS[name] = "ok"
    return payload


def _payload(envelope):
    if isinstance(envelope, dict) and "data" in envelope:
        return envelope["data"]
    return envelope


def _r(value, places=1):
    """Round for publication, or return None. Everything the model may see passes here."""
    if value is None:
        return None
    try:
        v = round(float(value), places)
    except (TypeError, ValueError):
        return None
    return int(v) if places == 0 else v


def _price_facts(pink_payload, series_key):
    out = {
        # Which Pink Sheet series this actually is. For fertilizer the benchmark
        # is urea, not a "fertilizer" composite; naming it here stops the prose
        # from generalising one series into a category.
        "price_series": series_key,
        "price_usd_per_unit": None,
        "price_unit": None,
        "price_month": None,
        "price_prev_month_value": None,
        "price_change_mom_pct": None,
    }
    if not series_key or not isinstance(pink_payload, dict):
        return out
    series = (pink_payload.get("series") or {}).get(series_key)
    if not isinstance(series, dict):
        return out
    out["price_usd_per_unit"] = _r(series.get("latest_value"), 1)
    out["price_unit"] = series.get("unit")
    out["price_month"] = series.get("latest_month")
    out["price_prev_month_value"] = _r(series.get("previous_value"), 1)
    out["price_change_mom_pct"] = _r(series.get("change_mom_pct"), 1)
    return out


def _ffpi_facts(ffpi_payload, component):
    out = {"ffpi_component": component, "ffpi_index": None,
           "ffpi_month": None, "ffpi_change_mom_pct": None}
    if not component or not isinstance(ffpi_payload, dict):
        return out
    latest = ffpi_payload.get("latest") or {}
    change = ffpi_payload.get("change_mom_pct") or {}
    out["ffpi_index"] = _r(latest.get(component), 1)
    out["ffpi_month"] = latest.get("month")
    if isinstance(change, dict):
        out["ffpi_change_mom_pct"] = _r(change.get(component), 1)
    return out


# USDA PSD rows carry a per-FIELD marketing year (`_year_production_kt` etc.).
# Those years are NOT uniform: on the 2026-07-18 file the production vintages run
# 1975, 1979, 1990, 1991, 1992, 1995, 1996, 1997, 1998, 2016, 2022 and 2026.
# Summing all of them into one number and calling it a current "global" total is
# a fabricated aggregate — it is not a figure that exists in any year. So we do
# what validate_data.py does for the Comtrade/PSD unit-price check: pick the
# latest vintage present and admit only rows within _MAX_YEAR_GAP of it. The
# surviving range is then stated explicitly in the facts AND in the prose, so
# even the in-band aggregate is never presented as a single-year world total.
_PSD_MAX_YEAR_GAP = 2

_PSD_FIELDS = (("production_kt", "global_production_kt"),
               ("stocks_kt", "global_stocks_kt"),
               ("imports_kt", "global_imports_kt"))


def _psd_facts(psd_payload, psd_key):
    """Global totals, restricted to one consistent recent marketing-year vintage.

    Returns nulls rather than a cross-decade sum when no consistent vintage
    exists. The accepted year range is always reported alongside the totals.
    """
    out = {
        "global_production_kt": None,
        "global_stocks_kt": None,
        "global_imports_kt": None,
        "global_stocks_pct_of_production": None,
        "psd_countries_counted": None,
        "psd_countries_excluded_stale_vintage": None,
        "psd_marketing_year_min": None,
        "psd_marketing_year_max": None,
        "psd_max_year_gap": _PSD_MAX_YEAR_GAP,
    }
    if not psd_key or not isinstance(psd_payload, dict):
        return out

    rows = []
    for row in psd_payload.values():
        if isinstance(row, dict) and isinstance(row.get(psd_key), dict):
            rows.append(row[psd_key])
    if not rows:
        return out

    # Reference vintage = the most recent year any admitted field reports.
    years = [c.get(f"_year_{field}") for c in rows for field, _ in _PSD_FIELDS]
    years = [y for y in years if isinstance(y, int)]
    if not years:
        # No vintage metadata at all: we cannot prove the rows are comparable,
        # so we do not sum them. Silence beats a fabricated aggregate.
        return out
    ref = max(years)

    totals = {out_key: 0.0 for _, out_key in _PSD_FIELDS}
    counted = set()
    excluded = set()
    for i, c in enumerate(rows):
        used = False
        for field, out_key in _PSD_FIELDS:
            y = c.get(f"_year_{field}")
            if not isinstance(y, int) or abs(y - ref) > _PSD_MAX_YEAR_GAP:
                continue
            try:
                totals[out_key] += float(c.get(field) or 0.0)
            except (TypeError, ValueError):
                continue
            used = True
        (counted if used else excluded).add(i)

    if not counted:
        return out

    in_band = [y for y in years if abs(y - ref) <= _PSD_MAX_YEAR_GAP]
    out["psd_countries_counted"] = len(counted)
    out["psd_countries_excluded_stale_vintage"] = len(excluded)
    out["psd_marketing_year_min"] = min(in_band)
    out["psd_marketing_year_max"] = max(in_band)
    for _, out_key in _PSD_FIELDS:
        out[out_key] = _r(totals[out_key], 0)
    prod = totals["global_production_kt"]
    if prod > 0:
        out["global_stocks_pct_of_production"] = _r(
            100.0 * totals["global_stocks_kt"] / prod, 1)
    return out


def _matches_commodity(item, key):
    """refresh_commodity_news.py tags each item with `matched: [commodity keys]`.
    Older/alternative shapes use `commodity` / `commodities`."""
    for field in ("matched", "commodities", "commodity"):
        v = item.get(field)
        if isinstance(v, str) and v == key:
            return True
        if isinstance(v, (list, tuple)) and key in v:
            return True
    return False


def _iter_news_items(news_payload, key, label):
    """commodity_news.json is authored by a separate pipeline; tolerate shapes.

    Canonical shape (v46): data = {"items": [...], "commodity_status": {...}, ...}
    where each item carries `matched`. Returns a list (possibly empty) or None
    when the file is unreadable/absent for this commodity.
    """
    if news_payload is None:
        return None
    if isinstance(news_payload, list):
        return [it for it in news_payload
                if isinstance(it, dict) and _matches_commodity(it, key)]
    if not isinstance(news_payload, dict):
        return None
    items = news_payload.get("items") or news_payload.get("articles")
    if isinstance(items, list):
        return [it for it in items
                if isinstance(it, dict) and _matches_commodity(it, key)]
    # Fallback: payload keyed directly by commodity
    for candidate in (key, label, label.lower()):
        v = news_payload.get(candidate)
        if isinstance(v, list):
            return v
        if isinstance(v, dict) and isinstance(v.get("items"), list):
            return v["items"]
    return None


def _parse_dt(value):
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _news_facts(news_payload, key, label):
    out = {
        "news_available": news_payload is not None,
        "headline_count": None,
        "headlines_last_7_days": None,
        "headlines_prior_7_days": None,
        "sources": [],
        "headlines": [],
    }
    items = _iter_news_items(news_payload, key, label)
    if items is None:
        return out
    out["headline_count"] = len(items)
    now = datetime.now(timezone.utc)
    recent = prior = 0
    dated = 0
    sources, titles = [], []
    for it in items:
        if not isinstance(it, dict):
            continue
        src = it.get("source") or it.get("publisher") or it.get("source_name")
        if isinstance(src, str) and src and src not in sources:
            sources.append(src)
        title = it.get("title") or it.get("headline")
        if isinstance(title, str) and title and len(titles) < 8:
            titles.append({"title": title, "source": src})
        dt = _parse_dt(it.get("published") or it.get("published_at")
                       or it.get("date") or it.get("timestamp"))
        if dt:
            dated += 1
            age = now - dt
            if age <= timedelta(days=7):
                recent += 1
            elif age <= timedelta(days=14):
                prior += 1
    if dated:
        out["headlines_last_7_days"] = recent
        out["headlines_prior_7_days"] = prior
    out["sources"] = sources[:8]
    out["headlines"] = titles
    return out


def build_facts(key, spec, pink, ffpi, psd, news):
    facts = {"commodity": spec["label"]}
    facts.update(_price_facts(pink, spec["pink"]))
    facts.update(_ffpi_facts(ffpi, spec["ffpi"]))
    facts.update(_psd_facts(psd, spec["psd"]))
    facts.update(_news_facts(news, key, spec["label"]))
    return facts


def facts_are_thin(facts):
    """True when there is not enough measured input to say anything substantive."""
    substantive = [
        facts.get("price_usd_per_unit"),
        facts.get("ffpi_index"),
        facts.get("global_production_kt"),
        facts.get("headline_count"),
    ]
    return sum(1 for v in substantive if v is not None) < 2


# ------------------------------------------------------------- number validation

NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


# Fields whose numbers are NOT ours. Headline text is a third-party claim; a
# figure inside a headline is unverified, so it must not become quotable. Numbers
# appearing only there are treated as unsupported and reject the interpretation.
UNTRUSTED_NUMBER_FIELDS = {"title", "headline", "url", "summary"}


# Date-shaped string values ("2026-06", "2026-06-15"). These are the ONLY string
# facts allowed to license digits, and they license only themselves — see
# _allowed_numbers.
DATE_STRING_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _allowed_numbers(facts):
    """The numeric values the model may reproduce.

    Returns (signed, unsigned, years, date_strings).

      signed        exact values as they appear in the facts. A token written
                    with an explicit +/- must match one of THESE, so a fact of
                    -8.8 can never license the literal "+8.8".
      unsigned      |value| for each fact, so "down 8.8%" is expressible. The
                    direction word is policed separately by
                    find_sign_inversions() — magnitude and direction are two
                    different checks and collapsing them is what let a
                    sign-flipped claim through.
      years         4-digit years mined from date-shaped strings.
      date_strings  the verbatim date tokens, masked out of the text before
                    tokenising.

    WHAT CHANGED AND WHY (2026-07-18 review): the previous version mined every
    digit out of every non-untrusted string and dropped it into one flat
    allowlist. Because month strings like "2026-06" contributed 2026, 6 and -6,
    any figure that happened to coincide with a date component passed
    validation. A date is not evidence for an arbitrary quantity. Dates now
    license only their own verbatim reproduction plus the year as an integer.
    """
    signed, unsigned, years, date_strings = set(), set(), set(), set()

    def add(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        signed.add(f)
        unsigned.add(abs(f))

    def walk(node, field=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, field)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            add(node)
        elif isinstance(node, str):
            if field in UNTRUSTED_NUMBER_FIELDS:
                return
            for token in DATE_STRING_RE.findall(node):
                date_strings.add(token)
            for m in DATE_STRING_RE.finditer(node):
                date_strings.add(m.group(0))
            for m in YEAR_RE.finditer(node):
                years.add(int(m.group(0)))

    walk(facts)
    return signed, unsigned, years, date_strings


def _decimals(token):
    return len(token.split(".")[1]) if "." in token else 0


def _mask_dates(text, date_strings):
    """Blank out verbatim date tokens so their digits are not re-tokenised."""
    for d in sorted(date_strings, key=len, reverse=True):
        text = text.replace(d, " <date> ")
    return text


def find_unsupported_numbers(text, facts):
    """Return the numeric tokens in `text` that no fact supports.

    A token is supported if some allowed value rounds to it at the token's own
    precision — so 8.8 is allowed for a fact of 8.83, but 8.9 is not, and any
    number with no corresponding fact at all is rejected. A token carrying an
    explicit sign must match a fact of that same sign.
    """
    signed, unsigned, years, date_strings = _allowed_numbers(facts)
    bad = []
    for token in NUMBER_RE.findall(_mask_dates(text, date_strings)):
        cleaned = token.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        places = _decimals(cleaned)
        explicit_sign = cleaned[0] in "+-"
        pool = signed if explicit_sign else unsigned
        candidate = value if explicit_sign else abs(value)
        if any(round(a, places) == round(candidate, places) for a in pool):
            continue
        # Bare integers may also be a year quoted from a date fact.
        if (not explicit_sign and places == 0 and float(value).is_integer()
                and int(value) in years):
            continue
        bad.append(token)
    return bad


# ---- direction (sign-inversion) check ---------------------------------------
#
# A magnitude check alone cannot see the difference between "down 8.8%" and
# "up 8.8%" — both reproduce a permitted magnitude. When the underlying fact is
# -8.8, the second sentence is a fabricated reversal of the only thing the number
# was for. This is the highest-consequence failure the guard can miss, because
# the output reads as confidently sourced.
_UP_WORDS = re.compile(
    r"\b(up|rose|rise|rises|risen|rising|gain|gained|gains|higher|climb|climbed|"
    r"climbing|increase|increased|increases|increasing|jump|jumped|surge|surged|"
    r"strengthen|strengthened|advance|advanced|firmer|firmed|rallied|rally|"
    r"appreciated|added|grew|growth|expansion|expanded)\b", re.I)
_DOWN_WORDS = re.compile(
    r"\b(down|fell|fall|falls|fallen|falling|drop|dropped|drops|decline|declined|"
    r"declines|declining|lower|slip|slipped|slid|slide|ease|eased|easing|weaken|"
    r"weakened|weaker|retreat|retreated|shed|lost|loss|softer|softened|contracted|"
    r"contraction|decrease|decreased|shrank|shrunk)\b", re.I)

# Facts whose sign carries directional meaning. Levels (a price, an index) have
# no direction; only changes do.
_DIRECTIONAL_FACT_SUFFIXES = ("_change_mom_pct", "_pct_change", "_change_pct")

_WINDOW_BEFORE = 70
_WINDOW_AFTER = 40


def _directional_facts(facts):
    for k, v in (facts or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if any(k.endswith(s) for s in _DIRECTIONAL_FACT_SUFFIXES) and v != 0:
            yield k, float(v)


def find_sign_inversions(text, facts):
    """Return descriptions of magnitudes used with the wrong direction word."""
    problems = []
    for key, value in _directional_facts(facts):
        mag = abs(value)
        for m in NUMBER_RE.finditer(text):
            cleaned = m.group(0).replace(",", "")
            try:
                tok = abs(float(cleaned))
            except ValueError:
                continue
            places = _decimals(cleaned)
            if round(tok, places) != round(mag, places):
                continue
            window = text[max(0, m.start() - _WINDOW_BEFORE):m.end() + _WINDOW_AFTER]
            up, down = bool(_UP_WORDS.search(window)), bool(_DOWN_WORDS.search(window))
            if up == down:      # neither, or genuinely ambiguous — magnitude check governs
                continue
            if (value < 0 and up) or (value > 0 and down):
                problems.append(
                    f"{key}={value} written as "
                    f"{'increase' if up else 'decrease'} near '{m.group(0)}'")
    return problems


# ---- written-out quantities --------------------------------------------------
#
# NUMBER_RE sees digits only. "Production doubled", "stocks fell by a third" and
# "twice the previous month" are quantitative claims with no digit in them, so
# the old guard passed them unconditionally. None of them can be derived from the
# facts block, so any of them rejects the interpretation.
_WORD_QUANTITY_RE = re.compile(
    r"\b("
    r"doubl(?:e|ed|es|ing)|tripl(?:e|ed|es|ing)|quadrupl(?:e|ed|es|ing)|"
    r"halv(?:e|ed|es|ing)|a? ?half|twice|thrice|"
    r"(?:two|three|four|five|ten|hundred)[- ]?fold|\d+[- ]?fold|"
    r"a (?:third|quarter|fifth|tenth)|two[- ]thirds|three[- ]quarters|"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|hundred)[- ](?:percent|per cent)|"
    r"percentage points?"
    r")\b", re.I)


def find_word_quantities(text):
    """Return spelled-out quantitative claims, which no fact can license."""
    return sorted({m.group(0).strip().lower() for m in _WORD_QUANTITY_RE.finditer(text)})


def find_field_names(text, facts):
    """v47 — return FACTS field names the model pasted into prose.

    Observed live: "a -8.8% change from the previous month, as the
    price_prev_month_value was 216.2". The figure was correctly licensed, so the
    numeric checks passed and shipped a raw identifier to readers. Asking the
    model not to do this in SYSTEM_PROMPT is necessary but not sufficient — the
    same run that was told the rule broke it. Only keys with an underscore are
    checked: bare words like 'commodity' are ordinary English and would fire on
    innocent prose.
    """
    low = text.lower()
    hits = set()
    for k in facts:
        if "_" not in k:
            continue
        # Identifier boundaries, not bare substring. Without \b, 'price_month'
        # fires inside a legitimate 'price_monthly' and a leak written with
        # spaces or hyphens ('price prev month value') slips past entirely.
        pattern = r"\b" + r"[\s_\-]+".join(re.escape(p) for p in k.lower().split("_")) + r"\b"
        if re.search(pattern, low):
            hits.add(k)
    return sorted(hits)


def validate_text(text, facts):
    """Every honesty check that applies to publishable prose.

    Returns (ok: bool, detail: dict). Used both to accept model output and, at
    write time, as the last gate before anything reaches the published file.
    """
    detail = {
        "unsupported_numbers": find_unsupported_numbers(text, facts),
        "sign_inversions": find_sign_inversions(text, facts),
        "word_quantities": find_word_quantities(text),
        "field_names": find_field_names(text, facts),
    }
    return (not any(detail.values())), detail


# -------------------------------------------------------- deterministic fallback

def _kt(v):
    """v47 — thousand-tonne totals with comma group separators. The model is now
    told to write '1,294,926 kt'; the template is the text a reader sees whenever
    the model is unavailable or rejected, so it must not look shabbier than the
    thing it stands in for. Commas are safe against the validator: NUMBER_RE
    admits them inside a token and find_unsupported_numbers strips them before
    comparing, so a comma'd figure still matches its locked fact."""
    return f"{int(v):,}" if isinstance(v, (int, float)) else str(v)


def deterministic_text(facts):
    """Pure-Python sentences. Used when there is no model output we can trust."""
    name = facts["commodity"]
    bits = []
    price, month = facts.get("price_usd_per_unit"), facts.get("price_month")
    chg = facts.get("price_change_mom_pct")
    if price is not None and month:
        unit = facts.get("price_unit")
        s = f"{name} benchmark price was {price}{' ' + unit if unit else ''} in {month}"
        if chg is not None:
            s += f", {'up' if chg >= 0 else 'down'} {abs(chg)}% month on month"
        bits.append(s + " (World Bank Pink Sheet).")
    idx, imonth = facts.get("ffpi_index"), facts.get("ffpi_month")
    if idx is not None and imonth:
        s = f"The FAO {facts.get('ffpi_component')} price index stood at {idx} in {imonth}"
        ic = facts.get("ffpi_change_mom_pct")
        if ic is not None:
            s += f" ({'+' if ic >= 0 else ''}{ic}% month on month)"
        bits.append(s + ".")
    prod, stocks = facts.get("global_production_kt"), facts.get("global_stocks_kt")
    if prod is not None:
        # The year range is stated in the sentence itself. This total is a sum
        # across reporting countries whose marketing years fall inside that
        # range — it is never described as a single-year world total.
        y0 = facts.get("psd_marketing_year_min")
        y1 = facts.get("psd_marketing_year_max")
        span = (f"marketing year {y0}" if y0 == y1 else
                f"marketing years {y0} to {y1}") if y0 and y1 else "the reported marketing years"
        s = (f"USDA PSD reports {_kt(prod)} kt of production summed across "
             f"{facts.get('psd_countries_counted')} reporting countries in {span}")
        if stocks is not None:
            s += f", against {_kt(stocks)} kt of ending stocks"
        excl = facts.get("psd_countries_excluded_stale_vintage")
        if excl:
            s += (f"; {excl} further country rows were excluded because their "
                  f"marketing year is older than that range")
        bits.append(s + ".")
    hc = facts.get("headline_count")
    if hc is not None:
        bits.append(f"{hc} cited headlines are attached to this commodity in the current news pull.")
    if not bits:
        return (f"No measured price, balance-sheet, or news inputs were available for {name} "
                f"in this build. Nothing is inferred here.")
    bits.append("This is a measured summary only; no pathway is asserted.")
    return " ".join(bits)


# ------------------------------------------------------------------- model call

def build_prompt(facts):
    return (
        "FACTS (the ONLY figures you may reference; treat as locked):\n"
        + json.dumps(facts, indent=2, ensure_ascii=False)
        + "\n\nWrite the 2-3 sentence interpretation for "
        + facts["commodity"]
        + ". Reference only figures that appear verbatim above. Frame anything "
          "forward-looking as a hedged, plausible pathway conditional on those "
          "figures — not a forecast."
    )


def resolve_provider():
    """Returns (provider_name, api_key) or (None, None). Never raises.

    Explicit NEWS_LLM_PROVIDER wins; 'none' disables the LLM entirely. Otherwise
    auto-detect by key presence, cheapest-free first.
    """
    requested = (os.environ.get("NEWS_LLM_PROVIDER") or "").strip().lower()
    if requested in ("none", "off", "disabled"):
        return None, None
    if requested:
        spec = PROVIDERS.get(requested)
        if not spec:
            print(f"[WARN] NEWS_LLM_PROVIDER={requested!r} is not one of "
                  f"{sorted(PROVIDERS)} — falling back to deterministic templates.")
            return None, None
        key = os.environ.get(spec["key_env"])
        if not key:
            print(f"[WARN] NEWS_LLM_PROVIDER={requested} but {spec['key_env']} is "
                  f"unset — falling back to deterministic templates.")
            return None, None
        return requested, key
    for name in PROVIDER_PRIORITY:
        key = os.environ.get(PROVIDERS[name]["key_env"])
        if key:
            return name, key
    return None, None


def _post(url, *, headers, payload, attempts=3):
    """POST JSON with backoff on 429/5xx. Raises on final failure."""
    last = None
    for attempt in range(attempts):
        try:
            r = requests.post(url, headers=headers, json=payload,
                              timeout=REQUEST_TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            else:
                r.raise_for_status()
                return r.json()
        except Exception as e:
            last = e
        if attempt < attempts - 1:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"POST {url.split('?')[0]} failed: {last}")


def call_llm(provider, api_key, system_prompt, user_prompt):
    """THE single provider seam. Returns plain text. Raises on failure.

    Swapping providers is a one-line change (NEWS_LLM_PROVIDER / PROVIDERS).
    Whatever comes back is untrusted prose — every number in it is validated
    downstream against the locked facts block regardless of provider.
    """
    model = PROVIDERS[provider]["model"]

    if provider == "groq":
        # OpenAI-compatible chat completions.
        body = _post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            payload={
                "model": model,
                "temperature": 0.2,     # low: this is summarisation, not ideation
                "max_tokens": 400,
                "messages": [{"role": "system", "content": system_prompt},
                             {"role": "user", "content": user_prompt}],
            },
        )
        return (body["choices"][0]["message"]["content"] or "").strip()

    if provider == "gemini":
        body = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            payload={
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 400,
                    # 2.5-family models think by default; thinking tokens would eat
                    # the small output budget for a 3-sentence answer.
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
        )
        parts = (body.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        return " ".join(p.get("text", "") for p in parts).strip()

    if provider == "anthropic":
        body = _post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key,
                     "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
            payload={
                "model": model,
                "max_tokens": 400,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
        )
        if body.get("stop_reason") == "refusal":
            raise RuntimeError("model refused")
        return " ".join(b.get("text", "") for b in body.get("content", [])
                        if b.get("type") == "text").strip()

    raise RuntimeError(f"unknown provider {provider!r}")


def interpret(provider, api_key, facts, caller=call_llm):
    """Returns (text, validation_dict). Never raises.

    `caller` is injected so the provider seam can be stubbed in tests.
    """
    validation = {"model_used": False, "provider": provider, "rejected": False,
                  "reason": None, "unsupported_numbers": [],
                  "sign_inversions": [], "word_quantities": []}

    if provider is None:
        validation["reason"] = "no LLM provider configured — deterministic output"
        return deterministic_text(facts), validation

    if facts_are_thin(facts):
        validation["reason"] = "inputs too thin for interpretation"
        return deterministic_text(facts), validation

    try:
        text = caller(provider, api_key, SYSTEM_PROMPT, build_prompt(facts))
    except Exception as e:  # network, auth, rate limit, refusal — degrade, never crash
        validation["reason"] = f"api_error: {type(e).__name__}: {e}"
        return deterministic_text(facts), validation

    text = (text or "").strip()
    if not text:
        validation["reason"] = "empty model response"
        return deterministic_text(facts), validation

    ok, detail = validate_text(text, facts)
    if not ok:
        validation.update(rejected=True, reason=(
            "numeric/direction validation failed — model text rejected and "
            "replaced by the deterministic template"), **detail)
        return deterministic_text(facts), validation

    validation["model_used"] = True
    return text, validation


def publishable(text, facts):
    """LAST GATE before anything is written to the public file.

    interpret() already substitutes the template on rejection, but nothing
    downstream re-checked what was actually about to be published. A `validation`
    block recording a failure next to the failing prose is not a safeguard — the
    prose still ships, and nobody reads the block. So the text handed to
    write_json is re-validated here, independently, and swapped out if it does not
    pass. Returns (text, forced_reason_or_None).
    """
    ok, _ = validate_text(text, facts)
    if ok:
        return text, None
    fallback = deterministic_text(facts)
    ok2, detail2 = validate_text(fallback, facts)
    if ok2:
        return fallback, "output gate: text failed re-validation, template substituted"
    # The template is built from the same facts, so this should be unreachable.
    # If it ever happens, publish nothing quotable rather than something wrong.
    return (
        f"No verifiable summary could be produced for {facts.get('commodity')} in "
        f"this build. Nothing is asserted here.",
        f"output gate: template also failed re-validation ({detail2})",
    )


# ------------------------------------------------------------------------ main

def _existing_has_ai_text():
    """True if data/<OUTPUT> already holds at least one accepted AI interpretation."""
    # Read directly: this is our own previous OUTPUT, not an input, and its
    # absence is an expected first-run state — it must not land in INPUT_STATUS.
    try:
        payload = _payload(json.loads((DATA_DIR / OUTPUT).read_text()))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return any(isinstance(v, dict) and v.get("is_ai_generated_interpretation")
               for v in payload.values())


def main():
    provider, api_key = resolve_provider()
    if provider:
        print(f"[INFO] LLM provider: {provider} ({PROVIDERS[provider]['model']}); "
              f"free tier: {PROVIDERS[provider]['free_tier']}")
    else:
        print("[INFO] No LLM provider configured (set NEWS_LLM_PROVIDER and a key, "
              "or just GROQ_API_KEY) — writing deterministic, fully-sourced "
              "interpretations only. This path is zero-cost and complete.")
        # Last-good preservation (the v44 pattern, refresh_usda_psd.py): if a
        # previous run produced accepted AI prose, a transiently missing secret
        # must not silently downgrade the site to templates.
        if _existing_has_ai_text():
            print(f"[KEEP] preserved existing {OUTPUT} (contains accepted AI "
                  f"interpretations from a previous run); nothing rewritten.")
            return 0

    pink = _payload(_load("worldbank_pink_sheet.json"))
    ffpi = _payload(_load("fao_ffpi.json"))
    psd = _payload(_load("usda_psd.json"))
    news_env = _load("commodity_news.json")
    news = _payload(news_env) if news_env is not None else None

    # A missing input must degrade VISIBLY. The status of every input is printed
    # here and recorded in the output envelope, so "which sources were actually
    # behind this build" is answerable from the published file alone.
    degraded = sorted(k for k, v in INPUT_STATUS.items() if v != "ok")
    print("[INPUTS] " + ", ".join(f"{k}={v}" for k, v in sorted(INPUT_STATUS.items())))
    if degraded:
        print(f"[DEGRADED] {len(degraded)} of {len(INPUT_STATUS)} inputs unavailable: "
              f"{', '.join(degraded)}. Facts derived from them are null (unavailable), "
              f"never zero, and the output status reflects the degradation.")

    generated_at = datetime.now(timezone.utc).isoformat()
    out, model_used, rejected, forced = {}, 0, 0, 0

    for idx, (key, spec) in enumerate(COMMODITIES.items()):
        if provider and idx:
            # Stay under the per-minute token ceiling instead of bursting.
            time.sleep(CALL_SPACING_SECONDS)
        facts = build_facts(key, spec, pink, ffpi, psd, news)
        text, validation = interpret(provider, api_key, facts)

        # Output gate (see publishable()): whatever is about to be written is
        # re-validated here. Rejected content never reaches the file.
        text, forced_reason = publishable(text, facts)
        if forced_reason:
            forced += 1
            validation.update(rejected=True, model_used=False)
            validation["reason"] = (
                f"{validation.get('reason') or 'no reason recorded'}; {forced_reason}")
            print(f"    [GATE] {spec['label']}: {forced_reason}")

        model_used += int(validation["model_used"])
        rejected += int(validation["rejected"])
        out[key] = {
            "commodity": spec["label"],
            # Every figure below is computed in Python from committed source files.
            "facts": facts,
            "interpretation": text,
            # Explicit: the prose is AI-generated commentary, NOT measured data.
            "is_ai_generated_interpretation": validation["model_used"],
            "content_type": ("ai_interpretation" if validation["model_used"]
                             else "deterministic_template"),
            "not_a_forecast": True,
            "provider": provider if validation["model_used"] else None,
            "model": (PROVIDERS[provider]["model"]
                      if validation["model_used"] and provider else None),
            "generated_at": generated_at,
            "validation": validation,
            # Which inputs were actually readable for this build.
            "input_status": dict(INPUT_STATUS),
        }
        print(f"  [{'AI' if validation['model_used'] else 'TEMPLATE'}] {spec['label']}"
              + (f" — {validation['reason']}" if validation.get("reason") else ""))

    write_json(
        OUTPUT,
        out,
        source=(f"{provider}/{PROVIDERS[provider]['model']}" if provider
                else "deterministic templates (no LLM provider configured)")
               + " (build-time, GitHub Actions) over committed "
                 "worldbank_pink_sheet.json / fao_ffpi.json / usda_psd.json / "
                 "commodity_news.json",
        notes=(f"Input status: {INPUT_STATUS}. "
               "Prose marked is_ai_generated_interpretation=true is AI-generated "
               "interpretation, not measured data. Every figure is computed in Python and "
               "passed to the model as a locked block; returned text is regex-validated so "
               "no number can originate from the model — including numbers appearing in "
               "third-party headlines. Text failing validation is replaced by a "
               "deterministic template. Forward-looking language is hedged pathway framing, "
               "not forecast."),
        status=("degraded_inputs" if degraded else
                ("ok" if provider else "deterministic_only")),
    )
    print(f"[OK] {model_used}/{len(COMMODITIES)} AI interpretations accepted, "
          f"{rejected} rejected by validation ({forced} caught at the output gate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
