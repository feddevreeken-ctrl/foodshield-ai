"""Join headlines to trade corridors (v46).

WHAT THIS IS FOR:
  A headline feed is the one thing this project has no advantage at — Google
  does it better and for free. What FoodShield has that a feed reader does not
  is data/commodity_flows.json: a bilateral graph of who ships what to whom,
  in kilotonnes. This module turns "Russia caps wheat exports" into "that
  corridor carries 8,526 kt to Egypt, 4,500 kt to Turkey, 2,600 kt to
  Bangladesh" — the propagation logic the scenario engine already runs,
  pointed at the news.

HONESTY CONTRACT (read before changing anything here):
  Country attribution is a KEYWORD MATCH on a headline. It is modeled, not
  measured, and it is wrong sometimes. Everything this module emits carries
  `attribution: "modeled"` and the UI must badge it as such. The corridor
  VOLUMES are a different matter — those are sourced from the flow atlas and
  are as good as that file. Do not let the two blur: a modeled country match
  pointing at a sourced tonnage is still a modeled claim about relevance.

  We deliberately do NOT claim the headline causes the exposure. The wording
  throughout is "touches this corridor", never "will raise prices in".

DIRECTION MATTERS:
  If a headline names an EXPORTER, the exposed parties are its importers
  (downstream). If it names an IMPORTER, that country is itself exposed
  (direct). Both are recorded and kept distinct — collapsing them produces
  the nonsense of "Egypt exposed" for a story about Egypt buying.
"""
import json
import re
from pathlib import Path

from _news_gazetteer import (ALIASES, AMBIGUOUS, COUNTRY_NAMES, DOTTED_FORMS,
                             NON_SOVEREIGN, REGIONS)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# The news taxonomy's commodity keys vs the flow atlas's keys. The atlas uses
# `palmoil`; the news pipeline uses `palm_oil`. Anything unmapped simply has
# no corridor data and degrades to "no corridor" rather than erroring.
# News taxonomy key -> flow-atlas key. Identity for almost everything, since
# the widened taxonomy in _news_taxonomy.py was deliberately keyed to match
# commodity_flows.json. Only the ones the atlas spells differently are listed
# explicitly; _flow_key() falls back to identity so adding a commodity to the
# taxonomy needs no change here.
COMMODITY_TO_FLOW_KEY = {
    "palm_oil": "palmoil",
}


def _flow_key(commodity, available):
    """Resolve a taxonomy key to an atlas key, or None if the atlas lacks it."""
    mapped = COMMODITY_TO_FLOW_KEY.get(commodity, commodity)
    return mapped if mapped in available else None

# Below this a corridor is noise and clutters the row for no gain.
MIN_CORRIDOR_KT = 50

# Cap per item so one headline cannot render a wall of 40 countries.
MAX_EXPOSED_PER_ITEM = 6

_flows_cache = None


def load_flows():
    """Load the bilateral flow atlas once. Returns {} if unavailable."""
    global _flows_cache
    if _flows_cache is not None:
        return _flows_cache
    path = DATA_DIR / "commodity_flows.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        _flows_cache = payload.get("commodities") or {}
    except Exception as e:
        print(f"  [warn] commodity_flows.json unavailable ({e}); "
              f"corridor attribution disabled")
        _flows_cache = {}
    return _flows_cache


def _build_surface_forms():
    """surface form -> ISO3, longest first so 'south korea' beats 'korea'."""
    forms = {}
    for iso, name in COUNTRY_NAMES.items():
        forms[name.lower()] = iso
    for alias, iso in ALIASES.items():
        forms.setdefault(alias.lower(), iso)
    return sorted(forms.items(), key=lambda kv: -len(kv[0]))


_SURFACE_FORMS = _build_surface_forms()


def detect_countries(title):
    """Return ISO3 codes mentioned in a headline.

    Conservative by design: ambiguous surface forms ("Turkey", "China",
    "Georgia") require a corroborating trade or geography term in the same
    headline, because a wrong attribution here is a visibly silly claim on a
    page whose entire selling point is that its claims hold up.
    """
    if not title:
        return []
    low = f" {title.lower()} "
    hits = []

    # Dotted abbreviations first. These need a leading boundary only — a
    # trailing \b after "u.s." can never match, which is why they are kept
    # out of the \b-anchored table below.
    for form, iso in DOTTED_FORMS.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(form)}", low):
            hits.append(iso)

    for region, members in REGIONS.items():
        if re.search(rf"\b{re.escape(region)}\b", low):
            hits.extend(members)

    for form, iso in _SURFACE_FORMS:
        if not re.search(rf"\b{re.escape(form)}\b", low):
            continue
        guards = AMBIGUOUS.get(form)
        # Word-boundary, not substring. Codex caught this: `"ban" in low` is
        # true for "bank", and `"mill"` for "million", so the guards were
        # firing on exactly the finance copy they were meant to exclude.
        # Plural-tolerant, still word-anchored. A bare \b...\b missed
        # "exports"/"tariffs" (the commonest headline forms), which silently
        # dropped real stories; dropping the trailing \b entirely would let
        # "ban" match "bank" again. Allow an optional plural suffix only.
        if guards and not any(
                re.search(rf"\b{re.escape(g)}(?:s|es)?\b", low) for g in guards):
            continue          # looks like the bird / the state / the given name
        hits.append(iso)

    seen, out = set(), []
    for iso in hits:
        if iso in NON_SOVEREIGN or iso in seen:
            continue
        seen.add(iso)
        out.append(iso)
    return out


def corridors_for(commodities, countries):
    """Map (commodities, mentioned countries) onto exposed trade partners.

    Returns a list of dicts, largest corridor first:
      {iso, kt, commodity, role, via}
        role='downstream'  this country IMPORTS from a country in the headline
        role='direct'      this country was itself named and is an importer
    """
    flows_by_commodity = load_flows()
    if not flows_by_commodity or not countries:
        return []

    tally = {}
    for commodity in commodities:
        flow_key = _flow_key(commodity, flows_by_commodity)
        block = flows_by_commodity.get(flow_key) if flow_key else None
        if not block:
            continue
        for flow in block.get("flows") or []:
            src, dst = flow.get("from"), flow.get("to")
            kt = flow.get("value")
            if not src or not dst or not isinstance(kt, (int, float)):
                continue
            if kt < MIN_CORRIDOR_KT or dst in NON_SOVEREIGN:
                continue

            # Check the IMPORTER first. When a headline names both ends of a
            # corridor ("Egypt buys Russian wheat"), the older exporter-first
            # order recorded Egypt as merely "downstream" and the card read
            # "Reaches EGY" for a story that is explicitly about Egypt. Being
            # named is the stronger claim, so it wins.
            if dst in countries:
                role, iso, via = "direct", dst, src
            elif src in countries:
                role, iso, via = "downstream", dst, src
            else:
                continue

            prev = tally.get(iso)
            # Keep the largest single corridor per country. Summing across
            # commodities would add wheat kt to palm-oil kt and present the
            # result with unearned authority.
            if prev is None or kt > prev["kt"]:
                tally[iso] = {"iso": iso, "kt": round(float(kt)),
                              "commodity": commodity, "role": role, "via": via}

    ranked = sorted(tally.values(), key=lambda r: -r["kt"])
    return ranked[:MAX_EXPOSED_PER_ITEM]


def annotate(items):
    """Attach country + corridor attribution to news items, in place.

    Adds per item:
      countries_mentioned  [ISO3]         modeled, keyword match
      exposed              [{iso,kt,...}] sourced volumes on modeled relevance
      exposure_kt          int            largest single corridor touched
      attribution          "modeled"
    """
    flows_available = bool(load_flows())
    annotated = 0
    for item in items:
        mentioned = detect_countries(item.get("title") or "")
        exposed = corridors_for(item.get("matched") or [], mentioned) if flows_available else []
        item["countries_mentioned"] = mentioned
        item["exposed"] = exposed
        item["exposure_kt"] = exposed[0]["kt"] if exposed else 0
        item["attribution"] = "modeled"
        if exposed:
            annotated += 1
    return annotated


def exposure_leaderboard(items, limit=12):
    """Aggregate: which countries the current news window most touches.

    Counts each country once per item so a country repeated across ten
    syndications of one wire story does not dominate the rail. Volume is the
    max corridor touched, not a sum, for the reason given in corridors_for.
    """
    agg = {}
    for item in items:
        for row in item.get("exposed") or []:
            iso = row["iso"]
            cur = agg.setdefault(iso, {"iso": iso, "kt": 0, "items": 0,
                                       "commodities": set()})
            cur["items"] += 1
            cur["kt"] = max(cur["kt"], row["kt"])
            cur["commodities"].add(row["commodity"])
    out = []
    for row in agg.values():
        row["commodities"] = sorted(row["commodities"])
        row["name"] = COUNTRY_NAMES.get(row["iso"], row["iso"])
        out.append(row)
    out.sort(key=lambda r: (-r["items"], -r["kt"]))
    return out[:limit]
