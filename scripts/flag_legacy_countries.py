#!/usr/bin/env python3
"""
flag_legacy_countries.py — Re-verification worklist generator for FoodShield AI.

Reviewers flagged that ~84% of per-country structural data is `legacy_curated`
(hand-authored heritage estimates, not sourced). This script reads
`data/countries.json`, audits the provenance of every structural field per
country, and emits a PRIORITISED worklist so the owner knows which countries to
re-verify first when he runs the (online) trade-data-verify workflow on his Mac.

It changes nothing. It is read-only reporting: a CSV + JSON worklist plus
summary stats printed to stdout.

Prioritisation (highest first), see BASELINE_REVERIFICATION_SPEC.md §3:
  (a) is the country in the demo set (Rabobank pitch)         — biggest weight
  (b) is it a large food importer / large economy             — reviewers check these
  (c) how many of its structural fields are still legacy      — most work to do

Stdlib only. Defensive about schema shape (supports both the v20.6
`{"data": {"countries": {...}}}` envelope and a flat `{"countries": {...}}` or
top-level `{ISO3: {...}}` layout). Compiles clean under `python3 -m py_compile`.

Usage:
    python3 scripts/flag_legacy_countries.py
    python3 scripts/flag_legacy_countries.py --input data/countries.json \
        --out-csv reverify_worklist.csv --out-json reverify_worklist.json
    python3 scripts/flag_legacy_countries.py --top 25   # only print top N rows

Exit codes: 0 on success, 2 on unreadable/empty input.
"""

import argparse
import csv
import json
import os
import sys

# ---------------------------------------------------------------------------
# Configuration — kept inline (stdlib-only, no external config files)
# ---------------------------------------------------------------------------

# The structural fields we care about re-verifying. Mirrors _meta.fields in
# countries.json. Each is audited independently for provenance.
STRUCTURAL_FIELDS = [
    "fdrs", "c", "f2030", "w", "r", "m", "fi",
    "net", "imports", "exports", "exportDests", "suppliers", "supPct",
    # v23 — FDRS v2 components, so the legacy-ratio measurement matches the UI
    # confidence pill and reflects the sourced Economic Access / Grain Buffer work.
    "econ_access", "grain_buffer",
]

# Quality flags that count as "still needs re-verification".
LEGACY_FLAGS = {"legacy_curated", "legacy_import_dependency"}

# Quality flags that count as already done / acceptable provenance.
SOURCED_FLAGS = {"sourced", "modeled", "manual"}

# Demo-set countries (DEMO_SCRIPT.md). Netherlands is the Rabobank home country;
# Egypt is the headline chokepoint story (wheat from Russia); the most-vulnerable
# ranking shown live names South Sudan, Yemen, Somalia. These get re-verified
# first because a reviewer will open exactly these panels.
DEMO_SET = {
    "NLD": "Netherlands (Rabobank home turf — demoed live)",
    "EGY": "Egypt (headline supplier-concentration / wheat chokepoint story)",
    "SSD": "South Sudan (top of most-vulnerable ranking, FDRS ~80)",
    "YEM": "Yemen (most-vulnerable ranking, ~90% wheat import dependence)",
    "SOM": "Somalia (most-vulnerable ranking, FDRS ~78)",
}

# Large food importers / large economies a reviewer is most likely to spot-check.
# Used as priority signal (b). Not exhaustive — it is a heuristic ranking aid, not
# a sourced list, and is explicitly labelled as such in the output. Roughly the
# world's biggest agri-food importers + largest economies by GDP.
LARGE_IMPORTERS = {
    "CHN": "China — largest food importer",
    "USA": "United States — large importer & economy",
    "DEU": "Germany",
    "JPN": "Japan — major net food importer",
    "GBR": "United Kingdom",
    "FRA": "France",
    "NLD": "Netherlands — major agri re-export hub",
    "ITA": "Italy",
    "IND": "India",
    "KOR": "South Korea — major net food importer",
    "ESP": "Spain",
    "SAU": "Saudi Arabia — Gulf importer",
    "MEX": "Mexico — major grain importer",
    "CAN": "Canada",
    "BEL": "Belgium — agri trade hub",
    "EGY": "Egypt — world's largest wheat importer",
    "TUR": "Turkey",
    "IDN": "Indonesia — large wheat/soy importer",
    "ARE": "United Arab Emirates — Gulf importer",
    "BRA": "Brazil",
    "RUS": "Russia",
    "POL": "Poland",
    "DZA": "Algeria — major wheat importer",
    "NGA": "Nigeria — large net food importer",
    "PHL": "Philippines — largest rice importer",
    "BGD": "Bangladesh — large grain importer",
    "VNM": "Vietnam",
    "THA": "Thailand",
    "MYS": "Malaysia",
    "IRN": "Iran — major wheat/maize importer",
}

# Priority weights (signal (a) dominates, then (b), then (c)).
W_DEMO = 1000           # any demo-set country sorts above everything else
W_IMPORTER = 100        # large-importer bump
W_LEGACY_FIELD = 1      # each remaining legacy field adds a point


# ---------------------------------------------------------------------------
# Schema-tolerant loading
# ---------------------------------------------------------------------------

def load_countries(path):
    """Return (countries_dict, meta) tolerant of the known schema shapes.

    Supported shapes:
      {"data": {"countries": {ISO3: {...}}}, "_meta": {...}}   (v20.6 envelope)
      {"countries": {ISO3: {...}}}                              (flat)
      {ISO3: {...}}                                             (bare top-level)
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    if not isinstance(doc, dict):
        raise ValueError("countries.json root is not a JSON object")

    meta = doc.get("_meta") or doc.get("meta") or {}

    # v20.6 envelope
    data = doc.get("data")
    if isinstance(data, dict) and isinstance(data.get("countries"), dict):
        return data["countries"], meta

    # flat {"countries": {...}}
    if isinstance(doc.get("countries"), dict):
        return doc["countries"], meta

    # bare top-level ISO3 map: keep only dict-valued, 3-char keys, skip metadata
    bare = {
        k: v for k, v in doc.items()
        if isinstance(v, dict) and len(k) == 3 and k.isupper()
    }
    if bare:
        return bare, meta

    raise ValueError("could not locate a countries mapping in the JSON")


def field_quality(field_obj):
    """Return the quality_flag for a field object, defensively.

    Field objects look like {"value": ..., "quality_flag": "...", ...}.
    Returns None if the field is absent or malformed.
    """
    if not isinstance(field_obj, dict):
        return None
    flag = field_obj.get("quality_flag")
    if isinstance(flag, str) and flag.strip():
        return flag.strip()
    return None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def audit_country(iso3, country_obj):
    """Audit one country. Returns a per-country record dict."""
    legacy_fields = []
    sourced_fields = []
    missing_fields = []
    other_fields = []  # present, but a flag outside the known sets

    present = country_obj if isinstance(country_obj, dict) else {}

    for field in STRUCTURAL_FIELDS:
        if field not in present:
            missing_fields.append(field)
            continue
        flag = field_quality(present.get(field))
        if flag is None:
            other_fields.append(field)
        elif flag in LEGACY_FLAGS:
            legacy_fields.append(field)
        elif flag in SOURCED_FLAGS:
            sourced_fields.append(field)
        else:
            other_fields.append(field)

    n_present = len(STRUCTURAL_FIELDS) - len(missing_fields)
    n_legacy = len(legacy_fields)
    n_sourced = len(sourced_fields)

    in_demo = iso3 in DEMO_SET
    is_importer = iso3 in LARGE_IMPORTERS

    # Composite priority score: demo dominates, then importer, then legacy count.
    priority = (
        (W_DEMO if in_demo else 0)
        + (W_IMPORTER if is_importer else 0)
        + W_LEGACY_FIELD * n_legacy
    )

    fully_sourced = n_present > 0 and n_legacy == 0 and len(other_fields) == 0
    fully_legacy = n_present > 0 and n_sourced == 0 and len(other_fields) == 0 and n_legacy == n_present

    return {
        "iso3": iso3,
        "priority_score": priority,
        "in_demo_set": in_demo,
        "demo_reason": DEMO_SET.get(iso3, ""),
        "is_large_importer": is_importer,
        "importer_reason": LARGE_IMPORTERS.get(iso3, ""),
        "n_fields_present": n_present,
        "n_legacy": n_legacy,
        "n_sourced": n_sourced,
        "n_missing": len(missing_fields),
        "n_other_flag": len(other_fields),
        "legacy_fields": legacy_fields,
        "sourced_fields": sourced_fields,
        "missing_fields": missing_fields,
        "fully_sourced": fully_sourced,
        "fully_legacy": fully_legacy,
    }


def build_worklist(countries):
    """Audit every country, return list of records sorted by priority desc."""
    records = []
    for iso3 in sorted(countries.keys()):
        try:
            records.append(audit_country(iso3, countries[iso3]))
        except Exception as exc:  # defensive: never let one bad row kill the run
            records.append({
                "iso3": iso3, "priority_score": -1, "error": str(exc),
                "in_demo_set": False, "is_large_importer": False,
                "n_fields_present": 0, "n_legacy": 0, "n_sourced": 0,
                "n_missing": len(STRUCTURAL_FIELDS), "n_other_flag": 0,
                "legacy_fields": [], "sourced_fields": [], "missing_fields": [],
                "fully_sourced": False, "fully_legacy": False, "demo_reason": "",
                "importer_reason": "",
            })
    # Sort: priority desc, then legacy count desc, then iso3 asc for stability.
    records.sort(key=lambda r: (-r.get("priority_score", 0),
                                -r.get("n_legacy", 0),
                                r.get("iso3", "")))
    return records


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

def compute_summary(records):
    """Aggregate stats across all countries."""
    total = len(records)
    fully_sourced = sum(1 for r in records if r.get("fully_sourced"))
    fully_legacy = sum(1 for r in records if r.get("fully_legacy"))
    in_demo = sum(1 for r in records if r.get("in_demo_set"))

    # Per-field legacy ratio.
    per_field = {}
    for field in STRUCTURAL_FIELDS:
        legacy = present = sourced = 0
        for r in records:
            if field in r.get("legacy_fields", []):
                legacy += 1
                present += 1
            elif field in r.get("sourced_fields", []):
                sourced += 1
                present += 1
            elif field in r.get("missing_fields", []):
                pass
            else:
                # present with an "other" flag still counts as present
                if field not in r.get("missing_fields", []) and r.get("n_fields_present", 0):
                    present += 1
        pct_legacy = (100.0 * legacy / present) if present else 0.0
        per_field[field] = {
            "present": present,
            "legacy": legacy,
            "sourced": sourced,
            "pct_legacy": round(pct_legacy, 1),
        }

    # Overall field-level legacy ratio (the number qa_checks.py guards on).
    total_present = sum(pf["present"] for pf in per_field.values())
    total_legacy = sum(pf["legacy"] for pf in per_field.values())
    overall_pct_legacy = round(100.0 * total_legacy / total_present, 1) if total_present else 0.0

    return {
        "total_countries": total,
        "fully_sourced_countries": fully_sourced,
        "fully_legacy_countries": fully_legacy,
        "demo_set_countries_present": in_demo,
        "total_field_instances_present": total_present,
        "total_field_instances_legacy": total_legacy,
        "overall_pct_legacy_fields": overall_pct_legacy,
        "per_field": per_field,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_csv(path, records):
    cols = [
        "rank", "iso3", "priority_score", "in_demo_set", "is_large_importer",
        "n_legacy", "n_sourced", "n_missing", "n_fields_present",
        "fully_legacy", "fully_sourced", "legacy_fields",
        "demo_reason", "importer_reason",
    ]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i, r in enumerate(records, start=1):
            w.writerow([
                i,
                r.get("iso3", ""),
                r.get("priority_score", ""),
                r.get("in_demo_set", False),
                r.get("is_large_importer", False),
                r.get("n_legacy", 0),
                r.get("n_sourced", 0),
                r.get("n_missing", 0),
                r.get("n_fields_present", 0),
                r.get("fully_legacy", False),
                r.get("fully_sourced", False),
                ";".join(r.get("legacy_fields", [])),
                r.get("demo_reason", ""),
                r.get("importer_reason", ""),
            ])


def write_json(path, records, summary):
    payload = {
        "_about": (
            "Re-verification worklist for FoodShield AI structural country data. "
            "Read-only audit produced by scripts/flag_legacy_countries.py. "
            "See BASELINE_REVERIFICATION_SPEC.md for the per-field source mapping "
            "and the staged rollout. Priority = demo-set + large-importer + "
            "remaining-legacy-field count."
        ),
        "summary": summary,
        "worklist": records,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def print_summary(summary, records, top):
    s = summary
    print("=" * 70)
    print("FoodShield AI — legacy-data re-verification worklist")
    print("=" * 70)
    print(f"Total countries audited:        {s['total_countries']}")
    print(f"Fully sourced (no legacy left):  {s['fully_sourced_countries']}")
    print(f"Fully legacy (nothing sourced):  {s['fully_legacy_countries']}")
    print(f"Demo-set countries present:      {s['demo_set_countries_present']} / {len(DEMO_SET)}")
    print()
    print(f"Overall field-level legacy ratio: {s['overall_pct_legacy_fields']}% "
          f"({s['total_field_instances_legacy']} of "
          f"{s['total_field_instances_present']} present field-instances)")
    print("  ^ this is the number qa_checks.py's provenance guard should watch fall.")
    print()
    print("Per-field legacy %:")
    print(f"  {'field':<12} {'present':>8} {'legacy':>8} {'sourced':>8} {'%legacy':>9}")
    for field in STRUCTURAL_FIELDS:
        pf = s["per_field"][field]
        print(f"  {field:<12} {pf['present']:>8} {pf['legacy']:>8} "
              f"{pf['sourced']:>8} {pf['pct_legacy']:>8}%")
    print()
    print(f"Top {top} countries to re-verify first "
          f"(demo set, then large importers, then most legacy fields):")
    print(f"  {'#':>3} {'iso':<4} {'pri':>5} {'demo':>5} {'imp':>4} "
          f"{'legacy':>7} {'sourced':>8}  note")
    for i, r in enumerate(records[:top], start=1):
        note = r.get("demo_reason") or r.get("importer_reason") or ""
        print(f"  {i:>3} {r.get('iso3',''):<4} {r.get('priority_score',0):>5} "
              f"{'Y' if r.get('in_demo_set') else '-':>5} "
              f"{'Y' if r.get('is_large_importer') else '-':>4} "
              f"{r.get('n_legacy',0):>7} {r.get('n_sourced',0):>8}  {note[:48]}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.abspath(__file__))
    default_input = os.path.normpath(os.path.join(here, "..", "data", "countries.json"))
    parser.add_argument("--input", default=default_input,
                        help="path to countries.json (default: ../data/countries.json)")
    parser.add_argument("--out-csv", default="reverify_worklist.csv",
                        help="output CSV path (default: reverify_worklist.csv)")
    parser.add_argument("--out-json", default="reverify_worklist.json",
                        help="output JSON path (default: reverify_worklist.json)")
    parser.add_argument("--top", type=int, default=30,
                        help="how many top-priority rows to print (default: 30)")
    parser.add_argument("--no-write", action="store_true",
                        help="print summary only; do not write CSV/JSON files")
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        sys.stderr.write(f"ERROR: input not found: {args.input}\n")
        return 2

    try:
        countries, _meta = load_countries(args.input)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(f"ERROR: could not load {args.input}: {exc}\n")
        return 2

    if not countries:
        sys.stderr.write("ERROR: no countries found in input\n")
        return 2

    records = build_worklist(countries)
    summary = compute_summary(records)

    if not args.no_write:
        try:
            write_csv(args.out_csv, records)
            write_json(args.out_json, records, summary)
        except OSError as exc:
            sys.stderr.write(f"WARNING: could not write outputs: {exc}\n")

    print_summary(summary, records, args.top)

    if not args.no_write:
        print(f"Wrote worklist CSV  -> {os.path.abspath(args.out_csv)}")
        print(f"Wrote worklist JSON -> {os.path.abspath(args.out_json)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
