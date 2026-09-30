#!/usr/bin/env python3
"""
build_enso_distribution.py — turn the 2027 harvest scenario into a predictive distribution.

On the cron (run_all, after the outlook) when numpy is installed; without numpy the step is skipped and the
last good file stays. Its output data/enso_distribution.json is committed.

CI has no 34 MB FAOSTAT bulk, so the per-pair fit residuals (the only FAOSTAT-derived input) are cached in
data/ref/enso_pair_residuals.json, keyed to the generated_at of data/enso_model.json. A hand run with the
FAOSTAT cache (and scipy, through build_enso_model) rebuilds that cache; CI reads it. If the model file
is newer than the cache, or a shown pair has no cached residuals, the step fails loudly instead of guessing.
CI also re-fetches CPC's ONI and RONI text files (falling back to the committed copies), so the bridge follows
the newest months.

Two pieces.

A. RONI to ONI bridge. NOAA's operational index is now RONI; the crop slopes were fitted on ONI.
   Both series are CPC's own files (oni.ascii.txt, RONI.ascii.txt), 1950 to now. For each season
   from JAS to MJJ the script fits, by least squares over every year both files cover,

       ONI(season) = a + b * RONI(season) + c * gap_JJA

   where gap_JJA = ONI minus RONI in the June-August that opens that event (the warming the two
   indices disagree about is persistent, and it has grown: the ONI-RONI gap in December-February
   rose about 0.009 per year). Conditional distribution = fitted value + a coefficient draw (from
   the OLS covariance) + a draw from the empirical residuals of the same fit. Diagnostics stored:
   n, R2, residual SD, leave-one-out RMSE of this fit against RONI-only and RONI-plus-year fits.

B. Monte Carlo (N draws, fixed seed) for every pair the outlook shows:
     1. RONI for the target season: CPC's published outlook (enso_strengths.json roni_outlook:
        median, 5th and 95th percentile) as a two-piece normal (sigma below the median =
        (median - p05) / 1.645, above = (p95 - median) / 1.645). CPC's class probabilities are
        compared with it as a check, not used to draw.
     2. RONI -> ONI through the bridge.
     3. The pair's El Nino slope ~ Normal(fitted slope, HAC standard error) from enso_model.json
        (La Nina slope and SE if the ONI draw is negative).
     4. A residual drawn from the pair's own fit residuals (same design, same years as
        build_enso_model.py, refit here; in-sample, so a floor on real harvest noise).
   change = exp(slope * ONI + residual) - 1. Baseline and area are NOT in the draw: the file holds
   percent distributions and a production_kt copy of the row; the page multiplies at render time.
   Aggregate per crop: production-weighted percent change over the shown pairs of the crop,
   sharing one ONI draw per iteration, slopes and residuals independent across pairs.

Run: FOODSHIELD_CACHE=~/.cache/foodshield python3 scripts/build_enso_distribution.py
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

try:
    import numpy as np
except ImportError:  # run_all imports this module; main() refuses to run without numpy
    np = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
M = None   # build_enso_model (needs scipy and the FAOSTAT bulk): imported only by a hand run that rebuilds the residual cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REF = os.path.join(DATA, "ref")
RES_CACHE = os.path.join(REF, "enso_pair_residuals.json")
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
RONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt"
N_DRAWS = 10000
SEED = 20260930
TARGET = "DJF"
ORDER = ["JJA", "JAS", "ASO", "SON", "OND", "NDJ", "DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ"]
PCTS = [5, 10, 25, 50, 75, 90, 95]


def fetch(url: str, gz_name: str, refresh: bool) -> tuple[str, str]:
    """Return (text, fetched date). Live fetch, cached gzip under data/ref/ for offline re-runs."""
    p = os.path.join(REF, gz_name)
    if refresh or not os.path.exists(p):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "FoodShield/1.0"})
            txt = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
            os.makedirs(REF, exist_ok=True)
            old = None
            if os.path.exists(p):
                with gzip.open(p, "rt", encoding="utf-8") as fh:
                    old = fh.read()
            if old != txt:   # unchanged text is not rewritten, so the cron commit holds no gzip-header noise
                with open(p, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
                    fh.write(txt.encode("utf-8"))
            return txt, datetime.now(timezone.utc).date().isoformat()
        except Exception as e:  # noqa: BLE001
            if not os.path.exists(p):
                raise
            print(f"[WARN] fetch failed ({e}); using cached {gz_name}")
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        txt = fh.read()
    d = datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).date().isoformat()
    return txt, d


def parse(txt: str, ncol: int, col: int) -> dict:
    out = {}
    for line in txt.splitlines():
        f = line.split()
        if len(f) == ncol and f[0] in ORDER:
            try:
                out[(f[0], int(f[1]))] = float(f[col])
            except ValueError:
                pass
    return out


def design_rows(season: str, oni: dict, roni: dict, extra_year: bool = False):
    """One row per event year: (season_year, RONI, ONI, gap_JJA). JJA of year y opens the event."""
    rows = []
    after = ORDER.index(season) >= ORDER.index("DJF")
    for y in range(1950, 2100):
        sy = y + (1 if after else 0)
        k, j = (season, sy), ("JJA", y)
        if k in oni and k in roni and j in oni and j in roni:
            rows.append((sy, roni[k], oni[k], oni[j] - roni[j]))
    return np.array(rows, float)


def loo_rmse(X, y) -> float:
    e = []
    for i in range(len(y)):
        m = np.ones(len(y), bool)
        m[i] = False
        c = np.linalg.lstsq(X[m], y[m], rcond=None)[0]
        e.append(y[i] - X[i] @ c)
    return float(np.sqrt(np.mean(np.square(e))))


def fit_bridge(season: str, oni: dict, roni: dict) -> dict:
    d = design_rows(season, oni, roni)
    r, o, g, yr = d[:, 1], d[:, 2], d[:, 3], d[:, 0] - 2000
    one = np.ones(len(r))
    X = np.column_stack([one, r, g])
    beta = np.linalg.lstsq(X, o, rcond=None)[0]
    res = o - X @ beta
    dof = len(o) - 3
    s2 = float(res @ res) / dof
    cov = s2 * np.linalg.inv(X.T @ X)
    tot = float(((o - o.mean()) ** 2).sum())
    warm = r >= 1.0
    return {
        "season": season, "n": int(len(o)), "year_from": int(d[0, 0]), "year_to": int(d[-1, 0]),
        "a": float(beta[0]), "b_roni": float(beta[1]), "c_gap_jja": float(beta[2]),
        "cov": cov.tolist(), "resid_sd": float(np.sqrt(s2)),
        "r2": 1 - float(res @ res) / tot,
        "loo_rmse": loo_rmse(X, o),
        "loo_rmse_roni_only": loo_rmse(np.column_stack([one, r]), o),
        "loo_rmse_roni_year": loo_rmse(np.column_stack([one, r, yr]), o),
        "resid_sd_roni_ge_1": float(res[warm].std(ddof=0)) if warm.sum() > 3 else None,
        "n_roni_ge_1": int(warm.sum()),
        "gap_jja_range": [float(g.min()), float(g.max())],
        "residuals": [round(float(v), 3) for v in res],
        "_beta": beta, "_res": res, "_cov": cov,
    }


def split_normal(rng, med, p05, p95, n):
    lo, hi = (med - p05) / 1.645, (p95 - med) / 1.645
    z = rng.standard_normal(n)
    return np.where(z < 0, med + z * lo, med + z * hi), lo, hi


def pair_residuals(iso: str, crop: str, panel, cals, djf):
    """Refit the El Nino / La Nina two-slope model exactly as build_enso_model.fit and return residuals."""
    cal = (cals.get(iso) or {}).get(crop)
    shift = M.shift_for(cal)
    series = panel[iso][crop]
    years = sorted(y for y in series if series[y].get("yield"))
    ay, anom = M.detrend(years, [series[y]["yield"] for y in years])
    x, y = [], []
    for yr, a in zip(ay, anom):
        o = djf.get(yr + shift)
        if o is not None:
            x.append(o)
            y.append(a)
    o, Y = np.array(x), np.array(y)
    X = np.column_stack([np.ones(len(o)), np.maximum(o, 0.0), np.minimum(o, 0.0)])
    beta = np.linalg.lstsq(X, Y, rcond=None)[0]
    return beta, Y - X @ beta


def pair_fits(keys, model_ts: str, recompute: bool) -> tuple[dict, str]:
    """{iso/crop: (El Nino slope fitted here, residuals)} and where they came from ('cache' or 'FAOSTAT').
    The cache is valid for exactly the enso_model.json it was fitted beside (same generated_at)."""
    cache = {}
    if os.path.exists(RES_CACHE):
        with open(RES_CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    ok = cache.get("model_generated_at") == model_ts and all(k in (cache.get("pairs") or {}) for k in keys)
    if ok and not recompute:
        return {k: (cache["pairs"][k]["beta_nino"], np.array(cache["pairs"][k]["residuals"], float)) for k in keys}, "cache"
    global M
    try:
        import build_enso_model as M_  # needs scipy and the FAOSTAT QCL bulk
        M = M_
        panel, cals, djf = M.load_faostat(), M.load_calendars(), M.load_oni()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"pair residual cache is out of date for enso_model.json {model_ts} and the FAOSTAT fit cannot run here "
                           f"({type(e).__name__}: {e}). Hand-run scripts/build_enso_distribution.py on a machine with the FAOSTAT cache.")
    out, rec = {}, {}
    for k in keys:
        iso, crop = k.split("/")
        beta, res = pair_residuals(iso, crop, panel, cals, djf)
        out[k] = (float(beta[1]), res)
        rec[k] = {"beta_nino": float(beta[1]), "residuals": [float(v) for v in res]}
    with open(RES_CACHE, "w", encoding="utf-8") as fh:
        json.dump({"model_generated_at": model_ts, "built": datetime.now(timezone.utc).isoformat(),
                   "note": "Residuals of the two-slope fit in build_enso_model.py (MA-detrended log yield minus the fit), per shown pair; "
                           "written by a hand run of build_enso_distribution.py, read by the cron run.", "pairs": rec}, fh, indent=1)
        fh.write("\n")
    return out, "FAOSTAT"


def pct_summary(v) -> dict:
    q = np.percentile(v, PCTS)
    return {**{f"p{p:02d}": round(float(x), 1) for p, x in zip(PCTS, q)},
            "mean": round(float(np.mean(v)), 1), "prob_fall": round(float(np.mean(v < 0)), 3)}


def main() -> int:
    if np is None:
        raise RuntimeError("numpy is not installed: the El Niño distribution is not rebuilt (the last good file stays)")
    # In CI the ONI/RONI text files are re-read so the bridge follows the newest months; locally they come from the committed copies.
    refresh = "--refresh" in sys.argv or bool(os.environ.get("GITHUB_ACTIONS"))
    oni_txt, oni_date = fetch(ONI_URL, "cpc_oni_ascii.txt.gz", refresh)
    roni_txt, roni_date = fetch(RONI_URL, "cpc_roni_ascii.txt.gz", refresh)
    oni, roni = parse(oni_txt, 4, 3), parse(roni_txt, 3, 2)
    print(f"[INFO] ONI {len(oni)} seasons, RONI {len(roni)} seasons")

    strengths = json.load(open(os.path.join(DATA, "enso_strengths.json"), encoding="utf-8"))["data"]
    outlook = json.load(open(os.path.join(DATA, "enso_outlook.json"), encoding="utf-8"))["data"]
    model = json.load(open(os.path.join(DATA, "enso_model.json"), encoding="utf-8"))["data"]
    row_out = next(r for r in strengths["roni_outlook"] if r["season"] == TARGET)
    med, p05, p95 = row_out["median"], row_out["p05"], row_out["p95"]
    rec_oni = outlook["cases"]["record"]["oni"]
    jja_gap = strengths["oni_roni_gap"]["jja_gap"]
    yr_jja = strengths["oni_roni_gap"]["jja_year"]
    own_gap = round(oni[("JJA", yr_jja)] - roni[("JJA", yr_jja)], 2)
    assert abs(own_gap - jja_gap) < 0.011, (own_gap, jja_gap)

    # A. bridge for every season after JJA
    bridges = {s: fit_bridge(s, oni, roni) for s in ORDER[1:]}
    rng = np.random.default_rng(SEED)

    def oni_draws(season, roni_draw, gap, n):
        b = bridges[season]
        coef = rng.multivariate_normal(b["_beta"], b["_cov"], size=n)
        res = rng.choice(b["_res"], size=n, replace=True)
        return coef[:, 0] + coef[:, 1] * roni_draw + coef[:, 2] * gap + res

    # bridge read-out at CPC's DJF median, isolated from the outlook spread
    at_med = oni_draws(TARGET, np.full(N_DRAWS, med), jja_gap, N_DRAWS)
    bd = bridges[TARGET]
    point = bd["a"] + bd["b_roni"] * med + bd["c_gap_jja"] * jja_gap
    bridge_at_median = {
        "roni": med, "gap_jja": jja_gap, "oni_fit": round(float(point), 2),
        "oni_p05": round(float(np.percentile(at_med, 5)), 2), "oni_p10": round(float(np.percentile(at_med, 10)), 2),
        "oni_p50": round(float(np.percentile(at_med, 50)), 2), "oni_p90": round(float(np.percentile(at_med, 90)), 2),
        "oni_p95": round(float(np.percentile(at_med, 95)), 2), "oni_sd": round(float(at_med.std()), 2),
    }

    # B1 target RONI draws and the ONI they map to
    roni_d, s_lo, s_hi = split_normal(rng, med, p05, p95, N_DRAWS)
    oni_d = oni_draws(TARGET, roni_d, jja_gap, N_DRAWS)
    classes = strengths["seasons"][[s["season"] for s in strengths["seasons"]].index(TARGET)]["probs"]
    edges = [-9, -2, -1.5, -1, -0.5, 0.5, 1.0, 1.5, 2.0, 9]
    check = [round(float(np.mean((roni_d >= edges[i]) & (roni_d < edges[i + 1])) * 100), 1) for i in range(9)]

    # B2-B4 pairs
    shown = [r for r in outlook["rows_all"] if r.get("status") == "shown"]
    model_ts = json.load(open(os.path.join(DATA, "enso_model.json"), encoding="utf-8"))["_meta"]["generated_at"]
    fits, fit_src = pair_fits([f"{r['iso']}/{r['crop']}" for r in shown], model_ts, "--recompute" in sys.argv)
    print(f"[INFO] pair residuals from {fit_src}")
    pairs, pct_draws = [], {}
    for r in shown:
        iso, crop = r["iso"], r["crop"]
        m = model[iso][crop]
        beta1, res = fits[f"{iso}/{crop}"]
        assert abs(beta1 * 100 - m["yield_pct_per_oni_nino"]) < 0.01, (iso, crop, beta1, m["yield_pct_per_oni_nino"])
        b_up = rng.normal(m["yield_pct_per_oni_nino"] / 100, m["se_nino_pct"] / 100, N_DRAWS)
        b_dn = rng.normal(m["yield_pct_per_oni_nina"] / 100, m["se_nina_pct"] / 100, N_DRAWS)
        eps = rng.choice(res, size=N_DRAWS, replace=True)
        slope_part = np.where(oni_d >= 0, b_up * oni_d, b_dn * oni_d)
        pct = (np.exp(slope_part + eps) - 1) * 100
        pct_draws[(iso, crop)] = pct
        # the same draws with one source switched on at a time, for the page's fold
        only_enso = (np.exp(m["yield_pct_per_oni_nino"] / 100 * oni_d) - 1) * 100
        only_slope = (np.exp(b_up * float(np.median(oni_d))) - 1) * 100
        only_resid = (np.exp(eps) - 1) * 100
        pairs.append({
            "key": f"{iso}/{crop}", "iso": iso, "crop": crop, "harvest": r.get("harvest"),
            "harvest_year": r.get("harvest_year"), "in_season": r.get("in_season"),
            "production_kt": r.get("production_kt"), "production_basis": r.get("production_basis"),
            "slope_pct_per_oni": m["yield_pct_per_oni_nino"], "slope_se_pct": m["se_nino_pct"],
            "resid_sd_log_pts": round(float(res.std(ddof=3 if len(res) > 3 else 0)) * 100, 1), "n_resid": int(len(res)),
            "change_pct": pct_summary(pct),
            "spread_p10_p90_by_source": {
                "enso_forecast_only": [round(float(x), 1) for x in np.percentile(only_enso, [10, 90])],
                "slope_only_at_median_oni": [round(float(x), 1) for x in np.percentile(only_slope, [10, 90])],
                "residual_only": [round(float(x), 1) for x in np.percentile(only_resid, [10, 90])],
            },
        })
        print(f"  {iso}/{crop}: P10 {pairs[-1]['change_pct']['p10']} P50 {pairs[-1]['change_pct']['p50']} "
              f"P90 {pairs[-1]['change_pct']['p90']} fall {pairs[-1]['change_pct']['prob_fall']}")

    crops = {}
    for crop in sorted({p["crop"] for p in pairs}):
        ps = [p for p in pairs if p["crop"] == crop and isinstance(p["production_kt"], (int, float))]
        if not ps:
            continue
        w = np.array([p["production_kt"] for p in ps], float)
        agg = sum(w[i] * pct_draws[(p["iso"], p["crop"])] for i, p in enumerate(ps)) / w.sum()
        crops[crop] = {"pairs": [p["key"] for p in ps], "production_kt": float(w.sum()), "change_pct": pct_summary(agg)}

    payload = {"_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(), "version": "v2", "hand_run": False,
        "builder": "scripts/build_enso_distribution.py (numpy; on the cron after the outlook; pair residuals from data/ref/enso_pair_residuals.json, rebuilt by a hand run with the FAOSTAT cache)",
        "pair_residuals": {"source": fit_src, "model_generated_at": model_ts},
        "sources": {
            "oni": {"url": ONI_URL, "fetched": oni_date, "cache": "data/ref/cpc_oni_ascii.txt.gz", "licence": "NOAA CPC, public domain"},
            "roni": {"url": RONI_URL, "fetched": roni_date, "cache": "data/ref/cpc_roni_ascii.txt.gz", "licence": "NOAA CPC, public domain"},
            "outlook": "data/enso_strengths.json roni_outlook (CPC RONI outlook, issued " + str(row_out["issued"]) + ")",
            "slopes": "data/enso_model.json (FAOSTAT QCL yields, HAC standard errors)",
            "baseline": "data/enso_outlook.json rows_all production_kt, copied as production_kt; NOT part of the distribution",
        },
        "draws": N_DRAWS, "seed": SEED, "target_season": TARGET,
        "method": (
            "RONI for the target season is drawn from CPC's outlook as a two-piece normal fitted to its median and 5th and "
            "95th percentiles, then mapped to ONI with a least-squares bridge fitted on every year both CPC files cover "
            "(ONI = a + b RONI + c gap_JJA, where gap_JJA is ONI minus RONI in the June-August that opens the event). Each "
            "draw adds a coefficient draw and a draw from the bridge's own residuals. The pair's El Niño slope is drawn from "
            "its fitted value and HAC standard error, and a residual is drawn from the pair's own fit residuals. change = "
            "exp(slope x ONI + residual) - 1."),
        "not_included": "Baseline production, harvested area, price, policy or trade response; slope and residual error are taken as independent across pairs; the two-slope fit is linear in ONI and no winter above ONI +2.5 is in the record.",
        "residual_note": "Residuals are in-sample (MA-detrended log yield minus the fit), so they understate out-of-sample error; see enso_hindcast.json for held-out errors.",
    }, "data": {
        "target": {
            "season": TARGET, "label": row_out["label"], "issued": row_out["issued"],
            "roni_outlook": {"median": med, "p05": p05, "p95": p95, "sigma_below": round(s_lo, 3), "sigma_above": round(s_hi, 3)},
            "class_probs_cpc_pct": dict(zip(strengths["bins"], classes)),
            "class_probs_draw_pct": dict(zip(strengths["bins"], check)),
            "roni_draw": {k: round(float(np.percentile(roni_d, q)), 2) for k, q in
                          [("p05", 5), ("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95)]},
            "oni_draw": {k: round(float(np.percentile(oni_d, q)), 2) for k, q in
                         [("p05", 5), ("p10", 10), ("p25", 25), ("p50", 50), ("p75", 75), ("p90", 90), ("p95", 95)]},
            "oni_draw_mean": round(float(oni_d.mean()), 2),
            "record_oni": rec_oni, "prob_oni_above_record": round(float(np.mean(oni_d > rec_oni)), 3),
            "heuristic_oni_equiv_in_strengths": row_out["oni_equiv"],
        },
        "bridge_at_median": bridge_at_median,
        "bridge": {s: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.items() if not k.startswith("_")}
                   for s, b in bridges.items()},
        "pairs": pairs,
        "crops": crops,
    }}
    with open(os.path.join(DATA, "enso_distribution.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    from pipeline_dag import stamp_file
    stamp_file("enso_distribution.json")
    t = payload["data"]["target"]
    print(f"[OK] bridge at RONI {med}: ONI fit {bridge_at_median['oni_fit']}, sd {bridge_at_median['oni_sd']}; "
          f"target ONI P05/50/95 {t['oni_draw']['p05']}/{t['oni_draw']['p50']}/{t['oni_draw']['p95']}")
    print(f"[OK] DJF bridge n={bd['n']} R2={bd['r2']:.3f} LOO {bd['loo_rmse']:.3f} vs RONI-only {bd['loo_rmse_roni_only']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
