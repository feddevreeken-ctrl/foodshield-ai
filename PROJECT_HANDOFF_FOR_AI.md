# FoodShield AI — Full Project Handoff (for the next AI)

_Written June 2026 as a complete briefing for an AI agent that will pick up this project and run a major data upgrade. Read this top to bottom before touching anything. It covers what the project is, how the research and data were obtained, how the AI build worked, how every calculation is determined, exactly where the files live, and how to make and ship changes safely._

---

## 0. TL;DR for the incoming agent

FoodShield AI is a free, single-page food-security dashboard that scores 193 countries + 50 US states on food-supply disruption risk (the **FDRS**, 0–100). It wires **33 public data pipelines** into one comparable score, refreshed every 6 hours, with every value badged for provenance.

- **Live:** https://foodshield-ai-fv.vercel.app (Vercel auto-deploys on push to `main`)
- **Repo:** https://github.com/feddevreeken-ctrl/foodshield-ai
- **Local project folder:** `/Users/fedde/Documents/Claudes Files/Projects/FoodSecurity AI`
- **Source of truth:** `index.html` (~22.7k lines, single-file app). `foodshield-v21.html` must be kept identical — copy one to the other after every change.
- **Owner:** Fedde Vreeken, International Economics & Business Economics, Erasmus School of Economics, Rotterdam.

**The one rule that governs everything: honesty over polish.** Every number is badged sourced / manual / modeled / curated / illustrative. The system degrades honestly — an empty feed becomes a low-confidence flag, never a fabricated score. The site shows 24/33 healthy, not a fake 33/33. Do not break this. It is the entire credibility proposition.

---

## 1. What the project is

One **Food Disruption Risk Score (FDRS)** per country: a 0–100 indicator where higher = more fragile food supply. It has two layers:

1. **Structural score** — a weighted composite of seven components (slow-moving, rebuilt on pipeline runs).
2. **Live nowcast** — a bounded delta (−10 / +35) from 15 live sub-signals (conflict, floods, prices, fires, etc.), recomputed daily.

The product is split into four tab groups: **Explore** (Map, Country, Trade Flows, Commodities, Companies), **Analyze** (Score, Scenario Stress Test, Modeled Outlook 2030), **Live** (Live Disturbances feed), **About** (Methodology, Data Status).

Positioning: the free, transparent, *structural-risk layer beneath* commodity/price work — synthesis and traceability, not forecasting. It is **not** calibrated for trade execution, humanitarian targeting, or sovereign-risk pricing (no back-test exists yet).

---

## 2. How the research and data were obtained

### 2.1 The research approach
The research was not "collect data" — it was **map the public food-data landscape source by source, grade each source, and translate economic concepts into measurable components.** Three steps:

1. **Map the sources.** Went through the public food-data ecosystem and worked out what each provider offers, how it updates, and how to access it. That produced the 33 pipelines.
2. **Grade each source, don't just ingest it.** A feed can be technically alive but return an empty payload, a changed schema, or partial geography. So the system reports per-source *health* rather than treating all 33 as equal.
3. **Translate domain concepts into numbers.** Supplier concentration uses the Herfindahl-Hirschman Index (the market-concentration measure from competition economics) on a country's top import partners. Import dependency = caloric import share. Production trend = 5-year CAGR of staple output. Each component is a domain concept mapped to a sourced number.

### 2.2 The 33 pipelines (by domain)
- **Trade & production:** UN Comtrade Plus (bilateral staples, 10 HS codes), USDA PSD (production/imports/exports), FAO Food Balance Sheets (caloric shares), FAOSTAT net food trade.
- **Prices & inflation:** FAO Food Price Index, World Bank Pink Sheet (60-month benchmark prices), Eurostat food HICP (EU monthly), FAOSTAT food CPI.
- **Crisis & conflict:** WFP HungerMap, WFP per-country, IPC Acute Food Insecurity, FEWS NET FDW, ReliefWeb (OCHA), ACLED.
- **Climate & water:** WRI Aqueduct 4.0, ND-GAIN, World Bank CCKP, Open-Meteo (weather), Open-Meteo Flood, USGS Water, NASA FIRMS.
- **Governance & development:** World Bank WGI (6 governance dims), LPI (logistics), WDI single + bulk, UNDP HDI, EU JRC INFORM Risk.
- **US & deep-link helpers:** Feeding America (Map the Meal Gap), FAO GIEWS briefs, WFP country reports, USDA GAIN reports.

### 2.3 How the company sourcing data was obtained
Traders (Cargill, ADM, Bunge, LDC, COFCO, Wilmar, Olam, JBS, Tyson, Nutrien, Yara, Viterra) **do not publish per-country sourcing volumes** — there is no customs dataset. So the Companies tab is a per-trader **research scaffold** in `data/companies/*.json`, built from each company's own disclosures (10-K / 20-F for listed players, ESG / sustainability / CDP for private ones). Four rules:
1. Cite a public source URL for every claim.
2. Grade evidence per commodity: **strong** (company publishes country %), **medium** (names a "major" origin, no %), **weak** (only a third-party analyst names it).
3. No inference from "it's a big producer."
4. Date-stamp every citation.

Current: **12 traders, 67 commodities, 248 cited country claims.** A company keeps its MODELED badge until its file is `research_status: complete`, then the frontend prefers the cited data with a SOURCED badge. Schema and rules are documented in `data/companies/README.md`.

### 2.4 Credential-gated sources (secrets live in GitHub Actions, never the browser)
ACLED (`ACLED_API_KEY` + `ACLED_EMAIL` — currently setup-required), FEWS NET FDW (`FEWS_API_TOKEN`, 12-hour JWT), UN Comtrade Plus (`API_KEY`), OpenAQ (key), NASA FIRMS (`MAP_KEY`), ReliefWeb (approved appname `vreeken-foodshield-7k3n`). An IPC API key would give a fallback independent of the (currently down) HungerMap.

---

## 3. How the AI build worked

This was a solo build with several AI tools, each used for a different job. **Every decision on scope, methodology, weights, and wording stayed with Fedde.** The models implemented and critiqued; they did not decide what the score should mean.

| Tool | Role |
|---|---|
| **Claude Code** | Primary build environment — the score engine, scenario stress-test, trade-flow rendering, country-panel UX, methodology copy, long debugging passes. |
| **ChatGPT** | Second-opinion code review, copy alternatives, challenge rounds on FDRS weighting and normalisation. |
| **Codex** | Mechanical refactoring across the 33 Python scripts — retry, backoff, provenance-tagging boilerplate. |
| **Gemini** | Adversarial reviewer on methodology and honesty — caught overclaims ("Forecast", "famine" wording, unsupported confidence intervals). |
| **Lovable** | Prototyped a Next.js / React version to test whether splitting the frontend would help (decided against for now). |
| **Groq / Llama** | High-throughput cleanup — country-name normalisation, ISO3 mismatches, source-page descriptions. |

**The hard part was honesty, not code.** AI tools fill empty feeds with plausible data, describe degraded pipelines as working, and round heuristics into forecasts by default. Much of the build was catching confident-but-wrong output and walking it back. This is *why* the provenance flag system exists. **Incoming agent: hold this same discipline. Never let a dishonest or unsourced number onto the page.**

**The build pattern was a check-fix-ship loop, not spec-to-code.** Real bugs caught by checking data against reality: USDA codes swapping Niger/Nigeria; an empty-string `.includes('')` match assigning the top supplier's share to every row (Egypt wheat read $15B instead of $4.2B); aid agencies (WFP, USAID) wrongly listed as wheat "suppliers"; France's map dot landing in Spain (centroid averaged in overseas territories). Fix at the source or render layer — **never hand-edit a country to look right.**

---

## 4. How the calculations are determined

### 4.1 The structural FDRS formula
```
FDRS (structural) =
    0.28 × Import Dependency
  + 0.18 × Supplier Concentration
  + 0.14 × Production Trend
  + 0.14 × Food Inflation
  + 0.09 × Climate Vulnerability
  + 0.09 × Conflict / Logistics
  + 0.08 × Supply-Chain Exposure
```
Weights sum to 1.00; output rounded to an integer, clipped to 0–100. **The weights are reasoned judgment calls, not regressed** — chosen by what Fedde researched and believes matters most, and explicitly open to being tweaked. There is no back-test, so the FDRS is a *structured exposure indicator, not a prediction.*

### 4.2 Component definitions (and their sources)
- **Import Dependency (28%)** — caloric import share (% of daily food energy from imports). 95%+ ≈ 100; self-sufficient ≈ 0. Source: FAO Food Balance Sheets. Caloric view, not trade-value.
- **Supplier Concentration (18%)** — HHI of the top 5 import partners. Single-source ≈ 100; diversified ≈ 0. Source: UN Comtrade + FAO.
- **Production Trend (14%)** — 5-year CAGR of wheat/rice/maize/soybean output (USDA PSD, 2018–2024). Negative growth raises risk.
- **Food Inflation (14%)** — source cascade: WFP HungerMap (crisis) → Eurostat HICP (EU monthly) → FAOSTAT food CPI (global annual) → World Bank headline CPI (fallback). >60% ≈ 100.
- **Climate Vulnerability (9%)** — ND-GAIN food vulnerability + WRI Aqueduct drought/riverine-flood risk + WB CCKP observed warming (recent-decade mean vs 1991–2000 baseline), blended **60% sourced / 40% heritage**. Falls back to heritage only where a specific feed is degraded (UI labels that case).
- **Conflict / Logistics (9%)** — EU JRC INFORM Risk (2026) + WB WGI (all 6 dims, −2.5..+2.5) + WB LPI + INFORM lack-of-coping-capacity, blended **60% sourced / 40% heritage**. ⚠️ INFORM carries internal weight ~0.45 inside this component — landing/altering it **shifts scores**.
- **Supply-Chain Exposure (8%)** — trade-volume-weighted import exposure across staples; catches large traded volumes a caloric view misses. Source: FBS + Comtrade + USDA PSD.

### 4.3 Worked example (Egypt)
```
0.28×85 + 0.18×72 + 0.14×55 + 0.14×38 + 0.09×58 + 0.09×42 + 0.08×30
= 23.80 + 12.96 + 7.70 + 5.32 + 5.22 + 3.78 + 2.40 = 61.18 → 61 structural
Headline 64 = 61 structural + 3 nowcast (recent price + regional IPC pressure)
```

### 4.4 The nowcast (live layer)
15 sub-signals computed per country, each capped at its own max, summed into a delta **bounded −10 / +35** so live noise can't overpower the structural baseline. Built by `scripts/build_nowcast.py`. The country panel breaks out the non-zero signals.

| Signal | Max | Signal | Max |
|---|---|---|---|
| IPC pressure | 0–12 | Flood | 0–3 |
| WFP consumption | 0–6 | INFORM amplifier | 0–3 |
| Conflict | 0–5 | PSD shortfall | 0–3 |
| Weather extremes | 0–4 | Global price | 0–2 |
| FX shock | 0–3 | Fire over cropland | 0–2 |
| Inflation shock | 0–3 | US water | 0–2 |
| Governance drag | 0–2 | Air quality | 0–1 |
| Humanitarian response | −2 to 0 | | |

**Confidence flag (the honesty mechanism):** each nowcast row gets `confidence: high | monitored | low | none`. `high` = backed by a core crisis feed (IPC/WFP) or Feeding America; `monitored` = live food-price feed only; `low` = secondary signals only; `none` = no live signal (a ~0 adjustment means *absence*, not confirmed calm). Do not revert to zero-filling missing signals.

### 4.5 Tiers, scenario engine, 2030 outlook
- **5 tiers:** Resilient 0–25, Exposed 26–50, Dependent 51–75, Vulnerable 76–88, Severe 89–100.
- **Scenario Stress Test:** what-if shocks (wheat-supplier disruption, rice export ban, fertilizer chokepoint, drought, etc.). Uses a **channel-overlap damper** — when multiple shocks hit one component, `√(Σ kick²)` instead of straight addition, so multi-shock runs don't peg every country at 100. **Provenance-aware:** shock on sourced data = full impact; on legacy/curated data = ×0.6.
- **Modeled Outlook 2030:** illustrative "if the trend continues," badged `CURATED baseline + MODELED live overlay`. Renamed from "Forecast" deliberately. The weakest surface — caveat heavily.

---

## 5. Architecture & where the files live

**Local root:** `/Users/fedde/Documents/Claudes Files/Projects/FoodSecurity AI`

```
index.html                  ← THE deployed app (Vercel serves this). ~22.7k lines, single file.
foodshield-v21.html         ← canonical source; MUST equal index.html (cp after every edit)
data/                       ← JSON snapshots, refreshed every 6h by GitHub Actions
  countries.json            ← canonical structural overlay + per-field provenance (~258–264 rows)
  nowcast.json              ← live per-country adjustment layer (~191 rows)
  source_manifest.json      ← per-source health/freshness — the AUTHORITY for health state
  country_caloric_shares.json ← FAOSTAT FBS shares that source the w/r/m fields
  companies.json + companies/*.json ← per-trader sourcing scaffold (+ README.md)
  <one json per pipeline>   ← usda_psd, comtrade_staples, fao_ffpi, worldbank_pink_sheet,
                              eurostat_food, inform_risk, aqueduct, cckp, wgi, lpi, hdi,
                              fews, ipc, wfp_*, reliefweb_alerts, acled, openmeteo*, usgs_water,
                              nasa_firms, openaq, ndgain, net_food_trade, wb_wfso, worldbank_*
scripts/                    ← 33 refresh_*.py (one per source) + builders + validators
  _common.py                ← shared retry / normalisation / provenance-tag helpers
  run_all.py                ← orchestrator: dispatches all refresh scripts (parallel where possible)
  build_countries_dataset.py ← assembles countries.json from pipeline outputs
  build_nowcast.py          ← merges 15 live signals → nowcast.json
  build_source_manifest.py  ← writes source_manifest.json (status/timestamp/use)
  build_companies.py        ← assembles companies/*.json → companies.json
  validate_data.py / validate_data_integrity.py ← QA (see remediation Phase 1.5)
  vendor_data/              ← committed bulk files, e.g. INFORM_Risk_2026_v072.xlsx
.github/                    ← GitHub Actions workflow (the 6-hourly refresh + deploy trigger)
legacy/                     ← archived old versions — DO NOT TOUCH or audit against these
foodshield/                 ← dead Next.js starter; gitignored, harmless, slated for deletion
```

**Data envelope format** (every `data/*.json`):
```json
{ "_meta": { "generated_at":"...", "source":"...", "notes":"...", "version":"v21" },
  "data": { ... } }
```
A file existing does **not** mean the source is healthy — `source_manifest.json` is the authority.

**Stack:** static single-file HTML + vanilla JS, D3.js (map + trade-flow globe), Chart.js (sparklines), CSS custom properties. Python data layer, GitHub Actions orchestration every 6h (00/06/12/18 UTC), Vercel static hosting with `/data/*` routing. No server, no database, no accounts.

**Project docs worth reading (all in the local root):**
- `START_HERE.md` — orientation map
- `HANDOFF_SESSION_JUNE2026.md` — most recent session record
- `CONVERSATION_LOG.md` — full build-sprint history with every bug fix
- `REMEDIATION_PLAN.md` — the ordered upgrade backlog (read this before the data upgrade — Section 7 below summarises it)
- `DATA_SOURCES_ROADMAP.md` — next sources to integrate
- `DEMO_SCRIPT.md` — Rabobank demo + tricky-question answers
- `FoodShield_AI_Technical_Brief.docx` / `FoodShieldAI Fedde Vreeken.docx` — the leave-behind brief
- `data/README.md`, `data/companies/README.md` — data + company schemas

---

## 6. How to make and ship changes safely

**Environment split:** the assistant sandbox cannot reliably reach the internet or run `git push`. So: **the AI writes/verifies code logic offline; Fedde runs live data fetches, file placement, and all git on his Mac.** Expect this division to continue.

**The safe commit sequence (use every time):**
```bash
cd "/Users/fedde/Documents/Claudes Files/Projects/FoodSecurity AI"
rm -f .git/index.lock                      # clear stale lock if git hangs
cp foodshield-v21.html index.html          # keep source ≡ deployed (if HTML changed)
diff -q foodshield-v21.html index.html     # confirm identical
# JS-syntax-check index.html inline <script> blocks BEFORE pushing (skip scripts 0–1: JSON-LD/module)
git add <files>
git commit -m "..."
git pull --rebase                          # the 6h bot edits data/*.json — always rebase first
git push
# on a data/*.json rebase conflict: keep YOUR version
#   git checkout --theirs data/<file> && git add data/ && git rebase --continue && git push
# verify data not empty after push:
git show HEAD:data/inform_risk.json | python3 -c "import sys,json;print(len(json.load(sys.stdin)['data']))"  # expect ~191
```
**Hard lesson:** a rebase resolved with `--ours` once took the bot's EMPTY data files and silently shipped blank INFORM/Aqueduct/CCKP/WGI. Always verify counts after a data push.

**Other gotchas:** `git config core.editor true` is set so rebases don't open vim. Vercel's queue can stall behind bot-author commits — an empty commit (`git commit --allow-empty -m "kick deploy"`) jumps it. Hard-refresh (Cmd+Shift+R) to bust browser cache. External reviewers once pulled stale `legacy/v2-*.js` from GitHub and filed false bugs — always ground analysis in the live `index.html`.

---

## 7. The data upgrade — start here

This project exists right now to be **upgraded with better/more data.** The full plan is in `REMEDIATION_PLAN.md`; the headline is that the structural layer is **~84% legacy** and **0 countries are net-trade-sourced**, so the upgrade's core goal is **raising the sourced ratio without breaking honesty.**

**Currently degraded/down (the upgrade targets):** WFP HungerMap + per-country, IPC (upstream 500/404 — outages, not our bug), FAOSTAT FBS + food CPI (mid auth migration), ND-GAIN (needs manual ZIP), net_food_trade (`net_food_trade.json` is empty — `refresh_net_food_trade.py` needs a probe/parse fix), ACLED (needs API key). Healthy as of June 2026: INFORM, Aqueduct, CCKP, WGI (all repointed to the World Bank Data360 mirror), plus the live market/weather feeds = 24/33.

**Sequencing the upgrade (from the remediation plan):**
1. **Correctness first (offline, fast):** fix duplicate FAOSTAT area-code keys (276 MDA/SDN, 251 VNM/ZMB — drops Moldova & Vietnam); de-dupe 5 inline country rows; reconcile the "193" coverage claim.
2. **Trust layer:** make `validate_data.py` hard-fail if any crisis-core feed (WFP/IPC) is empty; keep `build_nowcast.py` from zero-filling; add a real QA layer (`qa_checks.py`) — dup-ISO, coverage thresholds, freshness SLA, schema shape.
3. **Raise sourced ratio:** land/repair pipelines (net_food_trade, FAOSTAT once migrated, IPC fallback via key), **and ship the matching methodology/Data-Status copy in the same commit** — several `index.html` lines say "currently degraded / falls back to heritage" that become false once a feed is live. Recompute any worked FDRS examples that shift.
4. **Schema clarity (invasive, do after the above):** split the overloaded `w/r/m` fields into `diet_share_{w,r,m}` (caloric, FAOSTAT FBS) vs `import_dependency_{w,r,m}` (legacy meaning) — they're currently mixed, so cross-country scenario math isn't strictly comparable. Touches 100+ frontend read sites; stage per-surface.

**Two cautions specific to the upgrade:**
- **Landing INFORM or any conflict-component source shifts scores** (INFORM ~0.45 weight inside Conflict/Logistics). Expect and communicate score movement; recompute examples.
- **Any new source must arrive with:** a raw snapshot in `data/`, a documented parser in `scripts/`, `source_manifest.json` health rules, per-field provenance, and explicit UI labelling (sourced/manual/modeled/nowcasted). This is the non-negotiable pattern.

**Do NOT, without explicit sign-off:** change the FDRS weights (they're a deliberate, defensible choice), hand-edit country data to look better, externalize the inline COUNTRIES fallback (deliberate for instant load), or split the monolith (deliberate for safe single-file deploy).

---

## 8. The throughline (keep this even if you change everything else)

Honesty over polish. Badge everything. Show real source health, not a fake all-green. Degrade to low-confidence, never to a fabricated number. Fix at the source or render layer, never by hand-editing a country. Keep it free, transparent, and structural. That discipline is the product.
