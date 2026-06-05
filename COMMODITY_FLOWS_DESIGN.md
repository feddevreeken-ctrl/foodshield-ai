# Commodity trade-flow data — the Beefmap method, applied to FoodShield

_June 2026. The Trade Flow Atlas kept producing wrong/fabricated numbers because it
generates flows algorithmically from share-arrays + a curated route map, then dresses
modeled estimates as precise percentages. The Beefmap (`/Projects/Beefmap preview`)
solves this properly. This doc captures its method and how we adopt it._

## Why the Beefmap is accurate (and the Atlas wasn't)

The Beefmap doesn't *compute* flows — it **curates and cites each one**. Every
bilateral flow is an explicit, hand-researched entry:

```js
{ from: "BRA", to: "CHN", value: 1330, kind: "observed", src: "abiec",
  note: "China took ~1.33 Mt of Brazilian beef in 2024 (~50% of Brazil's exports), US$6bn." }
```

Each flow carries: a real tonnage (not a share), a provenance tier
(`observed` / `modeled`), a named source key, and a note explaining the figure.
Sources are a registry of named primary references (USDA FAS PSD, RaboResearch,
ABIEC, ITC Trade Map, OEC, UN Comtrade, FAOSTAT, Eurostat…). There are **no
fabricated percentages**: where no clean bilateral figure exists, the flow is
flagged `modeled` and the basis is stated. That's the entire reason it's trustworthy.

The Beefmap also has, per commodity:
- **COUNTRIES** — production / consumption / net-trade per country (3-year series), each `src` + `flag`.
- **FLOWS** — the curated bilateral list above.
- **RANKINGS** — top exporters / importers (real tonnages, cited).
- **COMPANIES** — trader footprints (sourced where disclosed, else modeled — no fake tonnages).
- **COMMODITIES** — HS-code product split (fresh/frozen/live/offal/hides), each a documented share of the base flow or its own route list.
- **GLOBAL / CATTLE / FORECAST / SCENARIOS** — totals, inventory, 2030 projection, stress library.

## The schema we adopt (FoodShield `data/commodity_flows.json`)

One file, keyed by commodity, each commodity holding the Beefmap structure:

```json
{ "_meta": { "version":"v23", "method":"curated cited bilateral flows + Comtrade cross-check" },
  "_sources": { "usda": {label, short, year, url}, "rabo": {...}, ... },
  "commodities": {
    "beef": {
      "hs": "0201+0202", "unit": "kt CWE",
      "balances": { "BRA": {prod, cons, net, src, flag, note}, ... },
      "flows":    [ {from, to, value, kind, src, note}, ... ],
      "rankings": { "exporters": [...], "importers": [...] },
      "global":   {...}, "forecast": {...}
    },
    "wheat": { ... }, ...
  } }
}
```

## Hybrid provenance (owner decision)

- **Authoritative layer = curated cited flows** (the Beefmap way). These are the
  flows the Atlas draws and the panel lists, with the real `value` + `kind` badge.
- **Comtrade cross-check / fill** = where a country/commodity has a Comtrade pull
  (`comtrade_staples` / `comtrade_exports`), use it to (a) corroborate a curated
  `observed` flow, and (b) fill partners the curation doesn't list. Comtrade-derived
  rows are badged `observed (Comtrade)`; curated `observed`/`modeled` keep their tier.
- **Never** show a fabricated percentage. A flow shows its real tonnage/USD when
  sourced; rank-only otherwise. (This is now enforced in the Atlas render code.)

## Rollout (proven incrementally)

1. **Beef first** — import the Beefmap's real beef dataset verbatim into
   `commodity_flows.json`; wire the Atlas to prefer it for beef. Prove the pattern
   end-to-end (accurate flows, honest badges, no fabrication).
2. **Validate** — confirm beef renders right, then template the structure.
3. **Expand** — staple commodities (wheat/rice/maize/soy) next, each researched the
   same curated-cited way (USDA PSD bilateral + FAOSTAT + Comtrade), subagent per
   commodity; then palm/sugar/coffee/cocoa/fertilizer.
4. **Consistency** — every surface that shows trade (Atlas, country panel, commodity
   drilldown, companies overlay) reads from this one file, so the same numbers appear
   everywhere. No more divergent per-surface generators.

## The honesty rule (carried from FoodShield + Beefmap)

Every flow is `observed` (a published/corroborated figure) or `modeled` (a curated
route with the basis stated). Tonnages and USD are real; shares are shown only when
sourced. Where data genuinely doesn't exist, the flow is absent or rank-only — never
invented. This is what makes the map defensible to reviewers.
