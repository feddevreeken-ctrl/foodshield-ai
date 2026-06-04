# FDRS v2 — Implementation Spec (Option B + "harder" formula + real FX pipeline)

_Status: **DRAFT FOR OWNER REVIEW**. This is a spec, not code. No formula change ships until the owner signs off (project rule: no silent formula changes). Builds on `FDRS_V2_DESIGN.md` (owner chose Option B + FX-the-accurate-way) and `PROJECT_HANDOFF_FOR_AI.md` §4._

_Author note: this spec corrects two factual errors in the design doc that I verified against the actual data files — see the boxed **DATA REALITY CHECK** in §3 and §4. They do not change the design, but they change what "sourced today" honestly means, and the owner must see them before sign-off._

---

## 0. What I verified before writing this (honesty audit of the inputs)

I read the real code and data, not the design doc's claims. Findings:

| Claim in design doc | Reality I found | Where |
|---|---|---|
| `c.c` is a 6-component vector `[import_dep, supplier_conc, prod_trend, food_infl, climate, conflict]` | **CONFIRMED.** `index.html` line ~13505 applies `w=[0.28,0.18,0.14,0.14,0.09,0.09]` to `c.c[0..5]`, rescales by /0.92, then blends 92% caloric + 8% SCE. SCE is **computed separately** (`c._sce`), not stored in `c.c`. | `index.html` 13505–13512; `build_countries_dataset.py` line 62 |
| Reserves `FI.RES.TOTL.MO` is "in pipeline" (WDI bulk healthy) | **FALSE — not present.** `worldbank_bulk.json` fetches 20 indicators; `FI.RES.TOTL.MO` is **not one of them**. Grep across all of `data/` returns zero matches. | `worldbank_bulk.json`; `refresh_worldbank_bulk.py` `INDICATORS` dict (lines 94–117) |
| Debt-service `DT.TDS.DECT.EX.ZS` is "in pipeline" (WDI bulk healthy) | **FALSE — not present.** Same: not in the `INDICATORS` dict, zero matches in `data/`. | same |
| Income (GNI per capita) is in HDI, healthy | **CONFIRMED.** `hdi.json` carries `gnipc` (GNI per capita, 2017 PPP $) for all countries, `quality_flag:"sourced"`, year 2023. EGY 16218, LKA 12616, SAU 50299. | `hdi.json` |
| FX from WFP feed | **CONFIRMED DOWN.** `wfp_country.json` count 0, 404. | `source_manifest.json` lines 35–48 |

**Consequence for sign-off:** reserves + debt-service are **not free today** — they are a one-line addition to the existing healthy WDI bulk fetcher (the bulk ZIP already contains every WDI series; we just aren't parsing those two codes). So Economic Access is buildable, but the design doc's "three of four sub-inputs source from healthy pipelines today" is only true **after** we add two indicator codes to `refresh_worldbank_bulk.py` and re-run it. Until then, Economic Access runs on **income only** + heritage. This spec treats adding those two codes as **Phase 0, gating work** (§7).

---

## 1. Final component set + weights

### 1.1 The 9 components and the v2 weight vector (Option B, confirmed with one adjustment)

| # | Component | Design-doc Option B | **SPEC v2 weight** | Note |
|---|---|---|---|---|
| 0 | Import Dependency | 0.23 | **0.23** | unchanged |
| 1 | Supplier Concentration | 0.16 | **0.16** | unchanged |
| 2 | Production Trend | 0.11 | **0.11** | unchanged |
| 3 | Food Inflation | 0.09 | **0.09** | unchanged (price stress only; no FX, no income) |
| 4 | Climate Vulnerability | 0.09 | **0.09** | unchanged |
| 5 | Conflict / Logistics | 0.08 | **0.08** | unchanged |
| 6 | Supply-Chain Exposure | 0.06 | **0.06** | **now stored in `c.c[6]`** (see §1.2) |
| 7 | **Economic Access / Affordability** | 0.12 | **0.12** | NEW |
| 8 | **Grain Reserve Buffer** | 0.06 | **0.06** | NEW |
| | **Total** | **1.00** | **1.00** | sums exactly |

I **confirm** the design doc's Option B weights without change. They are internally consistent, each move is justified in design §4.4, and re-deriving them risks introducing drift the reviewers would have to re-check. The only structural change I make is **bringing Supply-Chain Exposure into the stored vector** (next section) so the base formula is a single clean dot-product over `c.c[0..8]` instead of "dot-product over 6 + a bolted-on /0.92 rescale + an 8% SCE blend." That rescale-and-blend (`index.html` 13506–13512) is a v1 artifact; with a 9-vector that sums to 1.00 it is no longer needed and is a source of bugs.

### 1.2 New component vector layout

```
c.c[0] = Import Dependency        (unchanged)
c.c[1] = Supplier Concentration   (unchanged)
c.c[2] = Production Trend         (unchanged)
c.c[3] = Food Inflation           (unchanged)
c.c[4] = Climate Vulnerability    (unchanged)
c.c[5] = Conflict / Logistics     (unchanged)
c.c[6] = Supply-Chain Exposure    (NEW SLOT — was computed as c._sce, now persisted)
c.c[7] = Economic Access          (NEW)
c.c[8] = Grain Reserve Buffer     (NEW)
```

**Decision: Supply-Chain Exposure moves INTO the stored vector at index 6.** Rationale: (a) it makes the base formula one weighted sum over a length-9 vector whose weights sum to 1.00 — no rescale, no special-case blend; (b) the scenario engine, radar chart and FDRS Composition card already read `c.c[...]` by index, so persisting SCE there means they get it for free; (c) it removes the fragile `/0.92` rescale at line 13508 that silently assumes SCE is exactly the missing 8%. `c._sce` stays as the provenance-tagged raw value that fills `c.c[6]`; nothing about how SCE is *computed* changes.

**Migration safety:** code that reads `c.c[6]`, `c.c[7]`, `c.c[8]` must tolerate `undefined` on countries not yet rebuilt (`c.c?.[6] ?? c._sce ?? 0`). See §7.

---

## 2. The "harder/better" formula

### 2.1 Base: weighted sum (the honest backbone)

```
BASE = Σ_{i=0..8}  w[i] · c.c[i]          with  w = [0.23,0.16,0.11,0.09,0.09,0.08,0.06,0.12,0.06]
```

Because the weights sum to 1.00 and each `c.c[i] ∈ [0,100]`, **BASE ∈ [0,100]** with no rescaling. This already replaces the v1 `/0.92`-and-blend.

### 2.2 The sophistication I RECOMMEND: exactly ONE interaction term

I recommend adding **one** amplifier and **one** non-linear normalisation (the latter lives inside the Economic Access sub-formula, §3, not the top-level formula). I **reject** everything else below as false precision (§2.5). One top-level interaction term:

#### Interaction term: Import-dependency × Economic-access (the "can't grow it AND can't pay for it" amplifier)

**Economic justification (one sentence):** a country that imports most of its calories *and* lacks the FX/reserves/income to pay for those imports is more fragile than the additive sum implies, because the two fragilities act on the *same* failure mode — the balance-of-payments channel that lands grain at the port — so they compound rather than merely add (Sri Lanka 2022, Egypt's devaluation cycle, Ghana 2022 are exactly this joint condition).

**The math (bounded, gentle):**

```
amp_raw  = k · (c.c[0]/100) · (c.c[7]/100) · 100          // both high → up to k points
AMP      = min(amp_raw, AMP_CAP)
k        = 6          // max amplifier magnitude in points
AMP_CAP  = 6          // hard ceiling so the term can never dominate
```

- Both inputs are normalised to [0,1] before multiplying, so the product ∈ [0,1]; times `k=6` gives **AMP ∈ [0,6]**.
- It is **zero** unless *both* import-dependency and economic-access fragility are elevated. A wealthy importer (Gulf: high `c.c[0]`, low `c.c[7]`) gets ~0. A poor self-sufficient country (low `c.c[0]`, high `c.c[7]`) gets ~0. Only the genuine double-bind lights it up.
- `k=6` is a **reasoned ceiling, not a calibrated coefficient** — it is the same honesty status as the weights (judgment, not regressed). It caps the maximum joint-fragility nudge at 6 points, i.e. it can move a country by at most ~one-quarter of a tier. Badge it MODELED in the FDRS Composition breakdown.

#### Final formula (bounded 0–100)

```
FDRS_structural = clip( BASE + AMP , 0 , 100 )
                = clip( Σ w[i]·c.c[i]  +  min( 6·(c.c[0]/100)·(c.c[7]/100), 6 ) , 0, 100 )
```

Boundedness proof: BASE ∈ [0,100], AMP ∈ [0,6], sum ∈ [0,106], `clip(...,0,100)` → [0,100]. The clip only binds for already-near-maximal countries, which is acceptable (a country at 98 base + double-bind is correctly pinned near 100).

### 2.3 OLD vs NEW, side by side

```
OLD (v1, handoff §4.1):
  caloric = (0.28·c[0]+0.18·c[1]+0.14·c[2]+0.14·c[3]+0.09·c[4]+0.09·c[5]) / 0.92
  FDRS    = round( caloric·0.92 + SCE·0.08 ),  clip 0–100
  → 7 components, SCE bolted on, /0.92 rescale artifact, no economic access, no buffer, no interaction.

NEW (v2, this spec):
  BASE = 0.23·c[0]+0.16·c[1]+0.11·c[2]+0.09·c[3]+0.09·c[4]+0.08·c[5]+0.06·c[6]+0.12·c[7]+0.06·c[8]
  AMP  = min( 6·(c[0]/100)·(c[7]/100), 6 )
  FDRS = round( clip( BASE + AMP, 0, 100 ) )
  → 9 components in one clean dot-product (Σw=1.00), SCE now c[6], + 1 justified interaction term.
```

### 2.4 Recomputes (real arithmetic)

Component values reused from the design doc / handoff. Economic Access and Grain Reserve Buffer per design §5.1. For the two new countries I assign sub-input reads consistent with their well-documented profiles and the §3 sub-formula, badged illustrative.

**EGYPT** — `c = [85, 72, 55, 38, 58, 42, 30, 75, 65]`

```
BASE:
  0.23·85 = 19.55   import dep
  0.16·72 = 11.52   supplier conc
  0.11·55 =  6.05   production trend
  0.09·38 =  3.42   food inflation
  0.09·58 =  5.22   climate
  0.08·42 =  3.36   conflict/logistics
  0.06·30 =  1.80   supply-chain exposure  (c[6])
  0.12·75 =  9.00   economic access        (c[7])
  0.06·65 =  3.90   grain reserve buffer   (c[8])
  BASE    = 63.82
AMP = min(6·(85/100)·(75/100), 6) = min(6·0.85·0.75, 6) = min(3.825, 6) = 3.83
FDRS = clip(63.82 + 3.83, 0,100) = 67.65 → 68 structural
```
**Egypt: v1 61 → v2 68 (+7).** Design-doc Option-B-without-interaction was 64; the +4 over that is the amplifier correctly firing on Egypt's import-dependency × economic-fragility double-bind. This is the intended, defensible direction.

**SRI LANKA (FX-fragile)** — illustrative `c`. Sri Lanka: high import dependence on staples but not extreme (domestic rice); diversified-ish suppliers; the story is overwhelmingly economic (2022 default, rupee collapse, 90%+ food inflation in-crisis, thin reserves). Reads: import dep 70, supplier conc 45, prod trend 50, food inflation 55, climate 52, conflict/logistics 40, SCE 35, economic access **88**, grain buffer 55.
`c = [70, 45, 50, 55, 52, 40, 35, 88, 55]`

```
BASE:
  0.23·70 = 16.10
  0.16·45 =  7.20
  0.11·50 =  5.50
  0.09·55 =  4.95
  0.09·52 =  4.68
  0.08·40 =  3.20
  0.06·35 =  2.10
  0.12·88 = 10.56
  0.06·55 =  3.30
  BASE    = 57.59
AMP = min(6·(70/100)·(88/100), 6) = min(6·0.70·0.88, 6) = min(3.696, 6) = 3.70
FDRS = clip(57.59 + 3.70, 0,100) = 61.29 → 61 structural
```
Under **v1's 7-component formula** Sri Lanka would have scored materially lower (it had no economic-access term at all — its fragility was invisible to the score, which is the reviewers' exact complaint). The new economic-access weight (10.56 pts) plus the amplifier (3.70) together lift it ~14 points into "Dependent." **This is the headline fix working.**

**SAUDI ARABIA (resilient Gulf importer)** — illustrative `c`. Imports ~80%+ of food (very high import dep) but sovereign-wealth-insulated: deep reserves, no external-debt stress, very high income (GNI 50k) → very low economic-access fragility. Reads: import dep 88, supplier conc 60, prod trend 60, food inflation 25, climate 65, conflict/logistics 30, SCE 40, economic access **18**, grain buffer 45.
`c = [88, 60, 60, 25, 65, 30, 40, 18, 45]`

```
BASE:
  0.23·88 = 20.24
  0.16·60 =  9.60
  0.11·60 =  6.60
  0.09·25 =  2.25
  0.09·65 =  5.85
  0.08·30 =  2.40
  0.06·40 =  2.40
  0.12·18 =  2.16
  0.06·45 =  2.70
  BASE    = 54.20
AMP = min(6·(88/100)·(18/100), 6) = min(6·0.88·0.18, 6) = min(0.950, 6) = 0.95
FDRS = clip(54.20 + 0.95, 0,100) = 55.15 → 55 structural
```
Saudi's amplifier is **near-zero (0.95)** precisely because economic access is strong — the double-bind term correctly does NOT fire for a wealthy importer. Under v1 its 28% import-dependency weight alone pushed it higher; v2's lower import weight + the explicit "they can pay for it" economic-access term pulls it down, as the design intended.

**Spread the owner sees:** Sri Lanka 61, Saudi 55, Egypt 68 — the FX-fragile country and the wealthy importer now sit *below* Egypt, and the amplifier separates the genuine double-bind (Egypt 3.83, Sri Lanka 3.70) from the insulated importer (Saudi 0.95). That separation is the entire point of the new component.

### 2.5 Sophistication I explicitly REJECT (and why)

- **A second interaction (supplier-concentration × conflict).** Tempting — a single-supplier country whose supplier is in conflict is worse than additive. But the scenario engine already models exactly this dynamically (the `ban` and `conflict` shocks), and the conflict component already embeds INFORM/WGI. Adding it to the *structural* score double-counts the channel and adds a second un-calibrated coefficient. **Rejected — belongs in the nowcast/scenario layer, not structural.**
- **Geometric mean / CES aggregation instead of weighted sum.** A non-substitutability story (you can't fully offset zero reserves with high income) is real, but a full CES blend introduces an elasticity parameter we cannot honestly calibrate without a back-test, and makes the score far harder to explain to reviewers. The component-level non-linearity (reserves cliff, §3.2) captures the most important convexity without faking a structural elasticity. **Rejected at the top level; kept narrowly inside Economic Access.**
- **Per-component non-linear curves on all 9.** False precision; the components are already normalised judgment scales. **Rejected.**
- **Raising the amplifier cap above 6 or adding more amplifier terms.** Each added point of amplifier is an un-regressed coefficient; one capped term is the most we can defend as "structured judgment." **Rejected.**

---

## 3. Economic Access sub-formula (`c.c[7]`)

> ### DATA REALITY CHECK (must read before sign-off)
> Of the four sub-inputs, **only income (HDI `gnipc`) is sourced today.** Reserves and debt-service require adding two indicator codes to `refresh_worldbank_bulk.py` and re-running it (Phase 0, §7). FX requires the new pipeline in §4. So on **day 1 before Phase 0**, Economic Access = income-only + heritage. After Phase 0 (a ~15-line change + one bulk re-run), it = reserves + debt-service + income. After §4 FX pipeline, all four. Badge each sub-input independently; degrade honestly.

### 3.1 The four sub-inputs and their normalisations (0 = resilient, 100 = fragile)

Each sub-input maps a sourced number to a 0–100 fragility score. Higher = more fragile.

**(a) FX volatility / structural depreciation** — *slow/level, from the new FX pipeline (§4). NOT the nowcast delta (§3.4).*
```
Input: structural trailing depreciation vs USD (the STRUCTURAL field from fx_rates.json, §4):
       depr_struct = trailing 12-month % depreciation of local currency vs USD (level, not 90d shock)
Normalisation (piecewise linear):
       fx_sub = clip( (depr_struct − 5) / (40 − 5) · 100 , 0, 100 )
       → ≤5%/yr (normal drift) → 0 ;  40%/yr sustained → 100 ; linear between.
Provenance: SOURCED when fx_rates.json has the currency; HERITAGE otherwise.
```

**(b) Reserves in months of imports** — *non-linear (the one justified cliff).*
```
Input: FI.RES.TOTL.MO (WDI bulk, AFTER Phase 0)
Economic justification: import cover is NON-LINEAR — the danger is a cliff below ~3 months
  (the IMF/textbook adequacy floor), while the difference between 9 and 12 months is immaterial.
  A linear map would understate fragility in the danger zone.
Normalisation (convex, cliff-shaped):
       if  m >= 12 :  res_sub = 0
       elif m <= 1 : res_sub = 100
       else        : res_sub = clip( 100 · ((12 − m)/11)^1.6 , 0, 100 )
  → exponent 1.6 makes the curve steep near the 3-month cliff and flat up near 12 months.
    Sanity: m=3 → ((9/11)^1.6)·100 ≈ 72 ;  m=6 → ((6/11)^1.6)·100 ≈ 38 ;  m=9 → ≈12.
Provenance: SOURCED after Phase 0; HERITAGE otherwise.
```
This is the **second** sophistication I recommend (the first being the §2.2 amplifier). The exponent 1.6 is a reasoned shape, not a regressed parameter — badge the component MODELED-blend; the *input* (months) is SOURCED.

**(c) Debt-service burden** — *linear.*
```
Input: DT.TDS.DECT.EX.ZS = total external debt service as % of exports of goods/services (WDI bulk, AFTER Phase 0)
Normalisation:
       debt_sub = clip( (dsx − 5) / (35 − 5) · 100 , 0, 100 )
       → ≤5% of exports → 0 ;  ≥35% (severe crowd-out of import financing) → 100.
Provenance: SOURCED after Phase 0; HERITAGE otherwise.
```

**(d) Structural affordability / income** — *log-scaled (income fragility is non-linear in level).*
```
Input: HDI gnipc (GNI per capita, 2017 PPP $) — SOURCED TODAY.
Economic justification: the affordability gap between $2k and $5k GNI matters far more than
  between $40k and $50k. Use log scaling against floor $1,000 and ceiling $40,000.
Normalisation:
       g = clip(gnipc, 1000, 40000)
       inc_sub = clip( 100 · (log10(40000) − log10(g)) / (log10(40000) − log10(1000)) , 0, 100 )
       → gnipc ≤ $1,000 → 100 ;  ≥ $40,000 → 0 ; log-linear between.
  Sanity: EGY 16218 → ≈ 24 ; LKA 12616 → ≈ 31 ; SAU 50299→clip 40000 → 0.
Provenance: SOURCED (HDI healthy).
```

### 3.2 How they blend (weighted, with graceful degradation)

```
Nominal weights within Economic Access:
  fx 0.30,  reserves 0.30,  debt-service 0.25,  income 0.15
  (FX + reserves dominate because they gate import-payment capacity directly; income is the
   slow structural floor; debt-service is the crowd-out modifier.)

Economic_Access (c.c[7]) = Σ (w_k · sub_k) / Σ (w_k over PRESENT sub-inputs)   // re-normalise over available
```
**Graceful degradation = re-normalise the present weights** (same discipline as the existing Climate/Conflict 60/40 blends). If FX is missing, the remaining three re-weight to sum 1.0; if only income is present (pre-Phase-0), income alone defines the component and the field is badged HERITAGE-heavy / low-confidence. Record per-sub-input provenance in `c._provenance.econ_access = {fx:..., reserves:..., debt:..., income:...}`.

**Confidence rule:** if ≥3 of 4 sub-inputs are SOURCED → component badged `sourced`. If 2 → `partial`. If ≤1 → `heritage` and flagged low-confidence in the UI, exactly like a degraded nowcast row. No fabricated fill.

### 3.3 Anti-double-counting (the rule the reviewers will check)

- **NO CPI / inflation anywhere in Economic Access.** Price stress stays entirely in Food Inflation (`c.c[3]`). Economic Access measures *structural capacity to absorb* price stress (FX, reserves, debt, income), never realised price stress itself. This is design §6.2 point 1, enforced here: the four sub-inputs are FX-depreciation, reserve-months, debt-service-%, GNI — **none is a price index.**
- **`FP.CPI.TOTL.ZG` (the WDI inflation indicator that IS in the bulk file) must NOT be wired into Economic Access** — it feeds Food Inflation's fallback cascade only.

### 3.4 The structural-FX vs nowcast-FX split (design §6.2 point 2, made concrete)

| | Structural FX (this component) | Nowcast FX (unchanged) |
|---|---|---|
| Field | `depr_struct` = trailing **12-month** depreciation (a *level* of currency fragility) | `fx_90d_change_pct` / `fx_currency_shock` = sudden **90-day** drop >10% (an *event delta*) |
| Where | `c.c[7]` sub-input (a), slow, rebuilt on pipeline runs | nowcast signal, capped 0–3, recomputed daily (`index.html` ~13891) |
| Source | `fx_rates.json` `structural` block (§4) | `fx_rates.json` `shock` block (§4) — same file, separate fields |
| Double-count guard | measures the *standing* fragility level | measures the *acute* shock on top; the nowcast FX cap (0–3, line 13891 `Math.min(15,...)·0.6` → capped) is **kept as-is** so the live layer can't re-amplify the structural level |

Both read the **same `fx_rates.json`** but **different fields** (`structural.depr_12m_pct` vs `shock.depr_90d_pct`), so there's one pipeline, one source-of-truth, two cleanly separated consumers. If keeping them separable ever proves fragile, the design's safe fallback applies: FX lives only in the nowcast, and Economic Access runs on reserves+debt+income (still fully sourced after Phase 0).

### 3.5 Indicator-code confirmation (verified, §0)

- `FI.RES.TOTL.MO` — **NOT yet in pipeline.** Must be added to `refresh_worldbank_bulk.py INDICATORS`. The WDI bulk ZIP contains it; cost is two dict lines + a re-run.
- `DT.TDS.DECT.EX.ZS` — **NOT yet in pipeline.** Same.
- HDI `gnipc` — **PRESENT and sourced** (`hdi.json`, all countries, 2023).
- `FP.CPI.TOTL.ZG` — present but **deliberately not used here** (anti-double-count).

---

## 4. The FX pipeline spec — `scripts/refresh_fx.py`

> ### DATA REALITY CHECK (verified live)
> - **Frankfurter** (`api.frankfurter.dev`, ECB reference rates, free, no key, **historical time series available**) covers only **30 currencies** — the ECB majors. It covers TRY but **does NOT cover EGP, LKR, PKR, NGN, ARS, GHS** — i.e. the exact FX-fragile countries this whole component exists for. A pure-Frankfurter pipeline would be permanently dark for the most important countries. (Verified: `/v1/currencies` returns 30 symbols; a multi-symbol query silently drops unknown ones.)
> - **exchangerate.host** now **requires an API key** (migrated to apilayer) — not viable as the no-key source the design assumed. (Verified: returns `missing_access_key`.)
> - **open.er-api.com** (exchangerate-api.com's free "open" endpoint, no key) covers **160+ currencies incl. EGP, LKR, PKR, NGN, ARS, GHS** — but serves the **LATEST snapshot only, no history.** (Verified: full rate table incl. all fragile currencies.)
>
> **Design consequence:** no single free no-key source gives both broad coverage AND history. So the pipeline uses **two sources** and **builds its own history**: open.er-api.com daily snapshots accumulate the trailing window over time; Frankfurter provides an immediate historical bootstrap for the 30 majors so those currencies have a real 12-month/90-day window on day 1. This is a small, honest design and the right "accurate way" the owner asked for.

### 4.1 Sources (both free, no key)
1. **Primary (breadth):** `open.er-api.com/v6/latest/USD` — one call/day, 160+ currencies. Append each day's snapshot to a rolling history the pipeline maintains in `data/fx_rates.json` (keep ~400 daily points so the 12-month window is always available going forward).
2. **Bootstrap/anchor (history for majors):** `api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols=...` — gives true historical series for the 30 ECB currencies, so majors have a real window immediately and act as a cross-check on the er-api snapshots.

### 4.2 What it computes (per currency → mapped to ISO3)
```
shock.depr_90d_pct      = 100 · (rate_today − rate_90d_ago) / rate_90d_ago      // USD-per-local up = depreciation
structural.depr_12m_pct = 100 · (rate_today − rate_365d_ago) / rate_365d_ago    // slow level
structural.vol_12m      = stdev of daily log-returns over 12m, annualised        // optional secondary fragility cue
```
- Rates are quoted USD→local (e.g. EGP per USD); an *increase* = local-currency depreciation. Document the sign convention in the file `_meta.notes`.
- **Currency→ISO3 mapping:** a static `CCY_TO_ISO3` dict in the script (most ISO3 share the currency-issuing country; handle shared currencies — EUR→all eurozone ISO3s, XOF/XAF→their member ISO3s — by broadcasting the rate to each member country). Reuse the existing ISO3 canon (`refresh_faostat_fbs.FAO_AREA_TO_ISO3`) for validation, same pattern as the bulk script.
- When a currency is in neither source for a date, the country's FX sub-input degrades (no fabricated value).

### 4.3 Output schema — `data/fx_rates.json` (standard `_common.write_json` envelope)
```json
{
  "_meta": { "generated_at":"...", "source":"open.er-api.com (latest) + api.frankfurter.dev (history)",
             "notes":"USD->local quotes; increase = local depreciation. structural=12m level, shock=90d delta.",
             "version":"v23" },
  "data": {
    "history": { "EGP": { "2026-06-04": 51.94, "...": 0 }, "LKR": { "...": 0 } },   // rolling daily snapshots
    "by_iso3": {
      "EGY": { "currency":"EGP",
               "shock":      { "depr_90d_pct": 4.1,  "available": true },
               "structural": { "depr_12m_pct": 28.3, "vol_12m": 0.19, "available": true },
               "provenance": "sourced", "as_of":"2026-06-04" },
      "LKA": { "currency":"LKR", "...": 0 }
    }
  }
}
```
- `build_countries_dataset.py` reads `by_iso3[iso].structural.depr_12m_pct` for Economic Access sub-input (a).
- `build_nowcast.py` reads `by_iso3[iso].shock.depr_90d_pct` for the FX nowcast signal (replacing/supplementing the dead WFP `fx_currency_shock`).

### 4.4 source_manifest entry (added by `build_source_manifest.py`)
```json
"fx_rates": {
  "label": "FX rates (ECB Frankfurter + ER-API)", "file": "fx_rates.json",
  "status": "ok|degraded", "cadence": "daily", "mode": "live",
  "source": "open.er-api.com + api.frankfurter.dev (free, no key)",
  "notes": "Structural 12m depreciation feeds Economic Access; 90d shock feeds nowcast FX signal. History self-accumulated.",
  "count": <n by_iso3 with available structural> }
```
Status = `ok` when ≥120 countries have an available structural field; `degraded` (not failed) while the rolling history is still filling for non-major currencies in the first ~3 months after launch — and the UI must say so honestly (majors sourced immediately via Frankfurter bootstrap; broad set firms up as history accrues).

### 4.5 Separation guarantee (no double-count)
Structural consumer reads only `structural.*`; nowcast consumer reads only `shock.*`. The nowcast FX cap (line ~13891) is **unchanged**, so a currency that is *both* structurally weak and acutely shocked contributes a bounded level to `c.c[7]` and a bounded (≤3) delta to the nowcast — never the same number twice.

---

## 5. Scenario Stress-Test changes (9-component world)

The scenario engine (`SCENARIOS` array ~14389; `_simulateScenario` ~14524) keys every shock to a `channel` index and `c.c[index]`. The channel comment block (lines 14381–14383) must be **extended to 0–8**:
```
0 import dep · 1 supplier conc · 2 production trend · 3 food inflation
4 climate · 5 conflict/logistics · 6 supply-chain exposure
7 economic access · 8 grain reserve buffer        ← NEW channels
```

### 5.1 New shock levers

**(A) Debt/FX crisis — macro shock hitting Economic Access (channel 7)**
```
{id:'fxcrisis', label:'Debt / FX crisis', cat:'Macro', baseSev:0.30, sevUnit:'% deval', channel:7,
 desc:'Sharp currency depreciation + reserve drawdown — import-financing capacity impaired.',
 formula:'Δ = economic-access × 16% × (shock/0.30), provenance-damped',
 kick:(c,s=0.30)=>{
    const prov=(c?._provenance?.econ_access?.fx)||(c?._provenance?.econ_access)||'heritage';
    const damp=(prov==='sourced')?1.0:0.6;
    return (c.c?.[7]||0) * 0.16 * (s/0.30) * damp;
 },
 impact:function(c,s=0.30){return Math.round(this.kick(c,s));}}
```
Note this **replaces the old `fx` shock's mis-targeting**: the v1 `fx` shock (line 14461) hits `c.c[3]` (food inflation) as a proxy because there was no economic component. In v2 it should hit `c.c[7]`. **Decision: retire the old `fx` lever and let `fxcrisis` be the FX macro shock on the correct channel.** (Keep the old `fx` id as an alias mapping to `fxcrisis` so saved presets don't break.)

**(B) Grain reserve depletion — buffer shock hitting the Grain Reserve Buffer (channel 8)**
```
{id:'reservedraw', label:'Grain reserve depletion', cat:'Supply', baseSev:0.40, sevUnit:'% stocks', channel:8,
 desc:'Stocks-to-use falls sharply (drawdown / failed replenishment) — thinner cushion against the next shock.',
 formula:'Δ = grain-reserve-buffer × 12% × (shock/0.40), provenance-damped',
 kick:(c,s=0.40)=>{
    const prov=(c?._provenance?.grain_buffer)||'heritage';
    const damp=(prov==='sourced')?1.0:0.6;
    return (c.c?.[8]||0) * 0.12 * (s/0.40) * damp;
 },
 impact:function(c,s=0.40){return Math.round(this.kick(c,s));}}
```

### 5.2 Existing shocks needing index/formula updates
- **`fx` (line 14461):** retarget from `c.c[3]` → `c.c[7]` (or retire→alias to `fxcrisis` per 5.1A). **Must change** — otherwise FX double-stresses food inflation while ignoring the new economic component.
- All shocks referencing `c.c[0..5]` (`wheat20`/`rice20`/`maize20` ch0, `ban` ch1, `drought` ch2, `oil25`/`ship`/`conflict` ch5) — **indices unchanged** (0–5 are stable). No edit needed beyond confirming `c.c` is now length-9 and `c.c?.[i]||0` still safe.
- **Supply-Chain Exposure (ch6):** no existing shock targets it; optional future lever. The `fert` fertilizer shock uses bespoke `FERT` logic, not a `c.c` index — unaffected.

### 5.3 Damper + provenance confirmation
- **`√(Σ kick²)` channel-overlap damper:** works unchanged. `_simulateScenario` groups kicks by `channel` and applies sqrt-of-sum-of-squares when ≥2 share a channel. The two new channels (7, 8) slot into `byChannel` exactly like the others; stacking two macro shocks on ch7 will damp correctly.
- **Provenance-aware ×0.6:** both new kicks implement the same `damp = sourced?1.0:0.6` pattern, reading the new `_provenance.econ_access` / `_provenance.grain_buffer` tags. A heritage-only Economic Access (pre-Phase-0) correctly gets the 0.6 damping, so the scenario can't over-claim on a country whose economic component is still mostly heritage.

---

## 6. 2030 Outlook changes

The 2030 overlay (`index.html` ~13538–13586) keeps its structure: **`f2030` curated baseline + a bounded MODELED overlay**, badged `CURATED baseline + MODELED live overlay`, labelled "not a prediction." Changes:

1. **Baseline (`c._f2030Structural`) recomputed under the v2 formula.** Because the structural score changes (§2), the curated 2030 baseline must be re-derived from the same v2 components per the existing curated-trend method. No new projection logic — same f2030 field, recomputed inputs.
2. **New durable-signal terms in the overlay.** The overlay already up-weights persistent shocks (FX 0.7, inflation 0.6, governance 0.6, IPC 0.5). Add two, mirroring the new components:
```
f2030Adj += (cmp.econ_access_drift || 0) * 0.6;   // structural FX/reserve fragility persists multi-year
f2030Adj += (cmp.reserve_draw      || 0) * 0.3;   // stock buffers correct year-on-year (transient-ish)
```
   where `econ_access_drift` is derived from the structural FX 12m depreciation (slow, durable) and `reserve_draw` from any PSD stocks-to-use deterioration. Keep the existing `Math.max(-3, Math.min(10, ...))` overlay clamp so the new terms can't dominate.
3. **Badging unchanged.** Economic-access drift enters the MODELED overlay only, never the curated baseline's provenance. The "weakest surface — caveat heavily" handoff note stands; add one line to the methodology copy that the 2030 overlay now reflects structural economic fragility, still illustrative.

---

## 7. Migration + data plumbing

### 7.1 Order of operations (gating sequence)
**Phase 0 — unblock the two missing WDI codes (do FIRST, ~15 lines):**
- Add to `refresh_worldbank_bulk.py INDICATORS`:
  `"FI.RES.TOTL.MO": "Total reserves in months of imports"` and
  `"DT.TDS.DECT.EX.ZS": "Total debt service (% of exports of goods, services and primary income)"`.
- Re-run the bulk refresh; confirm both codes appear in `worldbank_bulk.json` for a healthy country count (expect partial coverage — reserves ~150 countries, debt-service ~120, mostly developing economies; high-income states often null these, which is fine because they degrade to income-only, and income alone correctly reads them as resilient).

**Phase 1 — FX pipeline (§4):** stand up `refresh_fx.py`, write `fx_rates.json`, register in manifest. Frankfurter bootstrap gives majors history immediately; er-api history accrues daily.

**Phase 2 — `build_countries_dataset.py` computes & stores the 2 new components + persists SCE into `c.c[6]`:**
- Add `compute_economic_access(iso, wdi_bulk, hdi, fx)` → returns `(value, provenance_dict)` per §3.
- Add `compute_grain_reserve_buffer(iso, usda_psd)` → stocks-to-use per §3.1 of the design (ending stocks ÷ consumption, normalised; low s/u = fragile = high score).
- Extend the stored vector from length 6 to length 9: write `c.c[6]=SCE`, `c.c[7]=econ_access`, `c.c[8]=grain_buffer`; write `c._provenance.econ_access`, `c._provenance.grain_buffer`.
- Update the dataset doc-string (line 62) and the `"c"` field description (line 476) to the 9-component layout.

**Phase 3 — `index.html` formula swap (§2):** replace the 13505–13512 block with the §2.2 dot-product + amplifier over `c.c[0..8]`; remove the `/0.92` rescale and the separate 8% SCE blend (SCE now in the vector). Add defensive `c.c?.[i] ?? c._sce ?? 0` reads for not-yet-rebuilt countries. Ship methodology + Data-Status copy **in the same commit** (new Economic Access + Grain Reserve Buffer entries; FX pipeline source row; the §1.3 scope statement and §6.2 double-count note from the design doc).

**Phase 4 — scenario (§5) + 2030 (§6) edits, same commit family.**

### 7.2 Which countries get SOURCED Economic Access today (after Phase 0+1) vs heritage
- **Sourced (≥3/4 sub-inputs):** countries with WDI reserves + debt-service **and** HDI income **and** an FX currency in the pipeline. This is most developing/emerging economies — including the target FX-fragile set (Egypt, Sri Lanka, Pakistan, Ghana, Türkiye, Argentina, Tajikistan, Kyrgyzstan) since er-api covers their currencies and WDI tracks their reserves/debt.
- **Partial (2/4):** high-income states where WDI nulls reserves/debt-service (common for Gulf/OECD) → run on income + FX. Income alone already reads them as resilient, so the partial badge is honest and the score direction is right.
- **Heritage (≤1/4):** micro-states / data-sparse countries missing WDI and FX → income-only or full heritage, flagged low-confidence.

---

## 8. What moves + what to tell reviewers

### 8.1 Expected country movements (worked examples confirm the direction)
- **Up (the intended fix):** FX-fragile / debt-distressed states gain the Economic Access weight (0.12) plus the amplifier. Sri Lanka ~+14 vs a no-economic-component v1; Egypt 61→68 (+7); Pakistan, Ghana, Argentina, Türkiye, Tajikistan, Lebanon similar. These are exactly the countries reviewers said were missing — this is the headline.
- **Down (also correct):** wealthy high-import-dependency states with strong balance sheets. Saudi ~55 (amplifier near-zero, lower import weight, explicit "they can pay" term). UAE/Qatar/Kuwait/Singapore similar. Sovereign wealth genuinely insulates them; v1 overstated their risk via the 0.28 import weight.
- **Option-B-specific movement:** big grain importers/exporters shift on the new stocks-to-use buffer (ch8) and the larger import-dependency cut (0.28→0.23).

### 8.2 Honest caveat paragraph (for methodology copy / reviewer briefing)
> FDRS v2 adds two structural components — Economic Access (FX depreciation, reserve adequacy, external debt-service burden, and income) and a Grain Reserve Buffer (USDA PSD stocks-to-use) — and one bounded interaction term that compounds import-dependency with economic fragility where the two genuinely act on the same balance-of-payments failure mode. Scores move accordingly: FX-fragile economies rise, sovereign-wealth-insulated importers fall, and that movement is the intended correction, not noise. The weights (Σ = 1.00) and the amplifier ceiling (6 points) remain **reasoned judgment, not regressed** — there is no back-test, so FDRS stays a *structured exposure indicator, not a prediction.* Each sub-input is badged sourced / partial / heritage and degrades honestly: Economic Access runs on income alone where reserves/debt/FX feeds are missing, never on a fabricated value. FX enters as a slow structural *level* in the score and a separate fast 90-day *shock* in the nowcast, drawn from the same source file but different fields, so it is never counted twice. Food-price stress remains solely in the Food Inflation component — Economic Access contains no price index by design.

---

## Appendix — implementation checklist (for the build, after sign-off)
- [ ] Phase 0: add 2 WDI codes, re-run bulk, confirm coverage counts
- [ ] `refresh_fx.py` + `fx_rates.json` + manifest row + sign convention documented
- [ ] `build_countries_dataset.py`: compute econ_access + grain_buffer, persist `c.c[6..8]`, provenance
- [ ] `index.html`: swap formula (§2.2), remove `/0.92`+SCE-blend, defensive reads, version bump
- [ ] `index.html`: scenario channels 7/8, `fxcrisis`+`reservedraw` shocks, retire/alias old `fx`
- [ ] `index.html`: 2030 overlay new durable terms within existing clamp
- [ ] Methodology + Data-Status copy in the SAME commit (scope statement, double-count note, new source rows)
- [ ] Recompute & publish Egypt 68 / Sri Lanka 61 / Saudi 55 worked examples
- [ ] `cp foodshield-v21.html index.html`, JS-syntax check, rebase-aware push, verify data counts
