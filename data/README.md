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
| `comtrade_staples.json` | UN Comtrade Plus |

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
- Some files intentionally exist as empty or degraded stubs so the workflow never hard-fails on one missing upstream.
