# Companies tab — data fix implementation spec

_Written June 4 2026. The reviewers said the Companies tab "shows wrong company
data." This is the verified root cause and the exact fix. Nothing here is pushed;
it is the plan to run on the Mac (the sandbox has no internet and can't push git)._

---

## TL;DR — what's actually broken

There are **two** bugs, and together they produce the "wrong data" the reviewers saw:

1. **`build_companies.py` over-promotes every file to `data_quality: "sourced"`.**
   Lines 155–162 set `data_quality = "sourced"` whenever a file has ≥5 claims and
   at least one `strong`-evidence claim — **ignoring `research_status` entirely**.
   Result: the generated `data/companies.json` marks **all 12** companies
   `"sourced"`, including ADM (`scaffolded`), Viterra (`historical_pre_merger`),
   and the 9 `partial` files. This directly violates the README rule: _"Once a
   company has `_meta.research_status: complete` … the frontend will prefer the
   real data."_ Only Nutrien is genuinely `complete`. So 11 of 12 are flying a
   SOURCED badge they haven't earned.

2. **The list/callout views never badge anything and always render the MODELED
   overlap.** `renderCompanyList()` (index.html ~24085) and
   `renderCompanyExposureCallout()` (~24024) build their numbers from
   `buildCompanyIndex()` (~23969) — the modeled commodity×country overlap — and
   show **no provenance badge at all** on the cards or the "Highest modeled
   exposure" callout. Only `showCompanyDetail()` (~24125) checks `data_quality`.
   So a reviewer scanning the tab sees modeled exposure scores presented as bare
   facts, then clicks in and sees a green SOURCED badge on data that's actually
   `partial`/`scaffolded`. The modeled fallback reads as sourced — exactly the
   failure §1 of REVIEWER_FEEDBACK_RESPONSE.md describes.

The cited files themselves are clean (every claim has a `citation_url`,
`share_pct` is `null` by design). The data is fine; the **gating and the
labelling** are wrong.

---

## 1. Promotion path: `partial` → `complete`

### 1.1 The rule (what must be true to flip a file to `complete`)

A `data/companies/<name>.json` may set `_meta.research_status: "complete"` only
when **all** of the following hold:

| # | Criterion | How to check |
|---|-----------|--------------|
| A | **Every commodity** the company materially trades has at least one `sourcing_countries` entry. No silent gaps; anything not researched goes in `evidence_gaps`, not omitted. | Read the file; cross-check the company's segment disclosures. |
| B | **Every `sourcing_countries` entry has a working `citation_url`** that resolves to a public document (annual report / 10-K / 20-F / ESG report / asset page / reputable trade press). No dead links. | Open each URL. Cargill's Russia entry already models the fix: original URL dead → `note` + trade-press replacement. |
| C | **`as_of` is present on every claim and is ≤ 24 months old** relative to the company's `latest_disclosure_year`, OR the staleness is explicitly called out in `evidence_gaps`. | Scan `as_of`. |
| D | **`evidence_strength` is honest per claim** (`strong` only when the company itself names the country; `medium` = named as "major" without %; `weak` = third-party). | Spot-check against the cited doc. |
| E | **The file reflects the company's CURRENT structure** — post-merger, post-divestiture. Stale corporate structure disqualifies. | See Bunge note below. |
| F | **`latest_disclosure_year` matches the newest filing actually cited**, and `last_updated` is bumped to the re-verification date. | Compare to citation years. |

When A–F hold, set `research_status: "complete"`. That is the **only** signal the
frontend should trust for the SOURCED badge (see §2).

### 1.2 Which of the 12 are closest

From the README status table + reading cargill.json and bunge.json:

| Tier | Companies | Why |
|------|-----------|-----|
| **Closest** | **Nutrien** (already `complete`), **Cargill**, **Wilmar**, **Olam**, **LDC** | High claim counts (12/20/27/34), strong evidence, clean citations. Cargill is the reference-quality file. |
| **Mid** | **JBS** (22), **Tyson** (14), **Yara** (27) | Solid but need a current-filing re-pull; Yara has 2022–25 European closures + Finland divestiture to verify still reflected. |
| **Needs work** | **ADM** (`scaffolded`, 11 claims), **COFCO** (China footprint excluded by design), **Viterra** (`historical_pre_merger` — folded into Bunge July 2025) | ADM must be fleshed out before it earns `complete`. Viterra should **not** be promoted standalone; keep it `historical_pre_merger` and treat it as Bunge reference. |
| **Blocked / stale** | **Bunge** | File is the **pre-Viterra** FY2024 10-K. The Bunge–Viterra combination **closed July 2, 2025**, adding Canada/Australia/Europe/Argentina grain + canola + wheat assets not in the current file. Bunge is also mid-divestiture (sugar JV to bp Q4 2024; NA corn milling to Grain Craft Apr 2025). **Do not promote Bunge until re-pulled against the FY2025 10-K.** The file's own `evidence_gaps` already flags all of this. |

### 1.3 Recommended promotion order (listed companies first — 10-K/20-F make it tractable)

1. **Nutrien** — already `complete`; just re-verify criteria B/E and confirm the
   2025 divestitures are reflected.
2. **ADM** (NYSE) — flesh out from the FY2024 10-K segment + asset map, then promote.
3. **Tyson** (NYSE) — US-dominant, smallest footprint, fastest to complete.
4. **Wilmar** (SGX) — palm/sugar/soy; best-in-class palm coverage already.
5. **Olam** (SGX) — ofi + Olam Agri split; cocoa/coffee strongest.
6. **JBS** (B3/NYSE) — beef/poultry; verify Pilgrim's Pride + Smithfield correction.
7. **Yara** (OSE) — nitrogen/NPK; verify 2022–25 plant closures + Finland divestiture.
8. **Bunge** (NYSE) — **only after FY2025 (post-Viterra) 10-K re-pull.**

Then the private players on ESG reports: **Cargill** (reference file, promote
early if criteria met), **LDC**, **COFCO** (keep China-footprint caveat),
**Viterra** (keep historical, do not promote).

---

## 2. Honest labelling fix (the real bug)

**Goal:** any uncited/modeled company view carries a visible **MODELED /
"illustrative — not company-disclosed"** badge on the tab itself; cited data
carries **SOURCED** with the citation. The badge must be driven by
`research_status == "complete"`, not by the over-permissive evidence heuristic.

> **Project rule:** `foodshield-v21.html` MUST equal `index.html`. Apply every
> change below to **both** files identically (or edit `index.html` then
> `cp index.html foodshield-v21.html`). Verify with `diff` after (§5).

### 2.1 Fix the gating in `build_companies.py` (the root cause)

**File:** `scripts/build_companies.py`, lines **155–162**.

Replace the evidence-only promotion with a `research_status`-gated one:

```python
    # research_status from per-company _meta — the ONLY signal that earns the
    # "sourced" badge (matches data/companies/README.md rule). Files that are
    # partial/scaffolded/historical render as MODELED on the frontend even if
    # they carry strong-evidence claims, until they are re-verified to complete.
    research_status = meta.get("research_status", "scaffolded")
    has_strong = any(cl.get("evidence_strength") == "strong" for cl in claims)
    if research_status == "complete" and len(claims) >= 1:
        data_quality = "sourced"
    elif has_strong and len(claims) >= 5:
        # Strong-but-unverified: cited claims exist and may be shown, but the
        # file hasn't passed §1 criteria. Mark "cited_partial" so the frontend
        # can show the cited country rows WITH a MODELED/PARTIAL badge — never
        # a clean SOURCED badge.
        data_quality = "cited_partial"
    else:
        data_quality = "modeled"
```

This introduces a third state, **`cited_partial`**: the cited rows are real and
worth showing, but the file isn't verified-complete, so it is badged honestly
rather than as fully SOURCED. After the fix, regenerating produces: Nutrien =
`sourced`; the other 10 with strong evidence = `cited_partial`; anything thin =
`modeled`. Re-run `python3 scripts/build_companies.py` to regenerate
`data/companies.json`.

### 2.2 Frontend: badge map + a single badge helper

**File:** `index.html` (and `foodshield-v21.html`). The existing `data_quality`
badge map lives at **~11902–11912** (`provenanceBadge`) and a company-specific
map at **~11907**. Add a company-tab badge helper near the company render
functions (insert just **above `function buildCompanyIndex()` at ~23969**):

```javascript
// v22 honesty pass — single source of truth for the Companies-tab provenance
// badge. Driven by data_quality from companies.json (which is now gated on
// research_status == "complete" in build_companies.py).
//   sourced       → green  SOURCED        (research_status complete, cited)
//   cited_partial → amber  CITED · PARTIAL (cited rows, not yet verified)
//   modeled/none  → blue   MODELED        (commodity-overlap, illustrative)
const COMPANY_PROV = {
  sourced:       { color:'#6ba36b', label:'SOURCED',
                   tip:"Country list drawn from this company's own published, citation-linked disclosures and verified complete." },
  cited_partial: { color:'#c9a957', label:'CITED · PARTIAL',
                   tip:"Cited from the company's disclosures but the file is not yet fully re-verified. Treat as provisional." },
  modeled:       { color:'#c47a3c', label:'MODELED',
                   tip:"Illustrative commodity-overlap — NOT company-disclosed. Modeled from this company's known commodities × importer/exporter countries." },
};
function companyProvFor(name){
  const src = (window.LIVE && LIVE.companies) ? LIVE.companies[name] : null;
  const q = src && src.data_quality;
  return COMPANY_PROV[q] || COMPANY_PROV.modeled;
}
function companyProvBadge(name){
  const p = companyProvFor(name);
  return `<span class="company-prov-badge" title="${p.tip}" `
    + `style="font-family:'Geist Mono',monospace;font-size:8.5px;color:${p.color};`
    + `background:${p.color}1c;border:1px solid ${p.color}55;padding:1px 5px;`
    + `border-radius:3px;margin-left:6px;letter-spacing:.06em;text-transform:uppercase;">`
    + `${p.label}</span>`;
}
```

### 2.3 Frontend: put the badge on the LIST cards (~24096–24121)

In `renderCompanyList()`, the card header at **~24100** currently renders only
`${company}` with no badge. Change the name line to include the badge:

```javascript
            <div class="company-name">${company} ${companyProvBadge(company)}</div>
```

And change the "Exposure Score" label (~24104) so a modeled card never implies a
measured score:

```javascript
            <div style="font-size:10px;color:var(--t3);margin-bottom:4px;">${
              companyProvFor(company).label === 'MODELED' ? 'Modeled Exposure' : 'Exposure Score'}</div>
```

### 2.4 Frontend: badge the callout (~24039–24041)

`renderCompanyExposureCallout()` already says "Highest **modeled** exposure" in
its eyebrow (~24040) — good, but the per-card names have no badge. In the
`scored.map(...)` card at **~24046**, change:

```javascript
          <div class="co-callout-name">${s.name} ${companyProvBadge(s.name)}</div>
```

### 2.5 Frontend: fix the detail-view gate (~24132–24137)

This is where the SOURCED green badge gets shown. Currently:

```javascript
  const sourced = LIVE.companies && LIVE.companies[company];
  if (sourced && sourced.data_quality === 'sourced' && (sourced.country_claims || []).length > 0) {
    renderSourcedCompanyDetail(el, company, sourced);
    return;
  }
```

Change so **both** `sourced` and `cited_partial` route to the cited detail
renderer (so the real cited rows are shown), but the renderer picks the badge
from `data_quality`:

```javascript
  const src = LIVE.companies && LIVE.companies[company];
  if (src && (src.data_quality === 'sourced' || src.data_quality === 'cited_partial')
          && (src.country_claims || []).length > 0) {
    renderSourcedCompanyDetail(el, company, src);   // badge chosen inside, per data_quality
    return;
  }
```

Then in **`renderSourcedCompanyDetail()`** the hard-coded green "sourced" badge
at **~24268–24270** must become data-driven. Replace that `<span … >sourced</span>`
with `${companyProvBadge(company)}`, and change the "Sourced Exposure" label at
**~24281** to read from the badge:

```javascript
          <div class="eyebrow" style="margin-bottom:4px;">Company Profile ${companyProvBadge(company)}</div>
```

and (~24281):

```javascript
          <div ... >${companyProvFor(company).label === 'SOURCED' ? 'Sourced Exposure' : 'Cited Exposure · provisional'}</div>
```

For the **MODELED fallback** path (`showCompanyDetail` ~24151 onward), the badge
at **~24155** is already an orange "modeled" pill — keep it, it's correct. The
only change there is cosmetic alignment with the new `companyProvBadge`; optional.

### 2.6 Net effect

- Nutrien → green **SOURCED** everywhere (list, callout, detail).
- The 10 cited-but-unverified majors → amber **CITED · PARTIAL** everywhere, with
  their real cited country rows still shown in detail.
- Any company without a companies.json entry → orange **MODELED** with the
  "illustrative — not company-disclosed" tooltip.
- No view can present a modeled number with no badge, and nothing reads SOURCED
  until it passes §1.

---

## 3. The MODELED footprint overlay (`scripts/build_company_overlay.py`)

Already written and verified (`py_compile` clean; runs against the real data:
9/12 companies get an overlay, JBS/Nutrien/Yara correctly get none because their
commodities — beef/fertilizer — aren't PSD staples; output asserted to contain
**no `share_pct`**).

### 3.1 Algorithm (precise)

Inputs: `data/companies/*.json` (disclosed footprint) and `data/usda_psd.json`
(observed per-country exports, 1000 MT, for **wheat, corn, rice, soybeans only**).

1. **Build PSD export rankings.** For each PSD commodity, collect every country
   with `exports_kt > 0`, sort descending, assign `rank = 1..N`. (`_load_psd_export_rankings`.)
2. **Map company commodity names → PSD staple keys** via regex
   (`PSD_COMMODITY_MATCHERS`): `wheat→wheat`, `corn|maize→corn`, `rice→rice`,
   `soy|soybean(s)→soybeans`. This catches the soy portion of "Vegetable Oils
   (soybean, …)". Commodities PSD can't back (palm, cocoa, coffee, beef, sugar,
   fertilizer) are **skipped** — no fabricated ranking.
3. **Intersect.** For each company × matched commodity, for each disclosed
   `sourcing_countries` entry:
   - If the country **is** a PSD-ranked exporter → emit a `disclosed_origins` row
     with `psd_export_rank`, `psd_exports_kt`, and a `why` like _"Bunge discloses
     soybeans origin assets in Argentina; Argentina is the 4th soybeans exporter
     per USDA PSD (2026 MY)."_
   - If the country is disclosed but **not** a top exporter (processing/import
     only) → still emit the row honestly with `psd_export_rank: null` and a `why`
     noting it's processing, not origin.
4. **Optional expand layer.** Also list the top-N (default 5) PSD exporters the
   company did **not** disclose, in a **separate** `other_top_exporters` array
   with `disclosed: false` and a `why` stating it's "plausible-origin context
   only, not a company claim." Kept separate so it can never read as disclosure.
5. **Rank** `disclosed_origins` by PSD rank (ranked exporters first, unranked
   processing countries last).
6. **Never emit a percentage.** There is no `share_pct` field anywhere in the
   output, by construction. Every row and the envelope carry
   `data_quality: "modeled"`.

Output: `data/company_overlay.json` via the `_common.write_json` envelope, keyed
by company display name, structured:

```json
{ "_meta": {…}, "data": {
  "Bunge": {
    "data_quality": "modeled",
    "psd_commodities_covered": ["corn","soybeans","wheat"],
    "overlays": [ { "psd_commodity":"soybeans",
       "disclosed_origins": [ { "iso3":"ARG","country":"Argentina",
          "psd_export_rank":4,"psd_exports_kt":…,"disclosed":true,
          "data_quality":"modeled","why":"Bunge discloses … 4th … exporter …" } ],
       "other_top_exporters": [ … disclosed:false … ] } ] } } }
```

### 3.2 Frontend wiring (spec only — implement alongside §2)

- Add `company_overlay` to the `LIVE` fetch block (next to the `companies` fetch
  at index.html **~13300–13304**): `LIVE.company_overlay = (env.data||env)||{}`.
- In `renderSourcedCompanyDetail()`, after the cited country rows, render an
  optional **"Modeled origin context (USDA PSD)"** section that reads
  `LIVE.company_overlay[company]` and lists, per staple, the ranked
  `disclosed_origins` with the orange MODELED badge and the `why` string. The
  `other_top_exporters` array stays visually separate (e.g. a muted "exporters
  this trader hasn't disclosed" sub-list) and must keep `disclosed:false` styling.
- Never sum or percentage the overlay; it is a ranked qualitative list only.

### 3.3 Known refinement (note for the implementer)

`other_top_exporters` is deduped against the **current commodity's** disclosed
ISO set only. A country disclosed under a *different* company-commodity entry
(e.g. Paraguay under "Vegetable Oils" rather than the base "Soybeans" path) can
still appear in `other_top_exporters` for soybeans. Harmless — the
`disclosed_origins` list is authoritative — but to fully dedup, build the
company-wide disclosed-ISO set per PSD key across all commodity entries before
the expand step. Left as a small follow-up; does not affect correctness of the
disclosed rows.

### 3.4 Run

```bash
cd "FoodSecurity AI"
python3 scripts/build_companies.py        # regenerate companies.json (after §2.1 fix)
python3 scripts/build_company_overlay.py  # generate company_overlay.json
```

---

## 4. What to tell reviewers (one paragraph)

> The Companies tab now shows three clearly-badged tiers, never one masquerading
> as another. Where a trader's file has been re-verified complete (Nutrien first,
> the other listed majors as each is re-pulled against its latest 10-K/20-F), the
> tab shows **SOURCED** country lists drawn from that company's own
> citation-linked disclosures. Where cited research exists but hasn't yet passed
> full re-verification, it shows the same cited rows under an honest **CITED ·
> PARTIAL** badge rather than a clean SOURCED one. For everything else it shows a
> **MODELED** commodity-overlap explicitly labelled "illustrative — not
> company-disclosed." On top of the cited layer we add a modeled footprint
> overlay for the four staples USDA PSD tracks (wheat, corn, rice, soybeans): we
> intersect each company's own disclosed operating countries with USDA's observed
> top-exporter rankings to produce a **ranked, qualitative** list of plausible
> origins (e.g. "discloses crush assets in Argentina; Argentina is the #4 soybean
> exporter per USDA PSD"), badged MODELED, with **no attributed tonnage or share**.
> We deliberately do **not** show customs-attributed company volumes: no free
> government source attributes shipments to a named trader — that data is
> paywalled (Panjiva/ImportGenius) and, in the US, routinely redacted at the
> consignee's request — so claiming it would be the exact overclaim our honesty
> rule exists to prevent.

---

## 5. Run + verify checklist (on the Mac)

```bash
cd "FoodSecurity AI"

# 1. Apply the build_companies.py gating fix (§2.1), then regenerate:
python3 scripts/build_companies.py
#   expect: Nutrien (sourced), 10× cited_partial, thin → modeled

# 2. Generate the modeled overlay:
python3 scripts/build_company_overlay.py
#   expect: 9 companies with overlay; JBS/Nutrien/Yara none

# 3. Apply the index.html frontend changes (§2.2–2.5, §3.2), then mirror:
cp index.html foodshield-v21.html
diff index.html foodshield-v21.html && echo "MIRROR OK (files identical)"

# 4. Sanity-check no fake percentages leaked into the overlay:
python3 - <<'PY'
import json
d = json.load(open("data/company_overlay.json"))
assert "share_pct" not in json.dumps(d), "share_pct leaked!"
print("overlay clean: no share_pct")
PY

# 5. As each company passes §1 criteria, flip its file's research_status to
#    "complete", re-run build_companies.py, and it auto-upgrades to SOURCED.
```

**Promotion is now a one-line edit per file** (`research_status: "complete"`) +
a rebuild — exactly the wiring the README promised but the build script wasn't
honouring.
