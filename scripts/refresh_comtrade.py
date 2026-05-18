"""
UN Comtrade — bilateral trade in cereal staples (wheat, maize, rice, soy).

REQUIRES API KEY (free): register at https://comtradeplus.un.org
Set GitHub Actions secret: COMTRADE_API_KEY

Output: data/comtrade_staples.json
  {
    iso3_importer: {
      commodity_code: {
        "total_kt": <thousand tonnes imported, last year>,
        "total_usd_m": <USD millions>,
        "top_suppliers": [{"iso3": "RUS", "share_pct": 43.2, "kt": ...}, ...]
      }
    }
  }

Commodity HS codes used:
  1001  Wheat
  1005  Maize / corn
  1006  Rice
  1201  Soybeans
"""
from collections import defaultdict
from _common import env, http_get, write_json

URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
COMMODITIES = {"1001": "wheat", "1005": "maize", "1006": "rice", "1201": "soybeans"}


def main():
    key = env("COMTRADE_API_KEY", required=True)
    if not key:
        write_json("comtrade_staples.json", {}, source="Comtrade (not configured)", notes="Set COMTRADE_API_KEY secret to enable.")
        return

    out = defaultdict(lambda: defaultdict(lambda: {"total_kt": 0, "total_usd_m": 0, "by_supplier": defaultdict(lambda: {"kt": 0, "usd_m": 0})}))
    year = 2024  # most recent full year as of refresh

    for cmd_code, cmd_name in COMMODITIES.items():
        print(f"  fetching {cmd_name} (HS {cmd_code})…")
        try:
            r = http_get(URL, params={
                "subscription-key": key,
                "cmdCode": cmd_code,
                "flowCode": "M",        # Imports
                "reporterCode": "all",
                "partnerCode": "all",
                "period": year,
                "max": 100000,
            }, timeout=120)
        except Exception as e:
            print(f"    failed: {e}")
            continue
        rows = r.json().get("data", [])
        for row in rows:
            imp = (row.get("reporterISO") or "").upper()
            sup = (row.get("partnerISO") or "").upper()
            if not imp or not sup or sup == "WLD" or imp == "WLD":
                continue
            kt = (row.get("netWgt") or 0) / 1000.0
            usdm = (row.get("primaryValue") or 0) / 1_000_000.0
            entry = out[imp][cmd_name]
            entry["total_kt"] += kt
            entry["total_usd_m"] += usdm
            s = entry["by_supplier"][sup]
            s["kt"] += kt
            s["usd_m"] += usdm

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
        notes="Top 5 suppliers per importer-commodity by tonnage. M flow only.",
    )


if __name__ == "__main__":
    main()
