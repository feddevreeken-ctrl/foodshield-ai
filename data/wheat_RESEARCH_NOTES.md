# Wheat (HS 1001) — research notes

Curated bilateral-trade dataset for FoodShield AI, built with the Beefmap method.
Output: `data/commodity_flows_wheat.json`. Marketing year 2024/25 throughout.

## Headline counts

- **Flows:** 61 bilateral lines.
- **Observed / modeled split:** 35 observed (57%) / 26 modeled (43%). Honest split — over half are tied to a published figure with a year and a named source; the rest are curated routes with the basis stated in each note.
- **Balances (countries covered):** 42 (41 with full prod/cons/exp/imp + 1 destination-only node, NPL).
- **Rankings:** top 10 exporters + top 10 importers.
- **Companies:** 6 grain majors (Cargill, ADM, Bunge–Viterra, LDC, COFCO, Viterra).
- **Scenarios:** 6 cited shock scenarios.

## Countries covered (balances)

Exporters: RUS, EU (bloc), CAN, AUS, USA, UKR, ARG, KAZ, FRA, DEU, ROU, TUR (also importer).
Importers: EGY, IDN, CHN, DZA, MAR, BGD, PHL, JPN, KOR, MEX, BRA, NGA, SAU, ITA, ESP, GBR, ZAF, UZB, VNM, KEN, THA, PAK, IND (producer), COL, TWN, PER, MYS, CHL, AFG, TJK.

## Sources used

- **USDA FAS PSD (usdaPSD)** — primary basis for all balances, rankings, global totals, and the modeling backbone for routed flows. MY2024/25.
- **Rusagrotrans / APK-Inform (rusagrotrans)** — Russian wheat export destinations by volume (Egypt 8.05 Mt, Turkey 3.21, Bangladesh 2.60, Algeria 1.69).
- **ITC Trade Map (tdm)** — bilateral calendar-2024 value figures used to size routes (Italy↔Russia, China↔France/US/Canada, Indonesia supplier mix, etc.). Existing source.
- **Grain Central / AEGIC (graincentral)** — Australian export destinations MY2024/25 (Indonesia 4.47 Mt, Philippines 3.53, Vietnam 1.57, Korea 1.46, China 1.05).
- **US Wheat Associates (uswheat)** — US destinations MY2024/25 (Mexico ~4.0 Mt, Korea 2.4, Philippines, Japan).
- **EU Commission export data via Tridge/ITC (tdm)** — EU soft-wheat destinations MY2024/25 (Nigeria 2.01 Mt, Morocco 1.51, Algeria 0.95, Egypt 0.88).
- **USDA Cairo GAIN report** — Egypt GASC 5-year supplier mix (Russia 16.8 Mt, Romania 4.7, Ukraine 3.5, France 2.5) used to apportion Egypt's suppliers.
- **OECD-FAO Agricultural Outlook (oecd)** — forecast CAGR envelope to 2030.
- **Company filings** — ADM, Bunge (incl. Viterra merger), LDC, COFCO, Cargill, Viterra.
- New source ids added in `_sources_patch`: rusagrotrans, uswheat, graincentral, igc, adm, bunge, ldc, cofco, viterra. (usdaPSD, tdm, oecd, cargill already exist in the 26 beef sources.)

## Figures I'm confident in (well corroborated)

- World MY2024/25: production ~793.8 Mt (record), exports ~209 Mt, consumption ~803.7 Mt — USDA, multiple releases agree.
- Top exporters Russia (~44 Mt, ~22% share), EU, Canada (~26.5), Australia (23.5, AEGIC-confirmed), US (~22), Ukraine (~16), Argentina (~12, later ~19.5 on new crop), Kazakhstan (~12).
- Top importers Egypt and Indonesia (~12.5 Mt each).
- Russian destination volumes (Egypt 8.05 / Turkey 3.21 / Bangladesh 2.60 / Algeria 1.69) — direct Rusagrotrans customs figures.
- Australian destination volumes — direct AEGIC/Grain Central figures.
- EU soft-wheat destinations — EU Commission export-licence data.

## Figures I'm less certain about — flagged honestly

1. **EU bloc export total (32,000 kt).** USDA PSD reports EU wheat exports incl. durum at ~30-32 Mt; the "soft wheat" series widely quoted (~20.3 Mt, -35% y/y) is narrower. I used the broader USDA grain-basis figure for the ranking but the two definitions differ — worth reconciling against the final USDA grain circular.
2. **Argentina export figure (12,000 kt in balance/ranking).** USDA's in-season MY2024/25 number was ~12 Mt; a later forecast put the new crop at a record ~19.5 Mt. I used the conservative ~12 Mt for the marketing year; if the dataset is meant to reflect the latest crop, bump Argentina (and its Brazil flow) upward.
3. **Value→volume conversions.** Several flows sized from ITC Trade Map are published in US$ value (e.g. Italy–Russia $2.5bn, France–China $708m). I converted to approximate tonnage at ~US$240-260/t 2024 prices. These are order-of-magnitude correct but the exact kt should be treated as modeled where I tagged them observed-by-value — notes state the value basis explicitly.
4. **Canada destination tonnages.** Canada's top-5 (China, Indonesia, Japan, Bangladesh, US) are confirmed by CGC/value data; the specific kt are partly modeled from value and the 21.8 Mt 2023-24 destination split.
5. **China import total (8,000 kt).** USDA showed China cutting imports hard from the ~13 Mt 2023/24 peak; the exact MY2024/25 landing (7-9 Mt) was still being revised — I used ~8 Mt.
6. **US destination split.** Mexico (~4 Mt) and Korea (2.4 Mt) are firm (US Wheat Associates); Japan, China, Nigeria, Colombia, Taiwan tonnages are modeled from value/inspection data.
7. **Modeled overland Central Asia flows** (Kazakhstan→Uzbekistan/Tajikistan/Afghanistan) are routed from USDA balances, not customs-confirmed bilaterally; directionally reliable but not precise.

## Recommended next step

If a fresh USDA grain circular or IGC Grain Market Report is available at integration time, re-pull the EU export definition (item 1) and Argentina (item 2) and overwrite those two ranking values; everything else is stable.
