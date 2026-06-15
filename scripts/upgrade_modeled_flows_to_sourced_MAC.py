#!/usr/bin/env python3
"""
========================================================================
FoodShield — upgrade UPGRADABLE modeled atlas flows to observed (sourced)
RUN ON YOUR MAC (unthrottled Comtrade access).
========================================================================

WHAT THIS DOES (and what it deliberately does NOT do)
  The atlas (data/commodity_flows.json) has ~597 `modeled` flows out of 4,043. Most are
  modeled for GOOD reasons and must STAY modeled:
    - exporter-mirror flows for Comtrade non-reporters (one-sided is the best that exists)
    - re-export attribution (India->Vietnam->China: the direct leg isn't a real record)
    - forward-looking projections
  This script ONLY targets the subset that is modeled because a real bilateral Comtrade 2024
  record exists but was never pulled — mainly USDA-PSD-allocated and ITC/FAO-estimate STAPLE
  flows (e.g. Ukraine->Egypt wheat: USDA-allocated as `modeled`, but Comtrade reports it
  directly at 1,913 kt). For each such flow it pulls the DIRECT bilateral
  (reporter=exporter, partner=importer) from Comtrade 2024 and, ONLY IF Comtrade confirms it
  with a real tonnage, promotes the flow to `kind:observed, src:comtrade` with the real value.

HONESTY (non-negotiable — matches the on-screen contract)
  - netWgt kg / 1e6 = kt. Only partner2Code==0 rows. legacyEstimationFlag!=0 -> keep modeled.
  - A flow that Comtrade does NOT confirm STAYS modeled, untouched. No fabrication, no
    "upgrade by assumption". Empty Comtrade response != observed.
  - Never downgrades anything. Never touches exporter-mirror / re-export / projection flows.
  - >20% disagreement between the existing modeled value and the Comtrade value -> keep the
    Comtrade value (it's the direct record) but flag it in the note for review.

USAGE
  cd "/path/to/FoodSecurity AI"
  python3 scripts/upgrade_modeled_flows_to_sourced_MAC.py --dry-run     # show what would upgrade
  python3 scripts/upgrade_modeled_flows_to_sourced_MAC.py               # write (checkpointed)
  python3 scripts/upgrade_modeled_flows_to_sourced_MAC.py --commodity wheat

After: python3 scripts/validate_data.py ; regenerate the index.html embed ; sync the mirror.
"""
import argparse, json, os, time, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATLAS = os.path.join(ROOT, "data", "commodity_flows.json")
PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
KEY = os.environ.get("COMTRADE_KEY", "")

# atlas commodity -> HS code(s). Multi-HS commodities (beef fresh+frozen, etc.) are pulled
# across all their HS lines and summed, so the upgraded value reflects the whole commodity.
HS = {
  # staples
  "wheat":["1001"], "rice":["1006"], "maize":["1005"], "soybeans":["1201"],
  "barley":["1003"], "sorghum":["1007"], "palmoil":["1511"], "sugar":["1701"],
  # meat & livestock
  "beef":["0201","0202"],          # fresh/chilled + frozen
  "poultry":["0207"], "pork":["0203"], "sheepgoat":["0204"], "offal":["0206"],
  "liveanimals":["0102"],
  # oilseeds & oils
  "oilseeds":["1205","1206"], "vegoils":["1507","1512","1514"], "feedcake":["2304","2306"],
  "groundnuts":["1202"],
  # dairy, eggs, fish
  "dairy":["0402","0406"], "eggs":["0407"], "fish":["0302","0303"], "shrimp":["0306"],
  # produce & other food
  "bananas":["0803"], "citrus":["0805"], "apples":["0808"], "grapes":["0806"],
  "potatoes":["0701"], "frozenpotato":["2004"], "vegetables":["0702","0703"], "roots":["0714"],
  "nuts":["0802"], "pulses":["0713"], "tea":["0902"], "coffee":["0901"], "cocoa":["1801"],
  "spices":["0904","0908"], "wine":["2204"], "beverages":["2203","2208"],
  "procgrains":["1101","1902"], "procveg":["2002"], "fertilizer":["3102","3104","3105"],
}

# src values that mark an UPGRADABLE flow (aggregate-allocated / estimate / EU-aggregate,
# NOT exporter-mirror or re-export). Eurostat-allocated EU flows (e.g. NLD->DEU beef) belong
# here: NLD/DEU/IRL are Comtrade reporters, so a direct bilateral exists and beats the
# Eurostat allocation.
UPGRADABLE_SRC = {"usdaPSD","usda","usdaPSDsoy","usdaOilseeds","itc","tdm","fao",
                  "faostatTCL","eurostat","abiec","usmef"}

# ISO3 -> M49 (exporters + importers that appear in the atlas). Extend as needed.
M49 = {
 "RUS":643,"UKR":804,"USA":842,"CAN":124,"FRA":251,"ARG":32,"AUS":36,"ROU":642,"DEU":276,
 "KAZ":398,"TUR":792,"BRA":76,"IND":699,"THA":764,"VNM":704,"PAK":586,"CHN":156,"MMR":104,
 "ITA":380,"ESP":724,"URY":858,"GUY":328,"KHM":116,"IDN":360,"MYS":458,"GTM":320,"BGR":100,
 "POL":616,"HUN":348,"LTU":440,"LVA":428,"NLD":528,"BEL":56,"MEX":484,"COL":170,"PER":604,
 "EGY":818,"NGA":566,"ZAF":710,"MAR":504,"DZA":12,"SAU":682,"IRN":364,"IRQ":368,"YEM":887,
 "BGD":50,"IDN2":360,"PHL":608,"JPN":392,"KOR":410,"LKA":144,"NPL":524,"AFG":4,"KEN":404,
 "ETH":231,"TZA":834,"MOZ":508,"AGO":24,"SEN":686,"CIV":384,"GHA":288,"CMR":120,"SDN":729,
 "TUN":788,"LBY":434,"JOR":400,"LBN":422,"SYR":760,"ARE":784,"OMN":512,"QAT":634,"KWT":414,
 "MWI":454,"ZMB":894,"ZWE":716,"UGA":800,"RWA":646,"BDI":108,"COD":180,"COG":178,"GAB":266,
 "VEN":862,"CUB":192,"DOM":214,"HTI":332,"GTM2":320,"HND":340,"NIC":558,"CRI":188,"PAN":591,
 "CHL":152,"PRY":600,"BOL":68,"ECU":218,"SGP":702,"HKG":344,"TWN":490,
 "GBR":826,"GRC":300,"IRL":372,"TJK":762,"PRT":620,"AUT":40,"CZE":203,"SVK":703,
 "DNK":208,"SWE":752,"FIN":246,"NOR":578,"CHE":756,"HRV":191,"SRB":688,
 # EU aggregate can't be pulled as one reporter — 'EU' rows are skipped
}

def _fetch_one(exporter_m49, importer_m49, hs, _tries=6):
    """One HS line. Returns kt (float), 'soft'/kt tuple, 0.0 (no flow), or None (error)."""
    q={"reporterCode":exporter_m49,"period":2024,"partnerCode":importer_m49,
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
    soft = x.get("legacyEstimationFlag",0)!=0 or x.get("isNetWgtEstimated")
    return ("soft",(x.get("netWgt") or 0)/1e6) if soft else (x.get("netWgt") or 0)/1e6

def fetch(exporter_m49, importer_m49, hs_list):
    """Sum a commodity across its HS lines. None if ANY line errors (rate-limit safety);
    'soft' if all-soft; total kt otherwise."""
    total=0.0; any_real=False; any_soft=False
    for hs in hs_list:
        r=_fetch_one(exporter_m49, importer_m49, hs)
        if r is None: return None
        if isinstance(r,tuple): any_soft=True; total+=r[1]
        elif r>0: any_real=True; total+=r
        time.sleep(0.4)   # gap between HS lines of the same commodity
    if any_real: return total
    if any_soft: return ("soft",total)
    return 0.0

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--commodity",default="")
    args=ap.parse_args()
    d=json.load(open(ATLAS)); comms=d.get("commodities",d)
    commodities=[args.commodity] if args.commodity else list(HS.keys())

    upgraded=confirmed_zero=unverifiable=skipped=0
    for cname in commodities:
        cd=comms.get(cname)
        if not isinstance(cd,dict) or cname not in HS: continue
        hs_list=HS[cname]
        for f in cd.get("flows",[]):
            if f.get("kind")!="modeled": continue
            if f.get("src") not in UPGRADABLE_SRC: skipped+=1; continue
            note=(f.get("note") or "").lower()
            if any(k in note for k in ("re-export","via ","expect","forecast","hub")):
                skipped+=1; continue
            ex,im=f.get("from"),f.get("to")
            exm,imm=M49.get(ex),M49.get(im)
            if not exm or not imm:    # can't pull (e.g. EU aggregate reporter)
                skipped+=1; continue
            res=fetch(exm,imm,hs_list); time.sleep(0.6)
            if res is None:
                unverifiable+=1; print(f"  ? {cname} {ex}->{im}: rate-limited/error (stays modeled)", flush=True); continue
            soft=False
            if isinstance(res,tuple): soft,res=True,res[1]
            if res and res>0.1 and not soft:
                old=f.get("value")
                disagree = old and abs(res-old)/max(old,1) > 0.20
                f["value"]=round(res,1)
                f["kind"]="observed"
                f["src"]="comtrade"
                f["note"]=(f"Direct bilateral: {ex} reported {round(res,1)} kt of {cname} to {im}, "
                           f"UN Comtrade 2024 (upgraded from modeled allocation"
                           + (f"; prior modeled value {old} differs >20%, Comtrade value kept" if disagree else "")
                           + ").")
                f["_verified"]="atlas_upgrade_macrun"
                upgraded+=1
                print(f"  + {cname} {ex}->{im}: {round(res,1)} kt -> OBSERVED"+(" [!disagree]" if disagree else ""), flush=True)
                if not args.dry_run:
                    json.dump(d,open(ATLAS,"w"),ensure_ascii=False,indent=1)  # checkpoint
            elif soft:
                unverifiable+=1; print(f"  ~ {cname} {ex}->{im}: Comtrade estimate-flagged (stays modeled)", flush=True)
            else:
                confirmed_zero+=1; print(f"  . {cname} {ex}->{im}: Comtrade shows no direct flow (stays modeled — likely re-export)", flush=True)

    if not args.dry_run:
        json.dump(d,open(ATLAS,"w"),ensure_ascii=False,indent=1)
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}upgraded {upgraded} modeled->observed; "
          f"{confirmed_zero} confirmed-no-direct (stay modeled); {unverifiable} unverifiable; {skipped} skipped (mirror/re-export/EU).", flush=True)
    if not args.dry_run:
        print("Now: python3 scripts/validate_data.py ; regenerate the embed ; sync the mirror.", flush=True)

if __name__ == "__main__":
    main()
