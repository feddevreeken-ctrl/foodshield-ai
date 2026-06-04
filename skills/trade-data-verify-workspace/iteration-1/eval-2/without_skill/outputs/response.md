# Sourcing and Verifying Nigeria Wheat Import Data by Supplier Country

You're right to be suspicious of stale supplier-concentration figures. Wheat trade to Nigeria shifts a lot year to year, and the supplier mix in particular has moved hard since 2022 (Russia displacing a chunk of what used to be US and Baltic-routed grain). Here's where to get current, authoritative data and how to check that it's actually right rather than just newer-looking.

## Where the accurate data lives

There is no single perfect source. The trick is to triangulate between a few authoritative ones, because each has known biases.

### Primary trade-statistics sources (the ones to anchor on)

1. **UN Comtrade** (comtrade.un.org)
   - The canonical international trade database. Query HS code **1001** (wheat and meslin) for reporter = Nigeria, flow = imports, and you get values and quantities broken down by partner (supplier) country.
   - Free API and web query. This is your backbone for supplier-by-supplier breakdown.
   - Caveat: Nigeria is an inconsistent reporter to Comtrade. In years where Nigeria doesn't report, you fall back to "mirror" data (see verification section).

2. **FAOSTAT** (fao.org/faostat) — Detailed Trade Matrix
   - Element: Import Quantity / Import Value, Item: Wheat. Gives bilateral flows. FAO partly fills gaps using mirror data and its own estimates, so it's good for cross-checking Comtrade and for longer consistent time series.

3. **USDA FAS** — two products worth pulling:
   - **PSD (Production, Supply & Distribution) database** for total Nigeria wheat imports (volume) by marketing year — good for the denominator.
   - **GATS (Global Agricultural Trade System)** and the periodic **GAIN reports** for Nigeria (the "Grain and Feed Annual" / "Grain and Feed Update" for Nigeria). The GAIN reports are written by FAS attachés on the ground and often explain the supplier shifts in plain language with current-marketing-year estimates. These are the best narrative source for "who is actually shipping wheat to Nigeria right now and why."

4. **Nigeria's own customs/statistics**
   - **National Bureau of Statistics (NBS) Foreign Trade Statistics** quarterly reports.
   - **Nigeria Customs Service** data.
   - These are the source-of-truth for what physically cleared Nigerian ports, but they lag and the supplier-country attribution can be muddy (transshipment, re-exports).

### Faster / more current but commercial

5. **IGC (International Grains Council)** — supply-and-demand and trade flow estimates, more current than FAOSTAT.
6. **Trade Data Monitor / S&P Global / Trademap (ITC)** — Trademap.org (free with registration) is essentially a friendlier front-end to Comtrade with mirror data already merged. Trade Data Monitor is paid but very current.
7. **Shipping/customs-feed providers** (e.g., trade-intelligence vendors that parse bills of lading and vessel data) — most current of all for "what's on the water right now," but pricey and need cleaning.

## How to make sure it's actually right

Newer data is not automatically correct. Run these checks before you trust it:

### 1. Use the mirror-data cross-check (most important for Nigeria)
Because Nigeria reports inconsistently, compare **Nigeria's reported imports** against the **sum of exports TO Nigeria reported by the supplier countries**. Pull Russia's, the US's, Argentina's, Canada's, Latvia/Lithuania's reported wheat exports to Nigeria from Comtrade and sum them. If the partner-reported total is wildly different from Nigeria's reported import total, trust the exporter-reported (mirror) figures — exporters generally report more reliably than Nigeria does. A gap of more than ~10-15% is a flag that you're looking at incomplete reporting.

### 2. Reconcile value vs. quantity
Always pull both USD value and metric tonnes. Compute implied unit price (USD/tonne) per supplier and sanity-check against world wheat prices for that year (FAO Food Price Index / IGC). A supplier showing an absurd unit price (e.g., $90/t or $2,000/t) signals a misclassification, a flour-vs-grain mixup, or a data-entry error.

### 3. Lock the HS code and product definition
Confirm you're using HS **1001** (wheat grain), not 1101 (wheat flour). Nigeria imports overwhelmingly grain (its mills process domestically), so a sudden "supplier" that's really a flour exporter means your code or product scope drifted. Mixing grain and flour is a classic way concentration figures go wrong.

### 4. Check the reporting period and marketing year alignment
Wheat is reported by calendar year in Comtrade but marketing year (often July-June) in USDA PSD. Don't compare a calendar-year supplier split against a marketing-year total and call it a discrepancy. Pick one convention and state it.

### 5. Watch for transshipment / port-of-origin vs. country-of-origin
Some data attributes wheat to the last port (e.g., a Baltic or Black Sea transshipment hub) rather than the true growing country. If "Latvia" or "Netherlands" shows up as a top supplier, ask whether that's origin or routing. The GAIN reports usually clarify the true origin.

### 6. Triangulate the top-line before trusting the split
Confirm total Nigeria wheat imports across at least two independent sources (USDA PSD volume vs. Comtrade/FAOSTAT volume) land within a reasonable band. If the total agrees, you can have more confidence in the supplier breakdown. If the totals disagree by a lot, fix that before you touch concentration.

### 7. Recompute the concentration metric yourself
Whatever supplier-concentration figure you report (top-supplier share, HHI, top-3 share), recompute it from the raw partner-level tonnes you pulled — don't inherit a number from a secondary source. Show the underlying table so it's auditable.

## Suggested workflow for your pipeline

1. Pull Comtrade HS 1001, reporter Nigeria, imports, by partner, last 3-5 years (value + qty).
2. Pull the same flow as mirror data: top exporter countries' wheat exports to Nigeria.
3. Pull USDA PSD total import volume + the latest Nigeria GAIN report for context.
4. Reconcile totals across sources; if Nigeria's own reporting is missing/inconsistent for the latest year, default to mirror data and flag it.
5. Compute unit prices per supplier as a sanity check.
6. Recompute supplier concentration from the reconciled partner-level tonnes.
7. Stamp the output with: source(s) used, reporting period/convention, HS code, and whether figures are direct or mirror-derived.

## Quick reality check on the "stale" suspicion
If your current numbers still show a large US/Baltic share and little-to-no Russia, that's a strong tell the data predates ~2022-2023, when Russian wheat surged into West African markets including Nigeria. A current pull should reflect Russia as a major (often the largest) supplier. If it doesn't, the data is almost certainly stale.

---
*Note: I don't have live internet access here, so verify the exact current rankings against a fresh Comtrade/USDA pull. The sources and verification method above are the reliable, current way to get and confirm the data.*
