"""
trade_pipeline/pull.py — JOB: fetch supplier data from UN Comtrade. Nothing else.

Pulls top-5 suppliers per (importer, commodity) for the countries in config.py,
parses the response, and writes the result to data/comtrade_staples.json (merging
with whatever is already there — see merge.py for the merge rule).

It does NOT touch countries.json or compute any FoodShield field. Pull → save.
That separation is deliberate: a bad pull can never corrupt the country dataset.

QUOTA / BATCHING (the public endpoint is ~500 calls/day; 10 commodities/country):
  Run the whole universe in slices so you never lose progress to a rate-limit:
    python3 pull.py --batch 40            # first 40 importers (priority order)
    python3 pull.py --batch 40 --start 40 # next 40, the following day
    python3 pull.py                       # everything (only with a keyed/premium quota)
  Each run MERGES into comtrade_staples.json, so batches accumulate.

  python3 pull.py --only EGY,NLD,VNM      # pull a specific set (re-verify a few)
  python3 pull.py --dry-run               # print the plan + call count, fetch nothing

If you have a keyed subscription, set COMTRADE_KEY in the environment; pull.py
sends it as the Ocp-Apim-Subscription-Key header. Without it, the public preview
endpoint is used (no auth, lower quota).
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

DATA = Path(__file__).resolve().parents[2] / "data"
OUT_FILE = DATA / "comtrade_staples.json"
UA = "FoodShield-AI/23 (+https://foodshield-ai-fv.vercel.app)"


def fetch_one(session, key, reporter_m49, cmd_code, flow="M"):
    """Return partner rows for one reporter×commodity×flow, or [] on failure.
    flow='M' (imports → suppliers) or 'X' (exports → destinations)."""
    params = {"cmdCode": cmd_code, "flowCode": flow,
              "reporterCode": reporter_m49, "period": C.YEAR, "max": 500}
    headers = {}
    if key:
        headers["Ocp-Apim-Subscription-Key"] = key
    try:
        r = session.get(C.ENDPOINT, params=params, headers=headers, timeout=45)
    except Exception as e:
        return None, f"network: {e}"
    if r.status_code == 429:
        return None, "429 rate-limited"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    return (r.json() or {}).get("data", []) or [], None


def pull(importers, dry_run=False, flow="M"):
    key = os.environ.get("COMTRADE_KEY")
    side = "imports/suppliers" if flow == "M" else "exports/destinations"
    print(f"[pull] flow={flow} ({side}) · endpoint={'KEYED' if key else 'public preview'} · "
          f"year={C.YEAR} · {len(importers)} reporters × {len(C.COMMODITIES)} commodities "
          f"= {len(importers)*len(C.COMMODITIES)} calls")
    if dry_run:
        print(f"[pull] DRY RUN — importers: {importers}")
        return {}

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})
    out = defaultdict(lambda: defaultdict(lambda: {"total_usd_m": 0.0,
                       "by_supplier": defaultdict(float)}))
    ok = skip = 0

    unresolved_codes = defaultdict(int)   # M49 codes we couldn't map → count
    for iso in importers:
        m49 = C.ISO3_TO_M49.get(iso)
        if not m49:
            print(f"  [skip] {iso}: no M49 code in config"); continue
        kept = dropped_partner = dropped_zero = raw_rows = 0
        for cmd_code, cmd_name in C.COMMODITIES.items():
            time.sleep(C.THROTTLE_SECONDS)
            rows, err = fetch_one(session, key, m49, cmd_code, flow=flow)
            if err:
                print(f"  [skip] {iso}/{cmd_name}: {err}")
                skip += 1
                if "429" in err:
                    time.sleep(30)
                continue
            ok += 1
            raw_rows += len(rows)
            for row in rows:
                p = row.get("partnerCode")
                if not p or int(p) == 0:
                    continue
                sup = C.M49_TO_ISO3.get(int(p))
                if not sup:
                    unresolved_codes[int(p)] += 1
                    dropped_partner += 1
                    continue
                usdm = row.get("primaryValue") or 0
                if usdm <= 0:
                    dropped_zero += 1
                    continue
                kept += 1
                e = out[iso][cmd_name]
                e["total_usd_m"] += usdm
                e["by_supplier"][sup] += usdm
        # per-country visibility — so a country that produces no entry is never silent
        produced = iso in out and any(out[iso].values())
        flag = "" if produced else "  ← NO ENTRY (all rows dropped)"
        print(f"  [{iso}] raw_rows={raw_rows} kept={kept} "
              f"dropped(unmapped_partner)={dropped_partner} dropped(zero)={dropped_zero}{flag}")

    print(f"[pull] fetched {ok} combos, skipped {skip}")
    if unresolved_codes:
        top = sorted(unresolved_codes.items(), key=lambda kv: -kv[1])[:12]
        print(f"[pull] unmapped partner M49 codes (add to config.M49_TO_ISO3 if big): {top}")
    # shape into the staples/exports schema. For imports the partner list is
    # "top_suppliers"; for exports it's "top_destinations" — same structure.
    partner_key = "top_suppliers" if flow == "M" else "top_destinations"
    shaped = {}
    for iso, commodities in out.items():
        shaped[iso] = {}
        for cmd_name, e in commodities.items():
            total = e["total_usd_m"]
            # v23 — keep ALL partners (not just top-5) so the frontend can show every
            # country this importer/exporter trades the commodity with. Capped at 40
            # to bound file size — that covers essentially all non-trivial partners
            # (a long tail of <0.1% partners adds noise, not signal). Sorted by value.
            partners = sorted(({"iso3": s, "usd_m": round(v, 2),
                            "share_pct": round(v/total*100, 1) if total else 0}
                           for s, v in e["by_supplier"].items()),
                          key=lambda x: -x["usd_m"])[:40]
            shaped[iso][cmd_name] = {"total_kt": None, "total_usd_m": round(total, 2),
                "n_partners": len(partners),
                partner_key: partners,
                "value_basis": "USD millions (primaryValue from Comtrade public preview)"}
    return shaped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, help="number of importers to pull this run")
    ap.add_argument("--start", type=int, default=0, help="offset into the priority-ordered list")
    ap.add_argument("--only", help="comma ISO3 list to pull exactly these")
    ap.add_argument("--flow", default="M", choices=["M", "X"],
                    help="M=imports→suppliers (default), X=exports→destinations")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.only:
        importers = [x.strip().upper() for x in args.only.split(",") if x.strip()]
    else:
        full = C.ordered_importers()
        importers = full[args.start: args.start + args.batch] if args.batch else full

    shaped = pull(importers, dry_run=args.dry_run, flow=args.flow)
    if args.dry_run or not shaped:
        return
    # merge.py owns the merge rule; imports → comtrade_staples.json,
    # exports → comtrade_exports.json. Batches accumulate safely either way.
    from merge import merge_into_staples, merge_into_exports
    if args.flow == "M":
        merge_into_staples(shaped)
    else:
        merge_into_exports(shaped)


if __name__ == "__main__":
    main()
