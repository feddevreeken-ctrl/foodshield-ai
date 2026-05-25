"""
Build the canonical countries.json structural overlay.

This file externalizes the structural baseline that still lives inside the
embedded COUNTRIES array in index.html. The goal is to make that baseline
auditable, versioned, and safe to replace field-by-field with sourced data over
time without breaking the current frontend.

Source priority:
  1. Existing non-legacy overrides already present in data/countries.json
  2. data/country_caloric_shares.json (FAOSTAT Food Balance Sheets), when present
  3. Embedded legacy values extracted from index.html

Fields migrated here are still labeled legacy_curated unless they have a real
source attached. The HTML treats index.html as a fallback only.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from _common import DATA_DIR, ROOT

HTML_PATH = ROOT / "index.html"
OUT_PATH = DATA_DIR / "countries.json"
FBS_PATH = DATA_DIR / "country_caloric_shares.json"
NET_TRADE_PATH = DATA_DIR / "net_food_trade.json"

STRUCTURAL_FIELDS = [
    "fdrs",
    "c",
    "f2030",
    "w",
    "r",
    "m",
    "fi",
    "net",
    "imports",
    "exports",
    "exportDests",
    "suppliers",
    "supPct",
]

QUALITY_FLAGS = {
    "sourced": "Verified against a public dataset; source_url + as_of populated.",
    "legacy_curated": "Hand-authored heritage value, not yet re-verified. Treated as draft.",
    "legacy_import_dependency": (
        "Heritage value originally meant 'fraction of consumption imported' (0-100), "
        "NOT the caloric-share definition the field name now implies. Mis-display would "
        "be misleading; UI should treat as import-dependency only until replaced by FBS-sourced caloric share."
    ),
    "modeled": "Computed from sourced inputs or explicit structural assumptions.",
    "manual": "Hand-maintained snapshot from an annual or static public release.",
}

FIELD_DESCRIPTIONS = {
    "fdrs": "Composite Food Dependency Risk Score 0-100.",
    "c": "6-component structural FDRS vector [import_dep, supplier_conc, prod_trend, food_infl, climate, conflict].",
    "f2030": "Structural 2030 FDRS projection.",
    "w": "Wheat caloric share % (fraction of national caloric supply).",
    "r": "Rice caloric share %.",
    "m": "Maize caloric share %.",
    "fi": "Food inflation baseline % (yr/yr). Live sources override this at render time.",
    "net": "Net food trade balance (USD millions, +export -import).",
    "imports": "Structural food import dependency list used by the dashboard. Not a live customs feed.",
    "exports": "Structural food export basket used by the dashboard. Not a live customs feed.",
    "exportDests": "Structural export-destination ranking used by the dashboard.",
    "suppliers": "Structural top supplier countries used by the dashboard.",
    "supPct": "Supplier concentration share weights paired with suppliers[].",
}

LEGACY_SOURCE = "FoodShield embedded country dataset"
LEGACY_NOTE = (
    "Inherited from the embedded May 2026 country dataset in index.html. "
    "Needs source-by-source re-verification before it can be treated as observed trade."
)
LEGACY_TRADE_NOTE = (
    "Structural dashboard snapshot only. The UI may normalize this into a food-only display; "
    "it is not a live customs ledger."
)


def main():
    legacy_rows = _extract_legacy_rows()
    existing_meta, existing_rows = _load_existing_overlay()
    caloric_shares = _load_fbs_overlay()
    net_trade = _load_net_trade_overlay()

    countries = {}
    for iso, fields in legacy_rows.items():
        row = {}
        for field in STRUCTURAL_FIELDS:
            if field not in fields:
                continue
            row[field] = _legacy_field_meta(field, fields[field])
        countries[iso] = row

    # Keep previously curated overrides, except where a newer sourced overlay
    # supersedes them (FBS for w/r/m; FAOSTAT TCL for net).
    for iso, row in existing_rows.items():
        if iso not in legacy_rows:
            continue
        target = countries.setdefault(iso, {})
        for field, meta in row.items():
            if field in {"w", "r", "m"} and iso in caloric_shares:
                continue
            if field == "net" and iso in net_trade:
                continue
            target[field] = meta

    for iso, payload in caloric_shares.items():
        if iso not in countries:
            continue
        countries[iso]["w"] = _fbs_field_meta("w", payload)
        countries[iso]["r"] = _fbs_field_meta("r", payload)
        countries[iso]["m"] = _fbs_field_meta("m", payload)

    # FAOSTAT TCL net food trade overlay — replaces legacy hand-curated `net`.
    # Stored as integer millions USD to match the existing `c.net` integer shape,
    # so the frontend math (c.net/1000).toFixed(0) keeps producing B-USD displays.
    for iso, payload in net_trade.items():
        if iso not in countries:
            continue
        countries[iso]["net"] = _net_field_meta(payload)

    ordered = {}
    for iso in sorted(countries):
        row = countries[iso]
        ordered[iso] = {field: row[field] for field in STRUCTURAL_FIELDS if field in row}

    meta = {
        "schema_version": "v20.6",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Canonical per-country structural overlay for the FoodShield frontend. "
            "index.html remains a fallback only; this file is the preferred source for "
            "baseline structural metrics and trade snapshots."
        ),
        "quality_flags": QUALITY_FLAGS,
        "fields": FIELD_DESCRIPTIONS,
        "source_priority": [
            "Existing non-legacy countries.json overrides",
            "FAOSTAT Food Balance Sheets caloric shares (w/r/m) — when present",
            "FAOSTAT Trade Crops & Livestock net food balance (net) — when present",
            "Embedded legacy fallback extracted from index.html",
        ],
        "coverage": {
            "countries": len(ordered),
            "fbs_countries": len(caloric_shares),
            "net_trade_countries": len(net_trade),
        },
        "notes": [
            "Caloric shares (w/r/m) come from FAOSTAT FBS; net food trade from FAOSTAT TCL.",
            "Per-commodity import/supplier lists (imports, exports, suppliers, supPct) remain legacy_curated.",
            "Food-only UI menus may be normalized at render time; canonical raw structural values remain stored here.",
            "The frontend supports both the historical top-level countries schema and this v20.6 envelope schema for safe rollout.",
        ],
        "previous_schema_version": existing_meta.get("schema_version"),
    }

    # Validate before writing. If the parser regresses (e.g. swallows the
    # COUNTRIES literal again because of a future HTML edit), fail loudly
    # instead of overwriting countries.json with a 1-row stub.
    names = getattr(_extract_legacy_rows, "names", {})
    _validate_dataset(ordered, names)

    payload = {"countries": ordered}
    OUT_PATH.write_text(
        json.dumps({"_meta": meta, "data": payload}, indent=2, ensure_ascii=False)
    )
    print(f"[OK] wrote {OUT_PATH} ({len(ordered)} countries)")


def _extract_legacy_rows():
    text = HTML_PATH.read_text()
    blocks = _country_blocks(text)
    rows = {}
    names = {}
    for block in blocks:
        head = re.split(r"\n\s*ai:", block, maxsplit=1)[0]
        iso = _match_string(head, "iso")
        if not iso:
            continue
        name = _match_string(head, "name")
        names[iso] = name
        rows[iso] = {
            "fdrs": _match_number(head, "fdrs"),
            "c": _match_array(head, "c"),
            "f2030": _match_number(head, "f2030"),
            "w": _match_number(head, "w"),
            "r": _match_number(head, "r"),
            "m": _match_number(head, "m"),
            "fi": _match_number(head, "fi"),
            "net": _match_number(head, "net"),
            "imports": _match_array(head, "imports"),
            "exports": _match_array(head, "exports"),
            "exportDests": _match_array(head, "exportDests"),
            "suppliers": _match_array(head, "suppliers"),
            "supPct": _match_array(head, "supPct"),
        }
    # Stash names on the function so main() can validate without changing the
    # downstream rows schema. countries.json itself does not store country
    # names — those live with the embedded COUNTRIES array — but we still want
    # to reject blocks where the legacy literal is missing a usable name.
    _extract_legacy_rows.names = names
    return rows


REQUIRED_ISOS = {"USA", "CHN", "IND", "NLD", "DEU", "BRA", "NGA", "NER", "BOL", "BLR", "SDN"}
MIN_COUNTRY_COUNT = 150


def _validate_dataset(ordered, names):
    """Hard-fail if the structural overlay regresses below baseline coverage.

    History: the parser silently extracted only SDN after JS line-comment
    apostrophes confused the string-detection logic, producing a 1-country
    countries.json. Validation guards against that class of silent regression.
    """
    errors = []

    # 1. Minimum country count.
    n = len(ordered)
    if n < MIN_COUNTRY_COUNT:
        errors.append(
            f"coverage.countries = {n}, expected >= {MIN_COUNTRY_COUNT}. "
            "The COUNTRIES parser likely misread index.html."
        )

    # 2. Required ISO set must be present.
    missing = sorted(REQUIRED_ISOS - set(ordered.keys()))
    if missing:
        errors.append(
            f"Required ISO3 codes missing from output: {missing}. "
            f"Found {n} countries; expected all of {sorted(REQUIRED_ISOS)}."
        )

    # 3. Per-row sanity: iso must be a non-empty string, name must be present
    #    and a non-empty string in the source literal.
    bad_rows = []
    for iso in ordered:
        if not isinstance(iso, str) or not iso.strip():
            bad_rows.append({"iso": repr(iso), "reason": "iso not a non-empty string"})
            continue
        name = names.get(iso)
        if not isinstance(name, str) or not name.strip():
            bad_rows.append({"iso": iso, "name": repr(name), "reason": "missing/empty name"})
    if bad_rows:
        errors.append(f"{len(bad_rows)} rows failed iso/name validation: {bad_rows[:10]}")

    if errors:
        print("[ERROR] countries.json validation failed:", file=sys.stderr)
        for msg in errors:
            print(f"  - {msg}", file=sys.stderr)
        sys.exit(1)


def _country_blocks(text: str):
    """Extract each top-level country object literal from the embedded
    `const COUNTRIES = [...]` array in index.html.

    The previous implementation walked the array as raw characters and treated
    both `"` and `'` as string delimiters. The COUNTRIES literal contains JS
    line comments like `// Sudan's top wheat suppliers...` whose apostrophes
    were misread as the opening quote of a long string, swallowing every
    subsequent `{` / `}` until another apostrophe appeared far downstream. As
    a result only the first country (SDN) was extracted.

    This implementation:
      * Skips `//` line comments and `/* ... */` block comments.
      * Treats only `"` as a string delimiter (the COUNTRIES literal never
        uses single-quoted strings; all string values are double-quoted).
      * Counts `{` / `}` with proper string-awareness to find each top-level
        country object.
    """
    start = text.find("const COUNTRIES = [")
    if start == -1:
        raise RuntimeError("Could not find COUNTRIES array in index.html")
    arr_start = text.find("[", start)
    if arr_start == -1:
        raise RuntimeError("Could not find opening [ for COUNTRIES array")

    blocks = []
    level = 0
    current_start = None
    in_string = False
    escape = False
    pos = arr_start + 1
    n = len(text)

    while pos < n:
        ch = text[pos]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            pos += 1
            continue

        # Outside strings: skip JS comments before doing structural counting.
        if ch == "/" and pos + 1 < n:
            nxt = text[pos + 1]
            if nxt == "/":
                # Line comment — skip to end of line.
                nl = text.find("\n", pos + 2)
                pos = n if nl == -1 else nl + 1
                continue
            if nxt == "*":
                # Block comment — skip to closing */.
                end = text.find("*/", pos + 2)
                pos = n if end == -1 else end + 2
                continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            if level == 0:
                current_start = pos
            level += 1
        elif ch == "}":
            level -= 1
            if level == 0 and current_start is not None:
                blocks.append(text[current_start:pos + 1])
                current_start = None
        elif ch == "]" and level == 0:
            break
        pos += 1

    if not blocks:
        raise RuntimeError("No country objects extracted from COUNTRIES array")
    return blocks


def _load_existing_overlay():
    if not OUT_PATH.exists():
        return {}, {}
    obj = json.loads(OUT_PATH.read_text())
    meta = obj.get("_meta", {}) if isinstance(obj, dict) else {}
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, dict) and isinstance(data.get("countries"), dict):
        countries = data["countries"]
    else:
        countries = obj.get("countries", {}) if isinstance(obj, dict) else {}
    return meta, countries


def _load_fbs_overlay():
    if not FBS_PATH.exists():
        return {}
    obj = json.loads(FBS_PATH.read_text())
    data = obj.get("data", {}) if isinstance(obj, dict) else {}
    return data if isinstance(data, dict) else {}


def _load_net_trade_overlay():
    if not NET_TRADE_PATH.exists():
        return {}
    obj = json.loads(NET_TRADE_PATH.read_text())
    data = obj.get("data", {}) if isinstance(obj, dict) else {}
    return data if isinstance(data, dict) else {}


def _legacy_field_meta(field, value):
    # v20.7: w/r/m heritage values are import-dependency %, not caloric shares.
    # Tag them with a distinct quality flag so the UI does NOT mis-display them as caloric.
    if field in {"w", "r", "m"}:
        return {
            "value": value,
            "source": LEGACY_SOURCE,
            "as_of": "2026-05",
            "method": (
                "Legacy embedded import-dependency percentage (0-100) from index.html. "
                "Semantically distinct from the FAOSTAT FBS caloric-share definition this field now nominally holds."
            ),
            "quality_flag": "legacy_import_dependency",
            "note": (
                "This value represents the share of consumption that is imported (legacy meaning), "
                "NOT the caloric share of national diet (current meaning). Replaced by FAOSTAT FBS sourced "
                "values once FBS coverage reaches this country."
            ),
        }
    note = LEGACY_TRADE_NOTE if field in {"imports", "exports", "exportDests", "suppliers", "supPct"} else LEGACY_NOTE
    method = _legacy_method(field)
    return {
        "value": value,
        "source": LEGACY_SOURCE,
        "as_of": "2026-05",
        "method": method,
        "quality_flag": "legacy_curated",
        "note": note,
    }


def _legacy_method(field):
    methods = {
        "fdrs": "Legacy embedded structural score from index.html.",
        "c": "Legacy embedded 6-factor structural vector from index.html.",
        "f2030": "Legacy embedded 2030 scenario from index.html.",
        "w": "Legacy embedded caloric share baseline from index.html.",
        "r": "Legacy embedded caloric share baseline from index.html.",
        "m": "Legacy embedded caloric share baseline from index.html.",
        "fi": "Legacy embedded food inflation baseline from index.html. Live sources override at render time.",
        "net": "Legacy embedded net agri-food trade estimate from index.html.",
        "imports": "Legacy embedded commodity list extracted from index.html.",
        "exports": "Legacy embedded commodity list extracted from index.html.",
        "exportDests": "Legacy embedded destination ranking extracted from index.html.",
        "suppliers": "Legacy embedded supplier ranking extracted from index.html.",
        "supPct": "Legacy embedded supplier share weights extracted from index.html.",
    }
    return methods[field]


def _fbs_field_meta(field, payload):
    key_map = {"w": "wheat_pct", "r": "rice_pct", "m": "maize_pct"}
    label_map = {"w": "Wheat", "r": "Rice", "m": "Maize"}
    value = payload.get(key_map[field])
    if value is None:
        raise RuntimeError(f"Missing {key_map[field]} for FAOSTAT payload")
    value = round(float(value), 1)
    return {
        "value": value,
        "source": payload.get("source") or "FAOSTAT Food Balance Sheets",
        "source_url": payload.get("source_url"),
        "as_of": str(payload.get("year") or ""),
        "method": payload.get("method") or "Share of total daily caloric supply",
        "quality_flag": payload.get("quality_flag") or "sourced",
        "note": (
            f"{label_map[field]} share derived from FAOSTAT Food Balance Sheets. "
            "Direct food calories only; indirect feed conversion is excluded."
        ),
    }


def _net_field_meta(payload):
    """FAOSTAT TCL net trade overlay → countries.json `net` field.

    Stored as an integer in millions USD to match the existing legacy schema:
    the frontend reads `(c.net / 1000).toFixed(0)` to produce a "B USD" display,
    so we keep the same unit (musd) regardless of where the value came from.
    """
    musd = payload.get("value")
    if musd is None:
        raise RuntimeError("Missing 'value' in net_food_trade payload")
    # Round to int — sub-million precision adds noise we don't want in the UI.
    musd_int = int(round(float(musd)))
    item_label = (
        "Food, Total (FAOSTAT item 1842)"
        if payload.get("item_used") == 1842
        else "Agricultural Products, Total (FAOSTAT item 1841)"
    )
    return {
        "value": musd_int,
        "source": payload.get("source") or "FAOSTAT Trade Crops & Livestock",
        "source_url": payload.get("source_url"),
        "as_of": str(payload.get("year") or ""),
        "method": payload.get("method") or (
            "Net food trade = Export Value - Import Value (FAOSTAT TCL). Millions USD."
        ),
        "quality_flag": payload.get("quality_flag") or "sourced",
        "note": (
            f"Net agri-food trade balance from {item_label}. "
            f"Positive = net exporter, negative = net importer. "
            f"Source year: {payload.get('year')}. "
            f"Exports: {payload.get('exports_musd')} M USD; "
            f"Imports: {payload.get('imports_musd')} M USD."
        ),
    }


def _match_string(block: str, field: str):
    m = re.search(rf'\b{re.escape(field)}:"([^"]*)"', block)
    return m.group(1) if m else None


def _match_number(block: str, field: str):
    m = re.search(rf"\b{re.escape(field)}:(-?\d+(?:\.\d+)?)\b", block)
    if not m:
        return None
    n = float(m.group(1))
    return int(n) if n.is_integer() else n


def _match_array(block: str, field: str):
    m = re.search(rf"\b{re.escape(field)}:\[(.*?)\]", block, flags=re.S)
    if not m:
        return None
    raw = "[" + m.group(1) + "]"
    return json.loads(raw)


if __name__ == "__main__":
    main()
