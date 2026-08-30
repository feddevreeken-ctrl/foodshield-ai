# FoodShield AI — `data/`

Auto-generated JSON snapshots used by the frontend.

## Core overlays

| File | Purpose | Typical cadence |
|---|---|---|
| `nowcast.json` | per-country live adjustment layer | daily |
| `countries.json` | canonical structural country overlay + per-field provenance | on pipeline rebuild |
| `country_caloric_shares.json` | FAOSTAT Food Balance Sheets caloric shares used to source `w/r/m` | daily fetch / annual upstream |
| `source_manifest.json` | per-source health and freshness summary | daily |
| `fao_ffpi.json` | FAO Food Price Index monthly series | daily fetch / monthly upstream |
| `worldbank_pink_sheet.json` | World Bank commodity benchmark prices | daily fetch / monthly upstream |
| `worldbank_wdi.json` | reference macro and food-security indicators | daily fetch / annual upstream |
| `places.json` | map label gazetteer — capitals, regional capitals and cities for the Global Map's zoom-tiered labels (Natural Earth 50m, public domain). Static reference data: built by `scripts/build_places.py`, deliberately NOT in `run_all.py`, rebuild only on a new Natural Earth edition. | on Natural Earth release |

## Food-security and crisis feeds

| File | Source |
|---|---|
| `wfp_hungermap.json` | WFP HungerMap LIVE |
| `wfp_country.json` | WFP HungerMap per-country |
| `ipc.json` | IPC via HungerMap mirror |
| `reliefweb_alerts.json` | ReliefWeb |
| `acled.json` | ACLED |

## Market, weather, and environment feeds

| File | Source |
|---|---|
| `eurostat_food.json` | Eurostat food HICP |
| `faostat_food.json` | FAOSTAT food CPI |
| `openmeteo.json` | Open-Meteo weather anomalies |
| `openmeteo_flood.json` | Open-Meteo river flood anomalies |
| `usgs_water.json` | USGS Water Services |
| `openaq.json` | OpenAQ |
| `nasa_firms.json` | NASA FIRMS |
| `comtrade_staples.json` | UN Comtrade Plus import-side bilateral structure |
| `comtrade_exports.json` | UN Comtrade Plus export-side bilateral structure |
| `commodity_flows.json` | FoodShield commodity-flow atlas source of truth |
| `trade_rebuild_priorities.json` | Generated country-by-country trade rebuild queue |
| `companies.json` | Company footprint / commodity exposure surface |

## Manual snapshot

| File | Source | Notes |
|---|---|---|
| `feeding_america_states.json` | Feeding America Map the Meal Gap | manual annual update |

## Envelope format

All files use the same wrapper:

```json
{
  "_meta": {
    "generated_at": "...",
    "source": "...",
    "notes": "...",
    "version": "v21"
  },
  "data": { ... }
}
```

## Important

- A file existing does **not** mean the source is healthy.
- The frontend should treat `source_manifest.json` as the authority for health, freshness, and setup state.
- `countries.json` is the preferred structural baseline for the frontend. The embedded `COUNTRIES`
  array in `index.html` is now fallback-only.
- `comtrade_staples.json` and `comtrade_exports.json` are the primary bilateral trade backbones for
  the country trade cards; `countries.json` stores the normalized display-facing envelopes built from them.
- `commodity_flows.json` is the atlas layer. Sidecar files like `commodity_flows_wheat.json` are reviewed
  inputs, not the final merged truth.
- Some files intentionally exist as empty or degraded stubs so the workflow never hard-fails on one missing upstream.
