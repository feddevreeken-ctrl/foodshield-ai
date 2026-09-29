#!/usr/bin/env python3
"""coastal_nino12.py -- the Peru/Ecuador coast's El Nino rain model, evaluated in pure Python (stdlib only).

data/ref/coastal_nino12_model.json is built locally by scripts/build_coastal_nino12_model.py (why, cells,
fit and skill rule are there). For its three coastal cells and the months where it is skilful,
outlook_rain_spi uses it in place of the ONI fit as the Outlook's statistical half, driven by the NMME
ensemble-mean Nino 1+2 box of the same month (payload box_means_c_months[month].nino12):
    z = a + b * max(0, min(N, n_cap) - 0.5)
A missing or unreadable file, or a month without a Nino 1+2 value, leaves those cells on the ONI fit.
"""
from __future__ import annotations

import json
from pathlib import Path

MODEL = Path(__file__).resolve().parent.parent / "data" / "ref" / "coastal_nino12_model.json"


def load(path: Path = MODEL) -> dict | None:
    """The table with integer keys, or None (the Outlook then keeps the ONI fit everywhere)."""
    try:
        t = json.loads(path.read_text())
        tab = {"cells": [int(c) for c in t["cells"]], "names": {int(k): v for k, v in t["names"].items()},
               "m1": {int(k): v for k, v in t["m1"].items()},
               "m3": {int(k): {int(m): e for m, e in v.items()} for k, v in t["m3"].items()},
               "th": float(t["meta"]["threshold_n12"]), "meta": t["meta"], "validation": t["validation"]}
        if any(len(tab["m1"][c]) != 12 for c in tab["cells"]):
            return None
        return tab
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _z(e: dict, n: float, th: float) -> float:
    return e["a"] + e["b"] * max(0.0, min(n, e["n_cap"]) - th)


def month_z(tab: dict | None, cell: int, month: int, n: float | None) -> tuple[float, float] | None:
    """(SPI, residual SD) for a coastal cell and calendar month where the fit is skilful, else None."""
    if tab is None or n is None or cell not in tab["m1"]:
        return None
    e = tab["m1"][cell][month - 1]
    if not e or not e.get("skilful"):
        return None
    return _z(e, float(n), tab["th"]), e["sd"]


def window_z(tab: dict | None, cell: int, end_month: int, n_mean: float | None) -> float | None:
    """SPI of the three months ending in end_month, from their mean Nino 1+2, where that fit is skilful."""
    if tab is None or n_mean is None or cell not in tab["m3"]:
        return None
    e = tab["m3"][cell].get(end_month)
    if not e or not e.get("skilful"):
        return None
    return _z(e, float(n_mean), tab["th"])
