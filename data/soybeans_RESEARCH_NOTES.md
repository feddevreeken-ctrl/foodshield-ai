# Soybeans (HS 1201) — research notes

Sidecar dataset: `data/commodity_flows_soybeans.json`. Built to the Beef Map method —
every bilateral flow is `observed` (published/corroborated figure, cited) or `modeled`
(curated route with the basis stated). Whole soybeans only (HS 1201); soybean meal (2304)
and oil (1507) are deliberately excluded so the map stays bean-on-bean comparable to beef.

## Coverage

- **Balances:** 26 countries — BRA, USA, ARG, CHN, PRY, CAN, IND, URY, RUS, EU-27, MEX,
  EGY, THA, JPN, IDN, TUR, TWN, VNM, PAK, BGD, IRN, KOR, UKR, DZA, COL, BOL.
  Covers every material producer, the entire crush belt, and the top ~15 importers.
- **Flows:** 41 bilateral lines.
  - **Observed: 13 (~32%).** Modeled: 28 (~68%).
  - The observed share is deliberately honest. Whole-bean bilateral trade is dominated by
    a handful of giant, well-published flows (Brazil→China, US→China, US→Mexico/EU/Egypt/
    Japan/Indonesia, Paraguay→Argentina), which are all observed. The long tail of
    second-supplier flows into mid-size crush markets has no clean single published figure,
    so those are modeled off USDA PSD country balances + Comtrade partner shares.
- **Rankings:** top 10 exporters + top 10 importers (kt, USDA PSD MY2024/25).
- **Companies:** 6 — Cargill, ADM, Bunge(-Viterra), Louis Dreyfus, COFCO International, Amaggi.
- **Global series:** 2020–2024 production/consumption/exports (kt).
- **Forecast:** to 2030, 26 countries, prod/cons CAGRs inside the OECD-FAO 2024-33 envelope.
- **Scenarios:** 6 — Brazil drought, US-China trade war, Argentina crush disruption,
  China demand drop, Parana/Mississippi logistics, EUDR deforestation tightening.

## Sources used

Existing beef sources reused: `usda`, `usdaPSD`, `oecd`, `cargill`, `itc`, `comtrade`,
`faostat`.

New sources added in `_sources_patch` (11): `usdaOilseeds` (USDA FAS Oilseeds PSD circular),
`usdaPSDsoy` (PSD Online soybean), `abiove` (Brazil oilseed industry assoc.), `ussec`
(US Soybean Export Council MY24/25 review), `secCustoms` (China GACC customs),
`iowafb` (Iowa Farm Bureau / Decision Innovation Solutions Comtrade analysis, Nov 2025),
`adm`, `bunge`, `ldc`, `cofco` (company filings/reports), `soycanada`.

## Key anchored figures (triangulated)

- **China total bean imports ~105 Mt CY2024 (customs); ~109-112 Mt MY24/25 (USDA).**
  Brazil ~74.6 Mt (~71%), US ~22 Mt (~21%) — the two are ~92% of China's imports.
- **Brazil:** crop ~169 Mt (record), bean exports ~110.5 Mt, ~65-71% to China.
- **USA:** crop ~119 Mt, whole-bean exports ~50.8 Mt (USSEC 51.2 Mt); China ~22-27 Mt,
  Mexico 5.21, EU 6.0, Egypt 3.34, Japan 2.17, Indonesia 2.16, Turkey 0.83 (all USSEC MY24/25).
- **Argentina:** crop ~49 Mt but bean exports only ~5 Mt — it crushes ~40 Mt domestically
  and is the world #1 soymeal/soyoil exporter. It is a NET BEAN IMPORTER (~6 Mt from Paraguay/
  Uruguay/Bolivia feed the crush). This is the most counter-intuitive fact in the dataset and
  is flagged in both the balance note and the rankings note.
- **Paraguay** exports ~80% of beans overland to Argentina.
- World production ~420 Mt MY24/25; world bean exports ~181 Mt.

## Figures I am least certain about (flagged honestly)

1. **Marketing-year vs calendar-year mixing.** China's 105 Mt is CY2024 customs; USDA's
   109-112 Mt is MY2024/25. I used ~109 Mt for the China balance and the 74.6 Mt Brazil→China
   line is the CY2024 customs figure. These bases are ~5% apart — fine for a map, but not
   a perfect ledger. Brazil→China + US→China (74.6+22) sum to ~97 Mt against a ~105-109 Mt
   total; the remainder (Argentina, Uruguay, Canada, Russia) is split across modeled lines.
2. **Brazil export total (110.5 Mt).** Sources range 97 Mt (2023/24) to 112 Mt (MY24/25
   USSEC) to 92.5 Mt (CY2024 Comtrade). I used ~110.5 Mt as the MY24/25 figure; defensible
   but the spread is real.
3. **US crop figure.** I used 118.84 Mt (4.37 bn bu, the as-harvested 2024 crop). Some 2025
   revisions quote ~124.9 Mt; I kept the original-season number to match MY24/25 exports.
4. **Second-supplier modeled flows** (Brazil/US splits into TWN, VNM, PAK, BGD, EGY, DZA,
   RUS, COL) are apportioned from USDA PSD import totals × typical Comtrade partner shares.
   Directionally right, individual tonnages ±20-30%.
5. **Company revenues.** ADM $85.5bn and COFCO $38.5bn/108 Mt are firm (filings). Cargill
   ~$160bn and LDC ~$50bn are group-wide, not soy-specific (both private) — flagged `modeled`.
   Bunge-Viterra combined revenue (~$50-60bn) is an estimate; merger closed Jul 2025, first
   full consolidated year not yet reported.
6. **Argentina→China bean volume (3 Mt)** is modeled — most Argentine soy leaves as meal/oil,
   so the whole-bean figure is small and lumpy year to year.

## Reliability caveats for the orchestrator

- Cross-check the China balance import figure (109 Mt) against whichever single basis the
  rest of FoodShield uses (CY vs MY) before merging, to avoid a double standard.
- The Argentina net-importer-of-beans / top-exporter-of-meal duality will look wrong to a
  casual reader; keep the rankings note that explains it.
- If meal/oil are ever added as a `commodities_split` (like beef's fresh/frozen/offal),
  Argentina, the US and Brazil crush volumes already in the balances give the hooks.
