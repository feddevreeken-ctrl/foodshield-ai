"""
build_company_overlay.py — MODELED company-commodity-country footprint overlay.

WHAT THIS IS
------------
The honest best-effort company exposure view, built ONLY from free, cited,
public data. It NEVER emits a fabricated tonnage or share percentage. It
produces, for each company x commodity, a *ranked* list of plausible origin
countries with a one-line "why", and tags the whole thing MODELED.

It exists because no free government source attributes customs flows to a named
trader (see REVIEWER_FEEDBACK_RESPONSE.md sec 4d). What we CAN do honestly is
intersect two public datasets:

  (a) a company's DISCLOSED operating footprint  — the cited
      data/companies/<name>.json files (where each trader itself says it
      operates: Cargill's Argentine crush, Bunge's Brazilian elevators, ...).
  (b) USDA PSD top-exporter rankings per commodity — data/usda_psd.json,
      observed per-country exports in 1000 MT.

INTERSECTION LOGIC
------------------
For a company C and commodity Z:
  1. Collect the set of countries C discloses for Z (from its cited file).
  2. Look up the USDA PSD global export ranking for Z.
  3. For each disclosed country Y that is also a ranked exporter of Z, emit a
     MODELED origin row:
        { iso3, country, commodity, psd_export_rank, psd_exports_kt,
          disclosed (bool), why }
     "why" reads e.g.:
        "Bunge discloses soybean processing assets in Argentina; Argentina is
         the #4 soybean exporter per USDA PSD (2026 MY)."
  4. Optionally (mode='expand') also surface the top-N PSD exporters of Z that
     the company does NOT disclose, flagged disclosed=false, as "exporters this
     trader could plausibly touch but hasn't disclosed" — clearly weaker, kept
     separate so it never reads as a company claim.

Output is RANKED and QUALITATIVE. There is no share_pct field anywhere in the
output, by design. The envelope and every row carry data_quality="modeled".

COMMODITY MAPPING
-----------------
USDA PSD only covers four staples: wheat, corn (maize), rice, soybeans. The
company files name commodities in their own words ("Maize", "Vegetable Oils
(soybean, rapeseed/canola, sunflower)", "Sugar (ethanol/cane)", ...). We map
only the commodities PSD can actually back; everything else (palm, cocoa,
coffee, beef, fertilizer, sugar) has NO PSD export ranking and is therefore
NOT given a modeled origin ranking here — it stays on the company's own cited
disclosure. We do not invent rankings for commodities PSD doesn't track.

Reads:   data/companies/*.json, data/usda_psd.json
Writes:  data/company_overlay.json   (write_json envelope via _common)

The frontend consumes this as the MODELED layer beneath the SOURCED cited layer:
cited disclosure shows first (green SOURCED), and for the PSD-trackable staples
this overlay adds a ranked, badged MODELED origin context.
"""
import json
import re
import sys
from pathlib import Path

# Import shared conventions (write_json envelope, DATA_DIR). Fall back to a
# local definition if _common isn't importable (e.g. run standalone), so the
# script never hard-crashes on an import path issue.
try:
    from _common import DATA_DIR, write_json  # type: ignore
except Exception:  # pragma: no cover - defensive
    from datetime import datetime, timezone

    ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = ROOT / "data"

    def write_json(filename, payload, *, source=None, notes=None):
        out = {
            "_meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": source or "unknown",
                "notes": notes or "",
                "version": "v21",
            },
            "data": payload,
        }
        path = DATA_DIR / filename
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"[OK] wrote {path}")
        return path


COMPANIES_DIR = DATA_DIR / "companies"
PSD_PATH = DATA_DIR / "usda_psd.json"
OUT_NAME = "company_overlay.json"

# Map the four USDA PSD staple keys to the regexes that match how the company
# JSON files name those commodities. PSD covers ONLY these four. Anything not
# matched here gets no modeled origin ranking (palm, cocoa, coffee, beef,
# sugar, fertilizer, etc.) — intentional: we don't have an export ranking for
# them and won't fake one.
PSD_COMMODITY_MATCHERS = {
    "wheat":    [r"\bwheat\b"],
    "corn":     [r"\bcorn\b", r"\bmaize\b"],
    "rice":     [r"\brice\b"],
    # "soybeans" should match "Soybeans", "Soy", and the soy portion of
    # "Vegetable Oils (soybean, rapeseed/canola, sunflower)".
    "soybeans": [r"\bsoy\b", r"\bsoya?beans?\b", r"\bsoybean\b"],
}

# Short display names mirror build_companies.py so the overlay keys line up
# with the frontend's COMPANY_MAP / companies.json keys.
DISPLAY_NAMES = {
    "Cargill, Incorporated": "Cargill",
    "Archer-Daniels-Midland Company (ADM)": "ADM",
    "Bunge Global SA": "Bunge",
    "Bunge Limited": "Bunge",
    "Wilmar International Limited": "Wilmar",
    "Olam Group Limited": "Olam Group",
    "Olam Group": "Olam Group",
    "Louis Dreyfus Company B.V.": "Louis Dreyfus",
    "Louis Dreyfus Company B.V. (LDC)": "Louis Dreyfus",
    "JBS S.A.": "JBS",
    "JBS N.V.": "JBS",
    "JBS N.V. (parent of JBS S.A.)": "JBS",
    "Tyson Foods, Inc.": "Tyson Foods",
    "Tyson Foods": "Tyson Foods",
    "Nutrien Ltd.": "Nutrien",
    "Yara International ASA": "Yara International",
    "Yara International": "Yara International",
    "Viterra Limited": "Viterra",
    "Viterra Limited (pre-merger standalone entity)": "Viterra",
    "COFCO International Limited": "COFCO",
    "COFCO": "COFCO",
    "COFCO International": "COFCO",
}


def _match_psd_commodity(name):
    """Return the PSD staple key a company-commodity name maps to, or None."""
    if not name:
        return None
    low = name.lower()
    for psd_key, patterns in PSD_COMMODITY_MATCHERS.items():
        for pat in patterns:
            if re.search(pat, low):
                return psd_key
    return None


def _load_psd_export_rankings(psd):
    """Build {psd_commodity: [ {iso3, country, exports_kt, year}, ... ]} sorted
    descending by exports_kt. Defensive against missing fields."""
    data = (psd or {}).get("data", psd) or {}
    by_commodity = {}
    for iso3, commodities in data.items():
        if not isinstance(commodities, dict):
            continue
        for cm, rec in commodities.items():
            if not isinstance(rec, dict):
                continue
            exports = rec.get("exports_kt")
            if exports is None:
                continue
            try:
                exports = float(exports)
            except (TypeError, ValueError):
                continue
            if exports <= 0:
                continue
            by_commodity.setdefault(cm, []).append({
                "iso3": iso3,
                "country": rec.get("country") or iso3,
                "exports_kt": exports,
                "year": rec.get("_year_exports_kt") or rec.get("year"),
            })
    rankings = {}
    for cm, rows in by_commodity.items():
        rows.sort(key=lambda r: -r["exports_kt"])
        for i, r in enumerate(rows):
            r["rank"] = i + 1
        rankings[cm] = rows
    return rankings


def _rank_lookup(ranking_rows):
    """{iso3: row} for fast disclosed-country lookups."""
    return {r["iso3"]: r for r in ranking_rows}


def _ordinal(n):
    if n is None:
        return "?"
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def build_overlay(expand_top_n=5):
    """Return the overlay payload dict, keyed by company display name."""
    if not COMPANIES_DIR.exists():
        print(f"[SKIP] {COMPANIES_DIR} does not exist", file=sys.stderr)
        return {}
    if not PSD_PATH.exists():
        print(f"[SKIP] {PSD_PATH} does not exist", file=sys.stderr)
        return {}

    try:
        psd = json.loads(PSD_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] failed to parse {PSD_PATH.name}: {e}", file=sys.stderr)
        return {}

    rankings = _load_psd_export_rankings(psd)
    rank_lookups = {cm: _rank_lookup(rows) for cm, rows in rankings.items()}

    out = {}
    files = sorted(p for p in COMPANIES_DIR.iterdir()
                   if p.suffix == ".json" and not p.name.startswith("_"))
    for fp in files:
        try:
            cdata = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] failed to parse {fp.name}: {e}", file=sys.stderr)
            continue

        meta = cdata.get("_meta", {}) or {}
        legal = meta.get("company", fp.stem)
        display = DISPLAY_NAMES.get(legal, legal)

        # company_overlays[psd_commodity] = { disclosed_origins:[], other_top_exporters:[] }
        overlays = {}
        for commodity in cdata.get("commodities", []) or []:
            cm_name = commodity.get("name") or ""
            psd_key = _match_psd_commodity(cm_name)
            if not psd_key or psd_key not in rankings:
                continue  # PSD doesn't track this commodity — no modeled ranking

            ranking = rankings[psd_key]
            lookup = rank_lookups[psd_key]
            slot = overlays.setdefault(psd_key, {
                "psd_commodity": psd_key,
                "company_commodity_names": [],
                "disclosed_origins": [],
                "other_top_exporters": [],
            })
            if cm_name not in slot["company_commodity_names"]:
                slot["company_commodity_names"].append(cm_name)

            disclosed_isos = set()
            for sc in commodity.get("sourcing_countries", []) or []:
                iso3 = sc.get("iso3")
                if not iso3:
                    continue
                disclosed_isos.add(iso3)
                psd_row = lookup.get(iso3)
                if not psd_row:
                    # Company discloses an asset here but the country isn't a
                    # PSD-ranked exporter of this staple (e.g. a processing-only
                    # importer). We still record it, honestly, with no rank.
                    why = (
                        f"{display} discloses {psd_key} {sc.get('role', 'sourcing')} "
                        f"assets in {sc.get('country', iso3)}; "
                        f"{sc.get('country', iso3)} is not a top {psd_key} exporter "
                        f"per USDA PSD (likely processing/import, not origin)."
                    )
                    if not any(o["iso3"] == iso3 for o in slot["disclosed_origins"]):
                        slot["disclosed_origins"].append({
                            "iso3": iso3,
                            "country": sc.get("country") or iso3,
                            "psd_export_rank": None,
                            "psd_exports_kt": None,
                            "disclosed": True,
                            "role": sc.get("role"),
                            "data_quality": "modeled",
                            "why": why,
                        })
                    continue

                why = (
                    f"{display} discloses {psd_key} {sc.get('role', 'sourcing')} "
                    f"assets in {psd_row['country']}; {psd_row['country']} is the "
                    f"{_ordinal(psd_row['rank'])} {psd_key} exporter per USDA PSD"
                    + (f" ({psd_row['year']} MY)." if psd_row.get('year') else ".")
                )
                if not any(o["iso3"] == iso3 for o in slot["disclosed_origins"]):
                    slot["disclosed_origins"].append({
                        "iso3": iso3,
                        "country": psd_row["country"],
                        "psd_export_rank": psd_row["rank"],
                        "psd_exports_kt": psd_row["exports_kt"],
                        "disclosed": True,
                        "role": sc.get("role"),
                        "data_quality": "modeled",
                        "why": why,
                    })

            # Optional: top-N PSD exporters NOT disclosed by the company. Kept
            # SEPARATE and disclosed=false so it never reads as a company claim.
            if expand_top_n:
                for psd_row in ranking[:expand_top_n]:
                    if psd_row["iso3"] in disclosed_isos:
                        continue
                    why = (
                        f"{psd_row['country']} is the {_ordinal(psd_row['rank'])} "
                        f"{psd_key} exporter per USDA PSD"
                        + (f" ({psd_row['year']} MY)" if psd_row.get('year') else "")
                        + f"; {display} has NOT disclosed {psd_key} origination "
                        f"here. Shown as plausible-origin context only, not a "
                        f"company claim."
                    )
                    slot["other_top_exporters"].append({
                        "iso3": psd_row["iso3"],
                        "country": psd_row["country"],
                        "psd_export_rank": psd_row["rank"],
                        "psd_exports_kt": psd_row["exports_kt"],
                        "disclosed": False,
                        "data_quality": "modeled",
                        "why": why,
                    })

            # Rank disclosed origins: PSD-ranked exporters first (by rank),
            # then disclosed-but-unranked (processing) countries.
            slot["disclosed_origins"].sort(
                key=lambda o: (o["psd_export_rank"] is None,
                               o["psd_export_rank"] or 9999)
            )

        if overlays:
            out[display] = {
                "display_name": display,
                "legal_name": legal,
                "data_quality": "modeled",
                "psd_commodities_covered": sorted(overlays.keys()),
                "overlays": list(overlays.values()),
                "note": (
                    "MODELED footprint overlay: intersection of this company's "
                    "OWN disclosed operating countries with USDA PSD top-exporter "
                    "rankings. Origins are ranked and qualitative — NO tonnage or "
                    "share is attributed to the company. PSD covers only wheat, "
                    "corn, rice and soybeans; the company's other commodities "
                    "(palm, cocoa, coffee, beef, sugar, fertilizer) have no PSD "
                    "ranking and stay on the cited disclosure only."
                ),
            }
            n_disc = sum(len(o["disclosed_origins"]) for o in overlays.values())
            print(f"  [OK] {fp.name:18s} -> {display:18s} "
                  f"{len(overlays)} staple(s), {n_disc} disclosed origin rows")
        else:
            print(f"  [--] {fp.name:18s} -> {display:18s} "
                  f"no PSD-trackable staples disclosed")

    return out


def main():
    payload = build_overlay(expand_top_n=5)
    write_json(
        OUT_NAME,
        payload,
        source="build_company_overlay.py — companies/*.json disclosed footprint x USDA PSD export rankings",
        notes=(
            "MODELED, qualitative company-commodity-country exposure overlay. "
            "Built only from free public data: each company's own disclosed "
            "operating countries intersected with USDA PSD per-commodity export "
            "rankings (wheat/corn/rice/soybeans only). Origins are RANKED, never "
            "given a fabricated percentage. No customs-attributed company volumes "
            "exist in free government data; this is the honest best-effort stand-in "
            "and is badged MODELED everywhere it renders."
        ),
    )
    print(f"[INFO] {len(payload)} companies have a modeled PSD-staple overlay")


if __name__ == "__main__":
    main()
