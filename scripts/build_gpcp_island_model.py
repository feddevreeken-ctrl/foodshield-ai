#!/usr/bin/env python3
"""build_gpcp_island_model.py -- SPI fits and an El Nino rain fit on GPCP for inhabited land PREC/L calls sea.

Why. The Outlook reads each forecast month as a drought index on NOAA PREC/L (scripts/precl_model.py,
data/ref/precl_enso_model.json.gz). PREC/L is a land product on a 2.5-degree grid, so a cell whose only
land is a few small islands is sea there, and the Outlook left it blank. A check of the map against 415
official forecasts across Asia and Oceania (2026-09-29) found 22 of its 29 blank checks were such islands
(Samoa, Tonga, Tarawa, Kiritimati, Chuuk, Palau, the Marianas). GPCP v2.3 (satellite and gauges, land and
sea, monthly since January 1979; NCEI's Climate Data Record files) covers them. This builder fits those cells on GPCP in the same schema
as the PREC/L file, so precl_model.fit, spi_from_total, total_from_spi and enso_predict read it with
path=, and outlook_rain_spi reads the NMME rain change there on GPCP instead of PREC/L.

Island cells, fixed by a rule that looks at no forecast: every cell of the rain grid (lat0 -56.25,
lon0 -178.75, 2.5 degrees, 52 x 144) where PREC/L has no value in any month (sea on PREC/L's own land
mask) and whose 2.5-degree box holds at least one populated place of GeoNames' cities500 extract (every
place of 500 people or more, plus every seat of an administrative division down to the fourth level;
CC BY 4.0). A cell is kept when GPCP gives it a fit in at least one window. The rule also takes in strips
of continental coast that PREC/L's mask calls sea (Sarasota, Florida, for one): land the PREC/L map
leaves blank in the same way. One point per cell for the map: its most populous place in that file
(the first one listed on a tie), with GeoNames' name and coordinates.

1. SPI fits (1991-2020), windows m1 and m3 ending in each calendar month, on GPCP's cell mean (land and
   sea together, as refresh_seasonal_outlook reads the NMME rain change over the whole cell), with the
   rule and code of build_precl_enso_model.py (fit_gamma, spi_np: zero_mm 0.5, arid under 0.3 mm a day,
   at least 2/3 of years wet). Its gauge-dropout rule is left out: that is a PREC/L gauge problem.
2. El Nino fit: each window's SPI regressed on CPC's ONI (one decimal, the season centred on the window's
   middle month, as there) over 1979-2020, since GPCP starts in January 1979 (m3 windows ending January
   and February start in 1980). The form the PREC/L model chose ("all" years), leave-one-out
   cross-validated correlation, skill = p < 0.05 and CV r > 0.2. oni_range = the lowest and highest ONI
   of each window's fit years, so a forecast beyond the record is read at the record.
3. stats: the 1991-2020 sample mean and SD (n - 1) of each calendar month's total, tenths of a mm (the
   encoding of precl_month_stats_1991_2020.json.gz), for outlook_rain_spi._moments.

Storage (data/ref/gpcp_island_model.json.gz): the keys of precl_enso_model.json.gz (grid, cells, kinds,
spi_rule, spi, enso, enso_rule, sources, meta) in the same encodings, plus stats {mean, sd},
points [{cell, lat, lon, name}] and island_rule.

GPCP source: NCEI's archive, one netCDF-4 file a month, read without an HDF5 library (_h5_contiguous).
PSL's copy (the same GPCP v2.3 on PSL's OPeNDAP server) is read too, only as a check: on 2026-09-29 it
agreed with NCEI's files in every month but February and March 2019, and PSL's March 2019 matched PREC/L
over land far worse (the numbers are in sources.gpcp_check), so NCEI's files are used.

Usage (local, once; numpy and scipy; the GPCP files, PSL's copy and the GeoNames file are cached in
$FOODSHIELD_CACHE or /tmp/foodshield-sst-cache, like PREC/L):
    python3 scripts/build_gpcp_island_model.py
"""
from __future__ import annotations

import gzip
import io
import json
import math
import re
import struct
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_precl_enso_model as B  # noqa: E402  (the PREC/L build's fit rules and code)
import precl_model as P  # noqa: E402
from _common import http_get  # noqa: E402
from build_sst_composites import CACHE, ONI_URL, UA, _dap_grid, load_oni, load_precl  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "ref" / "gpcp_island_model.json.gz"
GPCP_FILE = "https://downloads.psl.noaa.gov/Datasets/gpcp/precip.mon.mean.nc"
GPCP_DAP = "https://psl.noaa.gov/thredds/dodsC/Datasets/gpcp/precip.mon.mean.nc"
GPCP_NCEI = "https://www.ncei.noaa.gov/data/global-precipitation-climatology-project-gpcp-monthly/access"
PLACES_URL = "https://download.geonames.org/export/dump/cities500.zip"
GPCP_YEARS, FIT_YEARS = (1979, 2020), (1979, 2020)


def _rain_grid(pr: np.ndarray, lats: np.ndarray, lons: np.ndarray):
    """(months x lat x lon) on GPCP's global 2.5-degree grid -> the rain grid's rows (south to north, picked by
    latitude: GPCP has PREC/L's cell centres) and columns (-178.75..178.75); values outside 0..1000 -> NaN."""
    rows = np.flatnonzero((lats >= -56.25) & (lats <= 71.25))
    rows = rows[np.argsort(lats[rows])]
    lats, pr = lats[rows], pr[:, rows, :].astype(float)
    pr[~((pr >= 0) & (pr < 1000))] = np.nan
    lons = np.where(lons > 180, lons - 360, lons)
    order = np.argsort(lons)
    lons, pr = lons[order], pr[:, :, order]
    if len(lats) != 52 or lats[0] != -56.25 or lons[0] != -178.75 or len(lons) != 144:
        raise RuntimeError(f"unexpected GPCP grid {lats[0]}..{lats[-1]} x {lons[0]}..{lons[-1]}")
    return pr, lats, lons


def _h5_contiguous(raw: bytes, name: str, count: int) -> np.ndarray:
    """A contiguous, uncompressed float32 variable of a netCDF-4 (HDF5) file, without an HDF5 library: the
    link named `name` gives its object header, whose data layout message (version 3, contiguous) gives the
    address. NCEI's GPCP files store precip, latitude and longitude that way; load_gpcp checks every month
    against PSL's copy of the same data."""
    m = re.search(re.escape(bytes([len(name)]) + name.encode()) + b"(.{8})", raw, re.S)
    if not m:
        raise RuntimeError(f"no HDF5 link named {name}")
    todo, order = [(struct.unpack("<Q", m[1])[0], 0)], 0
    while todo:
        p, length = todo.pop()
        if raw[p:p + 4] == b"OHDR":
            flags = raw[p + 5]
            q = p + 6 + (16 if flags & 0x20 else 0) + (4 if flags & 0x10 else 0)
            nsz = 1 << (flags & 3)
            end = q + nsz + int.from_bytes(raw[q:q + nsz], "little")
            q, order = q + nsz, (2 if flags & 0x04 else 0)
        elif raw[p:p + 4] == b"OCHK":
            q, end = p + 4, p + length - 4
        else:
            raise RuntimeError(f"{name}: no HDF5 object header at {p}")
        while q + 4 <= end:
            t, sz = raw[q], int.from_bytes(raw[q + 1:q + 3], "little")
            body = raw[q + 4 + order:q + 4 + order + sz]
            if t == 0x08 and body[:2] == b"\x03\x01":
                addr, size = struct.unpack("<QQ", body[2:18])
                if size != 4 * count:
                    raise RuntimeError(f"{name}: {size} bytes, expected {4 * count}")
                return np.frombuffer(raw, "<f4", count, addr).copy()
            if t == 0x10:                       # continuation: (address, length) of the next block
                todo.append(struct.unpack("<QQ", body[:16]))
            q += 4 + order + sz
    raise RuntimeError(f"{name}: no contiguous data layout message")


def load_gpcp():
    """{(year, month): 2-D rain rate in mm/day} for GPCP_YEARS on the rain grid, from NCEI's GPCP v2.3 archive
    (one file per month; the newest version where a month has several), and the files used."""
    folder = CACHE / "gpcp_v23_ncei"
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for y in range(GPCP_YEARS[0], GPCP_YEARS[1] + 1):
        pat = rf"gpcp_v02r03_monthly_d{y}\d\d_c\d{{8}}\.nc"          # name[25:27] is the month
        have = sorted(p.name for p in folder.glob(f"gpcp_v02r03_monthly_d{y}??_c*.nc"))
        if len({n[25:27] for n in have}) < 12:
            have = sorted(set(re.findall(pat, http_get(f"{GPCP_NCEI}/{y}/", timeout=120, headers=UA, retries=3).text)))
        newest = {n[25:27]: n for n in have}       # sorted, so the last kept is the newest creation date
        if sorted(newest) != [f"{m:02d}" for m in range(1, 13)]:
            raise RuntimeError(f"NCEI GPCP {y}: months {sorted(newest)}")
        for n in newest.values():
            if not (folder / n).exists():
                (folder / n).write_bytes(http_get(f"{GPCP_NCEI}/{y}/{n}", timeout=120, headers=UA, retries=3).content)
        names += list(newest.values())
    raws = [(folder / n).read_bytes() for n in names]
    lats, lons = _h5_contiguous(raws[0], "latitude", 72).astype(float), _h5_contiguous(raws[0], "longitude", 144).astype(float)
    pr = np.array([_h5_contiguous(r, "precip", 72 * 144).reshape(72, 144) for r in raws])
    pr, lats, lons = _rain_grid(pr, lats, lons)
    keys = [(y, m) for y in range(GPCP_YEARS[0], GPCP_YEARS[1] + 1) for m in range(1, 13)]
    return dict(zip(keys, pr)), lats, lons, names


def psl_differences(rain: dict, precl: dict) -> list[dict]:
    """Months where PSL's copy of GPCP v2.3 (OPeNDAP) differs from NCEI's files by more than 0.001 mm a day in
    any rain-grid cell, with each version's correlation with PREC/L over land that month. Every other month
    agreeing also checks _h5_contiguous."""
    nt = 12 * (GPCP_YEARS[1] - GPCP_YEARS[0] + 1)
    path = CACHE / f"gpcp_v23_psl_{GPCP_YEARS[0]}-{GPCP_YEARS[1]}.npz"
    if not path.exists():
        parts = [_dap_grid(f"{GPCP_DAP}.dods?precip[{a}:1:{min(a + 168, nt) - 1}][0:1:71][0:1:143]")
                 for a in range(0, nt, 168)]
        np.savez_compressed(path, precip=np.concatenate([p[0] for p in parts]),
                            time=np.concatenate([p[1] for p in parts]), lat=parts[0][2], lon=parts[0][3])
    z = np.load(path)
    pr, _, _ = _rain_grid(z["precip"], z["lat"].astype(float), z["lon"].astype(float))
    t0 = [datetime(1800, 1, 1) + timedelta(days=float(d)) for d in z["time"]]
    out = []
    for k, d in enumerate(t0):
        a, b = rain[(d.year, d.month)], pr[k]
        if np.nanmax(np.abs(a - b)) > 0.001:
            p = precl[(d.year, d.month)]
            ok = np.isfinite(p)
            out.append({"month": f"{d.year}-{d.month:02d}", "max_abs_mm_day": round(float(np.nanmax(np.abs(a - b))), 2),
                        "corr_with_precl_on_land": {"ncei": round(float(np.corrcoef(a[ok], p[ok])[0, 1]), 3),
                                                    "psl": round(float(np.corrcoef(b[ok], p[ok])[0, 1]), 3)}})
    return out


def load_places() -> tuple[list, str]:
    """[(name, lat, lon, population)] of GeoNames cities500, and the date GeoNames wrote the file."""
    path = CACHE / "geonames_cities500.zip"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(http_get(PLACES_URL, timeout=300, headers=UA, retries=3).content)
    rows = []
    with zipfile.ZipFile(path) as z:
        stamp = "%04d-%02d-%02d" % z.getinfo("cities500.txt").date_time[:3]
        with z.open("cities500.txt") as fh:
            for line in io.TextIOWrapper(fh, encoding="utf-8"):
                f = line.rstrip("\n").split("\t")
                rows.append((f[1], float(f[4]), float(f[5]), int(f[14] or 0)))
    return rows, stamp


def cell_of(lat: float, lon: float, lat0: float, lon0: float, nlat: int, nlon: int) -> int | None:
    """Rain-grid cell whose 2.5-degree box holds the point (a point on an edge goes north or east)."""
    i = math.floor((lat - lat0 + 1.25) / 2.5)
    j = math.floor(((lon - lon0 + 1.25) % 360) / 2.5) % nlon
    return i * nlon + j if 0 <= i < nlat else None


def build() -> dict:
    rain, lats, lons, files = load_gpcp()
    precl, plats, plons = load_precl()
    if not (np.array_equal(lats, plats) and np.array_equal(lons, plons)):
        raise RuntimeError("GPCP and PREC/L rows or columns differ")
    psl = psl_differences(rain, precl)
    nlat, nlon = len(lats), len(lons)
    sea = ~np.isfinite(np.array([precl[k] for k in sorted(precl)])).any(0).ravel()
    places, stamp = load_places()
    by_cell: dict[int, list] = {}
    for p in places:
        c = cell_of(p[1], p[2], lats[0], lons[0], nlat, nlon)
        if c is not None and sea[c]:
            by_cell.setdefault(c, []).append(p)
    cand = np.array(sorted(by_cell))
    oni = {(y, m): v for y, m, v in load_oni()}

    spi_par, spi_obs = {}, {}
    for kind, L in B.KINDS.items():
        for m in range(1, 13):
            days = B.MONTH_DAYS[[(m - 1 - k) % 12 for k in range(L)]].sum()
            X = np.array([B.window_total(rain, y, m, L).ravel()[cand] for y in range(B.CLIM[0], B.CLIM[1] + 1)])
            if not np.isfinite(X).all():
                raise RuntimeError(f"GPCP has gaps in island cells ({kind}/{m})")
            a, b, q, ok = B.fit_gamma(X, days)
            spi_par[(kind, m)] = (a, b, q, ok)
            obs = {}
            for y in range(FIT_YEARS[0], FIT_YEARS[1] + 1):
                T = B.window_total(rain, y, m, L)
                if T is not None:
                    obs[y] = B.spi_np(T.ravel()[cand], a, b, q)
            spi_obs[(kind, m)] = obs

    anyfit = np.zeros(len(cand), bool)
    for (_, _, _, ok) in spi_par.values():
        anyfit |= ok
    keep = np.flatnonzero(anyfit)
    cells = [int(c) for c in cand[keep]]

    def enc(v, s, ok):
        return [int(round(s * t)) if o else None for t, o in zip(v, ok)]

    spi_out, enso_out, oni_range, years_used = {}, {}, {}, {}
    for kind in B.KINDS:
        sa, sb, sq, lo_hi, yrs_k = [], [], [], [], []
        ec = {k: [] for k in ("c0", "c1", "sd", "n", "p", "cvr", "skill")}
        for m in range(1, 13):
            a, b, q, ok = spi_par[(kind, m)]
            yrs = sorted(spi_obs[(kind, m)])
            x = np.array([oni[B.centre(y, m, kind)] for y in yrs])
            full = {k: np.full(len(cand), np.nan) for k in ("a", "b", "sd", "p", "cvr")}
            if ok.any():
                r = B.ols_loo(x, np.array([spi_obs[(kind, m)][y][ok] for y in yrs]))
                for k in full:
                    full[k][ok] = r[k]
            okk = ok[keep]
            sa.append(enc(np.log(np.where(ok, a, 1))[keep], B.LN_SCALE, okk))
            sb.append(enc(np.log(np.where(ok, b, 1))[keep], B.LN_SCALE, okk))
            sq.append(enc(np.where(ok, q, 0)[keep], 1000, okk))
            ec["c0"].append(enc(full["a"][keep], 1000, okk))
            ec["c1"].append(enc(full["b"][keep], 1000, okk))
            ec["sd"].append(enc(full["sd"][keep], 1000, okk))
            ec["n"].append([len(yrs) if o else None for o in okk])
            ec["p"].append(enc(full["p"][keep], 10000, okk))
            ec["cvr"].append(enc(full["cvr"][keep], 1000, okk))
            with np.errstate(invalid="ignore"):
                sk = (full["p"] < B.SKILL_P) & (full["cvr"] > B.SKILL_R)
            ec["skill"].append([int(s) if o else None for s, o in zip(sk[keep], okk)])
            lo_hi.append([float(x.min()), float(x.max())])
            yrs_k.append([yrs[0], yrs[-1]])
        spi_out[kind] = {"a": sa, "b": sb, "q": sq}
        enso_out[kind] = ec
        oni_range[kind] = lo_hi
        years_used[kind] = yrs_k

    mean, sd = [], []
    for m in range(1, 13):
        X = np.array([rain[(y, m)].ravel()[cells] * B.MONTH_DAYS[m - 1] for y in range(B.CLIM[0], B.CLIM[1] + 1)])
        mean.append([int(round(10 * v)) for v in X.mean(0)])
        sd.append([int(round(10 * v)) for v in X.std(0, ddof=1)])

    points = []
    for c in cells:
        top = max(by_cell[c], key=lambda p: p[3])     # max keeps the first on a tie
        points.append({"cell": c, "lat": round(top[1], 3), "lon": round(top[2], 3), "name": top[0]})
    dropped = [{"cell": int(c), "place": max(by_cell[int(c)], key=lambda p: p[3])[0]}
               for c in cand[~anyfit]]
    share = {k: [round(float(np.mean([s for s in row if s is not None])), 3) if any(s is not None for s in row) else None
                 for row in enso_out[k]["skill"]] for k in B.KINDS}
    return {
        "grid": {"lat0": float(lats[0]), "lon0": float(lons[0]), "step_deg": 2.5, "nlat": nlat, "nlon": nlon,
                 "encoding": "cells = row-major index from the southern edge; tables [end month - 1][cell position]"},
        "cells": cells,
        "kinds": {"m1": "the calendar month's total", "m3": "the total of the calendar month and the two before it"},
        "spi_rule": {"clim": list(B.CLIM), "zero_mm": B.ZERO_MM, "spi_clip": B.SPI_CLIP, "ln_scale": B.LN_SCALE,
                     "min_nonzero": round(B.MIN_NONZERO, 4), "arid_mm_day": B.ARID_MM_DAY,
                     "month_days": [int(d) for d in B.MONTH_DAYS],
                     "text": "total under zero_mm with q > 0: H = q/2; else H = q + (1-q) P(alpha, total/beta); "
                             "H clipped to Phi(+-spi_clip); SPI = Phi^-1(H). beta in mm. Totals are GPCP's cell "
                             "mean, land and sea together."},
        "spi": spi_out,
        "enso": enso_out,
        "enso_rule": {
            "form": "all", "fit_years": list(FIT_YEARS), "fit_years_by_window": years_used,
            "predictor": "CPC ONI of the 3-month season centred on the window's middle month (m1: the month)",
            "oni_range": oni_range,
            "oni_range_note": "min and max ONI of each window's fit years [kind][end month - 1]; predictions cap "
                              "the predictor to this range",
            "scales": {"c0": 1000, "c1": 1000, "sd": 1000, "p": 10000, "cvr": 1000},
            "skill_rule": f"p < {B.SKILL_P} and leave-one-out CV correlation > {B.SKILL_R}",
        },
        "stats": {"mean": mean, "sd": sd,
                  "encoding": "mean and sd: [calendar month - 1][cell position], tenths of a mm of the month's total "
                              "(mm a day x nominal days, February 28), 1991-2020; sd with n - 1"},
        "points": points,
        "island_rule": {
            "text": "A rain-grid cell where NOAA PREC/L has no value in any month (sea on PREC/L's own land mask) "
                    "whose 2.5-degree box holds at least one populated place of GeoNames cities500 (every place "
                    "of 500 people or more, and every seat of an administrative division down to the fourth "
                    "level). Kept when GPCP gives it a fit in at least one window. Fixed without looking at "
                    "any forecast. It also takes in strips of continental coast the PREC/L mask calls sea.",
            "point": "the cell's most populous place in that file, with its name and coordinates",
            "places_url": PLACES_URL, "places_file_date": stamp,
            "cells_by_rule": len(cand), "cells_kept": len(cells), "dropped_no_fit": dropped},
        "sources": {"gpcp": GPCP_NCEI, "gpcp_version": "GPCP v2.3 monthly CDR, 2.5 degree, NCEI's archive",
                    "gpcp_months": f"{GPCP_YEARS[0]}-01 to {GPCP_YEARS[1]}-12",
                    "gpcp_files": f"{files[0]} .. {files[-1]} ({len(files)})",
                    "gpcp_check": {"against": GPCP_FILE + " (PSL's copy, read through " + GPCP_DAP + ")",
                                   "months_that_differ": psl,
                                   "text": "Every month agrees to 0.001 mm a day except those listed. There NCEI's "
                                           "files are used; their correlation with PREC/L over land is given for both."},
                    "oni": ONI_URL,
                    "precl_mask": B.PRECL_FILE, "places": PLACES_URL, "built": date.today().isoformat()},
        "meta": {"skilful_share": share},
    }


def check(model: dict) -> float:
    """Max |SPI| gap between precl_model on the stored (rounded) file and scipy on the same stored fits, at every
    kept cell, window and 1991-2020 year's total, so the pure-Python reader is checked on this file."""
    from scipy import stats
    rain = load_gpcp()[0]
    lo = stats.norm.cdf(-B.SPI_CLIP)
    worst = 0.0
    for kind, L in B.KINDS.items():
        for m in range(1, 13):
            for y in range(B.CLIM[0], B.CLIM[1] + 1):
                T = B.window_total(rain, y, m, L).ravel()
                for c in model["cells"]:
                    f = P.fit(c, kind, m, OUT)
                    if f is None:
                        continue
                    al, be, q = f
                    h = q / 2 if (T[c] < B.ZERO_MM and q > 0) else q + (1 - q) * stats.gamma.cdf(T[c], al, scale=be)
                    ref = float(stats.norm.ppf(np.clip(h, lo, 1 - lo)))
                    worst = max(worst, abs(P.spi_from_total(c, kind, m, float(T[c]), OUT) - ref))
    return worst


def main() -> int:
    model = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(model, fh, separators=(",", ":"), ensure_ascii=False)
    worst = check(model)
    model["meta"]["reader_max_abs_spi_gap"] = round(worst, 6)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(model, fh, separators=(",", ":"), ensure_ascii=False)
    r = model["island_rule"]
    print(f"[rule] {r['cells_by_rule']} cells sea on PREC/L with a GeoNames cities500 place "
          f"(file of {r['places_file_date']}); {r['cells_kept']} with a GPCP fit; dropped {r['dropped_no_fit']}")
    print(f"[gpcp] PSL's copy differs from NCEI's files in: {model['sources']['gpcp_check']['months_that_differ']}")
    print(f"[skill] share of cells with a skilful El Nino fit, m1 by month: {model['meta']['skilful_share']['m1']}")
    print(f"[check] precl_model on the stored file against scipy: max |SPI gap| {worst:.2e}")
    for p in model["points"]:
        print(f"  {p['cell']:5d} {p['lat']:8.3f} {p['lon']:9.3f} {p['name']}")
    print(f"[OK] {OUT} {OUT.stat().st_size / 1e3:.0f} kB, {len(model['cells'])} cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
