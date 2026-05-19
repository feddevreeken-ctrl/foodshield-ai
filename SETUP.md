# FoodShield AI — Setup & Daily Data Refresh

This repo includes a GitHub Actions workflow that pulls fresh data from 8 public sources every day at 06:00 UTC and commits the result to `data/`. Vercel re-deploys automatically.

## One-time setup

### 1. Push the new files
First push of v19 adds:
- `scripts/` — Python refresh scripts (one per data source)
- `.github/workflows/refresh-data.yml` — daily cron workflow
- `data/` — JSON snapshots loaded by `index.html`

### 2. Register for API keys (some sources need registration)

The following sources need nothing — they're fully public:
- World Bank WDI, FAO FFPI, WFP HungerMap (both global + per-country endpoints)
- Feeding America (static lookup)
- Open-Meteo (weather + flood)
- USGS Water Services
- IPC (now via HungerMap mirror — no key needed since May 2026)

**ReliefWeb** (humanitarian alerts — register an *appname*, no key)
1. Email apidoc@reliefweb.int requesting an appname like "foodshield-ai-fv" (~24h approval)
2. In GitHub secrets: add `RELIEFWEB_APPNAME` = your approved name
3. Without this, the script writes an empty stub and the nowcast just skips ReliefWeb signals

**IPC** (food-security phase classifications)
- No registration required as of May 2026 — we read IPC from WFP HungerMap's mirror
  at `api.hungermapdata.org/v2/ipc.json` (same data, no auth).
- Keep `IPC_API_KEY` as a fallback secret if you want to hit the official endpoint
  when HungerMap's mirror is down. Optional.

**OpenAQ** (PM2.5 air quality — chronic crop & labour stress signal)
1. Register at https://api.openaq.org/register (free, instant)
2. Copy your API key
3. In GitHub secrets: add `OPENAQ_API_KEY` = your key
4. Without this, the OpenAQ script writes an empty stub — no error.

**NASA FIRMS** (active fire detection over cropland)
1. Register at https://firms.modaps.eosdis.nasa.gov/api/ (free, "MAP_KEY")
2. Copy the MAP_KEY emailed to you
3. In GitHub secrets: add `NASA_FIRMS_MAP_KEY` = your key
4. Without this, the FIRMS script writes an empty stub — no error.
5. Free tier: 5000 lines per request, 1000 requests per 10 minutes.

**ACLED** (conflict events — required for the nowcast layer)
1. Register a free account at https://developer.acleddata.com
2. Confirm your email
3. Copy your API key from the dashboard
4. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**
   - `ACLED_API_KEY` = your key
   - `ACLED_EMAIL` = the email you registered with

**UN Comtrade Plus** (bilateral trade volumes — required for accurate trade arcs)
1. Sign up at https://comtradeplus.un.org (free tier: 500 calls/day)
2. Subscribe to the "comtrade - v1" API in the developer portal
3. Copy your subscription key
4. In GitHub: add secret `COMTRADE_API_KEY` = your key

If you skip either step, the refresh workflow will still run — those scripts will simply write empty stubs and the frontend will degrade gracefully (it'll show the structural FDRS without the live adjustment for those signals).

### 3. Enable the workflow
After the first push, GitHub Actions will be enabled automatically. You can verify:
- **Actions tab → Refresh data (daily)** should appear
- Click **Run workflow** to test it manually before waiting for the cron

### 4. (Optional) Adjust the schedule
Edit `.github/workflows/refresh-data.yml` and change the `cron:` line. Default is `0 6 * * *` (06:00 UTC daily).

## What the workflow does

1. Checks out the repo
2. Sets up Python 3.11 + installs `requests`
3. Runs `scripts/run_all.py` which:
   - Fetches each data source (failures are caught individually)
   - Writes each result to `data/<source>.json`
   - Builds the composite `data/nowcast.json` from all signals
4. Commits any changes to `data/` and pushes to `main`
5. Vercel detects the push and redeploys

## How the frontend uses the data

On page load, `index.html` fetches `data/nowcast.json` and applies a per-country adjustment to the structural FDRS:

```
Nowcast FDRS = Structural FDRS + nowcast.json[iso3].adjustment
```

The adjustment is in the range `-10` (active humanitarian response damping shock) to `+25` (extreme IPC + conflict + global price spike). Country panels show both numbers with a "Δ +N from structural" annotation so users see what's moving.

## Updating the manual data source

`scripts/refresh_feeding_america.py` contains a hardcoded lookup of US state food-insecurity %. Map the Meal Gap publishes annually (typically May). When the new release drops:

1. Download the state summary CSV from https://www.feedingamerica.org/research/map-the-meal-gap
2. Update the `STATE_MMG` dict in `refresh_feeding_america.py`
3. Commit + push — workflow will rebuild on next run

## Troubleshooting

**Workflow runs but no data changes are committed**
→ Sources may be temporarily down, or values may legitimately not have moved. Check the workflow logs.

**ACLED returns "rate limit exceeded"**
→ ACLED free tier is 1 call/sec. The script makes one call. If you see this, ACLED is having an outage.

**Comtrade returns 429**
→ You've exceeded the free-tier 500 calls/day. The script makes one call per commodity (4 calls total). If you see this, check whether the workflow ran twice on the same day.

**Vercel deploys but the site looks unchanged**
→ Hard-refresh the browser (Cmd+Shift+R). Vercel CDN may also take ~60s to invalidate.
