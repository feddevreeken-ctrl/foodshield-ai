"""
FAOSTAT TCL cross-check helper — for trade-data-verify skill.

Pulls net food trade (or per-item import/export value) from the FAOSTAT TCL
bulk normalized ZIP, the independent cross-check for UN Comtrade pulls.

Runs on the user's Mac. Stdlib-only (csv, io, zipfile, urllib) so it needs no
project deps. Defensive against FAO's dual Item-Code columns and item-name
casing drift — the exact issue that broke the project's net_food_trade pipeline.

USAGE:
    python3 faostat_pull.py                       # downloads the ~250MB ZIP, computes net food trade
    python3 faostat_pull.py /path/to/local.zip    # use an already-downloaded ZIP
    python3 faostat_pull.py --items 1001,1006 --iso EGY,NLD   # specific items, specific countries

Output: faostat_verify.json with the project envelope.
"""
import argparse
import csv
import io
import json
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

BULK_URL = "https://bulks-faostat.fao.org/production/Trade_CropsLivestock_E_All_Data_(Normalized).zip"

ELEMENT_IMPORT_VAL = 5622  # Import Value, 1000 USD
ELEMENT_EXPORT_VAL = 5922  # Export Value, 1000 USD
ITEM_FOOD_TOTAL = 1842
ITEM_AGRI_TOTAL = 1841

# Normalized item-name targets (lowercased, punctuation stripped) — primary matcher.
FOOD_NAME_KEYS = {"food total", "food"}
AGRI_NAME_KEYS = {"agricultural products total", "agricultural products"}


def _norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum() or c == " ").replace("  ", " ").strip()


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return float(v) if v not in (None, "", "..") else None
    except (TypeError, ValueError):
        return None


def download(path=None):
    if path:
        print(f"[info] using local ZIP {path}")
        return open(path, "rb").read()
    print(f"[info] downloading {BULK_URL} (~250MB)...")
    req = urllib.request.Request(BULK_URL, headers={"User-Agent": "FoodShield-AI-verify/1"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def find_csv(zf):
    for n in zf.namelist():
        if n.endswith(".csv") and "Normalized" in n:
            return n
    for n in zf.namelist():
        if n.endswith(".csv"):
            return n
    return None


def parse(zip_bytes, want_items=None, want_iso=None):
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    member = find_csv(zf)
    if not member:
        print(f"[fail] no CSV in ZIP: {zf.namelist()}", file=sys.stderr)
        return {}
    print(f"[info] reading {member}")

    by_country = {}
    rows_seen = rows_kept = 0
    with zf.open(member) as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""))
        headers = reader.fieldnames or []
        # Pick the legacy numeric Item Code column, never the CPC string one.
        item_code_col = "Item Code" if "Item Code" in headers else next(
            (h for h in headers if "item code" in h.lower() and "cpc" not in h.lower()), None)
        item_name_col = "Item" if "Item" in headers else next(
            (h for h in headers if h.lower() == "item"), None)
        el_col = next((h for h in headers if _norm(h) == "element code"), None)
        yr_col = next((h for h in headers if _norm(h) == "year"), None)
        val_col = next((h for h in headers if _norm(h) == "value"), None)
        area_col = next((h for h in headers if _norm(h) == "area"), None)
        flag_col = next((h for h in headers if _norm(h) == "flag"), None)

        for row in reader:
            rows_seen += 1
            ec = _int(row.get(el_col))
            if ec not in (ELEMENT_IMPORT_VAL, ELEMENT_EXPORT_VAL):
                continue
            ic = _int(row.get(item_code_col)) if item_code_col else None
            iname = _norm(row.get(item_name_col)) if item_name_col else ""
            if want_items:
                if ic not in want_items:
                    continue
            else:
                if not (ic in (ITEM_FOOD_TOTAL, ITEM_AGRI_TOTAL)
                        or iname in FOOD_NAME_KEYS or iname in AGRI_NAME_KEYS):
                    continue
            flag = (row.get(flag_col) or "").strip() if flag_col else ""
            if flag in ("M", "-"):
                continue
            year = _int(row.get(yr_col))
            val = _num(row.get(val_col))
            if year is None or val is None:
                continue
            area = (row.get(area_col) or "").strip()
            slot = by_country.setdefault(area, {})
            islot = slot.setdefault(ic or iname, {})
            yslot = islot.setdefault(year, {})
            yslot["import_kusd" if ec == ELEMENT_IMPORT_VAL else "export_kusd"] = val
            rows_kept += 1

    print(f"[info] {rows_seen} rows scanned, {rows_kept} kept, {len(by_country)} areas")

    out = {}
    for area, items in by_country.items():
        for item_key, years in items.items():
            for y in sorted(years, reverse=True):
                yd = years[y]
                if "import_kusd" in yd and "export_kusd" in yd:
                    rec = out.setdefault(area, {})
                    rec[str(item_key)] = {
                        "net_musd": round((yd["export_kusd"] - yd["import_kusd"]) / 1000, 1),
                        "imports_musd": round(yd["import_kusd"] / 1000, 1),
                        "exports_musd": round(yd["export_kusd"] / 1000, 1),
                        "year": y, "source": "FAOSTAT TCL", "quality_flag": "sourced", "as_of": y,
                    }
                    break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", nargs="?", default=None, help="local ZIP path (optional)")
    ap.add_argument("--items", default=None, help="comma HS/item codes; default food+agri totals")
    ap.add_argument("--iso", default=None, help="(informational) ISO3 filter note")
    ap.add_argument("--out", default="faostat_verify.json")
    args = ap.parse_args()

    want_items = {int(x) for x in args.items.split(",")} if args.items else None
    data = parse(download(args.zip), want_items=want_items)
    envelope = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "FAOSTAT TCL bulk (Trade_CropsLivestock_E_All_Data_(Normalized).zip)",
            "notes": ("Independent cross-check for Comtrade pulls. Values in millions USD. "
                      "Keyed by FAOSTAT Area name; map to ISO3 before comparing to FoodShield."),
            "version": "verify-1",
        },
        "data": data,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(envelope, f, indent=2, ensure_ascii=False)
    print(f"[ok] wrote {args.out}: {len(data)} areas")


if __name__ == "__main__":
    main()
