"""
UN Comtrade — bilateral trade in cereal staples (wheat, maize, rice, soy).

REQUIRES API KEY (free): register at https://comtradeplus.un.org
Set GitHub Actions secret: COMTRADE_API_KEY

Free tier constraints (discovered May 2026):
  - 500 calls/day
  - max ~500 records per call
  - reporterCode='all' is NOT allowed; must specify a single reporter per call
  - To stay under quota we only fetch a curated list of ~25 priority countries
    (the most exposed staple importers, where trade-arc accuracy matters most)

Output: data/comtrade_staples.json
  {
    iso3_importer: {
      commodity_name: {
        "total_kt": <thousand tonnes imported>,
        "total_usd_m": <USD millions>,
        "top_suppliers": [{"iso3", "share_pct", "kt"}, ...]
      }
    }
  }

Commodity HS codes:
  1001  Wheat
  1005  Maize / corn
  1006  Rice
  1201  Soybeans
"""
import time
from collections import defaultdict

import requests
from _common import env, write_json, UA

URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
COMMODITIES = {"1001": "wheat", "1005": "maize", "1006": "rice", "1201": "soybeans"}

# Free tier rate limit: appears to be ~1 request per second.
# Sleep generously between calls and back off hard on 429.
THROTTLE_SECONDS = 1.5

# Priority importers (M.49 codes), chosen for high import dependency or strategic
# relevance to FoodShield's nowcast layer. ~25 countries × 4 commodities = 100 calls/day,
# well under the 500/day quota.
PRIORITY_IMPORTERS = {
    818: "EGY",  # Egypt — largest wheat importer
    360: "IDN",  # Indonesia
    156: "CHN",  # China
    792: "TUR",  # Turkey
    50:  "BGD",  # Bangladesh
    231: "ETH",  # Ethiopia
    566: "NGA",  # Nigeria
    24:  "AGO",  # Angola
    646: "RWA",  # Rwanda
    729: "SDN",  # Sudan
    682: "SAU",  # Saudi Arabia
    784: "ARE",  # UAE
    400: "JOR",  # Jordan
    422: "LBN",  # Lebanon
    887: "YEM",  # Yemen
    332: "HTI",  # Haiti
    862: "VEN",  # Venezuela
    192: "CUB",  # Cuba
    192: "CUB",
    608: "PHL",  # Philippines
    458: "MYS",  # Malaysia
    704: "VNM",  # Vietnam
    410: "KOR",  # South Korea
    392: "JPN",  # Japan
    826: "GBR",
    276: "DEU",
    250: "FRA",
    380: "ITA",
    724: "ESP",
    528: "NLD",
}


def main():
    key = env("COMTRADE_API_KEY", required=True)
    if not key:
        write_json("comtrade_staples.json", {}, source="Comtrade (not configured)", notes="Set COMTRADE_API_KEY secret to enable.")
        return

    out = defaultdict(lambda: defaultdict(lambda: {"total_kt": 0, "total_usd_m": 0, "by_supplier": defaultdict(lambda: {"kt": 0, "usd_m": 0})}))
    year = 2024  # most recent full year for free tier as of May 2026
    skipped = 0
    succeeded = 0

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})

    for reporter_code, importer_iso in PRIORITY_IMPORTERS.items():
        for cmd_code, cmd_name in COMMODITIES.items():
            # Throttle to stay under free-tier rate limit (~1 req/sec).
            time.sleep(THROTTLE_SECONDS)
            params = {
                "subscription-key": key,
                "cmdCode": cmd_code,
                "flowCode": "M",
                "reporterCode": reporter_code,
                "partnerCode": "",
                "period": year,
                "max": 500,
            }
            # Single attempt; back off on 429 instead of retrying (retries waste calls)
            try:
                r = session.get(URL, params=params, timeout=45)
            except Exception as e:
                print(f"    {importer_iso}/{cmd_name}: skipped (network: {e})")
                skipped += 1
                continue
            if r.status_code == 429:
                # Too many requests — back off 30s and continue to next call.
                # Don't retry; we'll catch the rest tomorrow.
                print(f"    {importer_iso}/{cmd_name}: 429 rate-limited, backing off 30s")
                skipped += 1
                time.sleep(30)
                continue
            if r.status_code != 200:
                print(f"    {importer_iso}/{cmd_name}: HTTP {r.status_code}")
                skipped += 1
                continue
            payload = r.json()
            rows = payload.get("data", []) or []
            succeeded += 1
            for row in rows:
                sup = (row.get("partnerISO") or "").upper()
                if not sup or sup == "WLD" or sup == "W00":
                    continue
                kt = (row.get("netWgt") or 0) / 1000.0
                usdm = (row.get("primaryValue") or 0) / 1_000_000.0
                if kt <= 0:
                    continue
                entry = out[importer_iso][cmd_name]
                entry["total_kt"] += kt
                entry["total_usd_m"] += usdm
                s = entry["by_supplier"][sup]
                s["kt"] += kt
                s["usd_m"] += usdm

    print(f"  Fetched {succeeded} commodity-importer combos; skipped {skipped}")

    # Convert to top-5 suppliers per (importer, commodity)
    final = {}
    for imp, commodities in out.items():
        final[imp] = {}
        for cmd_name, e in commodities.items():
            total_kt = e["total_kt"]
            suppliers = [
                {"iso3": s, "kt": round(v["kt"], 2), "usd_m": round(v["usd_m"], 2),
                 "share_pct": round(v["kt"] / total_kt * 100, 1) if total_kt else 0}
                for s, v in e["by_supplier"].items()
            ]
            suppliers.sort(key=lambda x: -x["kt"])
            final[imp][cmd_name] = {
                "total_kt": round(total_kt, 2),
                "total_usd_m": round(e["total_usd_m"], 2),
                "top_suppliers": suppliers[:5],
            }

    # Safety: if we got very little data this run (e.g. heavy rate-limiting),
    # don't overwrite an existing file that has real data.
    if len(final) < 5:
        from pathlib import Path
        import json
        existing = Path(__file__).resolve().parent.parent / "data" / "comtrade_staples.json"
        if existing.exists():
            try:
                prev = json.loads(existing.read_text())
                if len(prev.get("data", {})) > len(final):
                    print(f"  Only got {len(final)} importers this run; existing file has {len(prev.get('data',{}))}. Keeping existing.")
                    return
            except Exception:
                pass

    write_json(
        "comtrade_staples.json",
        final,
        source=f"UN Comtrade Plus (comtradeapi.un.org) — HS6, year {year}",
        notes=(f"Top 5 suppliers per importer-commodity. ~25 priority importers (free-tier quota). "
               f"Succeeded: {succeeded}, skipped: {skipped}."),
    )


if __name__ == "__main__":
    main()
