"""
Commodity-news taxonomy — pure data, no I/O.

Used by refresh_commodity_news.py to decide (a) what to ask upstream for,
(b) whether a returned headline is actually about the commodity, and
(c) how to rank and de-duplicate what survives.

DESIGN RULES
  - No network, no filesystem, no clock. Only `re`. Everything here is a
    constant or a pure function of its arguments, so it can be reasoned
    about and unit-tested without a fixture.
  - Matching is done on the HEADLINE ONLY. We never store or scan article
    bodies (see the legal note in refresh_commodity_news.py), so every rule
    below has to earn its keep against ~10 words of text.
  - The negative lists are the important part. A naive substring match on
    "corn" or "rice" pulls in more junk than signal; each entry below is a
    real false positive observed in GDELT/EC output, not a hypothetical.

WHY BOTH SPELLINGS: the European Commission, FAO, IFA and most non-US trade
press write "fertiliser". US wires write "fertilizer". Matching only one
silently drops roughly half the best-sourced items in this category, and the
loss is invisible because the feed still returns *something*.
"""
import re

# ─────────────────────────────────────────────────────────────────────────────
# Commodities
# ─────────────────────────────────────────────────────────────────────────────
# Exactly these six. Adding a seventh means adding it to every dict below.
COMMODITIES = ("wheat", "maize", "rice", "soybeans", "palm_oil", "fertilizer")

# Surface forms per commodity. Order matters only for readability.
# These drive BOTH the upstream query and the local relevance match, so a term
# added here widens recall on both sides at once.
SYNONYMS = {
    "wheat": [
        "wheat", "durum", "milling wheat", "feed wheat", "winter wheat",
        "spring wheat",
    ],
    "maize": [
        "maize", "corn", "yellow corn", "white maize", "feed corn", "corn crop",
    ],
    "rice": [
        "rice", "paddy", "basmati", "jasmine rice", "milled rice", "broken rice",
        "parboiled rice",
    ],
    "soybeans": [
        "soybeans", "soybean", "soya", "soyabean", "soymeal", "soybean meal",
        "soybean oil", "soyoil",
    ],
    "palm_oil": [
        "palm oil", "palm-oil", "crude palm oil", "cpo", "palm kernel oil",
        "palm olein",
    ],
    # Both spellings, always. See module docstring.
    "fertilizer": [
        "fertilizer", "fertiliser", "fertilizers", "fertilisers",
        "urea", "potash", "ammonia", "ammonium nitrate", "phosphate",
        "dap fertilizer", "map fertilizer", "npk",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Upstream queries
# ─────────────────────────────────────────────────────────────────────────────
# GDELT DOC 2.0 query strings. Kept deliberately narrow: GDELT's relevance is
# poor on bare nouns, so each query pairs the commodity with a market/supply
# context word. Multi-word phrases MUST be quoted or GDELT ORs the tokens.
#
# Syntax notes (GDELT DOC 2.0):
#   - implicit AND between terms; OR must be explicit and parenthesised
#   - sourcelang:english restricts to English-language articles
#   - the whole thing goes in the `query` param, URL-encoded by requests
COMMODITY_QUERIES = {
    "wheat": (
        '(wheat OR "durum wheat") '
        '(export OR exports OR harvest OR crop OR price OR prices OR shipment '
        'OR tariff OR quota OR drought) sourcelang:english'
    ),
    "maize": (
        '(maize OR corn) '
        '(export OR exports OR harvest OR crop OR price OR prices OR shipment '
        'OR tariff OR quota OR drought) sourcelang:english'
    ),
    "rice": (
        '(rice OR paddy) '
        '(export OR exports OR harvest OR crop OR price OR prices OR shipment '
        'OR tariff OR quota OR "export ban") sourcelang:english'
    ),
    "soybeans": (
        '(soybean OR soybeans OR soya OR "soybean meal") '
        '(export OR exports OR harvest OR crop OR price OR prices OR shipment '
        'OR tariff OR quota OR crush) sourcelang:english'
    ),
    "palm_oil": (
        '("palm oil" OR "crude palm oil" OR "palm kernel oil") '
        '(export OR exports OR levy OR price OR prices OR shipment OR tariff '
        'OR quota OR production) sourcelang:english'
    ),
    "fertilizer": (
        '(fertilizer OR fertiliser OR urea OR potash OR "ammonium nitrate" '
        'OR phosphate) '
        '(export OR exports OR price OR prices OR supply OR shortage OR tariff '
        'OR sanctions OR plant) sourcelang:english'
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Negative keywords — the false-positive killers
# ─────────────────────────────────────────────────────────────────────────────
# Each entry is a phrase that, if present in the headline, disqualifies the item
# for that commodity even though a synonym matched. Matched case-insensitively
# on word boundaries where the term is a single word.
#
# These are the traps that actually fire:
#   "corn"   → popcorn, corn syrup, Corn Exchange (a UK venue/building name),
#              candy corn, corn removal (podiatry), Cornwall/Cornell truncation
#   "rice"   → Rice University, Condoleezza Rice, Rice Krispies, Rice County,
#              Ricegrass, "Brian Rice" and every other surname Rice
#   "palm"   → palm trees, Palm Beach, Palm Springs, palm reading, "palm oil
#              free" cosmetics marketing, PalmPay/Palm Pilot
#   "wheat"  → wheatgrass, Wheaton (IL / college), Wheatley, wheat beer,
#              Wheatbelt place names, buckwheat (a different crop entirely)
NEGATIVE = {
    "wheat": [
        "wheatgrass", "wheat grass", "wheaton", "wheatley", "wheatstone",
        "wheat ridge", "buckwheat", "wheat beer", "wheat penny", "shredded wheat",
        "wheat thins", "cream of wheat", "wheat field art", "whole wheat recipe",
    ],
    "maize": [
        "popcorn", "corn syrup", "candy corn", "corn exchange", "corn dog",
        "corn maze", "cornwall", "cornell", "cornish", "corned beef",
        "corn removal", "corns and calluses", "corn hole", "cornhole",
        "corn rows", "cornrows", "unicorn", "capricorn", "corn on the cob recipe",
        "sweetcorn salad",
    ],
    "rice": [
        "rice university", "condoleezza rice", "rice krispies", "rice county",
        "rice lake", "ricegrass", "rice owls", "rice bowl game", "jerry rice",
        "brian rice", "declan rice", "anne rice", "rice a roni",
        "rice water hair", "rice paper craft", "rice pudding recipe",
        "cauliflower rice",
    ],
    "soybeans": [
        "soy candle", "soy wax", "soy latte", "soy sauce recipe", "soy boy",
        "soylent",
    ],
    "palm_oil": [
        "palm tree", "palm trees", "palm beach", "palm springs", "palm desert",
        "palm sunday", "palm reading", "palmistry", "palm pilot", "palmpay",
        "west palm", "palm of the hand", "palm oil free", "palm-oil-free",
        "without palm oil", "no palm oil", "palm angels", "palm print",
        "palm scanner", "palm recognition",
    ],
    "fertilizer": [
        "lawn fertilizer tips", "lawn fertiliser tips", "garden fertilizer diy",
        "houseplant fertilizer", "aquarium fertilizer", "fertilizer for tomatoes",
        "fertility clinic", "fertility rate", "in vitro",
        "fertilizer bomb suspect",
    ],
}

# Applies to every commodity. Overwhelmingly the aggregator/spam layer plus the
# "commodity as metaphor" headline ("a fertilizer for growth", "the wheat from
# the chaff") that no per-commodity list would catch.
NEGATIVE_GLOBAL = [
    "separate the wheat", "wheat from the chaff", "sowing the seeds of",
    "horoscope", "obituary", "wordle", "crossword", "sudoku",
    "recipe of the day", "best restaurants", "top 10 recipes",
    "giveaway", "coupon code", "black friday deal", "amazon deal",
]

# ─────────────────────────────────────────────────────────────────────────────
# Positive signal terms — what makes an item worth surfacing at all
# ─────────────────────────────────────────────────────────────────────────────
# An item that matches a commodity but carries none of these is almost always a
# market-colour piece or a listicle. Weights are INTERNAL scoring inputs only;
# they never reach the JSON.
POSITIVE = {
    # Baseline trade/supply vocabulary. Low weights: on their own these only
    # clear MIN_RELEVANCE for an item the upstream query also vouched for.
    # They are not optional — a live run on 2026-07-18 rejected 179 of 180
    # articles because the list below started at "export ban" and had no entry
    # for the far more common bare "exports", so ordinary market reporting
    # ("Ukraine corn exports rise 12%") scored the same as a listicle.
    "export": 2.0,
    "exports": 2.0,
    "import": 1.5,
    "imports": 1.5,
    "shipments": 2.0,
    "supply": 2.0,
    "supplies": 2.0,
    "price": 1.5,
    "prices": 1.5,
    "production": 1.5,
    "output": 1.5,
    "crop": 1.5,
    "acreage": 2.0,
    "demand": 1.2,
    "mill": 1.2,
    "crush": 2.0,
    # Supply-shock / policy — the reason this feed exists
    "export ban": 5.0,
    "export restriction": 5.0,
    "export curb": 4.5,
    "export levy": 3.5,
    "export duty": 3.5,
    "import ban": 4.0,
    "sanctions": 3.5,
    "embargo": 4.0,
    "quota": 3.0,
    "tariff": 3.0,
    "price cap": 3.0,
    # Physical supply
    "shortage": 4.0,
    "harvest": 3.0,
    "yield": 2.5,
    "drought": 4.0,
    "flood": 3.0,
    "frost": 2.5,
    "heatwave": 2.5,
    "blight": 3.5,
    "rust": 2.0,
    "locust": 3.5,
    "crop failure": 4.5,
    "planting": 2.0,
    # NOT bare "stocks". Observed 2026-07-18: bare "stocks" scored equity spam
    # ("Fertilizer Stocks To Watch Now", "Short Interest in Brazil Potash")
    # straight into the top of the list, because in headline English "stocks"
    # means equities far more often than it means grain inventories. The
    # qualified forms below only appear in the supply sense.
    "grain stocks": 3.0,
    "ending stocks": 3.0,
    "stocks-to-use": 3.0,
    "stocks to use": 3.0,
    "carryover": 2.5,
    "reserves": 2.5,
    "inventories": 2.0,
    # Logistics
    "port": 2.5,
    "freight": 2.5,
    "shipment": 2.0,
    "vessel": 2.0,
    "strait": 2.5,
    "canal": 2.0,
    "rail strike": 2.5,
    "backlog": 2.0,
    # Market state
    "shortfall": 3.0,
    "surplus": 2.0,
    "record crop": 2.5,
    "food security": 3.0,
    "famine": 4.0,
}

# ─────────────────────────────────────────────────────────────────────────────
# Demote terms — matched, on-topic, but not what this feed is for
# ─────────────────────────────────────────────────────────────────────────────
# Not disqualifying (a "price target" piece on a fertilizer producer can still
# be a real supply signal), just pushed down. Values are penalties.
DEMOTE = {
    "recipe": 6.0,
    "recipes": 6.0,
    "how to cook": 6.0,
    "meal prep": 5.0,
    "taste test": 5.0,
    "restaurant review": 5.0,
    "price target": 4.0,
    "share price": 3.5,
    "stock price": 3.5,
    "analyst note": 3.5,
    "analyst rating": 3.5,
    "upgraded to buy": 4.0,
    "downgraded to sell": 4.0,
    "buy rating": 3.5,
    "hold rating": 3.5,
    "earnings call": 3.0,
    "quarterly results": 3.0,
    "dividend": 3.0,
    "shares rise": 3.0,
    "shares fall": 3.0,
    "penny stock": 5.0,
    "etf": 2.5,
    "should you buy": 5.0,
    "top stocks": 5.0,
    # Equity-desk boilerplate. Added 2026-07-18 after a live run filled the
    # fertilizer slot with "Fertilizer Stocks To Watch Now", "Short Interest in
    # Brazil Potash Corp Declines By 40.0%" and "Chatham Rock Phosphate Trading
    # Down 30%" — all real fertilizer companies, none of them supply news.
    "stocks to watch": 6.0,
    "short interest": 6.0,
    "trading down": 5.0,
    "trading up": 5.0,
    "market cap": 4.0,
    "shares of": 4.0,
    "shares sold": 5.0,
    "shares purchased": 5.0,
    "position in": 4.0,
    "stake in": 3.5,
    "institutional investor": 5.0,
    "nyse": 3.0,
    "nasdaq": 3.0,
    "insider selling": 5.0,
    "insider buying": 5.0,
    "52-week": 4.0,
    "moving average": 5.0,
    # Evergreen how-to and trade-idea formats. Both surfaced in the 2026-07-18
    # run ("Complete Guide to Wheat Farming: From Sowing to Harvest",
    # "Soybean Prices Are Set to Challenge Their May High. 1 Trade to Make Here.")
    # — on-topic, correctly matched, and useless as a supply signal.
    "complete guide": 5.0,
    "step by step guide": 5.0,
    "beginner's guide": 5.0,
    "everything you need to know": 5.0,
    "trade to make": 5.0,
    "trade idea": 4.5,
    "how to start": 4.5,
}

# ─────────────────────────────────────────────────────────────────────────────
# Publisher trust
# ─────────────────────────────────────────────────────────────────────────────
# Domain (or domain suffix) → trust weight. Wires and official publishers rank
# above aggregators and syndication mirrors, which is what keeps the top of the
# list from filling with 40 copies of the same Reuters story via content farms.
#
# This is an editorial ranking input, NOT a truth claim about any item: every
# stored item is flagged as a third-party claim regardless of who published it.
PUBLISHER_TRUST = {
    # Official / intergovernmental — highest
    "ec.europa.eu": 1.00,
    "agriculture.ec.europa.eu": 1.00,
    "fao.org": 1.00,
    "wfp.org": 1.00,
    "usda.gov": 1.00,
    "fas.usda.gov": 1.00,
    "ers.usda.gov": 1.00,
    "worldbank.org": 0.98,
    "imf.org": 0.95,
    "unctad.org": 0.95,
    "ipcinfo.org": 0.98,
    "fews.net": 0.98,
    "reliefweb.int": 0.92,
    "amis-outlook.org": 0.95,
    "igc.int": 0.95,
    # Wire services
    "reuters.com": 0.92,
    "apnews.com": 0.90,
    "bloomberg.com": 0.88,
    "afp.com": 0.88,
    "dowjones.com": 0.85,
    # Trade press with real desks
    "agricensus.com": 0.82,
    "world-grain.com": 0.80,
    "agriculture.com": 0.78,
    "farmdocdaily.illinois.edu": 0.85,
    "successfulfarming.com": 0.72,
    "fastmarkets.com": 0.80,
    "argusmedia.com": 0.80,
    "spglobal.com": 0.82,
    "hellenicshippingnews.com": 0.70,
    # General press
    "ft.com": 0.85,
    "wsj.com": 0.85,
    "economist.com": 0.82,
    "bbc.com": 0.82,
    "bbc.co.uk": 0.82,
    "theguardian.com": 0.78,
    "aljazeera.com": 0.75,
    "cnbc.com": 0.70,
    "scmp.com": 0.72,
    "thehindu.com": 0.72,
    "business-standard.com": 0.70,
    "dawn.com": 0.68,
    "jakartapost.com": 0.68,
    "thestar.com.my": 0.68,
    # Aggregators / syndicators / low-signal — explicitly demoted
    "msn.com": 0.30,
    "news.yahoo.com": 0.32,
    "yahoo.com": 0.32,
    "finance.yahoo.com": 0.35,
    "marketscreener.com": 0.30,
    "investing.com": 0.35,
    "benzinga.com": 0.25,
    "zacks.com": 0.25,
    "simplywall.st": 0.20,
    "seekingalpha.com": 0.35,
    "prnewswire.com": 0.30,
    "globenewswire.com": 0.30,
    "businesswire.com": 0.30,
    "einnews.com": 0.20,
    "openpr.com": 0.15,
    "digitaljournal.com": 0.15,
    # Automated equity-headline farms. Observed in a live GDELT pull 2026-07-18
    # producing multiple "Short Interest in <fertilizer company>" items per hour.
    "themarketsdaily.com": 0.10,
    "dailypolitical.com": 0.10,
    "marketbeat.com": 0.15,
    "etfdailynews.com": 0.10,
    "tickerreport.com": 0.10,
    "modernreaders.com": 0.10,
    "americanbankingnews.com": 0.10,
}

# Anything not in the map. Deliberately mid-low: an unknown domain should be
# beatable by a known wire but should not be buried outright — long-tail
# national press is where genuinely local supply news shows up first.
PUBLISHER_TRUST_DEFAULT = 0.50

# ─────────────────────────────────────────────────────────────────────────────
# Dedup + recency tuning
# ─────────────────────────────────────────────────────────────────────────────
# Max Hamming distance between two 64-bit title simhashes for the pair to be
# treated as the same story. 3 is the standard operating point for 64-bit
# simhash over short text: it absorbs a swapped adjective or a trailing
# " - Reuters" suffix without collapsing genuinely different headlines.
DEDUP_HAMMING = 3

# Simhash width in bits. Changing this invalidates every stored dedup_key.
SIMHASH_BITS = 64

# Recency decay for ranking: an item's score halves every N hours. 36h keeps a
# yesterday-afternoon export-ban story above a fresh listicle, while a
# three-day-old item falls out of the top of the list on its own.
HALF_LIFE_HOURS = 36.0

# Hard cutoff — nothing older than this is ingested at all.
MAX_AGE_HOURS = 24.0 * 14

# Cap on stored items, total and per commodity. Keeps the committed JSON small
# (it lands in a public git repo on every cron) and keeps the tail honest.
MAX_ITEMS_TOTAL = 120
MAX_ITEMS_PER_COMMODITY = 25

# Minimum internal relevance score to store at all.
#
# Scoring floor is 1.0 (matched a commodity) + 1.0 (upstream query agreed).
# A threshold of 2.0 therefore admitted items with NO supply or policy signal
# at all — a live run on 2026-07-18 let through a story about vineyard
# composting purely because the word "fertilizer" appeared. 3.5 forces at least
# one real POSITIVE term to be present, and keeps unhinted RSS items honest too.
MIN_RELEVANCE = 3.5

# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────
_WORD_RE = re.compile(r"[a-z0-9']+")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")

# Wire/aggregator suffixes appended to syndicated headlines. Stripped before
# hashing so the same story from five mirrors collapses to one dedup_key.
_SUFFIX_RE = re.compile(
    r"\s*[-–—|]\s*(reuters|ap|associated press|bloomberg|afp|msn|yahoo(?: finance)?|"
    r"cnbc|the guardian|bbc(?: news)?|financial times|ft|wsj|nasdaq|investing\.com)\s*$"
)

# Tokens carrying no discriminating power in a headline.
_STOPWORDS = frozenset("""
a an the and or but if of in on at to for from by with as is are was were be been
being it its this that these those over under after before amid says say said
new news more most than then out up down into about
""".split())


def normalize_title(title):
    """Lowercase, strip wire suffix and punctuation, collapse whitespace.

    Pure. Used for both dedup hashing and keyword matching so the two never
    disagree about what the text actually says.
    """
    t = (title or "").lower().strip()
    # Strip repeatedly: "Story - Reuters | MSN" carries two suffixes.
    for _ in range(3):
        new = _SUFFIX_RE.sub("", t)
        if new == t:
            break
        t = new
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


# Generic trailing " - <Publisher>" / " | <Publisher>" segment. The named-wire
# list above only catches the big five; syndication mirrors append their own
# masthead, which is unbounded.
_TAIL_SEGMENT_RE = re.compile(r"^(?P<head>.{25,}?)\s+(?:[-–—|:]\s+)(?P<tail>[^-–—|]{1,45})$")


def dedup_text(title):
    """Normalized headline with a trailing masthead segment removed.

    Used ONLY for computing dedup_key — never for keyword matching, where
    dropping a tail could cost a real match.

    Why this exists: a live run on 2026-07-18 stored the same wire story twice,
    as "North Bay vintners try to dodge fertilizer woes ... - Red Bluff Daily
    News" and "... - The Mercury News". Identical text, two mastheads, two
    simhashes 20+ bits apart. The named-suffix list in normalize_title() cannot
    enumerate every mirror, so the dedup path strips any short trailing segment
    after a dash or pipe, provided the head is still substantial (>=25 chars) —
    which keeps "India bans rice exports - what happens next" from collapsing
    into "India bans rice exports".
    """
    t = normalize_title(title)
    # normalize_title() already removed punctuation, so re-run against the raw
    # title to find the separator, then re-normalize the head.
    raw = (title or "").strip()
    m = _TAIL_SEGMENT_RE.match(raw)
    if m:
        head = normalize_title(m.group("head"))
        # Only accept the trim if it leaves a usable amount of text.
        if len(tokenize(head)) >= 4:
            return head
    return t


def tokenize(text):
    """Content tokens of a normalized string, stopwords removed. Pure."""
    return [w for w in _WORD_RE.findall(text or "") if w not in _STOPWORDS and len(w) > 1]


def contains_term(haystack, term):
    """Whole-word (or whole-phrase) containment on an already-normalized string.

    Substring matching is what produces 'corn' inside 'Cornwall'. Everything in
    this module goes through here instead.
    """
    if not haystack or not term:
        return False
    t = _WS_RE.sub(" ", _PUNCT_RE.sub(" ", term.lower())).strip()
    if not t:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", haystack) is not None


def domain_trust(domain):
    """Trust weight for a bare domain, falling back through parent domains."""
    d = (domain or "").lower().strip().lstrip(".")
    if d.startswith("www."):
        d = d[4:]
    if d in PUBLISHER_TRUST:
        return PUBLISHER_TRUST[d]
    parts = d.split(".")
    for i in range(1, len(parts) - 1):
        parent = ".".join(parts[i:])
        if parent in PUBLISHER_TRUST:
            return PUBLISHER_TRUST[parent]
    return PUBLISHER_TRUST_DEFAULT

# ─────────────────────────────────────────────────────────────────────────────
# v46.1 — NEWS-ONLY COMMODITY EXTENSION
#
# COMMODITIES above stays at the six staples: build_news_interpretation.py
# writes one interpretation block per entry and the Commodities tab expects
# exactly those six, so widening that tuple would ripple into surfaces this
# change has no business touching.
#
# The NEWS page is a different consumer with a different need. The bilateral
# flow atlas carries 46 commodities, and the agriculture trade press writes
# overwhelmingly about the other 40 — beef, poultry, sugar, coffee, seafood.
# Filtering 1,978 headlines down to 11 was not selectivity, it was a taxonomy
# that could not see most of its own subject matter.
#
# Keys here MUST match commodity_flows.json so _news_corridors can join them.
# Anything without a corridor still gets classified, it just renders without
# an exposure line.
# ─────────────────────────────────────────────────────────────────────────────
EXTRA_SYNONYMS = {
    "beef":       ["beef", "cattle", "live cattle", "feeder cattle", "veal"],
    "poultry":    ["poultry", "chicken", "broiler", "turkey meat", "egg", "eggs",
                   "layer hen", "avian influenza", "bird flu"],
    "pork":       ["pork", "hog", "hogs", "swine", "pig meat", "african swine fever"],
    "dairy":      ["dairy", "milk", "butter", "cheese", "milk powder", "whey",
                   "skim milk powder", "whole milk powder"],
    "sugar":      ["sugar", "sugarcane", "sugar cane", "sugar beet", "raw sugar"],
    "coffee":     ["coffee", "arabica", "robusta", "coffee beans"],
    "cocoa":      ["cocoa", "cacao", "cocoa beans", "chocolate"],
    "barley":     ["barley", "malting barley", "feed barley"],
    "sorghum":    ["sorghum", "milo"],
    "oilseeds":   ["oilseed", "oilseeds", "rapeseed", "canola", "sunflower seed",
                   "sunflower oil", "sunseed"],
    "vegoils":    ["vegetable oil", "vegetable oils", "vegoil", "soybean oil",
                   "sunflower oil", "rapeseed oil"],
    "pulses":     ["pulses", "lentil", "lentils", "chickpea", "chickpeas",
                   "dry beans", "peas"],
    "fish":       ["fish", "fishmeal", "seafood", "whitefish", "pollock"],
    "shrimp":     ["shrimp", "prawn", "prawns"],
    "salmon":     ["salmon"],
    "tuna":       ["tuna"],
    "bananas":    ["banana", "bananas"],
    "citrus":     ["citrus", "orange juice", "oranges", "lemons"],
    "apples":     ["apple", "apples"],
    "grapes":     ["grape", "grapes", "table grapes"],
    "potatoes":   ["potato", "potatoes"],
    "vegetables": ["vegetable", "vegetables", "tomato", "tomatoes", "onion",
                   "onions"],
    "nuts":       ["almond", "almonds", "cashew", "cashews", "walnut", "pistachio"],
    "tea":        ["tea"],
    "groundnuts": ["groundnut", "groundnuts", "peanut", "peanuts"],
    "feedcake":   ["soybean meal", "soymeal", "rapeseed meal", "feed cake",
                   "oilseed meal"],
    "liveanimals": ["live export", "live sheep", "live cattle export"],
    "sheepgoat":  ["lamb", "mutton", "sheepmeat", "sheep meat", "goat meat"],
}

# Same false-positive discipline as NEGATIVE. Kept small and specific.
EXTRA_NEGATIVE = {
    # NOTE: no "turkey" guard here. contains_term strips punctuation, so
    # "turkey's" normalised to "turkey s" and matched any headline naming the
    # COUNTRY — but "turkey" is not a poultry synonym in the first place
    # (only "turkey meat" is), so this negative could only ever fire on a
    # headline that had ALREADY matched a real poultry term. It was rejecting
    # genuine Turkish poultry-export and avian-flu stories, from a major
    # poultry exporter. Removed rather than patched.
    "poultry":  [],
    "sugar":    ["sugar daddy", "blood sugar", "sugar rush", "added sugar",
                 "sugar tax", "sugary drink"],
    "cocoa":    ["cocoa butter skincare", "hot chocolate recipe"],
    "dairy":    ["dairy free", "dairy-free", "milk tea"],
    "fish":     ["fishing rod", "phishing", "fish tank"],
    "apples":   ["apple inc", "apple iphone", "apple watch", "big apple"],
    "tea":      ["tea party", "spill the tea"],
    "grapes":   ["sour grapes"],
    "beef":     ["beef with", "beefing"],
}

# Union used by the news classifier only.
NEWS_SYNONYMS = {**SYNONYMS, **EXTRA_SYNONYMS}
NEWS_NEGATIVE = {**NEGATIVE, **EXTRA_NEGATIVE}
NEWS_COMMODITIES = tuple(NEWS_SYNONYMS.keys())


# ─────────────────────────────────────────────────────────────────────────────
# v46.2 — COMPANIES AND CHOKEPOINTS
#
# General business and national wires write about the food system constantly,
# but rarely with a commodity noun in the headline. "Cargill warns on margins"
# and "Red Sea diversions add two weeks" are both squarely on-topic and both
# were being dropped, because the classifier only knew commodity words.
#
# These give an item a second way to qualify. A company or chokepoint match is
# NOT a commodity match — it does not invent a `matched` entry — it clears the
# relevance floor so the item survives to be judged on its merits.
#
# Company list mirrors data/companies.json (12 traders/processors). Keyed by
# the same display_name so the two cannot drift apart silently.
# ─────────────────────────────────────────────────────────────────────────────
COMPANY_TERMS = {
    "ADM":            ["adm", "archer-daniels", "archer daniels"],
    "Bunge":          ["bunge"],
    "Cargill":        ["cargill"],
    "COFCO":          ["cofco"],
    "JBS":            ["jbs"],
    "Louis Dreyfus":  ["louis dreyfus", "ldc"],
    "Nutrien":        ["nutrien"],
    "Olam Group":     ["olam"],
    "Tyson Foods":    ["tyson"],
    "Viterra":        ["viterra"],
    "Wilmar":         ["wilmar"],
    "Yara International": ["yara"],
}

# Chokepoints and corridor infrastructure. A closure at any of these reprices
# freight on routes the flow atlas already maps, which is exactly the kind of
# story this page should catch even with no commodity named.
CHOKEPOINT_TERMS = [
    "suez canal", "panama canal", "strait of hormuz", "bab el-mandeb",
    "bosphorus", "dardanelles", "turkish straits", "red sea", "black sea",
    "strait of malacca", "gulf of aden", "port of santos", "port of rosario",
    "mississippi river", "parana river", "rhine",
    "grain corridor", "shipping lane", "freight rates", "port strike",
    "port closure", "port congestion", "dry bulk", "panamax", "container rates",
]

# "adm" and "ldc" are short enough to collide with ordinary text, so they are
# only accepted alongside a corroborating business term. Same discipline as
# the country gazetteer's AMBIGUOUS guards.
COMPANY_AMBIGUOUS = {
    "adm": ["archer", "grain", "crush", "soybean", "ethanol", "earnings",
            "trader", "processor", "corn"],
    "ldc": ["louis dreyfus", "trader", "grain", "commodities"],
    "jbs": ["beef", "meat", "packer", "poultry", "pork", "brazil", "plant"],
}

# A chokepoint alone is NOT enough. Hormuz, Suez and the Red Sea are written
# about overwhelmingly as OIL stories, and the first run of the chokepoint
# route returned six consecutive crude-price headlines. A closure does reach
# food — via fertiliser, freight and Gulf grain imports — but that is a
# second-order story, and this page is not an energy desk.
#
# So: a chokepoint qualifies an item only when the headline is not dominated
# by energy terms. Commodity- and company-matched items are unaffected; an
# oil headline that also says "wheat" still gets in on the commodity route.
ENERGY_DOMINANT = [
    "oil", "crude", "brent", "wti", "barrel", "opec", "gas", "lng",
    "petroleum", "refinery", "refiner", "pipeline", "energy market",
    "diesel", "gasoline", "petrol", "tanker war", "fuel prices",
]

# Food/trade context that RESCUES a chokepoint story from the energy filter —
# if both appear, it is a food-logistics story and belongs here.
FOOD_CONTEXT = [
    "food", "grain", "wheat", "corn", "maize", "rice", "soy", "fertiliser",
    "fertilizer", "agriculture", "agricultural", "crop", "harvest", "livestock",
    "import", "export", "shipping", "freight", "supply chain", "container",
    "bulk carrier", "cargo", "port", "hunger", "famine",
]

# Terms that mark a headline as a DISRUPTION rather than commentary. Used to
# decide which items are allowed to surface on the Live Disturbances board.
#
# The bar is deliberately high. Disturbances is a list of events; news items
# are third-party CLAIMS. Mixing the two without discipline would turn an
# evidence surface into a headline ticker, so an item is only promoted when it
# names a concrete disruptive action AND resolves to a country on a corridor.
DISRUPTION_TERMS = [
    "export ban", "export restriction", "export curb", "export quota",
    "export tax", "export levy", "export duty", "banned exports",
    "halts exports", "suspends exports", "bans exports",
    "import ban", "port closure", "port strike", "port blockade",
    "shipping halted", "blockade", "embargo", "sanctions",
    "crop failure", "harvest failure", "drought emergency", "famine",
    "state of emergency", "food emergency", "shortage", "rationing",
    "price cap", "price controls", "stockpile release", "reserve release",
    "tariff", "quota", "recall", "outbreak", "culled", "cull",
]
