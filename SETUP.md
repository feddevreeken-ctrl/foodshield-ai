# FoodShield AI — Setup & Daily Data Refresh

This repo includes a GitHub Actions workflow that pulls fresh data from 8 public sources every day at 06:00 UTC and commits the result to `data/`. Vercel re-deploys automatically.

## One-time setup

### 1. Push the new files
First push of v19 adds:
- `scripts/` — Python refresh scripts (one per data source)
- `.github/workflows/refresh-data.yml` — daily cron workflow
- `data/` — JSON snapshots loaded by `index.html`

### 2. Register for API keys (only ACLED + Comtrade are gated)

The other 6 sources need nothing — they're fully public.

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
