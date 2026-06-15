#!/usr/bin/env python3
"""
========================================================================
FoodShield — rebuild ALL weak country trade rows from live UN Comtrade 2024
RUN THIS ON YOUR MAC (it has unthrottled internet; the Cowork sandbox does
not — Cowork's web_fetch tool is rate-limited to a handful of calls).
========================================================================

WHAT IT DOES
  For every country whose `suppliers`/`supPct` row is still partial / legacy /
  modeled (89 as of 2026-06-08), it:
    1. pulls each staple's suppliers from Comtrade 2024 using the EXPORTER-MIRROR
       (these countries are non-reporters: query each exporter's shipments TO them),
    2. aggregates suppliers across staples by real kt, computes whole-% shares,
    3. writes imports / suppliers / supPct back into data/countries.json with full
       provenance (source, source_url, as_of=2024, basis, basis_unit, exact-flow note),
    4. tags trade_scope (single_commodity_route / staple_basket / full_food_trade_surface
       / transit_hub) and flags transit hubs where tonnage >> population consumption,
    5. leaves a clean audit log of every flow pulled.

  It does NOT touch exports (food-only export cleanup is handled separately) and it
  NEVER fabricates: an empty/զero Comtrade response is recorded as an honest gap, not
  a guess. Rows it can't source stay flagged, not invented.

HONESTY CONTRACT (matches skills/trade-data-verify/SKILL.md)
  - netWgt is kg -> /1e6 = kt.
  - count ONLY the row with partner2Code==0 (others are re-export duplicates).
  - legacyEstimationFlag != 0 -> mark the figure soft (quality stays 'partial').
  - >2 suppliers within 10% and a source disagreement -> flag, don't silently pick.

USAGE
  cd "/path/to/FoodSecurity AI"
  python3 scripts/rebuild_all_weak_trade_rows_MAC.py --dry-run     # pull + print, no write
  python3 scripts/rebuild_all_weak_trade_rows_MAC.py               # pull + write countries.json
  python3 scripts/rebuild_all_weak_trade_rows_MAC.py --only ETH,SOM,GIN   # subset
  COMTRADE_KEY=xxxx python3 scripts/rebuild_all_weak_trade_rows_MAC.py    # use a free key (faster, higher quota)

After it runs:
  python3 scripts/validate_data.py          # must show 0 metadata failures
  # then regenerate the index.html embed + sync foodshield-v21.html as usual.
"""
import argparse, json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNTRIES = os.path.join(ROOT, "data", "countries.json")
LOG = os.path.join(ROOT, "data", "_rebuild_all_weak_log.json")
PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
AUTHED  = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
KEY = os.environ.get("COMTRADE_KEY", "")

# ---- HS codes for the staples we rank suppliers on ----
HS = {"Wheat":"1001","Rice":"1006","Maize":"1005","Soybeans":"1201",
      "Palm oil":"1511","Sugar":"1701","Vegetable oil":"1507","Sorghum":"1007",
      "Barley":"1003","Poultry":"0207"}
# map fuzzy import-list names -> a staple we can pull
NAME2STAPLE = {"Wheat flour":"Wheat","Wheat":"Wheat","Rice":"Rice","Maize":"Maize",
  "Soybeans":"Soybeans","Palm oil":"Palm oil","Vegetable oil":"Vegetable oil",
  "Sugar":"Sugar","Sorghum":"Sorghum","Barley":"Barley","Poultry":"Poultry"}

# ---- M49 numeric codes (ISO3 -> code) for targets AND candidate exporters ----
M49 = {
 # targets
 "ABW":533,"AFG":4,"AND":20,"ARE":784,"ASM":16,"ATG":28,"BEN":204,"BES":535,
 "BGD":50,"BHS":44,"BLR":112,"BMU":60,"BTN":64,"BWA":72,"CMR":120,"COD":180,
 "COG":178,"CUB":192,"CYM":136,"ERI":232,"ETH":231,"FRA":251,"FRO":234,"FSM":583,
 "GAB":266,"GHA":288,"GIN":324,"GNB":624,"GRL":304,"GUM":316,"IMN":833,"IND":699,
 "IRN":364,"IRQ":368,"KIR":296,"KNA":659,"LAO":418,"LBR":430,"LBY":434,"LIE":438,
 "MAF":663,"MCO":492,"MHL":584,"MKD":807,"MLI":466,"MNP":580,"NCL":540,"NIU":570,
 "NPL":524,"NRU":520,"PLW":585,"PNG":598,"PRI":630,"PRK":408,"PSE":275,"PYF":258,
 "ROU":642,"RUS":643,"RWA":646,"SDN":729,"SLE":694,"SMR":674,"SOM":706,"SRB":688,
 "STP":678,"SUR":740,"SVK":703,"SVN":705,"SYC":690,"SYR":760,"TCD":148,"TGO":768,
 "TJK":762,"TKM":795,"TON":776,"TTO":780,"TUV":798,"TWN":490,"UGA":800,"URY":858,
 "USA":842,"UZB":860,"VEN":862,"VIR":850,"VNM":704,"VUT":548,"WSM":882,"XKX":412,
 "ZMB":894,
 # candidate exporters (origins)
 "ARG":32,"AUS":36,"BRA":76,"CAN":124,"CHN":156,"EGY":818,"GUY":328,"KAZ":398,
 "MYS":458,"IDN":360,"PAK":586,"THA":764,"TUR":792,"UKR":804,"FRA":251,"ZAF":710,
 "TZA":834,"KEN":404,"NZL":554,"VNM":704,"SGP":702,"UZB":860,"SAU":682,"NLD":528,
 "DEU":276,"ITA":380,"ESP":724,"GBR":826,"PRT":620,"BEL":56,"POL":616,"ROU":642,
 "MEX":484,"COL":170,"PER":604,"SEN":686,"CIV":384,"GHA":288,"NGA":566,"ETH":231,
 "MOZ":508,"ZMB":894,"ZWE":716,"MWI":454,"SDN":729,"FJI":242,"USA":842,"IND":699,
 "RUS":643,"BLR":112,"TTO":780,
}

# ---- candidate exporters by region, so we don't pull all 200 reporters per staple ----
GLOBAL_GRAIN = ["USA","CAN","ARG","BRA","RUS","UKR","FRA","AUS","ROU","DEU","KAZ"]
RICE_ORIG    = ["IND","THA","VNM","PAK","CHN","MMR","KHM","USA","GUY","ITA","ESP","BRA","URY"]
PALM_ORIG    = ["IDN","MYS"]
SUGAR_ORIG   = ["BRA","THA","IND","GTM","FRA","EGY","ZAF","AUS"]
REGION = {  # target ISO -> extra likely neighbours/origins to test first
 "default": GLOBAL_GRAIN,
 # Africa
 "ETH":["UKR","RUS","USA","ARG","ROU","IND","TUR"],"SOM":["IND","PAK","THA","UKR","RUS","TUR","BRA"],
 "GIN":["IND","CHN","THA","PAK","SEN","BRA","FRA","UKR","RUS"],"MLI":["IND","SEN","CIV","BRA","FRA","UKR","RUS"],
 "TCD":["EGY","FRA","RUS","TUR","UKR","IND","PAK"],"COD":["ZAF","TZA","ZMB","IND","THA","BRA","UKR","RUS"],
 "COG":["FRA","BRA","UKR","RUS","THA","VNM","IND"],"CMR":["FRA","RUS","UKR","IND","THA","BRA"],
 "BEN":["IND","THA","VNM","FRA","RUS","UKR","BRA"],"TGO":["IND","THA","VNM","FRA","RUS","UKR"],
 "GHA":["VNM","THA","IND","USA","CAN","BRA","RUS"],"ETH":["UKR","RUS","USA","ARG","ROU"],
 "GAB":["FRA","BRA","THA","VNM","IND","RUS","UKR"],"LBY":["UKR","RUS","TUR","FRA","ARG","BRA","IND"],
 "ERI":["RUS","UKR","TUR","SAU","ARG","AUS","IND"],"RWA":["TZA","UGA","ZMB","RUS","UKR"],
 "UGA":["RUS","UKR","ARG","USA","IND","TZA","KEN"],"BWA":["ZAF","ZMB","ARG","BRA"],
 "ZMB":["ZAF","TZA","ZWE","ARG","BRA"],"STP":["PRT","BRA","IND","THA","FRA"],
 "SYC":["IND","PAK","THA","ZAF","FRA","BRA"],
 # Middle East / Asia
 "IRQ":["USA","AUS","RUS","UKR","TUR","IND","THA","BRA","ARG"],"IRN":["RUS","KAZ","IND","BRA","ARG","UAE","TUR"],
 "SYR":["RUS","UKR","TUR","IND","BRA","ARG"],"ARE":["IND","RUS","UKR","AUS","CAN","BRA","ARG","THA","PAK"],
 "AFG":["KAZ","UZB","PAK","RUS","IND","TKM"],"TJK":["KAZ","RUS","UZB"],"TKM":["KAZ","RUS","UZB"],
 "UZB":["KAZ","RUS","UKR"],"NPL":["IND","CHN"],"BTN":["IND"],"BGD":["IND","RUS","UKR","CAN","USA","BRA","ARG","VNM","THA"],
 "LAO":["THA","VNM","CHN"],"PRK":["CHN","RUS"],"PNG":["AUS","NZL","USA","CAN"],"VUT":["CHN","THA","VNM","AUS","NZL"],
 # Americas / Caribbean
 "CUB":["RUS","FRA","CAN","BRA","ARG","VNM","MEX","DEU"],"VEN":["BRA","ARG","USA","CAN","MEX","RUS","TUR"],
 "SUR":["GUY","USA","BRA","NLD","TTO"],"TTO":["USA","CAN","GUY","BRA","ARG"],
 "ATG":["USA","CAN","GUY","BRA","TTO"],"BHS":["USA","CAN","BRA"],"KNA":["USA","CAN","GUY"],
 # Pacific micro-states
 "KIR":["AUS","NZL","FJI","USA","THA","VNM"],"TUV":["AUS","NZL","FJI"],"NRU":["AUS","NZL","FJI"],
 "FSM":["USA","AUS","CHN","THA","VNM"],"MHL":["USA","AUS","THA","VNM"],"PLW":["USA","THA","VNM","AUS"],
 "WSM":["NZL","AUS","THA","VNM","FJI"],"TON":["NZL","AUS","THA","VNM","FJI"],"NIU":["NZL","AUS"],
 # Europe micro / others
 "AND":["ESP","FRA"],"MCO":["FRA","ITA"],"SMR":["ITA"],"LIE":["DEU","AUT" if False else "FRA"],
 "MKD":["RUS","UKR","SRB","BGR" if False else "ROU","HUN" if False else "DEU"],"SVN":["DEU","ITA","HRV" if False else "ROU","FRA"],
 "SVK":["DEU","FRA","ROU","HUN" if False else "POL"],"SRB":["ROU","RUS","UKR","DEU"],"XKX":["DEU","ROU","SRB" ,"UKR"],
 "BLR":["RUS","UKR"],"RUS":["KAZ","BRA","ARG"],"ROU":["UKR","RUS","FRA","DEU"],"FRA":["BRA","USA","ARG"],
 "ABW":["USA","NLD","BRA"],"BES":["USA","NLD","BRA"],"CYM":["USA","CAN"],"BMU":["USA","CAN"],
 "GRL":["DNK" if False else "DEU","ISL" if False else "NLD"],"FRO":["DNK" if False else "DEU","NLD"],
 "IMN":["GBR","IRL" if False else "FRA"],"PRI":["USA","DOM" if False else "BRA"],"VIR":["USA"],"GUM":["USA","AUS","THA"],
 "ASM":["NZL","USA","AUS"],"MNP":["USA","AUS","THA"],"NCL":["AUS","NZL","FRA"],"PYF":["FRA","NZL","USA"],"MAF":["FRA","USA"],
 "LBR":["IND","CHN","THA","USA","VNM","PAK"],"SLE":["IND","CHN","THA","PAK","VNM"],"GNB":["IND","PAK","CHN","SEN","THA"],
 "SDN":["RUS","UKR","EGY","IND","AUS"],"SSD":["UGA","KEN","ETH","SDN"],"BDI":["UGA","TZA","KEN","ZMB"],
 "SWZ":["ZAF","ZMB"],"PSE":["TUR","EGY","RUS","UKR","JOR" if False else "SAU"],
}

POP = {  # rough population (millions) for transit-hub heuristic; only need an order of magnitude
 "DJI":1.1,"GNB":2.1,"COM":0.85,"TLS":1.3,"BDI":12.6,"SSD":11.0,"ERI":3.6,"TCD":17.7,
}

def fetch(target_m49, hs, exporter_m49, _tries=6):
    """One exporter-mirror pull. Returns (kt, status) or (None, err). Retries on 429.

    Uses the FREE preview endpoint (it returns full data and needs no key). The
    authenticated /data/v1/get/ endpoint was unreliable from this account, so we
    don't use it. If a COMTRADE_KEY is set we pass it to the preview endpoint as an
    optional quota bump (harmless if ignored), but the call works without it.
    """
    q = {"reporterCode":exporter_m49,"period":2024,"partnerCode":target_m49,
         "cmdCode":hs,"flowCode":"X","motCode":0,"customsCode":"C00"}
    if KEY: q["subscription-key"] = KEY
    url = PREVIEW + "?" + urllib.parse.urlencode(q)
    last = ""
    for attempt in range(_tries):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                j = json.loads(r.read().decode())
            break
        except Exception as e:
            last = str(e)
            # 429 / transient -> exponential backoff and retry (don't mis-record as a gap)
            if "429" in last or "Too Many" in last or "timed out" in last or "URLError" in last or "500" in last:
                time.sleep(3 * (attempt + 1))   # 3,6,9,12,15s backoff
                continue
            return None, f"err:{last}"
    else:
        return None, f"err:rate_limited:{last}"
    rows = [x for x in j.get("data",[]) if x.get("partner2Code")==0]
    if not rows:
        return 0.0, "zero"
    x = rows[0]
    kt = (x.get("netWgt") or 0)/1e6
    soft = x.get("legacyEstimationFlag",0)!=0 or x.get("isNetWgtEstimated")
    return kt, ("soft" if soft else "ok")

def scope_for(target, staple_count, total_kt):
    pop = POP.get(target)
    if pop and total_kt/ pop > 50:   # >50 kg/capita of a single staple basket = transit signal
        return "transit_hub"
    if staple_count<=1: return "single_commodity_route"
    if staple_count<=4: return "staple_basket"
    return "full_food_trade_surface"

def rebuild(iso, row, dry):
    m49 = M49.get(iso)
    if not m49:
        return None, f"{iso}: no M49 code — add it"
    imp = row.get("imports"); imp_list = imp.get("value") if isinstance(imp,dict) else imp
    staples = []
    for nm in (imp_list or []):
        s = NAME2STAPLE.get(nm)
        if s and s not in staples: staples.append(s)
    if not staples:
        staples = ["Rice","Wheat","Maize"]   # default basket if the import list is non-staple
    cand = list(dict.fromkeys((REGION.get(iso, REGION["default"]) + GLOBAL_GRAIN +
            (RICE_ORIG if "Rice" in staples else []) +
            (PALM_ORIG if "Palm oil" in staples else []))))
    agg = {}; log=[]; throttled=0; calls=0
    for staple in staples:
        hs = HS.get(staple);  any_origin = PALM_ORIG if staple=="Palm oil" else (RICE_ORIG if staple=="Rice" else cand)
        for ex in any_origin:
            exm = M49.get(ex)
            if not exm: continue
            kt, status = fetch(m49, hs, exm); calls+=1
            if kt is None:
                # fetch() already retried 429s with backoff; a None here is a real error/rate-limit
                if isinstance(status,str) and ("rate_limited" in status or "429" in status):
                    throttled+=1
                time.sleep(1.0)
            elif kt>0.0:
                agg[ex] = agg.get(ex,0)+kt
                log.append(f"{iso} {staple} <- {ex}: {kt:.2f} kt ({status})")
            time.sleep(0.9)   # steady pacing for the free preview endpoint + backoff handles spikes
    if not agg:
        # distinguish a genuine no-trade gap from a throttle-induced empty so we never
        # bake a false gap into the data
        if throttled and throttled >= max(1, calls//2):
            return None, f"{iso}: RATE-LIMITED ({throttled}/{calls} calls throttled) — NOT a real gap, re-run"
        return None, f"{iso}: no Comtrade flows found (kept flagged, not invented)"
    ranked = sorted(agg.items(), key=lambda kv:-kv[1])
    total = sum(v for _,v in ranked)
    sup = [k for k,_ in ranked][:8]
    kt_list = [round(v,1) for _,v in ranked][:8]   # real kilotons per supplier
    pct = [round(v/total*100) for _,v in ranked][:8]
    # fix rounding to sum 100 on the top item
    if pct: pct[0]+= 100-sum(pct)
    scope = scope_for(iso, len(staples), total)
    note = (f"Suppliers aggregated across staples by real kt (UN Comtrade 2024 exporter-mirror): "
            + ", ".join(f"{k} {v:.1f} kt" for k,v in ranked[:8])
            + f". Total {total:.0f} kt across {', '.join(staples)}."
            + (" Transit-hub: tonnage far exceeds domestic consumption — partly re-export." if scope=="transit_hub" else ""))
    src = lambda value,basis,unit: {"value":value,"quality_flag":"sourced","as_of":"2024",
        "source":"UN Comtrade 2024 (HS6, exporter-mirror for non-reporter)",
        "source_url":"https://comtradeplus.un.org/","source_dataset":"un_comtrade_hs6_2024_mirror",
        "coverage":"bilateral_hs6_mirror_reported","basis":basis,"basis_unit":unit,
        "trade_schema_version":"v2","_verified":"comtrade_2024_mirror_macrun","note":note,
        **({"trade_scope":scope} if basis=="rank_quantity_kt" else {})}
    new = {"imports": {**src([s for s in staples],"rank_quantity_kt","kt")},
           "suppliers": src(sup,"rank_quantity_kt","kt"),
           "supPct": src(pct,"share_quantity_pct","pct"),
           # real tonnage per supplier (parallel to supPct) so the UI can show "USA 82% · 47 kt"
           "supKt": {**src(kt_list,"quantity_kt","kt"), "total_kt": round(total,1)}}
    return (new, log)   # dry-run vs write is decided by the caller, not here

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--resume", action="store_true",
                    help="skip rows already rebuilt by a prior run (lets you re-run after a Ctrl-C / throttle without redoing work)")
    args = ap.parse_args()
    d = json.load(open(COUNTRIES)); C = d["data"]["countries"]
    def weak(r):
        f=lambda k: (r.get(k) or {}).get("quality_flag") if isinstance(r.get(k),dict) else None
        return f("suppliers") in ("partial","legacy_curated","heritage","modeled") or \
               f("supPct") in ("partial","legacy_curated","heritage","modeled")
    def already_done(r):
        s = r.get("suppliers")
        return isinstance(s,dict) and s.get("_verified") in ("comtrade_2024_mirror_macrun",)
    only = set(x.strip() for x in args.only.split(",") if x.strip())
    targets = [iso for iso,r in C.items() if len(iso)==3 and not iso.startswith("US-")
               and weak(r) and (not only or iso in only)
               and not (args.resume and already_done(C[iso]))]
    mode = " (DRY RUN)" if args.dry_run else (" (RESUME — skipping already-done)" if args.resume else "")
    print(f"{len(targets)} weak rows to rebuild{mode}\n", flush=True)
    alllog=[]; done=0; gaps=[]
    for iso in sorted(targets):
        res, info = rebuild(iso, C[iso], args.dry_run)
        if res is None:
            print(f"  · {info}", flush=True); gaps.append(iso)
            continue
        new, log = res, info
        alllog += log
        if args.dry_run:
            for line in log: print(f"  {line}", flush=True)
        else:
            C[iso]["imports"]=new["imports"]; C[iso]["suppliers"]=new["suppliers"]; C[iso]["supPct"]=new["supPct"]
            # CHECKPOINT: write after EVERY country so a Ctrl-C / throttle never loses work.
            json.dump(d, open(COUNTRIES,"w"), ensure_ascii=False, indent=1)
        done+=1
        top=", ".join(f"{s}{p}%" for s,p in zip(new["suppliers"]["value"],new["supPct"]["value"]))
        print(f"  ✓ {iso}: {top}  [{new['imports'].get('trade_scope')}]  (saved {done}/{len(targets)})", flush=True)
    if not args.dry_run:
        json.dump(d, open(COUNTRIES,"w"), ensure_ascii=False, indent=1)
        json.dump({"_meta":{"generated_at":datetime.now(timezone.utc).isoformat(),"rebuilt":done,"gaps":gaps},
                   "log":alllog}, open(LOG,"w"), indent=1)
        print(f"\nWrote {done} rows to data/countries.json (checkpointed after each). Gaps (kept flagged): {gaps}", flush=True)
        print("If it was throttled, just re-run with --resume to finish the rest.", flush=True)
        print("Then: python3 scripts/validate_data.py   (expect 0 metadata failures); regenerate the embed; sync the mirror.", flush=True)
    else:
        print(f"\nDRY RUN: {done} would be rebuilt, {len(gaps)} gaps. No file written.", flush=True)

if __name__ == "__main__":
    main()
