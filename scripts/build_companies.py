"""
build_companies.py — aggregate per-company JSON files into a single
data/companies.json that the frontend can consume.

Reads:   data/companies/*.json  (one per company, hand-curated with citations)
Writes:  data/companies.json    (single aggregate, keyed by company name)

The per-company files (cargill.json, adm.json, bunge.json, wilmar.json, olam.json,
ldc.json, jbs.json) follow a research-friendly schema designed for citations:

    {
      "_meta": { "company": "...", "ownership": "...", "research_status": "...", ... },
      "commodities": [
        { "name": "Soybeans",
          "evidence_strength": "strong",
          "sourcing_countries": [
            { "iso3": "BRA", "country": "Brazil", "role": "origin",
              "share_pct": null, "evidence": "...", "citation_url": "...",
              "as_of": "2024" },
            ...
          ]
        },
        ...
      ],
      "evidence_gaps": [...]
    }

The frontend wants something flatter and query-friendly: given a company name,
return the list of countries + commodities with risk colors, evidence strength,
and citations. We compute one derived metric (Modeled Exposure replacement:
the AVERAGE FDRS across sourced countries, weighted equally because the source
data doesn't disclose tonnage shares) and tag the company `data_quality:
"sourced"` or `"modeled"` so the frontend can swap badges.

Output schema:

    {
      "_meta": { generated_at, source: "...", ... },
      "data": {
        "Cargill, Incorporated": {
          "display_name": "Cargill",
          "ownership": "private",
          "hq": "Minneapolis, USA",
          "data_quality": "sourced",
          "research_status": "partial",
          "fy_end": "May 31",
          "n_countries": 12,
          "n_commodities": 5,
          "commodities": ["Soybeans","Palm Oil","Cocoa","Wheat","Beef"],
          "country_claims": [
            { iso3, country, commodity, role, share_pct,
              evidence_strength, evidence, citation_url, as_of },
            ...
          ],
          "evidence_gaps": [...],
          "_meta_notes": "...",
          "primary_citation": "https://..."
        },
        ...
      }
    }

The frontend will:
  1. On Companies tab load, fetch /data/companies.json
  2. When a company in the curated list has data_quality === 'sourced',
     swap the modeled badge → sourced and render country_claims with citations.
  3. When a company isn't in companies.json, fall back to the existing
     commodity-overlap modeled view.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Repo layout: scripts/build_companies.py -> ../
ROOT = Path(__file__).resolve().parent.parent
COMPANIES_DIR = ROOT / "data" / "companies"
OUT_PATH = ROOT / "data" / "companies.json"

# Short display names (the JSON files use formal legal names; the frontend
# already uses short forms like "Cargill" / "Bunge" / "ADM" in COMPANY_MAP).
# Map legal → display so the frontend can look up by either.
DISPLAY_NAMES = {
    "Cargill, Incorporated":                       "Cargill",
    "Archer-Daniels-Midland Company (ADM)":        "ADM",
    "Bunge Global SA":                             "Bunge",
    "Bunge Limited":                               "Bunge",
    "Wilmar International Limited":                "Wilmar",
    "Olam Group Limited":                          "Olam Group",
    "Olam Group":                                  "Olam Group",
    "Louis Dreyfus Company B.V.":                  "Louis Dreyfus",
    "Louis Dreyfus Company B.V. (LDC)":            "Louis Dreyfus",
    "JBS S.A.":                                    "JBS",
    "JBS N.V.":                                    "JBS",
    "JBS N.V. (parent of JBS S.A.)":               "JBS",
    "Tyson Foods, Inc.":                           "Tyson Foods",
    "Tyson Foods":                                 "Tyson Foods",
    "Nutrien Ltd.":                                "Nutrien",
    "Yara International ASA":                      "Yara International",
    "Yara International":                          "Yara International",
    "Viterra Limited":                             "Viterra",
    "Viterra Limited (pre-merger standalone entity)": "Viterra",
    "COFCO International Limited":                 "COFCO",
    "COFCO":                                       "COFCO",
    "COFCO International":                         "COFCO",
}


# Citation hygiene — August 2026 link sweep. Twelve URLs cited across
# data/companies/*.json now return 404, 502 or fail DNS, leaving 21 claims
# pointing a reader at nothing. Where the page genuinely existed and the company
# merely reorganized its site, we repoint at the dated Internet Archive capture
# of the exact page that was cited, so the reader still sees what the claim was
# built on rather than a different page that happens to agree. Three further
# dead Yara URLs are deliberately absent: they have no capture at all and the
# assertions they carried turned out to be false, so those claims were corrected
# in yara.json instead of relinked. Re-run the sweep before trusting this map.
DEAD_CITATIONS = {
    "https://www.yara.com/this-is-yara/our-organization/":
        "https://web.archive.org/web/20260410200951/https://www.yara.com/this-is-yara/our-organization/",
    "https://www.viterra.ca/en/What-We-Do/Processing/Becancour":
        "https://web.archive.org/web/20250319001751/https://www.viterra.ca/en/What-We-Do/Processing/Becancour",
    "https://www.ofi.com/products-and-ingredients/nuts/cashews.html":
        "https://web.archive.org/web/20250809192552/https://www.ofi.com/products-and-ingredients/nuts/cashews.html",
    "https://www.ofi.com/products-and-ingredients/coffee/central-and-south-america.html":
        "https://web.archive.org/web/20250718133607/https://www.ofi.com/products-and-ingredients/coffee/central-and-south-america.html",
    "https://www.ofi.com/content/dam/olamofi/products-and-ingredients/cocoa/cocoa-pdfs/cocoa-compass-impact-report-final.pdf":
        "https://web.archive.org/web/20250117073338/https://www.ofi.com/content/dam/olamofi/products-and-ingredients/cocoa/cocoa-pdfs/cocoa-compass-impact-report-final.pdf",
    "https://betterricebetterlife.olamagri.com/":
        "https://web.archive.org/web/20251204234104/https://betterricebetterlife.olamagri.com/",
    "https://www.tysonsustainability.com/people/consumers/ingredient-sourcing":
        "https://web.archive.org/web/20230609015755/https://tysonsustainability.com/people/consumers/ingredient-sourcing",
    "https://jbs.com.br/en/press/releases-en/jbs-acquires-european-company-to-expand-its-global-plant-based-food-platform/":
        "https://web.archive.org/web/20221129194221/https://jbs.com.br/en/press/releases-en/jbs-acquires-european-company-to-expand-its-global-plant-based-food-platform/",
    "https://au.cofcointernational.com/":
        "https://web.archive.org/web/20200721201432/https://au.cofcointernational.com/",
}


def _normalize_one(company_json):
    """Convert per-company schema → flatter frontend schema."""
    meta = company_json.get("_meta", {}) or {}
    full_name = meta.get("company", "Unknown")
    display = DISPLAY_NAMES.get(full_name, full_name)

    # Flatten commodities → country_claims
    claims = []
    commodities = []
    isos = set()
    for c in company_json.get("commodities", []) or []:
        commodity_name = c.get("name") or "Unknown"
        evidence_strength = c.get("evidence_strength", "medium")
        commodities.append(commodity_name)
        for sc in c.get("sourcing_countries", []) or []:
            iso3 = sc.get("iso3")
            if not iso3:
                continue
            isos.add(iso3)
            claims.append({
                "iso3":              iso3,
                "country":           sc.get("country"),
                "commodity":         commodity_name,
                "role":              sc.get("role", "origin"),
                "share_pct":         sc.get("share_pct"),
                "evidence_strength": sc.get("evidence_strength", evidence_strength),
                "evidence":          sc.get("evidence"),
                "citation_url":      sc.get("citation_url"),
                "as_of":             sc.get("as_of"),
                "entity":            sc.get("entity"),   # for Olam-style split
            })

    # Pick the most-cited domain as the primary citation
    domains = {}
    for cl in claims:
        url = cl.get("citation_url") or ""
        if "://" in url:
            domain = url.split("/")[2]
            domains[domain] = domains.get(domain, 0) + 1
    primary = sorted(domains.items(), key=lambda x: -x[1])
    primary_citation = primary[0][0] if primary else None

    # Relink dead citations only AFTER primary_citation is computed, so a company
    # is never described to the reader as sourced from "web.archive.org".
    for cl in claims:
        replacement = DEAD_CITATIONS.get(cl.get("citation_url"))
        if replacement:
            cl["citation_url"] = replacement

    # research_status from per-company _meta — the ONLY signal that earns the
    # "sourced" badge (matches data/companies/README.md rule). Files that are
    # partial/scaffolded/historical render as MODELED/CITED·PARTIAL on the
    # frontend even if they carry strong-evidence claims, until they are
    # re-verified to complete. (Fixed Jun 2026 / v23 — the old rule promoted any
    # partial file with 5+ claims to "sourced", which is why all 12 companies
    # wrongly flew a SOURCED badge — the "wrong company data" reviewers saw.)
    has_strong = any(cl.get("evidence_strength") == "strong" for cl in claims)
    research_status = meta.get("research_status", "scaffolded")
    if research_status == "complete" and len(claims) >= 1:
        data_quality = "sourced"
    elif has_strong and len(claims) >= 5:
        # Strong-but-unverified: cited claims exist and may be shown, but the
        # file hasn't passed the completion criteria. "cited_partial" lets the
        # frontend show the cited rows WITH an honest amber badge — never a
        # clean SOURCED badge.
        data_quality = "cited_partial"
    else:
        data_quality = "modeled"

    return {
        "display_name":      display,
        "legal_name":        full_name,
        "ownership":         meta.get("ownership"),
        "hq":                meta.get("headquarters"),
        "fy_end":            meta.get("fiscal_year_end"),
        "latest_disclosure": meta.get("latest_disclosure_year"),
        "last_updated":      meta.get("last_updated"),
        "research_status":   research_status,
        "data_quality":      data_quality,
        "n_countries":       len(isos),
        "n_commodities":     len(commodities),
        "n_claims":          len(claims),
        "commodities":       commodities,
        "iso_set":           sorted(isos),
        "country_claims":    claims,
        "evidence_gaps":     company_json.get("evidence_gaps", []),
        "meta_notes":        meta.get("notes"),
        "primary_citation":  primary_citation,
    }


def main():
    if not COMPANIES_DIR.exists():
        print(f"[SKIP] {COMPANIES_DIR} does not exist", file=sys.stderr)
        _write({})
        return

    files = sorted([p for p in COMPANIES_DIR.iterdir()
                    if p.suffix == ".json" and not p.name.startswith("_")])
    print(f"[INFO] Aggregating {len(files)} company JSON files from {COMPANIES_DIR}")

    out = {}
    total_claims = 0
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [WARN] failed to parse {fp.name}: {e}", file=sys.stderr)
            continue
        try:
            normalized = _normalize_one(data)
        except Exception as e:
            print(f"  [WARN] failed to normalize {fp.name}: {e}", file=sys.stderr)
            continue

        # Key by short display name — that's what the frontend's COMPANY_MAP uses.
        key = normalized["display_name"]
        out[key] = normalized
        total_claims += normalized["n_claims"]
        print(f"  [OK] {fp.name:18s} → {key:18s} "
              f"{normalized['n_commodities']}c "
              f"{normalized['n_claims']:>3} claims "
              f"({normalized['data_quality']})")

    print(f"[INFO] Wrote {len(out)} companies, {total_claims} total country claims")
    _write(out)


def _write(out):
    envelope = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "build_companies.py — aggregates per-company JSON files in data/companies/",
            "notes": (
                "Each company entry is hand-curated from that company's OWN published material "
                "(annual report, ESG/sustainability report, asset location pages). "
                "data_quality='sourced' means at least 5 country claims with strong evidence. "
                "Frontend should fall back to the modeled commodity-overlap heuristic for "
                "companies not present in this file."
            ),
            "n_companies": len(out),
            "n_claims": sum(c.get("n_claims", 0) for c in out.values()),
            "version": "v21",
        },
        "data": out,
    }
    OUT_PATH.write_text(json.dumps(envelope, indent=2, ensure_ascii=False))
    print(f"[OK] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
