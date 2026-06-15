# FoodShield AI — Changelog

_Newest first. Supersedes V23_CHANGELOG.md (which stopped at v23, early June)._

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
