#!/usr/bin/env python3
"""Hand-run builder (numpy): crop-belt rain weights for the Reported lens.

Reads MIRCA2000 v1.1 (Portmann, Siebert, Doell 2010; Zenodo 10.5281/zenodo.7422506, CC-BY 4.0): annual harvested
area per crop at 5 arc-minutes (irrigated + rainfed), the unit-code grid and the condensed cropping calendars.
For every region in data/enso_regions.json that has a `crop_belt` block it writes, per crop, the harvested-area
weights on the project's 2.5 degree rain grid (normalised to sum to 1) plus a 12-month crop-stage table built from
the MIRCA calendars of the same cells. Output: data/ref/crop_area_2p5.json.gz (committed; CI never rebuilds it).

Inputs are cached under $FOODSHIELD_CACHE (default ~/.cache/foodshield):
  mirca_harv.zip, mirca_unit.zip, mirca_cal.zip  from https://zenodo.org/api/records/7422506/files/<name>/content
Run: python3 scripts/build_crop_area.py
"""
import gzip, io, json, os, sys, zipfile, datetime
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("FOODSHIELD_CACHE", os.path.expanduser("~/.cache/foodshield"))
CLASS = {"wheat": 1, "maize": 2, "rice": 3, "millet": 6, "sorghum": 7, "soy": 8, "oil_palm": 14, "pulses": 17}
G = json.load(open(os.path.join(ROOT, "data/rain_anomaly.json")))["data"]["grid"]
LAT0, LON0, STEP, NLAT, NLON = G["lat0"], G["lon0"], G["step_deg"], G["nlat"], G["nlon"]


def asc(z, name):
    raw = z.read(name)
    txt = gzip.decompress(raw).decode() if raw[:2] == b"\x1f\x8b" else raw.decode()
    lines = txt.split("\n")
    a = np.array([l.split() for l in lines[6:6 + 2160] if l.strip()], dtype=float)
    return a


def gz_text(z, name):
    raw = z.read(name)
    return (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode()


def read_cal(z, name):
    cal = {}
    for l in gz_text(z, name).split("\n")[4:]:
        p = l.split()
        if len(p) < 3:
            continue
        u, c, n = int(p[0]), int(p[1]), int(p[2])
        subs = []
        for k in range(n):
            a, s, e = float(p[3 + 3 * k]), int(p[4 + 3 * k]), int(p[5 + 3 * k])
            if a > 0:
                subs.append((a, s, e))
        cal[(u, c)] = subs
    return cal


def stages(start, end, perennial):
    """month -> stage for one sub-crop: plant (first month), veg, fill (second half), harvest (last month)."""
    if perennial:
        return {m: "year" for m in range(1, 13)}
    L = (end - start) % 12 + 1
    out = {}
    for t in range(L):
        m = (start - 1 + t) % 12 + 1
        if t == 0:
            out[m] = "plant"
        elif t == L - 1:
            out[m] = "harvest"
        else:
            out[m] = "veg" if t / (L - 1) < 0.5 else "fill"
    return out


def main():
    regs = json.load(open(os.path.join(ROOT, "data/enso_regions.json")))["data"]["regions"]
    regs = [r for r in regs if r.get("crop_belt")]
    zh = zipfile.ZipFile(os.path.join(CACHE, "mirca_harv.zip"))
    zu = zipfile.ZipFile(os.path.join(CACHE, "mirca_unit.zip"))
    zc = zipfile.ZipFile(os.path.join(CACHE, "mirca_cal.zip"))
    unit = asc(zu, "unit_code_grid/unit_code.asc.gz")
    cal = {"irc": read_cal(zc, "condensed_cropping_calendars/cropping_calendar_irrigated.txt.gz"),
           "rfc": read_cal(zc, "condensed_cropping_calendars/cropping_calendar_rainfed.txt.gz")}
    # country of each 5' cell: UN numeric code = unit // 1000 -> ISO3 via Natural Earth ISO_N3
    ne = json.load(open(os.path.join(ROOT, "data/geo/ne_50m_admin_0_countries.geojson")))["features"]
    n3 = {}
    for f in ne:
        p = f["properties"]
        try:
            n3.setdefault(int(p.get("ISO_N3")), p.get("ADM0_A3"))
        except (TypeError, ValueError):
            pass
    n3.update({250: "FRA", 578: "NOR", 756: "CHE", 158: "TWN", 728: "SSD"})
    ucode = np.where(unit > 0, unit, 0).astype(np.int64)
    un = ucode // 1000
    lat = 90 - (np.arange(2160) + 0.5) / 12.0
    lon = -180 + (np.arange(4320) + 0.5) / 12.0
    LAT, LON = np.meshgrid(lat, lon, indexing="ij")
    ci = np.floor((LAT - (LAT0 - STEP / 2)) / STEP).astype(int)
    cj = np.floor((LON - (LON0 - STEP / 2)) / STEP).astype(int)
    ok = (ci >= 0) & (ci < NLAT) & (cj >= 0) & (cj < NLON)
    cell = np.where(ok, ci * NLON + cj, -1)
    crops_cache = {}

    def harv(kind, cls):
        k = (kind, cls)
        if k not in crops_cache:
            a = asc(zh, f"harvested_area_grids/ANNUAL_AREA_HARVESTED_{kind.upper()}_CROP{cls}_HA.ASC.gz")
            a[a < 0] = 0
            crops_cache[k] = a
        return crops_cache[k]

    out = {}
    tot_all = {}
    for r in regs:
        cb = r["crop_belt"]
        iso_codes = {k for k, v in n3.items() if v in r["iso3"]}
        m = np.isin(un, list(iso_codes)) & ok
        if cb.get("bbox"):
            w, s, e, n = cb["bbox"]
            m &= (LON >= w) & (LON <= e) & (LAT >= s) & (LAT <= n)
        entry = {}
        for crop in cb["crops"]:
            cls = CLASS[crop]
            cells, ha_tot = {}, 0.0
            mon = {mm: {"plant": 0.0, "veg": 0.0, "fill": 0.0, "harvest": 0.0, "year": 0.0, "off": 0.0} for mm in range(1, 13)}
            for kind in ("irc", "rfc"):
                a = harv(kind, cls) * m
                idx = np.nonzero(a)
                for i, j in zip(*idx):
                    v = a[i, j]
                    c = int(cell[i, j]); cells[c] = cells.get(c, 0.0) + v; ha_tot += v
                # calendar mix by unit
                for u in np.unique(ucode[(a > 0)]):
                    ha_u = a[ucode == u].sum()
                    subs = cal[kind].get((int(u), cls), [])
                    tot = sum(x[0] for x in subs)
                    if not tot or ha_u <= 0:
                        continue
                    for (sa, s0, e0) in subs:
                        wgt = ha_u * sa / tot
                        st = stages(s0, e0, cls == 14)
                        for mm in range(1, 13):
                            mon[mm][st.get(mm, "off")] += wgt
            if ha_tot <= 0:
                print("NO AREA", r["id"], crop, int(m.sum()), sorted(iso_codes)); continue
            ws = {c: v / ha_tot for c, v in cells.items()}
            # round to 6 dp and renormalise so the stored weights sum to 1 within 1e-9
            q = {c: round(v, 6) for c, v in ws.items()}
            drift = 1.0 - sum(q.values()); big = max(q, key=q.get); q[big] = round(q[big] + drift, 6)
            cal_sum = lambda d: sum(d.values()) or 1.0
            stage_tbl = {str(mm): {k: round(v / cal_sum(mon[mm]), 3) for k, v in mon[mm].items() if v > 0} for mm in range(1, 13)}
            entry[crop] = {"ha": round(ha_tot), "cells": sorted([[c, w] for c, w in q.items()]), "stage": stage_tbl}
            tot_all[(r["id"], crop)] = ha_tot
        out[r["id"]] = {"iso3": r["iso3"], "bbox": cb.get("bbox"), "crops": entry}
    doc = {"_meta": {
        "built": datetime.date.today().isoformat(),
        "source": "MIRCA2000 v1.1 harvested area (irrigated + rainfed) and condensed cropping calendars, Portmann, Siebert & Doell 2010, Global Biogeochemical Cycles 24, GB1011",
        "url": "https://zenodo.org/records/7422506",
        "doi": "10.5281/zenodo.7422506", "licence": "CC-BY 4.0",
        "vintage": "around the year 2000 (1998-2002); crop areas have moved since, patterns of where each crop grows have not changed much at 2.5 degrees",
        "resolution": "5 arc-minute source, summed onto the 2.5 degree CPC rain grid of data/rain_anomaly.json",
        "method": "Per region in data/enso_regions.json with a crop_belt block: harvested area of each named crop inside the region's countries (and optional lon/lat box) summed per 2.5 degree cell, normalised to sum 1. cells = [[index, weight]] with index = row * nlon + col, row-major from the south edge. stage = share of the crop's area in each stage by calendar month, from the MIRCA calendars of the same units (plant = first month of the growing period, harvest = last month, veg and fill = first and second half of the months between).",
        "grid": {"lat0": LAT0, "lon0": LON0, "step_deg": STEP, "nlat": NLAT, "nlon": NLON},
        "hand_run": "scripts/build_crop_area.py needs numpy and the MIRCA zips in the shared cache; output is committed and not rebuilt by the cron",
    }, "regions": out}
    p = os.path.join(ROOT, "data/ref/crop_area_2p5.json.gz")
    with gzip.open(p, "wt", compresslevel=9) as f:
        json.dump(doc, f, separators=(",", ":"))
    print("wrote", p, os.path.getsize(p), "bytes")
    for k, v in sorted(tot_all.items()):
        print(k, round(v / 1e6, 2), "Mha")


main()
