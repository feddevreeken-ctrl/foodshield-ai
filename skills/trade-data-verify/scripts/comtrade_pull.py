"""
UN Comtrade pull helper — for trade-data-verify skill.

Pulls bilateral import/export by HS commodity for a set of reporter countries,
to verify or refresh FoodShield trade figures against the bilateral gold standard.

Runs on the user's Mac (the sandbox has no internet). Uses only stdlib +
requests if available, falling back to urllib. Writes the project's standard
{_meta, data} envelope so output drops into data/.

QUOTA: the free public preview is heavily rate-limited (a handful of calls).
A free API key (https://comtradeplus.un.org) raises this. Set COMTRADE_KEY env
var to use it. Without a key, keep REPORTERS small.

VALUE BASIS: the public preview returns primaryValue in RAW USD (not millions),
and usually omits netWgt (tonnes null). Do not double-convert — FoodShield's
field is named *_usd_m but holds raw USD by convention.

USAGE:
    COMTRADE_KEY=xxxx python3 comtrade_pull.py            # default staples + priority importers
    python3 comtrade_pull.py --reporters EGY,NLD --hs 1001 --year 2024 --flow M
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    import requests
    _HAVE_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.parse
    _HAVE_REQUESTS = False

API = "https://comtradeapi.un.org/data/v1/get/C/A/HS"

# Staple HS4 codes (see references/hs_codes.md)
STAPLE_HS = {"wheat": "1001", "rice": "1006", "maize": "1005", "soybeans": "1201"}

# A small default reporter set — keep short for the free tier.
DEFAULT_REPORTERS = ["EGY", "NLD", "BGD", "NGA", "IDN", "JPN", "MEX", "TUR"]

# ISO3 -> UN M49 numeric reporter codes. Covers the major food importers AND the
# major staple exporters (so export-flow verification works too). Extend as needed;
# the full map lives in the project's refresh_comtrade.py.
ISO3_TO_M49 = {
    # Major importers
    "EGY": 818, "NLD": 528, "BGD": 50, "NGA": 566, "IDN": 360, "JPN": 392,
    "MEX": 484, "TUR": 792, "SAU": 682, "KOR": 410, "PHL": 608, "DZA": 12,
    "MAR": 504, "ZAF": 710, "IRN": 364, "IRQ": 368, "YEM": 887, "ETH": 231,
    "KEN": 404, "SDN": 729, "ARE": 784, "PAK": 586, "VEN": 862, "CUB": 192,
    # Major staple exporters (for export-flow verification)
    "USA": 842, "BRA": 76, "CHN": 156, "IND": 356, "RUS": 643, "UKR": 804,
    "ARG": 32, "AUS": 36, "CAN": 124, "FRA": 251, "THA": 764, "VNM": 704,
    "ROU": 642, "BGR": 100, "KAZ": 398, "MMR": 104, "PRY": 600, "URY": 858,
}


def _get(url, params, key):
    headers = {"User-Agent": "FoodShield-AI-verify/1"}
    if key:
        headers["Ocp-Apim-Subscription-Key"] = key
    if _HAVE_REQUESTS:
        r = requests.get(url, params=params, headers=headers, timeout=60)
        r.raise_for_status()
        return r.json()
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pull(reporters, hs_map, year, flow):
    key = os.environ.get("COMTRADE_KEY")
    out = {}
    for iso3 in reporters:
        m49 = ISO3_TO_M49.get(iso3)
        if not m49:
            print(f"[skip] no M49 code for {iso3} — add it to ISO3_TO_M49", file=sys.stderr)
            continue
        out.setdefault(iso3, {})
        for commodity, hs in hs_map.items():
            params = {
                "reporterCode": m49, "period": year, "cmdCode": hs,
                "flowCode": flow, "partnerCode": "", "partner2Code": 0,
                "customsCode": "C00", "motCode": 0,
            }
            try:
                data = _get(API, params, key)
            except Exception as e:
                print(f"[fail] {iso3} {commodity} {hs}: {e}", file=sys.stderr)
                continue
            rows = (data or {}).get("data") or []
            partners = []
            total = 0.0
            for row in rows:
                pv = row.get("primaryValue")
                if pv is None:
                    continue
                pcode = row.get("partnerCode")
                if pcode in (0, "0", None):  # 0 = World aggregate
                    total = float(pv)
                    continue
                partners.append({
                    "partner_m49": pcode,
                    "partner": row.get("partnerDesc"),
                    "usd": float(pv),
                })
            partners.sort(key=lambda x: -x["usd"])
            for p in partners[:5]:
                p["share_pct"] = round(p["usd"] / total * 100, 1) if total else None
            out[iso3][commodity] = {
                "total_usd": total or (sum(p["usd"] for p in partners) if partners else None),
                "top_suppliers": partners[:5],
                "hs_code": hs, "year": year, "flow": flow,
                "value_basis": "raw USD (Comtrade primaryValue)",
                "source": "UN Comtrade", "quality_flag": "sourced",
                "as_of": year,
            }
            time.sleep(1.0)  # be polite to the free tier
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reporters", default=",".join(DEFAULT_REPORTERS))
    ap.add_argument("--hs", default=None, help="single HS code; default = all staples")
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--flow", default="M", choices=["M", "X"], help="M=import X=export")
    ap.add_argument("--out", default="comtrade_verify.json")
    args = ap.parse_args()

    reporters = [r.strip().upper() for r in args.reporters.split(",") if r.strip()]
    hs_map = {"custom": args.hs} if args.hs else STAPLE_HS

    data = pull(reporters, hs_map, args.year, args.flow)
    envelope = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": f"UN Comtrade (HS, year {args.year}, flow {args.flow})",
            "notes": (f"Verification pull for {len(reporters)} reporters. "
                      f"primaryValue is RAW USD. Cross-check against FAOSTAT before "
                      f"replacing any FoodShield value."),
            "version": "verify-1",
        },
        "data": data,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(envelope, f, indent=2, ensure_ascii=False)
    n = sum(len(v) for v in data.values())
    print(f"[ok] wrote {args.out}: {len(data)} reporters, {n} commodity rows")


if __name__ == "__main__":
    main()
