# Putting Vietnam's rice exports on a sourced footing

Following the `trade-data-verify` skill. The sandbox has no internet, so I cannot
fire the live API calls. Below is the exact procedure I'd run, the precise
parameters for each call, the reconciliation logic, and the verification record
the skill specifies — with the two live-pull cells clearly labelled
`PLACEHOLDER — LIVE PULL REQUIRED` rather than invented. The honesty contract
forbids fabricating a final number I can't source, so I do not assert one.

---

## Step 1 — State the exact claim

The skill's first rule: a vague claim can't be verified. Vietnam's current
FoodShield entry only lists "Rice" as a qualitative top-export string — there is
no sourced value behind it. I pin the claim down to something falsifiable:

> **Vietnam (VNM) rice exports, 2024, USD value, to World (all partners), HS 1006.**

- **Reporter:** Vietnam, ISO3 `VNM`, UN M49 `704`.
- **Commodity:** Rice, HS4 `1006` (per `references/hs_codes.md`). I pull at HS4
  for the headline export value so it matches FAOSTAT's "Rice" item scope. I will
  *also* pull HS6 `100630` (semi/wholly milled) because Vietnam's exports are
  overwhelmingly milled rice and `100630` is the line that reconciles to USDA PSD
  "rice, milled" if a third check is wanted.
- **Flow:** export, Comtrade `flowCode=X`.
- **Year:** 2024 (latest complete calendar year; FAOSTAT will lag to ~2023).
- **Basis:** USD value. Note explicitly — this is *not* tonnes. FAOSTAT 5610/5910
  tonnage and Comtrade USD must never be compared directly (reconciliation
  cheatsheet).

This is an **export** claim, so per the cheatsheet I prefer the **reporter's own
export figure** (Vietnam reporting its exports) as the primary, not a mirror built
from partners' imports.

## Step 2 — What's there now (the legacy guess we're replacing)

Read from `data/countries.json` → `VNM`:

- `exports.value` = `["Rice","Coffee","Seafood","Rubber","Vegetables"]`,
  `quality_flag: "legacy_curated"`, `as_of: "2026-05"`. Rice is listed first but
  carries **no dollar value** — it's an ordinal list inherited from the embedded
  `index.html` dataset.
- `net.value` = `5500` (net agri-food trade, USD millions), also
  `legacy_curated`, `as_of: "2026-05"`.
- `data/net_food_trade.json` `data` block is **empty** — its own `_meta` says
  "Covered 0 of 174 mapped countries", so there is no sourced net or rice figure
  for Vietnam anywhere in the pipeline.
- `data/comtrade_staples.json` contains VNM only as a *supplier* inside other
  reporters' import records (e.g. VNM rice into PHL, coffee into the US). There is
  **no VNM-as-reporter export block**, so the rice export value is genuinely
  unsourced today.

**Conclusion:** there is nothing to "confirm" — this is a net-new sourced figure
replacing a qualitative legacy placeholder. That makes triangulation more
important, not less: with no prior number to anchor against, two independent
sources that agree are the whole basis for trust.

## Step 3 — Primary pull: UN Comtrade (reporter = Vietnam)

Vietnam is not an EU member and the claim is non-US, so the bilateral gold
standard is the right primary (sources.md §2). Exact call:

```
GET https://comtradeapi.un.org/data/v1/get/C/A/HS
    ?reporterCode=704     # Vietnam (M49). NB: not in comtrade_pull.py's ISO3_TO_M49 — add "VNM": 704
    &partnerCode=0        # 0 = World aggregate -> the headline total
    &cmdCode=1006         # Rice, HS4
    &period=2024
    &flowCode=X           # X = export
    &customsCode=C00&motCode=0
Header: Ocp-Apim-Subscription-Key: $COMTRADE_KEY   # free key raises the quota
```

Via the bundled helper (after adding `"VNM": 704` to `ISO3_TO_M49`):

```
COMTRADE_KEY=xxxx python3 scripts/comtrade_pull.py \
    --reporters VNM --hs 1006 --year 2024 --flow X --out vnm_rice_comtrade.json
```

Read the `partnerCode=0` row's `primaryValue`. **Watch the basis gotcha**
(sources.md §2 / script header): the public preview returns `primaryValue` in
**raw USD, not millions**, and usually leaves `netWgt` null — so I take the USD
value and do *not* double-convert. To get the supplier/destination concentration
for the FDRS supplier-share input, I'd run the same call with `partnerCode=` blank
to get all partners and rank the top 5 by value.

**Result cell:**
`VNM rice (HS 1006) exports to World, 2024, Comtrade primaryValue =`
**`PLACEHOLDER — LIVE PULL REQUIRED`** (raw USD). For sizing context only, Vietnam
typically exports on the order of US$4–5B of rice annually; I am *not* recording
that as the value — it must come off the live pull.

## Step 4 — Independent cross-check: FAOSTAT TCL

Comtrade can be wrong on a single line (revisions, mirror lag, re-exports), so I
triangulate against FAOSTAT, which is independent of Comtrade for this flow.

- Domain: TCL (Trade — Crops & Livestock).
- Area: "Viet Nam" (FAOSTAT spelling — map to ISO3 `VNM`; the helper keys by Area
  name and warns you to map before comparing).
- Item: Rice — **match by normalised item name first**, numeric code as fallback,
  because FAO ships dual Item-Code columns and drifts name casing (sources.md §1;
  this is the exact bug that broke `net_food_trade`). The helper's `_norm()` +
  name-key logic handles this.
- Element: **5922 Export Value (1000 USD)** for the USD comparison. (5910 Export
  Quantity = tonnes — different basis, do not compare to the Comtrade USD.)
- Latest year: expect **2023**, not 2024 — FAOSTAT runs 1–2 years behind.

Use the bulk normalized ZIP, not the flaky query API:

```
python3 scripts/faostat_pull.py --items 1006 --iso VNM --out vnm_rice_faostat.json
# (downloads Trade_CropsLivestock_E_All_Data_(Normalized).zip, ~250MB, no auth/quota)
```

FAOSTAT TCL values are in **1000 USD**; the helper converts to millions
(`/1000`). Convert both sides to the same unit before comparing.

**Result cell:**
`Viet Nam rice export value, FAOSTAT TCL element 5922, latest year (~2023) =`
**`PLACEHOLDER — LIVE PULL REQUIRED`** (USD).

*Optional third check:* USDA PSD "rice, milled" exports (already in
`data/usda_psd.json`) — but that's in **tonnes**, so it validates the quantity and
trend, not the USD figure. Useful as a sanity ceiling, not a USD reconciliation.

## Step 5 — Reconciliation logic

Per the honesty contract and the cheatsheet, once both cells are filled:

1. **Align units & basis.** Comtrade raw USD → millions; FAOSTAT 1000-USD →
   millions. Both export value, both rice. Now comparable.
2. **Align years honestly.** Comtrade 2024 vs FAOSTAT ~2023 is a *one-year gap*,
   not an error — note it. A year of rice-price/volume movement can easily explain
   a single-digit-percent difference.
3. **Compute `agreement_pct`** = |A − B| / mean(A, B) × 100.
   - **≤ ~10%:** sources agree. Take the **more recent / more authoritative**
     value (Comtrade 2024, the reporter's own export) as the figure; cite FAOSTAT
     2023 as the cross-check. Provenance = `sourced`.
   - **> ~10%:** do **not** silently pick one. Record both, name the likely cause
     (year gap, HS4 `1006` vs FAOSTAT "Rice" item scope, re-export inflation —
     less likely for an origin exporter like Vietnam than for NLD/SGP), and set
     verdict `flag_for_review`.
4. **FDRS materiality check.** Vietnam is a top-3 world rice exporter; a sourced
   rice export value feeds Import Dependency / Supplier Concentration / net-food-
   trade and could move the FDRS. The skill says: if a correction would move the
   score materially, flag for a human even when the data is clean. So this one gets
   a human nod before it lands, regardless of agreement.

## Step 6 — Verification record (the deliverable)

Emitted in the skill's required JSON shape. Live-pull fields are explicit nulls
with a `PLACEHOLDER` marker — never a fabricated number.

```json
{
  "iso3": "VNM",
  "commodity": "rice",
  "flow": "export",
  "year": 2024,
  "basis": "USD",
  "value": null,
  "value_status": "PLACEHOLDER — LIVE PULL REQUIRED (no internet in sandbox)",
  "primary_source": {
    "name": "UN Comtrade",
    "hs_code": "1006",
    "reporter_m49": 704,
    "partner": "World (partnerCode=0)",
    "flow_code": "X",
    "url": "https://comtradeapi.un.org/data/v1/get/C/A/HS?reporterCode=704&partnerCode=0&cmdCode=1006&period=2024&flowCode=X",
    "as_of": 2024,
    "value": null,
    "value_note": "primaryValue is RAW USD on the public preview; do not double-convert"
  },
  "cross_check": {
    "name": "FAOSTAT TCL",
    "area": "Viet Nam",
    "item": "Rice",
    "element": "5922 Export Value (1000 USD)",
    "url": "https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip",
    "as_of": 2023,
    "value": null
  },
  "agreement_pct": null,
  "provenance": "pending_sourced",
  "existing_foodshield_value": {
    "where": "data/countries.json -> VNM.exports (ordinal list, no $ value) and VNM.net = 5500 (USD m, agri-food net)",
    "value": "Rice listed #1 export; no sourced rice export USD figure anywhere in pipeline",
    "quality_flag": "legacy_curated"
  },
  "verdict": "flag_for_review",
  "note": "Net-new sourcing, not a confirm: no prior sourced rice value exists for VNM (comtrade_staples has VNM only as a supplier in other reporters' imports; net_food_trade.json data block is empty). Run Comtrade reporter=704 HS 1006 flow=X 2024 as primary and FAOSTAT TCL element 5922 'Rice' (name-matched) as the independent cross-check, reconcile to ~10%, then upgrade VNM rice/export from legacy_curated to sourced. Held for human review because VNM is a top-3 world rice exporter and a sourced value moves the FDRS materially. On agreement, set provenance=sourced and value=Comtrade 2024 (more recent), FAOSTAT 2023 cited."
}
```

## What lands once the two cells are filled

- If `agreement_pct ≤ ~10%`: set `value` to the Comtrade 2024 figure, flip
  `provenance` to `sourced`, populate both `source_url`s and `as_of`, and update
  `data/countries.json` `VNM` so the rice export carries a dated, cited dollar
  value instead of an ordinal legacy string. The top-5 destination shares from the
  all-partners pull feed the supplier-concentration input.
- If they disagree by `> ~10%`: leave the legacy flag in place, record both
  numbers and the explained discrepancy, and surface it for human review — a
  flagged disagreement keeps reviewer trust; a confident wrong number destroys it.

## Honesty-contract checklist (self-audit)

- [x] Exact claim stated (country/commodity/flow/year/basis). 
- [x] Two independent sources named with exact parameters (Comtrade primary,
      FAOSTAT cross-check) — not one source twice. 
- [x] Correct HS code (1006 for rice; 100630 noted for the milled/USDA-PSD tie-in). 
- [x] Reconciliation rule applied (unit/basis align, ~10% threshold, year-gap
      caveat, FDRS materiality gate). 
- [x] Existing FoodShield value read and labelled `legacy_curated`. 
- [x] Verification record emitted in the skill's JSON shape. 
- [x] **No fabricated final number** — live-pull values are explicit
      `PLACEHOLDER — LIVE PULL REQUIRED`, provenance held at `pending_sourced` /
      verdict `flag_for_review` until the pulls run.
```
