#!/usr/bin/env python3
"""Calibrate the "strained" lines of the Prices-lens access ledger (hand-run builder).

Output: data/ref/access_thresholds.json (committed). Needs numpy and network; not on the cron.

Question: which levels of import dependence, economic access, grain reserve (FDRS components,
0 to 100, higher = more strained) and IPC Phase 3+ share separated countries that then entered an
acute food-price shock (or saw food insecurity worsen) from those that did not?

Design (point in time, no current cross-section):
  Panel     country x year P, 2005..2024, countries that were NOT already in a shock in year P.
  Outcome   ENTERED a food-price shock: the peak monthly year-on-year food CPI inflation (IMF CPI, COICOP CP01)
            in year P+1 is >= T (primary T = 20 %), while year P's peak was < T. Sensitivity T = 15, 25, 30.
  Predictor measured at P or earlier, reconstructed with the SAME formulas as scripts/build_countries_dataset.py:
    import dependence = USDA PSD cereal imports / domestic consumption (wheat, rice, corn), market year P-1, 0 to 100
    grain reserve     = PSD ending stocks / consumption, (0.40 - s/u) / 0.35 x 100, market year P-1
    economic access   = blend of FX depreciation (WDI PA.NUS.FCRF, year P vs P-1), reserves in months of imports,
                        debt service (% exports), GNI per capita (WDI Atlas, not the PPP series the page uses)
  Evaluation per component: ROC AUC (Mann-Whitney), country-clustered bootstrap CI, and for candidate lines
  30..80: recall, precision, flagged share, lift, Youden J. Chosen line = argmax J on the 5-point grid,
  reported with the bootstrap spread of that argmax. A line is "supported" only if the AUC CI excludes 0.5.
  IPC: HAPI (HDX) IPC Phase 3+ share per country and analysis, 2021 onward, the only public IPC history.
  Worsening = the next "current" analysis 5 to 19 months later has a Phase 3+ share >= 5 points higher.
Limits are written to _meta.limits. Nothing here is typed by hand except method constants at the top.
"""
import base64, csv, io, json, math, os, sys, time, urllib.request, zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
CACHE = Path(os.environ.get("FOODSHIELD_CACHE", Path.home() / ".cache" / "foodshield"))
CACHE.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "data" / "ref" / "access_thresholds.json"

T_PRIMARY, T_SENS = 20.0, (15.0, 25.0, 30.0)
GRID = list(range(30, 85, 5))
Y0, Y1 = 2005, 2024
NBOOT = 1000
RNG = np.random.default_rng(20260930)

FFPI_URL = ("https://www.fao.org/media/docs/worldfoodsituationlibraries/default-document-library/"
            "food_price_indices_data.csv?sfvrsn=523ebd2a_83&download=true")
GLOBAL_SHOCK_PCT = 10.0  # annual-mean FAO Food Price Index up >= this vs the year before = a global price-shock year
PSD_URL = "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip"
IMF_URL = ("https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/CPI/+/*.CPI.CP01.YOY_PCH_PA_PT.M"
           "?c%5BTIME_PERIOD%5D=ge:2004-01")
WDI = "https://api.worldbank.org/v2/country/all/indicator/{}?format=json&per_page=20000&date=2003:2025"
HAPI = ("https://hapi.humdata.org/api/v2/food-security-nutrition-poverty/food-security?app_identifier={}"
        "&output_format=json&limit=10000&admin_level=0&offset={}")


def fetch(url, name, headers=None, binary=False):
    p = CACHE / name
    if p.exists() and p.stat().st_size > 1000:
        return p.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "FoodShield-AI builder", **(headers or {})})
    b = urllib.request.urlopen(req, timeout=240).read()
    p.write_bytes(b)
    return b


# ---------------------------------------------------------------- component formulas (mirror build_countries_dataset)
def clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def fx_sub(d):
    return None if d is None else clip((d - 5) / 35 * 100)


def res_sub(m):
    if m is None: return None
    if m >= 12: return 0.0
    if m <= 1: return 100.0
    return clip(((12 - m) / 11) ** 1.6 * 100)


def debt_sub(d):
    return None if d is None else clip((d - 5) / 30 * 100)


def inc_sub(g):
    if g is None: return None
    g = clip(g, 1000, 40000)
    return clip(100 * (math.log10(40000) - math.log10(g)) / (math.log10(40000) - math.log10(1000)))


ISSUERS = {"USA", "JPN", "GBR", "CHE", "DEU", "FRA", "ITA", "ESP", "NLD", "BEL", "AUT", "IRL", "PRT", "GRC", "FIN", "LUX",
           "SVK", "SVN", "EST", "LVA", "LTU", "CYP", "MLT", "HRV"}


def econ_access(iso, fx_d, res_m, debt, gni):
    fx_v = fx_sub(fx_d)
    if fx_v is not None and fx_v <= 0: fx_v = None
    res_v = None if (iso in ISSUERS or (gni is not None and gni >= 14000)) else res_sub(res_m)
    subs = [(fx_v, .30), (res_v, .30), (debt_sub(debt), .25), (inc_sub(gni), .15)]
    pres = [(v, w) for v, w in subs if v is not None]
    if not pres: return None
    if len(pres) == 1 and res_v is not None: return None
    return sum(v * w for v, w in pres) / sum(w for _, w in pres)


# ---------------------------------------------------------------- data loaders
def load_psd():
    import refresh_usda_psd as psd
    z = zipfile.ZipFile(io.BytesIO(fetch(PSD_URL, "psd_grains_pulses_csv.zip")))
    rd = csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8"))
    keep = {"wheat": ("0410000", "410000"), "rice": ("0422110", "422110"), "corn": ("0440000", "440000")}
    code2k = {c: k for k, cs in keep.items() for c in cs}
    want = {"Imports": "imp", "Domestic Consumption": "con", "Ending Stocks": "stk"}
    acc = defaultdict(lambda: defaultdict(float))  # (iso, my) -> {imp, con, stk}
    seen = defaultdict(set)
    for r in rd:
        k = code2k.get(r["Commodity_Code"]); a = want.get(r["Attribute_Description"])
        if not k or not a: continue
        iso = psd.FAS_TO_ISO3.get(r["Country_Code"]) or psd.NAME_TO_ISO3.get(r["Country_Name"])
        if not iso: continue
        try: my = int(r["Market_Year"]); v = float(r["Value"])
        except ValueError: continue
        acc[(iso, my)][a] += v; seen[(iso, my)].add((k, a))
    out = {}
    for key, d in acc.items():
        if d["con"] > 0:
            out[key] = {"imp": clip(d["imp"] / d["con"] * 100), "buf": clip((0.40 - d["stk"] / d["con"]) / 0.35 * 100)}
    return out


def load_imf():
    txt = fetch(IMF_URL, "imf_cp01_yoy_2004.csv", headers={"Accept": "application/vnd.sdmx.data+csv;version=2.0.0"}).decode()
    peak = defaultdict(lambda: defaultdict(list))  # iso -> year -> [values]
    for r in csv.DictReader(io.StringIO(txt)):
        try: y, m = r["TIME_PERIOD"].split("-M"); v = float(r["OBS_VALUE"])
        except (ValueError, KeyError): continue
        peak[r["COUNTRY"].replace("WBG", "PSE").replace("KOS", "XKX")][int(y)].append((int(m), v))
    return peak


def global_shock_years():
    txt = fetch(FFPI_URL, "fao_ffpi_history.csv").decode("utf-8", "replace")
    by = defaultdict(list)
    for r in csv.reader(io.StringIO(txt)):
        if len(r) > 1 and len(r[0]) == 7 and r[0][4] == "-":
            try: by[int(r[0][:4])].append(float(r[1]))
            except ValueError: pass
    ann = {y: sum(v) / len(v) for y, v in by.items() if len(v) == 12}
    return {y: round((ann[y] / ann[y - 1] - 1) * 100, 1) for y in ann if y - 1 in ann}


def load_wdi(ind):
    d = json.loads(fetch(WDI.format(ind), f"wdi_{ind}.json"))[1]
    out = defaultdict(dict)
    for r in d:
        if r["value"] is not None and len(r["countryiso3code"]) == 3:
            out[r["countryiso3code"]][int(r["date"])] = float(r["value"])
    return out


def load_hapi():
    app = base64.b64encode(b"foodshield:fedde.vreeken@gmail.com").decode()
    rows, off = [], 0
    p = CACHE / "hapi_food_security_adm0.json"
    if p.exists():
        return json.loads(p.read_text())
    while True:
        b = json.loads(urllib.request.urlopen(HAPI.format(app, off), timeout=240).read())["data"]
        rows += b
        if len(b) < 10000: break
        off += 10000
    p.write_text(json.dumps(rows))
    return rows


# ---------------------------------------------------------------- statistics
def auc(x, y):
    x = np.asarray(x, float); y = np.asarray(y, bool)
    n1, n0 = y.sum(), (~y).sum()
    if n1 == 0 or n0 == 0: return None
    order = np.argsort(x, kind="mergesort"); xs = x[order]
    ranks = np.empty(len(x)); i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and xs[j + 1] == xs[i]: j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1; i = j + 1
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def line_stats(x, y, line):
    x = np.asarray(x, float); y = np.asarray(y, bool); f = x >= line
    tp = int((f & y).sum()); fp = int((f & ~y).sum()); fn = int((~f & y).sum()); tn = int((~f & ~y).sum())
    rec = tp / (tp + fn) if tp + fn else None
    fpr = fp / (fp + tn) if fp + tn else None
    prec = tp / (tp + fp) if tp + fp else None
    base = y.mean()
    return {"line": line, "flagged": int(f.sum()), "flagged_share": round(float(f.mean()), 3), "tp": tp, "fp": fp,
            "recall": None if rec is None else round(rec, 3), "precision": None if prec is None else round(prec, 3),
            "lift": None if prec is None or base == 0 else round(prec / base, 2),
            "youden_j": None if rec is None or fpr is None else round(rec - fpr, 3)}


def best_line(x, y, grid):
    bs = [(line_stats(x, y, g)["youden_j"], g) for g in grid]
    bs = [b for b in bs if b[0] is not None]
    return max(bs)[1] if bs else None


def boot(x, y, groups, grid):
    x = np.asarray(x, float); y = np.asarray(y, bool); g = np.asarray(groups)
    ug = np.unique(g); idx = {k: np.where(g == k)[0] for k in ug}
    aucs, lines = [], []
    for _ in range(NBOOT):
        pick = RNG.choice(ug, len(ug))
        ii = np.concatenate([idx[k] for k in pick])
        a = auc(x[ii], y[ii])
        if a is not None:
            aucs.append(a); lines.append(best_line(x[ii], y[ii], grid))
    lines = [l for l in lines if l is not None]
    return {"auc_ci95": [round(float(np.percentile(aucs, 2.5)), 3), round(float(np.percentile(aucs, 97.5)), 3)],
            "line_p25_p50_p75": [float(np.percentile(lines, q)) for q in (25, 50, 75)],
            "line_share_at_modal": round(float(np.mean(np.array(lines) == max(set(lines), key=lines.count))), 2)}


def evaluate(x, y, groups, grid, label):
    x = np.asarray(x, float); y = np.asarray(y, bool)
    a = auc(x, y)
    b = boot(x, y, groups, grid)
    ch = best_line(x, y, grid)
    return {"component": label, "n": int(len(x)), "events": int(y.sum()), "base_rate": round(float(y.mean()), 3),
            "countries": int(len(set(groups))), "auc": None if a is None else round(a, 3), **b,
            "supported": bool(b["auc_ci95"][0] > 0.5), "best_line_youden": ch,
            "at_lines": [line_stats(x, y, g) for g in grid],
            "percentiles_of_non_events": {str(q): round(float(np.percentile(x[~y], q)), 1) for q in (50, 75, 90)},
            "percentiles_of_events": {str(q): round(float(np.percentile(x[y], q)), 1) for q in (25, 50, 75)}}


# ---------------------------------------------------------------- price-shock panel
def price_panel(T):
    psd, imf = load_psd(), load_imf()
    res, debt, gni, fx = (load_wdi(i) for i in ("FI.RES.TOTL.MO", "DT.TDS.DECT.EX.ZS", "NY.GNP.PCAP.CD", "PA.NUS.FCRF"))
    rows = []
    for iso, yrs in imf.items():
        for P in range(Y0, Y1 + 1):
            a, b = yrs.get(P), yrs.get(P + 1)
            if not a or not b or len(a) < 9 or len(b) < 9: continue
            pk_now, pk_next = max(v for _, v in a), max(v for _, v in b)
            if pk_now >= T: continue  # already in a shock: not an entry
            p = psd.get((iso, P - 1))
            fxd = None
            if P in fx.get(iso, {}) and P - 1 in fx.get(iso, {}) and fx[iso][P - 1] > 0:
                fxd = (fx[iso][P] / fx[iso][P - 1] - 1) * 100 / (1 + (fx[iso][P] / fx[iso][P - 1] - 1))  # % loss of value vs USD
            ea = econ_access(iso, fxd, res.get(iso, {}).get(P), debt.get(iso, {}).get(P), gni.get(iso, {}).get(P))
            rows.append({"iso": iso, "P": P, "y": pk_next >= T, "imp": p["imp"] if p else None,
                         "buf": p["buf"] if p else None, "acc": ea})
    return rows, psd


def component_block(rows):
    out = {}
    for key, label in (("imp", "import_dependence"), ("acc", "economic_access"), ("buf", "grain_reserve")):
        r = [q for q in rows if q[key] is not None]
        out[label] = evaluate([q[key] for q in r], [q["y"] for q in r], [q["iso"] for q in r], GRID, label)
    # count of components over the candidate line, on country-years with all three
    full = [q for q in rows if None not in (q["imp"], q["acc"], q["buf"])]
    cnt = {}
    for L in (50, 60, 70):
        k = np.array([(q["imp"] >= L) + (q["acc"] >= L) + (q["buf"] >= L) for q in full])
        y = np.array([q["y"] for q in full])
        cnt[str(L)] = {"n": len(full), "base_rate": round(float(y.mean()), 3),
                       "by_count": {str(c): {"country_years": int((k == c).sum()), "event_rate": None if (k == c).sum() == 0 else round(float(y[k == c].mean()), 3)} for c in range(4)}}
    out["count_over_line_all_three"] = cnt
    return out


# ---------------------------------------------------------------- IPC history
def ipc_block():
    rows = load_hapi()
    by = defaultdict(dict)
    for r in rows:
        if r["ipc_type"] != "current": continue
        k = (r["location_code"], r["reference_period_start"][:10])
        by[k][r["ipc_phase"]] = r
    series = defaultdict(list)
    for (iso, start), d in by.items():
        if "3+" in d and "all" in d and d["all"]["population_in_phase"]:
            sh = 100 * d["3+"]["population_in_phase"] / d["all"]["population_in_phase"]
            series[iso].append((datetime.fromisoformat(start), sh, d["3+"]["population_in_phase"], d["all"]["population_in_phase"]))
    pairs = []
    for iso, s in series.items():
        s.sort()
        for i, (t0, sh0, _, _) in enumerate(s):
            nxt = [q for q in s[i + 1:] if 5 <= (q[0].year - t0.year) * 12 + q[0].month - t0.month <= 19]
            if nxt:
                q = nxt[0]; pairs.append({"iso": iso, "share": sh0, "next": q[1], "d": q[1] - sh0})
    if not pairs: return None
    x = [p["share"] for p in pairs]; g = [p["iso"] for p in pairs]
    out = {"n_analyses": int(sum(len(v) for v in series.values())), "countries": int(len(series)), "pairs": len(pairs),
           "period_span": [min(q[0] for v in series.values() for q in v).strftime("%Y-%m"),
                           max(q[0] for v in series.values() for q in v).strftime("%Y-%m")]}
    grid = [5, 10, 15, 20, 25, 30, 35, 40]
    y1 = [p["d"] >= 5 for p in pairs]
    out["worsening_ge5pp"] = evaluate(x, y1, g, grid, "ipc_share_vs_worsening_ge5pp")
    # bins of the current share, next-analysis outcome
    bins = []
    for lo, hi in ((0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 100.1)):
        s = [p for p in pairs if lo <= p["share"] < hi]
        if s:
            bins.append({"share_from": lo, "share_to": min(hi, 100), "pairs": len(s), "countries": len({p["iso"] for p in s}),
                         "worsened_ge5pp": round(float(np.mean([p["d"] >= 5 for p in s])), 3),
                         "median_change_pp": round(float(np.median([p["d"] for p in s])), 1),
                         "next_share_ge20": round(float(np.mean([p["next"] >= 20 for p in s])), 3)})
    out["bins"] = bins
    # entry into the IPC area-Crisis convention (>= 20 % in Phase 3+) from below it
    below = [p for p in pairs if p["share"] < 20]
    if below:
        out["entry_to_20pct_from_below"] = evaluate([p["share"] for p in below], [p["next"] >= 20 for p in below],
                                                    [p["iso"] for p in below], [5, 10, 15], "ipc_share_vs_entry_to_20pct")  # grid stops below the 20 % outcome line
    out["_latest_current"] = {iso: {"share_pct": round(v[-1][1], 1), "p3plus": int(v[-1][2]), "analysed": int(v[-1][3]),
                                    "period_start": v[-1][0].strftime("%Y-%m")} for iso, v in series.items()}
    return out


# ---------------------------------------------------------------- reconstruction check vs the page's current components
def recon_check(psd):
    cur = json.loads((ROOT / "data" / "countries.json").read_text())["data"]
    cur = cur if "ABW" in cur else next(iter(cur.values()))
    res, debt, gni, fx = (load_wdi(i) for i in ("FI.RES.TOTL.MO", "DT.TDS.DECT.EX.ZS", "NY.GNP.PCAP.CD", "PA.NUS.FCRF"))
    a, b, c, d, e, f = [], [], [], [], [], []
    from scipy.stats import spearmanr
    for iso, row in cur.items():
        comp = (row.get("c") or {}).get("value")
        if not comp or len(comp) < 9: continue
        p = psd.get((iso, 2023))
        if p and comp[0] is not None: a.append(comp[0]); b.append(p["imp"])
        if p and comp[8] is not None: c.append(comp[8]); d.append(p["buf"])
        P = 2023
        fxd = None
        if P in fx.get(iso, {}) and P - 1 in fx.get(iso, {}) and fx[iso][P - 1] > 0:
            r_ = fx[iso][P] / fx[iso][P - 1] - 1; fxd = r_ * 100 / (1 + r_)
        ea = econ_access(iso, fxd, res.get(iso, {}).get(P), debt.get(iso, {}).get(P), gni.get(iso, {}).get(P))
        if ea is not None and comp[7] is not None: e.append(comp[7]); f.append(ea)
    sp = lambda u, v: [int(len(u)), round(float(spearmanr(u, v)[0]), 3)]
    return {"note": "Spearman rho of the reconstructed 2023 value against the page's current component (n, rho)",
            "import_dependence_PSD_vs_FAOSTAT_c0": sp(a, b), "grain_reserve_PSD2023_vs_c8": sp(c, d),
            "economic_access_reconstructed_vs_c7": sp(e, f)}


def main():
    t0 = time.time()
    rows, psd = price_panel(T_PRIMARY)
    primary = component_block(rows)
    ffpi = global_shock_years()
    hot = {y for y, v in ffpi.items() if v >= GLOBAL_SHOCK_PCT}
    cond = {}
    for name, sel in (("outcome_year_is_global_price_shock_year", [q for q in rows if q["P"] + 1 in hot]),
                      ("outcome_year_is_not", [q for q in rows if q["P"] + 1 not in hot])):
        cond[name] = {k: v for k, v in component_block(sel).items() if k != "count_over_line_all_three"}
        for v in cond[name].values(): v.pop("at_lines", None)
    sens = {}
    for T in T_SENS:
        r2, _ = price_panel(T)
        blk = {}
        for key, label in (("imp", "import_dependence"), ("acc", "economic_access"), ("buf", "grain_reserve")):
            q = [z for z in r2 if z[key] is not None]
            blk[label] = {"n": len(q), "events": int(sum(z["y"] for z in q)), "auc": round(auc([z[key] for z in q], [z["y"] for z in q]), 3),
                          "best_line_youden": best_line([z[key] for z in q], [z["y"] for z in q], GRID)}
        sens[f"T{int(T)}"] = blk
    weak = {}
    for lo in (40, 50):
        sub = [q for q in rows if q["imp"] is not None and q["acc"] is not None and q["acc"] >= lo]
        weak[f"economic_access_ge_{lo}"] = {"n": len(sub), "events": int(sum(q["y"] for q in sub)),
                                            "auc_import_dependence": round(auc([q["imp"] for q in sub], [q["y"] for q in sub]), 3),
                                            "auc_grain_reserve": round(auc([q["buf"] for q in sub], [q["y"] for q in sub]), 3)}
    ipc = ipc_block()
    chk = recon_check(psd)
    out = {"_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "builder": "scripts/build_access_thresholds.py (hand-run, numpy+scipy, output committed; not on the cron)",
        "sources": {
            "food_price_shock_outcome": {"name": "IMF CPI, food and non-alcoholic beverages (COICOP CP01), year-on-year %, monthly", "url": "https://data.imf.org/en/datasets/IMF.STA:CPI", "api": IMF_URL, "licence": "IMF data terms, free reuse with attribution"},
            "import_dependence_and_reserve": {"name": "USDA FAS PSD Online, grains (wheat, rice, corn): imports, domestic consumption, ending stocks, by market year", "url": PSD_URL, "licence": "US Government public domain"},
            "economic_access": {"name": "World Bank WDI: FI.RES.TOTL.MO, DT.TDS.DECT.EX.ZS, NY.GNP.PCAP.CD, PA.NUS.FCRF", "url": "https://api.worldbank.org/v2/", "licence": "CC BY 4.0"},
            "ipc_history": {"name": "HDX HAPI food security (IPC/CH population in phase, national level), 2021 onward", "url": "https://hapi.humdata.org/docs", "licence": "CC BY-IGO via HDX"},
        },
        "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "method": {
            "panel": f"country-years P={Y0}..{Y1} not already in a shock in year P; predictors measured in/before P (PSD market year P-1, WDI year P); outcome in year P+1",
            "outcome": f"entered a food-price shock: peak monthly y/y food CPI in P+1 >= {T_PRIMARY:g} % while the peak in P was below it (nominal). Sensitivity T = {[int(t) for t in T_SENS]}.",
            "components": "same formulas as scripts/build_countries_dataset.py (import dependence, grain buffer, economic access) rebuilt from historical inputs",
            "line_choice": "argmax of Youden J (recall minus false-positive rate) on the 5-point grid 30..80; a line is 'supported' only if the country-clustered bootstrap 95% CI of the AUC excludes 0.5",
            "bootstrap": f"{NBOOT} country-clustered resamples, seed 20260930",
        },
        "episodes_note": "Episodes are all country-years meeting the outcome rule in the IMF series, not hand-picked; their list is in `episodes`.",
        "limits": [
            "Import dependence and grain reserve use USDA PSD grain balances, the page's c[0] uses FAOSTAT food balance sheets; the reconstruction check gives the rank correlation.",
            "Economic access is rebuilt from annual WDI series with Atlas GNI per capita (the page uses PPP GNI from UNDP) and an annual-average exchange-rate change, so it is an analogue, not the identical number.",
            "Supplier concentration, production trend, climate, conflict and supply-chain components are not calibrated here.",
            "Outcome is nominal food CPI; in very high-inflation economies a 20 % peak is not necessarily a shock in real terms, and IMF CPI covers fewer low-income countries than the page lists.",
            "Episodes cluster in 2008, 2011 and 2021 to 2023 (global price shocks); the result is about which countries were hit when prices rose, not a forecast.",
            "IPC history on HAPI starts in 2021 and covers about 50 countries that run IPC/CH analyses, so the IPC line rests on few countries and is weaker evidence than the price-shock lines.",
        ],
        "reconstruction_check": chk,
    },
        "price_shock_primary": primary,
        "price_shock_sensitivity": sens,
        "import_and_reserve_among_weak_access": weak,
        "price_shock_by_global_regime": {"global_shock_years": sorted(y for y in hot if y >= Y0 + 1), "rule": f"annual-mean FAO Food Price Index up >= {GLOBAL_SHOCK_PCT:g} % on the year before",
                                          "ffpi_annual_change_pct": {str(y): ffpi[y] for y in sorted(ffpi) if 2005 <= y <= 2025}, "blocks": cond},
        "ipc_history": ipc,
        "episodes": sorted([{"iso": q["iso"], "year_entered": q["P"] + 1} for q in rows if q["y"]], key=lambda e: (e["year_entered"], e["iso"])),
    }
    out["outcome"] = {"price_shock_threshold_pct": T_PRIMARY, "panel_years": [Y0, Y1], "ipc_entry_threshold_pct": 20}
    # the lines the page reads
    def high(e, min_tp):
        c = [a["line"] for a in e["at_lines"] if a["lift"] and a["lift"] >= 2 and a["tp"] >= min_tp]
        return min(c) if c and e["supported"] else None

    def pick(label):
        e = primary[label]
        return {"line": e["best_line_youden"] if e["supported"] else None, "high_line": high(e, 15),
                "flagged_share_at_line": next((a["flagged_share"] for a in e["at_lines"] if a["line"] == e["best_line_youden"]), None),
                "lift_at_line": next((a["lift"] for a in e["at_lines"] if a["line"] == e["best_line_youden"]), None), "auc": e["auc"], "auc_ci95": e["auc_ci95"],
                "supported": e["supported"], "n": e["n"], "events": e["events"], "countries": e["countries"]}
    out["lines"] = {"import_dependence": pick("import_dependence"), "economic_access": pick("economic_access"), "grain_reserve": pick("grain_reserve")}
    ip_line = None
    if ipc:
        e = ipc["entry_to_20pct_from_below"]
        out["lines"]["ipc_phase3plus_pct"] = {"line": e["best_line_youden"] if e["supported"] else None, "high_line": high(e, 10),
                                              "outcome": "next IPC analysis 5 to 19 months later has Phase 3+ share >= 20 % (the IPC area Crisis convention), from analyses below 20 %",
                                              "worsening_ge5pp_auc": ipc["worsening_ge5pp"]["auc"], "worsening_ge5pp_auc_ci95": ipc["worsening_ge5pp"]["auc_ci95"],
                                              "lift_at_line": next((a["lift"] for a in e["at_lines"] if a["line"] == e["best_line_youden"]), None),
                                              "flagged_share_at_line": next((a["flagged_share"] for a in e["at_lines"] if a["line"] == e["best_line_youden"]), None), "auc": e["auc"], "auc_ci95": e["auc_ci95"],
                                              "supported": e["supported"], "n": e["n"], "events": e["events"], "countries": e["countries"]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    meta = out.pop("_meta")
    OUT.write_text(json.dumps({"_meta": meta, "data": out}, indent=1, ensure_ascii=False))
    from pipeline_dag import stamp_file; stamp_file("ref/access_thresholds.json")
    print(f"wrote {OUT} in {time.time() - t0:.0f}s")
    print(json.dumps(out["lines"], indent=1)); print(json.dumps(chk, indent=1))


if __name__ == "__main__":
    main()
