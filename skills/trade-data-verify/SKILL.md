---
name: trade-data-verify
description: >-
  Pull and cross-check a country's food import/export figures against authoritative
  public sources — FAOSTAT, UN Comtrade, ITC Trade Map, World Bank WITS, WTO, Eurostat/Comext,
  USDA GATS, and OECD. Use this whenever you need to verify, source, or refresh trade numbers
  for FoodShield AI: when a country's import/export, supplier-concentration, or net-food-trade
  value looks wrong, stale, or legacy-curated; when a reviewer questions a trade figure; when
  adding a new country to a sourced footing; or when reconciling two sources that disagree.
  Trigger on phrases like "check this country's trade data", "is this import number right",
  "where did this export figure come from", "source the trade flows for X", "verify against
  FAOSTAT/Comtrade", or "cross-check the supplier data". Produces a cited, reconciled figure
  with provenance and an as_of date — never an unsourced guess.
---

# Trade-data verification & sourcing

## What this is for

FoodShield AI's structural trade numbers are ~84% legacy-curated — hand-authored
estimates that haven't been checked against a live source. Reviewers flagged the
per-country import/export figures as the weakest data. This skill is the disciplined
way to replace a legacy guess with a sourced, dated, cited number, or to confirm an
existing number is right.

The job is not "go find a number." It's **triangulate**: pull the same flow from two
independent authoritative sources, reconcile them, and record exactly where it came
from and as of when. A single source can be wrong (revised data, mirror lag, wrong
HS code); two that agree are trustworthy.

## The honesty contract (non-negotiable)

This is the entire credibility proposition of the project, so it governs everything here:

- **Every figure gets a provenance tag and an `as_of` year.** Tags: `sourced`
  (verified against a public dataset, `source_url` + `as_of` populated), `modeled`
  (computed from sourced inputs), `legacy_curated` (unverified heritage — the thing
  we're replacing). Never relabel a guess as sourced.
- **If two authoritative sources disagree by more than ~10%, do not silently pick one.**
  Record both, note the discrepancy and the likely reason (different HS coverage, a
  revision year gap, re-exports), and flag it for human review. Reviewers respect a
  flagged disagreement; they lose trust over a confident wrong number.
- **Never fabricate a value to fill a gap.** "Not available from these sources for
  this country/year" is a valid, honest result. Degrade to a low-confidence flag,
  not an invented figure.
- **Match the units and basis before comparing.** USD value vs metric tonnes,
  calendar year vs marketing year, gross vs net, food-total vs agri-total. Most
  apparent "errors" are unit or basis mismatches, not real disagreements.

## Picking the source for the question

Read `references/sources.md` for the full catalogue (endpoints, access notes, what
each is best at, known gotchas). The short decision rule:

- **Broad food/agriculture totals, any country, long time series** → start with
  **FAOSTAT** (TCL for trade value/quantity; FBS for caloric balance). 245+ countries.
- **Specific commodity, by partner country, the gold standard for bilateral flows**
  → **UN Comtrade** with the right **HS code** (see `references/hs_codes.md`). This is
  the authoritative cross-check for "who supplies whom."
- **EU member states, monthly, by product and partner** → **Eurostat / Comext**.
- **US agricultural trade specifically** → **USDA GATS** (also USDA PSD, already in the
  pipeline, for production + marketing-year trade in tonnes).
- **Tariffs + partner analysis layered on trade** → **World Bank WITS** (pulls Comtrade).
- **A friendly UI to eyeball a figure before committing to an API pull** → **ITC Trade
  Map** or **OECD Data Explorer**.
- **One country where the global sources look thin** → that country's **national
  statistics office / customs agency** (search "[country] customs trade statistics").

Default workflow: **FAOSTAT for the food-category figure, then UN Comtrade (or ITC
Trade Map) to confirm the product-level number using the HS code.** If they agree,
you have a sourced value. If not, reconcile per the honesty contract.

**Check the data already in the pipeline FIRST.** Before any external pull, look at
what FoodShield already has sourced — it's faster and free, and often answers the
question outright:
- `data/usda_psd.json` — production + import/export in **tonnes** per country per staple,
  by marketing year, refreshed on the WASDE cycle. For an export/import *quantity* claim
  on wheat/rice/maize/soybeans this is frequently a ready sourced answer (e.g. Vietnam
  rice exports ≈ 8,000 kt MY2026 is already in here, flagged `sourced`).
- `data/comtrade_staples.json` — bilateral supplier values + shares for priority importers.
- `data/net_food_trade.json` — net food balance (once the repaired pipeline lands).
Only go to the external APIs when the in-repo data is missing, stale, or you need a second
independent source to reconcile. A claim you can satisfy from `usda_psd.json` doesn't need
a Comtrade call — but note that PSD gives tonnes and Comtrade gives USD (different bases).

## The verification workflow

1. **State the exact claim.** Country (ISO3), commodity, flow direction (import/export/net),
   year, and basis (USD or tonnes). Vague claims can't be verified. "Egypt wheat imports,
   2024, USD value" — not "Egypt's trade."
2. **Pull from the primary source** for that question type (above).
3. **Pull the same flow from a second independent source.** Use the HS code from
   `references/hs_codes.md` for commodity-level pulls so you're comparing like with like.
4. **Reconcile.** Convert to a common unit/basis. If within ~10%, take the more recent /
   more authoritative as the value and cite both. If not, flag per the honesty contract.
5. **Compare to the existing FoodShield value.** Read the current value from
   `data/countries.json` (per-country structural overlay) or `data/comtrade_staples.json`
   / `data/net_food_trade.json`. Note whether the existing value was `legacy_curated`.
6. **Emit a verification record** in the format below — this is the deliverable, whether
   the number checks out or needs replacing.

When you need to pull at scale (many countries, repeated runs), use the bundled
`scripts/comtrade_pull.py` and `scripts/faostat_pull.py` helpers rather than hand-rolling
requests — they follow the project's `_common.py` envelope conventions and the established
HS-code set, so the output drops straight into the data pipeline. Read those scripts'
headers before running; they document the free-tier quotas and the bulk-download fallbacks.

## Output format

Always produce a verification record per claim, as a JSON object (or a table of them for
batch work). This is what makes the result usable in the pipeline and defensible to a reviewer:

```json
{
  "iso3": "EGY",
  "commodity": "wheat",
  "flow": "import",
  "year": 2024,
  "basis": "USD",
  "value": 4192000000,
  "primary_source": {"name": "UN Comtrade", "hs_code": "1001", "url": "...", "as_of": 2024},
  "cross_check": {"name": "FAOSTAT TCL", "value": 4100000000, "url": "...", "as_of": 2023},
  "agreement_pct": 2.2,
  "provenance": "sourced",
  "existing_foodshield_value": {"value": 4200000000, "quality_flag": "legacy_curated"},
  "verdict": "confirmed | replace | flag_for_review",
  "note": "Comtrade 2024 and FAOSTAT 2023 agree within 2.2%; existing legacy value confirmed, upgrade flag to sourced."
}
```

`verdict` is the action: **confirmed** (existing value is right, optionally upgrade its
flag to sourced), **replace** (existing value is wrong/stale — supply the corrected value
+ citation), **flag_for_review** (sources disagree or data unavailable — do not auto-change).

## A worked example

Claim: Egypt wheat imports, 2024, USD.
1. UN Comtrade, reporter=Egypt, partner=World, HS 1001, 2024 → ~$4.19B.
2. FAOSTAT TCL, Egypt, Wheat, Import Value, latest year (2023) → ~$4.1B.
3. Within ~2%, both authoritative. Value = $4.19B (Comtrade, more recent), FAOSTAT cited as cross-check.
4. Existing `comtrade_staples.json` Egypt wheat = $4.19B already. **Verdict: confirmed.** This
   is why the earlier "Egypt $15B" figure was a real bug and the current one isn't.

## When to stop and ask the human

- Sources disagree by >10% and you can't explain it from units/basis/coverage.
- The change would move a country's FDRS materially (trade feeds Import Dependency,
  Supplier Concentration, Supply-Chain Exposure — landing a big correction shifts the score).
- A source requires credentials or a paid tier you don't have (ITC Trade Map full API,
  some national portals). Report what's reachable free and flag the gap.

Read `references/sources.md` for endpoints and access details and `references/hs_codes.md`
for the staple HS codes before any commodity-level pull.
