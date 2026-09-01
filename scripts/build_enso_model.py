#!/usr/bin/env python3
"""
build_enso_model.py — measure each country's crop-yield response to ENSO.

v2. Rebuilt on FAOSTAT after the USDA PSD panel proved unusable; the failure is
recorded in git history and the reason is worth restating, because it is the
single design decision this script exists to get right.

THE PROBLEM THAT KILLED v1: SEASONAL ALIGNMENT
----------------------------------------------
ENSO peaks in DJF. To regress a crop's yield on it you must know WHICH DJF fell
inside that crop's growing season. Choose wrong and the sign inverts.

PSD indexes by MARKETING year, and its labelling is not consistent across
countries:

    South African maize -- plants Nov-Jan, harvests May-Jul  -> PSD labels it by
                           the PLANTING year, so the right index is DJF(t+1)
    Moroccan barley     -- plants Nov-Jan, harvests May-Jul  -> PSD labels it by
                           the HARVEST year, so the right index is DJF(t)

Identical agronomy, opposite answers. A crop calendar cannot resolve that,
because the ambiguity is in PSD's labelling, not in the field. USDA's per-crop
marketing-year table is not machine-retrievable (IPAD is retired; its calendars
survive only as archived images). Selecting the lag by best fit is a
specification search that flipped Australian wheat between -13.7%/ONI and
+13.2%. Splitting the sample to select it honestly discarded 1982/83 and
1991/92 -- the two events carrying most of the signal -- and left a model that
rejected South African maize while admitting Swiss barley.

THE FIX: INDEX BY HARVEST YEAR INSTEAD
--------------------------------------
FAOSTAT QCL is indexed by CALENDAR HARVEST YEAR. That dissolves the ambiguity
entirely, and the alignment reduces to a rule over the harvest month alone:

    harvest Jan-Aug  ->  the season spanned the PRECEDING Dec-Feb  ->  DJF(Y)
    harvest Sep-Dec  ->  the season ran Mar-Dec and the event peaks
                          afterwards, so the developing phase belongs to  DJF(Y+1)

Checked by hand against USDA/FAO-GIEWS calendars, and it reproduces every case:
ZAF maize (May-Jul -> DJF Y), Moroccan barley (May-Jul -> DJF Y), Australian
wheat (Nov-Dec -> DJF Y+1), US winter wheat (Jun -> DJF Y), Indian kharif rice
(Oct-Dec -> DJF Y+1). No lag is ever chosen by fit, and the full 1961-2023
history stays in the estimation window.

A pair with no verified harvest month gets NO coefficient. Coverage is limited
by the calendar, which is the honest constraint.

INFERENCE
---------
Two slopes, because La Nina is not negative El Nino -- the response is
asymmetric and one slope averages two physical regimes into a meaningless
number. Trend removed by a centred 9-year moving average of log yield. Standard
errors are Newey-West HAC: MA-detrended yield anomalies are serially correlated
and the detrend adds more, so OLS errors are too small and reward whichever
series is SMOOTHEST rather than whichever response is strongest. The joint test
is an HAC Wald. p is then passed through Benjamini-Hochberg across every pair,
because at a per-pair 0.10 threshold a panel this size hands back dozens of
passes on noise alone.

The Indian Ocean Dipole is fit alongside as a DIAGNOSTIC only, setting
`enso_specific`. It does not gate and it does not replace the coefficient: the
published number is the UNCONDITIONAL association, because the question this
dashboard asks is "an El Nino is forecast, what should this country expect?",
whose answer includes whatever the Indian Ocean typically does alongside one.
Conditioning is actively misleading under collinearity -- it flips Australian
wheat positive, offsetting a large ENSO slope against a large IOD slope.

Output: data/enso_model.json
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

import numpy as np
from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refresh_faostat_fbs import FAO_AREA_TO_ISO3  # noqa: E402

FAOSTAT_URL = ("https://bulks-faostat.fao.org/production/"
               "Production_Crops_Livestock_E_All_Data_(Normalized).zip")
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
DMI_URL = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"

ITEMS = {
    "Maize (corn)": "corn", "Wheat": "wheat", "Rice": "rice",
    "Sorghum": "sorghum", "Millet": "millet", "Barley": "barley",
    "Soya beans": "soybeans",
}

MIN_YEARS = 30
P_GATE = 0.10
MA_WINDOW, MA_MIN = 9, 5

HEADERS = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")}


def _cache(name: str, url: str) -> bytes:
    d = os.environ.get("FOODSHIELD_CACHE") or "/tmp/foodshield-enso-cache"
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    if os.path.exists(p) and os.path.getsize(p) > 1024:
        return open(p, "rb").read()
    import urllib.request
    b = urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=900).read()
    open(p, "wb").write(b)
    return b


def load_oni() -> dict[int, float]:
    raw = _cache("oni.ascii.txt", ONI_URL).decode("utf-8", "replace")
    out = {}
    for line in raw.splitlines():
        f = line.split()
        if len(f) == 4 and f[0] == "DJF":
            try:
                out[int(f[1])] = float(f[3])
            except ValueError:
                pass
    if len(out) < 60:
        raise RuntimeError(f"ONI parse got {len(out)} DJF seasons -- feed shape changed")
    return out


def load_dmi() -> dict[int, float]:
    """SON-mean DMI, keyed by SON year. The dipole co-occurring with a DJF-Y
    event peaked in SON of Y-1; callers must apply that offset."""
    raw = _cache("dmi.had.long.data", DMI_URL).decode("utf-8", "replace")
    out = {}
    for line in raw.splitlines():
        f = line.split()
        if len(f) != 13:
            continue
        try:
            y = int(f[0]); m = [float(v) for v in f[1:]]
        except ValueError:
            continue
        son = [v for v in m[8:11] if v > -900]
        if 1800 < y < 2100 and len(son) == 3:
            out[y] = sum(son) / 3.0
    if len(out) < 100:
        raise RuntimeError(f"DMI parse got {len(out)} years -- feed shape changed")
    return out


def load_calendars() -> dict:
    p = os.path.join(DATA, "crop_calendars.json")
    if not os.path.exists(p):
        return {}
    raw = json.load(open(p, encoding="utf-8"))
    return {k: v for k, v in (raw.get("data", raw)).items() if not k.startswith("_")}


def shift_for(cal_entry: dict) -> int | None:
    """DJF offset from the harvest month. See module docstring for the rule."""
    h = cal_entry.get("harvest") or []
    if not h:
        return None
    # A season harvested Jan-Aug was growing through the preceding DJF of its own
    # harvest year. One harvested Sep-Dec ran Mar-Dec, so the event that shaped it
    # peaks in the DJF that follows.
    return 0 if min(h) <= 8 else 1


def load_faostat() -> dict:
    """iso3 -> crop -> year -> {yield, prod, area, official}. Year = harvest year."""
    blob = _cache("faostat_qcl.zip", FAOSTAT_URL)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in zf.namelist() if "Normalized" in n and n.endswith(".csv"))
    panel: dict = {}
    field = {"Yield": "yield", "Production": "prod", "Area harvested": "area"}
    with zf.open(name) as fh:
        for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace")):
            crop = ITEMS.get(row["Item"])
            if not crop:
                continue
            key = field.get(row["Element"])
            if not key:
                continue
            iso3 = FAO_AREA_TO_ISO3.get(int(row["Area Code"])) if row["Area Code"].isdigit() else None
            if not iso3:
                continue
            try:
                year = int(row["Year"]); val = float(row["Value"])
            except (ValueError, TypeError):
                continue
            if val <= 0:
                continue
            slot = panel.setdefault(iso3, {}).setdefault(crop, {}).setdefault(year, {})
            slot[key] = val
            if key == "yield":
                slot["official"] = (row.get("Flag") == "A")
    return panel


def detrend(years, vals):
    logy = np.log(np.array(vals, float))
    half = MA_WINDOW // 2
    oy, oa = [], []
    for i in range(len(logy)):
        lo, hi = max(0, i - half), min(len(logy), i + half + 1)
        if hi - lo < MA_MIN:
            continue
        oy.append(years[i]); oa.append(float(logy[i] - logy[lo:hi].mean()))
    return oy, oa


def fit(years, anom, djf, shift, dmi=None):
    x, y, d = [], [], []
    for yr, a in zip(years, anom):
        o = djf.get(yr + shift)
        if o is None:
            continue
        if dmi is not None:
            dv = dmi.get(yr + shift - 1)   # SON of Y-1 pairs with the DJF-Y event
            if dv is None:
                continue
            d.append(dv)
        x.append(o); y.append(a)
    n = len(x)
    if n < MIN_YEARS:
        return None
    o = np.array(x); Y = np.array(y)
    cols = [np.ones(n), np.maximum(o, 0.0), np.minimum(o, 0.0)]
    if dmi is not None:
        cols.append(np.array(d))
    X = np.column_stack(cols)
    k = X.shape[1]
    if np.linalg.matrix_rank(X) < k:
        return None
    beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
    resid = Y - X @ beta
    dof = n - k
    if dof <= 0:
        return None
    try:
        XtX_inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return None
    L = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    S = (resid[:, None] * X).T @ (resid[:, None] * X)
    for lag in range(1, max(L, 1) + 1):
        if lag >= n:
            break
        w = 1.0 - lag / (L + 1.0)
        A = (resid[lag:, None] * X[lag:]).T @ (resid[:-lag, None] * X[:-lag])
        S += w * (A + A.T)
    cov = XtX_inv @ S @ XtX_inv * (n / max(dof, 1))
    se = np.sqrt(np.maximum(np.diag(cov), 1e-30))
    R = np.zeros((2, k)); R[0, 1] = 1.0; R[1, 2] = 1.0
    Rb = R @ beta
    try:
        wald = float(Rb @ np.linalg.inv(R @ cov @ R.T) @ Rb)
    except np.linalg.LinAlgError:
        return None
    tot = float(((Y - Y.mean()) ** 2).sum())
    return {
        "n": n,
        "b_nino": float(beta[1]), "se_nino": float(se[1]),
        "p_nino": float(2 * stats.t.sf(abs(beta[1] / se[1]), dof)) if se[1] > 0 else 1.0,
        "b_nina": float(beta[2]), "se_nina": float(se[2]),
        "p_nina": float(2 * stats.t.sf(abs(beta[2] / se[2]), dof)) if se[2] > 0 else 1.0,
        "r2": 1.0 - float(resid @ resid) / tot if tot > 0 else 0.0,
        "p_joint": float(stats.f.sf(wald / 2.0, 2, dof)) if wald > 0 else 1.0,
        "b_iod": float(beta[3]) if dmi is not None else None,
        "p_iod": (float(2 * stats.t.sf(abs(beta[3] / se[3]), dof))
                  if dmi is not None and se[3] > 0 else None),
    }


def main() -> int:
    djf, dmi, cals = load_oni(), load_dmi(), load_calendars()
    panel = load_faostat()
    print(f"[INFO] ONI DJF {len(djf)} | DMI SON {len(dmi)} | FAOSTAT countries {len(panel)}"
          f" | calendars {len(cals)}")
    if not cals:
        print("[WARN] no data/crop_calendars.json -- nothing can be aligned, so nothing is fit")

    results, n_fit, n_sig, n_thin, n_nocal = {}, 0, 0, 0, 0
    for iso3 in sorted(panel):
        for crop in sorted(panel[iso3]):
            cal = (cals.get(iso3) or {}).get(crop)
            shift = shift_for(cal) if cal else None
            if shift is None:
                n_nocal += 1
                continue
            series = panel[iso3][crop]
            years = sorted(y for y in series if series[y].get("yield"))
            if len(years) < MIN_YEARS:
                n_thin += 1
                continue
            vals = [series[y]["yield"] for y in years]
            ay, anom = detrend(years, vals)
            base = fit(ay, anom, djf, shift)
            if not base:
                n_thin += 1
                continue
            n_fit += 1
            adj = fit(ay, anom, djf, shift, dmi=dmi)
            recent = [series[y] for y in sorted(series)[-10:] if series[y].get("prod")]
            official = [series[y].get("official") for y in years]
            results.setdefault(iso3, {})[crop] = {
                "n_years": base["n"], "year_from": min(ay), "year_to": max(ay),
                "alignment": "djf_same_year" if shift == 0 else "djf_next_year",
                "alignment_basis": f"harvest months {cal.get('harvest')} (USDA/FAO-GIEWS calendar)",
                "official_share": round(sum(1 for o in official if o) / len(official), 2),
                "mean_production_t": round(float(np.mean([r["prod"] for r in recent])), 1) if recent else 0.0,
                "mean_production_kt": round(float(np.mean([r["prod"] for r in recent])) / 1000.0, 1) if recent else 0.0,
                "enso_specific": bool(adj and adj["p_joint"] < P_GATE),
                "p_incremental_over_iod": round(adj["p_joint"], 5) if adj else None,
                "iod_pct_per_dmi": round(adj["b_iod"] * 100, 3) if adj else None,
                "_p": base["p_joint"], "_pend": {
                    "yield_pct_per_oni_nino": round(base["b_nino"] * 100, 3),
                    "se_nino_pct": round(base["se_nino"] * 100, 3),
                    "p_nino": round(base["p_nino"], 5),
                    "yield_pct_per_oni_nina": round(base["b_nina"] * 100, 3),
                    "se_nina_pct": round(base["se_nina"] * 100, 3),
                    "p_nina": round(base["p_nina"], 5),
                    "r2": round(base["r2"], 4),
                },
            }

    flat = sorted((e["_p"], i, c) for i, cs in results.items() for c, e in cs.items())
    m = len(flat)
    q_of, run = {}, 1.0
    for rank in range(m, 0, -1):
        pv, i, c = flat[rank - 1]
        run = min(run, pv * m / rank)
        q_of[(i, c)] = run
    for i, cs in results.items():
        for c, e in cs.items():
            q = q_of[(i, c)]
            pend = e.pop("_pend"); pv = e.pop("_p")
            e["p_value"] = round(pv, 5); e["q_value"] = round(q, 5)
            e["signal"] = bool(q < P_GATE)
            if e["signal"]:
                n_sig += 1
                e.update(pend)
                if not e["enso_specific"]:
                    e["note"] = ("Reported, but NOT ENSO-specific: adding the Indian Ocean "
                                 "Dipole removes ENSO's incremental explanatory power. Treat "
                                 "as a shared Indo-Pacific teleconnection.")
            else:
                e["note"] = ("no ENSO signal surviving false-discovery control across the "
                             f"{m} pairs tested -- not modelled")

    payload = {"_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(), "version": "v2-faostat",
        "production_ready": bool(n_sig > 0),
        "method": ("Per country x crop OLS of detrended log-yield anomaly on DJF ONI with "
                   "separate El Nino and La Nina slopes. FAOSTAT QCL, indexed by CALENDAR "
                   "HARVEST YEAR. Alignment is set by the harvest month from USDA/FAO-GIEWS "
                   "crop calendars -- harvest Jan-Aug uses DJF(Y), Sep-Dec uses DJF(Y+1) -- "
                   "never chosen by fit. Newey-West HAC errors and an HAC Wald joint test; "
                   "Benjamini-Hochberg across the panel with q<0.10."),
        "sources": {"enso": ONI_URL, "iod": DMI_URL, "yields": FAOSTAT_URL},
        "honesty": ("Coefficients are MEASURED, not taken from literature. Pairs without a "
                    "verified harvest month get NO coefficient -- coverage is limited by the "
                    "crop calendar, which is the honest constraint. Absence of a reported "
                    "effect means no detectable signal, not zero effect. ENSO shifts the odds "
                    "of a yield outcome; it does not determine any single country-season."),
        "counts": {"pairs_fitted": n_fit, "pairs_with_signal": n_sig,
                   "pairs_too_thin": n_thin, "pairs_without_calendar": n_nocal},
    }, "data": results}
    out = os.path.join(DATA, "enso_model.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, sort_keys=True, ensure_ascii=False); f.write("\n")
    print(f"[OK] fitted {n_fit} | signal {n_sig} | thin {n_thin} | no-calendar {n_nocal}")
    print(f"[OK] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
