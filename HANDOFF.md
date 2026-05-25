# FoodShield AI — Handoff brief

Paste this into a fresh chat to pick up state without re-loading the full conversation history.

## What this project is

A solo-built free food-security dashboard at **foodshield-ai-fv.vercel.app**. 33 public data pipelines (FAO, WFP, World Bank, USDA, NASA, ND-GAIN, INFORM, ACLED, IPC, ReliefWeb, Open-Meteo, USGS, OpenAQ, UN Comtrade, etc.) refreshed every 6h via GitHub Actions, distilled into one **Food Disruption Risk Score (FDRS)** per country, 0–100. Covers 193 countries + 50 US states.

Owner: **Fedde Vreeken**, International Economics & Business Economics at **Erasmus School of Economics** (Rotterdam).

## Current state

- **All audit blockers are closed.** Site is launch-ready.
- Pushed: data honesty fixes (PSD ISO swaps, countries.json builder, methodology overclaims), TFA arc bugs, scenario stress test honesty, 2030 outlook honesty, FDRS percentile + peers.
- **LinkedIn post draft is finalised.** See `LINKEDIN_POST_DRAFT.md`. Tag `@Erasmus School of Economics` + `@Erasmus University Rotterdam`. Hashtags `#FoodSecurity #SupplyChain #OpenData`.

## File structure

```
/Users/fedde/Documents/Claude/Projects/FoodSecurity AI/
├── index.html                    ← deployed file (Vercel serves this)
├── foodshield-v21.html           ← canonical source (must match index.html)
├── data/                         ← refreshed every 6h by GitHub Actions
│   ├── countries.json            ← structural baseline (258 countries)
│   ├── source_manifest.json      ← per-pipeline health
│   ├── nowcast.json              ← live FDRS overlay
│   ├── usda_psd.json
│   ├── comtrade_staples.json
│   ├── fao_ffpi.json
│   ├── worldbank_pink_sheet.json (60-month history per series)
│   ├── reliefweb_alerts.json
│   └── companies/*.json          ← per-trader sourcing footprints
├── scripts/
│   ├── refresh_*.py              ← 33 pipeline scripts
│   ├── build_countries_dataset.py
│   ├── build_source_manifest.py
│   ├── build_nowcast.py
│   ├── validate_data_integrity.py
│   └── run_all.py
├── legacy/                       ← archived old versions, DO NOT TOUCH
├── CONVERSATION_LOG.md           ← full compaction of the build sprint
├── HANDOFF.md                    ← this file
├── LINKEDIN_POST_DRAFT.md
└── SETUP.md / README.md
```

## Key facts to remember

1. **`foodshield-v21.html` is the source of truth.** Always `cp foodshield-v21.html index.html` after edits and verify `diff -q` is clean.
2. **JS syntax check before committing.** Pattern:
   ```bash
   node -e "const fs=require('fs');const html=fs.readFileSync('index.html','utf8');const scripts=[...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)];let ok=true;scripts.forEach((m,i)=>{if(i<2)return;try{new Function(m[1])}catch(e){ok=false;console.log('FAIL line',html.slice(0,m.index).split('\n').length,e.message.slice(0,200))}});console.log(ok?'OK':'FAIL');"
   ```
3. **JSON-LD + module scripts in the head DON'T parse as JS** — they're scripts 0 and 1, skip them in syntax checks. The 4 inline JS blocks are scripts 2–5.
4. **Workflow auto-commits.** Always `git pull --rebase` before `git push`. The bot touches `data/*.json` while you touch HTML — no real conflicts but the push gets rejected.
5. **Vercel queue can stall.** Bot author `bot@foodshield.ai` doesn't resolve to a GitHub user. Empty commit `git commit --allow-empty -m "kick deploy"` jumps the queue.
6. **External reviewers used to pull legacy `v2-data.js`/`v2-logic.js` from GitHub** and generate false findings. Those are now in `/legacy/`. Always ground audit responses in the live `index.html`, not old GitHub blobs.

## Core architecture decisions

### FDRS formula
Seven components, weights **28/18/14/14/9/9/8**:
1. Import Dependency (caloric) — 28%
2. Supplier Concentration — 18%
3. Production Trend — 14%
4. Food Inflation — 14%
5. Climate Vulnerability — 9% (60% sourced from ND-GAIN + Aqueduct + CCKP, 40% heritage)
6. Conflict / Logistics — 9% (60% sourced from INFORM + WGI + LPI)
7. Supply-Chain Exposure (trade) — 8% (modeled from FBS + Comtrade + PSD)

### Two FDRS values per country
- **Structural FDRS** — annual cadence, rebuilt when new vintages drop
- **Nowcast FDRS** — structural + Δ_nowcast (15 signals bounded ±35)
- Headline FDRS = nowcast. Country panel shows split + clickable per-signal breakdown.

### Provenance taxonomy (canonical 5 classes)
- **SOURCED** — fetched live from a public pipeline
- **MANUAL** — hand-maintained snapshot of an official release
- **MODELED** — computed at render time from sourced inputs
- **CURATED** — legacy heritage value awaiting re-derivation
- **ILLUSTRATIVE** — plausible example with no upstream data (e.g. company shares)

### Trade Flow Atlas data sources
1. Observed UN Comtrade staples (10 HS codes) — primary, badged `OBS · COMTRADE`
2. `COMMODITY_TRADE_ROUTES` constant (27 commodities × 436 importer→supplier mixes) — fallback, badged `MODELED`
3. Heritage `c.suppliers` / `c.exportDests` flat lists — last resort

Both directions handled by `commoditySuppliers(c, commodity)` and `commodityDestinations(c, commodity)` helpers. All 12 consuming surfaces use these.

### Scenario Stress Test
- Channel-overlap damper: when 2+ shocks hit the same FDRS component, apply `sqrt(Σ kick²)` instead of straight sum.
- Provenance-aware: `sourced` data → full impact; `legacy_*` data → ×0.6 damper.
- "Russia halts wheat exports" renamed → "Major wheat supplier export shock" (math is generic supplier concentration, not bilateral).
- Featured-card numbers run live from `_simulateScenario()` — no hardcoded deltas.

## Common audit objections + canned responses

| Objection | Truth |
|---|---|
| "v2-data.js shows hardcoded country array" | That's a legacy file in `/legacy/`. Live site reads `data/countries.json` + inline COUNTRIES as fallback. |
| "BOL maps to Belarus in PSD" | Fixed in `refresh_usda_psd.py` (FAS code `BO` = Belarus is canonical USDA). Served JSON patched in-place. Awaiting next workflow refresh for clean upstream regen. |
| "Egypt FDRS doesn't match formula" | Headline FDRS = Structural + Nowcast Δ. Country panel shows the split explicitly. Methodology has worked Egypt example. |
| "2030 forecast claims FAO-OECD baseline" | Methodology has been softened — explicitly says "illustrative scenario estimates, NOT official FAO-OECD/CCKP/ND-GAIN forecasts." Country panel chip says "CURATED baseline + MODELED live overlay". |
| "Confidence % / ± band looks like calibrated stats" | Removed from UI. Placeholder: "Confidence intervals require a backtest pipeline we haven't built yet." |
| "Famine count is wrong" | Renamed to "Hunger alerts" — was IPC 3+ areas, not Phase 5 famines. |

## What's still on the post-launch backlog

- **Workflow run** to populate USDA PSD `imports_kt`/`exports_kt` with corrected FAS codes
- **Mobile country-card view** for shared LinkedIn links (~60min build)
- **#176 monolith split** — 22.7k-line `index.html` → separate CSS + per-tab modules
- **Per-commodity export destinations** — currently coarse (flat `exportDests` list)
- **Backtest pipeline** for confidence intervals (currently removed from UI)
- **Concentration-score-first commodity cards** — design dossier idea, ~1hr

## Don't do

- Don't add forecast confidence bands or fan charts without a real backtest pipeline.
- Don't rebrand "Stress Test" back to "Simulator" (it's not decision-grade).
- Don't claim CCKP / ND-GAIN drive the climate component until those files have country coverage.
- Don't add "AI" prefix to feature names (Trade Flow Atlas, etc.) — math is deterministic.
- Don't break the 5-class provenance taxonomy.
- Don't show country shares ≥99% as fact when they're modeled — soften wording.

## Push checklist

```bash
cd "/Users/fedde/Documents/Claude/Projects/FoodSecurity AI"
# 1. Verify files are in sync
diff -q foodshield-v21.html index.html
# 2. Syntax check (see above)
# 3. Pull rebase before push
git pull --rebase
git add -A
git commit -m "..."
git push
```

If push rejected: workflow auto-committed. `git pull --rebase` then `git push`.
If Vercel queue stalled: `git commit --allow-empty -m "kick deploy" && git push`.

## Repo

`https://github.com/feddevreeken-ctrl/foodshield-ai`

## Live site

`https://foodshield-ai-fv.vercel.app/`
