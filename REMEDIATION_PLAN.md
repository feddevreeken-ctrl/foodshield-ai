# FoodShield AI — Remediation & Improvement Plan

_Compiled 2026-06-01. Merges an external data/trust audit (verified against the
actual files) with the in-progress improvement roadmap. Ordered by
credibility-impact per unit of effort._

---

## Already shipped (live on Vercel)

- Mobile-responsive layout (additive media queries; desktop untouched)
- Favicon (inline SVG shield, matches header brand-mark)
- Food-inflation (`fi`) now sourced for **31 countries** (Eurostat + Sudan) instead of 1

---

## Verified audit findings (what's actually true)

| # | Finding | Verified? | Severity |
|---|---------|-----------|----------|
| A | `validate_data.py` passes even with 3 critical feeds empty (threshold `>=4`) | ✅ true | High — trust |
| B | Nowcast zero-fills missing signals → scores look more certain than they are | ✅ true | High — trust |
| C | Structural layer ~**84% legacy** (measured 83.7%); **0** net-trade-sourced countries | ✅ true | High — data quality |
| D | `w/r/m` overloaded: caloric-share for some countries, import-dependency for others | ✅ true (subtle) | Medium — comparability |
| E | Coverage misaligned: 264 rows (214+50), nowcast only 191 rows, **0 US states live** | ✅ true | Medium — honesty |
| F | Duplicate FAOSTAT keys `276` (MDA/SDN) and `251` (VNM/ZMB) → Moldova & Vietnam mis-mapped | ✅ **true — real bug** | High — correctness |
| G | 5 duplicate ISO rows in inline COUNTRIES (WSM/STP/TON/MHL/FSM) | ✅ true | Medium — correctness |
| H | "193 countries" claim doesn't match 214 non-state rows | ✅ true | Medium — honesty |
| I | No single source of truth (inline + overlay) | ⚠️ true but a deliberate tradeoff | Low — leave as-is |
| J | Dead Next.js starter app in `/foodshield/` | ✅ true, harmless | Low — cleanup |

---

## Phase 1 — Trust & correctness (do first; all testable offline)

Highest credibility return. Nothing here needs live APIs, so it can ship fast.

### 1.1 Make data health block "live" claims  _(audit #1, A+B)_
- `scripts/validate_data.py`: keep the `>=4` global threshold for transient outages,
  but add a **hard rule**: if ANY of `wfp_hungermap`, `wfp_country`, `ipc` is empty,
  fail the run (these are the crisis core — empty ≠ healthy).
- `scripts/build_nowcast.py`: stop converting missing signals to `0`. When a country's
  core crisis signals are absent, mark the row `confidence: "low"` and **suppress the
  numeric adjustment** (or carry it but flag it), rather than emitting a confident-looking
  number built from zeros.
- Frontend: where nowcast is shown, render low-confidence rows with an explicit
  "limited live data" treatment instead of a clean number.
- **Effort:** M. **Risk:** low (makes the site more honest, not less functional).

### 1.2 Fix duplicate FAOSTAT mapping keys  _(audit #5, F)_
- `scripts/refresh_faostat.py` line ~45 dict: `276` maps to both MDA and SDN; `251`
  to both VNM and ZMB. Second wins silently → **Moldova and Vietnam dropped/wrong**.
- Find the correct FAOSTAT area codes (MDA=276 is correct; SDN should be 276→ check,
  likely Sudan=276 is the dup to fix; VNM=251 vs ZMB=251 — Zambia is 251, Vietnam=237).
  Verify against FAOSTAT area-code list, assign correct unique codes.
- Add a dup-key guard test (see 1.5).
- **Effort:** S. **Risk:** low. **This is a real data bug — high value.**

### 1.3 Remove 5 duplicate country rows  _(audit, G)_
- Inline COUNTRIES has WSM, STP, TON, MHL, FSM twice (269 entries, 264 unique).
- De-dupe; verify count drops to 264 and no country renders twice.
- **Effort:** S. **Risk:** low.

### 1.4 Reconcile the coverage claim  _(audit #6, E+H)_
- Decide the honest number: 214 sovereign/territory rows vs advertised "193 countries".
  Either correct the headline copy, or define & trim to a defensible 193 UN members.
- Address US-state live coverage: nowcast has **0** states but UI implies live state
  coverage. Either generate state nowcast rows or change the copy to "structural only
  for US states."
- **Effort:** M. **Risk:** low (mostly copy + a count).

### 1.5 Add a real data-QA layer  _(audit #4)_
- New `scripts/qa_checks.py` (or extend `validate_data_integrity.py`): duplicate-ISO
  check, coverage thresholds per feed, freshness SLA (warn if a feed's `as_of` is older
  than its cadence), schema shape tests, and a semantic test ("if app claims live US
  state coverage, nowcast must contain US- rows").
- Wire into the GitHub Actions workflow after `run_all.py`.
- **Effort:** M. **Risk:** low.

---

## Phase 2 — Raise the sourced ratio (attacks the "84% legacy" finding)

This is the pipeline work already prepared. Each lifts real fields from legacy → sourced.

### 2.1 Land the 4 fixed pipelines
Scripts already written & probe-verified; need to be placed in `scripts/` and run locally
(your machine has internet; the sandbox does not).
- **INFORM** — from committed `scripts/vendor_data/INFORM_Risk_2026_v072.xlsx` → 191 countries.
  ⚠️ feeds the FDRS **conflict component (weight 0.45)** — landing it **shifts scores**.
- **Aqueduct** — WB Data360 mirror → flood + drought risk (2 of 5 indicators; the only
  stable source). ~190 countries.
- **CCKP** — fixed to the working *climatology* endpoint → temp + precip projections.
- **WGI** — query-form fix (mrnev, no date range); **may still need iteration** — the
  `.EST` codes were rejected even in catalog form during probing. Test first.
- **Effort:** M (mostly your local runs + my copy fixes). **Risk:** medium (score shifts).

### 2.2 Update methodology + Data Status copy to match  _(must ship WITH 2.1)_
- Lines that will become FALSE once pipelines are live:
  - `index.html` ~12897: CCKP "currently degraded"
  - ~18493, ~20257, ~22166, ~22179: "with both sources degraded, falls back to heritage"
  - ~13256: climate "today still partly heuristic"
- Recompute any worked FDRS examples that change.
- Remove/condition the "degraded" labels based on live manifest counts.
- **Effort:** M. **Risk:** medium — copy must match reality exactly or the site contradicts itself.

### 2.3 Wire `net` trade to a real source
- `net_food_trade.json` exists but is **empty (0 records)** — that's why 0 countries are
  net-sourced. Fix `refresh_net_food_trade.py` (likely same class of URL/parse drift).
- **Effort:** M. **Risk:** low. Needs a probe round like the others.

---

## Phase 3 — Schema clarity (the w/r/m fix)  _(audit #2, D)_

Real but invasive — touches 100+ frontend read sites. Do AFTER Phase 1–2 stabilize.

- Split the overloaded fields:
  - `diet_share_{w,r,m}` — caloric share (FAOSTAT FBS), sourced
  - `import_dependency_{w,r,m}` — legacy import-dependency meaning
- Scenario math (index.html ~14377) and the insights-rail label (~17719, "average wheat
  import dependency") must reference the correct field. Today they mix meanings, so
  cross-country scenario outputs aren't strictly comparable.
- Migrate `build_countries_dataset.py` to emit both; update frontend readers.
- **Effort:** L. **Risk:** medium-high (broad change) — stage carefully, test per-surface.

---

## Phase 4 — Reach & polish (lower urgency)

- **Per-country OG images** for LinkedIn reshares (`?country=Yemen` previews Yemen's card).
  Needs a small Vercel serverless function. Good ROI since LinkedIn is the traffic source. **M.**
- **Accessibility pass** — map + tab nav need ARIA/keyboard support; also helps SEO. **L.**
- **Delete dead Next.js scaffold** in `/foodshield/` (audit J) — pure cleanup. **S.**

---

## Explicitly NOT doing (decided against, with reason)

- **Full data externalization / move registry out of index.html** (audit #3 / I).
  Conflicts with the agreed priorities: instant country load on link-click + never showing
  a blank map. The inline-as-fallback model is a deliberate tradeoff, not an oversight.
  Revisit only if the single-file size becomes a real user problem.
- **Monolith split** (#176) — same reasoning; single-file deploy is simpler and safer
  while the site is freshly launched.
- **Backtest pipeline for confidence intervals** — large infra project; the UI already
  honestly says this doesn't exist yet. Post-launch.

---

## Suggested sequencing

1. **Phase 1.2 + 1.3** (FAOSTAT dup keys + duplicate countries) — fast, pure correctness, ship today.
2. **Phase 1.1 + 1.5** (health-blocks-live + QA layer) — the core trust fix.
3. **Phase 1.4** (coverage honesty) — ship with or right after #2.
4. **Phase 2** (pipelines + matching copy) — one coordinated commit; raises sourced %.
5. **Phase 3** (w/r/m schema split) — once the above is stable.
6. **Phase 4** (OG images, a11y, cleanup) — ongoing polish.

## Constraints to remember
- Sandbox can't write to `scripts/`/`data/` or reach the internet → live fetches & file
  placement happen on your Mac; Claude writes/verifies logic offline.
- `foodshield-v21.html` ≡ `index.html` — keep in sync; JS-syntax-check before every push.
- Data-refresh bot edits `data/*.json` → always `git pull --rebase` before push; on a
  `countries.json` conflict, keep your version (it's regenerated from source anyway).
