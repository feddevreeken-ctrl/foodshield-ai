# UI provenance visibility — make legacy vs sourced impossible to miss

_The reliability problem reviewers hit isn't only that data is legacy-curated — it's
that a legacy value and a sourced value look identical on screen. A recent refresh
timestamp on a hand-authored number reads as "fresh and sourced" when it's neither.
This spec closes that gap. It does NOT invent a badge system — index.html already has
one (`provenanceBadge`, ~11902). The job is to make sure it actually renders on the
surfaces a reviewer inspects._

## What already exists (don't rebuild)

- `provenanceBadge(quality_flag)` at index.html ~11902 — renders a coloured pill for
  `sourced` (green), `legacy_curated` ("CURATED · STATIC", amber), `legacy_import_dependency`
  ("CURATED · IMPORT-DEP", red), `modeled` (blue), `manual` (blue).
- `fieldProvenance(c, field)` ~11899 and `provenanceTooltip(c, field)` ~11913 — pull the
  per-field provenance object (source, as_of, method, url, note) for hover detail.
- `chartSourceFooter(sourceLabel, refreshedTime)` ~11929 — the "Updated · Source" footer.

The machinery is good. It's under-applied. This is a labelling completeness pass, not new infra.

## The principle

Every number a reviewer can see should answer, at a glance: is this sourced, or is it a
curated estimate? Two rules:

1. **A `legacy_curated` value must always carry its badge or be visually distinct** from a
   sourced one on the same surface. Never render a bare legacy number next to a badged
   sourced one — that's the exact trap.
2. **A recent refresh time must never imply the underlying value is sourced.** The data-file
   `generated_at` is when the file was written, not when the value was verified. Where a card
   shows freshness, it must show provenance alongside it, or the freshness line must be scoped
   to live feeds only.

## Surfaces to fix (priority order — reviewers see these first)

### 1. Country panel — structural metrics (highest priority)
The country slide-over shows FDRS, the 6 components, w/r/m, net trade, imports/exports,
suppliers. Most are `legacy_curated` today. For each structural metric row, call
`provenanceBadge(fieldProvenance(c, field)?.quality_flag)` next to the value, with
`title="${provenanceTooltip(c, field)}"` on hover. Where a whole panel is legacy, add one
honest header line: "Structural baseline: curated estimate, re-verification in progress"
rather than badging every row, to avoid clutter — but the FDRS headline number itself must
carry a badge. Find the country-panel render (grep `openPanel` / the panel metric rows).

### 2. Data table / "all countries" view
If there's a sortable data table, add a provenance column or per-cell badge for the
structural columns. A reviewer scanning the table is the most likely to spot "these all
look the same" — so this is where granularity honesty matters most. A small legend
("● sourced ● curated estimate") at the top of the table.

### 3. Commodities tab — observed vs modeled (already partly done)
The Commodities drilldown already separates "Observed bilateral trade · obs · comtrade"
(green, ~25184) from "Modeled dependency / exposure" (amber, ~25203). Good — this is the
pattern to replicate elsewhere. Just verify the modeled section's badge is as prominent
as the observed one and the language says "modeled," not implying observation.

### 4. Companies tab — see COMPANY_DATA_FIX_SPEC.md
Covered separately: the root bug is `build_companies.py` granting a SOURCED badge to
`partial` files, plus list/callout views rendering modeled data with no badge at all. Apply
that spec's `companyProvBadge()` (SOURCED green / CITED·PARTIAL amber / MODELED orange).

### 5. Methodology + Data Status pages
Already mostly honest (24/33, degraded flags). One addition: state plainly that the
structural layer is currently ~84% curated estimate and that re-verification is underway
(ties to BASELINE_REVERIFICATION_SPEC.md). Honesty about the gap is more credible than
hoping reviewers don't notice it.

## The freshness-vs-provenance fix

Audit every place that calls `chartSourceFooter(...)` or shows an "Updated X ago" line. For
any card backed by structural (`countries.json`) data rather than a live feed, either:
- append the provenance badge to the footer, or
- change the footer text from "Updated 2h ago" to "Source file refreshed 2h ago · values
  curated" so the timestamp can't be read as value-freshness.
Live-feed cards (prices, weather, disturbances) keep the plain freshness footer — there the
timestamp IS the value's recency.

## Implementation notes

- **Source-of-truth rule:** `foodshield-v21.html` must equal `index.html`. Edit one, then
  `cp foodshield-v21.html index.html` (or vice-versa), `diff -q` to confirm, JS-syntax-check
  the inline `<script>` blocks before any push.
- These are additive render-layer changes — no data files change, no FDRS values change, so
  this is low-risk and can ship independently of the data work.
- Stage per-surface and screenshot each (the country panel, the data table) so you can show
  reviewers the before/after — it directly answers "how do I know what's sourced."

## Why this matters more than it looks

The badges turn the project's biggest weakness (84% legacy) into a credibility *display*:
a reviewer who sees honest "CURATED · STATIC" tags trusts the green "SOURCED" tags far more
than if everything were unlabelled. The honesty system only earns trust if it's visible.
