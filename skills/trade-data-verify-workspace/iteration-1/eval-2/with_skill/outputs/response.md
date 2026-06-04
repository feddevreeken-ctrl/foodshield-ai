# Verifying Nigeria wheat supplier-concentration data

Short version: your supplier-concentration figure is a function of **who supplies Nigeria's wheat and in what proportion**, so the place to get it right is **UN Comtrade, HS 1001, reporter = Nigeria, by partner country** — then cross-checked against an independent source (FAOSTAT for the totals; ideally Nigeria's own customs data for the partner split). One source can be wrong; two that agree are trustworthy. Below is exactly how to pull it, compute the concentration metric, cross-check it, and record the result so it's defensible.

I don't have live internet in this session, so I can't pull the actual current numbers — I'll lay out the precise method, the endpoints, and the verification-record format, with the values left as placeholders to fill from the live pull. **I am not inventing the supplier shares.** Per the honesty contract, a fabricated number is worse than a flagged gap.

---

## 1. State the exact claim

You can't verify "supplier concentration looks stale." You verify a specific, dated claim. For supplier concentration the claim is actually a *vector* of partner shares plus a derived metric:

- **Country:** Nigeria (ISO3 **NGA**)
- **Commodity:** wheat — **HS 1001** (HS4; use this for "wheat broadly," which is what import-dependency wants; 100199 / 100119 only if a specific form matters)
- **Flow:** import
- **Partner:** *by partner country* (this is the whole point), plus partner = World as the denominator
- **Year:** the latest available complete year (state it — likely 2024 or 2025 depending on Comtrade coverage at pull time)
- **Basis:** USD value (Comtrade's reliable field on the free tier). Note: tonnes (`netWgt`) is usually null on the public preview, so concentration is computed on **value share**, not volume share — record that.

The metric you're verifying: **top-5 supplier shares and the HHI** (Herfindahl-Hirschman Index) computed from those partner shares.

---

## 2. Primary source + HS code: where the accurate current data lives

**UN Comtrade is the authoritative source for "who supplies whom."** That's exactly the supplier-concentration question. Use HS **1001**.

### 2a. Pull the denominator (total wheat imports)
```
https://comtradeapi.un.org/data/v1/get/C/A/HS
  ?reporterCode=566        # Nigeria
  &partnerCode=0           # World = total
  &cmdCode=1001            # wheat (HS4)
  &period=2024             # latest complete year
  &flowCode=M              # M = import
```
This gives Nigeria's **total wheat import value** — the denominator for every supplier share.

### 2b. Pull by partner (the supplier split)
Same call but `partnerCode=` left open (or iterate the major partners) so you get one row per supplier country:
```
.../get/C/A/HS?reporterCode=566&cmdCode=1001&period=2024&flowCode=M
```
Each row's `primaryValue` is that supplier's wheat exports *into Nigeria* in raw USD.

Two gotchas from the source catalogue that bite here:
- The public preview returns `primaryValue` in **raw USD, not millions** — the project field is misleadingly named `total_usd_m` but holds raw USD. Don't double-convert.
- **Re-exporters (NLD, BEL, SGP) inflate gross flows.** If the Netherlands shows up as a top Nigeria wheat supplier, that's likely transit/re-export, not origin. Note it; it distorts "true origin" concentration. Nigeria's real wheat origins are typically the US, Russia, Canada, Australia, Argentina, Latvia/Lithuania (Baltic transshipment) — sanity-check against that.

### Compute top-5 + HHI
1. Sort partners by import value descending.
2. **share_i = value_i / total_imports** (the World figure from 2a, or the sum of partners — record which).
3. **Top-5 concentration** = sum of the five largest shares.
4. **HHI = Σ (share_i × 100)²** across all partners (shares as percentages). 0–10,000 scale; >2,500 is highly concentrated. This is the supplier-concentration number that feeds FDRS.

This is exactly what `comtrade_staples.json` is meant to store: HS 1001, by partner, ranked, top-5 shares.

---

## 3. The cross-check (a single source isn't enough)

The job is **triangulate**, not "find a number." Pull the same flow from a second independent source and reconcile.

**Cross-check the total (3a → FAOSTAT).** FAOSTAT TCL, Nigeria, Wheat, Import Value:
- Bulk normalized ZIP (no auth, no rate limit):
  `https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip`
- Element **5622** = Import Value (1000 USD). Filter reporter = Nigeria, item = Wheat.
- FAOSTAT lags ~1–2 years (latest ≈ 2023 in mid-2026), so you're comparing Comtrade-2024 vs FAOSTAT-2023 — a known revision-year gap, not necessarily an error.

FAOSTAT confirms the **magnitude of the total**. It does **not** independently confirm the *partner split* (FAOSTAT TCL is reporter-side totals; ITC Trade Map and WITS are Comtrade-derived, so they're not truly independent on partners either).

**Cross-check the partner split — the genuinely independent source is Nigeria's own customs/statistics.** For the supplier breakdown, the only data not downstream of Comtrade is **Nigeria's National Bureau of Statistics (NBS) Foreign Trade reports** or the **Nigeria Customs Service**. Search "Nigeria NBS foreign trade statistics" / "Nigeria customs trade statistics." This gives country-level import flows by origin and is the right second leg for the concentration claim. (Firm/company-level isn't free, but country-of-origin is — and country-level is what FoodShield needs.)

So the triangulation is:
- **Total:** Comtrade 1001 World ↔ FAOSTAT TCL 5622 Wheat
- **Partner split / concentration:** Comtrade 1001 by-partner ↔ Nigeria NBS foreign-trade origin breakdown

---

## 4. Reconcile — honesty / agreement rules

Convert to a common unit and basis before comparing (USD value vs USD value; same year basis where possible).

- **Within ~10%** → agree. Take the more recent / more authoritative figure as the value, cite the other as cross-check.
- **Disagree by >10%** → **do not silently pick one.** Record both, name the likely reason, flag for human review:
  - **Revision-year gap** — Comtrade 2024 vs FAOSTAT 2023; expected, not an error.
  - **Coverage/scope** — FAOSTAT "wheat" item vs a single HS 1001 line; or food-total (1842) vs HS line.
  - **Re-export inflation** — a transit hub appearing as a "supplier."
  - **CIF vs FOB / mirror lag** — importer-reported imports vs partner-reported exports differ ~10–15% normally; for an *import* claim prefer Nigeria's import figure.
  - **FX timing** — Nigeria has had large naira devaluations; USD-value swings across years can be FX-conversion timing, not real volume change. This is a strong candidate for *why the data looks "stale"* — the old figure may have been right at an old exchange rate.
- **Not available for this year** → "not available from these sources" is a valid, honest result. Degrade to a low-confidence flag; never invent a share to make the top-5 sum to 100%.

Then **compare to the existing FoodShield value** in `data/comtrade_staples.json` (Nigeria wheat partner shares) or the per-country overlay in `data/countries.json`. Note whether the existing value is tagged `legacy_curated` — if it is, that's the unverified heritage estimate you're replacing, and a >10% move likely shifts Nigeria's FDRS (concentration feeds Supplier Concentration, Import Dependency, Supply-Chain Exposure), which is a stop-and-ask-the-human trigger.

---

## 5. Verification-record output

This is the deliverable — usable in the pipeline and defensible to a reviewer. Because I have no live data, every numeric field is a `<placeholder>` to be filled from the actual pull; the structure, sources, HS code, and method are real.

```json
{
  "iso3": "NGA",
  "commodity": "wheat",
  "flow": "import",
  "year": "<latest complete year, e.g. 2024>",
  "basis": "USD",
  "metric": "supplier_concentration",
  "total_import_value_usd": "<from Comtrade HS1001 partner=World>",
  "supplier_shares": [
    {"partner": "<USA>",       "value_usd": "<...>", "share_pct": "<...>"},
    {"partner": "<RUS>",       "value_usd": "<...>", "share_pct": "<...>"},
    {"partner": "<CAN>",       "value_usd": "<...>", "share_pct": "<...>"},
    {"partner": "<LVA/LTU>",   "value_usd": "<...>", "share_pct": "<...>", "note": "Baltic transship — verify true origin"},
    {"partner": "<ARG/AUS>",   "value_usd": "<...>", "share_pct": "<...>"}
  ],
  "top5_concentration_pct": "<sum of five largest shares>",
  "hhi": "<Σ (share_pct)^2 across all partners>",
  "primary_source": {
    "name": "UN Comtrade",
    "hs_code": "1001",
    "reporterCode": 566,
    "flowCode": "M",
    "url": "https://comtradeapi.un.org/data/v1/get/C/A/HS?reporterCode=566&cmdCode=1001&period=<year>&flowCode=M",
    "as_of": "<year>"
  },
  "cross_check": [
    {
      "name": "FAOSTAT TCL",
      "element": "5622 Import Value (1000 USD)",
      "scope": "total wheat import value (denominator only)",
      "value_usd": "<...>",
      "url": "https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip",
      "as_of": "<FAOSTAT latest, ~year-1 or -2>"
    },
    {
      "name": "Nigeria NBS Foreign Trade Statistics",
      "scope": "partner/origin split (independent of Comtrade)",
      "value": "<origin breakdown>",
      "url": "<NBS foreign trade report URL>",
      "as_of": "<year>"
    }
  ],
  "agreement_pct": "<Comtrade total vs FAOSTAT total, %>",
  "provenance": "sourced | flag_for_review",
  "existing_foodshield_value": {
    "source_file": "data/comtrade_staples.json",
    "value": "<existing Nigeria wheat shares / HHI>",
    "quality_flag": "legacy_curated"
  },
  "verdict": "confirmed | replace | flag_for_review",
  "note": "Method: HS1001 by partner from Comtrade for the share vector; FAOSTAT confirms the total magnitude; NBS confirms the origin split independently. Watch naira devaluation as a USD-value distortion and re-export hubs as false 'suppliers'. If Comtrade and NBS partner splits diverge >10% or the move shifts Nigeria's FDRS materially, verdict = flag_for_review, not auto-replace."
}
```

`verdict` is the action: **confirmed** (existing shares right — optionally upgrade flag `legacy_curated` → `sourced`), **replace** (existing is stale/wrong — supply corrected shares + HHI + citations), **flag_for_review** (sources disagree or data unavailable — do not auto-change).

---

## 6. Practical notes for the actual run

- At scale, don't hand-roll the requests — use the bundled `scripts/comtrade_pull.py` and `scripts/faostat_pull.py` (they follow `_common.py` envelope conventions and the established HS set, so output drops straight into `comtrade_staples.json`). Read their headers first for the free-tier quota and bulk fallback. The free Comtrade tier is heavily quota-limited (the project pulls only ~19–25 priority importers per run), so get a free API key before a full refresh.
- **Why it probably looks "stale":** Nigeria is a prime case for FX-timing distortion. The naira's devaluations mean a *value-based* concentration figure can swing year to year even if the physical supplier mix barely moved. Pull a recent year, and if the shares look volatile, that's the first thing to check — possibly compute concentration on tonnes if you can get `netWgt` from a keyed Comtrade pull or USDA PSD wheat, to separate FX noise from real supplier shifts.
- **Stop and ask a human if:** Comtrade vs NBS partner splits diverge >10% with no unit/basis/coverage explanation, or the correction moves Nigeria's FDRS materially (concentration feeds three FDRS factors).

Bottom line: pull **Comtrade HS 1001, reporter Nigeria, by partner** for the share vector, compute **top-5 + HHI on value share**, confirm the **total** against **FAOSTAT** and the **partner split** against **Nigeria NBS** (the one source not downstream of Comtrade), reconcile against the ~10% rule, and emit the verification record above with a real `as_of` year. If two independent sources agree, tag it `sourced`; if they don't, flag it — don't guess.
