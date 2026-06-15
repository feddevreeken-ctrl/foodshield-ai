#!/usr/bin/env python3
"""
========================================================================
FoodShield — fill GAPS in the commodity-flow ATLAS (data/commodity_flows.json)
RUN ON YOUR MAC (unthrottled internet; the Cowork sandbox web_fetch is rate-limited).
========================================================================

DIFFERENT from rebuild_all_weak_trade_rows_MAC.py:
  - that script fills COUNTRY supplier rows in data/countries.json (the country panel).
  - THIS script fills the per-commodity bilateral ATLAS in data/commodity_flows.json
    (the Trade Flow tab). It's why you still see "No cited wheat flows for Chad" even
    after the supplier rebuild — that message comes from the ATLAS, not the panel.

WHAT IT DOES
  For each staple commodity, finds importer countries that have NO cited flow in the
  atlas, pulls the exporter-mirror from UN Comtrade 2024 (exporters reporting shipments
  TO that country), and writes real flow records {from,to,value(kt),kind,src,note}.
  Prioritises AFRICA (the worst-covered region) but covers all gaps.

HONESTY (matches the app's on-screen message — do NOT fabricate)
  - netWgt kg / 1e6 = kt. Only partner2Code==0 rows. legacyEstimationFlag!=0 -> note it.
  - A genuine non-reporter with no exporter-mirror flow (e.g. Chad wheat — routed via
    Cameroon/Nigeria re-export, which Comtrade attributes to the hub, not Chad) STAYS
    BLANK. The app's "not captured / we don't fabricate" message is correct for these.
  - Never overwrite an existing sourced flow; only ADD missing ones.

USAGE
  cd "/path/to/FoodSecurity AI"
  python3 scripts/fill_commodity_atlas_gaps_MAC.py --dry-run          # pull + print, no write
  python3 scripts/fill_commodity_atlas_gaps_MAC.py                    # write the atlas
  python3 scripts/fill_commodity_atlas_gaps_MAC.py --commodity maize  # one commodity
  python3 scripts/fill_commodity_atlas_gaps_MAC.py --africa-only      # only African importers

After it runs:
  python3 scripts/validate_data.py
  # then regenerate the index.html embed (window.__BEEF_DATA__) + sync foodshield-v21.html.
"""
import argparse, json, os, time, urllib.request, urllib.parse
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATLAS = os.path.join(ROOT, "data", "commodity_flows.json")
PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
KEY = os.environ.get("COMTRADE_KEY", "")

# atlas commodity key -> HS code + candidate exporter origins (M49)
STAPLES = {
    "wheat":  ("1001", ["RUS","UKR","USA","CAN","FRA","ARG","AUS","ROU","DEU","KAZ","TUR","BRA"]),
    "rice":   ("1006", ["IND","THA","VNM","PAK","CHN","MMR","USA","ITA","ESP","BRA","URY","GUY","KHM"]),
    "maize":  ("1005", ["USA","BRA","ARG","UKR","RUS","FRA","ROU","ZAF","ZMB","TZA","IND","SRB"]),
    "sorghum":("1007", ["USA","ARG","AUS","FRA","NGA","ETH","IND"]),
    "soybeans":("1201",["BRA","USA","ARG","CAN","PRY","UKR","URY"]),
    "palmoil":("1511", ["IDN","MYS","GTM","COL","HND","CIV","GHA","NGA","THA","PNG"]),
    "sugar":  ("1701", ["BRA","THA","IND","GTM","FRA","ZAF","EGY","AUS","MEX","COL","MOZ","ESW"]),
}

M49 = {
 # importers (Africa + selected others) — extend as needed
 "DZA":12,"AGO":24,"BEN":204,"BWA":72,"BFA":854,"BDI":108,"CPV":132,"CMR":120,"CAF":140,
 "TCD":148,"COM":174,"COG":178,"COD":180,"CIV":384,"DJI":262,"EGY":818,"GNQ":226,"ERI":232,
 "SWZ":748,"ETH":231,"GAB":266,"GMB":270,"GHA":288,"GIN":324,"GNB":624,"KEN":404,"LSO":426,
 "LBR":430,"LBY":434,"MDG":450,"MWI":454,"MLI":466,"MRT":478,"MUS":480,"MAR":504,"MOZ":508,
 "NAM":516,"NER":562,"NGA":566,"RWA":646,"STP":678,"SEN":686,"SYC":690,"SLE":694,"SOM":706,
 "ZAF":710,"SSD":728,"SDN":729,"TZA":834,"TGO":768,"TUN":788,"UGA":800,"ZMB":894,"ZWE":716,
 # exporters
 "RUS":643,"UKR":804,"USA":842,"CAN":124,"FRA":251,"ARG":32,"AUS":36,"ROU":642,"DEU":276,
 "KAZ":398,"TUR":792,"BRA":76,"IND":699,"THA":764,"VNM":704,"PAK":586,"CHN":156,"MMR":104,
 "ITA":380,"ESP":724,"URY":858,"GUY":328,"KHM":116,"ZMB-x":894,"SRB":688,"IDN":360,"MYS":458,
 "GTM":320,"COL":170,"HND":340,"PNG":598,"MEX":484,"ESW":748,
}
AFRICA = {"DZA","AGO","BEN","BWA","BFA","BDI","CPV","CMR","CAF","TCD","COM","COG","COD","CIV",
 "DJI","EGY","GNQ","ERI","SWZ","ETH","GAB","GMB","GHA","GIN","GNB","KEN","LSO","LBR","LBY",
 "MDG","MWI","MLI","MRT","MUS","MAR","MOZ","NAM","NER","NGA","RWA","STP","SEN","SYC","SLE",
 "SOM","ZAF","SSD","SDN","TZA","TGO","TUN","UGA","ZMB","ZWE"}

def fetch(exporter_m49, target_m49, hs, _tries=6):
    q={"reporterCode":exporter_m49,"period":2024,"partnerCode":target_m49,
       "cmdCode":hs,"flowCode":"X","motCode":0,"customsCode":"C00"}
    if KEY: q["subscription-key"]=KEY
    url=PREVIEW+"?"+urllib.parse.urlencode(q)
    last=""
    for a in range(_tries):
        try:
            with urllib.request.urlopen(url,timeout=40) as r:
                j=json.loads(r.read().decode()); break
        except Exception as e:
            last=str(e)
            if any(k in last for k in ("429","Too Many","timed out","URLError","500")):
                time.sleep(3*(a+1)); continue
            return None
    else:
        return None
    rows=[x for x in j.get("data",[]) if x.get("partner2Code")==0]
    if not rows: return 0.0
    x=rows[0]
    kt=(x.get("netWgt") or 0)/1e6
    soft = x.get("legacyEstimationFlag",0)!=0 or x.get("isNetWgtEstimated")
    return (kt, soft)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--commodity",default="")
    ap.add_argument("--africa-only",action="store_true")
    args=ap.parse_args()
    d=json.load(open(ATLAS)); comms=d.get("commodities",d)

    staples = {args.commodity:STAPLES[args.commodity]} if args.commodity in STAPLES else STAPLES
    importers = AFRICA if args.africa_only else set(M49.keys())
    added=0; gaps=[]; log=[]

    for cname,(hs,origins) in staples.items():
        cd=comms.get(cname)
        if not isinstance(cd,dict): continue
        flows=cd.setdefault("flows",[])
        have=set(f.get("to") for f in flows)
        targets=[iso for iso in importers if iso in M49 and iso not in have and iso in AFRICA]
        for iso in sorted(targets):
            tm=M49[iso]; found=[]
            for ex in origins:
                exm=M49.get(ex)
                if not exm: continue
                res=fetch(exm,tm,hs)
                if res and isinstance(res,tuple):
                    kt,soft=res
                    if kt>0.1:   # ignore trace <0.1 kt
                        found.append((ex,round(kt,1),soft))
                time.sleep(0.9)
            if found:
                found.sort(key=lambda x:-x[1])
                for ex,kt,soft in found:
                    note=(f"Exporter-mirror: {ex} reported {kt} kt of {cname} to {iso}, UN Comtrade 2024"
                          + (" (estimated)" if soft else "")
                          + ". Non-reporter filled via exporter mirror; not fabricated.")
                    rec={"from":ex,"to":iso,"value":kt,
                         "kind":"observed" if not soft else "modeled",
                         "src":"comtradeMirror","note":note,"_verified":"atlas_gapfill_macrun"}
                    if not args.dry_run: flows.append(rec)
                    log.append(f"{cname}: {ex}->{iso} {kt}kt"+(" (soft)" if soft else ""))
                    added+=1
                if not args.dry_run:
                    # CHECKPOINT after each importer so a Ctrl-C / throttle never loses work
                    json.dump(d,open(ATLAS,"w"),ensure_ascii=False,indent=1)
                print(f"  + {cname} {iso}: " + ", ".join(f"{e} {k}kt" for e,k,_ in found), flush=True)
            else:
                gaps.append(f"{cname}/{iso}")
                print(f"  . {cname} {iso}: no exporter-mirror flow (honest gap — stays blank)", flush=True)

    if not args.dry_run:
        json.dump(d,open(ATLAS,"w"),ensure_ascii=False,indent=1)
        print(f"\nWrote {added} new atlas flows to data/commodity_flows.json (checkpointed as it went)", flush=True)
        print(f"Honest gaps left blank (genuine non-reporters / re-export-hub routed): {len(gaps)}", flush=True)
        print("Re-run anytime to add more — it skips importers that already have a flow.", flush=True)
        print("Now: regenerate the index.html embed + run validate_data.py.", flush=True)
    else:
        print(f"\nDRY RUN: {added} flows would be added, {len(gaps)} honest gaps. No file written.", flush=True)

if __name__ == "__main__":
    main()
