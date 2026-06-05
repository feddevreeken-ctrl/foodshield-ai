# FoodShield AI — Food Trade Data: Handoff & Continuation Guide

_Last updated: June 2026. Hand this to a new chat so it can continue the food-trade-data
build without re-learning everything. Read this top-to-bottom first._

---

## 1. What FoodShield's commodity trade data IS (the architecture)

There is **one source-of-truth file**: `data/commodity_flows.json`. It holds, per commodity:
`balances` (per-country prod/cons/exp/imp/net, kt), `flows` (the bilateral lines on the
map — `{from,to,value(kt),kind,src,note}`), `rankings`, `companies`, `global`, `forecast`,
`scenarios`. A registry of named sources sits at top level under `_sources`.

This file is **embedded inline** inside `index.html` as `window.__BEEF_DATA__` (yes, the
variable is still called that for historical reasons — it now holds ALL commodities). The
inline embed exists because the app is often opened as a `file://` URL, and browsers BLOCK
`fetch()` on file:// (CORS) — so without the inline copy the trade data is empty and the map
shows nothing. **CRITICAL: after ANY edit to `data/commodity_flows.json`, you MUST regenerate
the inline embed**, or the changes won't show in the app:

```bash
node -e "const fs=require('fs');const d=JSON.parse(fs.readFileSync('data/commodity_flows.json','utf8'));
const inline={_sources:d._sources,commodities:d.commodities};let h=fs.readFileSync('index.html','utf8');
h=h.replace(/window\.__BEEF_DATA__ = \{[\s\S]*?\};\n/, 'window.__BEEF_DATA__ = '+JSON.stringify(inline)+';\n');
fs.writeFileSync('index.html',h);console.log('embed regenerated');"
cp index.html foodshield-v21.html   # keep the mirror in sync
```

The frontend reads it through these functions in `index.html`:
- `_beefData()` → returns `LIVE.commodity_flows` or the inline fallback.
- `_curatedKey(label)` → maps a chip label ("Maize (feed)", "Soybeans"…) to a commodity key.
- `curatedPartners(country, label, dir)` → ranked bilateral partners for the panel + map.
- `curatedCommoditiesFor(country, dir)` → which commodity CHIPS to show for a country.
- `tfaShowFlows()` → draws the map lines + side panel (curated is TIER 0, drawn first).
- Company tab: `_beefCompanyFor()` / `beefCompanyCard()`.
- Scenario tab: each commodity's scenarios are injected into the `SCENARIOS` array with a
  flow-dependence `kick()`; categories live in `channelOrder` + `channelMeta`.
- Methodology tab: the "Curated commodity trade flows" card.

**To add a new commodity to the app you must touch FOUR places in index.html** (not just the
data): (1) `_curatedKey` regex, (2) `_CURATED_LABEL` chip-label map, (3) the `SCENARIOS` array
+ `channelOrder`/`channelMeta` for its scenarios, (4) regenerate the inline embed. The company
card + balance note are generic and need no per-commodity wiring.

---

## 2. Current state (what's DONE)

5 commodities are live, wired through Atlas + company tab + scenarios + methodology:

| Commodity | Flows | Observed | WITS-2024 | Balances | Companies | Scenarios |
|-----------|------:|---------:|----------:|---------:|----------:|----------:|
| beef      | 49    | 23       | 0 (Beefmap-sourced) | 19 | 6 | 5 |
| wheat     | 118   | 93       | 84        | 46       | 6         | 6 |
| rice      | 92    | 77       | 61        | 36       | 6         | 6 |
| maize     | 65    | 46       | 36        | 37       | 6         | 6 |
| soybeans  | 63    | 45       | 40        | 28       | 6         | 6 |

387 flows total, ~57% reconciled to **WITS/UN Comtrade 2024**. 56 named sources. Beef came
from the curated World Beef Map (`/Projects/Beefmap preview/beef-data.js`); the 4 staples were
researched per-commodity and then reconciled to WITS 2024.

Per-commodity research notes (with honest caveats) are in `data/{wheat,rice,maize,soybeans}_RESEARCH_NOTES.md`.

---

## 3. What was WRONG before (so you don't repeat it)

1. **Fabricated flows.** The original Trade Flow Atlas *computed* flows from share-arrays and
   dressed modeled estimates as precise percentages — it showed identical "350 kt / $X" rows
   and wrong suppliers (e.g. USA as China's #1 beef supplier instead of Brazil). FIXED by the
   curated cite-or-flag model. **Never reintroduce computed/fabricated flows.**
2. **file:// caching & fetch-block.** Repeatedly, edits "didn't show" because the browser
   loaded a cached page OR couldn't fetch data on file://. FIXED via the inline embed + a
   no-cache meta tag. The user should open via `RUN_FOODSHIELD.command` (a local server) or
   hard-reload (Cmd+Shift+R) / incognito. There is a green build banner at the top of the page
   on load showing the build + China-maize flow count — if the user can't see new data, the
   FIRST thing to check is whether they're on a cached page.
3. **Stale data.** Some flows were 2023 or earlier. **HARD RULE (owner): nothing older than
   2024.** Use WITS 2024, else USDA marketing-year 2024/25. Never downgrade a flow's year.
4. **Coverage gaps.** Agents capped country lists too tightly and missed major importers
   (Netherlands soybeans via Rotterdam, etc.). Always cover the genuine top ~25-40 traders.
5. **Weak scenario impacts.** The first scenario multiplier was too low (+4 for a 70%-dependent
   country). Now `Math.pow(dep,0.85)*severity*38` → realistic (~+10). If impacts feel flat,
   that multiplier is the knob.

---

## 4. HOW to do the research (the method that works)

### The reliability stack (best first) — owner-mandated, in `scripts/COMMODITY_RESEARCH_SPEC.md`
1. **UN Comtrade** — official raw bilateral source.
2. **FAOSTAT** — food/agri commodity definitions, production–trade.
3. **CEPII BACI** — research-grade *cleaned* bilateral data (HS6).
4. **World Bank WITS** — our PRIMARY practical pull (interface onto Comtrade).
5. **ITC Trade Map** — quick cross-check.
Plus commodity bodies (IGC grains, MPOC/GAPKI palm, ICO coffee, ICCO cocoa, IFA fertilizer,
FAO GLOBEFISH fish) + company filings for the company data.

### The proven WITS 2024 pull (use WebFetch on this HTML page — it WORKS)
```
https://wits.worldbank.org/trade/comtrade/en/country/{IMPORTER_ISO3}/year/2024/tradeflow/Imports/partner/ALL/product/{HS6}
```
It returns a partner table with **Quantity in Kg**. Convert Kg→kt (÷1,000,000, round). Each
partner row → a flow `{from:partner, to:importer, value:kt, kind:"observed", src:"wits",
note:"WITS/UN Comtrade 2024: {importer} imported {kt} kt of {commodity} from {partner} (HS {code}, gross imports)."}`.
Prefer **importer-reported imports** (more accurate than exports; imports CIF vs exports FOB →
10-20% gaps are normal). The WITS REST API (`tradestats-trade`) only gives aggregate partner
*shares*, NOT bilateral tonnages — so the HTML country page above is the right endpoint.

HS6 codes: wheat 100199 · maize 100590 · rice 100630 (+100640 broken) · soybeans 120100 ·
beef 020130/020230. Look up the correct HS6 for each NEW food before pulling.

### Known WITS-2024 NON-REPORTERS (return empty — do NOT fabricate, keep USDA/FAO figure)
Mexico (value-only, no quantity), Vietnam, Iran, Iraq, Bangladesh, Yemen, Benin, Ghana,
Nigeria (captures a fraction of real rice inflow), Taiwan. For these, retain the USDA/FAO
flow and add a note explaining the non-reporting — never replace with a misleadingly tiny number.

### The cite-or-flag discipline (what makes it reviewer-defensible)
EVERY flow is `observed` (published/corroborated figure, src + figure in note) OR `modeled`
(curated route, BASIS STATED in note). **No flow without a `src` AND a `note`. No fabricated
percentages — tonnages only.** A realistic ~50/50 observed/modeled split is honest; do not
inflate "observed".

### Output schema — copy beef exactly
Read the live beef object as the template:
`node -e "console.log(JSON.stringify(require('./data/commodity_flows.json').commodities.beef,null,1))"`
Each commodity needs: `hs`, `unit`, `balances{ISO:{prod,cons,exp,imp,net:[N],year,src,flag,note}}`,
`flows[]`, `rankings{exporters,importers}`, `companies[]` (cited revenue + sourcing footprint +
"improve" note), `global` (5-yr series), `forecast` (to 2030, OECD-FAO envelope), `scenarios[]`
(4-6 cited shocks). `net` must be BOTH a number and `[number]` (frontend reads `net[last]`).

---

## 5. WHAT'S LEFT TO DO

### A. Finish the WITS-2024 reconciliation that's partially done
- **wheat**: the reconciliation agent hit a session limit partway — 84/118 flows are WITS-2024.
  Re-run the WITS pull for any wheat importer still on non-WITS values (check with:
  `node -e "const f=require('./data/commodity_flows.json').commodities.wheat.flows;console.log(f.filter(x=>x.src!=='wits').map(x=>x.from+'->'+x.to))"`).
- **maize**: 36/65 — the remaining are mostly non-reporters (Mexico, Vietnam, Iran) which are
  correctly left on USDA. Verify there are no reachable importers missed.
- General: aim to push every reachable importer to WITS-2024; leave non-reporters on USDA 2024/25.

### B. Expand to the rest of the food basket (the big task — task #62)
Build each the SAME way (research → WITS-2024 reconcile → wire 4 places → regenerate embed).
Suggested order by food-security value:
- **Wave 2 (oils/sweeteners/soft):** palm oil (HS 1511), sugar (1701), coffee (0901),
  cocoa (1801), fertilizer (31xx — not food but drives all of it), sunflower/rapeseed oil (1512/1514).
- **Wave 3 (proteins/perishables):** poultry (0207), pork (0203), dairy (0402/0406),
  fish/seafood (03xx).
- **Wave 4 (produce + long tail):** banana (0803), citrus (0805), other fruit, vegetables (07xx),
  pulses (0713), nuts (0802), barley (1003), sorghum (1007), tea (0902), spices (09xx).
Run them as parallel subagents (one commodity each), 3-4 at a time to stay within limits.

### C. Reconcile the staple RESEARCH_NOTES caveats
Each notes file flags soft figures: EU bloc-vs-member double-counting (maize/soy "EU" node +
member balances — pick ONE convention when summing), marketing-year vs calendar-year mixing,
a few value→tonnage conversions, Cambodia/Nigeria informal-trade gaps. Worth a cleanup pass.

### D. Beef → WITS 2024 (optional)
Beef is still on the World Beef Map figures (2024-ish, cited). Could be reconciled to WITS
020130/020230 2024 for consistency, but it's already cited and recent, so low priority.

### E. Lower-priority polish
- Marketing/hero design elevation (task #52, never done).
- A per-country review pass once commodity coverage is broad.
- Nothing has been pushed to Vercel — the user keeps everything LOCAL until they say push.

---

## 6. The iron rules (do not violate)

1. **Never push to Vercel** unless the user explicitly says "push". Everything stays local.
2. **Data must be 2024 or newer.** Never 2023 or earlier.
3. **Every flow needs `src` + `note`.** Never fabricate. `observed` or `modeled`, basis stated.
4. **After editing `commodity_flows.json`, regenerate the inline embed** (§1) and `cp` to
   `foodshield-v21.html`, or the app won't show the change.
5. **Validate after every edit:** `node -e "JSON.parse(require('fs').readFileSync('data/commodity_flows.json','utf8'));console.log('valid')"`
   and node --check the main inline scripts of index.html.
6. **If the user says "I don't see the changes"** — it's almost always a cached file:// page,
   not a data bug. Verify on disk first (the data is usually correct), then have them hard-reload
   or use `RUN_FOODSHIELD.command`. Check the green build banner.

---

## 7. Key files

- `data/commodity_flows.json` — THE data (source of truth).
- `index.html` — the app; inline embed `window.__BEEF_DATA__` mirrors the data file.
- `foodshield-v21.html` — byte-identical mirror, keep synced.
- `scripts/COMMODITY_RESEARCH_SPEC.md` — the canonical research instructions (source stack, HS codes, schema).
- `scripts/build_commodity_flows_from_beefmap.js` — regenerates beef from the Beefmap.
- `data/{commodity}_RESEARCH_NOTES.md` — per-commodity provenance + honest caveats.
- `RUN_FOODSHIELD.command` — one-click local server launcher (the right way to view the app).
- `/Projects/Beefmap preview/beef-data.js` — the original curated model (read-only reference).
