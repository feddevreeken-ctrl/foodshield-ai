# Reviewer feedback — what's wrong, why, and how to fix it

_Written June 4 2026 after a full pass over the live files. The reviewers raised
six things: (1) make sure sourced data is accurate and recent, (2) get real
granularity between countries, (3) accuracy generally, (4) justify Supply-Chain
Exposure in the FDRS, (5) justify the weighted components ("mediators"), (6) say
why there's no economic-risk component. They named company data and per-country
import/export trade numbers as the worst offenders. This is the verified diagnosis
plus a sequenced fix. Nothing here has been pushed — it's the plan to argue with
before any change._

---

## The short version

The reviewers are right, and the problem is narrower and more fixable than it
sounds. Two things are actually wrong on the data side, and they're the two they
named:

1. **Company sourcing data is modeled, not the cited research — and the badge
   doesn't always make that obvious.** Every one of the 12 trader files is still
   `research_status: partial` (or `scaffolded` / `historical_pre_merger`). None
   is `complete`. The frontend only swaps in the cited data once a file is
   `complete`, so what a reviewer sees on the Companies tab is the *modeled
   commodity-overlap*, not the 248 cited country claims sitting in
   `data/companies/*.json`. The cited files themselves are clean — every claim
   has a URL, every share is honestly `null`. The gap is that the good data isn't
   wired in, and the modeled stand-in can read as if it were sourced.

2. **The per-country trade lists (`imports`, `exports`, `suppliers`, `supPct`,
   the FDRS itself) are legacy-curated for ~84% of countries.** `countries.json`
   says so in its own metadata: most rows carry `quality_flag: legacy_curated`
   with the note "Needs source-by-source re-verification before it can be treated
   as observed trade." That is the granularity complaint in one sentence — the
   numbers that should separate one country from another are hand-authored
   estimates, not live customs/FBS values.

Two things they'd assume are wrong are actually fine, and worth defending:

- **The observed Comtrade trade values display correctly.** The raw field is
  mis-named `total_usd_m` but it holds raw USD, and the frontend already knows
  this — `_fmtMoneyUSD()` converts it to $B/$M, so Egypt wheat renders ~$4.19B,
  not $4.19 trillion. Confirmed in `index.html` (~25083, ~25192). The earlier
  Egypt $15B bug is fixed. Don't let a reviewer "correct" this back.
- **The provenance system is honest.** 24/33 feeds healthy, badged, degrade to
  low-confidence rather than zero-fill. That's the credibility asset; keep it.

The three methodology questions are legitimate and answerable. The economic-risk
one points at a real gap. Full answers in §4.

---

## 1. Company data — what's actually happening

**Finding.** `data/companies/README.md` states the rule plainly: the frontend
prefers the cited data only when `_meta.research_status == "complete"`, otherwise
it falls back to the modeled commodity-overlap and keeps the MODELED badge. The
status table at the bottom of that README lists all 12 companies — Cargill, ADM,
Bunge, Wilmar, Olam, LDC, COFCO, JBS, Tyson, Nutrien, Yara, Viterra. Not one is
`complete`. So the cited research — 67 commodities, 248 country claims — is built
but dormant.

**Why this reads as "wrong data."** A reviewer opens the Companies tab, sees
specific countries attached to Cargill or Bunge, and reasonably assumes those are
sourced. They're the modeled overlap. When the modeled guess disagrees with what
the reviewer knows from the company's own filings, it looks like an error. It
isn't a fabrication — it's an unlabeled approximation standing where sourced data
was promised.

**The cited files are good.** `cargill.json` is the model: Brazil/Argentina/US
soy, Indonesia/Malaysia palm, CIV/Ghana cocoa — each with an `evidence`
sentence, a `citation_url`, an `as_of` year, and `share_pct: null` because
Cargill doesn't publish volumes. That's exactly the discipline the project is
built on. The work is done; it just needs promoting and wiring.

**Fix.**
- Promote the strongest files (listed companies first — ADM, Bunge, Tyson,
  Wilmar, Olam, JBS, Nutrien, Yara have 10-K/20-F backing) to
  `research_status: complete` once each is re-verified against its latest filing.
- Until a file is complete, the modeled view must carry a visible MODELED /
  "illustrative commodity-overlap, not company-disclosed" label on the tab
  itself, not buried in a methodology page. The reviewer's wrong-data reaction is
  really a labelling failure.
- Re-pull each listed company's most recent annual report (several cite 2024;
  Bunge's is explicitly pre-Viterra-merger and now stale — Bunge closed the
  Viterra deal July 2025). Date-stamp the refresh.
- Decision needed from you: keep the modeled fallback at all, or show "company
  discloses no country detail for X" instead of a modeled guess? Honesty argues
  for the latter for any commodity with no cited evidence.

---

## 2. Trade-flow numbers + granularity — the 84%-legacy problem

**Finding.** `countries.json` `_meta` is candid: 264 countries, 174 with
FAOSTAT-FBS caloric shares, **0 net-trade-sourced** (`net_trade_countries: 0`).
The per-commodity lists — `imports`, `exports`, `exportDests`, `suppliers`,
`supPct` — are documented as "remain legacy_curated." Sampling confirms it:
Aruba's FDRS, its 6-component vector `c`, and its 2030 projection all carry
`quality_flag: legacy_curated` and the boilerplate "inherited from the embedded
May 2026 dataset, needs re-verification." The handoff's 83.7% legacy measurement
holds.

**Why this is the granularity complaint.** If most countries' structural numbers
are hand-authored estimates rather than per-country observed values, neighbouring
countries can end up with suspiciously similar or round numbers, and the score
stops *discriminating* — which is the whole point of a 0–100 index. Reviewers
felt the countries blur together because, structurally, many do.

**What's genuinely sourced today** (so we don't oversell the fix): food inflation
for 31 countries (Eurostat + Sudan), the render-time climate blend (Aqueduct 188,
CCKP 238) and governance blend (WGI 216, INFORM 191, LPI 182), USDA PSD
production/trade for 151, Comtrade top-5 suppliers for ~19–25 priority importers.
The legacy layer is the per-commodity dependency lists and the baseline structural
vector.

**The empty pipeline that would help most: `net_food_trade.json` is empty
(0 of 174).** `refresh_net_food_trade.py` is supposed to parse the FAOSTAT TCL
bulk zip into a net agri-food balance per country; it covered 0. That single fix
would give every country one genuinely sourced, comparable trade number (net
exporter vs importer in USD), which is exactly the kind of hard differentiator the
granularity feedback wants. It's the highest-leverage data fix on the board.

**Fix, in order of leverage.**
1. **Repair `refresh_net_food_trade.py`** — probe why the TCL parse returns 0
   (almost certainly the same URL/schema drift that hit the other FAOSTAT
   scripts). Lands one sourced trade number for ~174 countries.
2. **Widen Comtrade beyond the ~19–25 free-tier importers.** Each added importer
   replaces a legacy supplier list with observed bilateral flows. Quota-limited,
   so prioritise the countries reviewers will check (large importers, the demo
   set).
3. **Finish the FAOSTAT FBS caloric shares** once the auth migration clears
   (`faostat_food` is 403 right now) — raises sourced `w/r/m` coverage past 174.
4. **Re-verify the structural baseline country by country**, starting with the
   demo set and the largest economies, replacing `legacy_curated` with sourced +
   `as_of`. This is the slow, unglamorous core of answering "accuracy."
5. Each landed source ships **with** the matching methodology/Data-Status copy in
   the same commit (the plan already flags ~5 `index.html` lines that turn false
   once a feed is live), and any worked FDRS example that moves gets recomputed.

---

## 3. Recency

`source_manifest.json` is the authority and it's mostly current (most feeds
`age_days: 0`, generated 2026-06-01). The recency risks worth naming to a
reviewer:

- **Comtrade is HS6 year 2024** — fine, but say "2024" rather than implying live.
- **Feeding America is data-year 2023** (Map the Meal Gap 2025 release); next
  release late July 2026. Labelled MANUAL, correct.
- **LPI is 2023** (biennial; WB skipped 2020/2022). Correct, just old by nature.
- **The structural baseline is "2026-05" embedded** — which is the legacy problem,
  not a freshness problem. A recent timestamp on a hand-authored value isn't
  recency in the sense reviewers mean.
- **Genuinely stale-because-down:** WFP HungerMap, IPC, FAOSTAT food CPI,
  ND-GAIN, ACLED. Upstream outages or missing keys, already flagged honestly.

---

## 4. The three methodology questions

These are answerable and the answers strengthen the pitch. Each should land in the
methodology copy.

### 4a. Why is Supply-Chain Exposure in the FDRS?

Because caloric import-dependency alone misses *volume through chokepoints*. A
country can import a small caloric share but move enormous physical tonnage of one
staple through a single port or strait — that's disruption risk a caloric view
can't see. Supply-Chain Exposure (8%, the smallest weight) is the
trade-volume-weighted import exposure across staples, built from FBS + Comtrade +
USDA PSD. It's the component that catches "the calories look fine but the logistics
are a single point of failure." It's deliberately the lightest weight because it
partly overlaps Import Dependency and Supplier Concentration — it's a correction
term, not a primary driver. Defensible. Keep it, and say exactly this.

### 4b. The weighted components ("mediators") — why these seven, these weights?

The seven components are the channels through which a supply shock reaches a
country's plates: how much it must import (28%), how concentrated its suppliers are
(18%), whether its own production is trending down (14%), whether food prices are
already stressed (14%), climate exposure to its growing regions (9%),
conflict/logistics fragility (9%), and chokepoint volume exposure (8%). Each maps a
food-economics concept to a measurable number — Supplier Concentration is the
Herfindahl-Hirschman Index from competition economics applied to import partners;
Import Dependency is caloric import share; Production Trend is the 5-year CAGR of
staple output.

The honest framing — and the one to give reviewers — is that **the weights are
reasoned judgment, not regressed against outcomes.** There's no back-test yet, so
the FDRS is a *structured exposure indicator, not a validated predictive model.*
That's already the project's stated position and it's the right one. The strongest
answer to "why these weights" is: "here's the reasoning for each, they're explicitly
open to revision, and the next real upgrade is a back-test that would let the data
argue for different weights." Don't claim empirical calibration you don't have.

### 4c. Why no economic-risk component?

This is the sharpest question and it points at a real gap. Right now economic risk
enters only **indirectly** — through Food Inflation (14%) and through governance
proxies inside Conflict/Logistics (WGI, INFORM). There's no explicit
**affordability / purchasing-power** term and no **FX / sovereign-liquidity** term,
even though the per-country narrative notes are full of exactly that reasoning:
Sri Lanka's FX collapse and 94% food inflation, Ghana's debt default and recovery,
Egypt's currency devaluations, Gulf states where sovereign wealth makes
affordability a non-issue, remittance-dependent Tajikistan/Kyrgyzstan. The model
*knows* economics matters — it's written all over the country cards — but it isn't
in the score.

Two honest responses, and you should pick one before the reviewers push again:

- **Defend the scope:** FDRS measures *physical supply-disruption exposure*, and
  affordability is a downstream, household-level outcome better handled by the
  IPC/nowcast crisis layer than baked into a structural supply index. This is
  coherent — but it concedes the index is supply-side only, and you should say so
  plainly rather than implying it's comprehensive.
- **Close the gap (stronger):** add an explicit **Economic Access / Affordability**
  component — candidate inputs are FX volatility vs USD (already pulled in the WFP
  per-country feed when it's up), food CPI burden, and a sovereign-liquidity /
  debt-distress proxy (World Bank, IMF). This would also raise granularity, since
  FX and debt distress vary sharply country to country. The cost is re-weighting
  (the seven weights would have to make room) and recomputing examples. Given the
  reviewers are economists, building this is the answer most likely to win them
  over.

My recommendation: acknowledge the gap directly, and scope a v2 Economic Access
component rather than defend the omission. It improves the score *and* the
granularity *and* answers the reviewers in one move.

---

## 4d. Can government import/export data model the companies?

Short answer: **partly, and only for some commodities in some countries — and it
won't get you company-attributed numbers from free government sources.** Here's the
honest landscape, because this distinction is exactly where the reviewers will
push.

**The core problem: customs data is organised by country, not by company.** A
government's official trade statistics (UN Comtrade, US Census, Eurostat, Brazil
Comex Stat) tell you "Egypt imported $4.2B of wheat, 73% from Russia." They do
**not** tell you "Cargill handled X% of that." To attribute a flow to a named
trader you need **bill-of-lading / consignee-level records**, and those are a
different, mostly-commercial world:

- **The named-company datasets are Panjiva (S&P), ImportGenius, PIERS, Tendata,
  etc.** They reconstruct shipper/consignee names from manifests. They're paid,
  and licensing terms generally forbid republishing — so they don't fit a free,
  open project without a budget and a contract.
- **US vessel-manifest data is *not* freely downloadable.** CBP supplies it on
  paid CD-ROM, and — critically — importers/consignees can request **confidential
  treatment of their name at no cost**, which large traders routinely do. So even
  the paid US feed has the big agribusiness names partially redacted.
- **Brazil Comex Stat is genuinely free and has an API** (relaunched June 2024),
  but it publishes at **HS-code + state + country level, not company level.** The
  company-named Brazilian data you see advertised comes from commercial resellers
  layering SISCOMEX on top. EU country-level transaction data with importer names
  exists for a few states (Finland, Belgium) but is subscription-gated, and GDPR
  restricts reuse of company names.
- **India, Indonesia, and others** have company-level data only through
  commercial platforms, not free official portals.

**So what *can* you do with free government data, honestly?** Build a **modeled
exposure estimate**, clearly badged MODELED, by combining two free sources you
already pull:

1. **Country production/export capacity** (USDA PSD — already in the project, 151
   countries) tells you which countries are the big origins for each commodity.
2. **A company's disclosed operating footprint** (the cited `companies/*.json`
   files — where each trader says it operates: Cargill's Argentine crush plants,
   Bunge's Brazilian elevators, etc.).

Intersect them: "Company X discloses sourcing/processing assets in country Y;
country Y is a top-N exporter of commodity Z per USDA PSD; therefore X has
*plausible, modeled* exposure to Z-from-Y." That's a defensible, fully-sourced
*model* — every input is public and cited — and it's a real upgrade on a pure
guess. But it is still a model, and it must keep the MODELED badge. **No free
government source will let you claim "Cargill imported N tonnes from Russia" as
sourced.** Be explicit with the reviewers about that ceiling; claiming otherwise
is the exact overclaim the project's honesty rule exists to prevent.

**Where free government data genuinely sharpens things:** the *country-level*
trade flows (§2) — net food trade, bilateral Comtrade — are free, official, and
attributable. That's where to spend the effort. The company tab is best framed as
"disclosed footprint (cited) + modeled exposure from public production data," not
as customs-attributed company volumes.

**Recommendation:** don't chase company-attributed customs data on a free project —
the data either doesn't exist publicly or is paywalled and redacted. Instead (a)
wire in the cited disclosure files (§1), and (b) add the USDA-PSD × disclosed-
footprint modeled overlay above, badged MODELED, as the honest best-effort company
view. Revisit a Panjiva/ImportGenius licence only if the project ever gets a budget.

---

## 5. Sequenced plan

Ordered by credibility-per-unit-effort, consistent with `REMEDIATION_PLAN.md`.

**Now (offline, fast, no API risk):**
- Promote/label the company tab: visible MODELED label on any uncited company
  view; decide modeled-fallback vs "no disclosure" (§1).
- Tighten the methodology copy with the three answers in §4, including an explicit
  "supply-side only, affordability lives in the crisis layer" sentence for 4c
  until/unless the new component ships.

**Next (needs your Mac — internet + git):**
- Fix `refresh_net_food_trade.py` → one sourced trade number for ~174 countries
  (§2.1). Highest leverage.
- Widen Comtrade importer coverage for the demo + large-importer set (§2.2).
- Re-verify the demo-set structural baselines, legacy → sourced (§2.4).

**Then (bigger):**
- Scope and build the **Economic Access** component (§4c) — the move that answers
  the economists and lifts granularity at once.
- FAOSTAT FBS caloric shares once auth clears; the `w/r/m` schema split from the
  remediation plan.
- The standing honesty gap: a back-test, so weights can stop being pure judgment.

**Don't:**
- "Correct" the Comtrade $B/$M display — it's already right.
- Hand-edit any country to look better — fix at source/render (the project's rule).
- Change the seven weights silently — if Economic Access lands, re-weight openly
  and recompute the worked examples.

---

## 6. One-paragraph answer you can send the reviewers now

> You're right on both data points. The company sourcing on the live tab is a
> modeled commodity-overlap, not the cited per-company research — that research
> exists (248 sourced country claims across 12 traders) but isn't wired in yet
> because none of the files are marked complete; I'm promoting the listed
> companies first and labelling the modeled view clearly in the meantime. The
> per-country trade and structural numbers are ~84% legacy-curated estimates, which
> is the granularity you flagged; the fix is landing real sourced trade data, and
> the single biggest lever is repairing the net-food-trade pipeline (currently
> empty) to give every country one comparable, sourced trade balance. On
> methodology: Supply-Chain Exposure is in the formula to catch chokepoint volume a
> caloric-import view misses (it's the lightest weight on purpose); the seven
> weights are reasoned judgment, not regressed — there's no back-test yet, and I
> say so. On economic risk: you've found a real gap. It currently enters only
> through food inflation and governance proxies, with no explicit affordability or
> FX/sovereign term — I'm scoping that as an explicit Economic Access component
> rather than defending the omission.
