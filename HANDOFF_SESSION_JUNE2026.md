# FoodShield AI — Session Handoff (June 2026)

_Complete record of the work done in this session, current state, and how to
continue. Read this first if you're resuming. Pairs with the repo's existing
`HANDOFF.md`, `REMEDIATION_PLAN.md`, and `DEMO_SCRIPT.md`._

---

## 0. CRITICAL CONTEXT FOR WHOEVER CONTINUES

**Project paths (the older memory/HANDOFF.md had the WRONG path — fixed):**
- Real project folder: `/Users/fedde/Documents/Claudes Files/Projects/FoodSecurity AI`
- GitHub: `https://github.com/feddevreeken-ctrl/foodshield-ai`
- Live (Vercel, auto-deploys on push to `main`): `https://foodshield-ai-fv.vercel.app`
- Source of truth file: `index.html` (must stay in sync with `foodshield-v21.html` — copy one to the other on every change)

**Environment constraints that shaped HOW work was done:**
- The assistant's sandbox **cannot reach the internet** (CDN/gov APIs blocked) and
  **cannot write to `scripts/` or run `git push`** reliably. So: the assistant
  writes/verifies code logic offline; **Fedde runs live fetches, git, and file
  placement on his Mac.** Expect this division to continue.
- The **data-refresh bot** commits `data/*.json` every 6h. Always `git pull --rebase`
  before pushing. On a `data/*.json` rebase conflict, **keep your version** —
  in a rebase use `git checkout --theirs data/<file>` (theirs = your commit),
  then `git add` + `git rebase --continue`. (We hit the ours/theirs confusion
  once and accidentally shipped empty data files — see lesson below.)
- A stale `.git/index.lock` recurs after interrupted commands — clear with
  `rm -f .git/index.lock`.
- `git config core.editor true` is set so rebases don't open vim and hang.

**Hard lesson learned this session:** a rebase conflict was resolved with
`--ours` and it took the bot's EMPTY data files, silently shipping blank
INFORM/Aqueduct/CCKP/WGI. Always verify after push:
`git show HEAD:data/inform_risk.json | python3 -c "import sys,json;print(len(json.load(sys.stdin)['data']))"`
should print ~191, not 0.

---

## 1. WHAT THIS SESSION SHIPPED (all pushed to main)

### Mobile + polish
- **Mobile-responsive layout** — additive `@media` queries (≤768/600px); desktop
  byte-for-byte unchanged. Map stacks, country panel becomes full-screen slide-over,
  compare caps at 2 on phones.
- **Favicon** — inline SVG shield (data-URI, no external file).

### Data pipelines: 4 dead feeds revived (the big win)
All were "degraded/empty"; now live & sourced. Scripts in `scripts/`:
- **INFORM** (`refresh_inform.py`) — JRC dropped the download URL, so we **commit
  the workbook** at `scripts/vendor_data/INFORM_Risk_2026_v072.xlsx` and parse
  locally → **191 countries**. To update yearly: drop new xlsx there, bump `LOCAL_FILE`.
- **Aqueduct** (`refresh_aqueduct.py`) — repointed to **World Bank Data360 mirror**
  (`DATABASE_ID=WRI_AQDT`). Mirror only has 2 of 5 indicators: drought + riverine
  flood risk (0-5 score). → **188 countries**.
- **CCKP** (`refresh_cckp.py`) — direct cckpapi returns empty; repointed to
  **Data360** (`WB_CCKP`, indicators `WB_CCKP_TAS`/`WB_CCKP_PR`). Now serves
  **observed** temp + warming (recent decade vs 1991-2000 baseline), NOT a 2050
  projection. Uses two narrow year-windows to stay under Data360's 10k-row cap.
  → **238 countries**.
- **WGI** (`refresh_wgi.py`) — WB v2 API archived the `.EST` codes; repointed to
  **Data360** (`WB_WGI`, codes `GOV_WGI_*`). KEY GOTCHA: filter `COMP_BREAKDOWN_1
  == "WGI_EST"` for the −2.5..+2.5 estimate (not WGI_SC score / WGI_SE error).
  Political Stability (PV) only publishes WGI_SC, so it's converted via
  `(score/100)*5-2.5`. → **216 countries, all 6 dims**.

### Data correctness bugs fixed
- **FAOSTAT duplicate area-codes** (`refresh_faostat.py`): code 276 was both
  Moldova & Sudan; 251 both Vietnam & Zambia. Fixed to canonical codes:
  Moldova=146, Sudan=276, Vietnam=237, Venezuela=236, Zambia=251. (277=South Sudan
  in the file confirmed 276=Sudan.) Moldova & Vietnam were being silently dropped.
- **5 duplicate country rows** removed from inline COUNTRIES (WSM/STP/TON/MHL/FSM
  each appeared twice). Now 264 unique entries. "193 countries + 50 US states"
  headline kept (per Fedde — the 21 extra are territories tracked but not in the claim).
- **Supplier shares unsorted** — 8 countries (incl. NLD, Egypt, US-CA) had supPct
  not in descending order, so the concentration donut/list rendered out of order.
  Fixed at **render time** (sort by pct desc) in index.html — fixes all 8 + future.

### Food inflation → sourced
- `build_countries_dataset.py` now wires Eurostat/WFP/FAOSTAT food inflation into
  the `fi` field with provenance. **31 countries sourced** (was 1), all EU + Sudan.

### Trust/integrity (the audit's core asks)
- **Nowcast confidence flag** (`build_nowcast.py`): each country gets
  `confidence: high | monitored | low | none`. `high` = backed by a core crisis
  feed (IPC/WFP) or US Feeding America; `monitored` = live food-price feed only
  (so NL/DE read correctly, not "no signal"); `low` = secondary signals only;
  `none` = no live signal (the ~0 adjustment reflects absence, NOT confirmed calm).
  Stops the old silent zero-fill that made sparse-data countries look calm+certain.
  `_meta.coverage` exposes counts + whether crisis feeds are live.
- **Frontend confidence pill** (index.html ~17105) folds nowcast confidence into
  the country-panel badge ("no live crisis feed" / "live signal provisional").
- **Validator** (`validate_data.py`): loud "CRISIS FEEDS EMPTY" report when
  WFP/IPC empty (kept non-blocking so the bot still commits healthy feeds).

### US states now have live data
- `build_nowcast.py` seeds US- rows from Feeding America (food insecurity %) +
  USGS water. All **50 states** now have real nowcast rows (were 0), high-confidence.

### Frontend reconciliation (field-shape changes from new pipelines)
- CCKP changed shape (`warming_c`, `hist_temp_c` instead of `proj_temp_change_c`);
  Aqueduct uses `.score` not `.cat`, only drought+flood. Updated EVERYWHERE these
  are read: FDRS climate blend, 2030 outlook, country cards (cckpCardHTML,
  aqueductCardHTML), live disturbances, data table. All "currently degraded /
  falls back to heritage" methodology + Data Status copy corrected to "sourced".

### Commodity design polish
- Card hover-lift + shadow, family-stripe brighten, tinted price-change chip.

### Source health
- `source_manifest.json` regenerated → **24/33 healthy** (was 20/33).

---

## 2. CURRENT STATE OF THE DATA (verified)

- 264 countries, all unique, FDRS all in 0-100, plausibility holds (NL 12-21,
  US 7, Yemen 79, S.Sudan 80; DR Congo 48 — see talking point below).
- Provenance: still ~84% legacy in `countries.json` BY DESIGN — the new climate/
  governance sourcing happens in the **render-time blend**, not baked into that
  file. Both states honestly labelled. This is correct, not a bug.
- **Still genuinely degraded (upstream issues, not our code):** WFP HungerMap &
  IPC (both return 500/404 from hungermapdata.org — upstream down), WFP per-country,
  FAOSTAT (mid auth migration), ND-GAIN (needs manual ZIP), net_food_trade (empty),
  ACLED (needs `ACLED_API_KEY` in GitHub secrets).

---

## 3. DELIVERABLES CREATED THIS SESSION (in project folder)

- `FoodShield_AI_Technical_Brief.docx` — updated to June 2026 / v1.3, 24/33,
  climate+governance as sourced, source table updated. (The leave-behind doc.)
- `DEMO_SCRIPT.md` — presentation structure + live demo flow + the two
  tricky-question answers (DR Congo 48, why 24/33).
- `DEMO_SCRIPT.md` is the companion to the deck.
- `FoodShield_Rabobank_Deck.pptx` — **15-slide** professional deck (dark
  forest + amber palette, Georgia/Calibri). Slides: 1 Title · 2 Who I am ·
  3 The Gap · 4 What it is (stats) · 5 FDRS methodology · 6 Trade Flow Atlas ·
  7 Commodities · 8 Scenario Stress Test · 9 Data pipeline · 10 Provenance ·
  11 Sourced & current · 12 Honest limitations · 13 How I used AI · 14 What I
  learnt · 15 The Ask. Built via `pptxgenjs`; build script in the outputs
  scratch dir (`build_deck.js`).
- `REMEDIATION_PLAN.md` — the ordered backlog (from an external audit, verified).

---

## 4. WHAT WAS IN PROGRESS / NEXT REQUESTS (NOT yet done)

Fedde's last requests, not yet built:
1. **Capture real screenshots** from the live site and embed into the deck
   (Trade Atlas / Egypt donut, Commodities cards, NL country panel, global map).
   Chrome MCP works on the vercel domain — earlier verified the live site renders.
2. **Deepen the FDRS + Trade Atlas slides** ("everything needs to be developed
   better" — more depth/explanation, not just the current summaries).
3. **Data-sources showcase slide** — "what exists, where I got data" — list the
   33 feeds and their providers as a credibility exhibit.
4. **Multi-AI usage** — Fedde said he used *multiple* AIs; the current slide 13
   says "AI" generically. He wants it to reflect different models for different
   jobs (coding vs review vs writing). NEEDS HIS SPECIFICS on which AI did what.
5. **Live disturbances / news feature slide** — the real-time event feed (GDACS,
   ReliefWeb, FEWS NET) that sits on top of structural scores and feeds the nowcast.
6. **Reference WFP HungerMap** (`hungermap.wfp.org/food?w=ipc-phase-3`) as a data
   source example, possibly screenshot it.

An `AskUserQuestion` was attempted to pin down (a) multi-AI specifics, (b)
disturbances emphasis, (c) HungerMap usage — but the tool call failed before
Fedde answered. **Resume by asking those three questions**, then build slides.

---

## 5. KEY TALKING POINTS FOR THE DEMO (rehearse)

- **"Why is DR Congo only 48 amid a hunger crisis?"** FDRS measures *structural
  trade-disruption exposure*; DRC grows much of its own food, so structurally
  less trade-exposed than Yemen. Acute crisis comes via the IPC/nowcast layer —
  which is upstream-down today, flagged honestly. Don't hand-edit the number.
- **"Why 24/33, not 33/33?"** Honest > fake-green. Some feeds are genuine upstream
  outages (WFP/IPC return server errors), one needs an API key (ACLED), FAOSTAT
  is mid-migration. System degrades honestly — empty feed → low-confidence flag,
  not a fabricated score.
- **Positioning (say early + late):** "the free, transparent, structural-risk
  layer *beneath* your price/forecast work — complementary, not a competitor."
- **Lead the demo on Commodities + Trade Flow Atlas** (their language). Don't
  lead with the 2030 forecast (they forecast for a living; it's your weakest
  surface — show only if asked, caveat heavily).

---

## 6. DURABLE FIXES FEDDE COULD DO (require his action, not code)

- Register a free **IPC API key** → set `IPC_API_KEY` in GitHub secrets, gives an
  IPC fallback independent of the down HungerMap.
- Register **ACLED** key (`ACLED_API_KEY` / `ACLED_EMAIL`) to light up conflict events.
- The first **scheduled GitHub Actions run** is the real test that the 4 repaired
  pipelines work from CI's IP (they worked from Fedde's Mac). Check the next run.

---

## 7. BACKLOG (from REMEDIATION_PLAN.md, post-demo)

- `w/r/m` schema split (caloric-share vs import-dependency overloaded) — invasive,
  touches 100+ read sites. Phase 3.
- Backtest pipeline for a defensible (not illustrative) 2030 forecast.
- Per-country OG images for LinkedIn reshares.
- Accessibility pass (map + nav ARIA/keyboard).
- Delete dead `/foodshield/` Next.js starter (already gitignored, harmless).
- Externalize the 334KB inline COUNTRIES blob — DECIDED AGAINST (conflicts with
  instant-load + single-source priorities; inline-as-fallback is deliberate).

---

## 8. GIT COMMIT SEQUENCE (the safe pattern, use every time)

```bash
cd "/Users/fedde/Documents/Claudes Files/Projects/FoodSecurity AI"
rm -f .git/index.lock
# (sync index.html -> foodshield-v21.html first if index changed)
git add <files>
git commit -m "..."
git pull --rebase
git push
# if data/*.json conflicts:  git checkout --theirs data/<file> && git add data/ && git rebase --continue && git push
# verify data not empty:  git show HEAD:data/inform_risk.json | python3 -c "import sys,json;print(len(json.load(sys.stdin)['data']))"
```

_End of handoff. Last commit this session: c246af0 (supplier-sort + commodity
polish). The nowcast confidence + US-states + monitored-tier work and the FAOSTAT/
duplicate-country fixes were pushed in commits before it (5df8388, 961af9f)._
