# Commodity research spec — the Beefmap method, for every food

_The canonical instructions every commodity subagent follows so the data is reliable,
cited, and structurally identical to `beef` in `data/commodity_flows.json`._

## Goal

Build a curated, cited bilateral-trade dataset for ONE commodity across ALL major
trading countries — exactly like the World Beef Map did for beef. Output is one JSON
object that slots into `data/commodity_flows.json → commodities[<key>]`.

## The non-negotiable reliability rule (carried from beef)

Every bilateral flow is either:
- **observed** — a published or corroborated figure. Cite the source (`src`) and state
  the figure in the `note` (e.g. "China imported 1.33 Mt of Brazilian beef in 2024, US$6bn (ABIEC)").
- **modeled** — no clean published bilateral figure exists, so it's a curated route with
  the BASIS STATED in the note (e.g. "USDA PSD gap-based estimate; Brazil is the dominant
  SE-Asia supplier").

**No flow without a `src` and a `note`. No fabricated percentages — tonnages only.**
Aim for a realistic mix (~40-60% observed is normal and honest). Do not inflate "observed".

## Authoritative sources — the reliability stack (owner-mandated)

**HARD RULE: data must be 2024 or newer. Never use 2023 or earlier.** Where 2024 calendar
data isn't published, use the latest marketing-year 2024/25 figure (USDA PSD). Never downgrade
a flow to a pre-2024 vintage.

Source hierarchy, best first:
1. **UN Comtrade** — the official raw bilateral source (country × partner × year × HS code).
2. **FAOSTAT** — best for food/agriculture-specific commodity definitions (FAO items), production–trade.
3. **CEPII BACI** — research-grade *cleaned* bilateral data (reconciles importer/exporter gaps, HS6).
4. **World Bank WITS** — the practical interface onto Comtrade + tariffs. THIS IS OUR PRIMARY PULL.
5. **ITC Trade Map** — business-facing market interface, good for quick cross-checks.

Plus commodity-specific bodies (IGC for grains, MPOC/GAPKI for palm, ICO for coffee,
ICCO for cocoa, IFA for fertilizer, FAO GLOBEFISH for fish) and company filings.

### How to pull WITS 2024 bilateral tonnages (the proven, working method)

WebFetch this URL pattern — it returns an HTML table of partners with **Quantity in Kg**:
```
https://wits.worldbank.org/trade/comtrade/en/country/{IMPORTER_ISO3}/year/2024/tradeflow/Imports/partner/ALL/product/{HS6}
```
Convert Kg → kt by dividing by 1,000,000 and rounding. Each partner row becomes a flow
`{from:partner, to:importer, value:kt, kind:"observed", src:"wits", note:"WITS/UN Comtrade 2024: ..."}`.

Prefer **importer-reported** data (imports are recorded more accurately than exports; note
Comtrade imports are CIF, exports FOB — a 10-20% gap is normal). The WITS REST API
(tradestats-trade) only returns aggregate partner *shares*, not bilateral tonnages — so the
HTML country page above is the correct endpoint for our per-flow tonnages.

HS6 codes used: wheat 100199 · maize 100590 · rice 100630 (+100640 broken) · soybeans 120100 ·
beef 020130/020230. (Look up the right HS6 for each new food before pulling.)

### Known non-reporters (WITS 2024 returns empty — use USDA/FAO instead, don't fabricate)
Mexico (under-reports), Vietnam, Iran, Iraq, Bangladesh, Yemen, Benin, Ghana, Nigeria
(captures only a fraction of real rice inflow). For these, keep the USDA/FAO-sourced figure
and never replace it with a misleadingly tiny Comtrade number.

Use the `trade-data-verify` skill's discipline: pull the same flow from two independent
sources, reconcile, record provenance + as_of date.

## Output schema (must match beef exactly)

```json
{
  "hs": "1001",                      // HS code(s) for the commodity
  "unit": "kt",                      // kt (thousand metric tons), product weight
  "balances": {                      // per country, latest year
    "USA": { "iso3":"USA","prod":N,"cons":N,"exp":N,"imp":N,"net":N,"net":[N],
             "year":2024,"src":"usda","flag":"sourced",
             "note":"USDA PSD 2024: prod N / cons N / exp N / imp N kt." }
  },
  "flows": [                         // the lines on the map — THE core deliverable
    { "from":"USA","to":"MEX","value":N,"kind":"observed","src":"comtrade",
      "note":"Published bilateral figure + year + source." }
  ],
  "rankings": {
    "exporters":[{"iso":"USA","name":"United States","v":N}, ...],   // top ~10, kt, cited
    "importers":[{"iso":"CHN","name":"China","v":N}, ...],
    "exportSrc":"usda","importSrc":"usda"
  },
  "companies": [                     // major traders, cited (revenue/metric + sourcing footprint)
    { "name":"...","hq":"...","iso":"...","src":"...","flag":"sourced",
      "metric":"Revenue/volume figure (FY2024)","sourcing":["ISO",...],
      "note":"...","improve":"..." }
  ],
  "global": { "years":[...],"production":[...],"consumption":[...],"exports":[...],
              "src":"usda","note":"..." },
  "forecast": { "horizon":2030,"base":2024,"src":"oecd","method":"...","rates":{ "ISO":[prodCAGR,consCAGR] } },
  "scenarios": [
    { "id":"...","label":"...","shockIso":"ISO","severity":0.0-1.0,"note":"cited basis","src":"..." }
  ]
}
```

Also return a `_sources` patch: any NEW source ids used (not already in the 26 beef
sources) as `{ id:{ id,label,short,year,accessed,url } }`.

## Coverage target

- **balances**: every country that materially produces, consumes, or trades the commodity
  (top ~25-40 by trade volume is enough to be comprehensive without padding).
- **flows**: every significant bilateral relationship for the top exporters AND top importers
  — so clicking any major country shows its real partners (like beef now does). Rank by tonnage.
- **rankings**: top ~10 exporters + importers.
- **companies**: the genuine major traders for that commodity (4-8), cited.
- **scenarios**: 3-6 cited, plausible shock scenarios.

## ISO / formatting rules

- ISO3 codes throughout (use "EU" for the EU-27 bloc as a node where appropriate, as beef does).
- Tonnages in kt (thousand metric tons). Round sensibly; don't imply false precision.
- `net` is provided BOTH as a number and as `[number]` (the frontend reads `net[last]`).
- Validate the final JSON parses.

## Deliverable

Write the commodity object to `data/commodity_flows_<key>.json` (a sidecar — the
orchestrator merges it into `commodity_flows.json` after review). Also write a short
`<key>_RESEARCH_NOTES.md` listing: total flows, observed/modeled split, countries covered,
sources used, and any figures you were uncertain about (flag them honestly).
