#!/usr/bin/env python3
"""refresh_seasonal_outlook.py -- the latest published seasonal outlook maps.

Feeds the "Outlook" step on the El Nino Ocean map, beside "This week" (observed
OISST) and "Past El Ninos" (composites of observed winters). This one is a
FORECAST: NOAA's North American Multi-Model Ensemble (NMME) as CPC posts it
each month, for the next three overlapping three-month seasons after the
forecast's start month (a September start gives Oct-Dec, Nov-Jan, Dec-Feb).

Data (all key-less, from CPC's public file server, classic NetCDF-3 read here
in pure Python; the Actions job installs only requests and openpyxl):
  * realtime_anom/ENSMEAN/<YYYYMM>0800/NMME.tmpsfc.<YYYYMM>.ENSMEAN.anom.nc
    -- ensemble-mean surface temperature anomaly, the sea layer.
  * the same folder, NMME.prate...anom.nc and, per model, <model>.prate...fcst.nc
    -- rain. CPC's NMME anomaly is the equal-weight mean of the model anomalies,
    each against that model's own hindcast climatology, so the NMME climatology
    is mean(model forecasts) - NMME anomaly. Rain percent =
    100 x (3-month mean NMME anomaly) / (3-month mean NMME climatology), both
    area-averaged onto the 2.5-degree grid first. Masked where that climatology
    is under 0.3 mm/day. One month of each model's own anomaly file is read to
    prove the models listed are exactly the ones in CPC's mean.
  * prob/netcdf/prate.<YYYYMM>.prob.adj.seas.nc -- CPC's adjusted (calibrated)
    NMME tercile probabilities for seasonal rain. tmpsfc.<YYYYMM>.prob.adj.seas.nc
    is read once for its NaN pattern: CPC's 1-degree land mask, so no land
    temperature leaks into a sea cell.
  * IRI's Data Library (IRI's own multi-model forecast, NMME mirrors) now sits
    behind a login, and C3S needs a key, so neither is used.

Byte ranges keep the pull to about 14 MB: only the five target months needed
(and one month of each per-model anomaly) are fetched, not whole files. When
CPC has not posted a newer start month than the one on disk, nothing is
downloaded and the last file is re-stamped.

Grids are EXACTLY those of data/sst_composites.json (read from it, with its
land/sea cells): sea on the ERSST 2-degree grid, rain on the PREC/L 2.5-degree
land grid, so the Outlook can swap in for the Past El Ninos layer cell for cell.
"""
from __future__ import annotations

import json
import math
import re
import struct
import sys
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, http_get, write_json  # noqa: E402

CPC = "https://ftp.cpc.ncep.noaa.gov/NMME"
ENS = f"{CPC}/realtime_anom/ENSMEAN"
PROB = f"{CPC}/prob/netcdf"
UA = {"User-Agent": "FoodShield-AI data refresh (github.com/feddevreeken-ctrl/foodshield-ai)"}
OUT = "seasonal_outlook.json"
SCHEMA = 1
ARID_MM_DAY = 0.3
N_SEASONS = 3
MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
BOXES = {"nino34": (-5, 5, -170, -120), "nino12": (-10, 0, -90, -80)}
# Printed sanity checks: textbook El Nino rain signs. (region, season, S, N, W, E, sign)
RAIN_CHECKS = (("Horn of Africa", "OND", -5, 10, 35, 51, 1),
               ("Indonesia", "OND", -10, 5, 95, 141, -1),
               ("eastern Australia", "OND", -38, -15, 140, 154, -1),
               ("southern Africa", "DJF", -35, -15, 15, 40, -1),
               ("northern South America", "DJF", 0, 12, -75, -50, -1),
               ("southern Brazil/Uruguay", "DJF", -35, -25, -60, -48, 1),
               ("US Gulf coast", "DJF", 27, 33, -98, -80, 1))

# ---------------------------------------------------------------- NetCDF-3 over HTTP
_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 4, 6: 8}
_FMT = {1: "b", 2: "c", 3: "h", 4: "i", 5: "f", 6: "d"}


def _get(url: str, first: int | None = None, last: int | None = None) -> bytes:
    hdrs = dict(UA)
    if first is not None:
        hdrs["Range"] = f"bytes={first}-{last}"
    r = http_get(url, timeout=120, headers=hdrs, retries=3)
    b = r.content
    if first is None:
        return b
    want = last - first + 1
    if r.status_code == 200 and len(b) > last:  # server ignored the Range header
        b = b[first:last + 1]
    if len(b) != want:
        raise RuntimeError(f"{url}: asked for {want} bytes at {first}, got {len(b)}")
    return b


class NC:
    """Header and fixed-size variables of one classic (CDF1/CDF2) NetCDF file."""

    def __init__(self, url: str):
        self.url = url
        self.head = _get(url, 0, 65535)
        b = self.head
        if b[:3] != b"CDF" or b[3] not in (1, 2):
            raise RuntimeError(f"{url} is not classic NetCDF (magic {b[:4]!r})")
        p = 8  # magic + numrecs

        def u32():
            nonlocal p
            p += 4
            return struct.unpack_from(">I", b, p - 4)[0]

        def name():
            nonlocal p
            n = u32()
            p += n + (-n % 4)
            return b[p - n - (-n % 4):p - (-n % 4)].decode()

        def atts():
            nonlocal p
            u32()
            out = {}
            for _ in range(u32()):
                k, t, cnt = name(), u32(), u32()
                size = _SIZE[t] * cnt
                raw = b[p:p + size]
                p += size + (-size % 4)
                out[k] = raw.decode("latin-1").rstrip("\x00") if t == 2 else struct.unpack(f">{cnt}{_FMT[t]}", raw)
            return out

        u32()
        dims = [(name(), u32()) for _ in range(u32())]
        atts()
        u32()
        self.vars = {}
        for _ in range(u32()):
            k = name()
            ids = [u32() for _ in range(u32())]
            va, t, _vsize = atts(), u32(), u32()
            begin = struct.unpack_from(">Q" if b[3] == 2 else ">I", b, p)[0]
            p += 8 if b[3] == 2 else 4
            shape = [dims[i][1] for i in ids]
            if 0 in shape:
                raise RuntimeError(f"{url}: {k} is a record variable; layout changed")
            self.vars[k] = {"shape": shape, "type": t, "begin": begin, "atts": va}

    def slabs(self, var: str, i0: int, n: int) -> list[array]:
        """n consecutive slabs along the first axis of a float32 variable, one request."""
        v = self.vars[var]
        if v["type"] != 5:
            raise RuntimeError(f"{self.url}: {var} is not float32")
        size = math.prod(v["shape"][1:]) * 4
        off = v["begin"] + i0 * size
        raw = self.head[off:off + n * size] if off + n * size <= len(self.head) else _get(self.url, off, off + n * size - 1)
        out = []
        for k in range(n):
            a = array("f")
            a.frombytes(raw[k * size:(k + 1) * size])
            if sys.byteorder == "little":
                a.byteswap()
            out.append(a)
        return out

    def axis(self, var: str) -> list[float]:
        v = self.vars[var]
        size = math.prod(v["shape"]) * _SIZE[v["type"]]
        raw = self.head[v["begin"]:v["begin"] + size]
        if len(raw) < size:
            raw = _get(self.url, v["begin"], v["begin"] + size - 1)
        return list(struct.unpack(f">{math.prod(v['shape'])}{_FMT[v['type']]}", raw))

    def months(self, var: str) -> list[int]:
        """'months since 1960-01-..' axis -> absolute month numbers (year * 12 + month - 1)."""
        units = self.vars[var]["atts"].get("units", "")
        if not units.startswith("months since 1960-01-"):
            raise RuntimeError(f"{self.url}: unexpected {var} units {units!r}")
        return [1960 * 12 + int(round(x)) for x in self.axis(var)]

    def check_grid(self):
        lat, lon = self.axis("lat"), self.axis("lon")
        if len(lat) != 181 or lat[0] != 90 or lat[-1] != -90 or len(lon) != 360 or lon[0] != 0 or lon[1] != 1:
            raise RuntimeError(f"{self.url}: grid is not 1-degree 90N..90S x 0..359E")


def _ok(v: float) -> bool:
    return v == v and abs(v) < 1e20


# ---------------------------------------------------------------- which forecast
def _pick_init() -> tuple[int, str, list[str], str]:
    """Newest start month whose files are all posted: (month number, folder url, models, YYYYMM)."""
    listing = _get(f"{ENS}/").decode("latin-1")
    probs = _get(f"{PROB}/").decode("latin-1")
    folders = sorted(set(re.findall(r'href="(\d{6})0800/"', listing)), reverse=True)
    for ym in folders[:2]:
        url = f"{ENS}/{ym}0800"
        files = set(re.findall(r'href="([^"/]+\.nc)"', _get(f"{url}/").decode("latin-1")))
        need = {f"NMME.prate.{ym}.ENSMEAN.anom.nc", f"NMME.tmpsfc.{ym}.ENSMEAN.anom.nc"}
        fc = [re.fullmatch(rf"(.+)\.prate\.{ym}\.ENSMEAN\.fcst\.nc", f) for f in sorted(files)]
        models = [m[1] for m in fc if m and m[1] != "NMME" and f"{m[1]}.prate.{ym}.ENSMEAN.anom.nc" in files]
        if need <= files and len(models) >= 4 and f"prate.{ym}.prob.adj.seas.nc" in probs \
                and f"tmpsfc.{ym}.prob.adj.seas.nc" in probs:
            return int(ym[:4]) * 12 + int(ym[4:]) - 1, url, models, ym
        print(f"[warn] NMME {ym}: files not all posted yet, trying the month before")
    raise RuntimeError(f"no complete NMME start month among {folders[:2]}")


def _label(m0: int) -> str:
    y0, y1 = m0 // 12, (m0 + 2) // 12
    a, b = MON[m0 % 12], MON[(m0 + 2) % 12]
    return f"{a}–{b} {y0}" if y0 == y1 else f"{a} {y0}–{b} {y1}"


# ---------------------------------------------------------------- regridding
def _src(lat: int, lon: int) -> int:
    """Index into a CPC 1-degree field (rows 90N..90S, columns 0..359E)."""
    return (90 - lat) * 360 + lon % 360


def _weights(lat_c: float, lon_c: float, half: float) -> list[tuple[int, float]]:
    """Area overlap of 1-degree source cells with a target cell of half-width `half`, times cos(lat)."""
    out = []
    for s in range(math.floor(lat_c - half - 0.5), math.ceil(lat_c + half + 0.5) + 1):
        wy = min(lat_c + half, s + 0.5) - max(lat_c - half, s - 0.5)
        if wy <= 0 or abs(s) > 90:
            continue
        wy *= math.cos(math.radians(s))
        for t in range(math.floor(lon_c - half - 0.5), math.ceil(lon_c + half + 0.5) + 1):
            wx = min(lon_c + half, t + 0.5) - max(lon_c - half, t - 0.5)
            if wx > 0:
                out.append((_src(s, t), wy * wx))
    return out


def _regrid(field, weights, need=0.0):
    """Weighted mean of the valid source cells of each target cell; None if too few valid."""
    out = []
    for ws in weights:
        if ws is None:
            out.append(None)
            continue
        s = w = tot = 0.0
        for i, wt in ws:
            tot += wt
            v = field[i]
            if _ok(v):
                s += v * wt
                w += wt
        out.append(s / w if w > 0 and w >= need * tot else None)
    return out


def _mean(fields):
    n = len(fields)
    return [sum(f[i] for f in fields) / n for i in range(len(fields[0]))]


def _box_mean(values, g, box) -> float | None:
    s_lat, n_lat, w_lon, e_lon = box
    s = w = 0.0
    for r in range(g["nlat"]):
        la = g["lat0"] + r * g["step_deg"]
        if not s_lat <= la <= n_lat:
            continue
        for c in range(g["nlon"]):
            lo = g["lon0"] + c * g["step_deg"]
            v = values[r * g["nlon"] + c]
            if w_lon <= lo <= e_lon and v is not None:
                s += v * math.cos(math.radians(la))
                w += math.cos(math.radians(la))
    return round(s / w, 2) if w else None


# ---------------------------------------------------------------- build
def _grids():
    comp = json.loads((DATA_DIR / "sst_composites.json").read_text())["data"]
    g, rg, maps = comp["grid"], comp["rain_grid"], list(comp["maps"].values())
    if (g["lat0"], g["lon0"], g["step_deg"], g["nlat"], g["nlon"]) != (-84.0, -180.0, 2.0, 85, 180) or \
            (rg["lat0"], rg["lon0"], rg["step_deg"], rg["nlat"], rg["nlon"]) != (-56.25, -178.75, 2.5, 52, 144):
        raise RuntimeError("sst_composites.json grids changed; update this collector with them")
    sea = [v is not None for v in maps[0]["sst"]]
    land = [any(m["rain"][i] is not None for m in maps) for i in range(rg["nlat"] * rg["nlon"])]
    return g, rg, sea, land


def build(init: tuple) -> dict:
    m_init, url, models, ym = init
    g, rg, sea, land = _grids()
    months = [m_init + k for k in range(1, N_SEASONS + 3)]  # every month the seasons touch

    def monthly(nc: NC, var: str, n=len(months)) -> list[array]:
        nc.check_grid()
        t = nc.months("target")
        if nc.months("initial_time")[0] != m_init:
            raise RuntimeError(f"{nc.url}: start month is not {ym}")
        i0 = t.index(months[0])
        if t[i0:i0 + n] != months[:n]:
            raise RuntimeError(f"{nc.url}: target months {t} do not cover {months[:n]}")
        return nc.slabs(var, i0, n)

    sst = monthly(NC(f"{url}/NMME.tmpsfc.{ym}.ENSMEAN.anom.nc"), "fcst")
    anom = [[v * 86400 for v in a] for a in monthly(NC(f"{url}/NMME.prate.{ym}.ENSMEAN.anom.nc"), "fcst")]
    fcst = [_mean(f) for f in zip(*[monthly(NC(f"{url}/{m}.prate.{ym}.ENSMEAN.fcst.nc"), "fcst")
                                     for m in models])]
    clim = [[f * 86400 - a for f, a in zip(fm, am)] for fm, am in zip(fcst, anom)]
    # CPC's NMME mean must be the equal-weight mean of exactly these models' anomalies.
    first = _mean([monthly(NC(f"{url}/{m}.prate.{ym}.ENSMEAN.anom.nc"), "fcst", 1)[0] for m in models])
    gap = max(abs(a * 86400 - b) for a, b in zip(first, anom[0]))
    low = min(min(c) for c in clim)
    if gap > 0.01 or low < -0.05:
        raise RuntimeError(f"model set {models} does not reproduce CPC's NMME mean "
                           f"(gap {gap:.3f} mm/day, lowest climatology {low:.3f} mm/day)")

    ocean_nc = NC(f"{PROB}/tmpsfc.{ym}.prob.adj.seas.nc")
    ocean_nc.check_grid()
    ocean = [_ok(v) for v in ocean_nc.slabs("prob_above", 0, 1)[0]]
    prob_nc = NC(f"{PROB}/prate.{ym}.prob.adj.seas.nc")
    prob_nc.check_grid()
    pt = prob_nc.months("target")
    i0 = pt.index(months[0])
    if pt[i0:i0 + N_SEASONS] != months[:N_SEASONS]:
        raise RuntimeError(f"prob file seasons {pt} do not start at {months[:N_SEASONS]}")
    below, above = prob_nc.slabs("prob_below", i0, N_SEASONS), prob_nc.slabs("prob_above", i0, N_SEASONS)

    # Sea: ERSST 2-degree cells, 1-degree weights 0.5/1/0.5 per axis, ocean sources only.
    sea_w = []
    for r in range(g["nlat"]):
        for c in range(g["nlon"]):
            if not sea[r * g["nlon"] + c]:
                sea_w.append(None)
                continue
            ws = [(i, w) for i, w in _weights(g["lat0"] + r * g["step_deg"], g["lon0"] + c * g["step_deg"], 1.0)
                  if ocean[i]]
            sea_w.append(ws)
    rain_w = [_weights(rg["lat0"] + r * rg["step_deg"], rg["lon0"] + c * rg["step_deg"], 1.25)
              if land[r * rg["nlon"] + c] else None
              for r in range(rg["nlat"]) for c in range(rg["nlon"])]

    seasons, maps, boxes = [], {}, {}
    for k in range(N_SEASONS):
        m0 = months[k]
        key = "".join(MON[(m0 + j) % 12][0] for j in range(3))
        seasons.append({"key": key, "label": _label(m0), "lead_months": m0 - m_init,
                        "months": [f"{(m0 + j) // 12}-{(m0 + j) % 12 + 1:02d}" for j in range(3)]})
        s = _regrid(_mean(sst[k:k + 3]), sea_w)
        a = _regrid(_mean(anom[k:k + 3]), rain_w)
        cl = _regrid(_mean(clim[k:k + 3]), rain_w)
        wet = [x is not None and y is not None and y >= ARID_MM_DAY for x, y in zip(a, cl)]
        pb, pa = _regrid(below[k], rain_w, 0.5), _regrid(above[k], rain_w, 0.5)
        ok = [v for v in s if v is not None]
        maps[key] = {
            "sst": [None if v is None else int(round(10 * v)) for v in s],
            "rain": [int(round(100 * x / y)) if w else None for x, y, w in zip(a, cl, wet)],
            "rain_prob_below": [int(round(100 * p)) if w and p is not None else None for p, w in zip(pb, wet)],
            "rain_prob_above": [int(round(100 * p)) if w and p is not None else None for p, w in zip(pa, wet)],
            "sst_range_c": [round(min(ok), 1), round(max(ok), 1)],
        }
        boxes[key] = {b: _box_mean(s, g, box) for b, box in BOXES.items()}

    first_s, last_s = seasons[0]["label"], seasons[-1]["label"]
    return {
        "source": "NOAA CPC, North American Multi-Model Ensemble (NMME) real-time forecast",
        "source_url": f"{url}/",
        "prob_source_url": f"{PROB}/prate.{ym}.prob.adj.seas.nc",
        "model": f"NMME ensemble mean of {len(models)} models, equally weighted",
        "models": models,
        "initialized": f"{ym[:4]}-{ym[4:]}",
        "schema": SCHEMA,
        "grid": {**{k: g[k] for k in ("lat0", "lon0", "step_deg", "nlat", "nlon")},
                 "encoding": "row-major from the southern edge, tenths of a degree C, null over land"},
        "rain_grid": {**{k: rg[k] for k in ("lat0", "lon0", "step_deg", "nlat", "nlon")},
                      "encoding": "row-major from the southern edge; rain is integer percent change, "
                                  "rain_prob_below and rain_prob_above integer percent chance; "
                                  "null over sea or dry land"},
        "seasons": seasons,
        "maps": maps,
        "box_means_c": boxes,
        "notes": [
            f"A published forecast, not an observation: the NMME ensemble mean started in "
            f"{MON[m_init % 12]} {m_init // 12} and posted by NOAA's Climate Prediction Center, for "
            f"the seasons from {first_s} to {last_s}.",
            f"The map averages {len(models)} models and all their runs. Averaging smooths out "
            "extremes, so the season that happens will be patchier and more extreme than this.",
            "Sea colour is the forecast surface-temperature anomaly for the three months, on the same "
            "2-degree grid as the past El Niño maps. Each model is measured against its own "
            "hindcast climate, which CPC dates from 1982-2010 to 1992-2019 depending on the model, "
            "not 1991-2020, so the sea reads somewhat warmer than against 1991-2020.",
            "Rain on land is the percent change of the forecast three-month rain against the "
            "models' own hindcast average for the same three months: 100 x the ensemble-mean "
            "anomaly / the mean of the models' climatologies, each averaged onto the 2.5-degree "
            "grid first. +20 means a fifth more rain than the models usually give that season.",
            f"Land where the models' average for the season is under {ARID_MM_DAY} mm a day is "
            "left blank, and so are cells that are desert all year on the past El Niño maps.",
            "Chance of below- or above-normal rain is CPC's adjusted NMME probability of the "
            "season falling in the driest or wettest third of the models' past seasons, pulled "
            "toward one in three where past forecasts had little skill. 33% means no signal.",
            "Seasonal rain forecasts are useful mainly in the tropics and where El Niño has a "
            "strong hold. Over much of Europe, Central Asia and the mid-latitude interiors skill "
            "is low, and a change shown there is close to a coin toss.",
            "Lead is the number of months from the forecast's start to the season's first month; "
            "skill falls as lead grows.",
            "CPC's Niño 3.4 plume divides each model by its hindcast amplitude error. This map "
            "does not, so its Niño 3.4 box reads a few tenths of a degree warmer than that plume.",
            "In seas that freeze, the models' surface temperature can be the ice surface rather "
            "than the water, so treat polar anomalies with care.",
        ],
    }


def rain_checks(payload: dict) -> list[str]:
    g = payload["rain_grid"]
    out = []
    for region, key, s, n, w, e, sign in RAIN_CHECKS:
        if key not in payload["maps"]:
            continue
        m = payload["maps"][key]
        vals = [v if v is None else float(v) for v in m["rain"]]
        mean = _box_mean(vals, g, (s, n, w, e))
        pb = _box_mean([None if v is None else float(v) for v in m["rain_prob_below"]], g, (s, n, w, e))
        pa = _box_mean([None if v is None else float(v) for v in m["rain_prob_above"]], g, (s, n, w, e))
        verdict = ("no data" if mean is None else "flat" if abs(mean) < 3
                   else "ok" if (mean > 0) == (sign > 0) else "UNEXPECTED")
        out.append(f"[check] {key} {region}: {mean:+.0f}% ({'wetter' if sign > 0 else 'drier'} expected; "
                   f"P(below) {pb:.0f}%, P(above) {pa:.0f}%) {verdict}")
    return out


def _write(payload: dict, source: str) -> Path:
    path = write_json(OUT, payload, source=source,
                      notes=("NMME seasonal outlook maps for the next three overlapping seasons: "
                             "ensemble-mean sea-surface temperature anomaly (ERSST 2-degree grid) and "
                             "rain percent change plus tercile odds on land (PREC/L 2.5-degree grid). "
                             "A published model forecast, not observed data."),
                      status="ok")
    # Three maps of four grids are ~1 MB at write_json's indent=2; same envelope, no whitespace.
    path.write_text(json.dumps(json.loads(path.read_text()), ensure_ascii=False, separators=(",", ":")))
    return path


def main() -> int:
    init = _pick_init()
    src = "NOAA CPC NMME real-time ensemble mean and adjusted tercile probabilities (ftp.cpc.ncep.noaa.gov/NMME)"
    try:
        old = json.loads((DATA_DIR / OUT).read_text()).get("data") or {}
    except (OSError, ValueError):
        old = {}
    ym = f"{init[3][:4]}-{init[3][4:]}"
    if old.get("initialized") == ym and old.get("schema") == SCHEMA and old.get("maps"):
        _write(old, src)
        print(f"[OK] NMME outlook unchanged (start {ym} is still CPC's newest); re-stamped, nothing downloaded")
        return 0
    payload = build(init)
    path = _write(payload, src)
    for s in payload["seasons"]:
        print(f"[OK] {s['key']} ({s['label']}, lead {s['lead_months']}): box means "
              f"{payload['box_means_c'][s['key']]} | sea range {payload['maps'][s['key']]['sst_range_c']}")
    for line in rain_checks(payload):
        print(line)
    print(f"[OK] NMME start {payload['initialized']}, {len(payload['models'])} models "
          f"({', '.join(payload['models'])}) | {path.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
