#!/usr/bin/env python3
"""
Round 2 probe — gets the EXACT data shapes needed to write parser fixes for
Aqueduct, WGI, and CCKP. Uses requests (certifi CA bundle) like the real
pipelines. Writes NO data files. Run locally and paste the full output.

  python3 scripts/_probe_pipelines.py
"""
import csv, io, json, sys
try:
    import requests
except ImportError:
    print("pip3 install requests certifi"); sys.exit(1)

UA = {"User-Agent": "Mozilla/5.0 (compatible; FoodShieldBot/1.0)"}

def get(url, timeout=40):
    return requests.get(url, headers=UA, timeout=timeout)

print("#" * 72)
print("# 1. AQUEDUCT — WB data360 mirror: dump columns + 2 sample rows")
print("#" * 72)
try:
    r = get("https://data360api.worldbank.org/data360/data?DATABASE_ID=WRI_AQDT&format=csv")
    print("HTTP", r.status_code, "bytes", len(r.content))
    reader = csv.DictReader(io.StringIO(r.text))
    cols = reader.fieldnames
    print("COLUMNS:", cols)
    rows = []
    indicators = set()
    ref_areas = set()
    for i, row in enumerate(reader):
        if i < 3:
            rows.append(row)
        indicators.add(row.get("INDICATOR"))
        ref_areas.add(row.get("REF_AREA"))
        if i > 5000:
            break
    print("\nSAMPLE ROW 1:", json.dumps(rows[0], ensure_ascii=False) if rows else "none")
    print("\nSAMPLE ROW 2:", json.dumps(rows[1], ensure_ascii=False) if len(rows) > 1 else "none")
    print("\nDISTINCT INDICATOR codes (first 25):", sorted(x for x in indicators if x)[:25])
    print("SAMPLE REF_AREA values (first 15):", sorted(x for x in ref_areas if x)[:15])
except Exception as e:
    print("EXCEPTION:", type(e).__name__, str(e)[:200])

print("\n" + "#" * 72)
print("# 2. WGI — test candidate indicator code formats")
print("#" * 72)
# WB archived the bare *.EST codes; current WGI codes are usually prefixed.
wgi_candidates = [
    "CC.EST",                 # old (control)
    "CC.PER.RNK",             # percentile rank variant
    "WGI.CC.EST",             # namespaced
    "GV.CONT.CORR.ES",        # possible new
    "CC.EST.XQ",              # variant
]
for code in wgi_candidates:
    url = f"https://api.worldbank.org/v2/country/USA/indicator/{code}?format=json&mrnev=1"
    try:
        r = get(url, timeout=20)
        body = r.json()
        ok = isinstance(body, list) and len(body) > 1 and body[1]
        preview = json.dumps(body)[:220]
        print(f"  {code:18} HTTP {r.status_code}  {'DATA ✓' if ok else 'no-data'}  {preview}")
    except Exception as e:
        print(f"  {code:18} EXCEPTION {type(e).__name__}: {str(e)[:80]}")
# Also: search the WB indicator catalog for governance indicators
try:
    r = get("https://api.worldbank.org/v2/indicator?format=json&per_page=20000", timeout=40)
    body = r.json()
    if isinstance(body, list) and len(body) > 1:
        gov = [(x["id"], x["name"]) for x in body[1]
               if any(k in (x.get("name") or "").lower()
                      for k in ["control of corruption", "rule of law", "government effectiveness",
                                "political stability", "regulatory quality", "voice and account"])]
        print("\n  WGI-matching indicators in WB catalog:")
        for cid, name in gov[:15]:
            print(f"    {cid:22} {name[:60]}")
except Exception as e:
    print("  catalog search EXCEPTION:", type(e).__name__, str(e)[:120])

print("\n" + "#" * 72)
print("# 3. CCKP — test candidate URL paths for one country (USA)")
print("#" * 72)
base = "https://cckpapi.worldbank.org/cckp/v1"
cckp_candidates = [
    # current script's path
    f"{base}/era5-x0.25_timeseries_tas_timeseries_annual_1991-2020_median_historical_era5-x0.25_mean_country_mean/USA",
    # without trailing statistic
    f"{base}/era5-x0.25_climatology_tas_climatology_annual_1991-2020_median_historical_ensemble_all_mean/USA",
    # 'all' geo with _format
    f"{base}/era5-x0.25_timeseries_tas_timeseries_annual_1991-2020_mean_historical_ensemble_all_mean/all_countries?_format=json",
    # cmip6 projection
    f"{base}/cmip6-x0.25_climatology_tas_climatology_annual_2040-2059_median_ssp245_ensemble_all_mean/USA",
    # simpler climatology pattern
    f"{base}/era5-x0.25_climatology_tas_climatology_annual_1991-2020_mean_historical_ensemble_all_mean/USA",
]
for url in cckp_candidates:
    try:
        r = get(url, timeout=25)
        body = r.json()
        data = body.get("data") if isinstance(body, dict) else None
        has = bool(data)
        print(f"  HTTP {r.status_code} {'DATA ✓' if has else 'empty'}  ...{url[-70:]}")
        if has:
            print(f"      data preview: {json.dumps(data)[:200]}")
    except Exception as e:
        print(f"  EXCEPTION {type(e).__name__}: {str(e)[:80]}  ...{url[-60:]}")

print("\n" + "#" * 72)
print("DONE — paste everything above.")
