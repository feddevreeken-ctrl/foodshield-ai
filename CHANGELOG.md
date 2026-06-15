# FoodShield AI — Changelog

_Newest first. Supersedes V23_CHANGELOG.md (which stopped at v23, early June)._

## 2026-06-15 — Release prep round 2: cold-load count, tour polish
- **Disturbances "101 → 193 on reload" FIXED (v39.1).** Real root cause: on cold load the external feeds (GDACS/ReliefWeb, 6s timeout race) often fail, so the run finishes with baseline+LIVE only (~101); a manual reload gets them HTTP-cached (~193). Added a bounded self-healing background re-fetch (3 attempts at 3.5/7/10.5s) that bypasses the cache and re-renders — corrects to the full set with no manual reload.
- **Commodity-flow tour step now actually renders.** The arcs are placed via getCoord(), which needs map centroids; the step now waits for `Globe.state.centroids.size > 0` (not just the function) and retries up to ~6s, so Wheat corridors draw reliably during the tour.
- **Removed the "Live health for 34 feeds" tour step** (per owner); the Methodology step now closes the tour. 11 steps remain.
- Audit: JS 4/4 valid, CSS 1604/1604, script tags 11/9, TOUR_STEPS balanced (74/74 braces, 131/131 parens), FDRS 11/11, QA 18/0/0. v21 mirror byte-identical.

## 2026-06-15 — Release prep: disturbances fix, tour, repo hygiene
- **Live disturbances initial-load bug FIXED (v39).** Two root causes: (1) `G.showDisturbances` was never assigned, so the post-geojson-ready re-render silently no-opped; now assigned. (2) the retry guard compared against a same-instant `placeable` snapshot (small before centroids load) → no retry fired; now retries until every event places (budget 15 × 400ms). Full set now renders on first paint.
- **Tour: added a "Live commodity flows" step** — pushes global Wheat corridors to the globe (observed=solid green / modeled=dashed blue / tonnage=stroke) right after the per-country Trade Flow Atlas step; cleaned up on advance and on tour-end.
- **Repo hygiene:** untracked 62 `data/*.bak_*` + `data/_*` working files; `.gitignore` extended (data backups, v21 mirror, mobile preview, SECURITY/cleanup files). Made repo private; first clean release pushed.
- **Country-data continuity verified:** 264 entities, perfect countries↔nowcast alignment, 0 missing/out-of-range scores, all 15 row fields populated, 50 US states, 0 null components. validate_data 0 fails, FDRS 11/11, QA 18/0/0.

## 2026-06-15 — Documentation cleanup + faostat_food fix
- Created INDEX.md (doc map), DECISIONS.md (settled choices), this CHANGELOG.md.
- Archived 12 superseded docs into `/archive/`; added SUPERSEDED banners to 7 kept-for-reference docs; stamped the 3 FDRS/nowcast "draft" specs as SHIPPED/AS-BUILT.
- Fixed SETUP.md feed count (18 → 32 scripts).
- **`faostat_food.json` RESOLVED** — now 162 countries. Root causes: the food index is in the **Item** column (Item Code 23013), not Element; and the CP bulk is **monthly-only** (no annual row), so the script now averages months into annual figures. Excludes the "Food price inflation" series (23014) and median/weighted variants. `scripts/refresh_faostat.py` (v38.4) rewritten + unit-tested; `scripts/verify_faostat_food.py` added; populated via `run_all.py` on the Mac.

## 2026-06-13 — v23 fix cycle (multiple sessions)
- Companies tab: `window.LIVE = LIVE` set (provenance helpers were falling back to MODELED); first-load ordering = 12 majors then full index; header copy reworded ("248 cited company–country disclosures across 46 countries"); Bunge en-dash/hyphen collision fixed.
- Chokepoints: surfaced in Live Disturbances feed; structured `transits` enrichment; deterministic click-through analysis; final UI = no standalone toggle (see DECISIONS.md).
- Disturbance bugs: chokepoint clicks (empty-iso path) fixed; 77 events at 0,0 now re-render after map geojson loads; GDACS "[object Object]" formatter.

## 2026-06-12 to 06-14 — Data feeds
- Full refresh via `run_all.py` (38/38 steps); stale_sources → 0; QA 18 pass / 0 warn / 0 fail.
- Script fixes: `refresh_usda_psd.py` (browser UA, was 403), `refresh_worldbank_pink_sheet.py` (header-scan + UA).
- Atlas reached 46 commodities / 4,122 flows / 95.6% observed.

## ≤ 2026-06-05 — see archive/
Earlier history (v1–v23, trade rebuild, FDRS v2 build, launch prep) is in `V23_CHANGELOG.md` and the archived handovers.
