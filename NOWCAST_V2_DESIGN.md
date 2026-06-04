# Nowcast v2 — Design Document: Broadening the Live Shock Layer

_Author: methodology working note. Status: DRAFT FOR OWNER SIGN-OFF. Decision required on §4 (revised cap table + whether the +35 bound moves) and §6 (phasing/priority) before any code lands._

_Scope: this document specifies the **live nowcast layer only** — the bounded delta added on top of the structural FDRS. It does **not** touch the structural formula; that is the separate `FDRS_V2_DESIGN.md`. The two must not double-count, and §3 draws the exact line. Built by `scripts/build_nowcast.py`; output `data/nowcast.json`._

---

## 0. What the nowcast is, and the design constraint

The nowcast is a per-country **delta**, bounded **−10 / +35**, added to the structural FDRS to produce the headline current-conditions score. It is built from 15 sub-signals, each capped at its own maximum, summed, then clamped to the bound. The bound exists so that **live noise can never overpower the structural baseline** — a country with a calm structure cannot be driven into crisis territory by a transient weather or fire blip, and a structurally fragile country cannot be flattered by a quiet fortnight.

Two honesty rules are load-bearing and v2 must not break them:

1. **The bound stays a bound.** The clamp `max(-10, min(35, adj))` is the firewall. Adding signals must not quietly let the live layer dominate structure.
2. **Missing ≠ calm.** A signal with no data is *flagged absent*, never zero-filled into a false zero. The `confidence` flag (`high | monitored | low | none`) carries this. v2 must keep absence visible as it scales to more signals.

Every signal below maps to a **real, free, obtainable** live feed. Where a feed is currently dark, that is stated, and no signal is proposed on a source we cannot actually pull.

---

## 1. Assessment of the current 15 signals

Cross-referenced against `data/source_manifest.json` (snapshot 2026-06-01: 33 sources, 24 healthy). The picture is uneven: **the two highest-cap signals are dark, and three small-cap signals are dark**, while the structural-amplifier signals (which arguably belong more in the structural score) are all healthy.

| # | Signal | Cap | Feed | Manifest status | Verdict |
|---|---|---|---|---|---|
| 1 | IPC pressure | 0–12 | `ipc.json` (HungerMap mirror) | **degraded — empty payload (upstream 404)** | **DARK.** Highest cap in the model, currently contributing nothing. This is the single biggest hole. |
| 2 | WFP consumption (FCS) | 0–6 | `wfp_hungermap.json` | **degraded — empty payload (API 404)** | **DARK.** Second core crisis feed, also down. Both crisis-core feeds are out simultaneously. |
| 3 | Conflict kick | 0–5 | `acled.json` | **setup_required — no API key** | **DARK.** Needs `ACLED_API_KEY` + `ACLED_EMAIL`. A real conflict signal is missing entirely. |
| 4 | Weather extremes | 0–4 | `openmeteo.json` | ok (182/195 countries) | **HEALTHY.** Well-sourced, broad coverage. |
| 5 | FX shock | 0–3 | `wfp_country.json` | **degraded — empty payload (0/195)** | **DARK.** FX rides on the same WFP per-country feed that is down. |
| 6 | Inflation shock | 0–3 | `wfp_country` → `eurostat_food` → `faostat_food` | WFP **down**, Eurostat **ok (30)**, FAOSTAT **down (403)** | **PARTIAL.** Only the Eurostat fallback works → EU-only. Global crisis countries get no inflation read. |
| 7 | Governance drag | 0–2 | `wgi.json` | ok (216) | **HEALTHY** — but slow-moving (see §3, candidate to move structural). |
| 8 | Humanitarian response damp | −2–0 | `reliefweb_alerts.json` | ok (50 events) | **HEALTHY.** Live. |
| 9 | Flood kick | 0–3 | `openmeteo_flood.json` | ok (29 countries) | **HEALTHY** but narrow (only 29–30 flood-exposed countries instrumented). |
| 10 | INFORM amplifier | 0–3 | `inform_risk.json` | ok (191) | **HEALTHY** — but annual/structural (see §3, candidate to move structural). |
| 11 | PSD shortfall | 0–3 | `usda_psd.json` | ok (151) | **HEALTHY** — but the in-code logic is a weak year-on-year/gap proxy, not a true shock (the code comment admits it needs the 5-yr table). |
| 12 | Global price | 0–2 | `fao_ffpi.json` | ok (latest 2026-04) | **HEALTHY.** Clean MoM FFPI read. |
| 13 | Fire over cropland | 0–2 | `nasa_firms.json` | **degraded — empty payload (0/47)** | **DARK.** |
| 14 | US water | 0–2 | `usgs_water.json` | ok (17 gages) | **HEALTHY** but US-only and very narrow. |
| 15 | Air quality | 0–1 | `openaq.json` | **degraded — empty payload (0 from 5000 stations)** | **DARK.** Smallest cap; lowest priority to fix. |

### 1.1 The cap-allocation problem this exposes

Sum of positive caps today = **12+6+5+4+3+3+2+3+3+3+2+2+2+1 = 51**, against a +35 bound. The bound already does real work: it absorbs ~16 points of theoretical over-signal, so no country can stack every signal to the ceiling.

But look at *where the live points actually are right now*:

- **Dark caps:** IPC 12, WFP 6, Conflict 5, FX 3, Fire 2, AQ 1 = **29 of 51 positive cap-points are currently unreachable.** The two biggest signals (IPC + WFP = 18) and the only true conflict signal are all dark.
- **Live caps:** Weather 4, Inflation (EU-only) 3, Governance 2, Flood 3, INFORM 3, PSD 3, Global price 2, US water 2 = **22 reachable points**, and several of those (Governance, INFORM, PSD) are slow-moving amplifiers, not genuine *shocks*.

**Verdict:** the cap allocation still makes sense *in principle* — the crisis-core signals rightly dominate when present, and the bound correctly prevents stacking. But **in the current outage state the live nowcast is being carried by amplifiers and weather, not by crisis or conflict data.** This is exactly why the `confidence` flag matters and must not be weakened: a country can today show a non-trivial positive delta built entirely from INFORM + governance + a dry fortnight, which is `low` confidence, not a crisis call. The honest fix is not to re-cap the dark signals to zero (that would zero-fill calm); it is to (a) restore/replace the dark feeds, (b) add genuinely *live* shock signals that don't depend on the two broken WFP/IPC endpoints, and (c) make confidence scale cleanly (§5).

---

## 2. Proposed new live signals

Each is mapped to a free, obtainable source, with a proposed cap and confidence semantics. These are chosen specifically because they are **real shocks** (fast, event-like) and because most do **not** depend on the currently-broken WFP/IPC endpoints — they widen live coverage even while the crisis-core feeds are dark.

### 2.1 Shipping / chokepoint disruption — **cap 0–4** — `monitored`

- **What it measures:** acute disruption to a maritime grain corridor (Suez, Bab-el-Mandeb/Red Sea, Hormuz, Panama, Bosphorus/Black Sea, Malacca). A blockage or attack-driven re-routing raises landed-grain cost and delays for the importers that depend on that lane.
- **Source (obtainable now):** **ReliefWeb v2** (`reliefweb_alerts.json`, healthy) and **GDACS** (`gdacs.rss` / GDACS API, free, no key) for maritime/transport disaster events; ReliefWeb is *already live in the pipeline*. Parse for chokepoint keywords + affected-country tags. A heavier-but-free option later: UNCTAD/IMF **PortWatch** open API (`portwatch.imf.org`) publishes daily chokepoint transit-count anomalies — a quantitative throughput signal rather than an event flag.
- **Country mapping:** signal applies to net importers whose top suppliers route through the affected chokepoint. We already hold supplier-routing in `comtrade_staples.json` (healthy, top-5 suppliers per importer) — so a Red Sea event lights up the importers sourcing Black Sea/Ukrainian/Russian wheat via Suez, not every country on earth.
- **Cap rationale:** 0–4. Larger than a weather kick because a corridor closure is a genuine multi-week supply shock for a dependent importer, but below the crisis-core signals because it is a channel risk, not a confirmed consumption gap.
- **Confidence:** `monitored` when driven by ReliefWeb/GDACS event flags (qualitative, sourced, current); upgrade to `high` only if/when PortWatch quantitative transit anomalies back it.

### 2.2 Export-restriction / trade-policy shock — **cap 0–3** — `monitored`

- **What it measures:** a new export ban, quota, or licensing restriction on a staple by a major supplier (India's 2022 wheat ban / 2023 rice ban; Russian grain quotas) — a textbook fast disruption to global availability and price.
- **Source (obtainable now):** **IFPRI Food & Fertilizer Export Restrictions Tracker** (free, public; `https://www.ifpri.org/project/food-and-fertilizer-export-restrictions-tracker`). It is a curated bulletin, **not a clean API**, so this enters as a **curated/manual event feed** in the same provenance class as the Feeding America snapshot — a hand-maintained `data/export_restrictions.json` refreshed when IFPRI updates, with explicit `manual` status in the manifest.
- **Country mapping:** the *restricting* exporter gets a small direct flag; the larger kick goes to **importers dependent on that exporter for that staple** (again via `comtrade_staples.json` supplier links). This is the honest direction of the shock — an Indian rice ban hurts West African importers more than India.
- **Cap rationale:** 0–3. A real, sharp availability/price channel, but bounded because its bite depends on the importer's actual exposure (captured via Comtrade), and because the price consequence partly shows up again in the FFPI global-price signal — keep it modest to avoid stacking with §1 #12.
- **Confidence:** `monitored` (curated, sourced, dated event), never `high` — it is a policy flag, not a measured consumption outcome. Each row carries the IFPRI bulletin date so staleness is visible.

### 2.3 FX / currency-crisis — **expand existing FX shock, cap stays 0–3** — `monitored`

- **What it measures:** same intent as today's FX shock (local currency collapse vs USD raises the cost of imported food) but **sourced independently of the broken WFP feed.**
- **Source (obtainable now):** **Frankfurter API** (`api.frankfurter.app`, ECB reference rates, free, no key) or **exchangerate.host** — compute 90-day depreciation vs USD per currency, map currency→ISO3. This is the fallback already named in `FDRS_V2_DESIGN.md` §3.4 and is a small, well-scoped pipeline.
- **Why now:** the current FX signal is **dark** purely because it rode `wfp_country.json`. A free ECB-based feed lights FX back up for ~150+ currencies without waiting on WFP recovery. This is a *restoration*, not a net-new cap.
- **Cap rationale:** keep **0–3**, unchanged. Do not raise it — see §3 on the structural FX overlap.
- **Confidence:** `monitored` (a clean market read is live monitoring, but not a humanitarian-crisis confirmation). The signal carries the FX provider and the rate date.

### 2.4 Fertilizer-price spike — **cap 0–2** — `high`

- **What it measures:** a sharp month-on-month jump in nitrogen/phosphate fertilizer benchmarks. Fertilizer cost is a leading indicator of next-season planting cost and yield risk, especially for import-dependent smallholder economies — the 2021–22 urea/DAP spike is the reference case.
- **Source (in pipeline now):** **World Bank Pink Sheet** (`worldbank_pink_sheet.json`, healthy, latest 2026-04). It **already carries Urea, DAP, and Phosphate rock** with `change_mom_pct` precomputed. No new pipeline — only new logic in `build_nowcast.py` reading series already present.
- **Trigger:** global signal (like FFPI global price), not per-country: e.g. +2 if the max of (urea, DAP) MoM > +15%, +1 if > +7%. Apply the kick to net fertilizer importers (proxy: low-income + high `import_dependency` countries) so it is not flatly global.
- **Cap rationale:** 0–2, matching the global-price signal — it is an input-cost leading indicator, slower-biting than a port closure, so it earns a small cap.
- **Confidence:** `high` — the Pink Sheet is an official, dated, healthy benchmark feed; the reading itself is unambiguous (its *transmission* to food security is the modeled part, which the cap deliberately keeps small).

### 2.5 Fuel / energy-price spike — **cap 0–2** — `high`

- **What it measures:** a sharp move in crude/diesel — diesel drives the entire inland logistics chain (trucking grain from port to market) and irrigation pumping. A fuel spike is a real, fast cost shock to food *access* in landlocked and poor-road economies.
- **Source (in pipeline, one new series):** the **World Bank Pink Sheet workbook** (already pulled) contains **Crude oil, average** in the full historical sheet. The current parser pulls a food+fertilizer subset and does **not** yet include crude. The honest, minimal change is to extend the existing Pink Sheet parser to also capture the crude-oil series (same file, same source, one added `source_code`) — not a new pipeline. (Diesel-specific retail prices per country are *not* freely/cleanly available cross-country, so crude is the honest global proxy; do not claim country-level diesel.)
- **Trigger:** global, like fertilizer: +2 if crude MoM > +20%, +1 if > +10%. Weight toward landlocked / low-LPI countries (we hold `lpi.json`, healthy, 182 countries) so the kick concentrates where logistics cost transmits hardest.
- **Cap rationale:** 0–2 — same tier as fertilizer and global price; a leading cost shock, not a confirmed consumption gap.
- **Confidence:** `high` for the price read (official Pink Sheet); the transmission stays modeled and small-capped.

### 2.6 Signals deliberately NOT added

- **Strategic national reserves:** no clean free cross-country dataset (per `FDRS_V2_DESIGN.md` §3.2). Not sourceable; not proposed.
- **Country-level retail diesel:** no clean free comparable feed. Crude is the honest proxy (§2.5).
- **Social-unrest / news-sentiment scrapers:** not honestly sourceable without a paid/opaque feed; ACLED (once keyed) is the disciplined conflict signal instead.

---

## 3. Keeping the new signals out of the structural Economic Access component

`FDRS_V2_DESIGN.md` adds a structural **Economic Access / Affordability** component (FX volatility, reserves-in-months, debt-service, income). The nowcast must not re-count what now lives there. The dividing line, stated once and applied to every signal:

> **Structural = slow / level.** It measures a country's *standing capacity*: how fragile is its balance sheet and income, as a level, on a multi-month-to-annual horizon. It answers "can this country structurally afford and pay for food?"
>
> **Nowcast = fast / shock.** It measures a *sudden deviation* on a days-to-weeks horizon — an event or a sharp delta. It answers "is something breaking right now?"

Applied to the overlap points:

| Overlap | Structural (FDRS v2) owns | Nowcast v2 owns | The dividing rule |
|---|---|---|---|
| **FX** | The *level* of currency fragility — trailing-window depreciation / volatility as a standing risk, blended with reserves & debt. | The *shock* — a sudden >10% 90-day drop, capped 0–3. | Structural = "this currency is chronically weak"; nowcast = "it just fell off a cliff this quarter." `FDRS_V2_DESIGN.md` §6.2 already commits to this split, and to capping the nowcast FX signal so the two can't double-amplify. **Safe fallback per that doc: if separability is hard, FX lives only in the nowcast** and structural uses reserves+debt+income. Either way, FX is counted once. |
| **Fertilizer / fuel prices** | Not in structural at all. | Owned entirely by nowcast (§2.4/2.5) as global cost shocks. | No overlap — structural has no input-cost term. Clean. |
| **Export restrictions** | Explicitly held *out* of structural (`FDRS_V2_DESIGN.md` §3.3 calls it aspirational for the structural score and points it to the nowcast). | Owned entirely by nowcast (§2.2). | No overlap — structural never ingests it. Clean. |
| **Shipping/chokepoint** | The structural **Supply-Chain Exposure** component is a slow *level* (how exposed is this country's routing in general). | The nowcast owns the *event* — a specific corridor is disrupted *now*. | Structural = "this country is chronically chokepoint-exposed"; nowcast = "the Red Sea is closed this week." Level vs event. Clean. |
| **Governance / INFORM (existing #7, #10)** | These are **annual, structural** by nature and arguably belong in the structural Conflict/Logistics component, where INFORM/WGI already sit. | — | **Recommendation (§5.2): reclassify these as amplifiers with a structural-tilt label, or move them structural in a later pass.** They are the clearest current violators of the "nowcast = fast" rule — they don't move on a weekly horizon. At minimum, never let them alone produce `high` confidence. |

**The one-line test for any future signal:** *does it move on a days-to-weeks horizon in response to an event?* If yes → nowcast. If it is a standing level that updates annually/quarterly → structural. INFORM, WGI, and the income/reserves terms fail this test and belong structural; chokepoint events, export bans, FX shocks, price spikes pass it and belong here.

---

## 4. Revised signal table and the bound math

### 4.1 Proposed v2 table

| # | Signal | Cap (v1→v2) | Direction | Status after v2 plan |
|---|---|---|---|---|
| 1 | IPC pressure | 12 → **12** | + | restore feed (§6) |
| 2 | WFP consumption | 6 → **6** | + | restore feed (§6) |
| 3 | Conflict (ACLED) | 5 → **5** | + | needs API key (§6) |
| 4 | Weather extremes | 4 → **4** | + | live |
| 5 | FX shock | 3 → **3** | + | re-sourced via ECB/Frankfurter (§2.3) |
| 6 | Inflation shock | 3 → **3** | + | live (EU); widens as feeds recover |
| 7 | Governance drag | 2 → **2** | + | live (amplifier; structural-tilt, §5.2) |
| 8 | Humanitarian damp | −2 → **−2** | − | live |
| 9 | Flood | 3 → **3** | + | live |
| 10 | INFORM amplifier | 3 → **3** | + | live (amplifier; structural-tilt, §5.2) |
| 11 | PSD shortfall | 3 → **3** | + | live |
| 12 | Global price (FFPI) | 2 → **2** | + | live |
| 13 | Fire over cropland | 2 → **2** | + | restore feed (§6) |
| 14 | US water | 2 → **2** | + | live (US-only) |
| 15 | Air quality | 1 → **1** | + | restore feed (§6) |
| **16** | **Shipping/chokepoint** | **— → 4** | + | NEW (§2.1) — ReliefWeb live now |
| **17** | **Export restriction** | **— → 3** | + | NEW (§2.2) — IFPRI curated |
| **18** | **Fertilizer-price spike** | **— → 2** | + | NEW (§2.4) — Pink Sheet live now |
| **19** | **Fuel/energy spike** | **— → 2** | + | NEW (§2.5) — Pink Sheet, +1 crude series |

### 4.2 The bound math

- **v1 sum of positive caps:** 12+6+5+4+3+3+2+3+3+3+2+2+2+1 = **51**, with one −2 damp, against a **+35 / −10** bound. Headroom absorbed by the bound: 51 − 35 = **16 points**.
- **v2 sum of positive caps:** 51 + 4 + 3 + 2 + 2 = **62**, same single −2 damp, against the bound. Headroom absorbed: 62 − 35 = **27 points**.

The four new signals add **11** to the positive-cap sum (4+3+2+2). The bound now absorbs 27 points of theoretical over-signal instead of 16 — i.e. the firewall does *more* work, not less.

### 4.3 Should the +35 bound move? **No. Keep −10 / +35.**

Argument for keeping it fixed:

1. **The bound's whole job is to stop live noise overpowering structure.** Widening it as we add signals would defeat the purpose — more signals make over-stacking *more* likely, so the clamp should hold, not relax.
2. **The new signals are channel/cost shocks, not new crisis-core signals.** They should rarely fire simultaneously with a full IPC+WFP+conflict stack; when they do, that country genuinely has a port closure *and* a famine-level IPC reading *and* an export ban — clamping that to +35 is correct, not lossy. +35 already means "about as bad as the live layer can credibly say on its own."
3. **Realistic worst case stays well-bounded.** A plausible severe-but-real co-occurrence (IPC 12 + WFP 6 + conflict 5 + FX 3 + inflation 3 + chokepoint 4 + export 3 = 36) only just exceeds 35 — the bound bites exactly where it should, at genuinely compound crises, and not before.
4. **Honesty:** the structural score is the considered baseline; the nowcast is the fast overlay. Keeping +35 keeps the overlay subordinate, which is the documented contract.

**Decision requested:** confirm −10 / +35 unchanged. (If reviewers insist the live layer should be able to express a more extreme compound shock, the only honest alternative is +40, justified solely by the new chokepoint+export caps; the author recommends against it.)

---

## 5. Confidence-flag upgrades

The current four-tier flag (`high | monitored | low | none`) is sound and must be preserved. As signals scale, refine it as follows so honesty doesn't degrade.

### 5.1 Keep the absence-aware core, extend the tier rules

Current logic: `high` if a crisis-core feed (IPC / WFP / Feeding America) is present; `monitored` if a live food-price feed is present; `low` if only secondary signals fired; `none` if nothing fired. Extend:

- **`high`** — unchanged trigger set, **plus** add the official market shock feeds that are unambiguous and dated: a fertilizer or fuel spike from the Pink Sheet is a *high-confidence reading* (the number is certain; only its food-security transmission is modeled, which the small cap reflects). Allow these to reach `monitored`, **not** `high`, on their own — reserve `high` strictly for crisis-core/affordability feeds and direct price feeds, so the top tier keeps meaning "a confirmed crisis or affordability signal backs this."
- **`monitored`** — broaden to: live food-price feed **OR** a current shipping/export/FX/fertilizer/fuel shock from a sourced, dated feed. This is the right home for the new signals: they are *live, sourced monitoring* but not humanitarian-crisis confirmations.
- **`low`** — only the secondary/amplifier signals fired (weather, flood, governance, INFORM, PSD, US water) with no crisis, price, or trade/market shock feed. **Add: a row whose entire positive delta comes from the slow amplifiers (INFORM + governance) must be capped at `low`** — it must never present as `monitored` or `high`, because those signals don't reflect a current event.
- **`none`** — no live signal at all; the ~0 delta is *absence*, never confirmed calm. Unchanged.

### 5.2 New: per-signal staleness and a structural-tilt tag

As signals multiply, a single country-level flag hides which signals are fresh. Add two refinements:

1. **Per-signal `as_of` dates** in the `signals` block already partially exist (e.g. `food_inflation_source`); extend so every new signal carries its source date (IFPRI bulletin date, FX rate date, Pink Sheet month, GDACS event date). A `monitored` flag built on a 9-month-old IFPRI bulletin is not the same as one built on yesterday's FX read — surface the oldest contributing date as `oldest_signal_age_days` so the UI can demote stale rows.
2. **A `structural_tilt` boolean** on the slow amplifiers (governance, INFORM). When the *only* positive contributors are structural-tilt signals, force `confidence: low` and set `structural_tilt: true` so the UI can say "this reflects standing risk, not a live event." This directly answers the §3 concern that these signals don't belong in a *fast* layer without firing the larger structural-redesign question.

### 5.3 New: a `signal_count` and `dominant_signal` field

With 19 possible signals, expose `n_signals_firing` and `dominant_signal` (the largest single contributor) per country so a reviewer can instantly see whether a +14 delta is one big IPC read or a pile of small amplifiers. This is pure transparency, no logic change, and scales the honesty model to more signals.

---

## 6. Phased plan

Priority order is driven by (a) how big the hole is and (b) how cheap the fix is. The four new signals split sharply: two light up with **no new pipeline**, two need new (small) pipelines, and the biggest wins are *restoring dark feeds*.

### Phase 1 — light up now, zero or near-zero new pipeline (do first)

1. **Fertilizer-price spike (§2.4).** Highest ROI: the data is *already in* `worldbank_pink_sheet.json` (urea/DAP/phosphate with MoM). Pure `build_nowcast.py` logic. Ship immediately.
2. **Fuel/energy spike (§2.5).** Extend the existing Pink Sheet parser to capture the crude-oil series (one added `source_code`, same source/file), then add the nowcast logic. One small parser edit, no new feed.
3. **Shipping/chokepoint (§2.1), event tier.** Parse `reliefweb_alerts.json` (already live) + add free GDACS for maritime/transport events; map to importers via `comtrade_staples.json` (already live). No new credentials. `monitored` confidence.

### Phase 2 — small new pipelines (no keys, free)

4. **FX re-sourcing (§2.3).** Stand up the free ECB/Frankfurter FX pipeline so the FX signal stops depending on the dead WFP feed. Small, no key. Restores a dark 0–3 signal independently of WFP recovery.
5. **Export-restriction tracker (§2.2).** Stand up the curated `export_restrictions.json` from the IFPRI tracker (manual provenance class, like Feeding America). No API, but needs the parse/curation discipline and a manifest entry.

### Phase 3 — restore the dark crisis-core feeds (biggest absolute impact, but partly upstream-dependent)

6. **ACLED conflict (§1 #3).** Just needs `ACLED_API_KEY` + `ACLED_EMAIL` secrets. Cheapest fix for the largest *fully-dark* genuine-shock signal (cap 5). Highest priority within Phase 3.
7. **IPC + WFP HungerMap (§1 #1, #2).** The two biggest caps (12 + 6) and both dark on upstream 404s. Needs either upstream recovery or an alternative IPC/FCS source (FEWS NET `fews.json` is healthy at 29 countries and is a partial IPC-style stand-in — wire it as a supplementary IPC source so IPC pressure isn't fully dark). This is the most impactful restoration but the least within our control.
8. **OpenAQ / FIRMS (§1 #13, #15).** Smallest caps (2 and 1); lowest priority. Restore opportunistically.

### Phase 4 — confidence-model upgrades (ship alongside Phase 1)

9. Implement §5: extend tier rules for the new market signals (cap them at `monitored`), add per-signal `as_of` + `oldest_signal_age_days`, add `structural_tilt` on governance/INFORM, add `n_signals_firing` + `dominant_signal`. This should land **with Phase 1**, not after, so the new signals arrive already honestly flagged.

### Sequencing rationale

The instinct is to chase the biggest caps (IPC/WFP). But those are dark on *upstream* outages we can't fix on demand. The fastest honest win is Phase 1 — three new live shock signals from feeds we already hold — which broadens the live layer *now*, exactly what the economist reviewers asked for, while crisis-core restoration proceeds on its own (upstream-gated) timeline. ACLED (a single key) is the one big-cap restoration fully in our control and should not wait.

---

## 7. Hard rules carried over

Per the handoff: any new source arrives with a raw snapshot in `data/`, a documented parser in `scripts/`, `source_manifest.json` health rules, per-field provenance, and explicit UI badging (live / monitored / manual / modeled). The bound stays −10 / +35. Missing signals stay flagged-absent, never zero-filled. No unsourced number reaches the page.
