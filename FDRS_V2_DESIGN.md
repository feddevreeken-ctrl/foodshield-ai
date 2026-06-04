# FDRS v2 — Design Document: Adding an Economic Access Component

_Author: methodology working note. Status: DRAFT FOR SIGN-OFF. Owner decision required on §4 (which re-weighting option) and §6 (double-counting fix) before any code lands._

_Scope of this document: it specifies the **structural FDRS** only — the slow-moving weighted composite. The live nowcast layer (15 sub-signals, bounded −10/+35) is unchanged by this design, except where noted in §6 to avoid double-counting FX and inflation shocks that already live there._

---

## 0. Why this document exists

Professional reviewers (economists) flagged that the structural FDRS has **no explicit economic-risk factor**. Economic risk currently enters only *indirectly* — through Food Inflation (14%) and through governance proxies (WGI, INFORM) buried inside Conflict/Logistics. There is no affordability / purchasing-power term and no FX / sovereign-liquidity term, even though the per-country narrative cards reason about exactly this (Sri Lanka's FX collapse + 94% food inflation; Ghana's 2022 debt default; Egypt's serial devaluations; remittance-dependent Tajikistan). The model *knows* economics matters; the score doesn't count it.

This document proposes how to close that gap **without violating the project's honesty rules**: no claim of empirical calibration that doesn't exist; every factor maps to a sourced input actually in the pipeline (or a named, free, obtainable source); everything badged; the score remains a *structured exposure indicator, not a prediction*.

---

## 1. What FDRS measures, and the scope decision

### 1.1 The definition (sharpened)

> **FDRS is a 0–100 structured exposure indicator of how fragile a country's food supply is to disruption.** Higher = more fragile. It is a composite of sourced and curated structural inputs, refreshed on pipeline runs, with a bounded live nowcast on top. It is **not** a back-tested predictive model and is not calibrated for trade execution, humanitarian targeting, or sovereign-risk pricing.

### 1.2 The scope question reviewers are really asking

"Why no economic-risk component?" is two questions stacked:

1. **Supply vs access.** Food security has two classic pillars relevant here: *availability* (is the food physically there / reachable?) and *access* (can the population afford to buy it?). The current FDRS is almost purely an **availability/supply-disruption** index. Reviewers are pointing out that a country can have physically available food and still face a food crisis purely because its population can no longer *afford* it — Sri Lanka 2022 is the textbook case: shelves had calories; the rupee collapse and 90%+ food inflation put them out of reach.

2. **Does access belong in a "disruption risk" score?** This is the genuine design tension, and we should resolve it explicitly rather than dodge it.

### 1.3 The decision: yes, a bounded economic-access term belongs — here's the principled line

**Argument for inclusion.** A "disruption" to the food system is anything that sharply degrades a population's ability to obtain adequate food. An FX collapse or sovereign default that halts the country's ability to *pay for imports* is a supply-side disruption channel, not merely a downstream household outcome: a net food importer that can't access USD physically cannot land grain at the port. That is a disruption to availability transmitted through the balance of payments. This is the cleanest economic justification for putting **affordability-of-imports / FX-and-liquidity** inside a supply-disruption score.

**The line we draw.** We include the part of economic risk that gates the *country's capacity to keep food flowing* — FX volatility, reserve adequacy, debt-service crowd-out, and structural affordability (poverty/income). We deliberately do **not** try to absorb the full household-level access picture (consumption gaps, coping strategies, acute phase classifications) — that belongs to the IPC/WFP **crisis nowcast layer**, which is the right surface for fast-moving, household-outcome data. So:

- **Structural Economic Access component (this proposal):** the slow-moving, balance-of-payments and income side of affordability. *Can this country structurally pay for and its people structurally afford food?*
- **Nowcast (unchanged):** the fast household-outcome side. *Is there an acute affordability shock or consumption gap right now?* (IPC pressure, WFP consumption, FX shock, inflation shock signals already exist here.)

This split is defensible, honest about what the structural index does and doesn't capture, and directly answers the reviewers: **we are closing the gap on structural economic capacity, while keeping acute household access in the crisis layer where the live data lives.**

---

## 2. Proposed v2 component set

The v2 structural FDRS adds **one new component — Economic Access / Affordability** — built from a small basket of sourced inputs. The seven existing components are retained (their definitions and sources are unchanged from §4.2 of the handoff).

### 2.1 The new component: Economic Access / Affordability

Normalised 0–100 (higher = more fragile). Built as a weighted blend of up to four sub-inputs, each independently normalised, with graceful degradation when a feed is down (same heritage-fallback discipline as Climate and Conflict components).

| Sub-input | What it captures | Normalisation (high score = fragile) | Source — **already in pipeline?** | Source file |
|---|---|---|---|---|
| **FX volatility / shock vs USD** | Ability to keep paying for imports; currency-collapse risk | 90-day local-currency depreciation vs USD; >10% = elevated, scaled to ~100 at ≥40% | **In pipeline when up** — currently DOWN (404). WFP per-country feed publishes `fx_currency_shock` (90d change vs USD) | `data/wfp_country.json` (degraded) |
| **Reserves in months of imports** | Import-cover buffer; sovereign liquidity | <3 months ≈ fragile (→100), ≥12 months ≈ resilient (→0) | **In pipeline** — WDI indicator `FI.RES.TOTL.MO` | `data/worldbank_wdi.json` / `data/worldbank_bulk.json` (both healthy) |
| **Debt-service burden** | Crowd-out of import-financing capacity by external debt | Debt service as % of exports of goods/services (`DT.TDS.DECT.EX.ZS`); high = fragile | **In pipeline** — WDI bulk (20 indicators) | `data/worldbank_bulk.json` (healthy) |
| **Structural affordability / income** | Population's baseline ability to afford food | GNI per capita (HDI series) and/or HDI level; low income = fragile. Inverse-scaled | **In pipeline** — UNDP HDI (195 countries, healthy), incl. GNI per capita sub-series | `data/hdi.json` (healthy) |

**Sourcing verdict.** Three of the four sub-inputs source from **healthy** pipelines today (WDI/bulk, HDI). The fourth (FX) is in the pipeline but the WFP feed is down — so on day one the component runs on reserves + debt-service + income, and FX activates automatically when `wfp_country.json` recovers (or via the alternative free FX source named in §3.3). This means **the component is buildable now**, not blocked on the outage. Each sub-input is independently badged sourced/heritage exactly like the existing blended components.

### 2.2 The full v2 component list

1. Import Dependency
2. Supplier Concentration
3. Production Trend
4. Food Inflation
5. Climate Vulnerability
6. Conflict / Logistics
7. Supply-Chain Exposure
8. **Economic Access / Affordability** ← new
9. _(optional, ambitious only)_ **Grain Reserve Buffer** — see §3.1

---

## 3. Other defensible additions reviewers might expect

### 3.1 Grain reserve / stock buffer — **sourceable now**

USDA PSD (`data/usda_psd.json`, healthy, 151 countries) carries **ending stocks** and consumption per staple per marketing year. A **stocks-to-use ratio** (ending stocks ÷ domestic consumption) is the standard food-economics buffer measure: low stocks-to-use = thin cushion against a shock, the metric the USDA/IGC use to read tightness. This is genuinely sourceable today and is arguably a cleaner "disruption resilience" signal than some legacy components.

**Recommendation:** offer it as the optional 8th-vs-9th component in the *ambitious* re-weighting only. In the *conservative* option, hold it back to keep the change minimal. Note it partly overlaps Production Trend (both read PSD) — see double-counting, §6.

### 3.2 Strategic stockholding (national reserves) — **aspirational**

Some countries hold strategic grain reserves (China's massive state reserves; Egypt's GASC strategic wheat stock; India's FCI buffer). There is **no clean, comparable, free, machine-readable cross-country dataset** of strategic reserve volumes — disclosure is partial and political. Verdict: **aspirational, not sourceable now.** The PSD ending-stocks measure in §3.1 is the honest free proxy; do not claim to measure strategic reserves specifically.

### 3.3 Trade-policy / export-restriction exposure — **partly sourceable, mostly aspirational**

Export bans (India's 2022 wheat ban, 2023 rice ban; Russia's grain quotas) are a real disruption channel. The reference dataset is **IFPRI's Food & Fertilizer Trade Policy Tracker** (free, but a curated bulletin, not a clean API). There is no live machine feed in the pipeline today. Verdict: **aspirational** as a sourced component; could enter the **nowcast** later as a curated/manual event flag (like ReliefWeb alerts), not the structural score. Do not build it into v2 structural FDRS.

### 3.4 Note on the FX source if WFP stays down

If `wfp_country.json` does not recover, FX volatility is still freely obtainable from **exchangerate.host / the ECB reference-rate feed / Frankfurter API** (all free, no key) — compute 90-day depreciation vs USD per currency, map to ISO3. This is a small, well-scoped new pipeline and is the recommended fallback so the FX sub-input is never permanently dark.

---

## 4. Re-weighting proposal

Adding a component to a weight vector that sums to 1.00 forces existing weights to shrink. The honest framing for reviewers: **these weights are reasoned judgment, not regressed — there is no back-test — and the re-weighting below is a transparent reallocation, with each move justified, explicitly open to revision once a back-test exists.**

### 4.1 The old weights

| Component | OLD weight |
|---|---|
| Import Dependency | 0.28 |
| Supplier Concentration | 0.18 |
| Production Trend | 0.14 |
| Food Inflation | 0.14 |
| Climate Vulnerability | 0.09 |
| Conflict / Logistics | 0.09 |
| Supply-Chain Exposure | 0.08 |
| **Total** | **1.00** |

### 4.2 Where the room comes from — the core tradeoff

Economic Access partly *overlaps two existing terms*: Food Inflation (price side of affordability) and Conflict/Logistics (which already embeds WGI governance, a weak economic-stability proxy). So the cleanest place to find room is to **trim Food Inflation** (because affordability is now represented more completely and we must avoid double-counting price stress — see §6) and to take a little from the broad Import Dependency weight (which is doing a lot of work alone at 0.28 and arguably crowds out the other supply channels). We deliberately do **not** raid Climate or Supply-Chain Exposure — they are already the lightest and are correction terms, not padding.

### 4.3 Option A — CONSERVATIVE (minimal change: just add Economic Access)

Add Economic Access at a modest **0.10**, funded mostly by trimming Food Inflation (which now shares the affordability job) and shaving Import Dependency slightly.

| Component | OLD | **OPTION A** | One-line justification for the change |
|---|---|---|---|
| Import Dependency | 0.28 | **0.26** | Still the single largest channel; shave 0.02 to make room without dethroning it. |
| Supplier Concentration | 0.18 | **0.17** | Concentration risk unchanged in importance; nominal −0.01 for arithmetic. |
| Production Trend | 0.14 | **0.13** | Domestic output trend still material; −0.01. |
| Food Inflation | 0.14 | **0.10** | Biggest trim: price stress is now partly carried by Economic Access; cut to avoid double-counting affordability. |
| Climate Vulnerability | 0.09 | **0.09** | Untouched — already light, distinct channel. |
| Conflict / Logistics | 0.09 | **0.09** | Untouched — distinct channel (keeps WGI/INFORM weight stable; see handoff caution on INFORM ~0.45 internal weight). |
| Supply-Chain Exposure | 0.08 | **0.06** | Correction term; −0.02 is the least-cost trim. |
| **Economic Access / Affordability** | — | **0.10** | NEW. Structural capacity to pay for and afford food (reserves, debt service, income, FX). |
| **Total** | **1.00** | **1.00** | |

### 4.4 Option B — AMBITIOUS (fuller restructure: Economic Access + Grain Reserve Buffer)

Add Economic Access at a meaningful **0.12** and a **Grain Reserve Buffer** at **0.06** (from PSD stocks-to-use, §3.1), with a broader rebalancing that reduces Import Dependency's dominance and lets affordability and buffers carry real weight.

| Component | OLD | **OPTION B** | One-line justification for the change |
|---|---|---|---|
| Import Dependency | 0.28 | **0.23** | Reduce dominance; it overlaps Supply-Chain Exposure and the new reserve buffer. |
| Supplier Concentration | 0.18 | **0.16** | Still important; modest trim to fund affordability. |
| Production Trend | 0.14 | **0.11** | Some of its "domestic resilience" signal now sits in the explicit Grain Reserve Buffer. |
| Food Inflation | 0.14 | **0.09** | Trimmed hardest: price stress now shared with Economic Access; avoids double-counting. |
| Climate Vulnerability | 0.09 | **0.09** | Untouched. |
| Conflict / Logistics | 0.09 | **0.08** | −0.01; keep governance signal largely intact. |
| Supply-Chain Exposure | 0.08 | **0.06** | Correction term; trimmed. |
| **Economic Access / Affordability** | — | **0.12** | NEW. Larger weight: reviewers are economists; affordability is a primary channel. |
| **Grain Reserve Buffer** | — | **0.06** | NEW. Stocks-to-use from USDA PSD ending stocks; standard tightness/buffer measure. |
| **Total** | **1.00** | **1.00** | |

---

## 5. Recomputed Egypt worked example

The current doc gives Egypt = **61 structural** from these component values (0–100 each):
Import Dependency **85**, Supplier Concentration **72**, Production Trend **55**, Food Inflation **38**, Climate Vulnerability **58**, Conflict/Logistics **42**, Supply-Chain Exposure **30**.

### 5.1 Plausible Economic Access value for Egypt

Egypt is the textbook case for this component: serial EGP devaluations (the pound roughly halved against the USD across 2022–2024), reserves under pressure, a heavy external debt-service burden, and the world's largest wheat importer. On a 0–100 fragility scale this lands **high**. Sub-input read (illustrative, badged):

- FX shock (recent depreciation, large): ~85
- Reserves in months of imports (thin, low single digits): ~75
- Debt-service burden (% of exports, elevated): ~80
- Structural affordability (lower-middle income, GNI per capita modest): ~55

Equal-ish blend → **Economic Access ≈ 75** (illustrative; final value will come from the live blend with provenance badging). This is deliberately high and clearly defensible for Egypt.

For Option B we also need a **Grain Reserve Buffer** value. Egypt runs strategic wheat stocks but is structurally import-dependent with modest stocks-to-use on a national-production basis → fragile → **≈ 65**.

### 5.2 Option A recompute (Economic Access = 75)

```
0.26×85  = 22.10   Import Dependency
0.17×72  = 12.24   Supplier Concentration
0.13×55  =  7.15   Production Trend
0.10×38  =  3.80   Food Inflation
0.09×58  =  5.22   Climate Vulnerability
0.09×42  =  3.78   Conflict / Logistics
0.06×30  =  1.80   Supply-Chain Exposure
0.10×75  =  7.50   Economic Access  (NEW)
------------------------------------
Sum      = 63.59  → 64 structural
```

**Egypt under Option A: 61 → 64 structural** (+3). Adding the affordability channel raises Egypt, as expected for a country whose fragility is heavily economic. (The headline would then be 64 structural + nowcast.)

### 5.3 Option B recompute (Economic Access = 75, Grain Reserve Buffer = 65)

```
0.23×85  = 19.55   Import Dependency
0.16×72  = 11.52   Supplier Concentration
0.11×55  =  6.05   Production Trend
0.09×38  =  3.42   Food Inflation
0.09×58  =  5.22   Climate Vulnerability
0.08×42  =  3.36   Conflict / Logistics
0.06×30  =  1.80   Supply-Chain Exposure
0.12×75  =  9.00   Economic Access  (NEW)
0.06×65  =  3.90   Grain Reserve Buffer  (NEW)
------------------------------------
Sum      = 63.82  → 64 structural
```

**Egypt under Option B: 61 → 64 structural** (+3). Both options move Egypt to ~64; Option B redistributes more across channels but lands the same headline for Egypt because its high affordability fragility and high import dependency reinforce each other. The options diverge more sharply for **other** country profiles (see §6.1).

---

## 6. Risks

### 6.1 Which countries move most

The re-weighting reshuffles every country, but the *largest movers* are those whose old score was dominated by the trimmed terms or who score very differently on Economic Access than on supply:

- **Up the most:** countries with sound physical supply but fragile economics — FX-collapse / debt-distress states. Sri Lanka, Ghana, Egypt, Pakistan, Argentina, Türkiye, Tajikistan, Kyrgyzstan, Lebanon. These are exactly the countries reviewers cited as missing from the score — this is the *intended* movement and the headline selling point.
- **Down slightly:** high-income, high-import-dependency states with strong balance sheets — the **Gulf** (Saudi Arabia, UAE, Qatar, Kuwait), Singapore, Hong Kong. They import nearly all food (high Import Dependency, which we trimmed) but have deep reserves and high income → low Economic Access fragility pulls their composite down. This is also *correct*: sovereign wealth genuinely insulates them, which the old score overstated as risk.
- **Option B specifically** moves big grain importers/exporters more, via the stocks-to-use buffer term and the larger Import Dependency cut.

**Tell reviewers:** score movement is expected and is the point — recompute and publish the worked examples (Egypt above; add Sri Lanka and a Gulf state as the two clearest illustrations of the new component working in both directions).

### 6.2 Double-counting — the real methodological hazard

Two overlaps must be handled explicitly or reviewers will (rightly) flag them:

1. **Food Inflation appears both as its own component AND as a driver of affordability.** If Economic Access also ingested food CPI, price stress would be counted twice. **Fix:** Economic Access is built from **FX, reserves, debt-service, and income only — it must NOT include any CPI/inflation sub-input.** Food Inflation stays the dedicated price-stress term (now trimmed to 0.10/0.09 partly *because* affordability is better represented elsewhere). This keeps the two components measuring distinct things: Food Inflation = realised price pressure; Economic Access = structural capacity to absorb it.

2. **FX and inflation already exist as nowcast signals** (the live layer has `FX shock` 0–3 and `Inflation shock` 0–3). Putting FX into the *structural* Economic Access component risks counting FX in both the baseline and the live delta. **Fix:** keep the structural Economic Access FX sub-input as a **slow, level/volatility measure** (e.g. trailing-window depreciation as a structural fragility level), while the nowcast FX signal stays a **fast, event-style delta** (a sudden >10% 90-day drop). They measure level vs shock. Document this split in the methodology copy, and cap the nowcast FX signal as today so the live layer can't double-amplify. If keeping them cleanly separable proves hard, the safe fallback is: **FX lives only in the nowcast, and structural Economic Access uses reserves + debt-service + income** (still a strong, fully-sourced component from healthy feeds).

3. **Grain Reserve Buffer (Option B) overlaps Production Trend** — both read USDA PSD. **Fix:** Production Trend uses *output CAGR*; the buffer uses *stocks-to-use ratio*. Distinct quantities from the same source; acceptable, but it's why Production Trend is trimmed in Option B and why the buffer carries only 0.06.

### 6.3 What to tell reviewers (the honest caveats)

- The new component is **sourced** (WDI reserves + debt-service, HDI income; FX when the WFP feed or the ECB fallback is live), badged per sub-input, and degrades to heritage honestly.
- The weights remain **reasoned judgment, not regressed**; the re-weighting is a transparent reallocation, not a recalibration, and the standing next step is a back-test.
- The score stays a **structured exposure indicator, not a prediction**, and is now explicitly **supply-disruption + structural economic capacity**, with acute household access remaining in the crisis nowcast layer.

---

## 7. Recommendation and phased implementation

### 7.1 Recommendation: **Option A (conservative) for v2.0, with Option B held as v2.1.**

Reasoning: Option A directly answers the reviewers (an explicit, sourced Economic Access component at a credible 0.10 weight) with the **smallest blast radius** — one new component, three of four sub-inputs sourced from already-healthy feeds, minimal disturbance to the defensible existing weights, and a clean double-counting story (FX/inflation handled per §6.2). It ships now. Option B's Grain Reserve Buffer is a good idea but adds a second new component, a second double-counting surface (PSD overlap), and a broader re-weight that needs more validation — better as a deliberate v2.1 once Economic Access is live and the team has seen how scores actually move. Recommending the ambitious restructure *and* the new affordability term in one release multiplies the things that can be wrong simultaneously.

### 7.2 Phased implementation — what data must land first

**Phase 1 — build Economic Access on healthy feeds (no blockers):**
- Wire reserves-in-months (`FI.RES.TOTL.MO`, WDI), debt-service-% (`DT.TDS.DECT.EX.ZS`, WDI bulk), and income (GNI per capita / HDI level, `hdi.json`) into a normalised, provenance-badged Economic Access sub-score. Confirm the exact WDI indicator codes are present in `worldbank_bulk.json` before coding.
- Apply Option A weights; recompute and re-publish all worked examples (Egypt → 64; add Sri Lanka, a Gulf state).
- Ship the matching methodology / Data-Status copy in the **same commit** — including the §1.3 scope statement and the §6.2 double-counting note (the methodology page currently has no Economic Access entry to update; this is new copy).

**Phase 2 — light up FX:**
- If `wfp_country.json` recovers, ingest its `fx_currency_shock` field as the structural FX sub-input (slow/level framing per §6.2).
- If it stays down, stand up the small free FX pipeline (ECB / Frankfurter / exchangerate.host, no key) per §3.4.
- Re-confirm the nowcast FX cap so the live and structural FX terms don't double-amplify.

**Phase 3 — (v2.1, optional) Grain Reserve Buffer:**
- Add stocks-to-use from USDA PSD ending stocks, move to Option B weights, recompute examples again, publish.

**Hard rule carried over from the handoff:** any new source arrives with a raw snapshot in `data/`, a documented parser in `scripts/`, `source_manifest.json` health rules, per-field provenance, and explicit UI badging. No unsourced or unlabelled number reaches the page.
