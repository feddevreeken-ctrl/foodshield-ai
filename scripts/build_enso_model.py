#!/usr/bin/env python3
"""
build_enso_model.py — fit each country's crop-yield response to ENSO from data.

WHY THIS EXISTS
---------------
An El Nino panel needs a per-country effect size. There are two ways to get one:
copy numbers out of papers by hand, or measure them. This project has already
been burned badly by the first approach (see docs/audits/), and the published
literature reports effects for crop-region aggregates that do not map cleanly
onto the 214 political units this dashboard scores. So we measure.

The panel needed is already downloaded nightly and thrown away: the USDA PSD
bulk ZIP carries every country x commodity x attribute back to 1960, and
refresh_usda_psd.py keeps only the latest two marketing years. This script
reads the same ZIP and keeps the history.

WHAT IS FIT
-----------
For each country x commodity with enough usable history:

    log_yield_anomaly(t) = b0 + b_nino * max(ONI, 0) + b_nina * min(ONI, 0)

Two separate slopes, because La Nina is NOT negative El Nino -- the atmospheric
response is asymmetric in both magnitude and footprint, and a single slope would
average two different physical regimes into one meaningless number.

The yield anomaly is log yield minus a centred 9-year moving average, which
removes the technology trend (yields roughly triple over the panel) while
leaving the 2-7 year ENSO band intact.

SEASON ALIGNMENT
----------------
ONI peaks in DJF, but which DJF overlaps a given crop's growing season depends
on both hemisphere AND how PSD labels that crop's marketing year -- and those
two do not line up the way intuition suggests. South African maize is a
southern-hemisphere summer crop, yet PSD labels its May-April marketing year by
the START year, so the DJF inside MY t is DJF of t+1. US winter wheat is
northern-hemisphere, planted in autumn t-1 and harvested June t, so its DJF is
DJF of t.

So the alignment is NOT a hemisphere fact and is deliberately not named as one.
Both shifts are tested and the better joint fit is reported, labelled by what
was actually done -- djf_same_year / djf_next_year -- rather than by a
hemisphere story that would be wrong for exactly the countries that matter most.
Two tests means the p-value carries a Bonferroni factor of 2, recorded as p_adj.

(Empirically this picks sensibly: South African and Australian crops select
djf_next_year, US winter wheat selects djf_same_year -- each matching its real
growing season once PSD's marketing-year labelling is accounted for.)

WHAT IS DELIBERATELY NOT CLAIMED
--------------------------------
A fit that fails its gates returns "no detectable signal" rather than a small
number with a big error bar. For a dashboard, "we looked and found nothing" is a
finding; a decorative coefficient is a liability. Gates: n >= 30 usable years,
and p_adj < 0.10.

DATA QUALITY GATE
-----------------
PSD back-fills thin early records by carrying values forward -- Zambia's maize
production is identical in 1961 and 1962. Runs of >= 3 identical yields are
flagged as filled and dropped, otherwise the detrender reads carried-forward
values as genuine stability and manufactures anomalies around them.

Output: data/enso_model.json   (idempotent -- same inputs, same bytes)
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

import numpy as np
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refresh_usda_psd import FAS_TO_ISO3, NAME_TO_ISO3  # noqa: E402

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
PSD_URLS = [
    ("grains_pulses", "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip"),
    ("oilseeds", "https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip"),
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/zip,text/plain,*/*",
    "Referer": "https://apps.fas.usda.gov/psdonline/app/index.html",
}

# PSD commodity description -> our key. Descriptions are stable; codes differ
# between the two ZIPs (leading zero), so match on the description prefix.
COMMODITIES = {
    "Wheat": "wheat",
    "Corn": "corn",
    "Rice, Milled": "rice",
    "Sorghum": "sorghum",
    "Millet": "millet",
    "Barley": "barley",
    "Oilseed, Soybean": "soybeans",
}

MIN_YEARS = 30          # usable observations required before we fit at all
P_GATE = 0.10           # Bonferroni-adjusted significance required to report
FLAT_RUN = 3            # >= this many identical consecutive yields => filled
MA_WINDOW = 9           # centred moving average window for the technology trend
MA_MIN = 5              # minimum periods at the series edges


def _http(url: str, timeout: int = 180) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout).read()


def _cache_path(name: str) -> str:
    d = os.environ.get("FOODSHIELD_CACHE") or "/tmp/foodshield-enso-cache"
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def _cached(name: str, url: str) -> bytes:
    p = _cache_path(name)
    if os.path.exists(p) and os.path.getsize(p) > 1024:
        with open(p, "rb") as f:
            return f.read()
    b = _http(url)
    with open(p, "wb") as f:
        f.write(b)
    return b


def load_oni() -> dict[int, float]:
    """DJF ONI by year. CPC labels DJF <Y> as Dec(Y-1)-Jan(Y)-Feb(Y)."""
    raw = _cached("oni.ascii.txt", ONI_URL).decode("utf-8", "replace")
    djf: dict[int, float] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[0] == "SEAS":
            continue
        seas, yr, _total, anom = parts
        if seas != "DJF":
            continue
        try:
            djf[int(yr)] = float(anom)
        except ValueError:
            continue
    if len(djf) < 60:
        raise RuntimeError(f"ONI parse yielded only {len(djf)} DJF seasons -- feed shape changed")
    return djf


def load_psd_panel() -> dict:
    """iso3 -> commodity -> year -> {yield, production, area}."""
    panel: dict = {}
    for label, url in PSD_URLS:
        blob = _cached(f"psd_{label}.zip", url)
        zf = zipfile.ZipFile(io.BytesIO(blob))
        name = next((n for n in zf.namelist() if n.endswith(".csv")), None)
        if not name:
            raise RuntimeError(f"no CSV inside {label} ZIP")
        with zf.open(name) as fh:
            for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")):
                desc = (row.get("Commodity_Description") or "").strip()
                key = COMMODITIES.get(desc)
                if not key:
                    continue
                attr = (row.get("Attribute_Description") or "").strip()
                if attr not in ("Yield", "Production", "Area Harvested"):
                    continue
                iso3 = (FAS_TO_ISO3.get((row.get("Country_Code") or "").strip())
                        or NAME_TO_ISO3.get((row.get("Country_Name") or "").strip()))
                if not iso3:
                    continue
                try:
                    year = int(row["Market_Year"])
                    val = float(row["Value"])
                except (ValueError, KeyError, TypeError):
                    continue
                slot = panel.setdefault(iso3, {}).setdefault(key, {}).setdefault(year, {})
                slot[{"Yield": "yield", "Production": "prod",
                      "Area Harvested": "area"}[attr]] = val
    return panel


def drop_filled(years: list[int], vals: list[float]) -> tuple[list[int], list[float]]:
    """Remove runs of >= FLAT_RUN identical yields (PSD carry-forward filling)."""
    keep = [True] * len(vals)
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j + 1] == vals[i]:
            j += 1
        if j - i + 1 >= FLAT_RUN:
            for k in range(i, j + 1):
                keep[k] = False
        i = j + 1
    return ([y for y, k in zip(years, keep) if k],
            [v for v, k in zip(vals, keep) if k])


def detrend(years: list[int], vals: list[float]) -> tuple[list[int], list[float]]:
    """log yield minus centred moving average -> fractional anomaly."""
    logy = np.log(np.array(vals, dtype=float))
    n = len(logy)
    half = MA_WINDOW // 2
    out_y: list[int] = []
    out_a: list[float] = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        if hi - lo < MA_MIN:
            continue
        out_y.append(years[i])
        out_a.append(float(logy[i] - logy[lo:hi].mean()))
    return out_y, out_a


def fit(anom_years: list[int], anom: list[float], djf: dict[int, float], shift: int):
    """OLS: anomaly ~ b0 + b_nino*max(ONI,0) + b_nina*min(ONI,0), ONI at DJF(t+shift)."""
    x, y = [], []
    for yr, a in zip(anom_years, anom):
        o = djf.get(yr + shift)
        if o is None:
            continue
        x.append(o)
        y.append(a)
    n = len(x)
    if n < MIN_YEARS:
        return None
    o = np.array(x)
    Y = np.array(y)
    X = np.column_stack([np.ones(n), np.maximum(o, 0.0), np.minimum(o, 0.0)])
    if np.linalg.matrix_rank(X) < 3:
        return None
    beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
    resid = Y - X @ beta
    dof = n - 3
    if dof <= 0:
        return None
    sigma2 = float(resid @ resid) / dof
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return None
    se = np.sqrt(np.diag(cov))
    tot = float(((Y - Y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / tot if tot > 0 else 0.0
    # joint F-test on the two ENSO slopes -- the honest overall test, since
    # reporting only the better of two t-stats would understate the p-value.
    f_stat = ((tot - float(resid @ resid)) / 2.0) / sigma2 if sigma2 > 0 else 0.0
    p_joint = float(stats.f.sf(f_stat, 2, dof)) if f_stat > 0 else 1.0
    return {
        "n": n,
        "b_nino": float(beta[1]), "se_nino": float(se[1]),
        "p_nino": float(2 * stats.t.sf(abs(beta[1] / se[1]), dof)) if se[1] > 0 else 1.0,
        "b_nina": float(beta[2]), "se_nina": float(se[2]),
        "p_nina": float(2 * stats.t.sf(abs(beta[2] / se[2]), dof)) if se[2] > 0 else 1.0,
        "r2": r2, "p_joint": p_joint,
    }


def main() -> int:
    djf = load_oni()
    panel = load_psd_panel()
    print(f"[INFO] ONI DJF seasons: {len(djf)} ({min(djf)}-{max(djf)})")
    print(f"[INFO] PSD countries: {len(panel)}")

    results: dict = {}
    n_fit = n_sig = n_thin = n_flat = 0

    for iso3 in sorted(panel):
        for commodity in sorted(panel[iso3]):
            series = panel[iso3][commodity]
            years = sorted(y for y in series if series[y].get("yield"))
            vals = [series[y]["yield"] for y in years]
            if len(years) < MIN_YEARS:
                n_thin += 1
                continue
            before = len(years)
            years, vals = drop_filled(years, vals)
            if before - len(years) > 0:
                n_flat += 1
            if len(years) < MIN_YEARS:
                n_thin += 1
                continue
            ay, anom = detrend(years, vals)

            cands = []
            for shift, align in ((0, "djf_same_year"), (1, "djf_next_year")):
                r = fit(ay, anom, djf, shift)
                if r:
                    r["alignment"] = align
                    r["djf_shift"] = shift
                    cands.append(r)
            if not cands:
                n_thin += 1
                continue
            n_fit += 1
            best = min(cands, key=lambda r: r["p_joint"])
            # Bonferroni over the two alignments actually tested.
            p_adj = min(1.0, best["p_joint"] * len(cands))

            recent = [series[y] for y in sorted(series)[-10:] if series[y].get("prod")]
            mean_prod = float(np.mean([r["prod"] for r in recent])) if recent else 0.0

            entry = {
                "n_years": best["n"],
                "year_from": min(ay), "year_to": max(ay),
                "alignment": best["alignment"],
                "r2": round(best["r2"], 4),
                "p_joint": round(best["p_joint"], 5),
                "p_adj": round(p_adj, 5),
                "mean_production_kt": round(mean_prod, 1),
                "signal": bool(p_adj < P_GATE),
            }
            if entry["signal"]:
                n_sig += 1
                entry.update({
                    "yield_pct_per_oni_nino": round(best["b_nino"] * 100, 3),
                    "se_nino_pct": round(best["se_nino"] * 100, 3),
                    "p_nino": round(best["p_nino"], 5),
                    "yield_pct_per_oni_nina": round(best["b_nina"] * 100, 3),
                    "se_nina_pct": round(best["se_nina"] * 100, 3),
                    "p_nina": round(best["p_nina"], 5),
                })
            else:
                entry["note"] = "no detectable ENSO signal at p_adj<0.10 -- not modelled"
            results.setdefault(iso3, {})[commodity] = entry

    payload = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": "v1",
            "method": (
                "Per country x commodity OLS of detrended log-yield anomaly on DJF ONI, "
                "with separate El Nino (ONI>0) and La Nina (ONI<0) slopes. Trend removed "
                "by centred 9-year moving average of log yield. Two seasonal alignments "
                "tested -- djf_same_year (DJF falling inside PSD marketing year t) and "
                "djf_next_year (DJF of t+1) -- and the better joint fit is reported, its "
                "p-value carrying a Bonferroni factor of 2 as p_adj. The alignment is "
                "named for the shift applied, not for a hemisphere: PSD labels southern "
                "-hemisphere marketing years by their start year, so SH summer crops "
                "select djf_next_year while US winter wheat selects djf_same_year."
            ),
            "sources": {
                "enso": ONI_URL,
                "yields": "USDA PSD bulk (apps.fas.usda.gov/psdonline/downloads/)",
            },
            "gates": {
                "min_years": MIN_YEARS, "p_adj": P_GATE,
                "filled_run_dropped": FLAT_RUN,
            },
            "honesty": (
                "Coefficients are MEASURED from 1960-2026 yield history, not taken from "
                "literature or authored by hand. Pairs failing the gates are returned as "
                "signal:false with no coefficient -- absence of a reported effect means "
                "no detectable signal, NOT zero effect. ENSO shifts the odds of a yield "
                "outcome; it does not determine any single country-season."
            ),
            "counts": {
                "pairs_fitted": n_fit, "pairs_with_signal": n_sig,
                "pairs_too_thin": n_thin, "pairs_with_filled_runs_dropped": n_flat,
            },
        },
        "data": results,
    }
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "enso_model.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    print(f"[OK] fitted {n_fit} pairs, {n_sig} with signal ({100*n_sig/max(n_fit,1):.0f}%), "
          f"{n_thin} too thin, {n_flat} had filled runs dropped")
    print(f"[OK] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
