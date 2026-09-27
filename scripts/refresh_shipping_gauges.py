#!/usr/bin/env python3
"""
refresh_shipping_gauges.py — the measured water behind the El Nino shipping lanes.

Every lane on the Shipping lens used to carry a hand-typed "Now" reading scraped
out of curated prose. This collects the gauges themselves, each from the agency
that owns it, with no key:

  Panama   Gatún Lake level, daily since 1965, and the Canal's 60-day projection
           with its estimated maximum drafts (Panama Canal Authority CSVs)
  Mississippi  St. Louis stage, observed and the NWS forecast (NOAA NWPS, EADM7)
               plus the weekly St. Louis barge rate and the ocean-going grain
               ships loaded at the US Gulf each week (USDA AgTransport, Socrata)
  Rhine    Kaub gauge, 30 days of readings and the station's own reference
           levels (German waterways agency, PEGELONLINE)
  Paraná   Rosario daily height (Argentina INA, series 34)
  Amazon   Manaus / Rio Negro level (Brazil ANA telemetry, station 14990000)

Each gauge is independent: one that fails is recorded under `unavailable` and
the others still publish. Nothing is back-filled from a previous run.

What the Gatún record adds is a climatology: for today's day of year, the
1966-2025 median and 10th/90th percentiles, and the same calendar path in the
El Nino years 1997-98, 2015-16 and 2023-24, so this year's lake can be read
against both normal and the events that forced slot and draft cuts.

The dry-season outlook (gatun.outlook) is this site's model, not the Canal's:
for every year since 1965, the lake on today's calendar day and the El Nino part
of the following December-February ONI, fitted by least squares to the lowest
level the lake reached between 1 January and 15 June. It is scored leave-one-out
against the historical average, driven by NOAA CPC's December-February forecast,
and published with its range. It adds no number the record does not support:
every input is a file in data/ or the Canal's own history.
"""
from __future__ import annotations

import csv
import io
import json
import math
import random
import re
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402

# The Canal server answers 406 to a request without a plain Accept header.
UA = {"User-Agent": "Mozilla/5.0 (FoodShield AI; public food-security dashboard)", "Accept": "*/*"}
GATUN_HIST = "https://evtms-rpts.pancanal.com/eng/h2o/Download_Gatun_Lake_Water_Level_History.csv"
GATUN_PROJ = "https://evtms-rpts.pancanal.com/eng/h2o/Gatun_Water_Level_Projection.csv"
NWPS_OBS = "https://api.water.noaa.gov/nwps/v1/gauges/eadm7/stageflow/observed"
NWPS_FC = "https://api.water.noaa.gov/nwps/v1/gauges/eadm7/stageflow/forecast"
BARGE = "https://agtransport.usda.gov/resource/deqi-uken.json"
VESSELS = "https://agtransport.usda.gov/resource/uiht-9xts.json"
KAUB = "https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations/KAUB/W"
INA = "https://alerta.ina.gob.ar/a5/obs/puntual/series/34/observaciones"
ANA = "https://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"
ANALOGS = (1997, 2015, 2023)   # El Nino onset years whose dry season forced Panama restrictions


def get(url: str, **params):
    return http_get(url, timeout=60, headers=UA, retries=2, params=params or None)


def _same_day(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:            # 29 Feb in a non-leap year
        return d.replace(year=year, day=28)


def gatun(today: date) -> dict:
    rows = []
    for r in csv.DictReader(io.StringIO(get(GATUN_HIST).text)):
        try:
            rows.append((date.fromisoformat(r["DATE_LOG"].strip()), float(r["GATUN_LAKE_LEVEL(FEET)"])))
        except (KeyError, ValueError):
            continue
    if len(rows) < 10000:
        raise RuntimeError(f"Gatún history parse got {len(rows)} rows")
    rows.sort()
    by = dict(rows)
    last_day, last_ft = rows[-1]
    if (today - last_day).days > 10:
        raise RuntimeError(f"Gatún history ends {last_day}: frozen feed")

    def clim(d: date) -> dict | None:
        vals = [by[x] for y in range(1966, 2026) if (x := _same_day(d, y)) in by]
        if len(vals) < 30:
            return None
        q = statistics.quantiles(vals, n=10)
        return {"p10": round(q[0], 2), "p50": round(statistics.median(vals), 2), "p90": round(q[-1], 2), "n_years": len(vals)}

    # A Jun-to-May window around this event's dry season, sampled weekly.
    start = date(last_day.year if last_day.month >= 6 else last_day.year - 1, 6, 1)
    days = [start + timedelta(days=7 * i) for i in range(52)]
    band = [dict(day=d.isoformat()[5:], **(clim(d) or {})) for d in days]
    this = [{"date": d.isoformat(), "ft": by[d]} for d in days if d in by]
    if not this or this[-1]["date"] != last_day.isoformat():
        this.append({"date": last_day.isoformat(), "ft": last_ft})
    analogs = {}
    for y in ANALOGS:
        pts = []
        for d in days:
            x = _same_day(d, y + (d.year - start.year))
            if x in by:
                pts.append({"day": d.isoformat()[5:], "ft": by[x]})
        analogs[f"{y}-{str(y + 1)[2:]}"] = pts
    # The true low of each analog year from the DAILY record over the same
    # Jun-May window, with its date: the weekly samples above miss the bottom,
    # and the month matters (2023's low came in the wet season, July).
    analog_min = {}
    for y in ANALOGS:
        lo = date(y, 6, 1)
        window = [(d, v) for d, v in rows if lo <= d < lo + timedelta(days=366)]
        if window:
            d_min, v_min = min(window, key=lambda t: t[1])
            analog_min[f"{y}-{str(y + 1)[2:]}"] = {"ft": v_min, "date": d_min.isoformat()}

    proj = []
    lines = [ln for ln in get(GATUN_PROJ).text.splitlines() if ln.strip()]
    hdr = next((i for i, ln in enumerate(lines) if ln.lower().startswith("projected_date")), None)
    if hdr is not None:
        for r in csv.DictReader(io.StringIO("\n".join(lines[hdr:])), skipinitialspace=True):
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            try:
                proj.append({"date": datetime.strptime(r["projected_date"], "%m/%d/%Y").date().isoformat(),
                             "ft": float(r["projected_gatun_water_level"]),
                             "neopanamax_draft_ft": float(r["max_neopanamax_draft_ft"])})
            except (KeyError, ValueError):
                continue
        proj.sort(key=lambda p: p["date"])
    now_clim = clim(last_day) or {}
    try:
        outlook = gatun_outlook(rows, last_day, last_ft)
    except Exception as e:  # noqa: BLE001 -- the model must never cost the reading
        outlook = None
        print(f"  gatun outlook skipped: {type(e).__name__}: {e}")
    return {
        "name": "Gatún Lake", "unit": "ft", "source": "Panama Canal Authority", "url": GATUN_HIST,
        "latest": {"date": last_day.isoformat(), "value": last_ft},
        "climatology_today": now_clim,
        "vs_median_ft": round(last_ft - now_clim["p50"], 2) if now_clim else None,
        "window_start": start.isoformat(), "band": band, "this_year": this, "analogs": analogs, "analog_min": analog_min,
        "projection": proj, "projection_url": GATUN_PROJ,
        "projection_note": "ACP's own estimate; official drafts are set only by Advisories to Shipping.",
        "outlook": outlook, "monthly_2019": lake_monthly(rows),
    }


DATA = Path(__file__).resolve().parent.parent / "data"


def _read(name: str) -> dict:
    try:
        d = json.loads((DATA / name).read_text())
        return d.get("data", d) if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination for the small normal equations below."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def _inv(A: list[list[float]]) -> list[list[float]]:
    n = len(A)
    cols = [_solve(A, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def _ols(X: list[list[float]], y: list[float]):
    k = len(X[0])
    XtX = [[sum(r[i] * r[j] for r in X) for j in range(k)] for i in range(k)]
    Xty = [sum(r[i] * v for r, v in zip(X, y)) for i in range(k)]
    b = _solve(XtX, Xty)
    res = [v - sum(bi * xi for bi, xi in zip(b, r)) for r, v in zip(X, y)]
    s2 = sum(e * e for e in res) / (len(y) - k)
    inv = _inv(XtX)
    return b, [[s2 * inv[i][j] for j in range(k)] for i in range(k)], math.sqrt(s2), res


def _chol(C: list[list[float]]) -> list[list[float]]:
    n = len(C)
    Lm = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            t = C[i][j] - sum(Lm[i][k] * Lm[j][k] for k in range(j))
            Lm[i][j] = math.sqrt(max(t, 1e-12)) if i == j else t / Lm[j][j]
    return Lm


def gatun_outlook(rows: list[tuple[date, float]], last_day: date, last_ft: float) -> dict | None:
    """How low the lake may go next dry season, from its own record and the winter ENSO forecast."""
    if not (8 <= last_day.month <= 12):
        return None   # the fit is made before the dry season, not during it
    by = dict(rows)
    djf = {h["year"]: h["anom"] for h in _read("enso.json").get("history", []) if isinstance(h.get("anom"), (int, float))}
    pts = []
    for y in range(1965, last_day.year):
        d0 = _same_day(last_day, y)
        season = [v for d, v in rows if date(y + 1, 1, 1) <= d <= date(y + 1, 6, 15)]
        if d0 in by and len(season) >= 150 and (y + 1) in djf:
            pts.append({"season": y + 1, "level_then": by[d0], "oni_djf": djf[y + 1], "low": min(season)})
    if len(pts) < 40:
        return None
    X = [[1.0, p["level_then"], max(p["oni_djf"], 0.0)] for p in pts]
    Y = [p["low"] for p in pts]
    b, cov, sd, res = _ols(X, Y)
    # Leave-one-out: the fit against the historical average, each season predicted without itself.
    loo, loo_clim = [], []
    for i in range(len(pts)):
        Xi, Yi = X[:i] + X[i + 1:], Y[:i] + Y[i + 1:]
        bi, _, _, _ = _ols(Xi, Yi)
        loo.append(Y[i] - sum(a * c for a, c in zip(bi, X[i])))
        loo_clim.append(Y[i] - sum(Yi) / len(Yi))
    rmse = lambda e: math.sqrt(sum(x * x for x in e) / len(e))
    for p, e in zip(pts, res):
        p["fit"] = round(p["low"] - e, 2)
    recent = [e for p, e in zip(pts, res) if p["season"] >= 2017]
    # The driver: CPC's December-February forecast on RONI, moved to ONI by this year's ONI-RONI gap.
    ro = next((r for r in _read("enso_strengths.json").get("roni_outlook", []) if r.get("season") == "DJF"), None)
    idx = {r.get("key"): r for r in _read("enso_indices.json").get("indices", [])}
    gap = (idx["oni"]["value"] - idx["roni"]["value"]) if "oni" in idx and "roni" in idx else None
    scen = []
    latest = _read("enso.json").get("latest", {})
    if isinstance(latest.get("anom"), (int, float)):
        scen.append({"key": "today", "label": f"ONI stays at today's {latest['anom']:+.1f}", "oni": latest["anom"]})
    fc = None
    if ro and gap is not None:
        fc = {"median": ro["median"] + gap, "p05": ro["p05"] + gap, "p95": ro["p95"] + gap,
              "roni_median": ro["median"], "roni_p05": ro["p05"], "roni_p95": ro["p95"], "gap": round(gap, 2),
              "issued": ro.get("issued"), "label": ro.get("label")}
        scen.append({"key": "cpc", "label": f"CPC's median for {ro.get('label', 'DJF')} (about ONI {fc['median']:+.1f})", "oni": fc["median"]})
    for sc in scen:
        sc["low"] = round(b[0] + b[1] * last_ft + b[2] * max(sc["oni"], 0.0), 2)
        sc["oni"] = round(sc["oni"], 2)
    record = min(pts, key=lambda p: p["low"])
    dist = None
    if fc:
        # Monte Carlo over the coefficients, the residual and CPC's forecast spread (5th-95th percentile as +-1.645 sd).
        rng, Lc = random.Random(20260927), _chol(cov)
        mu, sdo = fc["median"], (fc["p95"] - fc["p05"]) / 3.29
        draws = []
        for _ in range(20000):
            z = [rng.gauss(0, 1) for _ in range(3)]
            bb = [b[i] + sum(Lc[i][k] * z[k] for k in range(3)) for i in range(3)]
            o = rng.gauss(mu, sdo)
            draws.append(bb[0] + bb[1] * last_ft + bb[2] * max(o, 0.0) + rng.gauss(0, sd))
        draws.sort()
        q = lambda f: round(draws[int(f * (len(draws) - 1))], 2)
        dist = {"p05": q(.05), "p10": q(.10), "p50": q(.50), "p90": q(.90), "p95": q(.95),
                "p_below_record": round(sum(1 for v in draws if v < record["low"]) / len(draws), 3),
                "record_ft": record["low"], "record_season": record["season"]}
    # The last El Nino's slot cuts, each read against the lake level on its date.
    lanes = _read("enso_lanes.json").get("lanes", [])
    pan = next((ln for ln in lanes if ln.get("id") == "panama"), {})
    ladder = []
    for st in ((pan.get("precedent_2023") or {}).get("steps") or []):
        try:
            d = date.fromisoformat(st["date"])
        except (KeyError, ValueError):
            continue
        if d in by:
            ladder.append({"date": st["date"], "slots": st.get("total"), "lake_ft": by[d]})
    return {
        "method": "Least squares on the Canal's daily record: lowest level 1 Jan-15 Jun against the level on "
                  f"{last_day.strftime('%d %b')} the previous year and max(0, December-February ONI).",
        "n_seasons": len(pts), "first_season": pts[0]["season"], "last_season": pts[-1]["season"],
        "coef": {"intercept": round(b[0], 3), "level_then": round(b[1], 3), "el_nino_oni": round(b[2], 3)},
        "se": {"level_then": round(math.sqrt(cov[1][1]), 3), "el_nino_oni": round(math.sqrt(cov[2][2]), 3)},
        "resid_sd_ft": round(sd, 2), "loo_rmse_ft": round(rmse(loo), 2), "loo_rmse_average_ft": round(rmse(loo_clim), 2),
        "recent_mean_resid_ft": round(sum(recent) / len(recent), 2) if recent else None, "recent_from": 2017,
        "level_now": last_ft, "level_date": last_day.isoformat(), "forecast": fc, "scenarios": scen,
        "distribution": dist, "points": pts, "ladder_2023": ladder,
    }


def lake_monthly(rows: list[tuple[date, float]], since: int = 2019) -> list[dict]:
    """Monthly mean lake level, for reading the canal's transits against the water behind them."""
    acc: dict[str, list[float]] = {}
    for d, v in rows:
        if d.year >= since:
            acc.setdefault(d.strftime("%Y-%m"), []).append(v)
    return [{"month": m, "ft": round(sum(v) / len(v), 2)} for m, v in sorted(acc.items())]


def stlouis() -> dict:
    def series(url):
        j = get(url).json()
        return j, [{"t": p["validTime"], "ft": p["primary"], "kcfs": p.get("secondary")}
                   for p in j.get("data", []) if isinstance(p.get("primary"), (int, float)) and p["primary"] > -900]
    _, obs = series(NWPS_OBS)
    fj, fc = series(NWPS_FC)
    if not obs:
        raise RuntimeError("St. Louis observed series empty")
    daily = {}
    for p in obs:                      # last reading of each day
        daily[p["t"][:10]] = p
    fmin = min(fc, key=lambda p: p["ft"]) if fc else None
    fmax = max(fc, key=lambda p: p["ft"]) if fc else None
    return {
        "name": "Mississippi at St. Louis", "unit": "ft", "source": "NOAA National Water Prediction Service (EADM7)",
        "url": "https://water.noaa.gov/gauges/eadm7",
        "latest": {"date": obs[-1]["t"], "value": obs[-1]["ft"], "kcfs": obs[-1].get("kcfs")},
        "observed_30d": [{"date": k, "ft": v["ft"]} for k, v in sorted(daily.items())][-30:],
        "forecast": [{"t": p["t"], "ft": p["ft"]} for p in fc],
        "forecast_issued": fj.get("issuedTime"),
        "forecast_min": {"t": fmin["t"], "ft": fmin["ft"], "is_last_point": fmin is fc[-1]} if fmin else None,
        "forecast_max": {"t": fmax["t"], "ft": fmax["ft"]} if fmax else None,
    }


def barge() -> dict:
    rows = get(BARGE, **{"location": "St. Louis", "$order": "date DESC", "$limit": "60"}).json()
    # `rate` is a text column: sort it as a number or "999" outranks "2653".
    peak = get(BARGE, **{"location": "St. Louis", "$select": "date,rate", "$where": "rate IS NOT NULL",
                         "$order": "rate::number DESC", "$limit": "1"}).json()
    pts = [{"date": r["date"][:10], "pct_tariff": round(float(r["rate"]), 1)} for r in rows if r.get("rate")]
    if not pts:
        raise RuntimeError("barge rate series empty")
    return {
        "name": "St. Louis downbound grain barge rate", "unit": "% of 1976 benchmark tariff",
        "source": "USDA AMS AgTransport", "url": "https://agtransport.usda.gov/Barge/Downbound-Grain-Barge-Rates/deqi-uken",
        "latest": {"date": pts[0]["date"], "value": pts[0]["pct_tariff"]},
        "weekly_52": list(reversed(pts[:52])),
        "record": {"date": peak[0]["date"][:10], "value": round(float(peak[0]["rate"]), 1)} if peak else None,
    }


def gulf_loadings(today: date) -> dict:
    """Grain ships loaded at the US Gulf in the past 7 days, weekly (USDA GTR).

    The river is only half of the Mississippi chain: what low water costs shows
    up at the Gulf elevators as fewer ships loaded. The same week in the five
    previous years is the baseline, because loadings are strongly seasonal."""
    rows = get(VESSELS, **{"port": "Gulf", "$select": "date,week,year,loaded_7_days,due_10_days",
                           "$where": "loaded_7_days IS NOT NULL", "$order": "date DESC", "$limit": "400"}).json()
    pts = [{"date": r["date"][:10], "week": int(r["week"]), "year": int(r["year"]),
            "loaded": int(float(r["loaded_7_days"])),
            "due": int(float(r["due_10_days"])) if r.get("due_10_days") else None} for r in rows]
    if not pts:
        raise RuntimeError("Gulf vessel loading series empty")
    last = pts[0]
    age = (today - date.fromisoformat(last["date"])).days
    if age > 35:
        raise RuntimeError(f"Gulf vessel loadings last reported {last['date']}, {age} days ago: stale")
    prior = [p["loaded"] for p in pts
             if last["year"] - 5 <= p["year"] < last["year"] and abs(p["week"] - last["week"]) <= 1]
    # The mean is kept, but a single disrupted season drags it down (Hurricane
    # Ida shut Gulf elevators in weeks 35-37 of 2021), so the median is the
    # baseline to read against.
    return {
        "name": "Grain ships loaded at the US Gulf, past 7 days", "unit": "ocean-going vessels",
        "source": "USDA AMS Grain Transportation Report, via AgTransport",
        "url": "https://agtransport.usda.gov/d/uiht-9xts",
        "latest": {"date": last["date"], "value": last["loaded"], "due_10_days": last["due"]},
        "same_week_5y": {"mean": round(statistics.mean(prior), 1), "median": round(statistics.median(prior), 1),
                         "n": len(prior), "years": f"{last['year'] - 5}-{last['year'] - 1}",
                         "window_label": f"weeks {last['week'] - 1}–{last['week'] + 1}, {last['year'] - 5}–{last['year'] - 1}"}
                        if len(prior) >= 5 else None,
        "weekly_52": [{"date": p["date"], "value": p["loaded"]} for p in reversed(pts[:52])],
    }


def kaub() -> dict:
    meta = get(KAUB + ".json", includeCurrentMeasurement="true", includeCharacteristicValues="true").json()
    meas = get(KAUB + "/measurements.json", start="P30D").json()
    daily = {}
    for m in meas:
        daily.setdefault(m["timestamp"][:10], []).append(m["value"])
    cur = meta.get("currentMeasurement") or {}
    if not isinstance(cur.get("value"), (int, float)):
        raise RuntimeError("Kaub current reading missing")
    ref = {}
    for c in meta.get("characteristicValues", []):
        if c.get("shortname") in ("NW", "GlW", "MNW", "MW"):
            ref[c["shortname"]] = {"value": c.get("value"), "label": c.get("longname"),
                                   "from": (c.get("timespanStart") or "")[:10], "to": (c.get("timespanEnd") or "")[:10],
                                   "occurred": (c.get("occurences") or [None])[0]}
    return {
        "name": "Rhine at Kaub", "unit": "cm", "source": "German Federal Waterways and Shipping Administration (PEGELONLINE)",
        "url": "https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=2335",
        "latest": {"date": cur.get("timestamp"), "value": cur["value"], "state": cur.get("stateMnwMhw")},
        "daily_30d": [{"date": d, "cm": round(statistics.mean(v))} for d, v in sorted(daily.items())],
        "reference": ref,
    }


def rosario(today: date) -> dict:
    j = get(INA, timestart=(today - timedelta(days=45)).isoformat(), timeend=(today + timedelta(days=1)).isoformat()).json()
    pts = sorted(({"date": o["timestart"][:10], "m": o["valor"]} for o in j if isinstance(o.get("valor"), (int, float))),
                 key=lambda p: p["date"])
    if not pts:
        raise RuntimeError("Rosario series empty")
    return {
        "name": "Paraná at Rosario", "unit": "m",
        "source": "Instituto Nacional del Agua (INA), series 34; gauge operated by Prefectura Naval Argentina",
        "url": "https://alerta.ina.gob.ar/a5/obs/puntual/series/?estacion_id=34&var_id=2&proc_id=1",
        "latest": {"date": pts[-1]["date"], "value": pts[-1]["m"]}, "daily_45d": pts,
    }


def manaus(today: date) -> dict:
    xml = get(ANA, codEstacao="14990000", dataInicio=(today - timedelta(days=30)).strftime("%d/%m/%Y"),
              dataFim=today.strftime("%d/%m/%Y")).text
    pts = {}
    for block in re.findall(r"<DadosHidrometereologicos[^>]*>(.*?)</DadosHidrometereologicos>", xml, flags=re.S):
        t = re.search(r"<DataHora>([^<]+)</DataHora>", block)
        n = re.search(r"<Nivel>([^<]+)</Nivel>", block)
        if t and n and n.group(1).strip():
            try:
                pts.setdefault(t.group(1).strip()[:10], float(n.group(1).strip()) / 100)
            except ValueError:
                pass
    if not pts:
        raise RuntimeError("Manaus series empty")
    series = sorted(({"date": d, "m": round(v, 2)} for d, v in pts.items()), key=lambda p: p["date"])
    return {
        "name": "Rio Negro at Manaus", "unit": "m", "source": "Agência Nacional de Águas (ANA) telemetry, station 14990000",
        "url": "https://www.snirh.gov.br/hidrotelemetria/",
        "latest": {"date": series[-1]["date"], "value": series[-1]["m"]}, "daily_30d": series,
    }


def main() -> int:
    today = datetime.now(timezone.utc).date()
    gauges, unavailable = {}, []
    for key, fn in (("gatun", lambda: gatun(today)), ("stlouis", stlouis), ("barge_stlouis", barge),
                    ("gulf_loadings", lambda: gulf_loadings(today)),
                    ("kaub", kaub), ("rosario", lambda: rosario(today)), ("manaus", lambda: manaus(today))):
        try:
            gauges[key] = fn()
            print(f"  ok   {key}: {gauges[key]['latest']}")
        except Exception as e:  # noqa: BLE001 -- one agency down must not blank the rest
            unavailable.append({"key": key, "reason": f"{type(e).__name__}: {e}"})
            print(f"  FAIL {key}: {e}")
    if not gauges:
        raise RuntimeError("no gauge reachable -- keeping the previous file")
    write_json("enso_gauges.json", {"gauges": gauges, "unavailable": unavailable},
               source="Panama Canal Authority; NOAA NWPS; USDA AMS AgTransport; WSV PEGELONLINE; INA Argentina; ANA Brazil",
               notes="Measured water levels behind the El Niño shipping lanes, each from the agency that owns the gauge.",
               status="ok" if not unavailable else "partial")
    return 0


if __name__ == "__main__":
    sys.exit(main())
