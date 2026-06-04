# Data reliability & accuracy — master plan and run sequence

_June 4 2026. This is the index to everything built in response to the reviewer feedback
(wrong company/trade data, lack of granularity, accuracy, the three methodology questions)
plus the two follow-ups: harden FDRS + nowcast, and add a trade-data verification skill.
Read this first; each item links to its detailed doc. Sandbox writes/verifies code; you run
fetches and git on your Mac._

## What was delivered

**Diagnosis & methodology**
- `REVIEWER_FEEDBACK_RESPONSE.md` — the full audit: what's actually wrong (company tab shows
  modeled-not-cited data; ~84% legacy structural numbers; net_food_trade empty), what's fine
  (Comtrade $B display is correct), the three methodology answers (supply-chain, weights,
  economic-risk gap), §4d on government vs company data, and a ready-to-send reviewer paragraph.

**Reliability gate (ship first — pure correctness, no API risk)**
- `scripts/qa_checks.py` — content-level gate that runs after validate_data.py. Catches silent
  feed-emptying, dropped country counts, duplicate ISOs, stale feeds, the provenance ratio
  rising, missing US-state rows, and crisis-feed false-calm. Verified: 18 pass / 12 warn / 0
  fail on the current snapshot (the warns are sandbox-clock artifacts that clear on your Mac).

**Trade-flow accuracy + granularity**
- `scripts/_probe_net_food_trade.py` + `scripts/refresh_net_food_trade.FIXED.py` — repairs the
  empty net-food-trade pipeline (the single highest-leverage granularity fix: ~174 countries
  get one sourced trade balance). The probe reveals the exact FAOSTAT schema so the fix is
  definitive.
- `scripts/flag_legacy_countries.py` + `BASELINE_REVERIFICATION_SPEC.md` — the worklist and
  process to convert legacy_curated structural numbers to sourced, demo set first. Confirmed
  the legacy ratio is 83.7%.
- **`skills/trade-data-verify/`** — the new reusable skill: pulls + cross-checks food
  import/export figures from FAOSTAT, Comtrade, ITC, WITS, WTO, Eurostat, USDA GATS, OECD,
  national customs. Includes HS-code reference, source catalogue, and `comtrade_pull.py` /
  `faostat_pull.py` helpers. This is the tool the re-verification work runs on.

**Company data**
- `COMPANY_DATA_FIX_SPEC.md` + `scripts/build_company_overlay.py` — fixes the real bug
  (build_companies.py grants SOURCED to partial files; list/callout views render modeled data
  unbadged), defines the promotion path, and adds the honest MODELED footprint overlay
  (USDA-PSD top-exporters × each company's disclosed assets — no fabricated percentages).

**Methodology hardening (design docs for your sign-off — no silent score changes)**
- `FDRS_V2_DESIGN.md` — adds an Economic Access / Affordability component (answers the
  economists' gap). Two options; recommends conservative Option A at 0.10 weight. Egypt
  recomputes 61 → 64. Sourced from WDI + HDI now, FX later.
- `NOWCAST_V2_DESIGN.md` — adds shipping/chokepoint, export-restriction, FX, fertilizer, fuel
  signals within the bounded −10/+35 design; phased by which feeds are already live.

**UI**
- `UI_PROVENANCE_SPEC.md` — make legacy vs sourced impossible to miss using the badge system
  that already exists. Additive, low-risk.

## Suggested run order on your Mac

The standard safe sequence (from the handoff): `rm -f .git/index.lock`, keep
`foodshield-v21.html` ≡ `index.html`, JS-syntax-check before push, `git pull --rebase` before
push (the 6h bot edits data/*.json — keep YOUR version on conflict), verify data counts after.

**Phase 1 — reliability gate (today, no internet needed beyond normal):**
```bash
cd "/Users/fedde/Documents/Claudes Files/Projects/FoodSecurity AI/scripts"
python3 qa_checks.py            # see it pass against current data
python3 flag_legacy_countries.py   # generates the re-verification worklist
```
Then wire qa_checks.py into `.github/workflows/refresh-data.yml` after the validate step:
```yaml
      - name: QA content checks
        run: cd scripts && python qa_checks.py
```

**Phase 2 — fix net_food_trade (highest-leverage data fix):**
```bash
cd "/Users/fedde/Documents/Claudes Files/Projects/FoodSecurity AI"
python3 scripts/_probe_net_food_trade.py        # ground-truth the FAOSTAT schema
cd scripts && python3 refresh_net_food_trade.FIXED.py && cd ..
python3 -c "import json; d=json.load(open('data/net_food_trade.json')); print('countries:', len(d['data']))"   # expect ~174
# if good: mv scripts/refresh_net_food_trade.FIXED.py scripts/refresh_net_food_trade.py
# then re-promote net_food_trade.json to 'critical' in scripts/validate_data.py
```

**Phase 3 — company data:**
```bash
cd scripts && python3 build_company_overlay.py && python3 build_companies.py && cd ..
```
Then apply the build_companies.py badge-gating fix and the frontend badge changes per
COMPANY_DATA_FIX_SPEC.md. Promote company files partial→complete in the order it specifies.

**Phase 4 — UI provenance pass** per UI_PROVENANCE_SPEC.md (render-layer only, no data change).

**Phase 5 — methodology (after sign-off):** review FDRS_V2_DESIGN.md and NOWCAST_V2_DESIGN.md,
choose options, then implement. These shift scores, so they ship with recomputed examples and
matching methodology copy in one commit.

**Verification skill** is usable immediately — invoke it whenever a trade figure needs checking
or sourcing; it drives the Phase 2/3 re-verification work.

## The throughline (unchanged)
Honesty over polish. Badge everything. Degrade to low-confidence, never to a fabricated number.
Fix at the source/render layer, never by hand-editing a country. Every new factor maps to a
sourced input. Weights stay reasoned judgment until a back-test exists — and we say so.
