# Authoritative trade-data sources — catalogue

For each: what it's best at, how to access it free, the endpoint pattern, and known
gotchas. Ordered by how often you'll reach for it in FoodShield work.

## 1. FAOSTAT — food & agriculture, all countries, long series
- **Best for:** broad food/agriculture trade by country, 245+ countries/territories, 1961–latest.
- **Domains you want:** TCL (Trade — Crops & Livestock; import/export value + quantity),
  FBS (Food Balance Sheets; caloric supply, the basis for diet shares).
- **Free access:** bulk normalized ZIPs, no auth, no rate limit:
  `https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip`
  The v1 query API is unreliable for item/element filtering — prefer the bulk download.
- **Element codes:** 5622 Import Value (1000 USD), 5922 Export Value (1000 USD),
  5610 Import Quantity (t), 5910 Export Quantity (t). Item 1842 = Food Total, 1841 = Agri Total.
- **Gotcha:** FAO ships dual Item-Code columns (legacy numeric + CPC string) and drifts
  item-name casing between releases. Match item NAME normalized (lowercase, strip punctuation)
  as the primary matcher, codes as fallback. This is exactly what broke `net_food_trade`.
- **Recency:** annual upstream, typically 1–2 years behind current (latest ≈ 2023 in mid-2026).
- Already in the project: `refresh_faostat_fbs.py`, `refresh_net_food_trade.py` (+ the FIXED variant).

## 2. UN Comtrade — the bilateral gold standard
- **Best for:** detailed import/export by reporter × partner × commodity. The authoritative
  cross-check for "who supplies whom." Use HS codes (see hs_codes.md).
- **Free access:** the public preview / free tier of `comtradeapi.un.org` — quota-limited
  (handful of calls; the project pulls ~19–25 priority importers per run because of this).
  Sign up for a free API key to raise the quota.
- **Endpoint pattern (v1):** `https://comtradeapi.un.org/data/v1/get/C/A/HS?reporterCode=&partnerCode=&cmdCode=&period=&flowCode=M`
  flowCode M=import, X=export; period=year (e.g. 2024); cmdCode=HS code.
- **Gotcha:** the public preview returns `primaryValue` in **raw USD** (not millions) and
  usually omits `netWgt` (so tonnes are null). The project's field is named `total_usd_m`
  but holds raw USD — don't double-convert. Re-exports can inflate some partners.
- Already in the project: `refresh_comtrade.py` → `comtrade_staples.json`.

## 3. ITC Trade Map — friendly UI, 220+ countries, 5,300 products
- **Best for:** eyeballing a figure quickly, market-research-style country/product comparison,
  monthly/quarterly/annual flows.
- **Access:** free registration for the web UI; bulk/API access is paid. Use it to sanity-check
  a number before committing to a Comtrade pull, not as a programmatic source.
- **Gotcha:** built on Comtrade + national data, so it's a good confirmation but not fully
  independent of Comtrade.

## 4. World Bank WITS — trade + tariffs + partners
- **Best for:** trade figures with tariff and partner analysis layered on.
- **Access:** free; `wits.worldbank.org`, also a SOAP/REST API. Pulls from UN Comtrade and
  TRAINS (tariffs).
- **Gotcha:** also Comtrade-derived for trade flows — use for the tariff/partner angle, not as
  an independent flow cross-check.

## 5. WTO Statistics — high-level merchandise trends
- **Best for:** broad merchandise-trade summaries, monthly bilateral merchandise trade for many
  economies. Less food-specific.
- **Access:** free; WTO Stats portal + API.
- **Use:** macro context, not commodity-level food verification.

## 6. Eurostat / Comext — the EU authority
- **Best for:** EU member states, monthly, by product × quantity × value × partner. The best
  source for any EU country's food/agri-food trade.
- **Free access:** Eurostat REST API (`ec.europa.eu/eurostat/api`); Comext bulk for detailed flows.
- Already in the project for prices: `refresh_eurostat.py` (HICP food inflation).

## 7. USDA GATS — US agricultural trade
- **Best for:** US agricultural, fish, forest trade, 1989–present, very detailed.
- **Free access:** `apps.fas.usda.gov/gats/` (web + query). Complements USDA PSD (production +
  marketing-year trade in tonnes), which is already pulled by `refresh_usda_psd.py`.
- **Use:** the authoritative source whenever the claim is about US imports/exports specifically.

## 8. OECD Agriculture & Trade — OECD economies + policy
- **Best for:** OECD-country agricultural markets, policy monitoring, trade restrictions.
- **Free access:** OECD Data Explorer (`data-explorer.oecd.org`) + SDMX API.
- **Use:** policy context and OECD-economy cross-checks.

## 9. National statistics offices & customs agencies — single-country authority
- **Best for:** the most authoritative figure for one specific country, especially where the
  global aggregators look thin or lagged.
- **Access:** varies wildly. Search "[country] customs trade statistics" or "[country] national
  statistics food imports exports." Brazil Comex Stat (free API, HS+country level) and US Census
  USA Trade Online are good examples.
- **Gotcha:** company/firm-level detail is generally NOT free here (see REVIEWER_FEEDBACK_RESPONSE.md
  §4d) — these give country-level flows, which is what we want for FoodShield anyway.

## 10. Ministries of Agriculture / Trade — policy reports
- **Best for:** crop/livestock/fisheries reports, food-security and trade-policy framing.
- **Use:** qualitative corroboration alongside the customs/statistical numbers, not as the
  primary figure.

## 11. EU Agri-food Data Portal — EU agri-food trade, prices, quotas
- **Best for:** EU agri-food imports/exports, prices, production, tariff-rate quotas, by member state.
- **Access:** free; `agridata.ec.europa.eu`. Complements Eurostat COMEXT with agri-specific cuts.
- **Use:** the friendliest EU-specific cross-check for any EU country's food trade.

## 12. OEC (Observatory of Economic Complexity) — visual country/product profiles
- **Best for:** fast visual exploration of a country's export/import basket and partners.
- **Access:** free tier at `oec.world`; built on UN Comtrade.
- **Gotcha:** Comtrade-derived, so a good *visual sanity check* but NOT independent of Comtrade —
  don't treat an OEC figure as a second source when reconciling a Comtrade pull.

## 13. OECD-FAO Agricultural Outlook — projections (not customs data)
- **Best for:** forward-looking commodity/market trends and the 2030-outlook framing.
- **Access:** free; OECD. Use for the projection narrative, NOT as observed trade.

## Source-tier reminder for reconciliation
Independent cross-checks (use two of these from *different* lineages): FAOSTAT TCL, UN Comtrade,
USDA GATS, Eurostat COMEXT, a national customs office. NOT independent of Comtrade (don't pair
them as "two sources"): ITC Trade Map, WITS, OEC — all Comtrade-derived.

---

## Reconciliation cheatsheet
- FAOSTAT TCL value vs Comtrade value for the same flow/year should agree closely; gaps usually =
  a revision-year difference (FAOSTAT lags) or food-total (1842) vs a single HS line.
- Comtrade import as reported by the importer vs export as reported by the partner can differ
  (CIF vs FOB, ~10–15% is normal) — prefer the importer's import figure for import claims.
- USD-value disagreements during high-inflation/devaluation years are often FX-conversion timing,
  not data error.
- Tonnes (FAOSTAT 5610/5910, USDA PSD) vs USD (Comtrade) are different bases — never compare directly.
