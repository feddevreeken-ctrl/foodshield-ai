# Maize (corn, HS 1005) — research notes

Curated bilateral-trade dataset for FoodShield AI, built with the World Beef Map method.
Output: `data/commodity_flows_maize.json` (sidecar; merge into `commodity_flows.json → commodities.maize`).

## Headline counts

- **Flows:** 50 bilateral routes
- **Observed / modeled split:** 24 observed (48%) / 26 modeled (52%) — an honest mix; no flow lacks a `src` + `note`.
- **Balances (countries covered):** 37
- **Rankings:** top 10 exporters + top 10 importers
- **Companies:** 6 (the grain majors)
- **Scenarios:** 6 cited shocks
- **Global series:** 2020–2024 production / consumption / exports (kt)
- **New source ids in `_sources_patch`:** 14

## Units & vintage

- All tonnages in **kt** (thousand metric tons), product weight, HS 1005 (maize/corn).
- **Balances & rankings:** USDA PSD / FAS Grain **marketing year 2024/25** (the standard spine).
- **Observed bilateral flows:** mostly **ITC Trade Map / Comtrade calendar-year 2024** (HS 1005, via World's Top Exports compilation) plus ANEC/SECEX (Brazil) and IGC/Argus (Ukraine).
- Marketing-year and calendar-year tonnages differ modestly — this is flagged in the rankings `note` (e.g. ITC CY2024: US 62.6 / Brazil 39.8 / Argentina 32.1 / Ukraine 29.6 Mt vs USDA MY2024/25: US 72.5 / Brazil 40 / Argentina 36 / Ukraine 22 Mt).

## Sources used

Existing (from beef's 26): `usda`, `fao`, `wb`, `itc`, `usdaPSD`.

New (in `_sources_patch`):
- `usdaGrain` — USDA FAS Grain: World Markets & Trade (corn circular)
- `usdaPSDcorn` — USDA PSD Online, corn
- `wasde` — USDA WASDE corn tables
- `wtex` — World's Top Exports (compiles ITC Trade Map, HS 1005, CY2024) — backbone of observed bilateral value/tonnage
- `comtrade` — UN Comtrade bilateral HS 1005
- `igc` — International Grains Council Grain Market Report
- `oecdfao` — OECD-FAO Agricultural Outlook 2024-2033 (forecast basis)
- `usgc` — U.S. Grains Council (US export destinations)
- `abiove` — ANEC / Brazil SECEX (Brazilian export destinations) [note: id labeled ANEC/SECEX]
- `adm`, `bunge`, `cofco`, `ldc`, `viterra` — company filings/disclosures

## Anchored facts (verified)

- **Top exporters MY2024/25:** USA ~72.5 Mt (record), Brazil ~40, Argentina ~36, Ukraine ~22; these four are ~85-90% of world trade. France ~4, Paraguay ~3.8, Russia ~3.5, Romania/South Africa/Myanmar ~2.8-2.9.
- **Top importers MY2024/25:** Mexico ~25 (largest single buyer, ~99% from US), EU-27 ~19.5, Japan ~15.5, Vietnam ~12, S.Korea ~11.5, Egypt ~8.5, China ~8 (collapsed from ~23 Mt prior year), Iran ~7, Colombia ~6.7, Taiwan ~4.5.
- **Maize is primarily a FEED grain** — livestock (poultry/pork/cattle) dependence is the dominant demand driver in every major importer; US adds large ethanol use (~140 Mt). Captured in balance notes.
- **Key 2024 structural shifts** (all cited in flow notes): China cut total corn imports ~58% (value); Brazil overtook the US as China's #1 origin and became Egypt's #1 supplier (+240% y/y); Egypt was Brazil's top destination; Vietnam entered the US top-10 (5 kt → 1.1 Mt); India flipped to marginal net importer on ethanol/feed demand; Russian and Ukrainian exports cut by drought/war; Southern Africa El Nino drought.

## Figures I'm less certain about — flagged honestly

1. **Consumption figures** for several countries are USDA "domestic use" estimates rounded to sensible kt; the US split (feed ~140 Mt vs ethanol/FSI ~140 Mt) is approximate. Treated as indicative, not audited line items.
2. **EU-27 as a bloc:** I model EU imports ~19.5 Mt and also list member states (Spain, Italy, Germany, Netherlands, France, Romania, Hungary) individually. There is unavoidable **double-counting risk** between the "EU" node and intra-EU member flows. The EU node is the extra-EU import bloc; member balances mix intra- and extra-EU trade. Flagged for the orchestrator — the frontend should pick one convention (bloc OR members) when summing.
3. **Modeled feed-corn flows to MENA/SE Asia** (Algeria, Saudi Arabia, Malaysia, Peru, Taiwan from ARG/BRA/USA): these markets run competitive tenders where origin share swings year-to-year on freight. Tonnages are USDA-PSD-gap allocations across the known supplier set, not published bilateral figures — labeled `modeled` with the basis stated.
4. **Brazil export destinations** use ANEC/SECEX **Jan–Oct 2024** partial-year figures (the most granular available); full-year totals are modestly higher. Noted in context.
5. **Ukraine bilateral splits** (EU ~11 Mt = "roughly half"; Spain/Turkey/Egypt) blend IGC/Argus commentary with USDA EU-customs aggregates; the ~half-to-EU share is well-established but member-level splits are approximate.
6. **Company sourcing arrays** are directional footprints (where each major originates corn), not audited volume shares — consistent with how beef's company block is built. ADM/Bunge are sourced (SEC filers); Cargill/COFCO/LDC/Viterra are `modeled` (private/limited disclosure), matching beef's treatment of Cargill.
7. **Global series** rounded to the nearest Mt and partly reconciled across USDA PSD (production/consumption) and ITC (trade ~199 Mt CY2024); 2024 = MY2024/25 estimate, will firm up in later USDA revisions.

## Validation

`node -e "JSON.parse(require('fs').readFileSync('data/commodity_flows_maize.json','utf8')); console.log('valid')"` → **valid**.
Programmatic check confirms: 0 flows missing src/note, 0 balances missing src/note.
