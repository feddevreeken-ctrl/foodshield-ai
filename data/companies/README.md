# Real company sourcing data — research scaffold

This folder is where verified, *cited* sourcing data lives for each major
commodity trader. The goal is to replace the modeled / illustrative
country-mapping currently used on the "Trader Exposure" tab with genuine
data drawn from each company's own disclosures.

## Why this exists

Cargill, Bunge, ADM, LDC, COFCO and similar privately-held traders do not
publish customs counterparty records. There is no public dataset that says
"Cargill imported 4.2 Mt of wheat from Russia in Q3 2025". So the current
modeled view in the Companies tab approximates exposure based on
publicly-known commodity specialisations.

Some traders DO publish material in their annual reports, sustainability
reports, and CDP / TCFD / ESG filings that gives partial country-of-origin
information — particularly for:

- Soy + palm + cocoa + coffee (deforestation-sensitive supply chains
  where NDPE / NGO pressure has forced disclosure)
- Beef + cattle (EUDR + Brazil-Amazon traceability)
- Wheat / maize for major listed players (ADM, Bunge, Tyson) where
  10-K filings disclose top sourcing regions

## File structure

Each company gets its own JSON:

```
data/companies/cargill.json
data/companies/adm.json
data/companies/bunge.json
...
```

with schema:

```json
{
  "_meta": {
    "company": "Cargill, Incorporated",
    "headquarters": "Minneapolis, US",
    "ownership": "private",
    "fiscal_year_end": "May 31",
    "latest_disclosure_year": 2024,
    "last_updated": "2026-05-21",
    "research_status": "partial | complete"
  },
  "commodities": [
    {
      "name": "Soybeans",
      "evidence_strength": "strong | medium | weak",
      "sourcing_countries": [
        {
          "iso3": "BRA",
          "country": "Brazil",
          "role": "origin",
          "share_pct": null,
          "evidence": "Cargill 2024 ESG report mentions Brazilian soy specifically in the Cerrado deforestation context, lists Brazil as one of top 3 soy-origin countries.",
          "citation_url": "https://www.cargill.com/sustainability/2024-esg-report",
          "citation_page": 47,
          "as_of": "2024"
        }
      ]
    }
  ],
  "evidence_gaps": [
    "No published share breakdowns for grain trading"
  ]
}
```

## Methodology rules

1. **Cite everything.** No claim makes it into a JSON without a publicly-
   accessible source URL (annual report, ESG report, 10-K, sustainability
   report, CDP response, press release).
2. **Evidence strength** is per-commodity:
   - **strong**: company itself publishes country-level percentages
   - **medium**: company names country as a "major" origin without %
   - **weak**: third-party industry analyst (Bloomberg, FT, etc.) names
     the country as a likely origin
3. **No inference from industry-standard assumptions.** "Cargill probably
   sources soy from Brazil because it's a big soy producer" is not enough.
   We need Cargill saying so themselves.
4. **Date-stamp everything.** Sourcing relationships shift; old
   citations carry less weight.

## Status

| Company | Status | Notes |
|---------|--------|-------|
| Cargill | scaffolded | template + first cites populated |
| ADM | not started | listed; 10-K available |
| Bunge | not started | listed; 10-K available |
| LDC (Louis Dreyfus) | not started | private; partial ESG report |
| COFCO | not started | partial English ESG disclosures |
| Wilmar | not started | listed SGX; ESG strong |
| Olam Group | not started | listed SGX; ESG strong (best in class) |
| JBS | not started | listed; meatpacking |
| Tyson Foods | not started | listed; US-focused, 10-K |
| Nutrien | not started | listed; fertilizer |
| Yara International | not started | listed OSL; fertilizer |
| Viterra | not started | private (Glencore + CPPIB); partial |

## Priority order

1. **Listed companies first** — ADM, Bunge, Tyson, Wilmar, Olam, JBS, Nutrien, Yara — 10-K / 20-F filings make the work tractable
2. **Top private players** — Cargill, LDC, Viterra, COFCO — rely on
   ESG / sustainability reports

## How the frontend will consume this

Once a company has `_meta.research_status: "complete"`, a new build script
(`scripts/build_companies.py`) will assemble all the individual JSONs into
a single `data/companies.json` keyed by company name. The frontend
"Trader Exposure" tab will then prefer the real data when available, fall
back to the modeled commodity-overlap when not. A "sourced" badge replaces
the "modeled" badge on completed companies.
