"""
UN Comtrade — bilateral trade in cereal staples (wheat, maize, rice, soy).

PUBLIC PREVIEW ENDPOINT (v20.8, May 2026) — no API key required.

Earlier versions used /data/v1/get/C/A/HS which requires a paid Comtrade subscription
(the "Free APIs" subscription doesn't actually grant access; both header- and
query-string auth modes returned HTTP 403). The free public preview endpoint
/public/v1/preview/C/A/HS works without authentication for read access. Verified
May 19 2026 against Egypt 2024 wheat (HS 1001) → 16 supplier rows returned.

Endpoint: https://comtradeapi.un.org/public/v1/preview/C/A/HS

Rate limits on the public endpoint appear less strict than the v1/get path, but
we still throttle conservatively (~1 req/sec).

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
  1511  Palm oil
  1701  Sugar (cane / beet, raw or refined)
  0901  Coffee
  1801  Cocoa beans (raw)
  3102  Nitrogenous fertilizers (covers urea)
  0201  Bovine meat, fresh/chilled
"""
import time
from collections import defaultdict

import requests
from _common import env, write_json, UA

URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

# M49 numeric → ISO3 — needed because the public preview endpoint returns
# partnerCode (numeric) but partnerISO is null. Built from the PRIORITY_IMPORTERS
# reverse plus the top global staple-trade partner countries we expect to see.
M49_TO_ISO3 = {
    4:"AFG",8:"ALB",12:"DZA",24:"AGO",32:"ARG",36:"AUS",40:"AUT",50:"BGD",
    51:"ARM",56:"BEL",68:"BOL",70:"BIH",72:"BWA",76:"BRA",84:"BLZ",90:"SLB",
    96:"BRN",100:"BGR",104:"MMR",108:"BDI",112:"BLR",116:"KHM",120:"CMR",124:"CAN",
    132:"CPV",140:"CAF",144:"LKA",148:"TCD",152:"CHL",156:"CHN",158:"TWN",170:"COL",
    178:"COG",180:"COD",188:"CRI",191:"HRV",192:"CUB",196:"CYP",203:"CZE",208:"DNK",
    214:"DOM",218:"ECU",222:"SLV",226:"GNQ",231:"ETH",232:"ERI",233:"EST",242:"FJI",
    246:"FIN",250:"FRA",262:"DJI",266:"GAB",268:"GEO",270:"GMB",276:"DEU",288:"GHA",
    300:"GRC",320:"GTM",324:"GIN",328:"GUY",332:"HTI",340:"HND",344:"HKG",348:"HUN",
    352:"ISL",356:"IND",360:"IDN",364:"IRN",368:"IRQ",372:"IRL",376:"ISR",380:"ITA",
    384:"CIV",388:"JAM",392:"JPN",398:"KAZ",400:"JOR",404:"KEN",408:"PRK",410:"KOR",
    414:"KWT",417:"KGZ",418:"LAO",422:"LBN",426:"LSO",428:"LVA",430:"LBR",434:"LBY",
    440:"LTU",442:"LUX",446:"MAC",450:"MDG",454:"MWI",458:"MYS",462:"MDV",466:"MLI",
    470:"MLT",478:"MRT",480:"MUS",484:"MEX",496:"MNG",498:"MDA",499:"MNE",504:"MAR",
    508:"MOZ",512:"OMN",516:"NAM",524:"NPL",528:"NLD",548:"VUT",554:"NZL",558:"NIC",
    562:"NER",566:"NGA",578:"NOR",586:"PAK",591:"PAN",598:"PNG",600:"PRY",604:"PER",
    608:"PHL",616:"POL",620:"PRT",624:"GNB",626:"TLS",634:"QAT",642:"ROU",643:"RUS",
    646:"RWA",682:"SAU",686:"SEN",688:"SRB",694:"SLE",702:"SGP",703:"SVK",704:"VNM",
    705:"SVN",706:"SOM",710:"ZAF",716:"ZWE",724:"ESP",728:"SSD",729:"SDN",732:"ESH",
    740:"SUR",748:"SWZ",752:"SWE",756:"CHE",760:"SYR",762:"TJK",764:"THA",768:"TGO",
    780:"TTO",784:"ARE",788:"TUN",792:"TUR",795:"TKM",800:"UGA",804:"UKR",818:"EGY",
    826:"GBR",834:"TZA",840:"USA",854:"BFA",858:"URY",860:"UZB",862:"VEN",882:"WSM",
    887:"YEM",894:"ZMB",
}
COMMODITIES = {
    "1001": "wheat",
    "1005": "maize",
    "1006": "rice",
    "1201": "soybeans",
    # v21 expansion (May 2026): six more commodities so the drilldown can
    # show observed bilateral trade for palm oil, sugar, coffee, cocoa, fertilizer
    # and beef. ~25 importers × 10 commodities = 250 calls/day, still under the
    # free-tier 500/day quota.
    "1511": "palm_oil",
    "1701": "sugar",
    "0901": "coffee",
    "1801": "cocoa",
    "3102": "fertilizer",
    "0201": "beef",
}

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
    # v20.8: public preview endpoint requires no auth. We still read COMTRADE_API_KEY
    # for backward compat — if you later upgrade to a paid subscription, the key can
    # be used to bump rate limits on the protected endpoint.
    key = env("COMTRADE_API_KEY", required=False)
    if key:
        print("  [info] COMTRADE_API_KEY present but public endpoint used (no auth needed)")

    out = defaultdict(lambda: defaultdict(lambda: {"total_kt": 0, "total_usd_m": 0, "by_supplier": defaultdict(lambda: {"kt": 0, "usd_m": 0})}))
    year = 2024  # most recent full year for free tier as of May 2026
    skipped = 0
    succeeded = 0

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})

    # v20.8: public preview endpoint, no auth needed.
    # Note: omit partnerCode parameter entirely — leaving it blank returns 0 rows on the
    # public endpoint; omitting it returns the full supplier breakdown.

    for reporter_code, importer_iso in PRIORITY_IMPORTERS.items():
        for cmd_code, cmd_name in COMMODITIES.items():
            # Throttle to stay polite to the public endpoint (~1 req/sec).
            time.sleep(THROTTLE_SECONDS)
            params = {
                "cmdCode": cmd_code,
                "flowCode": "M",
                "reporterCode": reporter_code,
                "period": year,
                "max": 500,
            }
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
                # Public preview endpoint returns partnerISO as null; only partnerCode
                # (numeric M49) is populated. Resolve to ISO3 via the reverse lookup.
                # netWgt is also null on the public endpoint — primaryValue (USD millions
                # already, despite the legacy /1e6 divisor below) is the only volumetric
                # signal. Treat primaryValue AS the USD-million value and skip the kt
                # conversion that the paid endpoint supported.
                p_code = row.get("partnerCode")
                if not p_code or p_code == 0:
                    continue
                sup = M49_TO_ISO3.get(int(p_code))
                if not sup:
                    continue
                usdm = row.get("primaryValue") or 0
                if usdm <= 0:
                    continue
                # Public endpoint already returns USD millions directly, NOT raw USD.
                # Verified May 19 2026: Egypt 2024 wheat from Turkey shows primaryValue=10.899
                # which matches Egypt-Turkey wheat trade of $10.9M (Comtrade data viewer).
                entry = out[importer_iso][cmd_name]
                entry["total_usd_m"] += usdm
                # netWgt is null on public preview — we cannot compute kt. Set to 0
                # so downstream code knows volumes aren't available; UI must label as
                # "obs · aggregate (USD only)" rather than implying kt accuracy.
                s = entry["by_supplier"][sup]
                s["usd_m"] += usdm

    print(f"  Fetched {succeeded} commodity-importer combos; skipped {skipped}")

    # v20.8: public preview endpoint does not return netWgt — share_pct is USD-based.
    # Top-5 suppliers per (importer, commodity), ranked by USD value.
    final = {}
    for imp, commodities in out.items():
        final[imp] = {}
        for cmd_name, e in commodities.items():
            total_usd = e["total_usd_m"]
            suppliers = [
                {"iso3": s, "usd_m": round(v["usd_m"], 2),
                 "share_pct": round(v["usd_m"] / total_usd * 100, 1) if total_usd else 0}
                for s, v in e["by_supplier"].items()
            ]
            suppliers.sort(key=lambda x: -x["usd_m"])
            final[imp][cmd_name] = {
                "total_kt": None,   # not available on public preview endpoint
                "total_usd_m": round(total_usd, 2),
                "top_suppliers": suppliers[:5],
                "value_basis": "USD millions (primaryValue from Comtrade public preview)",
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
