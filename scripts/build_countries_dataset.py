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
    # v23 FDRS-v2 — the two new structural components, stored alongside `c` for
    # auditability. The frontend reads c.c[7]/c.c[8]; these mirror them with provenance.
    "econ_access",
    "grain_buffer",
]

# FDRS v2 weight vector (Option B, 9 components — see FDRS_V2_IMPLEMENTATION_SPEC §2).
FDRS_V2_WEIGHTS = [0.23, 0.16, 0.11, 0.09, 0.09, 0.08, 0.06, 0.12, 0.06]

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


# ─────────────────────────────────────────────────────────────────────────────
# v23 — FDRS v2 components: Economic Access (c[7]) + Grain Reserve Buffer (c[8])
# Formulas per FDRS_V2_IMPLEMENTATION_SPEC §3. Each sub-input maps a SOURCED number
# to a 0–100 fragility score (higher = more fragile); blends degrade gracefully.
# ─────────────────────────────────────────────────────────────────────────────
import math

WDI_RESERVES = "FI.RES.TOTL.MO"
WDI_DEBT_SVC = "DT.TDS.DECT.EX.ZS"


def _read_data(name):
    p = DATA_DIR / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("data", {}) or {}
    except Exception:
        return {}


def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _fx_sub(depr_12m):
    if depr_12m is None:
        return None
    return _clip((depr_12m - 5) / (40 - 5) * 100)


def _reserves_sub(m):
    if m is None:
        return None
    if m >= 12:
        return 0.0
    if m <= 1:
        return 100.0
    return _clip(((12 - m) / 11) ** 1.6 * 100)


def _debt_sub(dsx):
    if dsx is None:
        return None
    return _clip((dsx - 5) / (35 - 5) * 100)


def _income_sub(gnipc):
    if gnipc is None:
        return None
    g = _clip(gnipc, 1000, 40000)
    return _clip(100 * (math.log10(40000) - math.log10(g)) / (math.log10(40000) - math.log10(1000)))


def compute_economic_access(iso, fx, wdi, hdi):
    """Economic Access fragility 0–100 + provenance. Spec §3.1–3.2."""
    fx_row = (fx.get(iso) or {})
    depr = ((fx_row.get("structural") or {}).get("depr_12m_pct"))
    fx_v = _fx_sub(depr)
    res_v = _reserves_sub((wdi.get(iso, {}).get(WDI_RESERVES, {}) or {}).get("value"))
    debt_v = _debt_sub((wdi.get(iso, {}).get(WDI_DEBT_SVC, {}) or {}).get("value"))
    inc_v = _income_sub((hdi.get(iso, {}).get("gnipc", {}) or {}).get("value"))

    subs = [("fx", fx_v, 0.30), ("reserves", res_v, 0.30),
            ("debt", debt_v, 0.25), ("income", inc_v, 0.15)]
    present = [(k, v, w) for k, v, w in subs if v is not None]
    if not present:
        return None, {"fx": "heritage", "reserves": "heritage",
                      "debt": "heritage", "income": "heritage", "_n_sourced": 0}
    wsum = sum(w for _, _, w in present)
    val = round(sum(v * w for _, v, w in present) / wsum, 1)
    prov = {k: ("sourced" if v is not None else "heritage") for k, v, _ in subs}
    prov["_n_sourced"] = len(present)
    return val, prov


def compute_grain_buffer(iso, psd):
    """Grain Reserve Buffer fragility 0–100 from USDA PSD stocks-to-use. Spec §3.1.
    stocks-to-use = ending stocks / consumption across tracked staples; LOW s/u = fragile.
    """
    crow = psd.get(iso)
    if not crow:
        return None, "heritage"
    tot_stocks = tot_cons = 0.0
    for cmd, rec in crow.items():
        if not isinstance(rec, dict):
            continue
        s = rec.get("stocks_kt")
        c = rec.get("consumption_kt")
        if s is not None and c:
            tot_stocks += s
            tot_cons += c
    if tot_cons <= 0:
        return None, "heritage"
    stu = tot_stocks / tot_cons          # stocks-to-use ratio
    # Normalise: s/u >= 0.40 (ample, ~5 months) -> 0 ; <= 0.05 (very thin) -> 100.
    frag = _clip((0.40 - stu) / (0.40 - 0.05) * 100)
    return round(frag, 1), "sourced"


def _component_meta(field, value, prov, method, source):
    flag = "sourced" if (isinstance(prov, str) and prov == "sourced") else (
        "sourced" if (isinstance(prov, dict) and prov.get("_n_sourced", 0) >= 3) else
        "partial" if (isinstance(prov, dict) and prov.get("_n_sourced", 0) == 2) else "heritage")
    return {
        "value": value, "source": source, "as_of": "2024",
        "method": method, "quality_flag": flag,
        "sub_provenance": prov if isinstance(prov, dict) else {"status": prov},
    }


def _fdrs_v2(cv):
    """v2 score from a length-9 component vector. Mirrors index.html fdrsV2()."""
    base = sum(FDRS_V2_WEIGHTS[i] * (cv[i] if i < len(cv) and cv[i] is not None else 0)
               for i in range(9))
    c0 = cv[0] if len(cv) > 0 and cv[0] is not None else 0
    c7 = cv[7] if len(cv) > 7 and cv[7] is not None else 0
    amp = min(6 * (c0 / 100) * (c7 / 100), 6)
    return int(round(_clip(base + amp)))


def main():
    legacy_rows = _extract_legacy_rows()
    existing_meta, existing_rows = _load_existing_overlay()
    caloric_shares = _load_fbs_overlay()
    net_trade = _load_net_trade_overlay()
    food_inflation = _load_food_inflation_overlay()

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
            if field == "fi" and iso in food_inflation:
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

    # Food inflation overlay — yoy % from WFP / Eurostat / FAOSTAT live feeds.
    # Replaces the legacy hand-curated `fi` where a sourced value exists, so the
    # food-inflation FDRS component stops being a 263/264 legacy field.
    for iso, payload in food_inflation.items():
        if iso not in countries:
            continue
        countries[iso]["fi"] = _fi_field_meta(payload)

    # ── v23 FDRS v2: compute Economic Access (c[7]) + Grain Reserve Buffer (c[8]),
    # extend each country's component vector to length 9, recompute fdrs with the
    # v2 formula (BASE + amplifier). Components missing data degrade to heritage and
    # contribute 0 (resilient) rather than a fabricated value. See spec §2/§3/§7.
    fx = _read_data("fx_rates.json")
    wdi = _read_data("worldbank_bulk.json")
    hdi = _read_data("hdi.json")
    psd = _read_data("usda_psd.json")

    v2_applied = ea_sourced = gb_sourced = 0
    for iso, row in countries.items():
        if iso.startswith("US-"):
            continue
        c_meta = row.get("c")
        if not c_meta or not isinstance(c_meta.get("value"), list):
            continue
        cv = list(c_meta["value"])
        # ensure SCE slot c[6] exists (legacy vectors are length 6); leave 0 if absent —
        # the frontend supplies live SCE at render time, but we store a placeholder so
        # the stored vector is a consistent length 9.
        while len(cv) < 7:
            cv.append(0)

        ea_val, ea_prov = compute_economic_access(iso, fx, wdi, hdi)
        gb_val, gb_prov = compute_grain_buffer(iso, psd)

        cv = cv[:7] + [ea_val if ea_val is not None else 0,
                       gb_val if gb_val is not None else 0]
        c_meta["value"] = cv

        if ea_val is not None:
            row["econ_access"] = _component_meta(
                "econ_access", ea_val, ea_prov,
                "Economic Access fragility: blend of FX 12m depreciation, reserves-in-months "
                "(convex cliff), debt-service %% of exports, and GNI per capita (log). "
                "No CPI (price stress stays in Food Inflation).",
                "WDI (reserves, debt-service) + UNDP HDI (income) + FX pipeline")
            if isinstance(ea_prov, dict) and ea_prov.get("_n_sourced", 0) >= 3:
                ea_sourced += 1
        if gb_val is not None:
            row["grain_buffer"] = _component_meta(
                "grain_buffer", gb_val, gb_prov,
                "Grain Reserve Buffer fragility: 100 = thin stocks-to-use (ending stocks / "
                "consumption across staples), 0 = ample cushion. Source: USDA PSD.",
                "USDA PSD ending stocks / consumption")
            gb_sourced += 1

        # Recompute the structural score under v2 from the length-9 vector.
        new_fdrs = _fdrs_v2(cv)
        if isinstance(row.get("fdrs"), dict):
            row["fdrs"]["value"] = new_fdrs
            row["fdrs"]["method"] = ("FDRS v2 (9-component weighted composite + "
                                     "import×economic-access amplifier). See methodology.")
            # The score is now COMPUTED from a blend of sourced (econ_access, net,
            # w/r/m, suppliers) and still-legacy (c[0..5]) inputs — so it's "modeled",
            # not "legacy_curated". The frontend recomputes with live SCE + nowcast.
            row["fdrs"]["quality_flag"] = "modeled"
            row["fdrs"]["note"] = ("Computed by the FDRS v2 formula from the component "
                                   "vector. Some components are sourced, some still legacy; "
                                   "displayed score adds live supply-chain exposure + nowcast.")
        v2_applied += 1

    print(f"[v2] recomputed FDRS for {v2_applied} countries; "
          f"Economic Access sourced(>=3/4): {ea_sourced}; Grain Buffer sourced: {gb_sourced}")

    ordered = {}
    for iso in sorted(countries):
        row = countries[iso]
        ordered[iso] = {field: row[field] for field in STRUCTURAL_FIELDS if field in row}

    meta = {
        "schema_version": "v20.6",
        "release_version": "v23",
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


def _load_food_inflation_overlay():
    """Year-over-year food inflation % per ISO3, from live feeds.

    Source priority (highest wins): WFP per-country > Eurostat food HICP >
    FAOSTAT food CPI. Returns {iso3: {"value": float, "source": str,
    "as_of": str|None}}. Only countries with a real number are included;
    everyone else keeps their legacy_curated `fi`.
    """
    def _read(name):
        p = DATA_DIR / name
        if not p.exists():
            return {}
        try:
            obj = json.loads(p.read_text())
        except Exception:
            return {}
        return obj.get("data", obj) if isinstance(obj, dict) else {}

    out = {}
    # 3. FAOSTAT (lowest priority — written first so higher sources overwrite)
    for iso, rec in _read("faostat_food.json").items():
        if not isinstance(rec, dict):
            continue
        v = rec.get("food_cpi_yoy") or rec.get("food_inflation_yoy") or rec.get("yoy")
        if v is not None:
            try:
                out[iso.upper()] = {"value": round(float(v), 1),
                                    "source": "FAOSTAT Consumer Price Indices (food CPI, yoy)",
                                    "as_of": rec.get("month") or rec.get("year")}
            except (TypeError, ValueError):
                pass
    # 2. Eurostat (EU/EEA member states)
    for iso, rec in _read("eurostat_food.json").items():
        if not isinstance(rec, dict):
            continue
        v = rec.get("food_hicp_yoy_pct")
        if v is not None:
            try:
                out[iso.upper()] = {"value": round(float(v), 1),
                                    "source": "Eurostat food HICP (yoy %)",
                                    "as_of": rec.get("month")}
            except (TypeError, ValueError):
                pass
    # 1. WFP per-country (highest priority — broadest crisis-country coverage)
    for iso, rec in _read("wfp_country.json").items():
        if not isinstance(rec, dict):
            continue
        v = (rec.get("food_inflation") or rec.get("food_inflation_yoy")
             or rec.get("headline_food_inflation"))
        if v is not None:
            try:
                out[iso.upper()] = {"value": round(float(v), 1),
                                    "source": "WFP per-country food inflation",
                                    "as_of": rec.get("month") or rec.get("as_of")}
            except (TypeError, ValueError):
                pass
    return out


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


def _fi_field_meta(payload):
    """Sourced food-inflation envelope for the `fi` field."""
    return {
        "value": payload["value"],
        "source": payload.get("source") or "Live food-inflation feed",
        "source_url": None,
        "as_of": payload.get("as_of"),
        "method": "Year-over-year food price inflation (%), latest available month.",
        "quality_flag": "sourced",
        "note": (
            "Sourced from live WFP / Eurostat / FAOSTAT food-price feeds, replacing "
            "the legacy embedded baseline. Updated on each data-refresh cycle."
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
