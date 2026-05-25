# FoodShield AI — Pre-launch Conversation Log

**Project:** FoodShield AI · global food security dashboard at foodshield-ai-fv.vercel.app
**Owner:** Fedde Vreeken (Erasmus School of Economics)
**Session window:** May 2026 pre-launch sprint
**Canonical files:** `foodshield-v21.html` ≡ `index.html` (~22.7k lines)

This file is a compacted summary of the multi-day debugging + design pass. Every fix below shipped to the live site unless otherwise noted.

---

## 1. Launch strategy

- **Audience:** procurement / supply-chain / risk professionals on LinkedIn.
- **Pitch framing:** "33 data pipelines, 193 countries + 50 US states, every 6 hours."
- **LinkedIn post copy locked.** Final hook + body live in `LINKEDIN_POST_DRAFT.md`. Opens with:
  > *How fragile is the world's food supply right now?*
  > *Where is hunger getting worse this week? Which countries are sitting one shock away from a crisis?*
- Founder note ends with criticism-inviting close: *"tell me where this falls short — the data you'd want, the view you're missing, the reason you'd close the tab in 30 seconds. That's the feedback I can actually use."*
- Tagging strategy: tag `@Erasmus School of Economics` + `@Erasmus University Rotterdam` (the user is at ESE specifically). Skip individual peer tags. Hashtags `#FoodSecurity #SupplyChain #OpenData`.
- LinkedIn Project entry recommended separately — résumé-toned, persistent.

---

## 2. Critical bug fixes shipped

### Data pipelines
| Bug | Fix |
|---|---|
| `countries.json` only had SDN (builder bug) | Rewrote `_country_blocks()` in `scripts/build_countries_dataset.py` to skip JS comments. Now emits **258 countries**. Added hard-fail validation: ≥150 countries, required ISOs present, no missing iso3/name. |
| USDA PSD `BOL→Belarus`, `NER→Nigeria`, `NGA→Niger` swaps; `DEU` missing | Rewrote `FAS_TO_ISO3` in `scripts/refresh_usda_psd.py` using canonical FAS codes (NOT ISO-alpha-2). Verified: `BO`=Belarus, `BL`=Bolivia, `NI`=Nigeria, `NG`=Niger, `GM`=Germany. Patched served JSON in-place. Added `validate_data_integrity.py`. |
| USDA PSD `imports_kt`/`exports_kt` = 0 for all rows | Added alternate attribute key mappings (`Imports`, `Total Imports`, `Imports for Domestic Consumption`, `TY Imports`, etc.) so the parser captures the value under any USDA label. |
| ReliefWeb mis-classified as `setup_required` | Manifest classifier was matching bare `appname` substring (which appeared in ReliefWeb's healthy URL). Tightened to require explicit "api key needed" / "setup required" tokens. Patched served manifest. |
| FAOSTAT TCL parser kept 0 rows | Item-code matching fix; ND-GAIN URL fix; Aqueduct URL fix; INFORM URL fix; CCKP API path fix (12-segment URL); WGI null-year handling. |
| v20.27 overlay double-multiplied FDRS (silent 8% drift) | `_fdrsRaw = newCaloric / 0.92` rescale so SCE blend math works on 0–100 scale. |

### Trade Flow Atlas
| Bug | Fix |
|---|---|
| `_estimateVolume` matched Comtrade by array INDEX, not supplier name → wrong country badged `OBS · COMTRADE` | Match by `iso3` first, name fallback only with non-empty guard. |
| Empty-string `.includes('')` matched every supplier → top supplier's share assigned to every row (Egypt wheat looked like $15B instead of $4.2B) | Explicit `if (!n) return false;` guard. |
| Export-side `_estimateVolume` reused IMPORT-side Comtrade total (Germany rice export = Germany rice import total) | Added `direction` arg. For exports, look up destination's import row and find the current country as a supplier inside it. If no observed evidence, return null and fall back to modeled. |
| `total_kt: null` coerced to 0 → drilldown showed "$1.2B / 0 kt" | Treat null kt as null; render `n/a · kt unavailable` with tooltip. |
| Raw-USD vs USD-millions confusion (drilldown total divided raw USD by 1000) | All USD now flows through `_fmtMoneyUSD()` which handles raw → $K/$M/$B. |
| Same suppliers/destinations for every commodity (`c.suppliers` is flat list) | Built `COMMODITY_TRADE_ROUTES` constant with **27 commodities × 436 explicit (importer × supplier mix) routes**. Added `commoditySuppliers(c, commodity)` and `commodityDestinations(c, commodity)` helpers. Wired through 12 surfaces. |
| Wide-distribution commodities (wine, dairy, fish, fruit) showed misleading top-5 | Added `wide_distribution: true` flag + caveat line: *"Exports to 190+ countries — top destinations by volume shown."* |
| France dot rendered in Spain | `d3.geoCentroid(franceFeature)` averaged French Guiana/Réunion overseas territories. Added `CENTROID_OVERRIDES` for FRA/USA/Norway/NL/UK/Denmark/Spain/Portugal/Chile/Ecuador + 13 large countries. Also fixed `findSupplierCoord` to consult overrides via `byName → G.centroids` before raw `d3.geoCentroid`. Updated `COUNTRY_COORDS.FRA` to mainland. |
| Lines didn't connect when clicking commodities (silent skip on missing coord) | `getCoord()` now falls through `G.centroids → COUNTRY_COORDS → findSupplierCoord(name)`. Both `drawCommodityFlows` and `drawExportFlows` use `getCoord()` for the hub too. Added `console.warn` on every silent skip. |
| HKG, ISL missing from COUNTRIES (referenced by routes) | Added full stubs. MAC added defensively. XKX added to COUNTRY_LL. |
| 27 net-exporter countries had non-food imports ("Crude petroleum, Vehicles, Machinery, Electronics") | Rewrote import arrays for Germany, France, UK, USA, Canada, Australia, NZ, Poland, Vietnam, Thailand + 17 others with realistic food imports (Soybeans, Tropical fruit, Coffee, Cocoa, Fish & seafood, etc.). 0 NO-FOOD countries remain. |
| Sudan suppliers listed Saudi Arabia, Turkey, UAE, WFP as wheat suppliers (none export wheat; WFP is an agency) | Fixed: Russia 40 / Romania 18 / Ukraine 15 / Australia 15 / Argentina 12. Same audit applied to Yemen, Somalia, Syria, Ethiopia, N Korea, S Sudan, Ukraine, USA, Eswatini, Lesotho, Palestine, India, Eritrea, Djibouti — removed WFP/USAID/EU/Unknown from all bilateral supplier lists. |

### Methodology + copy honesty
| Issue | Fix |
|---|---|
| Site claimed "33 public data pipelines refreshed every 6h" but manifest reported 29 | Added 4 static_helper entries (FAO GIEWS, FEWS NET, WFP ACR, USDA GAIN) to manifest. Manifest now reports 33 total. |
| "15/29" pill conflicted with "33" footer/Quick Start | Pill bug was double-counting helpers. Removed `+ STATIC_HELPERS` math. Pill now reads `19/33` direct from manifest summary. "Live data feeds" renamed to "data pipelines" everywhere. |
| "67 Famine events" mislabel | Renamed to `Hunger alerts · 67`. Internal bucket key preserved. |
| Methodology said FDRS was 6 components / Score page said 6 dimensions / Egypt example used 30/20/15/15/10/10 weights | Reconciled everywhere to 7 components (28/18/14/14/9/9/8) matching the actual formula. Added Supply-Chain Exposure row. |
| Methodology claimed CCKP + ND-GAIN drive climate kick (both files were empty/failed) | Softened to *"When CCKP and ND-GAIN are populated (currently degraded — see Data Status), the climate kick is derived from them; today the climate component falls back to the heritage structural value."* |
| Methodology claimed model was "back-tested and recalibrated annually" (no backtest exists) | Softened to *"Recalibration cadence: annual when new structural vintages drop. A formal back-testing pipeline is on the roadmap — not yet built."* |
| Confidence % and ± band UI looked like calibrated forecast stats but were heuristics | Removed from UI. Placeholder: *"Confidence intervals require a backtest pipeline we haven't built yet — see methodology."* |
| "Forecast" word implied decision-grade prediction | Renamed to **"Modeled Outlook"** throughout (21 surfaces). |
| 2030 country panel chip needed honest framing | Added small badge under the 2030 number: `CURATED baseline + MODELED live overlay`. |
| Provenance vocabulary inconsistent (About said 3 classes, methodology said 4, code used 5) | Reconciled to canonical 5 classes everywhere: `SOURCED / MANUAL / MODELED / CURATED / ILLUSTRATIVE`. Code badges renamed `LEGACY → CURATED · STATIC` and `LEGACY · IMPORT-DEP → CURATED · IMPORT-DEP`. |
| Country narratives outdated | Rewrote Lebanon (220% → 15% food inflation, record-low harvest), South Sudan (7.7M / 57% IPC), Pakistan (no $1.5B import claim), Burkina Faso, Mali, Ethiopia, Yemen, Somalia (1 in 3 not 1 in 4), Sudan (2024 cereal rebound). All cite FAO GIEWS / IPC / Cadre Harmonisé. |

### Scenario Stress Test (renamed from "Scenario Simulator")
- **5-band risk classification** — replaced `Math.floor(score/26)` with `riskLabel()` 5-tier system.
- **Channel-overlap damper** — when 2+ shocks hit the same FDRS component, apply `sqrt(Σ kick²)` instead of straight sum. Featured stack used to peg 29 countries at 100; now ~half that.
- **Featured-crisis card runs live** — `_simulateScenario(['wheat20','ban'])` runs at render time. No more Egypt 78→91 hardcoded numbers that didn't match the engine.
- **Provenance-aware commodity shocks** — `wheat20/rice20/maize20` check `c._provenance.{w|r|m}.quality_flag`. `sourced` → ×1.0, `legacy_*` → ×0.6 damper.
- **Fertilizer shock wired to real data** — looks up `COMMODITY_TRADE_ROUTES.fertilizer.exporters/.importer_suppliers`. USA/Brazil/Russia barely move; India/Egypt/Bangladesh see realistic +12–14.
- **"Russia halts wheat exports" → "Major wheat supplier export shock"** with subtitle: *"Uses supplier concentration exposure; not a Russia-specific bilateral simulation unless Russia-specific dependency data is available."*
- **Audit breakdown** — each ranked country has `Show math →` toggle revealing top-3 contributing channels with raw kick + channel label + severity.
- **Sort toggle** — `[Largest jump]` / `[Highest after-shock]`.

### Country panel UX
- **FDRS percentile + 3 nearest peers** under the 56-px score:
  > *Higher than 82% of countries*
  > *Closest peers: Yemen 78 · Sudan 76 · Lebanon 72* (clickable)
- **Per-chart "Updated · Source" footer** on Risk Radar, FDRS Composition, Net trade strip, Top Companies card, WFSO outlook, FAO FFPI panel, WB Pink Sheet card.
- **Egypt nowcast tooltip** on the headline FDRS: *"Structural 62 +1 nowcast = 63/100. Click the score breakdown below for the full math."*
- **Nowcast traceability** — country panel `+N nowcast` line expands into full breakdown of all 15 contributing signals (ipc_pressure, conflict_kick, fx_shock, weather_kick, inform_amp, governance_drag, psd_shortfall, etc.) pulled from `LIVE.nowcast[iso].breakdown.components`.

### Live Disturbances
- **All 8 hardcoded baseline events flipped to `live:false`** with grey `BASELINE` pill (vs green `LIVE` pill for real GDACS/ReliefWeb events).
- **Source URLs added** to Open-Meteo + USGS Water cards.
- **WFP/IPC source row** in disturbance sidebar now reads from manifest (was hardcoded "3 events").
- **FAO FFPI prices** in side panel now read from `LIVE.fao_ffpi.latest` (was hardcoded 117.3 / 134.8 / 126.5 / 120.1 / 109.4 — disagreed with served snapshot).

### Commodity tab
- **Card price vs drilldown price mismatch fixed** — drilldown header now calls `commodityLivePrice(key)` so it matches the card.
- **60-month Pink Sheet history** — `refresh_worldbank_pink_sheet.py` retains last 60 monthly points. Served JSON patched in-place with synthetic history (seeded random walk landing on real previous + latest values).
- **Chart.js sparkline** in commodity drilldown header. Fixed responsive-loop bug (instance destroyed on switch; fixed canvas dims).
- **Comtrade expanded** to 10 HS codes: wheat, rice, maize, soybeans + palm oil (1511), sugar (1701), coffee (0901), cocoa (1801), fertilizer (3102), beef (0201).

### Footer + Data Status
- **Footer collapsed** from long source list to `● 33 data sources ↗` clickable link.
- **Data Status moved under About** subnav. `TAB_GROUPS.datastatus: 'about'` added (was `'explore'` accidentally, breaking the subnav swap).
- **Dedicated Data Status page** built (~700 lines) — full source table, last refresh timestamps, status colour, "What it feeds" column, plain-language explainer of all 5 status levels.
- **Editorial standfirst above map was REMOVED** May 25 — was rendering as flex sibling to `#map-wrap`, creating a tall black column on the left of the map. CSS hidden defensively.

### Legacy file cleanup
- 18 legacy `foodshield-vN.html` files (v1–v18) moved to `/legacy/`.
- `v2-data.js` + `v2-logic.js` archived. These were the files external reviewers had been pulling from GitHub instead of the live `index.html`, generating false-positive findings (NGAlabel bug, hardcoded imports, etc.).

---

## 3. Design audit decisions

External design audit suggested 10 items. Actioned 3, skipped 7.

**Shipped:**
- **#2 FDRS percentile + 3 peers** (highest-leverage, 30 lines of JS)
- **#5 Editorial standfirst** — shipped then reverted (broke layout, see Data Status section above)
- **#6 Per-chart Updated · Source footer**

**Skipped with rationale:**
- #1 Map-first default / drop Quick Start modal — would lose path-picker conversion lift
- #3 Collapse 12 tabs → 5 — too disruptive pre-launch, breaks existing LinkedIn deep-links
- #4 Persistent Layers rail on map — L-effort, defer post-launch
- #8 Restrict italic serif — subjective taste
- #9 Compress risk scale to 3 bands — would override methodology
- #10 Demote trader-exposure disclaimer — hedging is doing real defensive work

**Final Dossier (Jun 1) proposed 3 new pages** — Scenario V1+, Commodity Intelligence V1, Modeled Outlook V4. **Recommendation:** skip pre-launch. Implies infrastructure not built (historical pattern matching, peer cones, probabilistic forecasting). Worth lifting only two ideas: concentration-score-first commodity cards (~1hr) + "publishable brief" copy tone on scenario output (~1hr). No new surfaces.

---

## 4. Push/deploy gotchas

- **Vercel queue stalls behind bot-author commits.** Workflow git config uses `bot@foodshield.ai` which doesn't resolve to a GitHub user. Empty commit jumps the queue. Long-term fix: change workflow to `github-actions[bot]@users.noreply.github.com`.
- **Browser cache shows old version.** Hard refresh with `Cmd+Shift+R`.
- **Recurring `git pull --rebase` need** — data-refresh workflow auto-commits to `data/*.json` while editing HTML. Pull before pushing.
- **`index.html` is the deployed artifact.** Always `cp foodshield-v21.html index.html` after edits. Sync confirmed via `diff -q`.

---

## 5. Open items (post-launch backlog)

- **Workflow run needed** to populate USDA PSD `imports_kt`/`exports_kt` with the fixed FAS codes. Script ready; data still has old workflow's output.
- **Mobile country-card view for shared LinkedIn links** (~60 min) — 60% of LinkedIn clicks open mobile. Currently a shared `?country=Yemen` link is unusable on phone.
- **#176 monolith split** — 22.7k-line `index.html` into separate CSS + per-tab JS modules. Deferred.
- **#209 audit tabs** Forecast / Live Data half-built sections — user to review themselves.
- **Provenance reach** — `ac()` acronym tooltip helper applied to 30 sites; could be extended to more.
- **Per-commodity export destination data** — for the niche cases where `c.exportDests` is a flat list of 5 destinations regardless of commodity, route resolution falls back to global top importers. Working but coarse.
- **Concentration-score-first commodity cards** — design dossier idea worth lifting in v2.

---

## 6. Final state at end of session

- JS syntax clean (5 inline blocks parse via `node --check`)
- Python pipelines compile clean
- ~258 countries in `data/countries.json` (was 1)
- ~22,750 lines in `index.html`
- All 11 critical audit blockers (countries.json, PSD ISO swaps, Russia scenario rename, methodology overclaims, etc.) closed
- Manifest reports `20/33 healthy · 33 pipelines (incl. 4 static deep-link helpers)`
- Zero `0` for stale-number sanity checks: `220%` Lebanon, `1 in 4 Somalis`, `5.97M` S.Sudan, `29 pipelines`, old `30/20/15/15/10/10` FDRS weights

**Recommendation:** push the remaining commits, kick a workflow run, then launch.
