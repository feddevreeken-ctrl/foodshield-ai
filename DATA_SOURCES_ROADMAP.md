# Data Sources Roadmap

Official sources worth integrating next, ordered by impact on FoodShield's data quality.

## 1. FEWS NET acute food insecurity

Why:

- monthly acute food insecurity maps
- current and historical downloads
- stronger crisis-country coverage than relying only on curated FEWS references

Official references:

- `https://fews.net/data/acute-food-insecurity`
- `https://help.fews.net/fde/v3/export-data`
- `https://help.fews.net/fde/v3/data-sources`

Recommended use in FoodShield:

- add a monthly FEWS overlay alongside IPC
- store country and subnational geometry references
- mark FEWS-derived rows as sourced, not curated

## 2. USDA FAS Open Data / PS&D

Why:

- official production, supply, distribution, imports, exports, and consumption
- better structural commodity grounding than hand-authored import/export lists alone

Official references:

- `https://www.fas.usda.gov/data`
- `https://www.fas.usda.gov/data/production`
- `https://apps.fas.usda.gov/psdonline_legacy/psdAbout_welcome.htm`

Recommended use in FoodShield:

- rebuild commodity dependency baselines from real PSD volumes
- replace heuristic exporter/importer lists for core staples
- attach `as_of_marketing_year` to commodity exposures

## 3. FAO Data Lab daily food-price monitoring

Why:

- daily food price acceleration monitor
- food inflation nowcasting up to the current month
- good bridge when FAOSTAT official CPI lags

Official references:

- `https://www.fao.org/datalab/early-warnings/food-prices/en`
- `https://www.fao.org/prices/en/`

Recommended use in FoodShield:

- add a high-frequency inflation signal separate from annual FAOSTAT CPI
- support an explicit `nowcasted` quality flag for inflation series

## 4. World Bank Logistics Performance Index

Why:

- direct logistics and shipment-friction signal for structural supply-chain exposure
- better evidence for the transport side of trade fragility

Official references:

- `https://datacatalog.worldbank.org/search/dataset/0038649/logistics-performance-index`
- `https://lpi.worldbank.org`

Recommended use in FoodShield:

- fold LPI into the structural supply-chain exposure component
- keep it separate from food availability and inflation so users can see why a country is exposed

## 5. USDA Export Sales Reporting and Query System

Why:

- weekly US export commitments for wheat, corn, soybeans, rice, and more
- useful for near-term pressure on major staple flows

Official reference:

- `https://apps.fas.usda.gov/ESRQUERY/esrq.aspx`

Recommended use in FoodShield:

- optional weekly overlay for US-origin export tightening
- especially useful for wheat, maize, soybeans, and rice watchlists

## 6. FAO FPMA domestic and international price tool

Why:

- broad country coverage for retail and wholesale food prices
- useful for country-level price stress beyond the FFPI aggregate

Official references:

- `https://www.fao.org/prices/en/`
- `https://www.fao.org/giews/food-prices/price-tool/de/`

Recommended use in FoodShield:

- add country-level staple retail price stress
- distinguish global benchmark prices from domestic market stress

## Implementation principle

Any new source should land with:

- raw snapshot file in `data/`
- documented parser in `scripts/`
- `source_manifest.json` coverage and health rules
- per-field provenance where possible
- explicit labeling in the UI as `sourced`, `manual`, `modeled`, or `nowcasted`
