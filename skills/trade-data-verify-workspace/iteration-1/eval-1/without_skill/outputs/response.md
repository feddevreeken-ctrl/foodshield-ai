# Putting Vietnam's rice exports on a sourced footing

## 1. What we have now (the thing we're replacing)

The legacy curated record for Vietnam (`VNM`) in `legacy/foodshield-data-v4.js` is a
hand-written guess, not a sourced number:

- `net:5500` — a net food-trade balance in USD millions, no citation, no year.
- Prose: *"Vietnam is among the world's top 3 rice exporters and broadly food self-sufficient."*

Notice what's missing: there is **no actual rice-export quantity** in the legacy entry at
all. The "top 3 exporters" line is directionally correct but unfalsifiable as written —
it's a vibe, not a figure. If a reviewer asked "how much rice does Vietnam export, and says
who?", the legacy data can't answer. That's the gap we're closing.

## 2. The method — how to move a number from "guess" to "sourced"

The same recipe applies to any FoodShield trade figure:

1. **State the exact claim.** Not "Vietnam exports a lot of rice" but "Vietnam exports
   *X thousand metric tonnes of milled rice in marketing year Y*." Pin the commodity form
   (milled vs paddy vs husked), the unit (tonnes vs USD), and the period (marketing year vs
   calendar year). Most trade-data disputes are really unit/scope mismatches.
2. **Pick the right authority for that exact claim.** For a country's total exports of a
   staple *in tonnes*, the cleanest authority is **USDA PSD** (production, supply &
   distribution) — it publishes a reconciled production/import/export/consumption/stocks
   balance per country per marketing year. For the *value* and the *who-buys-it* breakdown,
   the authority is **UN Comtrade** (bilateral, by HS code). For the broad food-trade
   *balance*, it's **FAOSTAT** (TCL, item 1842). Rice = HS 1006, milled = HS 100630.
3. **Pull the figure from the primary source, not a summary.** Record the value, the unit,
   the year, the source name, and a source URL.
4. **Cross-check against an independent base.** Tonnes (USDA PSD) and USD (Comtrade) are
   different bases — you never compare them directly, but they should tell a consistent
   *story* (a country that's a top exporter by tonne should show up as a dominant supplier
   by value). If the two sources contradict the *direction* of the claim, stop and dig.
5. **Tag provenance and a quality flag** so the UI and any auditor can see it's sourced, with
   what, as of when.

## 3. Applying it to Vietnam — the sourced figure

**Claim, pinned:** *Vietnam's milled-rice exports, marketing year 2026.*

**Primary source — USDA PSD** (`data/usda_psd.json`, `VNM` → `rice`), already in the
project, refreshed on the WASDE cycle:

| Attribute (rice, milled) | Value | Unit | Year |
|---|---|---|---|
| **Exports** | **8,000** | **1000 MT (= 8.0 million tonnes)** | MY2026 |
| Production | 26,100 | 1000 MT | MY2026 |
| Consumption | 22,400 | 1000 MT | MY2026 |
| Imports | 4,000 | 1000 MT | MY2026 |
| Stocks | 2,197 | 1000 MT | MY2026 |
| `quality_flag` | `sourced` | — | — |
| `source` | USDA PSD | — | — |
| `source_url` | https://apps.fas.usda.gov/psdonline/ | — | — |

**The sourced figure: Vietnam exports ~8.0 million metric tonnes of milled rice (MY2026),
per USDA PSD.**

That single number also sanity-checks the legacy prose: 8.0 Mt of exports against 26.1 Mt of
production confirms Vietnam is a major net exporter and comfortably a top-3 global rice
exporter — so the legacy *story* was right, but now it's backed by a specific, dated,
attributable quantity instead of an adjective.

**Independent cross-check — UN Comtrade** (`data/comtrade_staples.json`): Vietnam shows up as
the dominant rice *supplier* by value on the import side — e.g. it accounts for ~73% of one
priority importer's rice purchases by value, ahead of Thailand (~13%), Pakistan, Myanmar and
China. Different base (USD, bilateral) but a consistent story: Vietnam is the leading exporter
in the regional rice trade. The two sources corroborate rather than contradict, so the figure
holds.

(One caveat on Comtrade: its public-preview `primaryValue` is raw USD and usually omits weight,
so it gives you value and supplier-share, not a clean total tonnage — which is exactly why USDA
PSD is the right primary source for the *quantity* claim and Comtrade is the cross-check.)

## 4. What to change in the data

Replace the legacy curated Vietnam entry's reliance on the uncited `net:5500` prose with the
USDA PSD record that's already present in `data/usda_psd.json`. Concretely, for Vietnam rice:

- **Value:** 8,000 thousand MT exports (MY2026)
- **Source:** USDA PSD
- **Source URL:** https://apps.fas.usda.gov/psdonline/
- **Quality flag:** `sourced` (was effectively `legacy-curated`)
- **As-of:** marketing year 2026

This is a drop-in: the PSD pipeline (`scripts/refresh_usda_psd.py`) already pulls and refreshes
this figure monthly on WASDE release, so once Vietnam's rice export reads from
`usda_psd.json` instead of the hand-written legacy value, it stays sourced and current without
further manual editing.

## 5. One-line summary for the dashboard

> Vietnam milled-rice exports: **~8.0 million tonnes** (MY2026). Source: USDA PSD
> (apps.fas.usda.gov/psdonline). Cross-checked against UN Comtrade supplier-share data, which
> confirms Vietnam as the leading rice exporter in the region. Quality flag: sourced.
