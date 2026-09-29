#!/usr/bin/env python3
"""outlook_rain_spi.py -- the Outlook's rain on the drought scale (SPI), started from the rain that has fallen.

Why. The Outlook months painted CPC's calibrated NMME tercile odds, which sit at 33% (no signal)
almost everywhere, so the glaze was nearly blank even under a forecast of a record El Nino. A
percent change does not say how rare a month would be either: -30% is ordinary in one place and a
drought in another. The observed stops already read rain as SPI (scripts/spi.py), so the Outlook
now does the same, on one record and one set of fits (NOAA PREC/L 1991-2020, scripts/precl_model.py),
from two independent forecasts:
  * dynamical: the NMME ensemble-mean month already in maps.rain (percent change against the models'
    own hindcast mean). Ratio transfer: the cell's PREC/L month distribution is scaled by
    k = 1 + rain / 100 (so each model's rain bias drops out) and rain_spi_nmme is the SPI of the
    scaled distribution's median, spi(k x median). The median, not the mean: the SPI of the plain
    1991-2020 mean is above zero wherever rain is skewed (median over fitted cell-months +0.13,
    95th percentile +0.27, measured on PREC/L 2026-09-28), so "no change" would have read wet.
  * statistical: the PREC/L ENSO regression (precl_model.enso_predict) driven by CPC's official
    ENSO outlook, ONI of the three-month season centred on the month (oni_equiv.median in
    data/enso_strengths.json roni_outlook), kept only where that cell and month are skilful.
  rain_spi = their mean where the ENSO model is skilful, NMME alone elsewhere. CPC's own seasonal
  outlooks combine dynamical models with ENSO composites and statistical tools (its 17 September
  2026 prognostic discussion); the equal mean here is simpler than CPC's skill-weighted one.

Drought outlook (rain_spi3): SPI of the three months ending in the month, OBSERVED months joined
to forecast ones, so the Outlook starts from the drought already on the ground and moves when a
new month is observed. Observed month totals: data/rain_months.json (CPC gauges, CHIRPS where
gauges are too few), else the 30-day window of data/rain_anomaly.json for the calendar month that
holds most of its days (the month not yet closed). They are moved onto the PREC/L scale by
rarity: the PREC/L total with the same SPI the observation has under its own 1991-2020 fit
(cpc.spi / fill.spi, precl_model.total_from_spi). Where there is no such SPI (arid, or no fit)
the share of normal is used instead: observed mm / observed normal x PREC/L 1991-2020 mean. Why
not the share everywhere: PREC/L's year-to-year spread differs from the gauge and satellite
products', most where its gauges are thin, so the same share of normal can be a different rarity.
For June to August 2026 the PREC/L SPI of the share-moved month differed from the month's own SPI
by more than 0.5 in 15-17% of cells (5th percentile -0.9 to -1.2, i.e. drier), and a Tibetan
cell with own SPIs +0.1 and -0.9 read -3 over three months. A month neither observed nor forecast
(between a closed month and a new run) enters at its 1991-2020 distribution and is listed.
Forecast months enter as distributions, not single totals: month j is the PREC/L month scaled by
k_j = total(rain_spi) / total(0) (k = 1 + rain/100 where the month has no fit), with mean
k x sample mean and variance k^2 x sample variance; the months' sum is taken as a gamma with that
mean and variance and its median joins the observed total. Summing each month's median instead
reads dry when nothing changes (median -0.12 over fitted cells); this rule reads a no-change
forecast within a few hundredths of zero (build meta, null_forecast_m3).
rain_p_dry / rain_p_wet: chance of the month's SPI at or below -1 / at or above +1, normal around
rain_spi with SD = the ENSO fit's residual SD where skilful, else 1 (climatology: 15.9%).

Island cells (island_cells): sea on the PREC/L grid but holding inhabited land, so PREC/L has no record
there. They use GPCP v2.3 instead (data/ref/gpcp_island_model.json.gz, scripts/build_gpcp_island_model.py
says how the cells are chosen): the same fits, El Nino fit and month stats in the same schema, passed to
precl_model with path=. refresh_seasonal_outlook reads the NMME change over the whole cell there.
Cells with an NMME change but no month fit of their own (PREC/L too thin or too dry) read that change on
CHIRPS v3's month spread (else CPC's gauge spread), NMME alone; their rain_spi3 stays blank.

Inputs are only committed data files, so this runs on every refresh, including the one that only
re-stamps an unchanged NMME run; the carry-in and the ONI then still update.

Monthly sample means and SDs (1991-2020, PREC/L, the rain grid, nominal month days) are in
data/ref/precl_month_stats_1991_2020.json.gz, built once locally (numpy):
    python3 scripts/outlook_rain_spi.py --build-stats
"""
from __future__ import annotations

import gzip
import json
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent))
import precl_model as P  # noqa: E402
import spi as SPI  # noqa: E402
from spi import to_x100  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATS = DATA / "ref" / "precl_month_stats_1991_2020.json.gz"
ISLANDS = DATA / "ref" / "gpcp_island_model.json.gz"   # scripts/build_gpcp_island_model.py
GPCP_URL = "https://www.ncei.noaa.gov/data/global-precipitation-climatology-project-gpcp-monthly/access"
SEASONS = "DJF JFM FMA MAM AMJ MJJ JJA JAS ASO SON OND NDJ".split()   # index = centre month - 1
PROB_LEAN = 40
PRECL_URL = "https://downloads.psl.noaa.gov/Datasets/precl/2.5deg/precip.mon.mean.2.5x2.5.nc"
_ND = NormalDist()


def _ab(ym: str) -> int:
    return int(ym[:4]) * 12 + int(ym[5:7]) - 1


def _ym(a: int) -> str:
    return f"{a // 12}-{a % 12 + 1:02d}"


# ---------------------------------------------------------------- inputs
def _stats() -> dict:
    with gzip.open(STATS, "rt", encoding="utf-8") as fh:
        s = json.load(fh)
    g = s["grid"]
    if (g["lat0"], g["lon0"], g["step_deg"], g["nlat"], g["nlon"]) != P.GRID:
        raise RuntimeError(f"{STATS.name}: grid is not the rain grid")
    s["_pos"] = {c: i for i, c in enumerate(s["cells"])}
    return s


def _moments(st: dict, cell: int, month: int) -> tuple[float, float] | None:
    """(1991-2020 mean, variance) of the month's PREC/L total in mm; None off the stats cells."""
    i = st["_pos"].get(cell)
    if i is None:
        return None
    return st["mean"][month - 1][i] / 10, (st["sd"][month - 1][i] / 10) ** 2


def island_cells() -> list[int]:
    """Rain-grid cells that are sea on PREC/L but hold inhabited land, read on GPCP (gpcp_island_model.json.gz)."""
    return list(P.load(ISLANDS)["cells"])


def _island_stats() -> dict:
    """The island file's 1991-2020 month means and SDs in the form _moments reads."""
    m = P.load(ISLANDS)
    return {"_pos": m["_pos"], "mean": m["stats"]["mean"], "sd": m["stats"]["sd"]}


def _fallback_fit(fits: tuple, cell: int) -> tuple[float, float, float] | None:
    """(shape, 1, dry share) of the cell's CHIRPS v3 month fit, else (alpha, 1, 0) of its CPC gauge month fit.
    Only the shape and the dry share matter to the ratio transfer, so the scale is 1."""
    (ch, n_years), cp = fits
    if cell in ch:
        return ch[cell][0], 1.0, ch[cell][2] / n_years
    if cell in cp:
        return cp[cell][0], 1.0, 0.0
    return None


def oni_by_month() -> tuple[dict, str | None, str]:
    """{absolute centre month: (season label, ONI)} from CPC's official ENSO outlook rows."""
    d = json.loads((DATA / "enso_strengths.json").read_text())["data"]
    out, issued = {}, None
    for row in d.get("roni_outlook") or []:
        m = re.match(r"([A-Z]{3}) (\d{4})", row["label"])
        oe = (row.get("oni_equiv") or {}).get("median")
        if not m or m[1] not in SEASONS or oe is None:
            continue
        centre = SEASONS.index(m[1]) + 1
        first = (centre - 2) % 12 + 1
        out[int(m[2]) * 12 + first] = (row["label"], oe)   # first month's index + 1 = centre
        issued = row.get("issued") or issued
    return out, issued, d.get("url", "")


def _cells(main: dict, fill: dict) -> tuple[list, int]:
    """[(mm, normal mm, own SPI x100 or None) or None per cell]: the main gauge grid, CHIRPS where it is null."""
    n = len(main["mm"])
    spi = main.get("spi") or [None] * n
    cells = [None if a is None or b is None else (a, b, z) for a, b, z in zip(main["mm"], main["norm_mm"], spi)]
    fc = fill.get("cells") or []
    fs = fill.get("spi") or [None] * len(fc)
    n_fill = 0
    for c, a, b, z in zip(fc, fill.get("mm") or [], fill.get("norm_mm") or [], fs):
        if cells[c] is None and a is not None and b is not None:
            cells[c] = (a, b, z)
            n_fill += 1
    return cells, n_fill


def observed() -> tuple[dict, list]:
    """{absolute month: [(mm, normal mm, own SPI x100) or None per cell]} and a list describing each month."""
    rm = json.loads((DATA / "rain_months.json").read_text())["data"]
    obs, info = {}, []
    for mo in rm["months"]:
        fill = mo.get("fill") or {}
        a = _ab(mo["month"])
        obs[a], n_fill = _cells(mo["cpc"], fill)
        last = date(a // 12 + (a % 12 == 11), (a + 1) % 12 + 1, 1) - timedelta(days=1)
        info.append({"month": mo["month"], "dates": f"{mo['month']}-01 to {last.isoformat()}",
                     "source": "data/rain_months.json: " + rm["product"].split(",")[0]
                               + (f"; CHIRPS ({fill.get('product')}) in {n_fill} cells without gauges" if n_fill else ""),
                     "closed": True})
    ra = json.loads((DATA / "rain_anomaly.json").read_text())["data"]
    w = ra["window"]
    d0, d1 = date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
    days = {}
    for k in range((d1 - d0).days + 1):
        dd = d0 + timedelta(days=k)
        days[dd.year * 12 + dd.month - 1] = days.get(dd.year * 12 + dd.month - 1, 0) + 1
    a = max(days, key=days.get)
    if a not in obs:
        f = ra.get("fill") or {}
        obs[a], n_fill = _cells(ra, f)
        fw = f.get("window") or {}
        info.append({"month": _ym(a), "dates": f"{w['start']} to {w['end']}"
                     + (f" (CHIRPS cells {fw['start']} to {fw['end']})" if fw and n_fill else ""),
                     "source": "data/rain_anomaly.json: the last 30 days, standing in for a month not yet closed "
                               f"({days[a]} of its days fall in {_ym(a)})"
                               + (f"; CHIRPS in {n_fill} cells without gauges" if n_fill else ""),
                     "closed": False})
    return obs, info


# ---------------------------------------------------------------- the model
def _norm_sf(x: float) -> float:
    return 1 - _ND.cdf(x)


def _p_below(t: float, o_tot: float, mu: float, var: float) -> float:
    """P(o_tot + X <= t) with X the forecast months' total, gamma-matched to (mu, var) as in _median_gamma."""
    x = t - o_tot
    if x <= 0:
        return 0.0
    if mu <= 0 or var <= 0:
        return 1.0 if mu <= x else 0.0
    return SPI.gammp(mu * mu / var, x / (var / mu))


def _median_gamma(mu: float, var: float, clip: float) -> float:
    if mu <= 0:
        return 0.0
    if var <= 0:
        return mu
    return P.total_of(0.0, (mu * mu / var, var / mu, 0.0), clip)


def add_rain_spi(payload: dict) -> dict:
    """Add the SPI fields to every payload['months'][i]['maps'] and a payload['rain_model'] block."""
    model, st = P.load(), _stats()
    clip = model["spi_rule"]["spi_clip"]
    oni, oni_issued, oni_url = oni_by_month()
    obs, obs_info = observed()
    mons = payload["months"]
    n = payload["rain_grid"]["nlat"] * payload["rain_grid"]["nlon"]
    # Island cells (sea on PREC/L, inhabited land) are read on GPCP: their own fits, El Nino fit and month stats.
    isl, st_isl = set(island_cells()), _island_stats()
    pth = [ISLANDS if c in isl else P.PARAMS for c in range(n)]
    sts = [st_isl if c in isl else st for c in range(n)]
    land = [c in isl or any(x["maps"]["rain"][c] is not None for x in mons) for c in range(n)]
    fc_ab = {_ab(x["key"]): j for j, x in enumerate(mons)}
    scale, oni_rows = {}, []          # scale[(j, cell)] = k of forecast month j

    for j, x in enumerate(mons):
        a, mth, mp = _ab(x["key"]), int(x["key"][5:7]), x["maps"]
        so = oni.get(a)
        lo, hi = model["enso_rule"]["oni_range"]["m1"][mth - 1]
        oni_rows.append({"month": x["key"], "season": so[0] if so else None, "oni_outlook": so[1] if so else None,
                         "oni_used": None if so is None else round(min(max(so[1], lo), hi), 2),
                         "fit_range": [lo, hi]})
        f_nmme, f_enso, f_spi, p_dry, p_wet = ([None] * n for _ in range(5))
        ch, ch_meta = SPI.chirps_fits(P.GRID, "month", mth - 1)
        fb_fits = ((ch, ch_meta["n_years"]), SPI.cpc_fits(P.GRID, "month", mth - 1)[0])
        for c in range(n):
            r = mp["rain"][c]
            ft = P.fit(c, "m1", mth, pth[c])
            if r is None or ft is None:
                fb = None if r is None else _fallback_fit(fb_fits, c)
                if fb is not None:
                    # No fit of its own but an NMME change: read the change on CHIRPS's (else CPC's) month spread,
                    # NMME alone, no El Nino fit. The three-month map stays blank there (no three-month fit).
                    z = P.spi_of(max(0.0, 1 + r / 100) * P.total_of(0.0, fb, clip), fb, -1.0, clip)
                    f_nmme[c] = f_spi[c] = to_x100(z)
                    p_dry[c] = int(round(100 * _ND.cdf(-1 - z)))
                    p_wet[c] = int(round(100 * _norm_sf(1 - z)))
                if land[c]:
                    scale[(j, c)] = 1.0 if r is None else max(0.0, 1 + r / 100)
                continue
            k = max(0.0, 1 + r / 100)
            med0 = P.total_of(0.0, ft, clip)
            z_n = P.spi_from_total(c, "m1", mth, k * med0, pth[c])
            pr = P.enso_predict(c, "m1", mth, so[1], pth[c]) if so else None
            z_e, sd = (pr[0], pr[1]) if pr and pr[2] else (None, 1.0)
            z = z_n if z_e is None else (z_n + z_e) / 2
            f_nmme[c], f_enso[c], f_spi[c] = to_x100(z_n), to_x100(z_e), to_x100(z)
            p_dry[c] = int(round(100 * _ND.cdf((-1 - z) / sd)))
            p_wet[c] = int(round(100 * _norm_sf((1 - z) / sd)))
            scale[(j, c)] = P.total_of(z, ft, clip) / med0 if med0 > 0 else k
        mp.update(rain_spi_nmme=f_nmme, rain_spi_enso=f_enso, rain_spi=f_spi, rain_p_dry=p_dry, rain_p_wet=p_wet)

    gap_months, windows = set(), []
    for j, x in enumerate(mons):
        a, mth = _ab(x["key"]), int(x["key"][5:7])
        parts = [a - 2, a - 1, a]
        windows.append({"month": x["key"], "window": [_ym(b) for b in parts],
                         "observed": [_ym(b) for b in parts if b not in fc_ab and b in obs],
                         "forecast": [_ym(b) for b in parts if b in fc_ab],
                         "climatology": [_ym(b) for b in parts if b not in fc_ab and b not in obs]})
        gap_months.update(windows[-1]["climatology"])
        out, p3d, p3w = [None] * n, [None] * n, [None] * n
        for c in range(n):
            if not land[c] or P.fit(c, "m3", mth, pth[c]) is None:
                continue
            o_tot = mu = var = 0.0
            ok = True
            for b in parts:
                bm = b % 12 + 1
                mv = _moments(sts[c], c, bm)
                if mv is None:
                    ok = False
                    break
                if b in fc_ab:
                    k = scale.get((fc_ab[b], c), 1.0)
                    mu, var = mu + k * mv[0], var + k * k * mv[1]
                elif b in obs:
                    o = obs[b][c]
                    if o is None:
                        # No gauge or CHIRPS reading (CHIRPS stops at 50N): that month enters at its normal range, as a
                        # month neither observed nor forecast does, so the map has no seam where the window turns
                        # all-forecast. Counted per window in rain_model.windows[].unobserved_cells.
                        mu, var = mu + mv[0], var + mv[1]
                        windows[-1]["unobserved_cells"] = windows[-1].get("unobserved_cells", 0) + 1
                        continue
                    if o[2] is not None and P.fit(c, "m1", bm, pth[c]) is not None:
                        o_tot += P.total_from_spi(c, "m1", bm, o[2] / 100, pth[c])   # same rarity on PREC/L (GPCP)
                    else:
                        o_tot += o[0] / o[1] * mv[0] if o[1] > 0 else o[0]   # share of normal x PREC/L (GPCP) mean
                else:
                    mu, var = mu + mv[0], var + mv[1]
            if ok:
                out[c] = to_x100(P.spi_from_total(c, "m3", mth, o_tot + _median_gamma(mu, var, clip), pth[c]))
                # The chance the three months end at least moderately dry (SPI <= -1) or wet (>= +1): the same
                # distribution of the months still to come, measured against the window's own 1991-2020 fit.
                lo, hi = P.total_from_spi(c, "m3", mth, -1.0, pth[c]), P.total_from_spi(c, "m3", mth, 1.0, pth[c])
                p3d[c] = int(round(100 * _p_below(lo, o_tot, mu, var)))
                p3w[c] = int(round(100 * (1 - _p_below(hi, o_tot, mu, var))))
        x["maps"]["rain_spi3"] = out
        x["maps"]["rain_p3_dry"], x["maps"]["rain_p3_wet"] = p3d, p3w

    payload["rain_model"] = _notes(model, st, oni_rows, oni_issued, oni_url, obs_info, windows, sorted(gap_months), payload)
    return payload


# ---------------------------------------------------------------- notes
def _notes(model, st, oni_rows, issued, oni_url, obs_info, windows, gaps, payload) -> dict:
    cv, meta, isl = model["meta"]["cv"], model["meta"], P.load(ISLANDS)
    used = {m for w in windows for m in w["observed"]}
    regions = [f"{r['region']}: {r['verdict']}" for r in meta["regions_at_oni_2_5"]]
    return {
        "computed": date.today().isoformat(),
        "method": [
            "All fields are on the rain grid. SPI fields are the Standardized Precipitation Index times 100, "
            "clamped to -300..300, against NOAA PREC/L 1991-2020 for the same calendar month (GPCP in "
            "island_cells, see island_note): -100, -150, -200 "
            "moderately, severely, extremely dry; the same above zero for wet. Chances are integer percent.",
            "rain_spi_nmme: the NMME month read as SPI. The cell's 1991-2020 month distribution is scaled by the "
            "models' own rain change (1 + rain / 100), so each model's rain bias drops out, and the SPI of the "
            "scaled median is kept. No change reads SPI 0.",
            "rain_spi_enso: what past El Ninos and La Ninas did to that cell in that month, a straight-line fit of "
            f"the month's SPI on ONI over {model['enso_rule']['fit_years'][0]}-{model['enso_rule']['fit_years'][1]}, "
            "read at CPC's official ENSO outlook for the three months centred on the month. Blank where the fit "
            f"is not skilful ({model['enso_rule']['skill_rule']}). An outlook ONI above the strongest El Nino "
            "in the fit years is read at that record, not beyond it.",
            "rain_spi: the forecast used on the map. The mean of the two where the ENSO fit is skilful, the NMME "
            "alone elsewhere. CPC's own seasonal outlooks also combine dynamical models with ENSO composites and "
            "statistical tools; this equal mean is simpler than CPC's skill-weighted blend.",
            "rain_p_dry and rain_p_wet: the chance that the month ends at SPI -1 or below, or +1 or above, "
            "reading the forecast as a normal spread around rain_spi with the ENSO fit's leftover spread where "
            "it is skilful and a spread of 1 elsewhere. With no signal both are 16.",
            "rain_spi3: the drought outlook, the SPI of the three months ending in the month. Months already "
            "observed enter as observed rain, months ahead as the forecast, so the first months start from the "
            "dry or wet spell already on the ground and the map moves when a new month is observed. An observed "
            "month is moved onto the PREC/L record at the same rarity it had in its own record (the same SPI); "
            "where it has no SPI, by its share of its own normal times the PREC/L average.",
            "Forecast months enter the three-month total as a spread of outcomes, not as one number, and the "
            "middle of the combined spread is used, so a forecast of no change reads close to SPI 0.",
            "Where the cell's own record (PREC/L, or GPCP in island_cells) has no fit for the month, because it "
            "has too few gauges or is too dry, but the NMME has a change, rain_spi is that change read on "
            "CHIRPS v3's 1991-2020 spread for the month, else on CPC's gauge spread, with no El Nino fit. "
            "rain_spi3 stays blank there.",
        ],
        "island_cells": list(isl["cells"]),
        "island_points": [{k: q[k] for k in ("cell", "lat", "lon", "name")} for q in isl["points"]],
        "island_note": "island_cells are sea on the PREC/L grid but hold inhabited land (a GeoNames place of 500 "
                       "people or more, or a local seat of government). Their rain is read on GPCP v2.3 "
                       "(satellite and gauges, land and sea), not PREC/L: the NMME change over the whole "
                       "2.5-degree cell, sea included, on GPCP's 1991-2020 spread, joined with an El Nino fit on "
                       "GPCP 1979-2020 under the same skill rule. An outlook ONI beyond that record is read at "
                       "the record. island_points puts each cell at its most populous place.",
        "oni": oni_rows, "oni_issued": issued, "oni_source_url": oni_url,
        "oni_note": "ONI is CPC's RONI outlook median moved onto the ONI scale by the site's gap "
                    "(enso_strengths.json oni_equiv.median); the ENSO fit uses CPC's ONI table.",
        "observed_months": [o for o in obs_info if o["month"] in used],
        "windows": windows,
        "climatology_months": gaps,
        "validation": {
            "enso_cross_validation": {k: cv["all_years"][k] for k in ("mse_en", "msss_en_vs_zero", "corr_en")},
            "enso_cross_validation_note": cv["scored_on"],
            "skilful_share_m1": meta["skilful_share"]["m1"],
            "hindcast_r_skilful": [{"event": h["event"], "month": h["month"], "r": h["r_skilful"]}
                                   for h in meta["hindcast"]],
            "regions_at_oni_2_5": regions,
            "null_forecast_m3": st["meta"]["null_forecast_m3"],
        },
        "sources": [
            {"name": "NOAA PREC/L monthly rain on land, 2.5 degree (1991-2020 fits and averages)", "url": PRECL_URL},
            {"name": "NOAA CPC ONI table (ENSO fit predictor)", "url": model["sources"]["oni"]},
            {"name": "NOAA CPC official ENSO outlook, RONI strength table", "url": oni_url},
            {"name": "NOAA CPC NMME real-time forecast", "url": payload.get("source_url")},
            {"name": "NOAA CPC prognostic discussion for long-lead seasonal outlooks, 17 September 2026",
             "url": "https://www.cpc.ncep.noaa.gov/products/predictions/90day/fxus05.html"},
            {"name": "NOAA CPC gauge rain and CHIRPS v3.0 (observed months)",
             "url": "https://ftp.cpc.ncep.noaa.gov/precip/CPC_UNI_PRCP/GAUGE_GLB/RT/"},
            {"name": "GPCP v2.3 monthly rain, NOAA NCEI Climate Data Record (island cells, 1979-2020)", "url": GPCP_URL},
            {"name": "GeoNames cities500 populated places (which sea cells hold inhabited land)",
             "url": isl["island_rule"]["places_url"]},
        ],
    }


# ---------------------------------------------------------------- printed checks
# The thirteen boxes and signs of precl_validate.REGIONS (sources are cited there); copied because that
# module needs numpy. (name, S, N, W, E on cell centres, expected sign under El Nino)
REGIONS = (("Indonesia", -10, 5, 95, 141, -1), ("eastern Australia", -38, -15, 140, 154, -1),
           ("southern Africa", -35, -15, 15, 40, -1), ("East Africa", -5, 5, 33, 42, 1),
           ("south India", 6, 15, 76, 82, 1), ("Philippines", 5, 19, 117, 127, -1),
           ("Colombia/Venezuela", 0, 12, -78, -60, -1), ("NE Brazil", -12, -2, -45, -35, -1),
           ("SE South America", -35, -25, -60, -48, 1), ("Peru/Ecuador coast", -8, 0, -82, -78.5, 1),
           ("US Gulf coast", 27, 33, -98, -80, 1), ("Central America", 7, 18, -92, -77, -1),
           ("southern China", 20, 30, 105, 122, 1))


def _pct(v: list, q: float) -> float:
    v = sorted(v)
    return v[min(len(v) - 1, int(q / 100 * len(v)))]


def checks(payload: dict) -> list[str]:
    g = payload["rain_grid"]
    out = []
    for x in payload["months"]:
        mp = x["maps"]
        for f in ("rain_spi", "rain_spi3"):
            v = [z / 100 for z in mp[f] if z is not None]
            if not v:
                continue
            out.append(f"[spi] {x['key']} {f}: n {len(v)}, p5/25/50/75/95 "
                       + "/".join(f"{_pct(v, q):+.2f}" for q in (5, 25, 50, 75, 95))
                       + f", |SPI|>=1 {sum(abs(z) >= 1 for z in v) / len(v):.0%}, >=1.5 {sum(abs(z) >= 1.5 for z in v) / len(v):.0%}"
                       + f", <=-1 {sum(z <= -1 for z in v) / len(v):.0%}, >=+1 {sum(z >= 1 for z in v) / len(v):.0%}")
        if "rain_prob_below" in mp:
            agree = tot = 0
            for z, pb, pa in zip(mp["rain_spi"], mp["rain_prob_below"], mp["rain_prob_above"]):
                if z is None or pb is None or pa is None or max(pb, pa) < PROB_LEAN or z == 0:
                    continue
                tot += 1
                agree += (z < 0) == (pb > pa)
            if tot:
                out.append(f"[spi] {x['key']} sign vs CPC odds leaning >= {PROB_LEAN}%: {agree}/{tot} ({agree / tot:.0%})")
        row = []
        for name, s, n_, w, e, sign in REGIONS:
            v1, v3 = [], []
            for r in range(g["nlat"]):
                la = g["lat0"] + r * g["step_deg"]
                if not s <= la <= n_:
                    continue
                for c in range(g["nlon"]):
                    lo = g["lon0"] + c * g["step_deg"]
                    if w <= lo <= e:
                        i = r * g["nlon"] + c
                        if mp["rain_spi"][i] is not None:
                            v1.append(mp["rain_spi"][i] / 100)
                        if mp["rain_spi3"][i] is not None:
                            v3.append(mp["rain_spi3"][i] / 100)
            f = (lambda v: "--" if not v else f"{sum(v) / len(v):+.2f}")
            row.append(f"{name}({'+' if sign > 0 else '-'}) {f(v1)}/{f(v3)}")
        out.append(f"[spi] {x['key']} region mean rain_spi/rain_spi3: " + "; ".join(row))
    return out


# ---------------------------------------------------------------- local build (numpy; never in CI)
def build_stats() -> None:
    """Write STATS: PREC/L 1991-2020 sample mean and SD of each calendar month's total per model cell,
    plus the no-change checks quoted in the docstring (numbers travel with the file)."""
    import numpy as np
    from build_sst_composites import load_precl
    rain, _, _ = load_precl()
    model = P.load()
    days, cells, clip = model["spi_rule"]["month_days"], model["cells"], model["spi_rule"]["spi_clip"]
    mean, sd = [], []
    for m in range(1, 13):
        X = np.array([rain[(y, m)].ravel()[cells] * days[m - 1] for y in range(1991, 2021)])
        mean.append([int(round(10 * v)) for v in X.mean(0)])
        sd.append([int(round(10 * v)) for v in X.std(0, ddof=1)])
    st = {"grid": model["grid"], "cells": cells, "mean": mean, "sd": sd, "_pos": {c: i for i, c in enumerate(cells)}}
    this, of_mean, sum_med = [], [], []
    for m in range(1, 13):
        for c in cells:
            f1 = P.fit(c, "m1", m)
            if f1 is not None:
                mv = _moments(st, c, m)
                of_mean.append(P.spi_from_total(c, "m1", m, mv[0]))
            if P.fit(c, "m3", m) is None:
                continue
            ms = [(m - 1 - k) % 12 + 1 for k in range(3)]
            mv = [_moments(st, c, x) for x in ms]
            this.append(P.spi_from_total(c, "m3", m, _median_gamma(sum(v[0] for v in mv), sum(v[1] for v in mv), clip)))
            meds = [P.fit(c, "m1", x) for x in ms]
            if all(meds):
                sum_med.append(P.spi_from_total(c, "m3", m, sum(P.total_of(0.0, f, clip) for f in meds)))

    def pct(v):
        return {f"p{q}": round(float(np.percentile(v, q)), 3) for q in (5, 25, 50, 75, 95)} | {"n": len(v)}
    out = {"grid": {k: model["grid"][k] for k in ("lat0", "lon0", "step_deg", "nlat", "nlon")}, "cells": cells,
           "mean": mean, "sd": sd,
           "meta": {"source": PRECL_URL, "years": [1991, 2020], "precl_last_month": model["sources"]["precl_last_month"],
                    "built": date.today().isoformat(),
                    "encoding": "mean and sd: [calendar month - 1][cell position], tenths of a mm of the month's total "
                                "(mm a day x nominal days, February 28); sd with n - 1",
                    "null_forecast_m3": {"text": "SPI of the 3-month window when every month is forecast with no change, "
                                                 "over every cell and end month with an m3 fit",
                                         "this_rule": pct(this), "sum_of_monthly_medians": pct(sum_med)},
                    "spi_of_1991_2020_mean_m1": pct(of_mean)}}
    STATS.write_bytes(gzip.compress(json.dumps(out, separators=(",", ":")).encode()))
    print(f"[OK] wrote {STATS} ({STATS.stat().st_size / 1e3:.0f} kB)")
    print(json.dumps(out["meta"], indent=1))


if __name__ == "__main__":
    if sys.argv[1:] == ["--build-stats"]:
        build_stats()
    else:
        print(__doc__)
