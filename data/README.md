# FoodShield AI — data/

Auto-generated JSON snapshots from public data sources, refreshed daily by GitHub Actions (`.github/workflows/refresh-data.yml`).

| File | Source | Update cadence |
|---|---|---|
| `wfp_hungermap.json` | WFP HungerMap LIVE (api.hungermapdata.org) | Daily |
| `worldbank_wdi.json` | World Bank WDI (api.worldbank.org) | Daily |
| `fao_ffpi.json` | FAO Food Price Index (CSV) | Daily (monthly upstream) |
| `reliefweb_alerts.json` | ReliefWeb (api.reliefweb.int) | Daily |
| `ipc.json` | IPC Info (api.ipcinfo.org) | Daily |
| `acled.json` | ACLED (api.acleddata.com) — requires API key | Daily |
| `comtrade_staples.json` | UN Comtrade Plus — requires API key | Weekly (rate-limited) |
| `feeding_america_states.json` | Feeding America Map the Meal Gap (manual annual) | Annual |
| `nowcast.json` | Composite of above feeds | Daily (after all others) |

All files share a `_meta` envelope with `generated_at`, `source`, `notes`, and `version`.

To wire your own API keys, see `SETUP.md` in the repo root.
