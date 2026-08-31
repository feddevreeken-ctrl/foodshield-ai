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
from trade_schema import normalize_trade_surface

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
    # v83 — c[0] and c[1] are sourced now, so their provenance has to survive to
    # the client. Without this the two heaviest components in the score would
    # still render with no badge, no source and no vintage, which is the exact
    # complaint the audit raised about them.
    "import_dep",
    "supplier_conc",
    "prod_trend",
    # Pinned so the sourced/heritage blend cannot ratchet across nightly rebuilds.
    "c_heritage",
]

# FDRS v2 weight vector (Option B, 9 components — see FDRS_V2_IMPLEMENTATION_SPEC §2).
FDRS_V2_WEIGHTS = [0.23, 0.16, 0.11, 0.09, 0.09, 0.08, 0.06, 0.12, 0.06]

QUALITY_FLAGS = {
    "sourced": "Verified against a public dataset; source_url + as_of populated.",
    "partial": "Uses real source lineage but the audit trail is incomplete or composite; basis/unit metadata is present, but treat with caution.",
    "legacy_curated": "Hand-authored heritage value, not yet re-verified. Treated as draft.",
    "legacy_import_dependency": (
        "Heritage value originally meant 'fraction of consumption imported' (0-100), "
        "NOT the caloric-share definition the field name now implies. Mis-display would "
        "be misleading; UI should treat as import-dependency only until replaced by FBS-sourced caloric share."
    ),
    "heritage": "Inherited historical value with no current direct-source audit trail.",
    "modeled": "Computed from sourced inputs or explicit structural assumptions.",
    "manual": "Hand-maintained snapshot from an annual or static public release.",
}

FIELD_DESCRIPTIONS = {
    "fdrs": "Composite Food Dependency Risk Score 0-100.",
    "c": "9-component structural FDRS vector [import_dep, supplier_conc, prod_trend, food_infl, climate, conflict, supply_chain_exposure, econ_access, grain_buffer].",
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


# v33 audit fix — months-of-import-cover is not a fragility signal for economies that
# issue (or belong to a union that issues) a global reserve currency: they settle
# imports in their own money and structurally hold thin FX reserves (the US holds ~2
# months and was scoring riskier than Venezuela). Drop the reserves sub for them and
# let fx/debt/income carry the score. Dollarized/euroized NON-issuers (ECU, SLV, PAN,
# MNE, XKX) are NOT exempt — they can't print, so reserves matter more, not less.
RESERVE_CURRENCY_ISSUERS = {
    "USA", "JPN", "GBR", "CHE",
    # euro area members (ECB backstop)
    "DEU", "FRA", "ITA", "ESP", "NLD", "BEL", "AUT", "IRL", "PRT", "GRC",
    "FIN", "LUX", "SVK", "SVN", "EST", "LVA", "LTU", "CYP", "MLT", "HRV",
}


def compute_economic_access(iso, fx, wdi, hdi):
    """Economic Access fragility 0–100 + provenance. Spec §3.1–3.2."""
    fx_row = (fx.get(iso) or {})
    depr = ((fx_row.get("structural") or {}).get("depr_12m_pct"))
    fx_v = _fx_sub(depr)
    res_v = _reserves_sub((wdi.get(iso, {}).get(WDI_RESERVES, {}) or {}).get("value"))
    debt_v = _debt_sub((wdi.get(iso, {}).get(WDI_DEBT_SVC, {}) or {}).get("value"))
    inc_v = _income_sub((hdi.get(iso, {}).get("gnipc", {}) or {}).get("value"))

    if iso in RESERVE_CURRENCY_ISSUERS:
        res_v = None

    # v33 audit fix — the reserves cliff is a buffer test for economies that NEED an
    # FX buffer. High-income economies with deep capital markets and hard-float
    # currencies (AUS holds ~2 months by choice, CAN, SWE, NZL...) are not fragile
    # the way a low-income 2-months country is — the cliff was scoring Australia
    # alongside Mozambique. Skip the reserves sub above the World Bank high-income
    # line (GNIpc ≈ $14k); genuine stress in a rich economy still surfaces through
    # the one-sided FX sub and the debt sub.
    gnipc_raw = (hdi.get(iso, {}).get("gnipc", {}) or {}).get("value")
    if gnipc_raw is not None and gnipc_raw >= 14000:
        res_v = None

    # v33 audit fix — FX is a ONE-SIDED stress indicator. A large 12m depreciation is
    # hard evidence of access fragility; a flat official rate is NOT evidence of
    # strength (administered/frozen regimes — SDG pegged at 600, official CUP — read
    # "stable" while the parallel market collapses). _fx_sub is already 0 for any
    # depreciation ≤5%, so a zero contributes nothing but its 0.30 weight, which
    # DILUTES the reserves/debt/income signal exactly for the crisis economies the
    # component exists to flag. Treat fx_v==0 as "no signal" instead.
    if fx_v is not None and fx_v <= 0:
        fx_v = None

    subs = [("fx", fx_v, 0.30), ("reserves", res_v, 0.30),
            ("debt", debt_v, 0.25), ("income", inc_v, 0.15)]
    present = [(k, v, w) for k, v, w in subs if v is not None]
    # v33 audit fix — a reserves-only score is too thin to assert anything extreme
    # (Cayman scored 100, tied with South Sudan, off a single input). One sub with no
    # corroboration from income/fx/debt → treat as unscored rather than authoritative.
    if len(present) == 1 and present[0][0] == "reserves":
        return None, {"fx": "heritage", "reserves": "sourced",
                      "debt": "heritage", "income": "heritage", "_n_sourced": 0}
    if not present:
        return None, {"fx": "heritage", "reserves": "heritage",
                      "debt": "heritage", "income": "heritage", "_n_sourced": 0}
    wsum = sum(w for _, _, w in present)
    val = round(sum(v * w for _, v, w in present) / wsum, 1)
    prov = {k: ("sourced" if v is not None else "heritage") for k, v, _ in subs}
    prov["_n_sourced"] = len(present)
    return val, prov


# v34 — EU members: USDA PSD stopped reporting them individually after accession
# (the bloc reports as "European Union", FAS code E4, which the parser used to drop).
# Their residual per-country rows are 1990–1998 relics — the Netherlands' grain
# buffer was being scored 100 off a single 1990 soybeans row presented as current.
EU27_MEMBERS = {
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA",
    "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD",
    "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE",
}

PSD_MIN_YEAR = 2015   # v34 — records older than this are relics, not current stocks


def compute_grain_buffer(iso, psd):
    """Grain Reserve Buffer fragility 0–100 from USDA PSD stocks-to-use. Spec §3.1.
    stocks-to-use = ending stocks / consumption across tracked staples; LOW s/u = fragile.
    v34: ignores pre-2015 relic rows; EU members fall back to the EU-27 bloc aggregate
    (single market, shared CAP storage) and are flagged 'partial' (bloc-level value).
    """
    def _stu(crow):
        tot_stocks = tot_cons = 0.0
        for cmd, rec in (crow or {}).items():
            if not isinstance(rec, dict):
                continue
            yr = rec.get("year")
            if yr is not None and yr < PSD_MIN_YEAR:
                continue
            s = rec.get("stocks_kt")
            c = rec.get("consumption_kt")
            if s is not None and c:
                tot_stocks += s
                tot_cons += c
        return (tot_stocks / tot_cons) if tot_cons > 0 else None

    stu = _stu(psd.get(iso))
    prov = "sourced"
    if stu is None and iso in EU27_MEMBERS:
        stu = _stu(psd.get("EU27"))
        prov = "partial"          # bloc-level aggregate applied to a member state
    if stu is None:
        return None, "heritage"
    # Normalise: s/u >= 0.40 (ample, ~5 months) -> 0 ; <= 0.05 (very thin) -> 100.
    frag = _clip((0.40 - stu) / (0.40 - 0.05) * 100)
    return round(frag, 1), prov


def _component_meta(field, value, prov, method, source):
    flag = "sourced" if (isinstance(prov, str) and prov == "sourced") else (
        "partial" if (isinstance(prov, str) and prov == "partial") else (
        "sourced" if (isinstance(prov, dict) and prov.get("_n_sourced", 0) >= 3) else
        "partial" if (isinstance(prov, dict) and prov.get("_n_sourced", 0) == 2) else "heritage"))
    return {
        "value": value, "source": source, "as_of": "2024",
        "method": method, "quality_flag": flag,
        "sub_provenance": prov if isinstance(prov, dict) else {"status": prov},
    }


def _fdrs_v2(cv):
    """v2 score from a length-9 component vector. Mirrors index.html fdrsV2()."""
    # v79 — mirrors the index.html fix: a missing component no longer reads as
    # perfect resilience. We renormalise to the weight actually observed instead
    # of substituting 0, and skip the amplifier when either of its two inputs is
    # unobserved. A stored 0 stays a real value; only None is a gap.
    def _obs(i):
        v = cv[i] if i < len(cv) else None
        return v if isinstance(v, (int, float)) and v == v else None

    base = 0.0
    w_obs = 0.0
    for i in range(9):
        v = _obs(i)
        if v is not None:
            base += FDRS_V2_WEIGHTS[i] * v
            w_obs += FDRS_V2_WEIGHTS[i]
    if w_obs <= 0:
        return 0
    if w_obs < 1:
        base = base / w_obs
    c0, c7 = _obs(0), _obs(7)
    amp = min(6 * (c0 / 100) * (c7 / 100), 6) if (c0 is not None and c7 is not None) else 0
    # half-up rounding to match JS Math.round in index.html::fdrsV2 (was int(round(...)),
    # i.e. banker's rounding, which could disagree with the client by 1 on exact .5 values).
    return int(_clip(base + amp) + 0.5)


def main():
    legacy_rows = _extract_legacy_rows()
    existing_meta, existing_rows = _load_existing_overlay()
    caloric_shares = _load_fbs_overlay()
    net_trade = _load_net_trade_overlay()
    food_inflation = _load_food_inflation_overlay()
    import_dep = _load_import_dependency()
    prod_trend = _load_prod_trend()

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
            cv.append(None)   # v35 — SCE placeholder is unscored (null), not 0; the
                              # frontend injects live SCE at render time (inline convention)

        ea_val, ea_prov = compute_economic_access(iso, fx, wdi, hdi)
        gb_val, gb_prov = compute_grain_buffer(iso, psd)

        # v35 — unscored stays null. Writing 0 for an unscored component made a
        # data gap (Haiti grain buffer: no PSD rows) indistinguishable from a
        # sourced zero (China grain buffer: huge stocks, s/u >= 0.40). _fdrs_v2
        # treats null exactly like 0 (counts 0), so scores are unchanged — but the
        # UI can now render "NO DATA · COUNTS 0" instead of a confident-looking 0.
        cv = cv[:7] + [ea_val, gb_val]

        # v83 — PIN THE HERITAGE BASELINE.
        #
        # This builder reads its own previous output: `c_meta = row.get("c")`
        # above comes from data/countries.json, which the last run wrote. That
        # makes any blend non-idempotent — blending 0.4*previous + 0.6*sourced
        # every night would ratchet each component toward the sourced value and
        # silently converge on it within about a week, so the published weights
        # would drift without a single line of code changing. Exactly the class
        # of silent drift this audit was cleaning up.
        #
        # So the ORIGINAL curated vector is captured once and carried forward
        # untouched. Every subsequent blend reads from it, never from the last
        # output, and re-running the builder any number of times is a no-op.
        _heritage_meta = row.get("c_heritage")
        if isinstance(_heritage_meta, dict) and isinstance(_heritage_meta.get("value"), list):
            heritage_cv = list(_heritage_meta["value"])
        else:
            heritage_cv = list(cv)
        row["c_heritage"] = {
            "value": heritage_cv,
            "source": LEGACY_SOURCE,
            "as_of": "2026-05",
            "method": ("The original hand-authored 9-component vector, pinned so that "
                       "sourced-vs-heritage blending stays idempotent across nightly rebuilds. "
                       "This is an AUTHORING date, not a data vintage — that is the whole "
                       "reason these components are being replaced."),
            "quality_flag": "heritage",
        }

        # v79 — WIRE THE LIVE FOOD-INFLATION FEED INTO THE COMPONENT IT NAMES.
        # The overlay above updated only the display field `fi`, so c[3] stayed a
        # hand-curated legacy number that ignored the live reading entirely:
        # Egypt showed 60.5% inflation while scoring 38, Venezuela 38.1% while
        # scoring 95, South Sudan 118.4% while scoring 45. The component is
        # called food_infl; it should be a function of observed food inflation.
        # Only a genuinely sourced reading may move the component; the 88
        # countries still on the embedded baseline keep their curated c[3]
        # (quality_flag "modeled") rather than having a legacy number laundered
        # through a live-looking transform.
        _fi_meta = row.get("fi") or {}
        if _fi_meta.get("quality_flag") == "sourced":
            _fi_score = _fi_to_component(_fi_meta.get("value"))
            if _fi_score is not None:
                cv[3] = _fi_score

        # v83 — SAME TREATMENT FOR c[0], THE HEAVIEST COMPONENT IN THE SCORE.
        #
        # import_dep carries weight 0.23 and, for all 214 countries, was a
        # hand-authored literal: no source, no data year, stamped as_of
        # "2026-05" (an authoring date). It also drives the import-dependency x
        # economic-access amplifier, so it moves the headline non-linearly.
        # An audit put it plainly: nothing measurable determined half of
        # Nigeria's and DR Congo's score, so nothing measurable could move it.
        #
        # FAOSTAT's cereal import dependency ratio is the standard published
        # measure and comes from a bulk file this pipeline already downloads.
        # Same guard as c[3]: only a genuinely sourced reading may move the
        # component; everyone else keeps the curated baseline rather than having
        # a legacy number laundered through a live-looking transform.
        # v83 — c[1] supplier_conc, weight 0.16, likewise hand-authored for all
        # 214 countries. Unlike c[0] this needed no new source: supPct has been
        # sourced from UN Comtrade for 180 countries all along and simply never
        # reached the component it describes.
        #
        # Metric is HHI (sum of squared shares) rather than top-3 share, because
        # top-3 cannot separate 60/20/10 from 34/33/33 — both give 90, and they
        # are very different exposures. HHI is then multiplied by import
        # dependency: concentrated suppliers only matter to the extent a country
        # actually depends on imports, which is the same reasoning
        # audit_provenance.py already applies when it flags exporters scored
        # high on raw concentration.
        #
        # Shares are truncated to the top 5 upstream, which biases HHI upward,
        # so they are renormalised over the observed set and the basis is
        # recorded rather than silently mixed.
        # v83 — c[2] prod_trend, weight 0.11, the last of the three
        # hand-authored components. FAOSTAT's gross food production index gives
        # a real slope per country; a null trend stays null rather than becoming
        # a flattering 0.
        _pt_rec = prod_trend.get(iso)
        if _pt_rec and _pt_rec.get("prod_trend_score") is not None:
            try:
                cv[2] = _blend_sourced(heritage_cv[2], float(_pt_rec["prod_trend_score"]))
                row["prod_trend"] = {
                    "value": cv[2],
                    "source": _pt_rec.get("source"),
                    "source_url": _pt_rec.get("source_url"),
                    "as_of": str(_pt_rec.get("year_latest") or ""),
                    "method": _pt_rec.get("method"),
                    "quality_flag": "sourced",
                    "sub_provenance": {
                        "status": "sourced",
                        "trend_pct_per_year": _pt_rec.get("trend_pct_per_year"),
                        "window": f"{_pt_rec.get('year_from')}-{_pt_rec.get('year_latest')}",
                        "points": _pt_rec.get("points"),
                    },
                }
            except (TypeError, ValueError):
                pass

        _sup_meta = row.get("supPct") or {}
        _sup_shares = _sup_meta.get("value")
        if (_sup_meta.get("quality_flag") == "sourced"
                and isinstance(_sup_shares, list) and _sup_shares):
            try:
                _sh = [float(x) for x in _sup_shares if x is not None and float(x) > 0]
            except (TypeError, ValueError):
                _sh = []
            _tot = sum(_sh)
            if _sh and _tot > 0:
                _frac = [x / _tot for x in _sh]          # renormalise over observed
                _hhi = sum(f * f for f in _frac)          # 1/n .. 1.0
                _n = len(_frac)
                # Plain HHI on a 0-100 scale. An earlier cut of this multiplied
                # by import dependency, which is wrong twice over: it double
                # counts c[0] (weight 0.23, already dependency) and it collapses
                # a diversified importer and a concentrated self-sufficient
                # producer onto the same number. The nine components are meant
                # to be separable; concentration is concentration.
                cv[1] = _blend_sourced(heritage_cv[1], _hhi * 100.0)
                row["supplier_conc"] = {
                    "value": cv[1],
                    "source": _sup_meta.get("source"),
                    "source_url": _sup_meta.get("source_url"),
                    "as_of": _sup_meta.get("as_of"),
                    "method": ("Herfindahl-Hirschman index of import shares (sum of squared "
                               "shares), renormalised over the observed top-5 partners and "
                               "expressed 0-100. Chosen over top-3 share because top-3 cannot "
                               "separate 60/20/10 from 34/33/33 — both read 90, and they are "
                               "very different exposures."),
                    "quality_flag": "sourced",
                    "sub_provenance": {
                        "status": "sourced",
                        "hhi": round(_hhi, 4),
                        "partners_observed": _n,
                        "basis": "top5_renormalised",
                        "supplier_basis": _sup_meta.get("_supplier_basis"),
                    },
                }
            
        _dep_rec = import_dep.get(iso)
        if _dep_rec and _dep_rec.get("import_dependency_pct") is not None:
            try:
                cv[0] = _blend_sourced(heritage_cv[0], float(_dep_rec["import_dependency_pct"]))
            except (TypeError, ValueError):
                _dep_rec = None
            if _dep_rec:
                # Built by hand rather than through _component_meta(), which
                # stamps as_of "2024" unconditionally for every field it touches
                # — the defect that had Afghanistan's 2020 reserves and Iran's
                # 1982 reserves both presenting as 2024 data. This carries the
                # real FBS reference year.
                row["import_dep"] = {
                    "value": cv[0],
                    "source": _dep_rec.get("source"),
                    "source_url": _dep_rec.get("source_url"),
                    "as_of": str(_dep_rec.get("year") or ""),
                    "method": _dep_rec.get("method"),
                    "quality_flag": "sourced",
                    "sub_provenance": {
                        "status": "sourced",
                        "basis": "faostat_fbs_cereal_balance",
                        "imports_kt": _dep_rec.get("imports_kt"),
                        "domestic_supply_kt": _dep_rec.get("domestic_supply_kt"),
                        "cereals": _dep_rec.get("cereals"),
                    },
                }

        c_meta["value"] = cv
        c_meta["method"] = (
            "FDRS v2 structural component vector "
            "[import_dep, supplier_conc, prod_trend, food_infl, climate, conflict, "
            "supply_chain_exposure, econ_access, grain_buffer]."
        )
        c_meta["quality_flag"] = "modeled"
        c_meta["note"] = (
            "Stored as the canonical 9-component structural vector. The frontend may inject "
            "live supply-chain exposure and nowcast adjustments at render time; this file "
            "preserves the structural baseline plus sourced v2 components."
        )

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

    trade_changes = normalize_trade_surface(countries)

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
            "Trade-facing fields carry explicit basis/unit metadata via trade_schema.py so value-ranked and quantity-ranked rows are not silently mixed.",
            "Rows labeled partial are derived from real source lineage but are not yet directly auditable from a single public extract.",
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
    print(f"[trade] normalized {len(trade_changes)} trade field envelopes")
    print(f"[OK] wrote {OUT_PATH} ({len(ordered)} countries)")


def _extract_legacy_rows():
    text = HTML_PATH.read_text()
    blocks = _country_blocks(text)
    rows = {}
    names = {}
    for block in blocks:
        # v48 — was `ai:`. The field holds hand-written country research citing
        # IPC, FAO GIEWS and WFP, and the UI labels it "Analysis · deterministic,
        # no LLM". The old key name told any maintainer grepping the file the
        # opposite. Renamed in index.html; this split must track it, or head
        # becomes the entire block and iso/name extraction reads narrative prose.
        head = re.split(r"\n\s*narrative:", block, maxsplit=1)[0]
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


    # v79 — piecewise-linear map from observed year-on-year food inflation (%)
# to the 0-100 FDRS food-inflation component. Monotone by construction and
# stated here rather than hidden in a curated table. Anchors chosen so that
# ~2% reads as benign, double digits as real stress, and hyperinflation
# saturates near 100 without a discontinuity.
# The floor is 10, not 0: food-price risk is never actually zero, and observed
# deflation in a crisis economy (Afghanistan -12.1%, Syria -12.4%) reflects
# collapsed purchasing power, not a food system under no price pressure. A
# score of 0 there would be a worse lie than the legacy number it replaces.
_FI_ANCHORS = [(-10.0, 10.0), (0.0, 15.0), (5.0, 25.0), (15.0, 50.0),
               (30.0, 70.0), (60.0, 85.0), (120.0, 95.0), (250.0, 100.0)]


def _fi_to_component(pct):
    """Observed food-inflation % -> 0-100 component score, or None if unusable."""
    if not isinstance(pct, (int, float)) or pct != pct:
        return None
    if pct <= _FI_ANCHORS[0][0]:
        return _FI_ANCHORS[0][1]
    if pct >= _FI_ANCHORS[-1][0]:
        return _FI_ANCHORS[-1][1]
    for (x0, y0), (x1, y1) in zip(_FI_ANCHORS, _FI_ANCHORS[1:]):
        if x0 <= pct <= x1:
            span = (x1 - x0) or 1.0
            return round(y0 + (y1 - y0) * (pct - x0) / span)
    return None


def _load_import_dependency():
    """Cereal import dependency per ISO3, written by refresh_faostat_fbs.py."""
    p = DATA_DIR / "faostat_import_dep.json"
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text())
    except Exception:
        return {}
    data = obj.get("data", obj) if isinstance(obj, dict) else {}
    return {k.upper(): v for k, v in data.items() if isinstance(v, dict)}


SOURCED_BLEND = 0.6


def _blend_sourced(heritage, sourced):
    """Blend a newly-sourced component with the curated baseline it replaces.

    v83 — this is the pattern index.html already uses for the climate and
    conflict components (`c._cOriginal[i] * 0.4 + sourcedScore * 0.6`), applied
    to the three components that had no source at all.

    Why blend rather than replace outright. The measured values are correct and
    the provenance win is real, but they measure a NARROWER construct than the
    literals did. FAO's cereal import dependency ratio asks "how much of the
    grain you eat comes from abroad"; the hand-authored number it replaces was
    carrying an unstated blend of that plus humanitarian context. Swapping one
    for the other wholesale moved Nigeria from rank 37 to 130 and lifted Cuba,
    Jamaica and Kiribati into the top ten — all of which is DEFENSIBLE on a pure
    supply-disruption reading (Jamaica really does import ~100% of its cereals
    from a concentrated set of partners, Nigeria really does grow its own), but
    it silently redefines what the published headline means, in one release,
    without saying so.

    At 0.6 the measurement dominates and the direction of travel is toward
    sourced data, which is the stated goal; the remaining 0.4 keeps the
    published ranking continuous while that transition is documented. Acute
    crisis is not lost either way — it enters through the nowcast (IPC, FEWS,
    and as of v83 live conflict), which is where a current emergency belongs
    rather than inside a structural component.
    """
    try:
        s_val = max(0.0, min(100.0, float(sourced)))
    except (TypeError, ValueError):
        return heritage
    if heritage is None:
        return int(round(s_val))
    try:
        h_val = float(heritage)
    except (TypeError, ValueError):
        return int(round(s_val))
    return int(round(h_val * (1.0 - SOURCED_BLEND) + s_val * SOURCED_BLEND))


def _load_prod_trend():
    """Food production trend per ISO3, written by refresh_faostat_prodindex.py."""
    p = DATA_DIR / "faostat_prod_index.json"
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text())
    except Exception:
        return {}
    data = obj.get("data", obj) if isinstance(obj, dict) else {}
    return {k.upper(): v for k, v in data.items() if isinstance(v, dict)}


def _load_food_inflation_overlay():
    """Year-over-year food inflation % per ISO3, from live feeds.

    Source priority (highest wins): World Bank RTFP > WFP per-country >
    Eurostat food HICP > FAOSTAT food CPI. Returns {iso3: {"value": float,
    "source": str, "as_of": str|None}}. Only countries with a real number are
    included; everyone else keeps their legacy_curated `fi`.

    v83 — RTFP added at the top, and it matters most for the countries this
    dashboard is about. The FAOSTAT rung underneath it is measured badly: 158 of
    162 countries carry year_latest 2026 with only 3 months in it, so its
    "year-on-year" is a 3-month average against a full prior year — a
    seasonality artefact, not a price signal — and 146 of its 176 rows carry no
    as_of at all. RTFP is market-level, monthly, updated weekly, and every row
    is stamped with a real date. It covers 37 countries, which is exactly the
    crisis set the FAOSTAT artefact distorted worst: Ethiopia, Sudan, Somalia,
    South Sudan, Chad, Haiti, Afghanistan, Yemen's neighbours.
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

    # v79 — plausibility band. A higher-priority source must not win with a
    # physically impossible reading: WFP reports -97.6% YoY food inflation for
    # South Sudan (prices cannot fall 97.6%) while FAOSTAT reports +118.4% for
    # the same country. Rejecting the bad value lets the lower-priority source
    # stand, because sources are written lowest-priority-first. The upper bound
    # stays generous — Argentina genuinely prints ~250%.
    FI_MIN, FI_MAX = -50.0, 1000.0

    def _plausible(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f != f or f < FI_MIN or f > FI_MAX:   # NaN or out of band
            return None
        return f

    out = {}
    # 3. FAOSTAT (lowest priority — written first so higher sources overwrite)
    for iso, rec in _read("faostat_food.json").items():
        if not isinstance(rec, dict):
            continue
        # v79 — refresh_faostat.py writes "food_cpi_yoy_pct"; this lookup asked
        # for "food_cpi_yoy" and never matched, so all 162 FAOSTAT rows were
        # silently dropped and only Eurostat's 30 reached the overlay. The
        # legacy aliases stay as fallbacks.
        v = (rec.get("food_cpi_yoy_pct") or rec.get("food_cpi_yoy")
             or rec.get("food_inflation_yoy") or rec.get("yoy"))
        f = _plausible(v)
        if f is not None:
            out[iso.upper()] = {"value": round(f, 1),
                                "source": "FAOSTAT Consumer Price Indices (food CPI, yoy)",
                                "as_of": rec.get("month") or rec.get("year")}
    # 2. Eurostat (EU/EEA member states)
    for iso, rec in _read("eurostat_food.json").items():
        if not isinstance(rec, dict):
            continue
        f = _plausible(rec.get("food_hicp_yoy_pct"))
        if f is not None:
            out[iso.upper()] = {"value": round(f, 1),
                                "source": "Eurostat food HICP (yoy %)",
                                "as_of": rec.get("month")}
    # 1b. WFP per-country (broad crisis-country coverage)
    for iso, rec in _read("wfp_country.json").items():
        if not isinstance(rec, dict):
            continue
        # v79 — refresh_wfp_country.py writes "food_inflation_pct"; this asked
        # for "food_inflation" and matched 0 of 172 rows (147 carry a value).
        v = (rec.get("food_inflation_pct") or rec.get("food_inflation")
             or rec.get("food_inflation_yoy") or rec.get("headline_food_inflation"))
        f = _plausible(v)
        if f is not None:
            out[iso.upper()] = {"value": round(f, 1),
                                "source": "WFP per-country food inflation",
                                "as_of": rec.get("month") or rec.get("as_of")}
    # 1a. World Bank RTFP (highest priority — market-observed, real per-country
    # as_of, weekly refresh). Written last so it wins.
    for iso, rec in _read("rtfp.json").items():
        if not isinstance(rec, dict):
            continue
        f = _plausible(rec.get("food_inflation_pct"))
        if f is not None:
            out[iso.upper()] = {
                "value": round(f, 1),
                "source": "World Bank Real-Time Food Prices (RTFP) via HDX",
                "as_of": rec.get("as_of"),
                "markets": rec.get("markets"),
                "confidence": rec.get("confidence"),
            }
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
        "c": "Legacy embedded structural vector from index.html. Expanded to the 9-component FDRS v2 schema during rebuild.",
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
