#!/usr/bin/env python3
"""build_coastal_nino12_model.py -- a rain model for the Peru/Ecuador coast driven by Nino 1+2 (local build).

Why. The Outlook's statistical half (precl_model.enso_predict) regresses each cell's month SPI on ONI, the
central Pacific (Nino 3.4). On the dry coast of northern Peru and southern Ecuador the rain follows the sea
just offshore instead: Lavado-Casimiro and Espinoza (2014) find the north-coast rain "estrechamente
relacionados con la zona Niño 1+2", and Takahashi et al. (2011) separate an eastern-Pacific (E) mode that
carries the extreme coastal events. On CHIRPS v3 1981-2020 the month SPI of these cells correlates with
Nino 1+2 at +0.64 to +0.87 from January to April (+0.36 to +0.87 December to May; Piura is the low one in
December and May), with ONI at +0.04 to +0.50 December to May. The ONI fit is not skilful in these
cells, so in September 2026, with the NMME running Nino 1+2 at +2.5 to +3.8 and ENFEN (comunicado 17-2026)
expecting rain above normal on the north coast, the map there was the NMME alone: Piura +0.40 to +0.54
from December to March.

Cells (fixed before any comparison with official forecasts): the rain-grid cells whose CHIRPS land lies
wholly west of the Andes crest between 0 and 8.75S: (-1.25,-81.25) Santa Elena / southern Manabi,
(-3.75,-81.25) Tumbes / Talara / western El Oro, (-6.25,-81.25) Piura / Paita / Sechura. The -78.75 column
mixes coast, Andes and the Amazon slope and stays on the ONI fit.

Model (per cell and calendar month m, fitted on months m-1, m, m+1 of 1981-2020):
    z = a + b * max(0, N - 0.5)
z = SPI of the CHIRPS v3 month total (cell mean over land pixels) on its own 1991-2020 gamma (totals
under 1 mm are the dry class, centre (d+1)/(2(n+1)) as scripts/spi.py); N = ERSST v5 Nino 1+2 anomaly
(1991-2020 base) of the same month, capped at the pooled record's highest value (n_cap). 0.5 is ENFEN's
threshold for a weak warm coastal condition (ICEN above 0.5, Nota Tecnica ENFEN 01-2024). sd = the fit's
residual SD. Skill per target month: in-sample slope p < 0.05 and a leave-one-hydrological-year-out
(July-June) cross-validated correlation above 0.2, the repo's rule. A three-month version (SPI of the
three-month total on its 1991-2020 gamma against the window-mean N) is fitted for windows ending
December to March. lr: a logistic fit of P(month above its 1991-2020 80th percentile) on the same term.
The straight-line (N) and ONI alternatives were compared on the same cross-validation only; the
threshold form was kept on that.

Writes data/ref/coastal_nino12_model.json (read by outlook_rain_spi, pure Python). Local only: numpy,
scipy, scikit-learn and Pillow (to decode CHIRPS's LZW tiles). CHIRPS is read by byte range from CHC's
monthly COGs, about 1.5 MB a month.
    python3 scripts/build_coastal_nino12_model.py [--cache FILE]   (FILE: JSON of cell means per month,
    read if present and extended with the months it lacks)
"""
from __future__ import annotations

import io
import json
import struct
import sys
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import requests
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import precl_model as P  # noqa: E402

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent.parent / "data" / "ref" / "coastal_nino12_model.json"
COG = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/monthly/global/cogs/chirps-v3.0.{y}.{m:02d}.cog"
ERSST = "https://www.cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii"
CELLS = [(-1.25, -81.25, "Santa Elena / southern Manabi (Ecuador)"), (-3.75, -81.25, "Tumbes / Talara / western El Oro"),
         (-6.25, -81.25, "Piura / Paita / Sechura (Peru)")]
T_H, ZERO, CLIP = 0.5, 1.0, 3.0
M3_ENDS = (12, 1, 2, 3)
SOURCES = [
    {"name": "CHIRPS v3.0 monthly rain, CHC UC Santa Barbara (cloud-optimised GeoTIFFs)", "url": COG.rsplit("/", 1)[0] + "/"},
    {"name": "NOAA CPC ERSST v5 Nino indices, 1991-2020 base", "url": ERSST},
    {"name": "ENFEN Nota Tecnica 01-2024: operational definition of El Nino Costero (ICEN classes; weak warm above 0.5)",
     "url": "https://enfen.imarpe.gob.pe/download/nota-tecnica-enfen-01-2024-definicion-operacional-de-los-eventos-el-nino-"
            "costero-y-la-nina-costera-en-el-peru/?wpdmdl=1905"},
    {"name": "Lavado-Casimiro and Espinoza (2014), Revista Brasileira de Meteorologia 29(2)",
     "url": "https://www.scielo.br/pdf/rbmet/v29n2/a03v29n2.pdf"},
    {"name": "Takahashi, Montecinos, Goubanova and Dewitte (2011), ENSO regimes: reinterpreting the canonical and "
             "Modoki El Nino (IGP repository record)", "url": "http://repositorio.igp.gob.pe/handle/IGP/3045"},
]
_S = requests.Session()


# ---------------------------------------------------------------- CHIRPS by byte range
def _rng(url: str, a: int, b: int) -> bytes:
    for _ in range(5):
        try:
            r = _S.get(url, headers={"Range": f"bytes={a}-{b}"}, timeout=120)
            if r.status_code == 206:
                return r.content
        except requests.RequestException:
            pass
    raise RuntimeError(f"no range {a}-{b} from {url}")


def _ifd(url: str) -> dict:
    h = _rng(url, 0, 65535)
    off = struct.unpack("<I", h[4:8])[0]
    tags = {}
    for i in range(struct.unpack("<H", h[off:off + 2])[0]):
        tag, typ, cnt, val = struct.unpack("<HHII", h[off + 2 + 12 * i: off + 14 + 12 * i])
        tags[tag] = (typ, cnt, val)
    arr = (lambda t: struct.unpack(f"<{tags[t][1]}I", h[tags[t][2]:tags[t][2] + 4 * tags[t][1]]))
    return dict(w=tags[256][2], tw=tags[322][2], th=tags[323][2], comp=tags[259][2],
                pred=tags.get(317, (0, 0, 1))[2], offs=arr(324), cnts=arr(325))


def _tile(url: str, meta: dict, r: int, c: int) -> np.ndarray:
    """One tile, decoded by wrapping its bytes in a one-strip TIFF for Pillow."""
    from PIL import Image
    i = r * -(-meta["w"] // meta["tw"]) + c
    raw = _rng(url, meta["offs"][i], meta["offs"][i] + meta["cnts"][i] - 1)
    tw, th = meta["tw"], meta["th"]
    ent = [(256, 3, tw), (257, 3, th), (258, 3, 32), (259, 3, meta["comp"]), (262, 3, 1), (273, 4, 0), (277, 3, 1),
           (278, 3, th), (279, 4, len(raw)), (284, 3, 1), (317, 3, meta["pred"]), (339, 3, 3)]
    data_off = 8 + 2 + 12 * len(ent) + 4
    b = bytearray(b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", len(ent)))
    for tag, typ, val in ent:
        val = data_off if tag == 273 else val
        b += struct.pack("<HHIHH", tag, typ, 1, val, 0) if typ == 3 else struct.pack("<HHII", tag, typ, 1, val)
    b += struct.pack("<I", 0) + raw
    return np.array(Image.open(io.BytesIO(bytes(b))), dtype=np.float32)


def cell_means(y: int, m: int) -> list:
    """Mean of CHIRPS's 0.05-degree land pixels in each coastal cell (lon -82.5..-80, lat 0..-7.5), None if no land."""
    url = COG.format(y=y, m=m)
    meta = _ifd(url)
    x0, x1, y0, y1 = 1950, 2000, 1200, 1350   # pixel columns/rows: lon (x + 0.5) * 0.05 - 180, lat 60 - (y + 0.5) * 0.05
    tw, th = meta["tw"], meta["th"]
    a = np.full((y1 - y0, x1 - x0), np.nan, np.float32)
    for tr in range(y0 // th, (y1 - 1) // th + 1):
        for tc in range(x0 // tw, (x1 - 1) // tw + 1):
            t = _tile(url, meta, tr, tc)
            ya, yb, xa, xb = max(y0, tr * th), min(y1, (tr + 1) * th), max(x0, tc * tw), min(x1, (tc + 1) * tw)
            a[ya - y0:yb - y0, xa - x0:xb - x0] = t[ya - tr * th:yb - tr * th, xa - tc * tw:xb - tc * tw]
    a[a < 0] = np.nan
    out = []
    for k in range(3):
        blk = a[50 * k:50 * (k + 1), :]
        out.append(float(np.nanmean(blk)) if np.isfinite(blk).any() else None)
    return out


def load_rain(months: list, cache: Path | None) -> dict:
    have = json.loads(cache.read_text()) if cache and cache.exists() else {}
    for y, m in months:
        k = f"{y}-{m:02d}"
        if k not in have:
            have[k] = cell_means(y, m)
            print(f"  CHIRPS {k}: {have[k]}", flush=True)
            if cache:
                cache.write_text(json.dumps(have))
    return {(y, m): have[f"{y}-{m:02d}"] for y, m in months}


def load_n12() -> dict:
    n12 = {}
    for line in _S.get(ERSST, timeout=60).text.split("\n")[1:]:
        p = line.split()
        if len(p) >= 4:
            n12[(int(p[0]), int(p[1]))] = float(p[3])
    return n12


# ---------------------------------------------------------------- fits
def fit_month(s: dict, m: int) -> dict:
    x = np.array([s[(y, m)] for y in range(1991, 2021)])
    wet = x[x >= ZERO]
    f = dict(n=len(x), dry=len(x) - len(wet), p80=float(np.percentile(x, 80)), median=float(np.median(x)), a=None, sc=None)
    if len(wet) >= 10 and len(np.unique(np.round(wet, 2))) >= 8:
        f["a"], _, f["sc"] = stats.gamma.fit(wet, floc=0)
    return f


def spi(v: float, f: dict) -> float | None:
    if f["a"] is None:
        return None
    q = f["dry"] / f["n"]
    if v < ZERO:
        h = min((f["dry"] + 1) / (2 * (f["n"] + 1)), q + (1 - q) * stats.gamma.cdf(ZERO, f["a"], scale=f["sc"]))
    else:
        h = q + (1 - q) * stats.gamma.cdf(v, f["a"], scale=f["sc"])
    return float(np.clip(stats.norm.ppf(np.clip(h, 1e-12, 1 - 1e-12)), -CLIP, CLIP))


def _hy(y: int, m: int) -> int:
    return y if m >= 7 else y - 1


def _pool(m: int) -> set:
    return {(m - 2) % 12 + 1, m, m % 12 + 1}


def _x(n: float) -> np.ndarray:
    return np.array([1.0, max(0.0, n - T_H)])


def rows_of(s: dict, n12: dict) -> tuple[dict, list]:
    fits = {m: fit_month(s, m) for m in range(1, 13)}
    rows = [dict(y=y, m=m, hy=_hy(y, m), N=n12[(y, m)], v=v, z=spi(v, fits[m]), w=float(v > fits[m]["p80"]))
            for (y, m), v in s.items() if (y, m) in n12 and v is not None]
    return fits, rows


def fit_model(train: list, m: int) -> dict | None:
    from sklearn.linear_model import LogisticRegression
    tr = [r for r in train if r["m"] in _pool(m) and r["z"] is not None]
    if len(tr) < 30:
        return None
    X, Y = np.array([_x(r["N"]) for r in tr]), np.array([r["z"] for r in tr])
    beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
    trw = [r for r in train if r["m"] in _pool(m)]
    Xw, Yw = np.array([_x(r["N"]) for r in trw]), np.array([r["w"] for r in trw])
    lr = LogisticRegression(C=10.0).fit(Xw[:, 1:], Yw) if len(set(Yw)) > 1 else None
    return dict(beta=beta, sd=float(np.std(Y - X @ beta, ddof=2)), Nmax=max(r["N"] for r in tr),
                Nmin=min(r["N"] for r in tr), lr=lr, pw=float(Yw.mean()))


def predict(mod: dict, n: float) -> tuple[float, float]:
    x = _x(min(max(n, mod["Nmin"]), mod["Nmax"]))
    p = float(mod["lr"].predict_proba(x[1:].reshape(1, -1))[0, 1]) if mod["lr"] is not None else mod["pw"]
    return float(x @ mod["beta"]), p


def month_table(rows: list, fits: dict) -> list:
    """Per calendar month: the 1981-2020 fit and its skill (leave-one-hydrological-year-out CV r on the target month)."""
    base = [r for r in rows if 1981 <= r["y"] <= 2020 and r["hy"] <= 2019]
    out = []
    for m in range(1, 13):
        zp, zo = [], []
        for r in (q for q in base if q["m"] == m and q["z"] is not None):
            mod = fit_model([q for q in base if q["hy"] != r["hy"]], m)
            if mod is not None:
                zp.append(predict(mod, r["N"])[0])
                zo.append(r["z"])
        full = fit_model([r for r in rows if 1981 <= r["y"] <= 2020], m)
        if full is None:
            out.append(None)
            continue
        tr = [r for r in rows if 1981 <= r["y"] <= 2020 and r["m"] in _pool(m) and r["z"] is not None]
        p = float(stats.linregress([_x(r["N"])[1] for r in tr], [r["z"] for r in tr]).pvalue)
        cvr = float(np.corrcoef(zp, zo)[0, 1]) if len(zp) > 5 and np.std(zp) > 0 else float("nan")
        f, lr = fits[m], full["lr"]
        out.append(dict(a=round(float(full["beta"][0]), 3), b=round(float(full["beta"][1]), 3), sd=round(full["sd"], 3),
                        n_cap=round(full["Nmax"], 2), cv_r=round(cvr, 3), p=round(p, 4), skilful=bool(p < 0.05 and cvr > 0.2),
                        lr=None if lr is None else [round(float(lr.intercept_[0]), 3), round(float(lr.coef_[0][0]), 3)],
                        p80_mm=round(f["p80"], 1), median_mm=round(f["median"], 1),
                        gamma=None if f["a"] is None else [round(f["a"], 4), round(f["sc"], 3), f["dry"]]))
    return out


def m3_entry(s: dict, n12: dict, m: int) -> dict:
    ser = {}
    for (y, mm) in s:
        prev = [((y * 12 + mm - 1 - k) // 12, (y * 12 + mm - 1 - k) % 12 + 1) for k in (2, 1, 0)]
        if all(s.get(q) is not None and q in n12 for q in prev):
            ser[(y, mm)] = (sum(s[q] for q in prev), float(np.mean([n12[q] for q in prev])))
    x91 = np.array([ser[(y, m)][0] for y in range(1991, 2021)])
    f = dict(n=30, dry=int((x91 < ZERO).sum()))
    f["a"], _, f["sc"] = stats.gamma.fit(x91[x91 >= ZERO], floc=0)
    rows = [dict(y=y, z=spi(ser[(y, m)][0], f), N=ser[(y, m)][1]) for y in range(1981, 2021) if (y, m) in ser]

    def fit(rr):
        A, Y = np.array([_x(r["N"]) for r in rr]), np.array([r["z"] for r in rr])
        b, *_ = np.linalg.lstsq(A, Y, rcond=None)
        return b, float(np.std(Y - A @ b, ddof=2)), max(r["N"] for r in rr)
    zp = []
    for r in rows:
        b, _, nmax = fit([q for q in rows if q["y"] != r["y"]])
        zp.append(float(_x(min(r["N"], nmax)) @ b))
    b, sd, nmax = fit(rows)
    p = float(stats.linregress([_x(r["N"])[1] for r in rows], [r["z"] for r in rows]).pvalue)
    cvr = float(np.corrcoef(zp, [r["z"] for r in rows])[0, 1])
    return dict(a=round(float(b[0]), 3), b=round(float(b[1]), 3), sd=round(sd, 3), n_cap=round(nmax, 2), cv_r=round(cvr, 3),
                p=round(p, 4), skilful=bool(p < 0.05 and cvr > 0.2))


def validation(rows: list) -> dict:
    """Hindcast skill: leave one hydrological year out over Jul 1981-Jun 2020, and fitted on that period then
    scored out of sample from July 2021. MSSS against a forecast of 0, BSS of the very-wet chance against 20%."""
    base = [r for r in rows if 1981 <= r["hy"] <= 2019 and r["y"] <= 2020]

    def run(targets, loo):
        out, cache = [], {}
        for r in targets:
            key = (r["hy"] if loo else None, r["m"])
            if key not in cache:
                cache[key] = fit_model([q for q in base if q["hy"] != r["hy"]] if loo else base, r["m"])
            if cache[key] is not None:
                zp, pp = predict(cache[key], r["N"])
                out.append(dict(r, zp=zp, pp=pp))
        return out

    def met(rr, months=None):
        rr = [r for r in rr if months is None or r["m"] in months]
        rz = [r for r in rr if r["z"] is not None]
        zo, zp = np.array([r["z"] for r in rz]), np.array([r["zp"] for r in rz])
        w, pp = np.array([r["w"] for r in rr]), np.array([r["pp"] for r in rr])
        return dict(n=len(rz), r=round(float(np.corrcoef(zo, zp)[0, 1]), 2), msss=round(float(1 - np.mean((zo - zp) ** 2) / np.mean(zo ** 2)), 2),
                    bss=round(float(1 - np.mean((pp - w) ** 2) / np.mean((0.2 - w) ** 2)), 2))
    loo = run(base, True)
    oos = run([r for r in rows if r["hy"] >= 2021], False)
    return {"loo_all_months": met(loo), "loo_oct_dec": met(loo, {10, 11, 12}), "loo_jan_mar": met(loo, {1, 2, 3}),
            "out_of_sample_from_2021_07": met(oos), "out_of_sample_last_month": max(f"{r['y']}-{r['m']:02d}" for r in oos)}


def build(cache: Path | None) -> dict:
    n12 = load_n12()
    last = max(n12)
    months = [(y, m) for y in range(1981, last[0] + 1) for m in range(1, 13) if (y, m) <= last]
    rain = load_rain(months, cache)
    out = {"cells": [], "names": {}, "m1": {}, "m3": {}, "validation": {}}
    for k, (la, lo, name) in enumerate(CELLS):
        c = round((la - P.GRID[0]) / P.GRID[2]) * P.GRID[4] + round((lo - P.GRID[1]) / P.GRID[2])
        s = {ym: v[k] for ym, v in rain.items()}
        fits, rows = rows_of(s, n12)
        out["cells"].append(c)
        out["names"][c] = f"{name} ({la}, {lo})"
        out["m1"][c] = month_table(rows, fits)
        out["m3"][c] = {m: m3_entry(s, n12, m) for m in M3_ENDS}
        out["validation"][c] = validation(rows)
        print(f"[OK] cell {c} {name}: skilful months {[m + 1 for m, e in enumerate(out['m1'][c]) if e and e['skilful']]}, "
              f"3-month {[m for m, e in out['m3'][c].items() if e['skilful']]}, {out['validation'][c]}", flush=True)
    out["meta"] = {"built": date.today().isoformat(), "threshold_n12": T_H, "fit_years": [1981, 2020], "spi_base": [1991, 2020],
                   "predictor": "ERSST v5 Nino 1+2 anomaly (1991-2020 base), same calendar month; in the Outlook the "
                                "NMME ensemble-mean Nino 1+2 box (payload box_means_c_months[].nino12)",
                   "form": "z = a + b * max(0, min(N, n_cap) - threshold_n12); sd = residual SD in SPI units; lr = logistic "
                           "[intercept, slope] of P(month above p80_mm) on the same term",
                   "skill_rule": "in-sample slope p < 0.05 and leave-one-hydrological-year-out CV r > 0.2 on the target month",
                   "m1": "per cell, 12 entries for calendar months 1..12, fitted on months m-1, m, m+1",
                   "m3": "per cell, windows ending in the month given (all three months forecast in the Outlook)",
                   "record_last_month": f"{last[0]}-{last[1]:02d}", "sources": SOURCES}
    return out


if __name__ == "__main__":
    cache = Path(sys.argv[sys.argv.index("--cache") + 1]) if "--cache" in sys.argv else None
    tab = build(cache)
    OUT.write_text(json.dumps(tab, indent=1))
    print(f"[OK] wrote {OUT} ({OUT.stat().st_size / 1e3:.1f} kB)")
