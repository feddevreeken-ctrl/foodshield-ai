"""Historical replay smoke test for the wheat scenario engine.

Uses USDA PSD vintage market years instead of current data so the two replayed
events are scored from information that existed around the event window.
"""
import csv
import io
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import refresh_usda_psd as psd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "_backtest_cache"
CACHE_PATH = CACHE_DIR / "psd_grains_pulses.zip"
OUTPUT_PATH = DATA_DIR / "backtest_scenarios.json"
PRECEDENTS_PATH = DATA_DIR / "precedents.json"

PSD_URL = "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip"
PSD_HOST = "apps.fas.usda.gov"
PSD_RESOLVE_IP = "162.79.21.203"
TARGET_YEARS = {2009, 2021}
EVENTS = [
    {"id": "russia_ban_2010", "vintage_market_year": 2009},
    {"id": "black_sea_2022", "vintage_market_year": 2021},
]

# v2 — origin-weighted score variant. The v1 (dependence-only) result was
# diagnostic: 0/2 hits, EGY ranked 70/79 — because EGY and LBN were hit
# through WHO they bought from, not how much they import overall. Variant 2
# multiplies dependence by the share of the country's wheat imports sourced
# from the banned origins. ANACHRONISM, disclosed: the shares come from the
# 2026 cited flow atlas standing in for 2009/2021 (no vintage bilateral
# shares in the PSD file); Russia's export share was LOWER in 2009 than
# today, so the 2010 variant overstates origin exposure.
BANNED_ORIGINS = {
    "russia_ban_2010": {"RUS"},
    "black_sea_2022": {"RUS", "UKR"},
}

VARIANT2_NOTE = (
    "variant2 multiplies each country's vintage import dependence by the share of its wheat "
    "imports sourced from the banned origins per the CURRENT (2026) cited flow atlas — an "
    "anachronism standing in for unavailable 2009/2021 bilateral shares. Russia's world export "
    "share was lower in 2009 than today, so the 2010 variant overstates origin exposure."
)


def load_wheat_origin_shares(banned_isos):
    """Share of each importer's cited wheat imports coming from `banned_isos` (2026 atlas)."""
    flows_path = DATA_DIR / "commodity_flows.json"
    flows = json.loads(flows_path.read_text())["commodities"]["wheat"]["flows"]
    total = {}
    banned = {}
    for f in flows:
        to = f.get("to")
        val = f.get("value") or 0
        if not to or val <= 0:
            continue
        total[to] = total.get(to, 0.0) + val
        if f.get("from") in banned_isos:
            banned[to] = banned.get(to, 0.0) + val
    return {iso: banned.get(iso, 0.0) / tot for iso, tot in total.items() if tot > 0}


def build_variant2(event_id, ranked, documented_isos):
    shares = load_wheat_origin_shares(BANNED_ORIGINS[event_id])
    scored = []
    for row in ranked:
        share = shares.get(row["iso"])
        if share is None:
            continue          # no cited wheat flows for this importer — excluded, not zeroed
        scored.append({**row, "origin_share_pct": round(share * 100, 1),
                       "v2_score": row["_dependence"] * share})
    scored.sort(key=lambda r: (-r["v2_score"], r["_cover_months"]))
    rank_of = {r["iso"]: i + 1 for i, r in enumerate(scored)}
    top15 = [{"iso": r["iso"], "name": r["name"], "dependence_pct": r["dependence_pct"],
              "origin_share_pct": r["origin_share_pct"], "rank": i + 1}
             for i, r in enumerate(scored[:15])]
    documented = [{"iso": iso, "predicted_rank": rank_of.get(iso)} for iso in documented_isos]
    hits = sum(1 for d in documented if d["predicted_rank"] is not None and d["predicted_rank"] <= 15)
    return {
        "anachronistic_shares": True,
        "universe_size": len(scored),
        "top15": top15,
        "documented": documented,
        "hits": hits,
        "hit_rate": round(hits / len(documented_isos), 2) if documented_isos else None,
    }

MATERIAL_IMPORTS_FLOOR_KT = 100.0
MATERIAL_CONSUMPTION_FLOOR_KT = 0.0

# refresh_usda_psd.py keeps these headers local to main(), so they cannot be
# imported directly. Mirror the values here and reuse its http_get helper.
USDA_HEADERS = {
    "Accept": "application/zip,application/octet-stream,*/*",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://apps.fas.usda.gov/psdonline/app/index.html",
}

# Backtest-only country-name fallbacks following refresh_usda_psd.NAME_TO_ISO3
# conventions. These cover wheat importers that can clear the materiality floor
# in old PSD vintages but are absent from the current refresh mapping table.
BACKTEST_NAME_TO_ISO3 = {
    "Aruba": "ABW",
    "Bahamas, The": "BHS",
    "Barbados": "BRB",
    "Belize": "BLZ",
    "Croatia": "HRV",
    "Curacao": "CUW",
    "Cyprus": "CYP",
    "Guyana": "GUY",
    "Haiti": "HTI",
    "Kosovo": "XKX",
    "Luxembourg": "LUX",
    "Macau": "MAC",
    "Macedonia": "MKD",
    "Malta": "MLT",
    "Mauritius": "MUS",
    "Singapore": "SGP",
    "Suriname": "SUR",
    "Trinidad and Tobago": "TTO",
    "West Bank": "PSE",
    "West Bank and Gaza": "PSE",
    "West Bank/Gaza": "PSE",
}

METHOD_NOTE = (
    "Each event is reconstructed from USDA PSD wheat rows for its vintage market year, "
    "aggregating Imports, Domestic Consumption, and Ending Stocks by ISO3 using "
    "refresh_usda_psd country mappings rather than the rank-only scenario metrics mirror. "
    "Countries are ranked by import dependence, imports_kt divided by consumption_kt and "
    "bounded to 0-1, with lower cover_months, 12 times ending_stocks_kt divided by "
    "consumption_kt, used only as the tie-breaker to match the engine's physical-supply logic. "
    "No supplier-concentration term is included because the PSD bulk file has no bilateral "
    "trade shares for 2009 or 2021, and the materiality floor is imports_kt > 100 and "
    "consumption_kt > 0 so tiny importers cannot be meaningfully worst-hit."
)


def main():
    zip_bytes, source_note = load_psd_zip()
    parsed = parse_psd_zip(zip_bytes)

    ranked_by_year = {
        year: rank_countries(metrics)
        for year, metrics in parsed["metrics_by_year"].items()
    }
    check_unmapped_material(parsed["unmapped_by_year"], ranked_by_year)

    precedents = load_precedents()
    events_out = []
    for event in EVENTS:
        event_id = event["id"]
        year = event["vintage_market_year"]
        ranked = ranked_by_year.get(year, [])
        documented_isos = precedents[event_id]
        result = build_event_result(event_id, year, ranked, documented_isos)
        result["variant2"] = build_variant2(event_id, ranked, documented_isos)
        events_out.append(result)

    payload = {
        "_meta": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "method": METHOD_NOTE,
            "quality_flag": "reconstruction",
            "caveat": "n=2 events — a smoke test of the engine's dominant term, not a validation of the model",
            "variant2_note": VARIANT2_NOTE,
        },
        "events": events_out,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print_source_summary(source_note, parsed)
    print_summary(events_out)
    print(f"\n[OK] wrote {OUTPUT_PATH}")


def load_psd_zip():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists():
        return CACHE_PATH.read_bytes(), f"cache reused at {CACHE_PATH}"

    try:
        print(f"[INFO] downloading USDA PSD bulk ZIP: {PSD_URL}")
        zip_bytes, download_note = download_live_zip()
        if not zip_bytes or len(zip_bytes) < 1024:
            raise RuntimeError(f"download returned only {len(zip_bytes or b'')} bytes")
        validate_zip(zip_bytes)
        CACHE_PATH.write_bytes(zip_bytes)
        return zip_bytes, f"{download_note} and cached at {CACHE_PATH}"
    except Exception as exc:
        if CACHE_PATH.exists():
            return (
                CACHE_PATH.read_bytes(),
                f"cache fallback at {CACHE_PATH}; live download failed: {exc}",
            )
        raise RuntimeError(
            f"USDA PSD download failed and no cache exists at {CACHE_PATH}: {exc}"
        ) from exc


def download_live_zip():
    try:
        response = psd.http_get(PSD_URL, timeout=180, headers=USDA_HEADERS, retries=3)
        return response.content, "live download succeeded via refresh_usda_psd.http_get"
    except Exception as exc:
        # The Codex shell can have outbound routing while local DNS resolution is
        # disabled. Keep the imported helper as the primary path, then use curl's
        # --resolve to preserve the USDA hostname, TLS SNI, and Referer semantics.
        cmd = [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--max-time",
            "180",
            "--resolve",
            f"{PSD_HOST}:443:{PSD_RESOLVE_IP}",
            "-H",
            f"Accept: {USDA_HEADERS['Accept']}",
            "-H",
            f"User-Agent: {USDA_HEADERS['User-Agent']}",
            "-H",
            f"Accept-Language: {USDA_HEADERS['Accept-Language']}",
            "-H",
            f"Referer: {USDA_HEADERS['Referer']}",
            "--output",
            str(CACHE_PATH.with_suffix(".zip.download")),
            PSD_URL,
        ]
        tmp_path = CACHE_PATH.with_suffix(".zip.download")
        try:
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=190,
                text=True,
            )
        except Exception as curl_exc:
            stderr = getattr(curl_exc, "stderr", "") or ""
            raise RuntimeError(
                f"{exc}; curl --resolve fallback also failed: {curl_exc}; stderr={stderr.strip()}"
            ) from curl_exc
        zip_bytes = tmp_path.read_bytes()
        tmp_path.unlink(missing_ok=True)
        note = (
            "live download succeeded via curl --resolve after "
            f"refresh_usda_psd.http_get failed: {exc}"
        )
        if result.stderr:
            note += f"; curl stderr: {result.stderr.strip()}"
        return zip_bytes, note


def validate_zip(zip_bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        if not any(name.lower().endswith(".csv") for name in zf.namelist()):
            raise RuntimeError(f"PSD ZIP contains no CSV files: {zf.namelist()}")


def parse_psd_zip(zip_bytes):
    metrics_by_year = {year: {} for year in TARGET_YEARS}
    unmapped_by_year = {year: {} for year in TARGET_YEARS}
    row_counts_by_year = {year: 0 for year in TARGET_YEARS}
    wheat_rows_by_year = {year: 0 for year in TARGET_YEARS}
    kept_rows_by_year = {year: 0 for year in TARGET_YEARS}

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if not csv_name:
            raise RuntimeError("No CSV file found in PSD ZIP")

        with zf.open(csv_name, "r") as fh:
            text_stream = io.TextIOWrapper(
                fh, encoding="latin-1", errors="replace", newline=""
            )
            reader = csv.DictReader(text_stream)
            for row in reader:
                year = psd._int(row.get("Market_Year"))
                if year not in TARGET_YEARS:
                    continue
                row_counts_by_year[year] += 1

                commodity_code = (row.get("Commodity_Code") or "").strip()
                if psd.COMMODITY_TO_KEY.get(commodity_code) != "wheat":
                    continue
                wheat_rows_by_year[year] += 1

                attr_desc = (row.get("Attribute_Description") or "").strip()
                attr_info = psd.ATTR_KEYS.get(attr_desc)
                if not attr_info:
                    continue
                attr_key = attr_info[0]
                if attr_key not in {"imports_kt", "consumption_kt", "stocks_kt"}:
                    continue

                value = psd._num(row.get("Value"))
                if value is None:
                    continue

                country_name = (row.get("Country_Name") or "").strip()
                fas_code = (row.get("Country_Code") or "").strip()
                iso3 = resolve_iso3(fas_code, country_name)
                output_key = "ending_stocks_kt" if attr_key == "stocks_kt" else attr_key
                if not iso3:
                    slot = unmapped_by_year[year].setdefault(
                        country_name,
                        {"name": country_name, "country_code": fas_code},
                    )
                    slot[output_key] = round(value, 1)
                    continue

                slot = metrics_by_year[year].setdefault(iso3, {"iso": iso3, "name": country_name})
                if country_name and not slot.get("name"):
                    slot["name"] = country_name
                slot[output_key] = round(value, 1)
                kept_rows_by_year[year] += 1

    return {
        "metrics_by_year": metrics_by_year,
        "unmapped_by_year": unmapped_by_year,
        "row_counts_by_year": row_counts_by_year,
        "wheat_rows_by_year": wheat_rows_by_year,
        "kept_rows_by_year": kept_rows_by_year,
    }


def resolve_iso3(fas_code, country_name):
    iso3 = (
        psd.FAS_TO_ISO3.get(fas_code)
        or psd.NAME_TO_ISO3.get(country_name)
        or BACKTEST_NAME_TO_ISO3.get(country_name)
    )
    if not iso3 or len(iso3) != 3:
        return None
    return iso3


def rank_countries(metrics_by_iso):
    ranked = []
    for iso3, metrics in metrics_by_iso.items():
        imports_kt = float(metrics.get("imports_kt") or 0.0)
        consumption_kt = float(metrics.get("consumption_kt") or 0.0)
        ending_stocks_kt = float(metrics.get("ending_stocks_kt") or 0.0)
        if consumption_kt <= MATERIAL_CONSUMPTION_FLOOR_KT:
            continue
        if imports_kt <= MATERIAL_IMPORTS_FLOOR_KT:
            continue

        raw_dependence = imports_kt / consumption_kt
        # The live scenario engine's dominant term is bounded dependence. This
        # smoke test diverges from scripts/_scenario_metrics.py, which only
        # evaluates finished rankings and does not reconstruct these formulas.
        dependence = max(0.0, min(1.0, raw_dependence))
        cover_months = 12.0 * ending_stocks_kt / consumption_kt
        ranked.append(
            {
                "iso": iso3,
                "name": metrics.get("name") or iso3,
                "dependence_pct": round(dependence * 100.0, 1),
                "cover_months": round(cover_months, 2),
                "rank": None,
                "_dependence": dependence,
                "_cover_months": cover_months,
            }
        )

    ranked.sort(key=lambda item: (-item["_dependence"], item["_cover_months"], item["name"]))
    for idx, item in enumerate(ranked, start=1):
        item["rank"] = idx
    return ranked


def check_unmapped_material(unmapped_by_year, ranked_by_year):
    failures = []
    for year, country_metrics in unmapped_by_year.items():
        pseudo_entries = []
        for name, metrics in country_metrics.items():
            imports_kt = float(metrics.get("imports_kt") or 0.0)
            consumption_kt = float(metrics.get("consumption_kt") or 0.0)
            ending_stocks_kt = float(metrics.get("ending_stocks_kt") or 0.0)
            if consumption_kt <= MATERIAL_CONSUMPTION_FLOOR_KT:
                continue
            if imports_kt <= MATERIAL_IMPORTS_FLOOR_KT:
                continue
            dependence = max(0.0, min(1.0, imports_kt / consumption_kt))
            cover_months = 12.0 * ending_stocks_kt / consumption_kt
            pseudo_entries.append(
                {
                    "iso": None,
                    "name": name,
                    "country_code": metrics.get("country_code"),
                    "_dependence": dependence,
                    "_cover_months": cover_months,
                    "_unmapped": True,
                }
            )

        if not pseudo_entries:
            continue
        combined = list(ranked_by_year.get(year, [])) + pseudo_entries
        combined.sort(key=lambda item: (-item["_dependence"], item["_cover_months"], item["name"]))
        for idx, item in enumerate(combined[:15], start=1):
            if item.get("_unmapped"):
                failures.append(
                    f"MY{year} rank {idx}: {item['name']} "
                    f"(Country_Code={item.get('country_code')})"
                )

    if failures:
        joined = "\n  ".join(failures)
        raise RuntimeError(
            "Unmapped material wheat importers would enter the top 15. "
            "Add backtest-only country-name mappings before trusting output:\n  "
            + joined
        )


def load_precedents():
    obj = json.loads(PRECEDENTS_PATH.read_text())
    by_id = {}
    for event in obj.get("events", []):
        event_id = event.get("id")
        if event_id in {item["id"] for item in EVENTS}:
            by_id[event_id] = [row["iso"] for row in event.get("importers_hit", [])]

    missing = [item["id"] for item in EVENTS if item["id"] not in by_id]
    if missing:
        raise RuntimeError(f"Missing precedent event(s): {', '.join(missing)}")
    return by_id


def build_event_result(event_id, year, ranked, documented_isos):
    documented_set = set(documented_isos)
    top15 = [strip_private_fields(item) for item in ranked[:15]]
    rank_by_iso = {item["iso"]: item["rank"] for item in ranked}
    documented = [
        {"iso": iso3, "predicted_rank": rank_by_iso.get(iso3)}
        for iso3 in documented_isos
    ]
    hits = sum(1 for item in top15 if item["iso"] in documented_set)
    hit_rate = hits / len(documented_set) if documented_set else 0.0
    unverified_top = [
        {"iso": item["iso"], "name": item["name"], "rank": item["rank"]}
        for item in ranked
        if item["iso"] not in documented_set
    ][:5]

    return {
        "id": event_id,
        "vintage_market_year": year,
        "universe_size": len(ranked),
        "predicted_top15": top15,
        "documented": documented,
        "hits": hits,
        "hit_rate": round(hit_rate, 4),
        "unverified_top": unverified_top,
    }


def strip_private_fields(item):
    return {
        "iso": item["iso"],
        "name": item["name"],
        "dependence_pct": item["dependence_pct"],
        "cover_months": item["cover_months"],
        "rank": item["rank"],
    }


def print_source_summary(source_note, parsed):
    print("\nPSD source:")
    print(f"  {source_note}")
    for year in sorted(TARGET_YEARS):
        print(
            f"  MY{year}: rows={parsed['row_counts_by_year'][year]}, "
            f"wheat_rows={parsed['wheat_rows_by_year'][year]}, "
            f"kept_metric_rows={parsed['kept_rows_by_year'][year]}"
        )


def print_summary(events_out):
    print("\nScenario backtest smoke summary")
    for event in events_out:
        documented_count = len(event["documented"])
        print(
            f"\n{event['id']} (MY{event['vintage_market_year']}): "
            f"{event['hits']}/{documented_count} = {event['hit_rate']:.2f}"
        )
        print("  documented ranks:")
        for doc in event["documented"]:
            rank = doc["predicted_rank"] if doc["predicted_rank"] is not None else "below floor"
            print(f"    {doc['iso']}: {rank}")
        print("  unverified top:")
        for item in event["unverified_top"]:
            print(f"    #{item['rank']} {item['iso']} {item['name']} - unverified")


if __name__ == "__main__":
    main()
