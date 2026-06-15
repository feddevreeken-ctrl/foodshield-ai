#!/usr/bin/env python3
"""
========================================================================
FoodShield — MAXIMIZE observed atlas flows (all 3 levers, one resumable run)
RUN ON YOUR MAC (the Cowork web_fetch is rate-limited; your Mac is not).
========================================================================

Upgrades `modeled` flows in data/commodity_flows.json to `observed` — ONLY when a real
UN Comtrade 2024 record confirms them. Three levers, run in order:

  LEVER 1 — DIRECT bilateral. For aggregate-allocated/estimate flows (USDA PSD, Eurostat,
            ITC, FAO), pull the exporter's direct export (reporter=exporter, flowCode=X).
            If Comtrade confirms a real tonnage -> observed.

  LEVER 2 — IMPORTER-MIRROR for confidential exporters. Russia (and a few others) report
            their grain/pulse exports as CONFIDENTIAL, so the export side is invisible. But
            the IMPORTER usually reports its imports. Pull reporter=importer, flowCode=M,
            partner=exporter. If the importer confirms -> observed (importer-reported).

  LEVER 3 — TWO-SIDE CONFIRMATION of exporter-mirror flows. ~261 flows are `modeled` because
            only the exporter reported (mirror). Pull the IMPORTER's side (flowCode=M). If the
            importer ALSO reports the flow and the two figures agree within 25%, the flow is
            two-side confirmed -> observed (both-sides). If they disagree >25%, keep the flow
            modeled but record both values in the note (honest discrepancy, not a silent pick).

HONESTY (non-negotiable)
  - netWgt kg / 1e6 = kt. Only partner2Code==0 rows. legacyEstimationFlag!=0 -> stays modeled.
  - A flow Comtrade does NOT confirm STAYS modeled. Empty/zero/error != observed. No fabrication.
  - Never downgrades. Never touches re-export/projection flows.
  - Checkpoints after EVERY flow -> safe to Ctrl-C / rate-limit; re-run to continue (it skips
    flows already upgraded this session via the _verified tag).

USAGE
  cd "/path/to/FoodSecurity AI"
  python3 scripts/maximize_observed_flows_MAC.py --dry-run            # preview all 3 levers
  python3 scripts/maximize_observed_flows_MAC.py                      # run all 3, write
  python3 scripts/maximize_observed_flows_MAC.py --lever 3            # just lever 3
  python3 scripts/maximize_observed_flows_MAC.py --commodity beef     # one commodity
After: python3 scripts/validate_data.py ; regenerate the index.html embed ; sync the mirror.
"""
import argparse, json, os, time, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATLAS = os.path.join(ROOT, "data", "commodity_flows.json")
PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
KEY = os.environ.get("COMTRADE_KEY", "")

HS = {
  "wheat":["1001"],"rice":["1006"],"maize":["1005"],"soybeans":["1201"],"barley":["1003"],
  "sorghum":["1007"],"palmoil":["1511"],"sugar":["1701"],"beef":["0201","0202"],
  "poultry":["0207"],"pork":["0203"],"sheepgoat":["0204"],"offal":["0206"],"liveanimals":["0102"],
  "oilseeds":["1205","1206"],"vegoils":["1507","1512","1514"],"feedcake":["2304","2306"],
  "groundnuts":["1202"],"dairy":["0402","0406"],"eggs":["0407"],"fish":["0302","0303"],
  "shrimp":["0306"],"bananas":["0803"],"citrus":["0805"],"apples":["0808"],"grapes":["0806"],
  "potatoes":["0701"],"frozenpotato":["2004"],"vegetables":["0702","0703"],"roots":["0714"],
  "nuts":["0802"],"pulses":["0713"],"tea":["0902"],"coffee":["0901"],"cocoa":["1801"],
  "spices":["0904","0908"],"wine":["2204"],"beverages":["2203","2208"],
  "procgrains":["1101","1902"],"procveg":["2002"],"fertilizer":["3102","3104","3105"],
}
LEVER1_SRC = {"usdaPSD","usda","usdaPSDsoy","usdaOilseeds","itc","tdm","fao","faostatTCL",
              "eurostat","abiec","usmef"}
MIRROR_SRC = {"witsMirror","comtradeMirror"}  # exporter-mirror flows (lever 3) + RUS (lever 2)

M49 = {
 "RUS":643,"UKR":804,"USA":842,"CAN":124,"FRA":251,"ARG":32,"AUS":36,"ROU":642,"DEU":276,
 "KAZ":398,"TUR":792,"BRA":76,"IND":699,"THA":764,"VNM":704,"PAK":586,"CHN":156,"MMR":104,
 "ITA":380,"ESP":724,"URY":858,"GUY":328,"KHM":116,"IDN":360,"MYS":458,"GTM":320,"BGR":100,
 "POL":616,"HUN":348,"LTU":440,"LVA":428,"NLD":528,"BEL":56,"MEX":484,"COL":170,"PER":604,
 "EGY":818,"NGA":566,"ZAF":710,"MAR":504,"DZA":12,"SAU":682,"IRN":364,"IRQ":368,"YEM":887,
 "BGD":50,"PHL":608,"JPN":392,"KOR":410,"LKA":144,"NPL":524,"AFG":4,"KEN":404,"ETH":231,
 "TZA":834,"MOZ":508,"AGO":24,"SEN":686,"CIV":384,"GHA":288,"CMR":120,"SDN":729,"TUN":788,
 "LBY":434,"JOR":400,"LBN":422,"SYR":760,"ARE":784,"OMN":512,"QAT":634,"KWT":414,"MWI":454,
 "ZMB":894,"ZWE":716,"UGA":800,"RWA":646,"BDI":108,"COD":180,"COG":178,"GAB":266,"VEN":862,
 "CUB":192,"DOM":214,"HTI":332,"HND":340,"NIC":558,"CRI":188,"PAN":591,"CHL":152,"PRY":600,
 "BOL":68,"ECU":218,"SGP":702,"HKG":344,"TWN":490,"GBR":826,"GRC":300,"IRL":372,"TJK":762,
 "PRT":620,"AUT":40,"CZE":203,"SVK":703,"DNK":208,"SWE":752,"FIN":246,"NOR":578,"CHE":756,
 "HRV":191,"SRB":688,"BFA":854,"MLI":466,"NER":562,"TCD":148,"GIN":324,"BEN":204,"TGO":768,
 "SOM":706,"ERI":232,"DJI":262,"COM":174,"MRT":478,"GMB":270,"SLE":694,"LBR":430,"GNB":624,
}

def _fetch_one(reporter_m49, partner_m49, hs, flow, _tries=6):
    q={"reporterCode":reporter_m49,"period":2024,"partnerCode":partner_m49,
       "cmdCode":hs,"flowCode":flow,"motCode":0,"customsCode":"C00"}
    if KEY: q["subscription-key"]=KEY
    url=PREVIEW+"?"+urllib.parse.urlencode(q)
    for a in range(_tries):
        try:
            with urllib.request.urlopen(url,timeout=40) as r:
                j=json.loads(r.read().decode()); break
        except Exception as e:
            s=str(e)
            if any(k in s for k in ("429","Too Many","timed out","URLError","500")):
                time.sleep(3*(a+1)); continue
            return None
    else:
        return None
    rows=[x for x in j.get("data",[]) if x.get("partner2Code")==0]
    if not rows: return 0.0
    x=rows[0]
    soft = x.get("legacyEstimationFlag",0)!=0 or x.get("isNetWgtEstimated")
    return ("soft",(x.get("netWgt") or 0)/1e6) if soft else (x.get("netWgt") or 0)/1e6

def pull(reporter_m49, partner_m49, hs_list, flow):
    """Sum a commodity across HS lines for one direction. None=error; 'soft'/kt; total kt; 0.0."""
    total=0.0; any_real=False; any_soft=False
    for hs in hs_list:
        r=_fetch_one(reporter_m49, partner_m49, hs, flow)
        if r is None: return None
        if isinstance(r,tuple): any_soft=True; total+=r[1]
        elif r>0: any_real=True; total+=r
        time.sleep(0.4)
    if any_real: return total
    if any_soft: return ("soft",total)
    return 0.0

def upgrade(f, kt, src_label, note):
    f["value"]=round(kt,1); f["kind"]="observed"; f["src"]=src_label
    f["note"]=note; f["_verified"]="maximize_observed_macrun"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--lever",type=int,default=0,help="run only lever 1/2/3 (0=all)")
    ap.add_argument("--commodity",default="")
    args=ap.parse_args()
    d=json.load(open(ATLAS)); comms=d.get("commodities",d)
    commodities=[args.commodity] if args.commodity else list(HS.keys())
    levers = [args.lever] if args.lever else [1,2,3]
    stats={"L1":0,"L2":0,"L3":0,"no_direct":0,"disagree_kept":0,"unverifiable":0,"skip":0}

    def save():
        if not args.dry_run: json.dump(d,open(ATLAS,"w"),ensure_ascii=False,indent=1)

    for cname in commodities:
        cd=comms.get(cname)
        if not isinstance(cd,dict) or cname not in HS: continue
        hs=HS[cname]
        for f in cd.get("flows",[]):
            if f.get("kind")!="modeled": continue
            if f.get("_verified")=="maximize_observed_macrun": continue
            note=(f.get("note") or "").lower()
            if any(k in note for k in ("re-export","via ","expect","forecast","hub")): stats["skip"]+=1; continue
            ex,im=f.get("from"),f.get("to"); old=f.get("value")
            exm,imm=M49.get(ex),M49.get(im)
            if not exm or not imm or ex=="EU" or im=="EU": stats["skip"]+=1; continue
            src=f.get("src","")

            # LEVER 1 — direct export pull for aggregate-allocated flows
            if 1 in levers and src in LEVER1_SRC and ex!="RUS":
                res=pull(exm,imm,hs,"X"); time.sleep(0.5)
                if res is None: stats["unverifiable"]+=1; print(f"  ? L1 {cname} {ex}->{im}: unverifiable",flush=True); continue
                if isinstance(res,tuple): stats["unverifiable"]+=1; print(f"  ~ L1 {cname} {ex}->{im}: soft, stays modeled",flush=True); continue
                if res>0.1:
                    dis="; prior modeled %s differs >20%%"%old if old and abs(res-old)/max(old,1)>0.2 else ""
                    upgrade(f,res,"comtrade",f"Direct bilateral: {ex}->{im} {round(res,1)}kt {cname}, UN Comtrade 2024 (L1 upgrade{dis}).")
                    stats["L1"]+=1; save(); print(f"  + L1 {cname} {ex}->{im}: {round(res,1)}kt OBSERVED",flush=True); continue
                stats["no_direct"]+=1; print(f"  . L1 {cname} {ex}->{im}: no direct (stays modeled)",flush=True); continue

            # LEVER 2 — importer-mirror for confidential exporters (RUS etc.)
            if 2 in levers and (ex=="RUS" or src in MIRROR_SRC and ex in ("RUS",)):
                res=pull(imm,exm,hs,"M"); time.sleep(0.5)   # importer reports its import FROM exporter
                if res is None: stats["unverifiable"]+=1; print(f"  ? L2 {cname} {ex}->{im}: unverifiable",flush=True); continue
                if isinstance(res,tuple) or not res or res<=0.1:
                    stats["no_direct"]+=1; print(f"  . L2 {cname} {ex}->{im}: importer doesn't confirm (stays modeled)",flush=True); continue
                upgrade(f,res,"comtradeImporter",f"Importer-reported: {im} reports {round(res,1)}kt {cname} imported from {ex}, UN Comtrade 2024 (L2; exporter side confidential).")
                stats["L2"]+=1; save(); print(f"  + L2 {cname} {ex}->{im}: {round(res,1)}kt OBSERVED (importer-reported)",flush=True); continue

            # LEVER 3 — two-side confirm exporter-mirror flows
            if 3 in levers and src in MIRROR_SRC and ex!="RUS":
                res=pull(imm,exm,hs,"M"); time.sleep(0.5)   # does the importer also report it?
                if res is None: stats["unverifiable"]+=1; print(f"  ? L3 {cname} {ex}->{im}: unverifiable",flush=True); continue
                if isinstance(res,tuple) or not res or res<=0.1:
                    stats["no_direct"]+=1; print(f"  . L3 {cname} {ex}->{im}: importer doesn't report (stays mirror-modeled)",flush=True); continue
                agree = old and abs(res-old)/max(old,1) <= 0.25
                if agree:
                    upgrade(f,res,"comtradeTwoSide",f"Two-side confirmed: importer {im} reports {round(res,1)}kt {cname} from {ex}, matches exporter-mirror within 25% (L3, UN Comtrade 2024).")
                    stats["L3"]+=1; save(); print(f"  + L3 {cname} {ex}->{im}: {round(res,1)}kt OBSERVED (two-side)",flush=True); continue
                else:
                    # disagreement -> keep modeled but record both, honestly
                    f["note"]=(f.get("note","")+f" [L3: importer {im} reports {round(res,1)}kt vs exporter-mirror {old}kt — >25% gap, kept modeled pending review.]").strip()
                    stats["disagree_kept"]+=1; save(); print(f"  ! L3 {cname} {ex}->{im}: importer {round(res,1)} vs mirror {old} >25% — kept modeled, flagged",flush=True); continue

            stats["skip"]+=1

    save()
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}upgrades: L1={stats['L1']} L2={stats['L2']} L3={stats['L3']} "
          f"(total {stats['L1']+stats['L2']+stats['L3']} new observed). "
          f"no-confirm-stay-modeled={stats['no_direct']}, disagreements-flagged={stats['disagree_kept']}, "
          f"unverifiable={stats['unverifiable']}, skipped={stats['skip']}.",flush=True)
    if not args.dry_run:
        obs=mod=0
        for cd in comms.values():
            if isinstance(cd,dict):
                for f in cd.get("flows",[]):
                    if f.get("kind")=="observed": obs+=1
                    elif f.get("kind")=="modeled": mod+=1
        print(f"atlas now: {obs} observed / {mod} modeled ({round(100*obs/(obs+mod),1)}% observed).",flush=True)
        print("Re-run to continue if rate-limited. Then validate_data.py + regenerate the embed + sync.",flush=True)

if __name__ == "__main__":
    main()
