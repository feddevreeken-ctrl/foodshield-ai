# FoodShield AI — Research & Data Handoff

_Last updated: 5 June 2026. Hand this to the next AI/session so it can continue the commodity-data build without re-learning everything. Read top-to-bottom first._

---

## 0. TL;DR — exact current state

- **25 commodities** live in `data/commodity_flows.json` → wired into `index.html`.
- **1,500 bilateral trade flows** (1,255 observed / 245 modeled), **161 countries**.
- **88 cited scenarios** in the `SCENARIOS` array (+ 5 untagged beef scenarios = ~93).
- **FDRS v2** (9-component) used consistently; legacy structural ratio **~48.7%** and falling.
- **Everything is LOCAL. Nothing pushed to Vercel.** The owner pushes manually only when he says "push".

**The 25 commodities:** beef, wheat, rice, maize, soybeans, palmoil, sugar, coffee, cocoa, vegoils, fertilizer, poultry, pork, dairy, fish, bananas, pulses, shrimp, barley, sorghum, citrus, nuts, tea, sheepgoat, potatoes.

---

## 1. IMMEDIATE UNFINISHED WORK (do this first)

Wave 5 was mid-build. **4 of 8 are merged (nuts, tea, sheepgoat, potatoes). 4 are NOT yet merged:**

1. **vegetables** (HS 0702 tomatoes + 0703 onions) — dataset was BUILT by a subagent (Comtrade-triangulated, ~42 flows, 20 balances, 6 companies) but NOT yet written to a staging file or merged. The full JSON is in the session transcript. If lost, rebuild from `/sessions/.../outputs/seed_vegetables.json` (FAOSTAT 2024 seed, on disk) + Comtrade enrichment. NOTE the agent's `net_series` had 5 elements (fine — frontend reads `net[last]` + uses series for sparkline).
2. **eggs** (HS 0407) — seed at `seed_eggs.json` (27 flows). NOT built. Build via subagent.
3. **offal** (HS 0206 edible offal) — seed at `seed_offal.json` (52 flows). NOT built.
4. **apples** (HS 0808) — seed at `seed_apples.json` (~105 flows). NOT built. WATCH: the FAOSTAT "apple" substring also matches "pineapple" — filter to item == "Apples" exactly.

**After merging those 4:**
- Add **Wave 5 scenarios** (4-5 cited each for nuts, tea, sheepgoat, potatoes, vegetables, eggs, offal, apples) — use the generator pattern in §4.
- **Refresh methodology copy**: it currently says "1,366 curated bilateral flows across 159 countries" and "21 commodities". After Wave 5 it should be ~25-29 commodities / ~1,600+ flows / ~161 countries. Update the exact strings (see §5).
- Regenerate embed + sync mirror + run final QA (§3).

---

## 2. THE DATA-SOURCING METHOD (hard-won — follow exactly)

### Source priority, most-accurate first
1. **FAOSTAT 2024 Detailed Trade Matrix** (the uploaded `Trade_DetailedTradeMatrix_E_All_Data.zip`, non-normalized, 2.1GB) — THE authoritative bilateral backbone. Has a `Y2024` column. Covers ~114 importer countries incl. many non-reporters. This is the seed for everything.
2. **UN Comtrade preview API** (live, for cross-check + enrichment): `https://comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode={M49}&period=2024&cmdCode={HS}&flowCode=M` — use `web_fetch` (the bash VM blocks the host; web_fetch routes around it). Parse ONLY rows with `partner2Code=0, motCode=0, customsCode=C00` (the bilateral total) — else you double-count. `netWgt` kg → kt (÷1e6).
3. **USDA FAS PSD** (production/export balances for grains, oilseeds, meat, dairy, sugar, tree nuts), **FAO** commodity bodies (IGG Tea, ICCO cocoa, ICO coffee, INC nuts, MLA sheepmeat), **ITC**, national boards.

### The exact FAOSTAT extraction recipe (REUSE THIS)
```bash
# 1. stream-extract import-quantity rows from the 2.1GB zip (don't unzip the whole file):
cd /sessions/.../outputs
unzip -p ZIP "Trade_DetailedTradeMatrix_E_All_Data.csv" | LC_ALL=C grep -a -m1 "Reporter Country Code" > imp.csv
unzip -p ZIP "Trade_DetailedTradeMatrix_E_All_Data.csv" | LC_ALL=C grep -a ',"5610","Import quantity",' >> imp.csv
# imp.csv is non-normalized: Y2024 is COLUMN INDEX 88 (0-based). cols: 1=repM49, 4=partM49, 8=item, 10=elementName, 88=Y2024 value (tonnes)
# 2. Python: keep rows with non-empty col[88], map item->commodity, kt=val/1000, threshold >=15kt, roll up sub-items per (rep,part) pair.
# 3. M49->ISO3 via the standard map (built inline in earlier scripts; or use Reporter/PartnerCountries.csv inside the zip).
```

### CRITICAL — item-basis matching (the trap that corrupts data)
FAOSTAT item names are BROADER than our HS basis. ONLY keep the matching product:
- **soybeans** = "Soya beans" (the SEED) — EXCLUDE "Cake of soya beans" (meal) and "Soya bean oil".
- **palmoil** = "Palm oil" — exclude palm kernel oil.
- **vegoils** = sunflower-seed oil + rape/colza oil only.
- **dairy** = milk-powder items only (whole/skim dried).
- **citrus** = "Oranges" only (mandarins/lemons separate).
- **apples** = exactly "Apples" (NOT pineapple — substring trap).
- **nuts** = tree nuts in shell/shelled (almonds/cashew/walnut/pistachio/hazelnut) — note cashews are technically HS0801 but anchor the basket; flag in notes.
- **pulses** = lentils/chickpeas/dry beans/dry peas/broad/cow/pigeon peas.
If unsure an item matches → EXCLUDE it. Always state the item filter per commodity.

### Provenance & flags (NON-NEGOTIABLE honesty rules)
- Every flow needs `src` AND `note`. `kind` = `observed` (importer-reported/published figure) or `modeled` (estimate/mirror, basis stated).
- `src` values in use: `wits` (Comtrade via WITS), `comtrade`, `faostatTCL` (FAOSTAT 2024 matrix), `witsMirror` (exporter-side mirror for non-reporters), `usdaPSD`/`usdaGrain`/etc, `fao`, `ico`/`icco`/`mla` (commodity bodies).
- **Mirror data** (exporter reports shipping TO a non-reporter): `kind:"modeled", src:"witsMirror"`, note "(X is a non-reporter)".
- **NEVER fabricate.** "Not captured for this country/year" is a valid result. The map shows an honest message (see §6 fabrication-bug fix), never invented %-shares.
- **2024 OR NEWER ONLY.** The owner's hard rule. The FIRST uploaded FAOSTAT zip (`FAOSTAT_T-Z_E.zip`) only went to 2023 → was NOT used. Use marketing-year MY2024/25 where a flow surged (e.g. USA→Vietnam maize jumped 5→1,100 kt — calendar-2024 understated it).

### Sources that DON'T work
- Comtrade bulk CSVs with `cmdCode=TOTAL` / `partnerISO=W00` (World) = useless (no commodity/partner breakdown).
- Per-reporter Comtrade CSVs (one country each, 100k-row API page cap) = only good for spot-verifying ONE country.
- The old WITS HTML path-form URLs error now; use the Comtrade preview API instead.
- `zsh` chokes on inline `#` comments in pasted commands — give clean one-per-line commands to the owner.

---

## 3. BUILD → MERGE → VALIDATE pipeline (every commodity)

1. Write FAOSTAT seed to `seed_{key}.json` (done for Wave 5 — they're on disk).
2. Subagent: take the seed as verified backbone, triangulate/enrich via Comtrade + USDA + FAO bodies, return ONE JSON object matching the EXACT schema (see §4). USE EVERY SOURCE.
3. Normalize the agent output if needed (common drift: rankings `{iso,name,v}` → `{iso,kt}`; `forecast.rates` keyed-by-ISO → `{production:NUM,trade:NUM}`; company objects missing `iso`/`flag`/`improve`; dedup same-pair flows that differ only by HS sub-line; strip inline scenarios). Write to `data/_wave{N}_{key}.json`.
4. Validate: 0 dups, 0 self-flows, valid ISO3, every flow has src+note, no value >120000, balances have numeric `prod` + array `net`, rankings `{iso,kt}`, companies have `iso/flag/note`.
5. Merge into `data/commodity_flows.json` (cp a `.bak_*` first), dedup against existing pairs.
6. Wire `_curatedKey` (regex → key) + `_CURATED_LABEL` (key → display label) in index.html — TWO places.
7. **Regenerate the inline embed** (CRITICAL — see §6 iron rules), `cp index.html foodshield-v21.html`, validate embed JSON parses.
8. Verify render: `curatedPartners(country, label, dir)` returns rows for a sample country.

---

## 4. THE EXACT COMMODITY SCHEMA (copy this)
```json
{"hs":"<HS>","unit":"kt",
 "balances":{"ISO":{"iso3":"ISO","prod":N,"cons":N,"exp":N,"imp":N,"net":[N],"net_series":[N],"year":"2024","src":"...","flag":"sourced|modeled","note":"..."}, ...14-20 countries...},
 "flows":[{"from":"ISO","to":"ISO","value":N,"kind":"observed|modeled","src":"...","note":"..."}, ...28-45...],
 "rankings":{"exporters":[{"iso":"ISO","kt":N},...],"importers":[{"iso":"ISO","kt":N},...],"exportSrc":"...","importSrc":"...","note":"..."},
 "companies":[{"name":"...","hq":"...","iso":"ISO","src":"...","flag":"sourced|modeled","sourcing":["ISO",...],"note":"...","improve":"..."}, ...5-6...],
 "global":{"years":[2020,2021,2022,2023,2024],"production":[...],"consumption":[...],"exports":[...],"src":"...","note":"..."},
 "forecast":{"horizon":2030,"base":2024,"src":"oecdfao","method":"...; 2030=2024×(1+rate)^6","rates":{"production":NUM,"trade":NUM}},
 "scenarios":[]}
```
**Scenarios** are added SEPARATELY (not in the commodity object). Generator pattern: each scenario is `{id,label,cat:'X sector',baseSev,sevUnit:'×',channel:0,desc,formula,_shockIso,_src,_commodity, kick:function(c,sv){...curatedPartners share-of-imports ^0.85 × sv × 38...}, impact:function}`. Inject before the `SCENARIOS` array's closing `];`, add the sector to `channelOrder` + `channelMeta`. Scenarios render in collapsible `<details>` dropdowns per sector.

---

## 5. METHODOLOGY COPY TO UPDATE (after every wave)
In index.html, update these exact strings (current values shown):
- `"<N> commodities</b> are now covered — beef, wheat, ..."` (list all commodity display names)
- `"1,366 curated bilateral flows</b> across 159 countries."` → new counts
- `"all <N> commodities now follow the same curated-cited method"`
- The `<meta>` description tags say "34 pipelines" — keep consistent.

---

## 6. KEY FIXES ALREADY MADE (don't re-break these)

- **v23.4 seed-all-commodities:** `loadLiveData` originally seeded ONLY beef into `LIVE.commodity_flows`, but `_beefData()` returns the whole LIVE object once `.beef` exists → all other commodities were invisible ("0 partners"). FIXED to seed ALL commodities from the embed.
- **v23.3 markers/panel sync + redraw gating:** `tfaShowFlows`/`tfaSelectChip`/`tfaSetDir` now resolve country from `_currentOpenISO` OR `Globe.state.selectedISO` and always redraw.
- **v24.1 Globe-not-ready retry:** `tfaShowFlows` retries up to 12× at 200ms if the map isn't initialized (was silently dropping the draw → "no lines/sometimes no panel").
- **v24.3 fabrication kill:** non-curated heritage chips (Pulses/Vegetable oil/Tea) used to fall through to `COMMODITY_TRADE_ROUTES` which FABRICATED %-shares + fake company names (the "Chad→Pulses Canada 41% AGT Food" bug). FIXED: any specific commodity not in the 15→25 curated keys shows an honest "not tracked / no curated data — doesn't mean it doesn't import X; may be a non-reporter or re-export-routed; we don't fabricate" message. Only the "All" aggregate uses heritage.
- **Chip cap:** `TOP_N = Math.max(4, curatedChips.length)` so all curated chips show (not capped into "More…").
- **EU double-count:** EU-node flows are "rest-of-EU residuals" (EU total minus itemized members) on the Brussels coord, to avoid double-counting with member flows.
- **FDRS v2 throughout:** weights `[0.23,0.16,0.11,0.09,0.09,0.08,0.06,0.12,0.06]` (~line 13478); scenario engine `_simulateScenario`/`_applyScenarioDelta` (HALF=22 diminishing-returns); 2030 outlook driver-attribution all use v2.

### The IRON RULES (from the original handoff, still apply)
1. **Never push to Vercel** unless owner says "push". All local.
2. **Data 2024 or newer.** Never older.
3. **Every flow needs src + note.** Never fabricate.
4. **After editing commodity_flows.json, regenerate the inline embed** (`window.__BEEF_DATA__` in index.html) and `cp index.html foodshield-v21.html`, or the app won't show changes:
```bash
node -e "const fs=require('fs');const d=JSON.parse(fs.readFileSync('data/commodity_flows.json','utf8'));const inline={_sources:d._sources,commodities:d.commodities};let h=fs.readFileSync('index.html','utf8');h=h.replace(/window\.__BEEF_DATA__ = \{[\s\S]*?\};\n/,'window.__BEEF_DATA__ = '+JSON.stringify(inline)+';\n');fs.writeFileSync('index.html',h);"
cp index.html foodshield-v21.html
```
5. **Validate after every edit** (JSON.parse the data + the embed; node-check is unreliable on the huge file — instead grep `<script` vs `</script>` counts: 11 vs 9 is EXPECTED, an HTML comment contains a literal `<script>`).
6. **"I don't see changes"** = almost always a cached file:// page. Hard-reload (Cmd+Shift+R) or use `RUN_FOODSHIELD.command`. Check the green build banner.

---

## 7. ACCURACY CROSS-CHECK WORK DONE (FAOSTAT reconciliation)
Using the FAOSTAT 2024 matrix, 26 of our flows were reconciled to authoritative importer-reported figures. Real errors fixed: wheat CAN→CHN 53→2524, wheat RUS→ITA 1200→59, maize BRA→MEX 1000→66, Italy-wheat cluster + Belgium-banana undercounts. DELIBERATELY KEPT vegoils RUS→CHN 1797 (ours = sun+rapeseed, FAOSTAT = sunflower-only = correct basis difference, NOT an error). Rice discrepancies (74) are a FAOSTAT milled-equivalent basis artifact — IGNORED.

**Still worth doing:** a systematic FAOSTAT cross-check of the Wave 2-5 flows (only Wave 1-ish was reconciled). The matrix is the gold standard for catching our modeled-flow errors.

---

## 8. WHAT'S LEFT (priority order)
1. **Finish Wave 5** (vegetables/eggs/offal/apples merge + scenarios + methodology) — §1.
2. **Wave 5 scenarios** for all 8 new commodities.
3. **FAOSTAT cross-check** Wave 2-5 flows for errors (§7 method).
4. **Coverage tail:** ~50 small/island + true-non-reporter countries still have no flows (Chad, CAR, Eritrea, Burundi, PRK, small Pacific/Caribbean). Most are genuinely unsourceable from 2024 data — honest blanks. Could mirror-fill more at a lower threshold.
5. **Track B baselines:** ~48.7% of per-country structural numbers still `legacy_curated`. The bulk accelerators (`scripts/refresh_net_food_trade.py`, `refresh_faostat_fbs.py`) need the owner's Mac (FAOSTAT API/network). Per-country WITS supplier flips can continue in-sandbox.
6. **Wave 6 ideas:** grapes/other fruit, spices (09xx), eggs done?, processed (frozen potato HS2004), live animals.
7. **Push to Vercel** when owner approves.

---

## 8b. KEY FILES
- `data/commodity_flows.json` — THE source of truth (all 25 commodities).
- `index.html` — the app; inline `window.__BEEF_DATA__` mirrors the data file; `foodshield-v21.html` is a byte-identical mirror.
- `data/countries.json` — per-country structural overlay (FDRS components, balances). `_meta` documents the quality-flag taxonomy.
- `data/_wave5_{nuts,tea,sheepgoat,potatoes}.json` — staged Wave 5 (merged). `data/.archive_*` — older staged datasets.
- `scripts/flag_legacy_countries.py` — reports the legacy ratio + worklist.
- `BASELINE_REVERIFICATION_SPEC.md` — the Track B re-sourcing process.
- `scripts/COMMODITY_RESEARCH_SPEC.md` — original research spec.
- Backups: `data/commodity_flows.json.bak_*` (one per merge step).
- Uploaded FAOSTAT matrix: `/sessions/.../uploads/Trade_DetailedTradeMatrix_E_All_Data.zip` (re-extractable per §2 recipe).

---

## 9. SANITY CHECKLIST before declaring a wave done
- [ ] All new commodities in `data/commodity_flows.json`, embed regenerated, mirror synced.
- [ ] `_curatedKey` + `_CURATED_LABEL` wired for each new key.
- [ ] Scenarios added + channelOrder/channelMeta updated; `kick()` functions execute without throw.
- [ ] Methodology copy numbers updated.
- [ ] Full QA: 0 structural issues (dup/self/ISO/magnitude/src), embed JSON valid, script-tag count 11/9.
- [ ] A few sample countries render chips + lines + right-panel (especially a newly-added country).
- [ ] Memory updated (`spaces/.../memory/project_foodshield_wave2_coverage.md`).
- [ ] NOT pushed (unless owner said "push").
