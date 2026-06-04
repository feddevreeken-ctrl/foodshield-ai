"""
trade_pipeline/config.py — THE ONE FILE YOU EDIT.

This holds every knob for the trade-data pipeline: which countries to pull,
which commodities, the endpoint, throttle, and the ISO3<->M49 code maps. The
other modules (pull / merge / build_fields / audit) import from here and should
NOT need editing for routine changes.

If you want to:
  - pull more/fewer countries        → edit ALL_IMPORTERS below
  - add a commodity                  → add to COMMODITIES (HS code -> name)
  - slow down to avoid rate limits   → raise THROTTLE_SECONDS
  - change the data year             → edit YEAR
…you only touch this file.
"""

# UN Comtrade public preview endpoint — no API key required, ~500 calls/day.
# (If you later use your keyed subscription, see pull.py's header note; the
#  public endpoint is what refresh_comtrade.py has used reliably since May 2026.)
ENDPOINT = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

# Most recent full year the free tier serves.
YEAR = 2024

# Seconds between calls. The public endpoint tolerates ~1/sec; 1.5 is polite.
# Raise if you see HTTP 429 (rate limited).
THROTTLE_SECONDS = 1.5

# Commodities to pull: HS6 code -> FoodShield commodity key.
# (Matches refresh_comtrade.py so the merged file stays consistent.)
COMMODITIES = {
    "1001": "wheat",
    "1005": "maize",
    "1006": "rice",
    "1201": "soybeans",
    "1511": "palm_oil",
    "1701": "sugar",
    "0901": "coffee",
    "1801": "cocoa",
    "3102": "fertilizer",
    "0201": "beef",
}

# ── ISO3 -> M49 numeric reporter code ────────────────────────────────────────
# This is the FULL importer set (every country with a Comtrade reporter code).
# pull.py iterates this. Batching is handled by pull.py's --batch / --start flags,
# NOT by editing this list — leave it complete so the pipeline knows the universe.
ISO3_TO_M49 = {
    "AFG":4,"ALB":8,"DZA":12,"AGO":24,"ARG":32,"AUS":36,"AUT":40,"BGD":50,
    "ARM":51,"BEL":56,"BOL":68,"BIH":70,"BWA":72,"BRA":76,"BLZ":84,"SLB":90,
    "BRN":96,"BGR":100,"MMR":104,"BDI":108,"BLR":112,"KHM":116,"CMR":120,"CAN":124,
    "CPV":132,"CAF":140,"LKA":144,"TCD":148,"CHL":152,"CHN":156,"TWN":158,"COL":170,
    "COG":178,"COD":180,"CRI":188,"HRV":191,"CUB":192,"CYP":196,"CZE":203,"DNK":208,
    "DOM":214,"ECU":218,"SLV":222,"GNQ":226,"ETH":231,"ERI":232,"EST":233,"FJI":242,
    "FIN":246,"FRA":250,"DJI":262,"GAB":266,"GEO":268,"GMB":270,"DEU":276,"GHA":288,
    "GRC":300,"GTM":320,"GIN":324,"GUY":328,"HTI":332,"HND":340,"HKG":344,"HUN":348,
    "ISL":352,"IND":356,"IDN":360,"IRN":364,"IRQ":368,"IRL":372,"ISR":376,"ITA":380,
    "CIV":384,"JAM":388,"JPN":392,"KAZ":398,"JOR":400,"KEN":404,"PRK":408,"KOR":410,
    "KWT":414,"KGZ":417,"LAO":418,"LBN":422,"LSO":426,"LVA":428,"LBR":430,"LBY":434,
    "LTU":440,"LUX":442,"MAC":446,"MDG":450,"MWI":454,"MYS":458,"MDV":462,"MLI":466,
    "MLT":470,"MRT":478,"MUS":480,"MEX":484,"MNG":496,"MDA":498,"MNE":499,"MAR":504,
    "MOZ":508,"OMN":512,"NAM":516,"NPL":524,"NLD":528,"VUT":548,"NZL":554,"NIC":558,
    "NER":562,"NGA":566,"NOR":578,"PAK":586,"PAN":591,"PNG":598,"PRY":600,"PER":604,
    "PHL":608,"POL":616,"PRT":620,"GNB":624,"TLS":626,"QAT":634,"ROU":642,"RUS":643,
    "RWA":646,"SAU":682,"SEN":686,"SRB":688,"SLE":694,"SGP":702,"SVK":703,"VNM":704,
    "SVN":705,"SOM":706,"ZAF":710,"ZWE":716,"ESP":724,"SSD":728,"SDN":729,"ESH":732,
    "SUR":740,"SWZ":748,"SWE":752,"CHE":756,"SYR":760,"TJK":762,"THA":764,"TGO":768,
    "TTO":780,"ARE":784,"TUN":788,"TUR":792,"TKM":795,"UGA":800,"UKR":804,"EGY":818,
    "GBR":826,"TZA":834,"USA":842,"BFA":854,"URY":858,"UZB":860,"VEN":862,"WSM":882,
    "YEM":887,"ZMB":894,
    # Additional partner codes that appear as SUPPLIERS but may not be importers we
    # pull — needed so partner resolution in pull.py doesn't silently drop them.
    "HKG":344,"SGP":702,"TWN":158,"NZL":554,"CHL":152,"PER":604,"COL":170,
    "CHE":757,"BHR":48,"NOR":579,
}

# M49 -> ISO3 (auto-derived; used to resolve partner codes the preview returns).
M49_TO_ISO3 = {v: k for k, v in ISO3_TO_M49.items()}

# Comtrade sometimes uses ALTERNATE/legacy M49 codes for the same country (e.g.
# France with overseas territories = 251, India legacy = 699, Switzerland incl.
# Liechtenstein = 757). Map these to ISO3 too so real suppliers aren't dropped.
# (Discovered via pull.py's unmapped-code diagnostics, Jun 2026.)
M49_TO_ISO3.update({
    699: "IND",   # India (alt)
    251: "FRA",   # France incl. Monaco / overseas (alt)
    842: "USA",   # USA (already mapped, kept explicit)
    757: "CHE",   # Switzerland incl. Liechtenstein (alt)
    # Real countries the preview endpoint returned with codes not in the base map
    # (found via pull.py unmapped diagnostics, batch 1, Jun 2026). Verified against
    # the UN M49 standard before adding:
    807: "MKD",   # North Macedonia
    31:  "AZE",   # Azerbaijan
    531: "CUW",   # Curaçao
    678: "STP",   # Sao Tome and Principe
    204: "BEN",   # Benin
    20:  "AND",   # Andorra
    275: "PSE",   # State of Palestine
    28:  "ATG",   # Antigua and Barbuda
    308: "GRD",   # Grenada
    # Small islands / offshore + transshipment hubs that show up heavily as EXPORT
    # destinations (food routed through them). Real countries — found via pull.py
    # --flow X unmapped diagnostics, Jun 2026.
    292: "GIB",   # Gibraltar
    584: "MHL",   # Marshall Islands
    52:  "BRB",   # Barbados
    690: "SYC",   # Seychelles
    44:  "BHS",   # Bahamas
    136: "CYM",   # Cayman Islands
    60:  "BMU",   # Bermuda
    234: "FRO",   # Faroe Islands
    540: "NCL",   # New Caledonia
    # More Caribbean/Pacific destinations seen in export pulls (batch 2):
    533: "ABW",   # Aruba
    92:  "VGB",   # British Virgin Islands
    796: "TCA",   # Turks and Caicos
    659: "KNA",   # Saint Kitts and Nevis
    660: "AIA",   # Anguilla
    670: "VCT",   # Saint Vincent and the Grenadines
    212: "DMA",   # Dominica
    662: "LCA",   # Saint Lucia
    # NON-country pseudo-codes — intentionally NOT mapped, so they stay dropped:
    #   490 "Other Asia, nes" · 568 "Other Europe, nes" · 899 "Areas, nes"
    #   837 "Bunkers / special categories" (ship/air stores, not a destination)
    # Mapping these would fabricate a partner.
})

# The full importer universe pull.py walks (ISO3 list). Ordered by rough import
# importance so an interrupted/quota-limited run still covers the countries that
# matter most first. Everything not listed here but in ISO3_TO_M49 is appended.
PRIORITY_ORDER = [
    "EGY","CHN","IDN","JPN","KOR","TUR","BGD","NGA","PHL","DZA","SAU","MEX","IRN",
    "IRQ","MAR","ETH","YEM","ARE","VNM","MYS","BRA","GBR","DEU","FRA","ITA","ESP",
    "NLD","BEL","IND","PAK","THA","RUS","UKR","CAN","AUS","ARG",
]


def ordered_importers():
    """Full ISO3 list to pull, priority countries first, then the rest A-Z."""
    seen = set(PRIORITY_ORDER)
    rest = sorted(i for i in ISO3_TO_M49 if i not in seen)
    return [i for i in PRIORITY_ORDER if i in ISO3_TO_M49] + rest
