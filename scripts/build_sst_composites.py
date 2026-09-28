#!/usr/bin/env python3
"""build_sst_composites.py -- sea and rain in past El Nino years, by strength and season.

Feeds the strength slider and the season steps on the El Nino Ocean map. This
is NOT a model run and NOT a forecast. For each CPC strength class (weak,
moderate, strong, very strong) and each of four seasons around the winter peak
it averages what was OBSERVED in the past El Nino winters of that class: the
sea-surface temperature anomaly over the ocean and the percent change in rain
over land. Each class/season ships as integer grids the page's canvas can paint.

Seasons, for the winter labelled "Y-(Y+1)":
  JJA = Jun-Aug of Y (developing), SON = Sep-Nov of Y (building),
  DJF = Dec Y to Feb Y+1 (peak), MAM = Mar-May of Y+1 (decaying).

Data:
  * SST: NOAA ERSST v5 monthly on its native 2-degree grid, read through the
    CoastWatch ERDDAP griddap endpoint as NetCDF-3 (scipy reads it; no netCDF4
    needed). Decade chunks are cached in $FOODSHIELD_CACHE or
    /tmp/foodshield-sst-cache and never refetched once cached. The last chunk
    ends at May after the latest complete winter (or the last ERSST month
    before that), so it is only refetched when that end date moves.
  * Rain: NOAA PSL PREC/L monthly land precipitation (gauge based, 1948 on),
    2.5-degree grid. The file is PRECL_FILE on downloads.psl.noaa.gov, but it
    is netCDF4 (HDF5), which scipy cannot read, so the same file is read
    through PSL's THREDDS OPeNDAP server as plain DAP2 binary and cached as
    NetCDF-3, one cache file per record length (a new month means a refetch).
  * Anomalies are against 1991-2020 monthly climatologies computed here from
    the same files. Rain is the percent change against the 1991-2020 mean of
    the same three calendar months (the mean of the winters' percents equals
    the percent of their mean, since the base is shared). Sea cells and cells
    whose 1991-2020 seasonal mean is under 0.3 mm/day (arid) are null. The
    agreement grid counts the class's winters whose change had the same sign
    as the composite.
  * Events and classes: CPC oni.ascii.txt. ONI values are rounded to one
    decimal as in CPC's table. An episode is a run of at least five
    consecutive overlapping seasons with ONI >= +0.5 (CPC's rule). An El Nino
    winter is a DJF inside an episode. Its class is the episode's peak ONI in
    the July-June year around that winter, so a two-winter episode such as
    1986-88 gives two winters with their own peaks (CPC's bins: weak +0.5 to
    +0.9, moderate +1.0 to +1.4, strong +1.5 to +1.9, very strong >= +2.0).

Every winter is measured against 1991-2020, so older winters sit on a cooler
baseline and their sea anomalies read cooler than they were against the
climate of their day. (A trend-removed "relative" SST version shipped until
2026-09-28; the page never used it, so it was dropped to keep the file small.)
"""
from __future__ import annotations

import io
import json
import os
import re
import struct
import sys
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
from scipy.io import netcdf_file

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402

ERDDAP = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"
DS = "nceiErsstv5_LonPM180"
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
PRECL_FILE = "https://downloads.psl.noaa.gov/Datasets/precl/2.5deg/precip.mon.mean.2.5x2.5.nc"
PRECL_DAP = "https://psl.noaa.gov/thredds/dodsC/Datasets/precl/2.5deg/precip.mon.mean.2.5x2.5.nc"
UA = {"User-Agent": "FoodShield-AI data refresh (github.com/feddevreeken-ctrl/foodshield-ai)"}
# CPC answers browser user agents more reliably (same choice as refresh_enso_indices).
CPC_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
CACHE = Path(os.environ.get("FOODSHIELD_CACHE") or "/tmp/foodshield-sst-cache")

# ERSST centres run -88..88 by 2; 84 is the last row inside the Web Mercator
# extent the page paints (Leaflet clips at 85.05).
LAT0, LAT1, LON0, LON1, STEP = -84.0, 84.0, -180.0, 178.0, 2.0
# PREC/L centres run 88.75N..88.75S by 2.5. Rows 7..58 keep 71.25N (North Cape,
# north of all farmland) to 56.25S (Tierra del Fuego). The Arctic rows above and
# Antarctica are left out to keep the file under ~1.8 MB.
RAIN_ROWS, RAIN_STEP, ARID_MM_DAY = (7, 58), 2.5, 0.3
CLIM = (1991, 2020)
FIRST_WINTER = 1950  # first DJF in CPC's ONI table
ONI_SEASONS = "DJF JFM FMA MAM AMJ MJJ JJA JAS ASO SON OND NDJ".split()
# key, label, stage, months as (offset from the winter's January year, month)
SEASONS = (("JJA", "Jun–Aug", "developing", ((-1, 6), (-1, 7), (-1, 8))),
           ("SON", "Sep–Nov", "building", ((-1, 9), (-1, 10), (-1, 11))),
           ("DJF", "Dec–Feb", "peak", ((-1, 12), (0, 1), (0, 2))),
           ("MAM", "Mar–May", "decaying", ((0, 3), (0, 4), (0, 5))))
# key, label, CPC range text, lower bound (inclusive), upper bound (exclusive)
CLASSES = (("weak", "Weak El Niño", "+0.5 to +0.9", 0.5, 1.0),
           ("moderate", "Moderate El Niño", "+1.0 to +1.4", 1.0, 1.5),
           ("strong", "Strong El Niño", "+1.5 to +1.9", 1.5, 2.0),
           ("very_strong", "Very strong El Niño", "+2.0 and above", 2.0, 99.0))
# (lat_s, lat_n, lon_w, lon_e), inclusive on cell centres
BOXES = {"nino34": (-5, 5, -170, -120), "nino12": (-10, 0, -90, -80)}
# Printed sanity check on the very strong class: textbook El Nino rain signs.
# (region, season, lat_s, lat_n, lon_w, lon_e, expected sign)
RAIN_CHECKS = (("southern Africa", "DJF", -35, -15, 15, 40, -1),
               ("northern South America", "DJF", 0, 12, -75, -50, -1),
               ("NE Brazil (its Mar-May rains)", "MAM", -12, -2, -45, -35, -1),
               ("southern Brazil/Uruguay", "DJF", -35, -25, -60, -48, 1),
               ("US Gulf coast", "DJF", 27, 33, -98, -80, 1),
               ("Indonesia", "JJA", -10, 5, 95, 141, -1),
               ("Indonesia", "SON", -10, 5, 95, 141, -1),
               ("eastern Australia", "JJA", -38, -15, 140, 154, -1),
               ("eastern Australia", "SON", -38, -15, 140, 154, -1),
               ("India", "JJA", 8, 30, 70, 88, -1),
               ("Horn of Africa", "SON", -5, 10, 35, 51, 1))


def _r1(text: str) -> float:
    return float(Decimal(text).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def load_oni() -> list[tuple[int, int, float]]:
    """Chronological (year, centre month, ONI to one decimal). DJF 1950 -> (1950, 1)."""
    text = http_get(ONI_URL, timeout=60, headers=CPC_UA, retries=3).text
    rows = []
    for line in text.splitlines():
        f = line.split()
        if len(f) == 4 and f[0] in ONI_SEASONS:
            try:
                rows.append((int(f[1]), ONI_SEASONS.index(f[0]) + 1, _r1(f[3])))
            except (ValueError, ArithmeticError):
                continue
    if len(rows) < 800:
        raise RuntimeError(f"ONI parse got {len(rows)} seasons -- feed shape changed")
    for a, b in zip(rows, rows[1:]):
        if (a[0] * 12 + a[1] + 1) != (b[0] * 12 + b[1]):
            raise RuntimeError(f"ONI seasons not consecutive at {a} -> {b}")
    return rows


def el_nino_winters(rows) -> list[dict]:
    runs, cur = [], []
    for r in rows + [(0, 0, -9.0)]:  # sentinel closes the last run
        if r[2] >= 0.5:
            cur.append(r)
            continue
        if len(cur) >= 5:
            runs.append(cur)
        cur = []
    out = []
    for run in runs:
        for y, m, v in run:
            if m != 1 or y < FIRST_WINTER:
                continue
            peak = max(x[2] for x in run if (y - 1, 7) <= (x[0], x[1]) <= (y, 6))
            out.append({"year": y, "label": f"{y - 1}-{y % 100:02d}", "peak_oni": peak, "djf_oni": v})
    return out


def _chunk(t0: str, t1: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"ersstv5_{t0}_{t1}.nc"
    if p.exists() and p.stat().st_size > 100_000:
        return p.read_bytes()
    q = (f"sst%5B({t0}T00:00:00Z):1:({t1}T00:00:00Z)%5D%5B(0.0)%5D"
         f"%5B({LAT0}):1:({LAT1})%5D%5B({LON0}):1:({LON1})%5D")
    b = http_get(f"{ERDDAP}/{DS}.nc?{q}", timeout=300, headers=UA, retries=3).content
    tmp = p.with_suffix(".part")
    tmp.write_bytes(b)
    tmp.replace(p)
    return b


def load_sst(end: tuple[int, int]):
    """{(year, month): 2-D SST array} from Dec 1949 to `end` (year, month)."""
    spans = [("1949-12-01", "1959-12-31")]
    spans += [(f"{y}-01-01", f"{y + 9}-12-31") for y in range(1960, end[0] - 9, 10)]
    start = int(spans[-1][1][:4]) + 1
    spans.append((f"{start}-01-01", f"{end[0]}-{end[1]:02d}-20"))
    fields, lats, lons = {}, None, None
    for t0, t1 in spans:
        nc = netcdf_file(io.BytesIO(_chunk(t0, t1)), mmap=False)
        lats = np.array(nc.variables["latitude"][:], dtype=float)
        lons = np.array(nc.variables["longitude"][:], dtype=float)
        sst = np.array(nc.variables["sst"][:], dtype=float)[:, 0]
        for k, t in enumerate(nc.variables["time"][:]):
            d = datetime.fromtimestamp(float(t), timezone.utc)
            fields[(d.year, d.month)] = sst[k]
        nc.close()
    if lats[0] > lats[-1] or len(lats) != 85 or len(lons) != 180:
        raise RuntimeError(f"unexpected ERSST grid {lats[0]}..{lats[-1]} x {len(lons)}")
    return fields, lats, lons


def _dap_grid(url: str):
    """One DAP2 Grid reply -> (array, time, lat, lon). XDR, big-endian, each length written twice."""
    buf = http_get(url, timeout=300, headers=UA, retries=3).content
    pos = buf.index(b"\nData:\n") + 7
    arrays = []
    for dt in (">f4", ">f8", ">f4", ">f4"):  # the array, then its maps time, lat, lon
        n = struct.unpack(">I", buf[pos:pos + 4])[0]
        pos += 8
        arrays.append(np.frombuffer(buf, dtype=dt, count=n, offset=pos))
        pos += n * np.dtype(dt).itemsize
    precip, t, lat, lon = arrays
    if precip.size != len(t) * len(lat) * len(lon) or pos != len(buf):
        raise RuntimeError(f"PREC/L DAP2 reply did not parse: {url}")
    return precip.reshape(len(t), len(lat), len(lon)), t, lat, lon


def _fetch_precl(nt: int, path: Path) -> None:
    """Pull PREC/L rows RAIN_ROWS as DAP2 binary and cache them as NetCDF-3, PSL's own layout.
    Twenty-year pieces: PSL's server broke off a single 31 MB reply (2026-09-28)."""
    r0, r1 = RAIN_ROWS
    parts = [_dap_grid(f"{PRECL_DAP}.dods?precip[{a}:1:{min(a + 240, nt) - 1}][{r0}:1:{r1}][0:1:143]")
             for a in range(0, nt, 240)]
    precip, t = np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])
    lat, lon = parts[0][2], parts[0][3]
    tmp = path.with_suffix(".part")
    nc = netcdf_file(str(tmp), "w")
    for name, arr in (("time", t), ("lat", lat), ("lon", lon)):
        nc.createDimension(name, len(arr))
        nc.createVariable(name, "f8" if name == "time" else "f4", (name,))[:] = arr
    v = nc.createVariable("precip", "f4", ("time", "lat", "lon"))
    v[:] = precip
    v.units = "mm/day"
    nc.variables["time"].units = "hours since 1800-1-1 00:00:0.0"
    nc.close()
    tmp.replace(path)


def load_precl():
    """{(year, month): 2-D rain rate in mm/day}, rows south to north, lon -178.75..178.75."""
    dds = http_get(f"{PRECL_DAP}.dds", timeout=90, headers=UA, retries=3).text
    m = re.search(r"time\s*=\s*(\d+)\]", dds)
    if not m:
        raise RuntimeError("PREC/L DDS has no time axis -- feed shape changed")
    nt = int(m.group(1))
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"precl_2.5deg_{nt}m_rows{RAIN_ROWS[0]}-{RAIN_ROWS[1]}.nc"
    if not (path.exists() and path.stat().st_size > 1_000_000):
        _fetch_precl(nt, path)
    nc = netcdf_file(str(path), mmap=False)
    t = np.array(nc.variables["time"][:], dtype=float)
    lats = np.array(nc.variables["lat"][:], dtype=float)[::-1]
    lons = np.array(nc.variables["lon"][:], dtype=float)
    pr = np.array(nc.variables["precip"][:], dtype=float)[:, ::-1, :]
    nc.close()
    pr[~((pr >= 0) & (pr < 1000))] = np.nan  # PSL's fill value -9.97e36 marks the sea
    lons = np.where(lons > 180, lons - 360, lons)
    order = np.argsort(lons)
    lons, pr = lons[order], pr[:, :, order]
    if len(lats) != 52 or lats[0] != -56.25 or lons[0] != -178.75 or len(lons) != 144:
        raise RuntimeError(f"unexpected PREC/L grid {lats[0]}..{lats[-1]} x {lons[0]}..{lons[-1]}")
    t0 = datetime(1800, 1, 1)
    fields = {}
    for k, h in enumerate(t):
        d = t0 + timedelta(hours=h)
        fields[(d.year, d.month)] = pr[k]
    return fields, lats, lons


def _monthly_clim(fields, what: str) -> dict:
    clim = {}
    for m in range(1, 13):
        keys = [(y, m) for y in range(CLIM[0], CLIM[1] + 1)]
        missing = [k for k in keys if k not in fields]
        if missing:
            raise RuntimeError(f"{what} climatology months missing: {missing[:5]}")
        clim[m] = np.mean([fields[k] for k in keys], axis=0)
    return clim


def _season_mean(fields, year: int, months):
    keys = [(year + dy, m) for dy, m in months]
    if any(k not in fields for k in keys):
        return None
    return np.mean([fields[k] for k in keys], axis=0)


def _wmean(field, lats, lons, lat_s, lat_n, lon_w=-180, lon_e=180) -> float:
    rows = (lats >= lat_s) & (lats <= lat_n)
    cols = (lons >= lon_w) & (lons <= lon_e)
    sub = field[np.ix_(rows, cols)]
    w = np.broadcast_to(np.cos(np.radians(lats[rows]))[:, None], sub.shape)
    ok = np.isfinite(sub)
    return float((sub[ok] * w[ok]).sum() / w[ok].sum()) if ok.any() else float("nan")


def _encode(field, scale: float = 1.0) -> list:
    return [None if not np.isfinite(v) else int(round(scale * v)) for v in field.ravel()]


def build() -> dict:
    oni = load_oni()
    j = http_get(f"{ERDDAP}/{DS}.json?time%5B(last)%5D", timeout=90, headers=UA, retries=2).json()
    last = datetime.fromisoformat(j["table"]["rows"][0][0].replace("Z", "+00:00"))
    last_year = last.year if last.month >= 2 else last.year - 1
    djf_oni = {y: v for y, m, v in oni if m == 1}
    last_year = min(last_year, max(djf_oni))
    sst, lats, lons = load_sst(min((last.year, last.month), (last_year, 5)))
    rain, rlats, rlons = load_precl()

    sst_clim, rain_clim = _monthly_clim(sst, "ERSST"), _monthly_clim(rain, "PREC/L")
    sst_base, rain_base = {}, {}
    for key, _, _, months in SEASONS:
        sst_base[key] = np.mean([sst_clim[m] for _, m in months], axis=0)
        r = np.mean([rain_clim[m] for _, m in months], axis=0)
        rain_base[key] = np.where(r >= ARID_MM_DAY, r, np.nan)  # arid or sea -> NaN

    winters = [w for w in el_nino_winters(oni) if w["year"] <= last_year]
    per = {}  # (winter year, season) -> (SST anomaly C, rain change %)
    for w in winters:
        for key, _, _, months in SEASONS:
            s, r = _season_mean(sst, w["year"], months), _season_mean(rain, w["year"], months)
            if s is None or r is None:
                continue
            per[(w["year"], key)] = (s - sst_base[key], 100 * (r - rain_base[key]) / rain_base[key])
        if (w["year"], "DJF") not in per:
            raise RuntimeError(f"ERSST or PREC/L months missing for winter {w['label']}")
        w["nino34_c"] = round(_wmean(per[(w["year"], "DJF")][0], lats, lons, *BOXES["nino34"]), 2)

    classes, maps, box_means = [], {}, {}
    for ckey, label, rng, lo, hi in CLASSES:
        ev = [w for w in winters if lo <= w["peak_oni"] < hi]
        if not ev:
            raise RuntimeError(f"no winters in class {ckey}")
        classes.append({
            "key": ckey, "label": label, "oni_range": rng, "n": len(ev),
            "events": [{"label": w["label"], "peak_oni": w["peak_oni"], "djf_oni": w["djf_oni"]} for w in ev],
            "mean_djf_oni": round(sum(w["djf_oni"] for w in ev) / len(ev), 2),
        })
        for skey, *_ in SEASONS:
            got = [per[(w["year"], skey)] for w in ev if (w["year"], skey) in per]
            late = [w["label"] for w in ev if (w["year"], skey) not in per]
            if late:
                print(f"[warn] {ckey}/{skey}: no data yet for {', '.join(late)}; averaged without them")
            sst_c = np.mean([g[0] for g in got], axis=0)
            pct = np.array([g[1] for g in got])
            rain_c = pct.mean(axis=0)
            with np.errstate(invalid="ignore"):
                agree = ((np.sign(pct) == np.sign(rain_c)) & (rain_c != 0)).sum(axis=0)
            ok = sst_c[np.isfinite(sst_c)]
            mk = f"{ckey}/{skey}"
            maps[mk] = {
                "sst": _encode(sst_c, 10),
                "sst_range_c": [round(float(ok.min()), 1), round(float(ok.max()), 1)],
                "rain": _encode(rain_c),
                "rain_agree": [int(a) if np.isfinite(r) else None
                               for a, r in zip(agree.ravel(), rain_c.ravel())],
                "n": len(got),
            }
            box_means[mk] = {b: round(_wmean(sst_c, lats, lons, *box), 2) for b, box in BOXES.items()}

    n = {c["key"]: c["n"] for c in classes}
    return {
        "source_url": f"{ERDDAP}/{DS}.html", "rain_source_url": PRECL_FILE, "oni_url": ONI_URL,
        "grid": {"lat0": float(lats[0]), "lon0": float(lons[0]), "step_deg": STEP,
                 "nlat": len(lats), "nlon": len(lons),
                 "encoding": "row-major from the southern edge, tenths of a degree C, null over land"},
        "rain_grid": {"lat0": float(rlats[0]), "lon0": float(rlons[0]), "step_deg": RAIN_STEP,
                      "nlat": len(rlats), "nlon": len(rlons),
                      "encoding": "row-major from the southern edge, integer percent change, "
                                  "null over sea or arid land"},
        "base": "1991–2020 monthly climatology: ERSST v5 for the sea, PREC/L for rain on land",
        "seasons": [{"key": k, "label": lab, "when": when} for k, lab, when, _ in SEASONS],
        "classes": classes,
        "maps": maps,
        "box_means_c": box_means,
        "notes": [
            "A composite of past El Niño years, not a model run or a forecast. Each map averages what "
            "was observed in the past El Niño winters of that strength class, for one season.",
            "Seasons line up with each winter: June to August and September to November of the year "
            "the event grew, December to February at its usual peak, March to May as it faded.",
            "Sea colour is the sea-surface temperature anomaly against a 1991-2020 monthly climatology "
            "from the same ERSST v5 record, on its native 2-degree grid from 84S to 84N. Ice-covered "
            "sea keeps a value near zero.",
            "Rain on land is NOAA PREC/L, a 2.5-degree grid built from rain gauges, shown as the percent "
            "change against the 1991-2020 mean for the same three months. It stops at 72N, north of all farmland.",
            f"Land that averages under {ARID_MM_DAY} mm of rain a day in that season is left blank: a "
            "percent change of almost nothing says little. Sea cells are blank on the rain layer.",
            "Agreement counts how many of the class's winters moved the same way as the average, wetter "
            "or drier. Where all agree the pattern repeats; where few do, one or two winters drive it.",
            "A winter counts when its DJF ONI is +0.5 or more inside an episode of at least five "
            "overlapping seasons at or above +0.5, with ONI rounded to one decimal as in CPC's "
            "table. The class is the episode's peak ONI in the July to June year around that winter.",
            "Every winter is measured against 1991-2020, so older winters sit on a cooler baseline "
            "and their sea anomalies read cooler than they were against the climate of their day.",
            "ERSST is a statistical reconstruction. Before about 1980 the Southern Ocean and parts "
            "of the tropical Pacific rest on few ship observations, so older winters are smoother "
            "than recent ones. PREC/L also thins out where gauges are few, as in much of Africa.",
            "CPC's ONI now comes from ERSST v6 on centred 30-year base periods. These maps use "
            "ERSST v5 on a fixed 1991-2020 base, so a box mean here will not equal the ONI listed "
            "for the same winters.",
            f"Each class averages a handful of winters ({n['weak']} weak, {n['moderate']} moderate, "
            f"{n['strong']} strong, {n['very_strong']} very strong), so a single unusual winter can "
            "shape a class map.",
        ],
        "_check": {w["label"]: {"djf_oni": w["djf_oni"], "nino34_c": w["nino34_c"]} for w in winters},
    }


def rain_checks(payload: dict, ckey: str = "very_strong") -> list[str]:
    g = payload["rain_grid"]
    lats = g["lat0"] + g["step_deg"] * np.arange(g["nlat"])
    lons = g["lon0"] + g["step_deg"] * np.arange(g["nlon"])
    out = []
    for region, skey, s, nn, w, e, sign in RAIN_CHECKS:
        m = payload["maps"][f"{ckey}/{skey}"]
        pct = np.array([np.nan if v is None else v for v in m["rain"]], float).reshape(g["nlat"], g["nlon"])
        agr = np.array([np.nan if v is None else v for v in m["rain_agree"]], float).reshape(pct.shape)
        rows, cols = (lats >= s) & (lats <= nn), (lons >= w) & (lons <= e)
        sub, sa = pct[np.ix_(rows, cols)], agr[np.ix_(rows, cols)]
        mean = _wmean(pct, lats, lons, s, nn, w, e)
        share = np.mean(np.sign(sub[np.isfinite(sub)]) == sign)
        verdict = "ok" if np.sign(mean) == sign else "UNEXPECTED"
        out.append(f"[check] {ckey}/{skey} {region}: {mean:+.0f}% ({'wetter' if sign > 0 else 'drier'} "
                   f"expected, {share:.0%} of {np.isfinite(sub).sum()} cells agree in sign, mean agreement "
                   f"{np.nanmean(sa):.1f}/{m['n']}) {verdict}")
    return out


def main() -> int:
    payload = build()
    check = payload.pop("_check")
    path = write_json("sst_composites.json", payload,
                      source="NOAA NCEI ERSST v5 via CoastWatch ERDDAP; NOAA PSL PREC/L via PSL THREDDS; "
                             "NOAA CPC ONI",
                      notes=("Average observed sea-surface temperature anomaly (ERSST v5, 2-degree) and "
                             "land rain change (PREC/L, 2.5-degree) of past El Niño winters by CPC "
                             "strength class and season, against 1991-2020. Composites of past winters, "
                             "not a model or a forecast."),
                      status="ok")
    # Sixteen maps of three grids each are several MB at write_json's indent=2. Same
    # envelope, no whitespace (as build_trade_matrix does).
    path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=False, separators=(",", ":")))
    for c in payload["classes"]:
        print(f"[OK] {c['key']:<12} n={c['n']:<2} mean DJF ONI {c['mean_djf_oni']:+.2f} | "
              + ", ".join(f"{e['label']}({e['peak_oni']})" for e in c["events"]))
        for skey, *_ in SEASONS:
            mk, bm = f"{c['key']}/{skey}", payload["box_means_c"][f"{c['key']}/{skey}"]
            rain = [v for v in payload["maps"][mk]["rain"] if v is not None]
            print(f"     {skey} n={payload['maps'][mk]['n']} Nino3.4 {bm['nino34']:+.2f} Nino1+2 "
                  f"{bm['nino12']:+.2f} SST {payload['maps'][mk]['sst_range_c']} | rain cells {len(rain)}, "
                  f"median {np.median(rain):+.0f}%, 5-95% {np.percentile(rain, 5):+.0f}..{np.percentile(rain, 95):+.0f}%")
    print("[check] winter: DJF ONI vs ERSST v5 Nino3.4 (1991-2020 base) -- "
          + ", ".join(f"{k} {v['djf_oni']:+.1f}/{v['nino34_c']:+.2f}" for k, v in check.items()))
    print("\n".join(rain_checks(payload)))
    print(f"[OK] {path} {path.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
