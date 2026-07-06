#!/usr/bin/env python3
"""
honesty_remediation.py — v41 data-honesty remediation for countries.json trade rows.

Implements the 2026-07-01 verification-audit fixes at the data layer, as a
deterministic, idempotent, RERUNNABLE transform (no network):

  D1  Query-level source lineage.
      - un_comtrade_hs6_2024_{imports,exports} rows: replace the homepage-only
        source_url with the actual public-preview API query the verification run
        performed (comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode=…&
        period=…&flowCode=…&cmdCode=…) — parameters derived ONLY from the row's
        own metadata (_supplier_basis / _export_basis / "(HS xxxx)" in method /
        the tracked-staple set for whole-basket rows). Underivable → the row is
        DOWNGRADED to partial (contract: no query-level lineage ⇒ not sourced).
      - un_comtrade_hs6_2024_mirror rows: exporter-mirror query (reporter = the
        row's supplier ISOs, partner = the country, flowCode=X).
      - world_bank_wits_comtrade_interface rows: WITS country/year/flow URL.
      - FAOSTAT net rows + USDA PSD rows + grain_buffer: add a machine-readable
        source_query_spec (bulk/dataset URL + exact filter) — these datasets have
        no stable per-query web URL; the spec + refresh script is the
        reproducible extraction logic.
  D2  trade_scope completeness: derive single_commodity_route wherever a
      _supplier_basis/_export_basis is set and no scope exists (incl. PAK);
      propagate an existing scope across sibling fields of the same panel.
  D4  Bare-100% single-supplier rows: coverage=top_partner_only and downgrade
      supPct+suppliers to partial (a truncated single-partner extract must not
      render as total concentration).
  D7a net rows: populate basis/basis_unit/coverage (uniform FAOSTAT TCL method).
  D7b grain_buffer rows whose PSD input series' latest year < 2015: downgrade to
      partial with a stale_inputs note.

Usage:
  python3 scripts/trade_pipeline/honesty_remediation.py           # report only
  python3 scripts/trade_pipeline/honesty_remediation.py --apply   # write changes
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DATA = REPO / "data"
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "trade_pipeline"))

from config import M49_TO_ISO3                      # noqa: E402
from refresh_comtrade import COMMODITIES            # noqa: E402  (HS4 -> name)

ISO3_TO_M49 = {v: k for k, v in M49_TO_ISO3.items()}
# Small territories absent from the reporter-oriented config map but needed as
# mirror-query partners. Standard ISO 3166-1 numeric (= UN M49) codes.
ISO3_TO_M49.update({
    "ASM": 16, "BTN": 64, "COM": 174, "FSM": 583, "GUM": 316, "KIR": 296,
    "MNP": 580, "NIU": 570, "NRU": 520, "PLW": 585, "PYF": 258, "TON": 776,
    "TUV": 798,
})
NAME_TO_HS = {v: k for k, v in COMMODITIES.items()}          # 'wheat' -> '1001'
# row metadata writes basis names like 'palm_oil' / 'Palm oil' / 'Wheat'
def _basis_to_hs(basis):
    if not basis:
        return None
    key = str(basis).strip().lower().replace(" ", "_")
    return NAME_TO_HS.get(key)

ALL_TRACKED_HS = ",".join(sorted(COMMODITIES.keys()))
COMTRADE_API = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
HOMEPAGE_URLS = {"https://comtradeplus.un.org/", "https://comtradeplus.un.org",
                 "https://wits.worldbank.org/", "https://wits.worldbank.org"}

FLOW_BY_FIELD = {"imports": "M", "suppliers": "M", "supPct": "M",
                 "exports": "X", "exportDests": "X"}
PANELS = {"suppliers": ("suppliers", "supPct"),
          "supPct": ("suppliers", "supPct"),
          "imports": ("imports",),
          "exports": ("exports", "exportDests"),
          "exportDests": ("exports", "exportDests")}

BARE_100_ISOS = {"AND", "BRB", "BTN", "CYM", "DMA", "ERI", "FJI", "FRO", "GUM",
                 "LSO", "MNG", "NIU", "PRK", "PSE", "SDN", "SEN", "SLB", "SSD",
                 "SWE", "SWZ", "ZWE"}

FAOSTAT_BULK = ("https://bulks-faostat.fao.org/production/"
                "Trade_CropsLivestock_E_All_Data_(Normalized).zip")


def _hs_from_text(text):
    m = re.search(r"\(HS\s*(\d{4,6})\)", str(text or ""))
    return m.group(1) if m else None


def _cmd_codes_for_row(row):
    """Derive the Comtrade cmdCode(s) from the row's own metadata, else None.
    Mirror-verified rows carry their query description in `note` rather than
    `method`, so both are searched."""
    text = f"{row.get('method') or ''} {row.get('note') or ''}"
    hs = (_basis_to_hs(row.get("_supplier_basis"))
          or _basis_to_hs(row.get("_export_basis"))
          or _hs_from_text(text))
    if hs:
        return hs
    low = text.lower()
    # "Total 49 kt across Maize, Wheat." — explicit commodity list in the note
    m = re.search(r"across ([A-Za-z ,]+?)\.", text)
    if m:
        codes = sorted({_basis_to_hs(name) for name in m.group(1).split(",")
                        if _basis_to_hs(name)})
        if codes:
            return ",".join(codes)
    # whole-basket rows: "Food staples ranked by import/export value" over the
    # tracked staple set — the query is the full tracked commodity list.
    if ("food staples ranked" in low or "aggregated across staples" in low
            or "largest staple import" in low or "staple imports" in low
            or "staple exports" in low):
        return ALL_TRACKED_HS
    # free-text mirror notes name commodities inline ("maize 27.6 kt, wheat …")
    found = sorted({hs for name, hs in
                    (("wheat", "1001"), ("maize", "1005"), ("rice", "1006"),
                     ("soybean", "1201"), ("palm", "1511"), ("sugar", "1701"),
                     ("coffee", "0901"), ("cocoa", "1801"))
                    if re.search(r"\b" + name, low)})
    if found:
        return ",".join(found)
    return None


def comtrade_url(reporter_iso, flow, cmd_codes, period, partner_iso=None):
    rep = ISO3_TO_M49.get(reporter_iso)
    if rep is None or not cmd_codes or not period:
        return None
    url = (f"{COMTRADE_API}?reporterCode={rep}&period={period}"
           f"&flowCode={flow}&cmdCode={cmd_codes}")
    if partner_iso:
        p = ISO3_TO_M49.get(partner_iso)
        if p is None:
            return None
        url += f"&partnerCode={p}"
    return url


def main(apply=None):
    # apply=None → honour the CLI flag; run_all passes apply=True explicitly.
    if apply is None:
        apply = "--apply" in sys.argv
    path = DATA / "countries.json"
    doc = json.loads(path.read_text())
    countries = doc["data"]["countries"]

    stats = {"url_upgraded": 0, "url_downgraded_partial": 0, "url_untouched": 0,
             "query_spec_added": 0, "scope_derived": 0, "scope_propagated": 0,
             "bare100_downgraded": 0, "net_basis_added": 0,
             "buffer_stale_downgraded": 0}
    downgraded_rows, scope_missing_after = [], []

    # ── D7b prep: stale PSD input series per country ─────────────────────────
    # usda_psd.json shape: iso -> {commodity: {year, stocks_kt, consumption_kt, …}}.
    # A stocks-to-use buffer "across staples" blends every series, so any input
    # series frozen before 2015 pollutes a row stamped sourced/2024.
    psd_stale = {}
    psd_path = DATA / "usda_psd.json"
    if psd_path.exists():
        psd = json.loads(psd_path.read_text())
        psd = psd.get("data", psd)
        for iso, cmds in psd.items():
            if not isinstance(cmds, dict):
                continue
            stale = [(cmd, r.get("year")) for cmd, r in cmds.items()
                     if isinstance(r, dict) and isinstance(r.get("year"), int)
                     and r["year"] < 2015]
            if stale:
                psd_stale[iso] = sorted(stale, key=lambda x: x[1])

    for iso, c in countries.items():
        if not isinstance(c, dict):
            continue

        # ── D2: propagate an existing trade_scope across panel siblings ──────
        for f, sibs in PANELS.items():
            row = c.get(f)
            if not isinstance(row, dict) or "trade_scope" in row:
                continue
            for s in sibs:
                other = c.get(s)
                if isinstance(other, dict) and other.get("trade_scope"):
                    row["trade_scope"] = other["trade_scope"]
                    stats["scope_propagated"] += 1
                    break

        for f in ("imports", "exports", "suppliers", "supPct", "exportDests"):
            row = c.get(f)
            if not isinstance(row, dict):
                continue
            ds = row.get("source_dataset") or ""
            url = row.get("source_url")

            # ── D2: derive scope from a single-commodity basis ───────────────
            if "trade_scope" not in row and (row.get("_supplier_basis") or row.get("_export_basis")):
                row["trade_scope"] = "single_commodity_route"
                stats["scope_derived"] += 1
            # ── D2: derive scope from the row's own query description ────────
            # (WITS rows: "top import partners for wheat (HS 1001)" = one HS
            # code → single-commodity; staple aggregates → staple basket.)
            if "trade_scope" not in row and row.get("quality_flag") in ("sourced", "partial") \
                    and row.get("value"):
                cmd = _cmd_codes_for_row(row)
                if cmd == ALL_TRACKED_HS or (cmd and "," in cmd):
                    row["trade_scope"] = "staple_basket"
                    stats["scope_derived"] += 1
                elif cmd:
                    row["trade_scope"] = "single_commodity_route"
                    stats["scope_derived"] += 1
                elif f in ("suppliers", "supPct"):
                    # commodity undocumented in the row's own metadata (tiny-state
                    # mirror extracts): inherit the country's imports scope, else
                    # label conservatively as a narrow single-route extract — the
                    # failure mode to prevent is a narrow sketch reading as a
                    # national surface, so err toward the narrow label.
                    imp_sc = (c.get("imports") or {}).get("trade_scope") \
                        if isinstance(c.get("imports"), dict) else None
                    row["trade_scope"] = imp_sc or "single_commodity_route"
                    stats["scope_derived"] += 1

            # ── D1: query-level URLs for Comtrade/WITS families ─────────────
            if row.get("quality_flag") == "sourced" and url in HOMEPAGE_URLS:
                period = str(row.get("as_of") or "").strip()[:4]
                new = None
                if ds in ("un_comtrade_hs6_2024_imports", "un_comtrade_hs6_2024_exports"):
                    cmd = _cmd_codes_for_row(row)
                    new = comtrade_url(iso, FLOW_BY_FIELD.get(f, "M"), cmd, period)
                elif ds == "un_comtrade_hs6_2024_mirror":
                    # exporter-mirror: reporters are the suppliers, partner is us
                    sup_row = c.get("suppliers") or {}
                    sups = sup_row.get("value") if isinstance(sup_row, dict) else None
                    sups = [s for s in (sups or []) if isinstance(s, str) and s in ISO3_TO_M49]
                    cmd = _cmd_codes_for_row(row)
                    if sups and cmd and period:
                        reps = ",".join(str(ISO3_TO_M49[s]) for s in sups)
                        p = ISO3_TO_M49.get(iso)
                        if p is not None:
                            new = (f"{COMTRADE_API}?reporterCode={reps}&period={period}"
                                   f"&flowCode=X&cmdCode={cmd}&partnerCode={p}")
                elif ds == "world_bank_wits_comtrade_interface":
                    flow = "Import" if FLOW_BY_FIELD.get(f, "M") == "M" else "Export"
                    if period:
                        new = (f"https://wits.worldbank.org/CountryProfile/en/Country/"
                               f"{iso}/Year/{period}/TradeFlow/{flow}/Partner/all/Product/all")
                if new:
                    row["source_url"] = new
                    row["source_urls"] = [new]
                    stats["url_upgraded"] += 1
                else:
                    # contract: no derivable query-level lineage ⇒ not `sourced`
                    row["quality_flag"] = "partial"
                    row.setdefault("note", "")
                    row["note"] = (row["note"] + " | " if row["note"] else "") + (
                        "v41 honesty pass: downgraded sourced→partial — query-level "
                        "source URL could not be derived from row metadata.")
                    stats["url_downgraded_partial"] += 1
                    downgraded_rows.append((iso, f, ds))
            elif row.get("quality_flag") == "sourced" and ds == "usda_psd_supply_balance":
                # Tier 2: PSD has no stable per-query URL — attach the exact spec.
                if "source_query_spec" not in row:
                    row["source_query_spec"] = {
                        "dataset": "USDA PSD Online bulk supply/demand balance",
                        "dataset_url": "https://apps.fas.usda.gov/psdonline/app/index.html#/app/downloads",
                        "filter": {"country_iso3": iso,
                                   "commodities": "tracked staple set (see scripts/refresh_usda_psd.py)",
                                   "market_year": row.get("as_of")},
                        "extraction": "scripts/refresh_usda_psd.py",
                    }
                    stats["query_spec_added"] += 1

        # ── D4: bare-100% single-supplier rows ───────────────────────────────
        sp = c.get("supPct")
        if iso in BARE_100_ISOS and isinstance(sp, dict) and sp.get("value") == [100]:
            for f in ("supPct", "suppliers"):
                row = c.get(f)
                if not isinstance(row, dict):
                    continue
                changed = False
                if row.get("coverage") != "top_partner_only":
                    row["coverage"] = "top_partner_only"
                    changed = True
                if row.get("quality_flag") == "sourced":
                    row["quality_flag"] = "partial"
                    changed = True
                if changed:
                    row.setdefault("note", "")
                    if "v41 honesty pass" not in row["note"]:
                        row["note"] = (row["note"] + " | " if row["note"] else "") + (
                            "v41 honesty pass: single-partner extract — a bare 100% share "
                            "reflects list truncation, not verified total concentration; "
                            "coverage=top_partner_only, downgraded to partial.")
                    stats["bare100_downgraded"] += 1

        # ── D7a: net rows lineage ────────────────────────────────────────────
        net = c.get("net")
        if isinstance(net, dict) and net.get("quality_flag") == "sourced":
            if not net.get("basis"):
                net["basis"] = "net_trade_value"
                net["basis_unit"] = "USD_millions"
                net["coverage"] = "faostat_tcl_agrifood_aggregate"
                stats["net_basis_added"] += 1
            if "source_query_spec" not in net:
                net["source_query_spec"] = {
                    "dataset": "FAOSTAT Trade Crops & Livestock (TCL) bulk normalized",
                    "dataset_url": FAOSTAT_BULK,
                    "filter": {"country_iso3": iso,
                               "item": "agri-food aggregate (see method)",
                               "elements": ["Export Value", "Import Value"],
                               "year": net.get("as_of")},
                    "extraction": "scripts/refresh_net_food_trade.py",
                }
                stats["query_spec_added"] += 1

        # ── D7c: econ_access lineage (WDI indicator API queries are real
        #        query-level URLs; HDI/FX are bulk → named in the spec) ────────
        ea = c.get("econ_access")
        if isinstance(ea, dict) and ea.get("quality_flag") in ("sourced", "partial") \
                and "source_query_spec" not in ea:
            sub = ea.get("sub_provenance") or {}
            wb_urls = [
                f"https://api.worldbank.org/v2/country/{iso}/indicator/FI.RES.TOTL.MO?format=json&mrnev=1",
                f"https://api.worldbank.org/v2/country/{iso}/indicator/DT.TDS.DECT.EX.ZS?format=json&mrnev=1",
            ]
            ea["source_query_spec"] = {
                "dataset": "WDI (reserves FI.RES.TOTL.MO, debt-service DT.TDS.DECT.EX.ZS) "
                           "+ UNDP HDI (gnipc) + FX pipeline",
                "query_urls_wdi": wb_urls,
                "filter": {"country_iso3": iso,
                           "sub_inputs": sub or "see builder",
                           "as_of": ea.get("as_of")},
                "extraction": "scripts/build_countries_dataset.py (compute_economic_access)",
            }
            if not ea.get("source_urls"):
                ea["source_urls"] = wb_urls
            stats["query_spec_added"] += 1

        # ── D7b: stale PSD inputs feeding grain_buffer ───────────────────────
        gb = c.get("grain_buffer")
        if isinstance(gb, dict) and gb.get("quality_flag") == "sourced":
            stale = psd_stale.get(iso)
            if stale:
                gb["quality_flag"] = "partial"
                gb["stale_inputs"] = [f"{cmd}: {yr}" for cmd, yr in stale]
                gb.setdefault("note", "")
                if "v41 honesty pass" not in gb["note"]:
                    gb["note"] = (gb["note"] + " | " if gb["note"] else "") + (
                        "v41 honesty pass: stocks-to-use blends PSD series frozen "
                        "before 2015 (" + ", ".join(f"{c2} {y}" for c2, y in stale)
                        + ") — downgraded to partial (stale inputs).")
                stats["buffer_stale_downgraded"] += 1
            if "source_query_spec" not in gb:
                gb["source_query_spec"] = {
                    "dataset": "USDA PSD Online — ending stocks / consumption across staples",
                    "dataset_url": "https://apps.fas.usda.gov/psdonline/app/index.html#/app/downloads",
                    "filter": {"country_iso3": iso, "attributes":
                               ["Ending Stocks", "Domestic Consumption"],
                               "market_year": gb.get("as_of")},
                    "extraction": "scripts/build_countries_dataset.py (grain_buffer)",
                }
                stats["query_spec_added"] += 1

    # ── post-transform assertions ────────────────────────────────────────────
    homepage_sourced = scope_missing = 0
    for iso, c in countries.items():
        if not isinstance(c, dict):
            continue
        for f in ("imports", "exports", "suppliers", "supPct", "exportDests"):
            row = c.get(f)
            if not isinstance(row, dict):
                continue
            if row.get("quality_flag") == "sourced" and row.get("source_url") in HOMEPAGE_URLS:
                homepage_sourced += 1
            if f in ("suppliers", "supPct") and row.get("quality_flag") in ("sourced", "partial") \
                    and row.get("value") and "trade_scope" not in row \
                    and (row.get("_supplier_basis") or row.get("_export_basis")):
                scope_missing += 1
                scope_missing_after.append((iso, f))

    print("=== honesty_remediation report ===")
    for k, v in stats.items():
        print(f"  {k:>26}: {v}")
    print(f"  ASSERT sourced rows with homepage-only URL after pass: {homepage_sourced} (must be 0)")
    print(f"  ASSERT basis-tagged supplier panels missing trade_scope: {scope_missing} (must be 0)")
    if downgraded_rows:
        print(f"  downgraded rows ({len(downgraded_rows)}): {downgraded_rows[:12]}")
    if not apply:
        print("\nDRY RUN — no file written. Re-run with --apply to write.")
        return 0
    if homepage_sourced or scope_missing:
        print("ASSERTION FAILED — refusing to write.")
        return 1
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print(f"\nWROTE {path}")
    return 0


def build():
    """run_all.py entrypoint — re-derive query-level trade lineage after the
    Trade re-verify step clobbers it back to homepage URLs. Returns 0 on success
    (or dry-run), 1 if the honesty assertions fail (then it refuses to write,
    and the downstream validate_data gate catches the missing trade_scope)."""
    return main(apply=True)


if __name__ == "__main__":
    sys.exit(main())
