#!/usr/bin/env python3
"""sst_rain_spi.py -- the Past El Nino rain maps on the site's drought scale (3-month SPI).

Why. The Past El Nino maps (data/sst_composites.json and data/sst_winters/<label>.json) show rain
as a percent change against 1991-2020. Every other rain layer on the El Nino Ocean map (observed
months, the 30-day window, the Outlook) reads rain as SPI with one colour scale: -1 moderately dry,
-1.5 severely, -2 extremely. A percent change is not on that scale (a 40% cut is ordinary in a
desert margin and extreme in a wet tropic), so each past season also gets `rain_spi`: the 3-month
SPI of that season's PREC/L total, read with the SAME 1991-2020 gamma fits the other layers use
(data/ref/precl_enso_model.json.gz, kind "m3", evaluated by scripts/precl_model.py).

  Season -> window: JJA, SON, DJF, MAM are the 3-month windows ending Aug, Nov, Feb, May of the
  right year (DJF of the winter "1997-98" ends Feb 1998). The total is PREC/L's mean rate (mm a
  day) x each month's nominal days (February 28), as the fits were made.
  Class map: the mean of its member winters' SPI (unrounded), over the same winters the class's
  percent map averages (its `n`).
  Encoding: SPI x 100, rounded, clamped to -300..300, null wherever `rain` is null. Where `rain`
  has a value but the cell has no 3-month fit (the fit needs 2/3 of its 30 totals above zero and
  a 1991-2020 mean of 0.3 mm a day, weighted by days, so a few dry-margin cells differ from the
  percent map's unweighted arid cut), `rain_spi` is null too and the count is printed.

add_rain_spi() adds the field to the files already on disk and changes nothing else in them
(used by `build_sst_composites.py --rain-spi-only`); season_spi() and encode() are what the
full rebuild in build_sst_composites.build() calls.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import precl_model  # noqa: E402

MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
CLAMP = 300
NOTE = ("Rain on the drought scale is the 3-month SPI of each season from the same PREC/L record, "
        "read against 1991-2020 with the fits the other rain layers use: -1 moderately dry, -1.5 "
        "severely, -2 extremely, the same steps on the wet side. A class map is the mean SPI of its "
        "winters, so averaging pulls it toward zero and one winter at -2 can sit inside a class near -1.")


def season_spi(rain: dict, year: int, months, shape) -> np.ndarray | None:
    """SPI grid of one season's 3-month total. `months` as in SEASONS: (offset from the winter's
    January year, month), last one = the window's end. NaN where the cell has no m3 fit."""
    keys = [(year + dy, m) for dy, m in months]
    if any(k not in rain for k in keys):
        return None
    total = sum(rain[k] * MONTH_DAYS[k[1] - 1] for k in keys).ravel()
    end = keys[-1][1]
    out = np.full(total.size, np.nan)
    for cell in precl_model.load()["cells"]:
        t = total[cell]
        if np.isfinite(t):
            s = precl_model.spi_from_total(cell, "m3", end, float(t))
            if s is not None:
                out[cell] = s
    return out.reshape(shape)


def encode(spi, rain_enc: list) -> tuple[list, int]:
    """SPI x100 as integers on `rain`'s null pattern; also the count of cells `rain` has but no fit."""
    out, gaps = [], 0
    for s, r in zip(np.asarray(spi, float).ravel(), rain_enc):
        if r is None:
            out.append(None)
        elif not math.isfinite(s):
            out.append(None)
            gaps += 1
        else:
            out.append(max(-CLAMP, min(CLAMP, int(round(100 * s)))))
    return out, gaps


def _with_after(d: dict, after: str, key: str, value) -> dict:
    """d with key inserted right after `after` (or replaced in place if present)."""
    out = {}
    for k, v in d.items():
        if k == key:
            continue
        out[k] = v
        if k == after:
            out[key] = value
    return out


def _dump(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def add_rain_spi(data_dir: Path, rain: dict, seasons, winter_dir: str = "sst_winters") -> dict:
    """Add rain_spi to sst_composites.json and every winter file, touching nothing else.
    Returns {'gaps': {map key: n}, 'spi': {(label, season): grid}} for the caller's report."""
    comp_path = data_dir / "sst_composites.json"
    comp = json.loads(comp_path.read_text())
    d = comp["data"]
    g = d["rain_grid"]
    shape = (g["nlat"], g["nlon"])
    months_of = {k: m for k, _, _, m in seasons}
    per, gaps = {}, {}
    for c in d["classes"]:
        for e in c["events"]:
            label = e["label"]
            wpath = data_dir / winter_dir / f"{label}.json"
            wobj = json.loads(wpath.read_text())
            year = int(label[:4]) + 1
            maps = wobj["data"]["maps"]
            for skey in list(maps):
                s = season_spi(rain, year, months_of[skey], shape)
                if s is None:
                    raise RuntimeError(f"PREC/L months missing for {label} {skey}")
                per[(label, skey)] = s
                enc, gaps[f"{label}/{skey}"] = encode(s, maps[skey]["rain"])
                maps[skey] = _with_after(maps[skey], "rain", "rain_spi", enc)
            _dump(wpath, wobj)
    for c in d["classes"]:
        for skey in months_of:
            mk = f"{c['key']}/{skey}"
            got = [per[(e["label"], skey)] for e in c["events"] if (e["label"], skey) in per]
            if len(got) != d["maps"][mk]["n"]:
                raise RuntimeError(f"{mk}: {len(got)} winters with SPI, map says n={d['maps'][mk]['n']}")
            enc, gaps[mk] = encode(np.mean(got, axis=0), d["maps"][mk]["rain"])
            d["maps"][mk] = _with_after(d["maps"][mk], "rain", "rain_spi", enc)
    notes = [n for n in d["notes"] if n != NOTE]
    at = next((i + 1 for i, n in enumerate(notes) if n.startswith("Rain on land is NOAA PREC/L")), len(notes))
    d["notes"] = notes[:at] + [NOTE] + notes[at:]
    _dump(comp_path, comp)
    return {"gaps": gaps, "spi": per}
