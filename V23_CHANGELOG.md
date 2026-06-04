# FoodShield AI — V23 changelog: reviewer feedback → what changed

_June 2026. This maps every piece of reviewer/owner feedback to exactly what was
done, the status, and how to verify it. Built on the v23 work; nothing here is
deployed until pushed to `main`._

The throughline held the whole way: **honesty over polish.** Every number is
badged sourced / partial / heritage / modeled / curated. Where data genuinely
isn't available, the UI says so rather than faking it.

---

## A. Reviewer feedback (the economists' review)

### A1. "Make sure sourced data is accurate and recent"
**What they meant:** several sourced figures were stale or wrong.
**What I did:**
- Repaired the **net food trade** pipeline (was returning 0 of 174 countries). Root
  cause: FAOSTAT moved its trade aggregate to the CPC code column (`F1982`); the
  parser now matches it. **Result: 180 countries, 2024 data, sourced.**
- Confirmed the Comtrade values display correctly ($B/$M) — the earlier "Egypt $15B"
  bug was already fixed; the field is mis-named `total_usd_m` but holds raw USD and
  the frontend handles it.
- Added a **freshness SLA** to `qa_checks.py` so any feed older than its cadence is
  flagged, and the data status page now shows live source health (24→26 healthy).
**Verify:** country panel → Net food trade row shows a 2024 sourced value with badge;
`python3 scripts/flag_legacy_countries.py` shows `net` at ~32% legacy (was 100%).

### A2. "Ensure granularity between countries — this was lacking"
**What they meant:** countries blurred together because most structural numbers
were hand-authored estimates, not differentiated per-country data.
**What I did:**
- Measured the gap honestly: structural layer was **83.7% legacy-curated**.
- Built the **trade re-verification pipeline** (`scripts/trade_pipeline/`) and pulled
  real UN Comtrade supplier data. **Result: 101 countries now have sourced supplier
  concentration** (was 19), **34 countries have sourced export destinations** (was 0),
  imports sourced for ~150.
- **Overall legacy ratio fell 83.7% → ~68%** and keeps falling as pulls continue.
**Verify:** `flag_legacy_countries.py` per-field table; Egypt suppliers now show
Russia 73% (real) instead of the legacy 43%.

### A3. "Data is accurate" (company data + import/export numbers were wrong)
**What they meant:** the Companies tab showed wrong company data; per-country trade
numbers were off.
**What I did:**
- **Company data — found a real bug:** `build_companies.py` was badging all 12
  traders SOURCED even though only Nutrien is research-complete. Fixed the gating →
  Nutrien `sourced`, the other 11 `cited_partial`, with honest 3-tier badges
  (SOURCED / CITED·PARTIAL / MODELED) wired through every company view.
- Added a **modeled origin overlay** (USDA PSD top-exporters × each trader's
  disclosed assets) — ranked, qualitative, **no fabricated tonnage or share**.
- **Fixed fabricated trade breakdowns** (the Norway seafood panel): destination
  shares came from a hardcoded array `[35,25,18,12,7,3]` reused for every country,
  and company splits from a formula — now they show real observed shares where
  sourced, or rank-only with no fake numbers where not.
**Verify:** Companies tab → only Nutrien is green SOURCED; Norway exports show ranked
destinations (real % for the 34 sourced countries), salmon companies named without
fake per-destination %.

### A4. "Explain why there is Supply-Chain Exposure in the FDRS formula"
**What I did:** added a methodology explanation — caloric import-dependency alone
misses *volume through chokepoints*; a country can import a small caloric share but
move huge tonnage of one staple through a single port. Supply-Chain Exposure (the
lightest weight) is the correction term that catches this. Also clarified it is
**MODELED** (computed from sourced inputs), not measured — no agency publishes it.

### A5. "Explain the mediators in FDRS" (the weighted components)
**What I did:** the methodology now explains each of the 9 components as a channel
through which a supply shock reaches a country's plates, maps each to its sourced
input, and states plainly that **the weights are reasoned judgment, not regressed —
there is no back-test.** No claim of empirical calibration that doesn't exist.

### A6. "Why is there no economic risk in the FDRS formula?" (the sharpest one)
**What they meant:** a country can have food on shelves but a population that can't
afford it (Sri Lanka 2022) — and the score ignored that.
**What I did — this drove the biggest change, FDRS v2:**
- Added an explicit **Economic Access / Affordability** component (weight 0.12):
  FX depreciation, reserve adequacy, external debt-service, income. It measures
  *structural capacity to pay for / afford food* — never realised prices (those
  stay in Food Inflation, so no double-counting).
- Added a **Grain Reserve Buffer** component (0.06) from USDA stocks-to-use.
- The formula is now 9 components + a bounded **double-bind amplifier** (a country
  that both imports most of its food AND can't pay for it is worse than additive).
- **Result:** FX-fragile / debt-distressed countries rise (Sri Lanka, Pakistan,
  Egypt 61→68); sovereign-wealth-insulated importers fall (Gulf states) — exactly
  the correction reviewers were pointing at. Economic Access is **sourced for 141
  countries**.
**Verify:** any country panel → score composition shows Economic Access + Grain
Reserve Buffer with badges; methodology "Where's the economic risk?" box.

---

## B. Owner requests (Fedde, during the session)

### B1. "Pull import/export data from governments to model companies"
**Done + honest finding:** researched it (§4d of REVIEWER_FEEDBACK_RESPONSE). Free
government customs data is **country-level, not company-level** — no free source
attributes shipments to a named trader (that's paywalled Panjiva/ImportGenius, and
US manifests let big traders redact). So company exposure stays a **MODELED overlay**
built from public production data × disclosed footprint, never claimed as customs-
attributed volume.

### B2. "Create a skill that pulls trade data from authoritative sources"
**Done:** built the **`trade-data-verify` skill** — pulls + cross-checks food trade
against FAOSTAT, UN Comtrade, ITC, WITS, WTO, Eurostat, USDA GATS, OECD, OEC, EU
Agri-food portal, national customs. Includes HS-code reference, a source catalogue
(flagging which sources are Comtrade-derived so they're not used as false
"independent" cross-checks), and `comtrade_pull.py` / `faostat_pull.py` helpers.

### B3. "Harden the FDRS formula and the nowcast with more factors"
**Done:** FDRS v2 above (Option B — both new components + the amplifier + a
non-linear reserves cliff). Sophistication added only where economically justified;
explicitly rejected false-precision options (full CES aggregation, extra interaction
terms). Nowcast verified healthy and honest (flags crisis feeds as down rather than
faking calm).

### B4. "Make sure all data updates automatically every 6 hours"
**Done:** wired every new pipeline into `run_all.py` + the GitHub Actions cron
(`refresh_fx`, the repaired net-food-trade, the new WDI indicators, trade
re-verification, company overlay) and added `qa_checks.py` to the workflow.

### B5. "Bump the version to V23, update branding/data/methodology"
**Done:** version stamped V23 in the data envelope, build script, footer, and
methodology — the dev-history comments left intact (they're change history, not the
release version). The data-status pipeline count is now dynamic so it never goes stale.

### B6. "Make sure changes are consistent throughout — no divergence per country"
**Done:** unified the M49→ISO3 partner-code map into one canonical source
(`trade_pipeline/config.py`) that both Comtrade scripts import, so the same country
resolves identically everywhere. Added partner codes found via diagnostics (USA was
on the wrong code; India/France/Switzerland alternates; small islands), while
correctly dropping non-country aggregates ("Other Asia nes" etc.).

### B7. "Improve the Company profile page" / "it renders small"
**Done:** fixed the layout bug (the detail was squeezed into one grid cell — now
full-width), added a company metadata strip (ownership, HQ, fiscal year, disclosure
year), an evidence-strength summary, a clarity line, and a "← All traders" back link.

### B8. "Get ACLED working / use it throughout"
**Done + honest scoping:** ACLED's API moved to OAuth — rewrote `refresh_acled.py`
for the new flow. Discovered the free tier is **12-month-lagged**, so ACLED is used
as a **structural conflict baseline** (feeds the FDRS conflict component), **not** a
live signal — explicitly gated out of the Live Disturbances feed and the live
nowcast delta, because presenting year-old conflict as "live" would be dishonest.
(Set `ACLED_LIVE=1` if the account is ever upgraded to live access.)

### B9. "Why do exporters gain FDRS when they profit?" (scenario bug)
**Done:** the scenario stress-test raised risk for net exporters during price shocks.
Added a **trade-direction factor** — net exporters of a shocked commodity now see
*reduced or negative* risk (they benefit), importers take the full hit, and a
country's own drought still hurts it regardless of trade direction. Audited all 11
shocks for the same class of bug.

### B10. "Don't show data on Live Disturbances if it's not live"
**Done:** ACLED (lagged) is gated out of the live feed; the nowcast only counts it
when `is_live=true`.

---

## C. What's still open (honest status)

- **Finish the Comtrade pulls** — import `--start 135` (rate-limited last run) +
  remaining export batches (`--flow X`). Each batch lowers the legacy ratio further.
  Runs on the 6h cron once started.
- **ACLED key** — works locally; add `ACLED_EMAIL` + `ACLED_PASSWORD` as GitHub
  secrets (done) and confirm the first cron run populates it.
- **FX history** — fragile-currency windows fill over ~2 weeks of daily runs (auto).
- **Beefmap** — the preview folder isn't connected yet; pending access to learn from it.
- **The standing gap:** a **back-test** would move FDRS from "structured exposure
  indicator" to "validated model." Large infra project; the UI honestly says it
  doesn't exist yet.

---

## D. Files changed in V23 (for the commit)

**New scripts:** `refresh_fx.py`, `qa_checks.py`, `flag_legacy_countries.py`,
`build_company_overlay.py`, `_probe_net_food_trade.py`, and the
`scripts/trade_pipeline/` package (config / pull / merge / build_fields).
**Rewritten:** `refresh_acled.py` (OAuth), `refresh_net_food_trade.py` (CPC fix).
**Modified:** `build_countries_dataset.py` (v2 components + formula), `build_companies.py`
(badge gating), `build_nowcast.py` (ACLED live-gate), `build_source_manifest.py`,
`refresh_worldbank_bulk.py` (+2 indicators), `refresh_comtrade.py` (canonical map),
`run_all.py` + `.github/workflows/refresh-data.yml` (wiring), `validate_data.py`.
**Frontend:** `index.html` (= `foodshield-v21.html`) — FDRS v2 formula, methodology,
scenario engine, 2030 outlook, country profiles, company tab, provenance badges,
trade-panel fabrication fix, version bump.
**Skill:** `skills/trade-data-verify/`.
**Docs:** this file + `REVIEWER_FEEDBACK_RESPONSE.md`, `FDRS_V2_DESIGN.md`,
`FDRS_V2_IMPLEMENTATION_SPEC.md`, `COMPANY_DATA_FIX_SPEC.md`,
`BASELINE_REVERIFICATION_SPEC.md`, `UI_PROVENANCE_SPEC.md`, `DATA_RELIABILITY_PLAN.md`.
