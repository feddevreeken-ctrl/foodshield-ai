# FoodShield AI — Setup & Daily Data Refresh

This repo includes a GitHub Actions workflow that refreshes `data/` every day at `06:00 UTC`, rebuilds the nowcast, writes a per-source health manifest, and redeploys the frontend.

## Current feed inventory

The pipeline currently tracks 18 feeds:

- `fao_ffpi.json` — FAO Food Price Index
- `worldbank_pink_sheet.json` — World Bank commodity benchmarks
- `worldbank_wdi.json` — World Bank indicators
- `wfp_hungermap.json` — WFP HungerMap LIVE
- `wfp_country.json` — WFP per-country FX / food inflation / nutrition
- `ipc.json` — IPC via HungerMap mirror
- `feeding_america_states.json` — Feeding America state snapshot
- `openmeteo.json` — weather anomalies
- `openmeteo_flood.json` — river discharge anomalies
- `usgs_water.json` — US river flow anomalies
- `eurostat_food.json` — EU food inflation
- `faostat_food.json` — FAOSTAT food CPI
- `reliefweb_alerts.json` — humanitarian alerts
- `acled.json` — conflict events
- `comtrade_staples.json` — bilateral staples trade
- `openaq.json` — PM2.5
- `nasa_firms.json` — fire detections
- `fews.json` — FEWS NET IPC-style phase outlook (35 crisis countries)

The workflow also generates:

- `nowcast.json` — live per-country adjustment layer
- `source_manifest.json` — truthful source health summary for the UI

## One-time setup

### 1. Push the refresh stack

Make sure the repo includes:

- `scripts/`
- `.github/workflows/refresh-data.yml`
- `data/`

### 2. Public feeds that need no secrets

These work out of the box:

- FAO Food Price Index
- World Bank Pink Sheet
- World Bank WDI
- WFP HungerMap LIVE
- WFP HungerMap per-country
- IPC via HungerMap mirror
- Eurostat food HICP
- Open-Meteo weather
- Open-Meteo flood
- USGS Water Services
- Feeding America manual snapshot

### 3. Feeds that still need configuration

**ReliefWeb**

1. Request an approved `appname` from `apidoc@reliefweb.int`
2. Add `RELIEFWEB_APPNAME` to GitHub Actions secrets

Without it, `reliefweb_alerts.json` is written as a setup-required stub and the nowcast skips ReliefWeb signals.

**ACLED**

1. Register at `https://developer.acleddata.com`
2. Add:
   - `ACLED_API_KEY`
   - `ACLED_EMAIL`

Without both, `acled.json` is written as a setup-required stub.

**FEWS NET FDW**

The script tries unauthenticated public access first. If that returns IPC-style data, no setup is needed. If FEWS NET requires partner credentials (typical), you'll need an FDW account:

1. Request an account from the FEWS NET Help Desk: `https://fewsnet.atlassian.net/servicedesk/customer/portal/2/group/-1`
   - Explain that you're building an open-data food-security dashboard and need read-only API access to IPC-style phase classifications
2. Add two GitHub Actions secrets:
   - `FDW_USERNAME` — your FDW account username
   - `FDW_PASSWORD` — your FDW account password

The script POSTs these to `https://fdw.fews.net/api-token-auth/` to obtain a 12-hour JWT, then uses that token on every `ipcphase` request. Without working credentials, `fews.json` is written as an empty stub with a diagnostic note and the source manifest flags the feed as `setup_required`.

With access, the script fetches current (CS) + 3-month (ML1) + 6-month (ML2) IPC-style phase classifications for ~35 crisis countries every 6h.

**UN Comtrade Plus**

1. Register at `https://comtradeplus.un.org`
2. Subscribe to the API product
3. Add `COMTRADE_API_KEY`

Without it, `comtrade_staples.json` stays in setup-required mode.

**OpenAQ**

1. Register at `https://api.openaq.org/register`
2. Add `OPENAQ_API_KEY`

**NASA FIRMS**

1. Register at `https://firms.modaps.eosdis.nasa.gov/api/`
2. Add `NASA_FIRMS_MAP_KEY`

### 4. FAOSTAT note

`refresh_faostat.py` currently relies on an older guest-token flow that can return `403` as FAO migrates services. This feed is intentionally treated as degradable:

- the workflow keeps running if it fails
- `source_manifest.json` marks it `degraded`
- the frontend falls back to fresher WFP / Eurostat / World Bank inflation inputs

If you want FAOSTAT fully live again, treat it as a separate integration task rather than assuming the legacy guest endpoint is stable.

## Workflow behavior

The daily workflow:

1. checks out the repo
2. installs Python dependencies from `scripts/requirements.txt`
3. runs `scripts/run_all.py`
4. writes fresh JSON snapshots into `data/`
5. rebuilds:
   - `nowcast.json`
   - `source_manifest.json`
6. commits changed snapshots
7. triggers redeploy

## Frontend data contract

The frontend now reads four different classes of data:

- structural baseline data embedded in `index.html`
- live per-country overlays from `data/nowcast.json`
- market benchmarks from `data/worldbank_pink_sheet.json` and `data/fao_ffpi.json`
- feed truthfulness metadata from `data/source_manifest.json`

Important rule: a file existing is not the same thing as a feed being healthy. The UI should always use `source_manifest.json` before describing a source as live or healthy.

## Manual annual source

`scripts/refresh_feeding_america.py` is a manual snapshot of Map the Meal Gap (MMG).

- **Current release**: MMG 2025, data year 2023, published May 14, 2025.
- **Next release**: MMG 2026 (data year 2024), expected late July 2026.

Update procedure when MMG publishes:

1. download the latest state table from `https://www.feedingamerica.org/research/map-the-meal-gap/overall-executive-summary`
2. update the `STATE_MMG` lookup in `refresh_feeding_america.py`
3. bump the `year` field and the source note string
4. rerun the refresh pipeline

The source manifest will label this feed `manual` even when it is current.

## Provenance scaffold (live since v21)

Country structural fields (`fdrs`, `c[]`, `w`, `r`, `m`, `fi`, `net`, `imports`, `exports`,
`suppliers`, etc.) are now generated into `data/countries.json` with per-metric provenance:

```
{
  "_meta": { "...": "..." },
  "data": {
    "countries": {
      "BEL": {
        "w": { "value": 12, "source": "FAOSTAT Food Balance Sheets", "as_of": "2022",
               "source_url": "https://www.fao.org/faostat/en/#data/FBS",
               "method": "caloric share", "quality_flag": "sourced" },
        "imports": { "value": ["Wheat","Rice","Vegetable oil"], "quality_flag": "legacy_curated" }
      }
    }
  }
}
```

Quality flags: `sourced` (verified against a public dataset), `legacy_curated` (hand-authored
heritage value, not yet re-verified), `modeled` (computed from sourced inputs), `manual`
(hand-maintained from an annual release).

The build currently preserves the embedded dataset as a fallback while externalizing it into
`countries.json`. Fields backed by public datasets can be upgraded in-place without changing
the frontend schema.

## Troubleshooting

**The site loads but the source pill is yellow or red**

Open the pill. The manifest dialog will show which feeds are:

- `Healthy`
- `Manual snapshot`
- `Degraded`
- `Setup required`
- `Failed`

**A live panel still shows values when a feed is missing**

That panel should use explicit fallback copy such as `Unavailable` or `static reference`. If it does not, fix the frontend before trusting the number.

**The workflow ran but nothing changed**

Many upstreams are monthly or annual. No diff does not necessarily mean failure.
