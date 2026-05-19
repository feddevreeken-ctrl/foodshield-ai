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
from collections import defaultdict
from _common import env, http_get, write_json

URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
COMMODITIES = {"1001": "wheat", "1005": "maize", "1006": "rice", "1201": "soybeans"}

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

    for reporter_code, importer_iso in PRIORITY_IMPORTERS.items():
        for cmd_code, cmd_name in COMMODITIES.items():
            try:
                r = http_get(URL, params={
                    "subscription-key": key,
                    "cmdCode": cmd_code,
                    "flowCode": "M",
                    "reporterCode": reporter_code,
                    "partnerCode": "",   # all partners
                    "period": year,
                    "max": 500,
                }, timeout=45)
            except Exception as e:
                print(f"    {importer_iso}/{cmd_name}: skipped ({e})")
                skipped += 1
                continue
            payload = r.json()
            rows = payload.get("data", [])
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

    write_json(
        "comtrade_staples.json",
        final,
        source=f"UN Comtrade Plus (comtradeapi.un.org) — HS6, year {year}",
        notes=f"Top 5 suppliers per importer-commodity. ~25 priority importers (free-tier quota).",
    )


if __name__ == "__main__":
    main()
