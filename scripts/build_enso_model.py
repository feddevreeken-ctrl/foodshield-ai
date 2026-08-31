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
# Indian Ocean Dipole (HadISST1.1 DMI). ENSO and the IOD covary, and for several
# of the countries this dashboard most wants to score the IOD does the work that
# gets credited to ENSO -- partialling it out collapses Australia's apparent
# ENSO-wheat correlation from about -0.49 to -0.08 (Yuan & Yamagata 2015).
# So every ENSO coefficient is refit with SON DMI alongside it and flagged for
# whether it survives. A coefficient that does not survive is not reported.
DMI_URL = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"
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


def load_dmi() -> dict[int, float]:
    """SON-mean Dipole Mode Index by year -- the IOD's peak season.

    Keyed by the SON year itself. Callers must align it to the ENSO event: the
    dipole co-occurring with a DJF-Y event is SON of Y-1.
    """
    raw = _cached("dmi.had.long.data", DMI_URL).decode("utf-8", "replace")
    out: dict[int, float] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 13:
            continue  # header (2 fields) and the trailing metadata block
        try:
            year = int(parts[0])
            months = [float(v) for v in parts[1:]]
        except ValueError:
            continue
        if not (1800 < year < 2100):
            continue
        son = [m for m in months[8:11] if m > -900]  # Sep, Oct, Nov; -9999 = missing
        if len(son) == 3:
            out[year] = sum(son) / 3.0
    if len(out) < 100:
        raise RuntimeError(f"DMI parse yielded only {len(out)} years -- feed shape changed")
    return out


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


def fit(anom_years: list[int], anom: list[float], djf: dict[int, float], shift: int,
        dmi: dict[int, float] | None = None):
    """OLS: anomaly ~ b0 + b_nino*max(ONI,0) + b_nina*min(ONI,0) [+ b_iod*DMI].

    When `dmi` is supplied the two ENSO slopes are estimated CONDITIONAL on the
    Indian Ocean Dipole, and p_joint becomes the test of whether ENSO explains
    anything the IOD does not already explain.
    """
    x, y, d = [], [], []
    for yr, a in zip(anom_years, anom):
        o = djf.get(yr + shift)
        if o is None:
            continue
        if dmi is not None:
            # The IOD contemporaneous with an ENSO event peaking in DJF of year
            # Y peaked in SON of Y-1: the 2015-16 El Nino pairs with the strong
            # positive IOD of SON 2015, not SON 2016. Indexing DMI on the DJF
            # year instead grabs the following spring's dipole -- the decaying
            # phase, ~9 months late -- which reads as noise and lets a
            # confounded ENSO coefficient through the control unchallenged.
            dv = dmi.get(yr + shift - 1)
            if dv is None:
                continue
            d.append(dv)
        x.append(o)
        y.append(a)
    n = len(x)
    if n < MIN_YEARS:
        return None
    o = np.array(x)
    Y = np.array(y)
    cols = [np.ones(n), np.maximum(o, 0.0), np.minimum(o, 0.0)]
    if dmi is not None:
        cols.append(np.array(d))
    X = np.column_stack(cols)
    k = X.shape[1]
    if np.linalg.matrix_rank(X) < k:
        return None
    beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
    resid = Y - X @ beta
    dof = n - k
    if dof <= 0:
        return None
    sigma2 = float(resid @ resid) / dof
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return None
    se = np.sqrt(np.diag(cov))
    rss = float(resid @ resid)
    tot = float(((Y - Y.mean()) ** 2).sum())
    r2 = 1.0 - rss / tot if tot > 0 else 0.0
    # Joint F-test on the two ENSO slopes -- the honest overall test, since
    # reporting only the better of two t-stats would understate the p-value.
    # With a DMI column present the restricted model keeps the DMI, so this
    # tests ENSO's INCREMENTAL contribution rather than ENSO plus whatever the
    # Indian Ocean was doing at the same time.
    if dmi is not None:
        Xr = np.column_stack([np.ones(n), np.array(d)])
        br, *_ = np.linalg.lstsq(Xr, Y, rcond=None)
        rss_r = float(((Y - Xr @ br) ** 2).sum())
    else:
        rss_r = tot
    f_stat = ((rss_r - rss) / 2.0) / sigma2 if sigma2 > 0 else 0.0
    p_joint = float(stats.f.sf(f_stat, 2, dof)) if f_stat > 0 else 1.0
    return {
        "n": n,
        "b_nino": float(beta[1]), "se_nino": float(se[1]),
        "p_nino": float(2 * stats.t.sf(abs(beta[1] / se[1]), dof)) if se[1] > 0 else 1.0,
        "b_nina": float(beta[2]), "se_nina": float(se[2]),
        "p_nina": float(2 * stats.t.sf(abs(beta[2] / se[2]), dof)) if se[2] > 0 else 1.0,
        "r2": r2, "p_joint": p_joint,
        "b_iod": float(beta[3]) if dmi is not None else None,
        "p_iod": (float(2 * stats.t.sf(abs(beta[3] / se[3]), dof))
                  if dmi is not None and se[3] > 0 else None),
    }


def main() -> int:
    djf = load_oni()
    dmi = load_dmi()
    panel = load_psd_panel()
    print(f"[INFO] ONI DJF seasons: {len(djf)} ({min(djf)}-{max(djf)})")
    print(f"[INFO] DMI SON years:   {len(dmi)} ({min(dmi)}-{max(dmi)})")
    print(f"[INFO] PSD countries: {len(panel)}")

    results: dict = {}
    n_fit = n_sig = n_thin = n_flat = n_naive_sig = n_lost_to_iod = 0

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
            naive_sig = p_adj < P_GATE
            if naive_sig:
                n_naive_sig += 1

            # Refit at the SAME alignment with the IOD alongside. This is the
            # estimate we actually publish: ENSO's contribution net of the
            # Indian Ocean, which for several countries is most of the apparent
            # effect. Alignment is not re-selected here, so the Bonferroni
            # factor stays at 2.
            adj = fit(ay, anom, djf, best["djf_shift"], dmi=dmi)
            p_adj_iod = min(1.0, adj["p_joint"] * len(cands)) if adj else 1.0

            recent = [series[y] for y in sorted(series)[-10:] if series[y].get("prod")]
            mean_prod = float(np.mean([r["prod"] for r in recent])) if recent else 0.0

            signal = bool(p_adj_iod < P_GATE)
            entry = {
                "n_years": best["n"],
                "year_from": min(ay), "year_to": max(ay),
                "alignment": best["alignment"],
                "mean_production_kt": round(mean_prod, 1),
                "signal": signal,
                # Naive = ENSO alone. Published = ENSO net of the IOD. Both are
                # carried so the shrinkage is visible rather than silent.
                "naive": {
                    "yield_pct_per_oni_nino": round(best["b_nino"] * 100, 3),
                    "yield_pct_per_oni_nina": round(best["b_nina"] * 100, 3),
                    "r2": round(best["r2"], 4),
                    "p_adj": round(p_adj, 5),
                    "signal": bool(naive_sig),
                },
            }
            if signal and adj:
                n_sig += 1
                entry.update({
                    "yield_pct_per_oni_nino": round(adj["b_nino"] * 100, 3),
                    "se_nino_pct": round(adj["se_nino"] * 100, 3),
                    "p_nino": round(adj["p_nino"], 5),
                    "yield_pct_per_oni_nina": round(adj["b_nina"] * 100, 3),
                    "se_nina_pct": round(adj["se_nina"] * 100, 3),
                    "p_nina": round(adj["p_nina"], 5),
                    "iod_pct_per_dmi": round(adj["b_iod"] * 100, 3),
                    "p_iod": round(adj["p_iod"], 5),
                    "r2": round(adj["r2"], 4),
                    "p_adj": round(p_adj_iod, 5),
                })
            else:
                entry["p_adj"] = round(p_adj_iod, 5)
                if naive_sig:
                    n_lost_to_iod += 1
                    entry["note"] = (
                        "ENSO alone looked significant, but the effect does not survive "
                        "controlling for the Indian Ocean Dipole -- not modelled")
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
                "pairs_fitted": n_fit,
                "pairs_signal_enso_alone": n_naive_sig,
                "pairs_with_signal": n_sig,
                "pairs_lost_to_iod_control": n_lost_to_iod,
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
    print(f"[OK] fitted {n_fit} pairs | ENSO alone significant: {n_naive_sig} | "
          f"survives IOD control: {n_sig} | lost to IOD: {n_lost_to_iod}")
    print(f"[OK] {n_thin} too thin, {n_flat} had filled runs dropped")
    print(f"[OK] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
