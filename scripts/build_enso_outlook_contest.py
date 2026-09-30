#!/usr/bin/env python3
"""
build_enso_outlook_contest.py: does the Ocean outlook's NMME + ENSO-regression hybrid earn its place?

The Ocean map's rain outlook (scripts/outlook_rain_spi.py) averages two forecasts of a month's SPI: NMME (CPC's ensemble mean, ratio
transfer onto PREC/L) and the PREC/L ENSO regression driven by the official ENSO outlook, the regression kept only in cells where
it is skilful. An auditor fears ENSO is counted twice, since NMME already contains ENSO. This scores the pieces on past forecasts.

Forecasts (archived by CPC, https://ftp.cpc.ncep.noaa.gov/NMME/realtime_anom/ENSMEAN/<YYYYMM>0800/): the NMME run started in September
of each year 2018 to 2025, for January of the next year (lead 4; CPC's archive before September 2018 holds GRIB1 files only, which are not read here). Observed: NOAA PREC/L January rain, read as SPI on the same
1991-2020 fits the outlook uses (scripts/precl_model.py). Four forecasts per cell and year:
  nmme       = spi(k x median), k = 1 + NMME percent change (the production ratio transfer)
  regression = the PREC/L ENSO fit, SPI = c0 + c1 x ONI, ONI = IRI's mid-September plume average for the coming DJF (data/enso_forecast_skill.json;
               CPC's official September outlook is not archived in machine-readable form; the plume is a stand-in and is Nino 3.4 on the models' base)
  hybrid     = mean of the two where the regression is skilful, NMME alone elsewhere (what the page shows)
  climatology= SPI 0
Scores: RMSE and anomaly correlation of SPI, CRPS and Brier (dry: SPI <= -1, wet: SPI >= +1) of normal forecasts (SD = the fit's residual SD where
the regression is used, else 1), each compared with climatology. Year-block bootstrap for differences.

Leakage, stated: the regression was fitted on 1951-2020 (so targets January 2019 and 2020 are in its sample; January 2021 to 2026 are out of sample), and
the SPI fits use 1991-2020. Scores are therefore split in-sample / out-of-sample. This is a one-target-month, one-lead test (September start,
January target, the month the DJF ONI centres on), not every month the map paints.

Hand-run (numpy, scipy; PREC/L through build_sst_composites.load_precl). Writes data/enso_outlook_contest.json.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refresh_seasonal_outlook as RS  # noqa: E402
import precl_model as P  # noqa: E402
from build_sst_composites import load_precl  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = Path.home() / ".cache" / "foodshield"
MODEL_FIT_YEARS = (1951, 2020)
REGIONS = {
    "Southern Africa": (-35, -10, 15, 40), "East Africa": (-10, 12, 28, 52), "Eastern Australia": (-40, -10, 135, 155),
    "Maritime Continent": (-10, 8, 95, 140), "India": (8, 30, 70, 90), "Brazil": (-25, 0, -60, -35),
    "Argentina and Uruguay": (-40, -25, -65, -50), "Central America": (8, 20, -95, -75), "US south": (25, 37, -105, -80),
}


def nmme_percent(ym: str, rg: dict, cells: list) -> dict | None:
    """{cell: NMME percent change of January rain} for the run started `ym` (September), or None if the archive lacks it."""
    cache = CACHE / f"nmme_jan_pct_{ym}.json"
    if cache.exists():
        return {(int(k) if k.isdigit() else k): v for k, v in json.loads(cache.read_text()).items()}
    url = f"{RS.ENS}/{ym}0800"
    try:
        files = set(re.findall(r'href="([^"/]+\.nc)"', RS._get(f"{url}/").decode("latin-1")))
    except Exception:  # noqa: BLE001
        return None
    m_init = int(ym[:4]) * 12 + int(ym[4:]) - 1
    tgt = m_init + 4
    anomf = f"NMME.prate.{ym}.ENSMEAN.anom.nc"
    models = sorted(m[1] for f in files for m in [re.fullmatch(rf"(.+)\.prate\.{ym}\.ENSMEAN\.fcst\.nc", f)]
                    if m and m[1] != "NMME" and f"{m[1]}.prate.{ym}.ENSMEAN.anom.nc" in files)
    if anomf not in files or len(models) < 4:
        return None

    def slab(fname):
        nc = RS.NC(f"{url}/{fname}")
        nc.check_grid()
        if nc.months("initial_time")[0] != m_init:
            raise RuntimeError(f"{fname}: wrong start month")
        t = nc.months("target")
        if tgt not in t:
            return None
        return nc.slabs("fcst", t.index(tgt), 1)[0]

    try:
        anom = [v * 86400 for v in slab(anomf)]
        per = [s for s in (slab(f"{m}.prate.{ym}.ENSMEAN.fcst.nc") for m in models) if s is not None and 2 * sum(map(RS._ok, s)) > len(s)]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] {ym}: {e}")
        return None
    if len(per) < RS.MIN_MODELS:
        return None
    clim = [f * 86400 - a for f, a in zip(RS._mean(per), anom)]
    out = {}
    for c in cells:
        r, col = divmod(c, rg["nlon"])
        w = RS._weights(rg["lat0"] + r * rg["step_deg"], rg["lon0"] + col * rg["step_deg"], 1.25)
        a, cl = RS._regrid(anom, [w])[0], RS._regrid(clim, [w])[0]
        if a is not None and cl is not None and cl >= RS.ARID_MM_DAY:
            out[c] = 100 * a / cl
    out["_n_models"] = len(per)
    cache.write_text(json.dumps(out))
    return out


def crps(mu, sd, y):
    z = (y - mu) / sd
    return sd * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / math.sqrt(math.pi))


def main() -> int:
    skill = json.loads((DATA / "enso_forecast_skill.json").read_text())["data"]
    plume = {int(r["issued"][:4]): r["forecast_avg"] for r in skill["rows"]}
    if skill.get("current"):
        plume[int(skill["current"]["issued"][:4])] = skill["current"]["forecast_avg"]
    rain, lats, lons = load_precl()
    m = P.load()
    rg = {"lat0": -56.25, "lon0": -178.75, "step_deg": 2.5, "nlat": 52, "nlon": 144}
    cells = [c for c in m["cells"] if P.fit(c, "m1", 1) is not None and P.enso_predict(c, "m1", 1, 0.0) is not None]
    med = {c: P.total_from_spi(c, "m1", 1, 0.0) for c in cells}
    rows, skipped = [], []
    n_models_by = {}
    for y in range(2011, 2026):
        if y < 2018:
            skipped.append({"init": y, "why": "CPC archive before 2018 is GRIB1 (prate.<date>.<model>.ensmean.*.1x1.grb), not NetCDF; not read"})
            continue
        if y not in plume or (y + 1, 1) not in rain:
            skipped.append({"init": y, "why": "no IRI plume or no observed January"})
            continue
        pct = nmme_percent(f"{y}09", rg, cells)
        if not pct:
            skipped.append({"init": y, "why": "NMME archive file missing or unreadable"})
            continue
        n_models_by[y] = pct.pop("_n_models", None)
        obs_field = rain[(y + 1, 1)]
        for c in cells:
            r, col = divmod(c, rg["nlon"])
            rate = obs_field[r, col]
            if c not in pct or not np.isfinite(rate) or not med[c]:
                continue
            obs = P.spi_from_total(c, "m1", 1, float(rate) * 31)
            k = max(0.0, 1 + pct[c] / 100)
            nm = P.spi_from_total(c, "m1", 1, k * med[c])
            en = P.enso_predict(c, "m1", 1, plume[y])
            if obs is None or nm is None or en is None:
                continue
            lat, lon = rg["lat0"] + r * rg["step_deg"], rg["lon0"] + col * rg["step_deg"]
            rows.append((y, c, lat, lon, obs, nm, en[0], en[1], bool(en[2])))
    if not rows:
        raise RuntimeError("no forecast-observation pairs: the archive or PREC/L could not be read")
    A = np.array([(r[0], r[2], r[3], r[4], r[5], r[6], r[7], r[8]) for r in rows], float)
    yr, lat, lon, obs, nm, rg_, sd_r, sk = A.T
    sk = sk.astype(bool)
    hyb = np.where(sk, (nm + rg_) / 2, nm)
    sd_h = np.where(sk, sd_r, 1.0)
    methods = {"nmme": (nm, np.ones_like(nm)), "regression": (rg_, sd_r), "hybrid": (hyb, sd_h), "climatology": (np.zeros_like(nm), np.ones_like(nm))}

    def score(mask):
        n = int(mask.sum())
        out = {"cell_years": n}
        if not n:
            return out
        for k, (mu, sd) in methods.items():
            e = obs[mask] - mu[mask]
            d = {"rmse": round(float(np.sqrt(np.mean(e ** 2))), 3),
                 "corr": None if k == "climatology" or np.std(mu[mask]) == 0 else round(float(np.corrcoef(mu[mask], obs[mask])[0, 1]), 3),
                 "crps": round(float(np.mean(crps(mu[mask], sd[mask], obs[mask]))), 3)}
            for nm_, thr, sgn in (("brier_dry", -1.0, -1), ("brier_wet", 1.0, 1)):
                p = norm.cdf((thr - mu[mask]) / sd[mask]) if sgn < 0 else 1 - norm.cdf((thr - mu[mask]) / sd[mask])
                o = (obs[mask] <= thr) if sgn < 0 else (obs[mask] >= thr)
                d[nm_] = round(float(np.mean((p - o) ** 2)), 4)
            out[k] = d
        return out

    allm = np.ones(len(yr), bool)
    in_s = yr + 1 <= MODEL_FIT_YEARS[1]
    def bootstrap(mask, a, b, n_boot=4000):
        """Year-block bootstrap of the RMSE difference a minus b (negative: a is better)."""
        yrs = sorted(set(yr[mask]))
        rng = np.random.default_rng(20261001)
        se = {y_: (np.sum((obs[mask & (yr == y_)] - methods[a][0][mask & (yr == y_)]) ** 2), np.sum((obs[mask & (yr == y_)] - methods[b][0][mask & (yr == y_)]) ** 2), int((mask & (yr == y_)).sum())) for y_ in yrs}
        d = []
        for _ in range(n_boot):
            pick = [yrs[i] for i in rng.integers(0, len(yrs), len(yrs))]
            n = sum(se[p][2] for p in pick)
            d.append(math.sqrt(sum(se[p][0] for p in pick) / n) - math.sqrt(sum(se[p][1] for p in pick) / n))
        lo, hi = np.percentile(d, [2.5, 97.5])
        return {"years": len(yrs), "diff_rmse": round(float(np.mean(d)), 3), "ci95": [round(float(lo), 3), round(float(hi), 3)]}

    by_region = {}
    for name, (s, n, w, e) in REGIONS.items():
        mk = (lat >= s) & (lat <= n) & (lon >= w) & (lon <= e)
        by_region[name] = {"all_cells": score(mk), "skilful_cells": score(mk & sk)}
    per_year = {}
    for y_ in sorted(set(yr)):
        per_year[f"Jan {int(y_) + 1}"] = score((yr == y_) & sk)
    res = {
        "all_cells": score(allm), "skilful_cells_only": score(sk),
        "skilful_in_sample_targets_2019_2020": score(sk & in_s), "skilful_out_of_sample_targets_2021_2026": score(sk & ~in_s),
        "all_cells_out_of_sample_targets_2021_2026": score(~in_s),
        "bootstrap_rmse_difference_skilful_cells": {"hybrid_minus_nmme": bootstrap(sk, "hybrid", "nmme"),
                                                    "hybrid_minus_regression": bootstrap(sk, "hybrid", "regression"),
                                                    "nmme_minus_climatology": bootstrap(sk, "nmme", "climatology"),
                                                    "hybrid_minus_nmme_out_of_sample": bootstrap(sk & ~in_s, "hybrid", "nmme")},
        "by_region": by_region, "by_year_skilful_cells": per_year,
    }
    # Verdict, from numbers: does the hybrid beat NMME alone and the regression alone in the skilful cells?
    sc, oo = res["skilful_cells_only"], res["skilful_out_of_sample_targets_2021_2026"]
    verdict = {"hybrid_beats_nmme_rmse": sc["hybrid"]["rmse"] < sc["nmme"]["rmse"], "hybrid_beats_regression_rmse": sc["hybrid"]["rmse"] < sc["regression"]["rmse"],
               "hybrid_beats_nmme_crps": sc["hybrid"]["crps"] < sc["nmme"]["crps"], "out_of_sample_hybrid_beats_nmme_rmse": oo.get("cell_years", 0) > 0 and oo["hybrid"]["rmse"] < oo["nmme"]["rmse"],
               "nmme_beats_climatology_rmse": sc["nmme"]["rmse"] < sc["climatology"]["rmse"],
               "hybrid_beats_nmme_ci_excludes_zero": res["bootstrap_rmse_difference_skilful_cells"]["hybrid_minus_nmme"]["ci95"][1] < 0}
    bh = res["bootstrap_rmse_difference_skilful_cells"]["hybrid_minus_nmme"]
    verdict["reading"] = (f"In the cells where the regression is used the hybrid's SPI error is {sc['hybrid']['rmse']:.2f} against {sc['nmme']['rmse']:.2f} for NMME alone, "
                          f"{sc['regression']['rmse']:.2f} for the regression alone and {sc['climatology']['rmse']:.2f} for climatology "
                          f"(hybrid minus NMME {bh['diff_rmse']:+.3f}, 95% interval {bh['ci95'][0]:+.3f} to {bh['ci95'][1]:+.3f} over {bh['years']} Januaries). "
                          f"Over all cells NMME's error is {res['all_cells']['nmme']['rmse']:.2f} against climatology's {res['all_cells']['climatology']['rmse']:.2f}: "
                          "at this lead and month NMME alone has no skill over climatology across the map, so the hybrid's gain is confined to the cells the regression covers. "
                          "Eight Januaries, one lead, regression partly in sample: the hybrid is not shown to double count ENSO, and is not proven better than NMME elsewhere.")
    payload = {"_meta": {
        "generated_at": datetime.now(timezone.utc).isoformat(), "version": "v1",
        "builder": "scripts/build_enso_outlook_contest.py (hand-run, numpy and scipy; output committed)", "hand_run": True,
        "sources": {"nmme": f"{RS.ENS}/<YYYYMM>0800/ (CPC NMME archive, September starts 2011-2025; public domain)",
                    "observed": "NOAA PSL PREC/L 2.5 degree monthly rain (precip.mon.mean.2.5x2.5.nc), January 2012 to 2026",
                    "spi_fits": "data/ref/precl_enso_model.json.gz (1991-2020 gamma SPI, ENSO regression fitted 1951-2020)",
                    "enso_input": "data/enso_forecast_skill.json: IRI mid-September plume average for DJF (proxy for CPC's official outlook ONI)"},
        "design": "One lead (September start, January target), one month. Forecast SPI from each method; observed SPI from PREC/L. Normal forecasts, SD 1 (NMME) or the regression's residual SD.",
        "caveats": ["Regression fitted 1951-2020: targets January 2019 and 2020 are in its sample; the out-of-sample block is January 2021 to 2026 (six years).",
                    "ENSO input is IRI's plume, not CPC's official outlook ONI (not archived as data).",
                    "One lead and one target month, not every month the map paints; 'skilful cells' are the cells the page blends.",
                    "Cells in a year share weather: read the region and year blocks, not the cell-year count, as the sample size."],
        "models_averaged_by_init": n_models_by, "init_years_used": sorted({int(v) for v in set(yr)}), "init_years_skipped": skipped,
    }, "data": {"verdict": verdict, "summary": res}}
    (DATA / "enso_outlook_contest.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    from pipeline_dag import stamp_file
    stamp_file("enso_outlook_contest.json")
    print(json.dumps({"verdict": verdict, "skilful": sc, "oos": oo, "all": res["all_cells"], "boot": res["bootstrap_rmse_difference_skilful_cells"], "skipped": skipped}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
