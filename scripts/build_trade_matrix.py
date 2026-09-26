#!/usr/bin/env python3
"""Measured bilateral food-trade flows from the FAOSTAT Detailed Trade Matrix.

Why: the map's "Trade flows" layer drew a hand-curated atlas (commodity_flows.json,
June 2026) in which each corridor was typed in from a report or a Comtrade lookup.
FAOSTAT publishes the whole matrix: every reporter x partner x item, in tonnes,
with the importer's and the exporter's declarations side by side. This builds the
layer from that instead.

Method, per commodity (a list of FAO items summed on a product-weight basis):
  * quantity of a corridor exporter->importer = the IMPORTER's reported import
    quantity (element 5610). Where the importer did not report that year, the
    EXPORTER's reported export quantity to it (5910) is used and flagged
    basis="mirror". Importer declarations are preferred because customs record
    imports more completely (they are taxed).
  * the year is the latest one whose reported world total is at least 85% of the
    year before, so a half-filled final year is not mistaken for a trade collapse.
  * the top 300 corridors are kept, with each corridor's share of the importer's
    total imports of that commodity and of world trade.

Run by hand (annual source, 420 MB download); not part of the 6-hourly cron:
    python3 scripts/build_trade_matrix.py
Cache: $FOODSHIELD_CACHE or ~/.cache/foodshield.
"""
import csv
import io
import json
import os
import sys
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import write_json  # noqa: E402

BULK_URL = "https://bulks-faostat.fao.org/production/Trade_DetailedTradeMatrix_E_All_Data_(Normalized).zip"
REF_URL = "https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json"
CACHE = Path(os.environ.get("FOODSHIELD_CACHE") or Path.home() / ".cache" / "foodshield")
ZIP_PATH = CACHE / "Trade_DetailedTradeMatrix_E_All_Data_Normalized.zip"
REF_PATH = CACHE / "partners.json"
TOP_N = 300
MIN_YEAR = 2019

# atlas dataKey -> (label, FAO item codes summed, basis note)
ITEMS = {
    "wheat":        ("Wheat", [15], "grain"),
    "maize":        ("Maize", [56], "grain"),
    "rice":         ("Rice", [28, 29, 31, 32], "husked, milled and broken rice, product weight"),
    "soybeans":     ("Soybeans", [236], "beans"),
    "barley":       ("Barley", [44], "grain"),
    "sorghum":      ("Sorghum", [83], "grain"),
    "palmoil":      ("Palm oil", [257], "crude and refined palm oil"),
    "vegoils":      ("Vegetable oils", [237, 268, 271], "soybean, sunflower and rapeseed oil"),
    "sugar":        ("Sugar", [162, 164], "raw and refined sugar"),
    "coffee":       ("Coffee", [656], "green coffee"),
    "cocoa":        ("Cocoa", [661], "cocoa beans"),
    "tea":          ("Tea", [667], "tea leaves"),
    "bananas":      ("Bananas", [486], "fresh bananas"),
    "beef":         ("Beef", [867, 870], "bovine meat, product weight"),
    "poultry":      ("Poultry", [1058], "chicken meat"),
    "pork":         ("Pork", [1035, 1038], "pig meat"),
    "dairy":        ("Dairy", [886, 897, 898, 901, 904], "butter, cheese, milk and whey powders, product weight"),
    "pulses":       ("Pulses", [176, 181, 187, 191, 195, 197, 201, 211], "dry pulses"),
    "potatoes":     ("Potatoes", [116], "fresh potatoes"),
    "frozenpotato": ("Frozen potatoes", [118], "frozen potato products"),
    "groundnuts":   ("Groundnuts", [242, 243], "in shell and shelled"),
    "sesame":       ("Sesame", [289], "sesame seed"),
    "oliveoil":     ("Olive oil", [261], "olive oil"),
    "citrus":       ("Citrus", [490, 497, 507, 512], "oranges, lemons, limes, grapefruit"),
    "apples":       ("Apples", [515], "fresh apples"),
    "grapes":       ("Grapes", [560], "fresh grapes"),
    "wine":         ("Wine", [564], "wine"),
    "eggs":         ("Eggs", [1062], "hen eggs in shell"),
    "nuts":         ("Tree nuts", [217, 221, 222, 225, 230, 231, 232, 233], "cashew, almond, walnut, hazelnut"),
    "sheepgoat":    ("Sheep and goat meat", [977, 1017], "sheep and goat meat"),
    "feedcake":     ("Oilseed cake", [238, 269, 272], "soybean, sunflower and rapeseed cake"),
    "spices":       ("Spices", [687, 689, 702], "pepper, chillies, nutmeg and cardamom"),
    "vegetables":   ("Vegetables", [388, 403, 463], "tomatoes, onions and other fresh vegetables"),
    "roots":        ("Cassava and roots", [122, 125, 128, 137], "cassava, sweet potato, yam"),
    "offal":        ("Offal", [868], "edible bovine offal"),
    "procgrains":   ("Flour", [16, 58], "wheat and maize flour"),
}
IMPORTER_KEYS = ["wheat", "maize", "rice", "soybeans", "barley", "sorghum", "vegoils", "palmoil",
                 "sugar", "pulses", "poultry", "beef", "dairy"]
ITEM_TO_KEY = {code: key for key, (_, codes, _) in ITEMS.items() for code in codes}
IMPORT_EL, EXPORT_EL = "5610", "5910"
# FAO aggregates that would double count their members
SKIP_M49 = {"159", "097"}  # China (incl. HK/Macao/Taiwan), EU-27


def ensure_inputs():
    CACHE.mkdir(parents=True, exist_ok=True)
    if not ZIP_PATH.exists():
        print(f"[get] {BULK_URL}")
        tmp = ZIP_PATH.with_suffix(".part")
        urllib.request.urlretrieve(BULK_URL, tmp)
        tmp.rename(ZIP_PATH)
    if not REF_PATH.exists():
        urllib.request.urlretrieve(REF_URL, REF_PATH)


def m49_table():
    rows = json.loads(REF_PATH.read_text())["results"]
    out = {}
    for r in rows:
        iso = r.get("PartnerCodeIsoAlpha3") or ""
        if len(iso) == 3 and iso.isalpha() and not r.get("isGroup"):
            out[str(r["PartnerCode"]).zfill(3)] = iso
    out["156"] = "CHN"  # FAO "China, mainland"
    out["158"] = "TWN"
    return out


def read_matrix(m49):
    """-> vals[key][year][(exporter, importer)] = {'imp': t, 'exp': t}, and
    exp_reporters[key][year] = countries that filed export declarations."""
    vals = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    exp_reporters = defaultdict(lambda: defaultdict(set))
    unmapped = defaultdict(int)
    z = zipfile.ZipFile(ZIP_PATH)
    name = next(i.filename for i in z.infolist() if "All_Data" in i.filename and i.filename.endswith(".csv"))
    reader = csv.reader(io.TextIOWrapper(z.open(name), encoding="latin-1"))
    h = next(reader)
    ix = {k: h.index(k) for k in ("Reporter Country Code (M49)", "Partner Country Code (M49)",
                                  "Item Code", "Element Code", "Year", "Unit", "Value")}
    n = 0
    for row in reader:
        n += 1
        if n % 10_000_000 == 0:
            print(f"  {n // 1_000_000}M rows", flush=True)
        el = row[ix["Element Code"]]
        if el != IMPORT_EL and el != EXPORT_EL:
            continue
        key = ITEM_TO_KEY.get(int(row[ix["Item Code"]]))
        if not key:
            continue
        year = int(row[ix["Year"]])
        if year < MIN_YEAR or row[ix["Unit"]] != "t":
            continue
        rep = row[ix["Reporter Country Code (M49)"]].lstrip("'")
        par = row[ix["Partner Country Code (M49)"]].lstrip("'")
        if rep in SKIP_M49 or par in SKIP_M49:
            continue
        ri, pi = m49.get(rep), m49.get(par)
        if not ri or not pi or ri == pi:
            if not ri:
                unmapped[rep] += 1
            if not pi:
                unmapped[par] += 1
            continue
        try:
            v = float(row[ix["Value"]])
        except ValueError:
            continue
        if v <= 0:
            continue
        pair = (pi, ri) if el == IMPORT_EL else (ri, pi)  # (exporter, importer)
        k = "imp" if el == IMPORT_EL else "exp"
        slot = vals[key][year][pair]
        slot[k] = slot.get(k, 0.0) + v
        if el == EXPORT_EL:
            exp_reporters[key][year].add(ri)
    print(f"[read] {n:,} rows; unmapped M49 codes: {dict(sorted(unmapped.items(), key=lambda x: -x[1])[:8])}")
    return vals, exp_reporters


def corridor_values(pairs):
    out = {}
    for pair, d in pairs.items():
        if d.get("imp"):
            out[pair] = (d["imp"], "reported")
        elif d.get("exp"):
            out[pair] = (d["exp"], "mirror")
    return out


def pick_year(by_year):
    years = sorted(by_year)
    totals = {y: sum(v for v, _ in corridor_values(by_year[y]).values()) for y in years}
    best = years[-1]
    for y in reversed(years):
        prev = totals.get(y - 1)
        if prev is None or totals[y] >= 0.85 * prev:
            best = y
            break
    return best, totals


def build():
    ensure_inputs()
    m49 = m49_table()
    vals, exp_reporters = read_matrix(m49)
    out = {}
    for key, (label, codes, basis) in ITEMS.items():
        if key not in vals:
            continue
        year, totals = pick_year(vals[key])
        flows = corridor_values(vals[key][year])
        world = sum(v for v, _ in flows.values())
        imp_tot, exp_tot = defaultdict(float), defaultdict(float)
        for (e, i), (v, _) in flows.items():
            imp_tot[i] += v
            exp_tot[e] += v
        ranked = sorted(flows.items(), key=lambda kv: -kv[1][0])
        top = [{
            "from": e, "to": i, "t": round(v),
            "basis": b,
            "share_of_importer_pct": round(100 * v / imp_tot[i], 1) if imp_tot[i] else None,
            "share_of_world_pct": round(100 * v / world, 2) if world else None,
        } for (e, i), (v, b) in ranked[:TOP_N]]
        # Large exporters that filed no export declarations this year (Russia has published
        # none since 2022): their trade is seen only through importers that report, so it is
        # understated. Named so the page can say so rather than rank them low.
        silent = [e for e, _ in sorted(exp_tot.items(), key=lambda kv: -kv[1])[:25]
                  if e not in exp_reporters[key][year]]
        mirror_share = sum(v for v, b in flows.values() if b == "mirror") / world if world else 0
        out[key] = {
            "label": label, "fao_items": codes, "basis": basis, "year": year,
            "world_t": round(world), "corridors_total": len(flows),
            "mirror_share_pct": round(100 * mirror_share, 1),
            "exporters_not_reporting": silent,
            "top_exporters": [{"iso": k, "t": round(v), "share_pct": round(100 * v / world, 1)}
                              for k, v in sorted(exp_tot.items(), key=lambda kv: -kv[1])[:12]],
            "top_importers": [{"iso": k, "t": round(v), "share_pct": round(100 * v / world, 1)}
                              for k, v in sorted(imp_tot.items(), key=lambda kv: -kv[1])[:12]],
            "flows": top,
            "totals_by_year_t": {str(y): round(t) for y, t in sorted(totals.items())},
        }
        print(f"[ok] {key:12s} {year}  world {world/1e6:8.2f} Mt  corridors {len(flows):5d}  mirror {100*mirror_share:4.1f}%")
    # Per-importer view for the country panel and the Country tab: every importer's top five
    # suppliers of the staples the score and the supplier cards talk about.
    by_importer = {}
    for key in IMPORTER_KEYS:
        if key not in vals:
            continue
        year = out[key]["year"]
        flows = corridor_values(vals[key][year])
        per = defaultdict(list)
        for (e, i), (v, b) in flows.items():
            per[i].append((e, v, b))
        for i, lst in per.items():
            tot = sum(v for _, v, _ in lst)
            lst.sort(key=lambda x: -x[1])
            by_importer.setdefault(i, {})[key] = {
                "year": year, "imports_t": round(tot),
                "suppliers": [{"iso": e, "t": round(v), "share_pct": round(100 * v / tot, 1), "basis": b}
                              for e, v, b in lst[:5]],
            }
    write_json("trade_matrix_importers.json", by_importer,
               source="FAOSTAT Detailed Trade Matrix (TM), bulk normalized download: " + BULK_URL,
               notes=("Per importer and commodity: total imports (t) and the top five suppliers with their share. "
                      "Importer-reported quantity, exporter-reported mirror where the importer did not report (basis). "
                      "Commodities: " + ", ".join(IMPORTER_KEYS) + "."))
    method = ("Corridor tonnes = importer-reported import quantity (FAO element 5610); where the importer did not "
              "report, the exporter-reported export quantity (5910) to it, flagged basis='mirror'. Items summed on "
              "a product-weight basis per commodity. Year = latest with a reported world total >= 85% of the year "
              "before. Top 300 corridors kept per commodity; shares are of the importer's total and of world trade. "
              f"Bulk file last modified {datetime.fromtimestamp(ZIP_PATH.stat().st_mtime, timezone.utc).date()}.")
    write_json("trade_matrix.json", out,
               source="FAOSTAT Detailed Trade Matrix (TM), bulk normalized download: " + BULK_URL,
               notes=method)


if __name__ == "__main__":
    build()
