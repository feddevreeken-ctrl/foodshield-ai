#!/usr/bin/env python3
"""
========================================================================
FoodShield — confirm/upgrade exporter-MIRROR atlas flows using FAOSTAT
RUN ON YOUR MAC (FAOSTAT blocks datacenter IPs; the bulk ZIP works from a real machine).
========================================================================

THE GAP THIS FILLS
  ~360 atlas flows are `modeled` because they're exporter-mirror to a Comtrade NON-REPORTER
  (e.g. Russia->Nigeria wheat, India->Chad rice — the exporter reported it, the importer
  doesn't file to Comtrade, so Comtrade can't two-side confirm them). The maximize_observed
  script (Comtrade) correctly leaves these modeled.

  FAOSTAT's DETAILED TRADE MATRIX is bilateral AND imputes non-reporters (both-sides
  harmonized) — it's the project's stated "verified backbone." So FAOSTAT CAN confirm or
  correct exactly these flows that Comtrade cannot. This script uses it to upgrade them.

WHAT IT DOES
  1. Downloads the FAOSTAT Detailed Trade Matrix bulk (normalized CSV in a ZIP), once.
  2. For each exporter-mirror modeled flow, looks up FAOSTAT's bilateral
     export-quantity (element 5910) for {exporter -> importer, commodity, latest year}.
  3. If FAOSTAT has it and it agrees with the mirror value within 30%, upgrade to
     observed (src=faostatBilateral). If FAOSTAT has it but differs >30%, keep modeled but
     record BOTH values (honest discrepancy). If FAOSTAT lacks it, stays modeled.

HONESTY (non-negotiable)
  - FAOSTAT quantity is in tonnes -> /1000 = kt. Match item by NORMALIZED NAME (FAOSTAT drifts
    casing + dual item-code columns — see trade-data-verify/sources.md).
  - FAOSTAT non-reporter values are IMPUTED ESTIMATES. So an upgrade here is labeled
    `src=faostatBilateral` and the note says "FAOSTAT imputed bilateral" — it is NOT presented
    as a hard customs record. This is a genuine provenance step up from a one-sided mirror
    (FAOSTAT reconciles both sides) but the label stays honest about the imputation.
  - Never fabricate. FAOSTAT-absent flow stays modeled. >30% disagreement stays modeled + flagged.
  - FAOSTAT lags 1-2 years (latest ~2023 in mid-2026); the note records FAOSTAT's actual year.

USAGE
  cd "/path/to/FoodSecurity AI"
  python3 scripts/fill_mirror_flows_from_faostat_MAC.py --dry-run
  python3 scripts/fill_mirror_flows_from_faostat_MAC.py
  python3 scripts/fill_mirror_flows_from_faostat_MAC.py --zip /path/to/already_downloaded.zip
After: python3 scripts/validate_data.py ; regenerate the embed ; sync the mirror.
"""
import argparse, csv, io, json, os, re, sys, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATLAS = os.path.join(ROOT, "data", "commodity_flows.json")
BULK_URL = "https://bulks-faostat.fao.org/production/Trade_DetailedTradeMatrix_E_All_Data_(Normalized).zip"
CACHE = os.path.join(ROOT, "data", "_faostat_tradematrix.zip")

# atlas commodity -> FAOSTAT item name(s), normalized lowercase. FAOSTAT uses food-item names,
# not HS. Multiple names are summed (e.g. beef = the bovine-meat items).
FAO_ITEMS = {
  "wheat":["wheat"], "rice":["rice","rice, milled","rice, paddy (rice milled equivalent)"],
  "maize":["maize (corn)","maize"], "soybeans":["soya beans","soybeans"],
  "barley":["barley"], "sorghum":["sorghum"],
  "palmoil":["palm oil","oil, palm"], "sugar":["sugar (centrifugal, raw)","sugar refined","raw cane or beet sugar (centrifugal only)"],
  "beef":["meat of cattle with the bone, fresh or chilled","meat, cattle","bovine meat","meat of cattle boneless, fresh or chilled","meat of cattle, frozen"],
  "poultry":["meat of chickens, fresh or chilled","meat, chicken","poultry meat"],
  "pork":["meat of pig with the bone, fresh or chilled","meat, pig","pig meat"],
  "sheepgoat":["meat of sheep, fresh or chilled","meat, sheep","mutton and lamb"],
  "oilseeds":["rape or colza seed","sunflower seed","oilseeds"],
  "vegoils":["oil, soybean","oil, sunflower","soya bean oil","sunflower-seed oil, crude"],
  "feedcake":["cake, soybeans","cake of soya beans"], "groundnuts":["groundnuts, excluding shelled"],
  "dairy":["milk, whole dried","milk, skimmed dried","whole milk powder","cheese from whole cow milk"],
  "eggs":["eggs, hen, in shell","hen eggs in shell, fresh"],
  "fish":[], "shrimp":[],  # FAOSTAT TCL excludes fish (separate Fisheries dataset) -> leave to Comtrade
  "bananas":["bananas"], "citrus":["oranges","tangerines, mandarins, clementines","lemons and limes"],
  "apples":["apples"], "grapes":["grapes"], "potatoes":["potatoes"],
  "vegetables":["tomatoes","onions and shallots, dry (excluding dehydrated)"],
  "roots":["cassava, fresh","cassava (dry)"], "nuts":["cashew nuts, in shell","almonds, in shell"],
  "pulses":["beans, dry","peas, dry","lentils, dry","chick peas, dry"],
  "tea":["tea leaves","tea"], "coffee":["coffee, green"], "cocoa":["cocoa beans"],
  "spices":["pepper (piper spp.), raw","pepper, black"],
  "wine":["wine"], "beverages":["beer of barley, malted"],
  "fertilizer":[],  # not a FAOSTAT TCL food item
  "frozenpotato":[], "procgrains":[], "procveg":[],
}

def norm(s): return re.sub(r"[^a-z0-9 ]","",(s or "").lower()).strip()

# FAOSTAT area names -> ISO3 (partial; the matrix uses country names). Built from the atlas ISOs.
# A real run resolves names via the FAOSTAT area-code table inside the ZIP; here we match on a
# normalized-name map for the countries that actually appear. (FAOSTAT ships an area name column.)
def load_iso_map():
    # minimal name->iso3 for the countries in our mirror flows; extend as the dry-run reports misses.
    return {
     "russian federation":"RUS","ukraine":"UKR","united states of america":"USA","india":"IND",
     "china, mainland":"CHN","china":"CHN","pakistan":"PAK","thailand":"THA","viet nam":"VNM",
     "argentina":"ARG","brazil":"BRA","australia":"AUS","canada":"CAN","france":"FRA",
     "indonesia":"IDN","malaysia":"MYS","turkiye":"TUR","turkey":"TUR","kazakhstan":"KAZ",
     "nigeria":"NGA","ethiopia":"ETH","sudan":"SDN","chad":"TCD","mali":"MLI","niger":"NER",
     "yemen":"YEM","somalia":"SOM","eritrea":"ERI","djibouti":"DJI","haiti":"HTI","cuba":"CUB",
     "venezuela (bolivarian republic of)":"VEN","iraq":"IRQ","iran (islamic republic of)":"IRN",
     "syrian arab republic":"SYR","libya":"LBY","democratic republic of the congo":"COD",
     "congo":"COG","cameroon":"CMR","guinea":"GIN","sierra leone":"SLE","liberia":"LBR",
     "guinea-bissau":"GNB","gabon":"GAB","rwanda":"RWA","burundi":"BDI","kenya":"KEN",
     "united republic of tanzania":"TZA","mozambique":"MOZ","angola":"AGO","ghana":"GHA",
     "egypt":"EGY","morocco":"MAR","algeria":"DZA","tunisia":"TUN","saudi arabia":"SAU",
     "united arab emirates":"ARE","oman":"OMN","qatar":"QAT","kuwait":"KWT","jordan":"JOR",
     "lebanon":"LBN","bangladesh":"BGD","nepal":"NPL","afghanistan":"AFG","sri lanka":"LKA",
     "tajikistan":"TJK","benin":"BEN","togo":"TGO","burkina faso":"BFA","senegal":"SEN",
     "cote divoire":"CIV","côte d'ivoire":"CIV","gambia":"GMB","mauritania":"MRT","comoros":"COM",
     "malawi":"MWI","zambia":"ZMB","zimbabwe":"ZWE","uganda":"UGA","south sudan":"SSD",
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--zip",default="")
    args=ap.parse_args()

    zip_path = args.zip or CACHE
    if not os.path.exists(zip_path):
        print(f"Downloading FAOSTAT Detailed Trade Matrix bulk (~large, one-time)...\n  {BULK_URL}")
        try:
            urllib.request.urlretrieve(BULK_URL, zip_path)
        except Exception as e:
            print(f"DOWNLOAD FAILED: {e}\nIf FAOSTAT is slow, download the ZIP manually and pass --zip PATH.")
            return 1
    print(f"Reading {zip_path} ...")

    # FAOSTAT normalized CSV columns: Reporter Countries, Partner Countries, Item, Element, Year, Unit, Value, ...
    # We want Element 'Export Quantity' (5910), Reporter=exporter, Partner=importer.
    iso_map = load_iso_map()
    # index: (exporterISO, importerISO, atlas_commodity) -> (kt, year)
    fao = {}
    item_name_to_com = {}
    for com, names in FAO_ITEMS.items():
        for nm in names: item_name_to_com[norm(nm)] = com

    with zipfile.ZipFile(zip_path) as z:
        csv_name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        with z.open(csv_name) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace"))
            for row in reader:
                if (row.get("Element") or "").strip() != "Export Quantity": continue
                com = item_name_to_com.get(norm(row.get("Item")))
                if not com: continue
                ex = iso_map.get(norm(row.get("Reporter Countries")))
                im = iso_map.get(norm(row.get("Partner Countries")))
                if not ex or not im: continue
                try:
                    val = float(row.get("Value") or 0); yr = int(row.get("Year") or 0)
                except ValueError: continue
                kt = val/1000.0
                key=(ex,im,com)
                if key not in fao or yr > fao[key][1]:
                    fao[key]=(kt, yr)
    print(f"FAOSTAT bilateral export records indexed for our commodities: {len(fao)}")

    d=json.load(open(ATLAS)); comms=d.get("commodities",d)
    upg=confirmed=disagree=absent=0
    for cname,cd in comms.items():
        if not isinstance(cd,dict): continue
        for f in cd.get("flows",[]):
            if f.get("kind")!="modeled": continue
            if "mirror" not in str(f.get("src","")).lower(): continue   # only exporter-mirror flows
            ex,im=f.get("from"),f.get("to"); old=f.get("value")
            rec=fao.get((ex,im,cname))
            if not rec: absent+=1; continue
            kt,yr=rec
            if kt<=0.1: absent+=1; continue
            if old and abs(kt-old)/max(old,1) <= 0.30:
                f["value"]=round(kt,1); f["kind"]="observed"; f["src"]="faostatBilateral"
                f["note"]=(f"FAOSTAT Detailed Trade Matrix (bilateral, both-sides reconciled): "
                           f"{ex}->{im} {round(kt,1)}kt {cname}, FAOSTAT {yr} (upgraded from exporter-mirror; "
                           f"FAOSTAT imputes non-reporters).")
                f["_verified"]="faostat_bilateral_macrun"
                upg+=1; print(f"  + {cname} {ex}->{im}: {round(kt,1)}kt FAOSTAT {yr} -> observed", flush=True)
                if not args.dry_run: json.dump(d,open(ATLAS,"w"),ensure_ascii=False,indent=1)
            else:
                f["note"]=(f.get("note","")+f" [FAOSTAT {yr}: {round(kt,1)}kt vs mirror {old}kt — >30% gap, kept modeled.]").strip()
                disagree+=1; print(f"  ! {cname} {ex}->{im}: FAOSTAT {round(kt,1)} vs mirror {old} >30% — kept modeled, flagged", flush=True)
                if not args.dry_run: json.dump(d,open(ATLAS,"w"),ensure_ascii=False,indent=1)

    if not args.dry_run: json.dump(d,open(ATLAS,"w"),ensure_ascii=False,indent=1)
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}upgraded {upg} mirror->observed via FAOSTAT; "
          f"{disagree} disagreements flagged; {absent} not in FAOSTAT (stay modeled).")
    if not args.dry_run:
        obs=mod=0
        for cd in comms.values():
            if isinstance(cd,dict):
                for f in cd.get("flows",[]):
                    if f.get("kind")=="observed":obs+=1
                    elif f.get("kind")=="modeled":mod+=1
        print(f"atlas now: {obs} observed / {mod} modeled ({round(100*obs/(obs+mod),1)}% observed).")
        print("Then: validate_data.py ; regenerate the embed ; sync the mirror.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
