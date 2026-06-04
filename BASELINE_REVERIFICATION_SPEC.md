# Baseline re-verification spec — turning legacy-curated country data into sourced data

_Written June 2026. Owner: Fedde. Status: working spec, ready to execute on the Mac._

_This is the answer to reviewer feedback §2: ~84% of the per-country structural
numbers in `data/countries.json` are `legacy_curated` heritage estimates, not
sourced values. That is why the countries blur together — the numbers that should
*discriminate* one country from another are hand-authored, so they don't. This
spec is the disciplined, country-by-country process to flip them to sourced. It
is deliberately slow and honest: there is no one-command fix, and pretending there
is would reproduce the exact overclaim the project's honesty rule exists to stop._

---

## 0. Where things stand (measured, not estimated)

Run the bundled audit first; it reads `data/countries.json` and reports the truth:

```
python3 scripts/flag_legacy_countries.py
```

As of this writing it reports (and you should re-run it after every batch):

- **264 countries**, **0 fully sourced**, **87 fully legacy**.
- **Overall field-level legacy ratio: 83.7%** (2,874 of 3,432 present field-instances).
- Per field: `fdrs`, `c`, `f2030`, `net`, `imports`, `exports`, `exportDests` are
  **100% legacy**; `suppliers`/`supPct` ~99.6% legacy (only EGY sourced so far);
  `fi` 88.3% legacy; `w`/`r`/`m` already **33.7% legacy** (FAOSTAT FBS landed for 175).

That 83.7% is the headline number reviewers reacted to, and it is the number the
QA provenance guard (§7) watches fall as this work proceeds.

The script also writes a **prioritised worklist** (`reverify_worklist.csv` +
`.json`) — the order to actually work through, not just the stats.

---

## 1. The schema you are writing back into

Every field in `data/countries.json` is an object, not a bare value:

```json
"suppliers": {
  "value": ["Russia", "Ukraine", "Romania", "France", "USA"],
  "source": "UN Comtrade",
  "source_url": "https://comtradeplus.un.org/...",
  "as_of": "2024",
  "method": "Reporter=EGY, partner-shares for HS 1001 (wheat), top-5 by import value",
  "quality_flag": "sourced",
  "note": "Cross-checked against FAOSTAT TCL 2023; agreement within 6%."
}
```

The contract (from the trade-data-verify skill's honesty section, non-negotiable):

- `quality_flag` is one of: **`sourced`** (verified against a public dataset,
  `source_url` + `as_of` populated), **`modeled`** (computed from sourced inputs),
  **`manual`** (hand-maintained snapshot of a static public release),
  **`legacy_curated`** / **`legacy_import_dependency`** (the heritage values we are
  replacing).
- **You may only flip `legacy_*` -> `sourced` when you have a public source with a
  URL and an `as_of` year that confirms the value.** No URL, no flip. See §4.
- Never relabel a guess as `sourced`. "Not available from these sources for this
  country/year" is a valid result — leave it `legacy_curated` and note the gap.

---

## 2. Per-field re-verification checklist

For each country, walk its fields in this order. Each row says: what the field is,
which authoritative source confirms it, the trade-data-verify entry point, and what
to write back. **Use the `trade-data-verify` skill for everything trade-related** —
it owns the source catalogue (`references/sources.md`), the HS codes
(`references/hs_codes.md`), the triangulation workflow, and the verification-record
output format. This spec maps fields to that skill; it does not duplicate it.

### 2.1 `w`, `r`, `m` — wheat/rice/maize caloric shares

- **Authoritative source:** FAOSTAT **Food Balance Sheets (FBS)** — kcal/capita/day
  (element 664), item-share of Grand Total (item 2901). This is already the sourced
  path for 175 countries via `refresh_faostat_fbs.py`.
- **Action:** these flip to `sourced` automatically when FBS coverage reaches the
  country (pending the FAOSTAT auth migration clearing the 403). Do **not** hand-edit
  them. For the 89 still legacy, they carry `legacy_import_dependency` and mean
  "fraction of consumption imported," *not* caloric share — do not display them as
  caloric share until FBS replaces them.
- **Write-back:** `source: "FAOSTAT Food Balance Sheets"`,
  `source_url: https://www.fao.org/faostat/en/#data/FBS`, `as_of` = FBS data year,
  `quality_flag: "sourced"`.

### 2.2 `imports` / `exports` / `exportDests` — commodity & destination baskets

- **Authoritative source:** UN **Comtrade** (bilateral, the gold standard for "who
  trades what with whom"), cross-checked against **FAOSTAT TCL** (trade value/quantity
  by commodity). EU members: **Eurostat / Comext**. US: **USDA GATS**.
- **trade-data-verify entry:** pull the top food commodities by import (and export)
  value for the reporter using the staple **HS codes** in `references/hs_codes.md`
  (wheat 1001, rice 1006, maize 1005, soy 1201, palm 1511, etc.). The "basket" is the
  ranked list of the largest food flows — not a guess at what the country eats.
- **Write-back:** the ranked commodity list, `source: "UN Comtrade"` (+ FAOSTAT TCL
  cross-check in `note`), `as_of` = trade year (currently 2024 for Comtrade),
  `method` naming the HS codes and the value basis.
- **Honesty note:** `exports` legacy rows often contain non-food items ("Tourism",
  "Petroleum products"). The sourced replacement is the **food** export basket; keep
  the scope explicit in `method`.

### 2.3 `suppliers` / `supPct` — supplier countries & concentration weights

- **Authoritative source:** UN **Comtrade** partner-shares for the country's main
  import staple(s) — this is the single most reviewer-visible field (it drives the
  Trade Flow Atlas donut and the Supplier-Concentration FDRS component). Already
  sourced for EGY; `data/comtrade_staples.json` holds top-5 supplier values+shares for
  the ~19–25 priority importers.
- **trade-data-verify entry:** reporter = country, partner = each origin, HS = the
  dominant staple (usually wheat 1001 for import-dependent countries). Top-5 partners
  by import value; `supPct` = their shares. **Check `data/comtrade_staples.json`
  first** — if the country is already in there, the answer is sourced and free.
- **Write-back:** `suppliers` = ordered partner list, `supPct` = matching shares
  (must align index-for-index), `source: "UN Comtrade"`, `as_of` = year, `method`
  naming the HS code and that shares are by import value. Cross-check the HHI implied
  by `supPct` against the FDRS supplier-concentration input.
- **Reconcile rule:** if Comtrade and the cross-check disagree >10%, record both and
  `flag_for_review` — do not silently pick one (skill honesty contract).

### 2.4 `net` — net agri-food trade balance (USD millions, +export / −import)

- **Authoritative source:** **FAOSTAT TCL** net agri-food balance — this is what the
  (now repaired) `refresh_net_food_trade.py` produces into `data/net_food_trade.json`.
  Comtrade is the cross-check (sum of food chapters, exports − imports).
- **Action:** this is the **highest-leverage single fix** — one sourced, comparable
  trade number for ~174 countries at once, exactly the hard differentiator the
  granularity feedback wants. Run the repaired pipeline; it populates `net` wholesale.
  Per-country manual work here is only for the countries the bulk parse misses.
- **Write-back:** `source: "FAOSTAT TCL"`,
  `source_url: https://www.fao.org/faostat/en/#data/TCL`, `as_of` = year,
  `quality_flag: "sourced"`.

### 2.5 Production-trend input (inside `c[2]`) — staple output 5-yr trend

- **Authoritative source:** **USDA PSD** (`data/usda_psd.json`, already sourced for
  151 countries, production in tonnes by marketing year) — compute the 5-year CAGR of
  staple output. FAOSTAT QCL is the cross-check.
- **Write-back:** this feeds the Production-Trend component of the `c` vector; recompute
  `c[2]` from the sourced PSD series rather than carrying the legacy estimate.

### 2.6 `fdrs`, `c`, `f2030` — composite score, 6-component vector, 2030 projection

- **These are NOT independently sourced.** They are **modeled** outputs computed from
  the inputs above (import dependency, supplier concentration, production trend, food
  inflation, climate, conflict). You do not "verify `fdrs` against a source" — you
  re-derive it once its inputs are sourced.
- **Action:** after a country's input fields (§2.1–2.5 + the climate/conflict/inflation
  blends, which are already sourced at render time) are upgraded, **recompute** `c`
  and `fdrs` and set their `quality_flag` to **`modeled`** (not `sourced` — they are
  derived), with `method` naming the FDRS formula version and `as_of` = the date of
  recompute. `f2030` stays `modeled` and explicitly labelled an illustrative scenario,
  per DEMO_SCRIPT.md ("don't lead with the 2030 forecast").
- **See §5 (FDRS movement) — this step shifts the score and must be coordinated with
  `FDRS_V2_DESIGN.md`.**

### 2.7 `fi` — food inflation baseline

- Already overridden at render time by live sources (Eurostat for 31 countries, etc.).
  The stored `fi` is a fallback; leave `legacy_curated` unless you have a national CPI
  food-index figure with a URL. Lowest priority — the live layer handles it.

---

## 3. The provenance flip rule (the one rule that matters)

> A field moves `legacy_curated` (or `legacy_import_dependency`) **-> `sourced`**
> only when its `value` has been confirmed against a **public source**, and that
> source's **URL and `as_of` year are written into the field object**. Derived
> composites (`fdrs`, `c`, `f2030`) move to **`modeled`**, never `sourced`.
> Anything you cannot confirm stays `legacy_*` and gets a `note` saying what you
> tried and why it's still unverified.

Corollaries:
- **One source is not enough for trade flows** — triangulate per the skill: a primary
  (Comtrade) and an independent cross-check (FAOSTAT TCL / Eurostat / USDA). Within
  ~10% → take the more recent/authoritative, cite both. Beyond 10% → `flag_for_review`,
  leave legacy.
- **Match units/basis before comparing** (USD vs tonnes, calendar vs marketing year,
  food-total vs agri-total). Most apparent errors are basis mismatches.
- **Never fabricate to fill a gap.** A flagged honest gap earns reviewer trust; a
  confident wrong number loses it.

---

## 4. Staged rollout (realistic — this is slow manual work)

Work the prioritised worklist from `flag_legacy_countries.py` top-down. The priority
score is: demo-set (weight 1000) > large importer (100) > count of remaining legacy
fields. Three stages:

### Stage 1 — the demo set (do these first, by name)

These are the panels a Rabobank reviewer will actually open (DEMO_SCRIPT.md):

| ISO3 | Country | Why first |
|---|---|---|
| **NLD** | Netherlands | Rabobank home country, demoed live (Step 3). Get every field sourced. |
| **EGY** | Egypt | Headline chokepoint story — "43% of wheat from Russia, HHI 72". Suppliers already sourced; finish the rest. |
| **SSD** | South Sudan | Top of the most-vulnerable ranking (FDRS ~80) shown on the map. |
| **YEM** | Yemen | Most-vulnerable ranking; ~90% wheat import dependence is a spoken demo line. |
| **SOM** | Somalia | Most-vulnerable ranking (FDRS ~78). |

For each: verify §2.1–2.5, recompute §2.6, land all in one commit per country with the
matching methodology/Data-Status copy updated in the same commit. **5 countries × ~10
legacy fields each — budget this as the first real session of work, not an afternoon.**

### Stage 2 — top-20 food importers / large economies

The next ~25 rows in the worklist (all weight 110): CHN, USA, JPN, FRA, IND, DEU, GBR,
KOR, SAU, MEX, IDN, BRA, RUS, TUR, NGA, PHL, BGD, DZA, IRN, ARE, BEL, CAN, ESP, ITA,
VNM, THA, MYS, POL. These are the countries reviewers will spot-check against their own
knowledge, and large importers where supplier-concentration data matters most. Comtrade
free-tier quota is the constraint — batch the pulls, prioritise within this set by
import size, and lean on `data/comtrade_staples.json` and `data/usda_psd.json` for
countries already covered (free, no quota).

### Stage 3 — the long tail

The remaining ~234 countries. Two accelerators do most of the work without per-country
labour:
- **`net`** lands for ~174 at once from the repaired `refresh_net_food_trade.py`.
- **`w`/`r`/`m`** flip for the rest as FAOSTAT FBS auth clears (`refresh_faostat_fbs.py`).

After those bulk lands, the residual manual work is `imports`/`exports`/`suppliers`/
`supPct` for the small/data-thin countries — lowest reviewer-visibility, do last. For
countries where the global sources are thin, fall back to the national statistics
office / customs portal (skill `references/sources.md`), and where nothing is reachable
free, **leave legacy and flag** — do not invent.

**Pace.** This is weeks of intermittent work, not one run. The honest framing for the
reviewers (and for yourself) is: demo set sourced now, top importers next, full baseline
over time, with the legacy ratio reported openly as it falls.

---

## 5. FDRS-movement caution

Changing import-dependency, supplier-concentration, production-trend, or net-trade
inputs **moves the FDRS score** — those are four of its weighted components (28% / 18%
/ 14% + the supply-chain term). So every batch of sourced inputs requires:

1. **Recompute** `c` and `fdrs` for each touched country (set them `modeled`, §2.6).
2. **Record the movement.** Note old vs new FDRS in the commit / a movement log, with
   the reason ("supplier HHI fell because Comtrade shows more diversified wheat origins
   than the legacy estimate assumed"). A reviewer who sees a score change wants to know
   *why*, and a documented, sourced reason is the whole credibility proposition.
3. **Coordinate with `FDRS_V2_DESIGN.md`.** That document is adding an Economic Access
   component and re-weighting the seven existing ones. **Do not** re-verify baselines
   and re-weight in the same uncoordinated pass — you won't be able to tell whether a
   score moved because the data got better or because the weights changed. Sequence it:
   either (a) finish a baseline batch under the *current* weights, record the movement,
   then apply v2 re-weighting as a separate, labelled step; or (b) freeze baselines,
   land v2, then re-verify. Pick one and state it in the commit. Any worked FDRS example
   in `index.html` / methodology copy that moves must be recomputed in the same commit
   (REVIEWER_FEEDBACK_RESPONSE.md §2 fix 5).
4. **Big moves stop and ask the human** (skill's "when to stop" rule): if a correction
   shifts a country's FDRS materially, surface it for sign-off before committing — a
   large score swing on a demo country mid-pitch-prep is exactly the surprise to avoid.

---

## 6. The verification record (the per-country deliverable)

For each field you touch, the trade-data-verify skill's output format is the artefact —
emit a verification record (JSON) per claim, then transcribe the confirmed value +
provenance into the `data/countries.json` field object. Keep the records (a per-country
`reverify/EGY.json` etc., or appended to a log) so the work is auditable: a reviewer can
ask "where did NLD's wheat suppliers come from?" and you can show the Comtrade pull, the
FAOSTAT cross-check, the agreement %, and the verdict. The record's `verdict` drives the
action: **confirmed** (legacy value was right → flip flag to sourced), **replace**
(legacy value wrong → write corrected value + citation), **flag_for_review** (sources
disagree / unavailable → leave legacy, note why).

---

## 7. Tie-in to the QA provenance-ratio guard

`scripts/qa_checks.py` (the reliability gate, per REMEDIATION_PLAN.md and
PROJECT_HANDOFF_FOR_AI.md) should carry a **provenance-ratio guard**: it computes the
field-level legacy ratio the same way `flag_legacy_countries.py` does, and the
expectation is that **this number monotonically falls** as re-verification proceeds.

Recommended wiring:
- `qa_checks.py` reads `data/countries.json`, computes overall `pct_legacy_fields`
  (today **83.7%**), and **fails / warns if the ratio *rises*** relative to a stored
  baseline (a regression — someone re-introduced legacy data or reverted a sourced
  field).
- It records the current ratio to a small state file (e.g. `data/qa_baseline.json`) on
  each green run, so the guard is a ratchet: the legacy share can only go down.
- Optionally, a per-stage target: e.g. "demo set must be 0% legacy" — fail if any of
  NLD/EGY/SSD/YEM/SOM still has a `legacy_*` field, so a demo country can't silently
  regress before a pitch.
- Run `flag_legacy_countries.py` after every batch and before every deploy; its
  `overall_pct_legacy_fields` is the single number to paste into the Data Status page
  and to quote to reviewers as the progress metric.

The honest story this enables: not "we fixed it," but "here is the legacy ratio, here is
the worklist, here is it falling commit by commit." That trajectory is more convincing to
a research audience than any single claim of completeness.

---

## 8. Quick-start (on the Mac, with internet)

```bash
# 1. See where you stand and get the worklist
python3 scripts/flag_legacy_countries.py            # prints stats, writes reverify_worklist.csv/.json

# 2. Land the two bulk accelerators first (they clear most of the tail)
python3 scripts/refresh_net_food_trade.py           # -> net for ~174 countries (FAOSTAT TCL)
python3 scripts/refresh_faostat_fbs.py              # -> w/r/m as auth allows

# 3. Work the worklist top-down using the trade-data-verify skill, country by country.
#    Demo set (NLD, EGY, SSD, YEM, SOM) first. Triangulate, write back provenance, recompute FDRS.

# 4. Re-run the audit after each batch; confirm the legacy ratio fell.
python3 scripts/flag_legacy_countries.py

# 5. Run the QA gate before deploying.
python3 scripts/qa_checks.py
```

Nothing here is one command. That's the point — it's tractable, sequenced, and honest.
