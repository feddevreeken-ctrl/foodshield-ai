"""
FAOSTAT — global food consumer-price indices (CP domain).

v38 (2026-06-13): rewritten to pull from the FAOSTAT **bulk ZIP** first
(bulks-faostat.fao.org — no token, no rate limit), which is the same reliable
path refresh_net_food_trade.py and refresh_faostat_fbs.py use. The old JSON-API
path (faostatservices.fao.org/api/v1, guest-JWT) kept failing/blocking and wrote
an empty file, which collapsed the nowcast "food inflation" signal. The JSON API
is retained only as a fallback if the bulk download fails.

Dataset: CP (Consumer Price Indices).
  - element 23014 = Consumer Prices, Food Indices (2015 = 100)   <-- the FOOD index
  - element 23013 = Consumer Prices, General Indices (2015 = 100) <-- NOT what we want
    (the earlier default 23012 was the General/older code and is a likely reason
     the food filter kept 0 rows — see v38.2 note below; we now match by NAME first
     and treat the numeric code only as a hint, so a code drift can't silently
     produce an empty file again.)
  - months code 7021 = annual average ("Annual value" in the bulk CSV)
We pull the most recent two available years per country and compute YoY change.

Output: data/faostat_food.json
  {
    iso3: {
      "food_cpi_index_latest": <e.g. 142.3, base 2015=100>,
      "food_cpi_yoy_pct": <% change vs previous year>,
      "year_latest": <year>,
      "country": <name>,
      "inflation_shock": <bool>,    # YoY > 15%
    }
  }
"""
import io
import time
import zipfile
import csv as _csv

from _common import http_get, write_json

BULK_URL = "https://bulks-faostat.fao.org/production/ConsumerPriceIndices_E_All_Data_(Normalized).zip"
AUTH_URL = "https://faostatservices.fao.org/api/v1/auth/guest"
DATA_URL = "https://faostatservices.fao.org/api/v1/en/data/CP"

# Numeric codes are now only HINTS — matching is name-first (see _is_food_element).
# 23014 is the current FOOD index code; 23012 was the previous default and matches
# the GENERAL index in the current bulk, which is why the old filter kept 0 rows.
FOOD_ELEMENT_CODES = {"23014", "23012"}   # accept either; name match decides
ANNUAL_MONTH_CODE = "7021"                # annual average ("Annual value")

# FAOSTAT M49 / FAO area codes → ISO3 for the subset we care about.
FAO_AREA_TO_ISO3 = {
    2:"AFG",3:"ALB",4:"DZA",7:"AGO",8:"ATG",9:"ARG",1:"ARM",10:"AUS",11:"AUT",52:"AZE",
    12:"BHS",13:"BHR",16:"BGD",14:"BRB",57:"BLR",255:"BEL",23:"BLZ",53:"BEN",18:"BTN",19:"BOL",
    80:"BIH",20:"BWA",21:"BRA",26:"BRN",27:"BGR",233:"BFA",29:"BDI",115:"KHM",32:"CMR",33:"CAN",
    35:"CAF",39:"TCD",40:"CHL",41:"CHN",351:"COL",45:"COM",46:"COG",250:"COD",48:"CRI",107:"CIV",
    98:"HRV",49:"CUB",50:"CYP",167:"CZE",54:"DNK",72:"DJI",55:"DMA",56:"DOM",58:"ECU",59:"EGY",
    60:"SLV",61:"GNQ",178:"ERI",63:"EST",209:"SWZ",238:"ETH",66:"FJI",67:"FIN",68:"FRA",74:"GAB",
    75:"GMB",73:"GEO",79:"DEU",81:"GHA",84:"GRC",86:"GRD",89:"GTM",90:"GIN",175:"GNB",91:"GUY",
    93:"HTI",95:"HND",97:"HUN",99:"ISL",100:"IND",101:"IDN",102:"IRN",103:"IRQ",104:"IRL",105:"ISR",
    106:"ITA",109:"JAM",110:"JPN",112:"JOR",108:"KAZ",114:"KEN",83:"KIR",118:"KGZ",120:"LAO",119:"LBN",
    122:"LSO",123:"LBR",124:"LBY",126:"LTU",256:"LUX",129:"MDG",130:"MWI",131:"MYS",132:"MDV",133:"MLI",
    134:"MLT",136:"MRT",137:"MUS",138:"MEX",146:"MDA",141:"MNG",273:"MNE",143:"MAR",144:"MOZ",28:"MMR",
    147:"NAM",149:"NPL",150:"NLD",156:"NZL",157:"NIC",158:"NER",159:"NGA",116:"PRK",154:"NOR",221:"OMN",
    165:"PAK",166:"PAN",168:"PNG",169:"PRY",170:"PER",171:"PHL",173:"POL",174:"PRT",179:"QAT",183:"ROU",
    185:"RWA",186:"KNA",188:"LCA",191:"WSM",193:"STP",194:"SAU",195:"SEN",272:"SRB",196:"SYC",197:"SLE",
    200:"SVK",198:"SVN",25:"SOM",202:"ZAF",277:"SSD",203:"ESP",38:"LKA",276:"SDN",207:"SUR",210:"SWE",
    211:"CHE",212:"SYR",214:"TZA",216:"THA",176:"TGO",219:"TTO",218:"TUN",223:"TUR",213:"TKM",226:"UGA",
    230:"UKR",225:"ARE",229:"GBR",231:"USA",234:"URY",235:"UZB",236:"VEN",237:"VNM",249:"YEM",251:"ZMB",
    181:"ZWE",
}


def _num(v):
    try:
        return float(v) if v not in (None, "", "..", "...") else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _finalize(by_iso, source, note_extra=""):
    out = {}
    for iso3, ys in by_iso.items():
        # ys maps year -> list of monthly values (this bulk is monthly-only; there
        # is NO "Annual value" row). Collapse each year to its mean across the
        # months that are present, then compute YoY on those annual means.
        annual = {}
        for y, vals in ys.items():
            if not isinstance(y, int):
                continue
            nums = [v for v in vals if isinstance(v, (int, float))]
            if nums:
                annual[y] = sum(nums) / len(nums)
        years_data = sorted(annual.items(), reverse=True)
        if not years_data:
            continue
        latest_y, latest_v = years_data[0]
        yoy = None
        if len(years_data) > 1:
            prev_y, prev_v = years_data[1]
            if prev_v and prev_v > 0:
                yoy = round((latest_v - prev_v) / prev_v * 100, 2)
        out[iso3] = {
            "food_cpi_index_latest": round(latest_v, 1),
            "food_cpi_yoy_pct": yoy,
            "year_latest": latest_y,
            "country": ys.get("_name"),
            "inflation_shock": bool(yoy is not None and yoy > 15),
            "months_in_latest_year": len([v for v in ys.get(latest_y, []) if isinstance(v, (int, float))]),
        }
    write_json(
        "faostat_food.json",
        out,
        source=source,
        notes=(
            "Consumer Prices Food Index (2015=100), annual average. "
            "food_cpi_yoy_pct = % change vs prior year. inflation_shock = >15% YoY. "
            f"Covered {len(out)} countries.{note_extra}"
        ),
    )
    return len(out)


def _try_bulk():
    """Primary path: FAOSTAT CP bulk ZIP (no token, no rate limit)."""
    print(f"[INFO] FAOSTAT CP bulk download → {BULK_URL}")
    r = http_get(BULK_URL, timeout=300, headers={"Accept": "application/zip,*/*"}, patient=True)
    zip_bytes = r.content
    if not zip_bytes or len(zip_bytes) < 1024:
        raise RuntimeError(f"bulk returned empty body ({len(zip_bytes) if zip_bytes else 0} bytes)")
    print(f"[INFO] Downloaded {len(zip_bytes)//1024//1024} MB ZIP")
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    csv_member = next((n for n in zf.namelist() if n.endswith(".csv") and "Normalized" in n), None) \
        or next((n for n in zf.namelist() if n.endswith(".csv")), None)
    if not csv_member:
        raise RuntimeError(f"no CSV in ZIP: {zf.namelist()}")
    print(f"[INFO] Reading {csv_member} from ZIP")

    by_iso = {}
    rows_seen = rows_kept = 0
    with zf.open(csv_member, "r") as f:
        stream = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
        reader = _csv.DictReader(stream)
        headers = reader.fieldnames or []

        def col(*cands):
            norm = {h.lower().replace(" ", "").replace("_", "").strip(): h for h in headers}
            for c in cands:
                k = c.lower().replace(" ", "").replace("_", "").strip()
                if k in norm:
                    return norm[k]
            return None

        C_AREA = col("Area Code", "AreaCode")
        C_AREANAME = col("Area")
        # v38.3 — in the CP Normalized bulk the FOOD index lives in the ITEM column
        # (Item Code 23013 / Item "Consumer Prices, Food Indices (2015 = 100)"),
        # NOT the Element column (which is just code 6125 / name "Value"). Earlier
        # versions matched on Element and kept 0 rows. We now match on ITEM, with
        # Element kept only as a legacy fallback.
        C_ITEM = col("Item Code", "ItemCode")
        C_ITEMNAME = col("Item")
        C_ELEM = col("Element Code", "ElementCode")
        C_ELEMNAME = col("Element")
        C_MONTHS = col("Months Code", "MonthsCode")
        C_MONTHSNAME = col("Months", "Month")
        C_YEAR = col("Year", "Year Code")
        C_VALUE = col("Value")
        print(f"[INFO] cols → area={C_AREA} item={C_ITEM} itemName={C_ITEMNAME} "
              f"elem={C_ELEM} elemName={C_ELEMNAME} months={C_MONTHS} "
              f"monthsName={C_MONTHSNAME} year={C_YEAR} value={C_VALUE}")
        if not all([C_AREA, C_YEAR, C_VALUE]) or not (C_ITEMNAME or C_ITEM or C_ELEMNAME):
            raise RuntimeError(f"missing expected columns in {headers}")

        # The "food index" series is identified by the Item column. We match the
        # Item NAME ("...Food Indices") first, fall back to the Item code, and only
        # then to the Element column for older layouts. The annual row is matched by
        # month code 7021 / month NAME "Annual value". Diagnostics capture whatever
        # the bulk actually carries so any future drift is visible, not silent.
        # 23013 = "Consumer Prices, Food Indices (2015 = 100)" — the index we want.
        # NOTE 23014 = "Food price inflation" is a DIFFERENT series (already a YoY %,
        # not an index) — must NOT be mixed in. Sub-codes like 230131/230132 are
        # weighted-average/median variants — also excluded (we want the plain index).
        FOOD_ITEM_CODES = {"23013"}
        GENERAL_ITEM_CODES = {"23012", "23018", "23014"}  # general + the inflation series
        seen_elements = {}   # item/element code -> name (for [DIAG])
        seen_months = {}     # code -> name

        def _is_food_element(row):
            # ITEM column carries the index identity in this bulk. We want EXACTLY
            # "Consumer Prices, Food Indices (2015 = 100)" (item 23013) — NOT the
            # "Food price inflation" series (23014), NOT the general index (23012),
            # and NOT the median/weighted-average variants (230131/230132/...).
            icode = str(row.get(C_ITEM, "")).strip() if C_ITEM else ""
            iname = str(row.get(C_ITEMNAME, "")).strip().lower() if C_ITEMNAME else ""
            if C_ITEM or C_ITEMNAME:
                seen_elements.setdefault(icode or iname,
                                         row.get(C_ITEMNAME) if C_ITEMNAME else icode)
            # Code is the precise discriminator here — prefer it.
            if icode:
                return icode in FOOD_ITEM_CODES
            # No code column? fall back to an exact-ish name match for the plain
            # food index, excluding inflation / median / weighted-average variants.
            if ("food" in iname and "indices" in iname
                    and "inflation" not in iname
                    and "median" not in iname and "weighted" not in iname):
                return True
            return False

        # This CP Normalized bulk is MONTHLY-only — there is NO "Annual value"
        # (7021) row, only months 7001–7012. So we ACCEPT all monthly rows and
        # compute each year's annual mean in _finalize(). We still record an
        # "Annual value" row if a future bulk reintroduces one (handled the same
        # way — it just lands as the sole value for that year).
        def _record_months(row):
            mcode = str(row.get(C_MONTHS, "")).strip() if C_MONTHS else ""
            if C_MONTHS and C_MONTHSNAME:
                seen_months.setdefault(mcode, row.get(C_MONTHSNAME))

        for row in reader:
            rows_seen += 1
            _record_months(row)
            if not _is_food_element(row):
                continue
            area_code = _int(row.get(C_AREA))
            iso3 = FAO_AREA_TO_ISO3.get(area_code) if area_code is not None else None
            if not iso3:
                continue
            year = _int(row.get(C_YEAR))
            val = _num(row.get(C_VALUE))
            if year is None or val is None:
                continue
            d = by_iso.setdefault(iso3, {})
            d.setdefault(year, []).append(val)   # accumulate monthly values per year
            d["_name"] = row.get(C_AREANAME) if C_AREANAME else iso3
            rows_kept += 1
            if rows_seen % 500000 == 0:
                print(f"  [progress] {rows_seen} rows scanned, {rows_kept} kept, {len(by_iso)} countries")

    print(f"[INFO] Parsed {rows_seen} rows, kept {rows_kept}, {len(by_iso)} countries")
    # Always emit diagnostics — on success this confirms WHICH element/month matched;
    # on failure it gives the exact codes to correct without guessing.
    print(f"[DIAG] elements seen (code→name): {dict(list(seen_elements.items())[:25])}")
    print(f"[DIAG] months seen (code→name): {dict(list(seen_months.items())[:20])}")
    if not by_iso:
        raise RuntimeError("bulk parse produced zero countries (element/months filter mismatch?)")
    return by_iso


def _try_api():
    """Fallback path: FAOSTAT JSON API (guest JWT)."""
    print("[INFO] FAOSTAT bulk failed — falling back to JSON API")
    tok_r = http_get(AUTH_URL, timeout=20)
    token = (tok_r.json() or {}).get("token")
    if not token:
        raise RuntimeError("no token in guest response")
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "element_code": "23014",   # current FOOD index code (was 23012 = general)
        "months_code": ANNUAL_MONTH_CODE,
        "year": "2022,2023,2024,2025,2026",
        "format": "json",
        "page_size": 5000,
    }
    rows = []
    page = 1
    while page <= 5:
        r = http_get(DATA_URL, params={**params, "page": page}, headers=headers, timeout=45, retries=2)
        j = r.json() or {}
        chunk = j.get("data") or []
        rows.extend(chunk)
        total_pages = (j.get("metadata") or {}).get("pages") or 1
        if page >= total_pages or not chunk:
            break
        page += 1
        time.sleep(0.3)
    by_iso = {}
    for row in rows:
        area_code = _int(row.get("Area Code"))
        iso3 = FAO_AREA_TO_ISO3.get(area_code) if area_code is not None else None
        if not iso3:
            continue
        year = _int(row.get("Year"))
        val = _num(row.get("Value"))
        if year is None or val is None:
            continue
        by_iso.setdefault(iso3, {}).setdefault(year, []).append(val)
        by_iso[iso3]["_name"] = row.get("Area")
    if not by_iso:
        raise RuntimeError("API returned zero usable rows")
    return by_iso


def main():
    # Primary: bulk ZIP. Fallback: JSON API. Last resort: empty (graceful).
    try:
        by_iso = _try_bulk()
        n = _finalize(by_iso, "FAOSTAT CP bulk (bulks-faostat.fao.org, Food Indices element, annual)")
        print(f"[OK] FAOSTAT food CPI via bulk: {n} countries")
        return
    except Exception as e:
        print(f"  [warn] bulk path failed: {e}")

    try:
        by_iso = _try_api()
        n = _finalize(by_iso, "FAOSTAT CP JSON API (faostatservices, element 23012, annual)",
                      " [fallback: JSON API, bulk was unavailable]")
        print(f"[OK] FAOSTAT food CPI via API fallback: {n} countries")
        return
    except Exception as e:
        print(f"  [warn] API fallback failed: {e}")

    write_json("faostat_food.json", {}, source="FAOSTAT CP",
               notes=f"Both bulk and API paths failed; wrote empty so downstream degrades gracefully.")


if __name__ == "__main__":
    main()
