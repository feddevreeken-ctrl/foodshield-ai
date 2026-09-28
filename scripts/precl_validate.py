#!/usr/bin/env python3
"""precl_validate.py -- checks of the PREC/L ENSO rain model that build_precl_enso_model.py writes.

Why. A statistical model that says "at this ONI this cell's month is usually this dry" is only worth
painting if it (a) reproduces the rain of past strong El Ninos it was not fitted on, (b) is skilful
over a meaningful share of land, and (c) agrees with the El Nino rain signals the literature has
documented for decades. This module computes all three at build time (numpy; local only) and puts
them in the model file's meta, so the numbers travel with the model.

(a) Hindcast pattern correlation: for October and December of the onset year and January and
    February after, of 1982-83, 1997-98 and 2015-16 (inside the 1951-2020 fit: the leave-one-out
    prediction, i.e. the fit without that year) and 2023-24 (after the fit, fully out of sample),
    the Pearson correlation over land cells between predicted and observed 1-month SPI, once over
    the skilful cells and once over every fitted cell. The skilful set comes from the full fit, so
    it has seen the three in-sample events; the all-cell number has no such selection.
(b) The share of fitted land cells flagged skilful, per window.
(c) The model's SPI at ONI +2.5 (capped at each window's fit maximum) averaged over the land cells of
    thirteen regions, per calendar month, with the sign the literature gives for the season it
    names. Sources opened for this (2026-09-28):
      CPC   = NOAA CPC, "Typical Impacts of Warm (El Nino/Southern Oscillation - ENSO) and Cold
              Episodes" and its DJF / JJA warm-episode maps, which draw on Ropelewski and Halpert
              (1987, 1989): https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/impacts/enso.html
      BOM   = Bureau of Meteorology, "What is El Nino and what might it mean for Australia?":
              https://www.bom.gov.au/climate/updates/articles/a008-el-nino-and-australia.shtml
      ZR06  = Zubair and Ropelewski (2006), J. Climate 19, doi:10.1175/JCLI3670.1 (abstract)
      WMO26 = WMO, "El Nino impacts Greater Horn of Africa", 19 August 2026:
              https://wmo.int/media/news/el-nino-impacts-greater-horn-of-africa
      BSS03 = Black, Slingo and Sperber (2003), Mon. Wea. Rev. 131, doi:10.1175/1520-0493(2003)131<0074:AOSOTR>2.0.CO;2 (abstract)
      WWF00 = Wang, Wu and Fu (2000), J. Climate 13, doi:10.1175/1520-0442(2000)013<1517:PEATHD>2.0.CO;2 (abstract)
      BRDL  = Bourrel, Rau, Dewitte and Labat (2015), Hydrol. Process., doi:10.1002/hyp.10247 (abstract)
    Ropelewski and Halpert (1987) itself, Mason and Goddard (2001) and Davey et al. (2014) could not
    be opened from here (closed access, a bot wall, HTTP 403), so they are not cited as read.
"""
from __future__ import annotations

import numpy as np

EVENTS = (("1982-83", 1982), ("1997-98", 1997), ("2015-16", 2015), ("2023-24", 2023))
EVENT_MONTHS = ((0, 10), (0, 12), (1, 1), (1, 2))       # (year offset from onset year, month)
TEST_ONI = 2.5
# name, (lat_s, lat_n, lon_w, lon_e) on cell centres, expected sign, months the source names, source
REGIONS = (
    ("Indonesia and Borneo", (-10, 5, 95, 141), -1, (6, 7, 8, 12, 1, 2), "CPC: dry over Indonesia in both seasons (JJA and DJF)"),
    ("eastern Australia", (-38, -15, 140, 154), -1, (6, 7, 8, 9, 10, 11), "BOM: reduced winter-spring rain, east and north; CPC JJA map"),
    ("southern Africa", (-35, -15, 15, 40), -1, (12, 1, 2), "CPC: drier over southeastern Africa in the northern winter"),
    ("East Africa short rains", (-5, 5, 33, 42), 1, (10, 11, 12), "WMO26: El Nino with a positive IOD normally wetter OND; BSS03: IOD-led; CPC DJF map wet"),
    ("India north-east monsoon", (6, 15, 76, 82), 1, (10, 11, 12), "ZR06: El Nino enhances Oct-Dec rain in south India and Sri Lanka"),
    ("Philippines", (5, 19, 117, 127), -1, (6, 7, 8, 12, 1, 2), "CPC: dry over the Philippines in both seasons"),
    ("Colombia and Venezuela", (0, 12, -78, -60), -1, (12, 1, 2), "CPC DJF map: dry over northern South America"),
    ("north-east Brazil", (-12, -2, -45, -35), -1, (12, 1, 2), "CPC: drier over northern Brazil in the northern winter"),
    ("southern Brazil, Uruguay, NE Argentina", (-35, -25, -60, -48), 1, (12, 1, 2), "CPC: wetter from southern Brazil to central Argentina"),
    ("Peru and Ecuador coast", (-8, 0, -82, -78.5), 1, (1, 2, 3), "CPC: wetter along the west coast of tropical South America; BRDL: not in the 2000s"),
    ("US Gulf coast", (27, 33, -98, -80), 1, (12, 1, 2), "CPC: wetter along the Gulf coast in winter"),
    ("Central America", (7, 18, -92, -77), -1, (6, 7, 8), "CPC JJA map: dry and warm over Central America"),
    ("southern China", (20, 30, 105, 122), 1, (11, 12, 1, 2, 3, 4), "WWF00: wet from late fall to the following spring; CPC DJF map wet"),
)


def _grid_axes(model: dict):
    g = model["grid"]
    return g["lat0"] + g["step_deg"] * np.arange(g["nlat"]), g["lon0"] + g["step_deg"] * np.arange(g["nlon"])


def _full(model: dict, kind: str, m: int, field: str, scale: float) -> np.ndarray:
    g = model["grid"]
    out = np.full(g["nlat"] * g["nlon"], np.nan)
    vals = model["enso"][kind][field][m - 1]
    out[np.array(model["cells"])] = [np.nan if v is None else v / scale for v in vals]
    return out


def predict(model: dict, kind: str, m: int, oni: float) -> tuple[np.ndarray, np.ndarray]:
    """(SPI grid at this ONI capped to the fit range, skill grid) from the stored coefficients."""
    lo, hi = model["enso_rule"]["oni_range"][kind][m - 1]
    x = min(max(oni, lo), hi)
    return _full(model, kind, m, "c0", 1000) + _full(model, kind, m, "c1", 1000) * x, _full(model, kind, m, "skill", 1)


def _corr(a, b) -> float | None:
    ok = np.isfinite(a) & np.isfinite(b)
    return round(float(np.corrcoef(a[ok], b[ok])[0, 1]), 3) if ok.sum() > 10 else None


def validate(model: dict, ctx: dict) -> dict:
    form, oni = ctx["form"], ctx["oni"]
    hind = []
    for label, y0 in EVENTS:
        for dy, m in EVENT_MONTHS:
            y = y0 + dy
            r, xs, yrs = ctx["fits"][(form, "m1", m)]
            OK = ctx["spi_par"][("m1", m)][3]
            obs = ctx["spi_obs"][("m1", m)].get(y)
            x = oni.get((y, m))
            if obs is None or x is None:
                continue
            pred = np.full(ctx["ncell"], np.nan)
            if y in yrs:
                pred[OK] = r["loo"][list(yrs).index(y)]
                how = "leave-one-out"
            else:
                pred, _ = predict(model, "m1", m, x)
                how = "out of sample" if y > model["enso_rule"]["fit_years"][1] else "in fit"
            skill = _full(model, "m1", m, "skill", 1) == 1
            hind.append({"event": label, "month": f"{y}-{m:02d}", "oni": x, "how": how,
                         "r_skilful": _corr(np.where(skill, pred, np.nan), obs),
                         "r_all": _corr(pred, obs), "n_skilful": int(skill.sum())})

    share = {}
    for kind in ("m1", "m3"):
        share[kind] = []
        for m in range(1, 13):
            sk = [v for v in model["enso"][kind]["skill"][m - 1] if v is not None]
            share[kind].append(round(sum(sk) / len(sk), 3))

    lats, lons = _grid_axes(model)
    LAT, LON = np.meshgrid(lats, lons, indexing="ij")
    regions = []
    for name, (s, n, w, e), sign, months, src in REGIONS:
        box = ((LAT >= s) & (LAT <= n) & (LON >= w) & (LON <= e)).ravel()
        per_month, per_m3 = [], []
        for kind, dest in (("m1", per_month), ("m3", per_m3)):
            for m in range(1, 13):
                spi, sk = predict(model, kind, m, TEST_ONI)
                v = spi[box]
                ok = np.isfinite(v)
                dest.append({"m": m, "spi": round(float(v[ok].mean()), 2) if ok.any() else None,
                             "cells": int(ok.sum()), "skilful": int(np.nansum(sk[box] == 1))})
        season = [p["spi"] for p in per_month if p["m"] in months and p["spi"] is not None]
        mean = round(float(np.mean(season)), 2) if season else None
        agree = [p["m"] for p in per_month if p["m"] in months and p["spi"] is not None and np.sign(p["spi"]) == sign]
        regions.append({"region": name, "box": [s, n, w, e], "expected": "wetter" if sign > 0 else "drier",
                        "months": list(months), "source": src, "season_mean_spi": mean,
                        "months_agreeing": agree,
                        "verdict": None if mean is None else ("agrees" if np.sign(mean) == sign else "DISAGREES"),
                        "per_month": per_month, "per_m3_ending": per_m3})
    return {"hindcast": hind, "skilful_share": share, "regions_at_oni_2_5": regions,
            "validation_notes": [
                "Hindcast r: Pearson correlation over land cells of predicted and observed 1-month SPI. "
                "1982-83, 1997-98 and 2015-16 use the leave-one-out fit without that year; 2023-24 is after "
                "the fit. r_skilful uses the cells the full fit flags skilful; r_all every fitted cell.",
                f"Regions: model SPI at ONI +{TEST_ONI} capped at each window's fit maximum, mean of the "
                "region's fitted land cells, per calendar month: per_month for 1-month windows, per_m3_ending "
                "for the 3-month window ending in that month; skilful = cells flagged skilful.",
            ]}


def report(model: dict) -> None:
    meta = model["meta"]
    for h in meta["hindcast"]:
        print(f"[hindcast] {h['event']} {h['month']} ONI {h['oni']:+.1f} ({h['how']}): r skilful "
              f"{h['r_skilful']} (n {h['n_skilful']}), r all {h['r_all']}")
    for kind, v in meta["skilful_share"].items():
        print(f"[skill] {kind} share of fitted land cells skilful Jan..Dec: {v}")
    for r in meta["regions_at_oni_2_5"]:
        pm = " ".join(f"{p['m']}:{p['spi']:+.2f}" if p["spi"] is not None else f"{p['m']}:--" for p in r["per_month"])
        print(f"[region] {r['region']}: expected {r['expected']} in {r['months']}, season mean {r['season_mean_spi']} "
              f"-> {r['verdict']} (agreeing months {r['months_agreeing']}) | {pm}")
        print("         m1 skilful cells: " + " ".join(f"{p['m']}:{p['skilful']}/{p['cells']}" for p in r["per_month"]))
        print("         m3 ending:        " + " ".join(
            f"{p['m']}:{p['spi']:+.2f}({p['skilful']}/{p['cells']})" if p["spi"] is not None else f"{p['m']}:--"
            for p in r["per_m3_ending"]))
