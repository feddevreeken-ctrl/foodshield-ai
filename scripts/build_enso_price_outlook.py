"""
El Niño price outlook — domestic staple prices in the countries El Niño hits hardest, set against the last
two strong El Niños at the same stage, with an analog path and a skill-gated model.

Sources (all already feeding the site): FAO GIEWS FPMA Tool domestic price API (the one refresh_fpma_prices.py
and refresh_enso_price_analogs.py read; attribute FAO GIEWS FPMA), the World Bank Pink Sheet monthly workbook (the
one refresh_worldbank_pink_sheet.py reads) and US CPI (FRED CPIAUCSL), FoodShield's harvest model
(data/enso_model.json, data/enso_outlook.json), the region cards and published effects
(data/enso_regions.json, data/enso_published_effects.json) and CPC's ONI (data/enso.json).

Universe (UNIVERSE_RULE below): a country x staple is in when El Niño's effect on that staple's harvest there is
documented as harmful by (a) FoodShield's harvest model (outlook row shown, negative El Niño slope), (b) a
drying region card rated high or medium confidence, or (c) a published effect row, AND FPMA carries a
CPI-deflated monthly series for that staple that is current and reaches back to March 2015 or March 2023.
One series per pair, named in SERIES: a national average where FPMA has one, else the longest-running capital
or benchmark market (the same rule as refresh_enso_price_analogs.py). Pairs that qualify but have no usable
series are listed in _meta.excluded with the reason.

Per row, all in real (CPI-deflated) terms:
  aftermath  change from March of the El Niño year to the latest month, now and in 2015-16 / 2023-24 at the
             same month offset from the December peak.
  analog     the latest value carried along each past event's own path (index: latest month = 100), through
             March after the peak (+15 months). A replay, not a forecast.
  effect_replay  the El Niño part only: each past event's real path minus the normal seasonal swing (median real
             change per calendar month in non-El-Niño years) minus the series' world-market pass-through (beta x the
             World Bank Pink Sheet price, deflated by US CPI; beta fitted on non-El-Niño months, kept only if
             significant on 48+ months, clipped to 0..1). Forward view = a normal year + that excess. Scored
             leave-one-event-out against no change, the plain replay and the normal year alone; published regardless.
  model      peak real-price rise from the latest month over the 16 months December..March+1, from a pooled
             OLS on two predictors fixed before fitting (n_specs_tried = 1): the country's own fitted El Niño
             yield slope x the event's DJF ONI, and the price at the origin month vs its own 3-year median.
             Scored leave-one-event-out against no change and the analog average. It is published only if its
             pooled MAE beats both and it beats the analog on at least half of the held-out events; otherwise
             model_status = "no_skill" and every model field is null.

Output: data/enso_price_outlook.json
"""
import json
import math
import re
import statistics
from datetime import date

from _common import DATA_DIR, http_get, write_json

API = "https://fpma.fao.org/giews/v4/global/price_module/api/v1"
TOOL_URL = "https://fpma.fao.org/giews/fpmat4/"
SOURCE = ("FAO GIEWS FPMA Tool (domestic prices API, CPI-deflated); World Bank Pink Sheet (monthly, deflated by US CPI, "
          "FRED CPIAUCSL); FoodShield El Niño harvest model; NOAA CPC ONI")
FRED_CPI = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# iso3, market, commodity, price type (as FPMA names them), staple key
SERIES = [
    ("ZAF", "Randfontein", "Maize (white)", "WHOLESALE", "maize"),
    ("ZMB", "National Average", "Maize (white)", "RETAIL", "maize"),
    ("MWI", "National Average", "Maize", "RETAIL", "maize"),
    ("MOZ", "Maputo", "Maize (white)", "RETAIL", "maize"),
    ("SWZ", "National Average", "Maize meal", "RETAIL", "maize"),
    ("LSO", "Maseru", "Maize meal", "RETAIL", "maize"),
    ("NAM", "Windhoek", "Maize meal", "RETAIL", "maize"),
    ("BWA", "National Average", "Maize meal", "RETAIL", "maize"),
    ("MDG", "Antananarivo Average", "Maize (yellow)", "RETAIL", "maize"),
    ("GTM", "National Average", "Maize (white)", "WHOLESALE", "maize"),
    ("GTM", "National Average", "Beans (black)", "WHOLESALE", "beans"),
    ("HND", "National Average", "Maize (white)", "WHOLESALE", "maize"),
    ("HND", "National Average", "Beans (red)", "WHOLESALE", "beans"),
    ("ETH", "Bahirdar", "Teff (mixed)", "WHOLESALE", "cereals"),
    ("IDN", "National Average", "Rice", "RETAIL", "rice"),
    ("PHL", "National Average", "Rice (regular milled)", "RETAIL", "rice"),
    ("IND", "National Average", "Rice", "RETAIL", "rice"),
    ("THA", "Bangkok", "Rice (5% broken)", "WHOLESALE", "rice"),
    ("BRA", "São Paulo", "Rice (milled, fine long-grain, type 1)", "RETAIL", "rice"),
    ("BRA", "São Paulo", "Maize (yellow)", "WHOLESALE", "maize"),
]
# Region card -> the staples whose harvest it is about (cards about fish, palm oil or floods have none).
REGION_STAPLES = {
    "southern_africa_maize": ["maize"], "ethiopia_kiremt": ["cereals"], "indonesia_rice": ["rice"],
    "philippines_rice": ["rice"], "brazil_centrewest": ["maize"], "central_america_dry_corridor": ["maize", "beans"],
    "india_monsoon": ["rice"], "sahel_millet": ["millet", "sorghum"], "australia_wheat": ["wheat"],
}
STAPLE_RE = {"maize": r"maize|corn", "rice": r"rice", "beans": r"bean", "cereals": r"cereal|maize|teff|sorghum|wheat|millet", "sorghum": r"sorghum",
             "wheat": r"wheat", "millet": r"millet", "barley": r"barley"}
MODEL_CROP = {"maize": "corn", "rice": "rice", "sorghum": "sorghum", "wheat": "wheat", "millet": "millet"}
CROP_STAPLE = {"corn": "maize"}
UNIVERSE_RULE = ("A country x staple is in when El Niño's effect on that staple's harvest there is documented as "
                 "harmful by FoodShield's harvest model (outlook row shown, negative El Niño slope), a drying region "
                 "card rated high or medium confidence, or a published-effect row; AND FPMA has a CPI-deflated "
                 "monthly series for that staple that runs to within 7 months of today and reaches back to March "
                 "2015 or March 2023. One series per pair: national average where FPMA has one, else the "
                 "longest-running capital or benchmark market.")

CURRENT = ("2026-27", "2026-12")
ANALOGS = ("2015-16", "2023-24")
K_BASE, K_END = -9, 15        # March of the El Niño year; March after the December peak
MAX_AGE_MONTHS = 7
MIN_WINDOW = 12               # of the 16 target months, at least this many observed
MIN_SAMPLES, MIN_EVENTS = 10, 3
ROW_MIN_EVENTS = 5           # a row shows model numbers only with this many held-out events of its own
PREDICTORS = ["shortfall_pct", "rel_3y_pct"]
# World-market benchmark per staple (World Bank Pink Sheet column label). Beans and teff have none: beta = 0.
WORLD = {"maize": ("Maize", "World Bank Pink Sheet maize (US Gulf), deflated by US CPI"),
         "rice": ("Rice, Thai 5%", "World Bank Pink Sheet rice (Thai 5% broken), deflated by US CPI")}
SEASON_MIN_YEARS = 3          # every calendar month needs this many non-El-Niño years, else the series has no normal year
BETA_MIN_MONTHS, BETA_T = 48, 1.96
# FPMA's CPI deflator for these steps near-constantly over its last months, which looks extrapolated: their last three
# months are dropped from the map fill and from the price-risk band (build_price_risk_band.py reads this).
TAIL_TRIM = {"MDG": 3, "ETH": 3}
GAP_MIN_MONTHS = 12          # a run of missing months this long inside a series is flagged in series.gap


def mi(iso):
    return int(iso[:4]) * 12 + int(iso[5:7]) - 1


def month(m):
    return f"{m // 12}-{m % 12 + 1:02d}"


def _gap(real):
    """Longest run of missing months between two observed ones, when it is GAP_MIN_MONTHS or more, else None."""
    ms = sorted(real)
    best = max(((b - a - 1, a + 1, b - 1) for a, b in zip(ms, ms[1:])), default=None)
    return {"months": best[0], "from": month(best[1]), "to": month(best[2])} if best and best[0] >= GAP_MIN_MONTHS else None


def pct(a, b):
    return round((a / b - 1) * 100, 1)


def el_nino_events(history, first_year=2000, before=CURRENT[1]):
    """Past El Niños from CPC's DJF ONI: DJF >= 0.5; back-to-back El Niño winters are one event at the stronger peak."""
    djf = {h["year"]: h["anom"] for h in history if h.get("season") == "DJF" and isinstance(h.get("anom"), (int, float))}
    out, run = [], []
    for y in sorted(djf) + [None]:
        if y is not None and djf[y] >= 0.5 and (not run or run[-1] == y - 1):
            run.append(y)
            continue
        if run:
            top = max(run, key=lambda r: djf[r])
            if top - 1 >= first_year and f"{top - 1}-12" < before:
                out.append({"label": f"{top - 1}-{str(top)[2:]}", "peak": f"{top - 1}-12", "oni": djf[top]})
        run = [y] if y is not None and djf[y] >= 0.5 else []
    return out


def rel_3y(real, m):
    past = [real[j] for j in range(m - 36, m) if j in real]
    return pct(real[m], statistics.median(past)) if len(past) >= 24 and m in real else None


def peak_rise(real, p, k0):
    """Peak real price over December..March+1 (k 0..15) vs the price at the origin month p+k0."""
    base = real.get(p + k0)
    win = [real[p + k] for k in range(0, K_END + 1) if (p + k) in real]
    return pct(max(win), base) if base and len(win) >= MIN_WINDOW else None


def aftermath(real, p, k0):
    a, b = real.get(p + k0), real.get(p + K_BASE)
    return {"pct": pct(a, b), "from": month(p + K_BASE), "to": month(p + k0)} if a and b else None


def analog(real, latest_m, k0, peaks):
    """Latest real price (=100) carried along each past event's own path from the same month offset."""
    paths = {}
    for lab in ANALOGS:
        p = peaks[lab]
        base = real.get(p + k0)
        if not base:
            continue
        pts = [[month(latest_m + j), round(real[p + k0 + j] / base * 100, 1)]
               for j in range(0, K_END - k0 + 1) if (p + k0 + j) in real]
        if len(pts) > 1:
            top = max(pts[1:], key=lambda q: q[1])
            paths[lab] = {"points": pts, "peak_pct": round(top[1] - 100, 1), "peak_month": top[0]}
    if not paths:
        return None
    months = sorted({q[0] for v in paths.values() for q in v["points"]})
    band = []
    for mo in months:
        vals = [q[1] for v in paths.values() for q in v["points"] if q[0] == mo]
        band.append([mo, min(vals), max(vals)])
    hi_lab = max(paths, key=lambda k: paths[k]["peak_pct"])
    return {"base_month": month(latest_m), "index_base": "latest month = 100 (real)", "paths": paths, "band": band,
            "lo": min(v["peak_pct"] for v in paths.values()), "hi": max(v["peak_pct"] for v in paths.values()),
            "peak_month": paths[hi_lab]["peak_month"], "n_paths": len(paths)}


def nino_years(history, current=CURRENT[1]):
    """Calendar years left out of the normal-year fits: every El Niño year in CPC's ONI (a DJF ONI >= 0.5 in year y
    marks an El Niño that started in year y-1) and the year after it (y), plus the current event's year."""
    ys = {int(current[:4])}
    for h in history:
        if h.get("season") == "DJF" and isinstance(h.get("anom"), (int, float)) and h["anom"] >= 0.5:
            ys |= {h["year"] - 1, h["year"]}
    return ys


def dlog(s, m):
    return math.log(s[m] / s[m - 1]) if m in s and (m - 1) in s and s[m] > 0 and s[m - 1] > 0 else None


def _clean_steps(real, bad):
    """Months m whose step m-1 -> m lies wholly in non-El-Niño years."""
    return [m for m in sorted(real) if (m // 12) not in bad and ((m - 1) // 12) not in bad and dlog(real, m) is not None]


def seasonal_normal(real, bad):
    """Median real log change per calendar month over non-El-Niño years, centred so the twelve sum to zero (a seasonal
    swing with no trend: medians of monthly steps do not add up to a typical year, so their sum is bias, not signal).
    Returns ({month 0..11: step}, {month: n years}). All or nothing: if any calendar month has fewer than
    SEASON_MIN_YEARS years the steps are {} (no normal year)."""
    by = {}
    for m in _clean_steps(real, bad):
        by.setdefault(m % 12, []).append(dlog(real, m))
    n = {c: len(by.get(c, [])) for c in range(12)}
    if min(n.values()) < SEASON_MIN_YEARS:
        return {}, n
    med = {c: statistics.median(v) for c, v in by.items()}
    mean = sum(med.values()) / 12
    return {c: v - mean for c, v in med.items()}, n


def normal_path(med, origin, n):
    """Cumulative normal-year log change from month `origin` over j = 0..n (j = 0 is 0)."""
    out = [0.0]
    for j in range(1, n + 1):
        out.append(out[-1] + med.get((origin + j) % 12, 0.0))
    return out


def pass_through(real, world, med, bad):
    """OLS of the series' deseasonalised monthly real log change on the world real log change, non-El-Niño months
    only. beta is kept only with >= BETA_MIN_MONTHS months and |t| >= BETA_T, then clipped to 0..1; else 0."""
    if not world:
        return {"beta": 0.0, "beta_raw": None, "t": None, "n_months": 0, "used": False, "why": "no world benchmark for this staple"}
    pairs = [(dlog(world, m), dlog(real, m) - med.get(m % 12, 0.0)) for m in _clean_steps(real, bad) if dlog(world, m) is not None]
    n = len(pairs)
    if n < BETA_MIN_MONTHS:
        return {"beta": 0.0, "beta_raw": None, "t": None, "n_months": n, "used": False, "why": f"only {n} non-El-Niño months"}
    mx, my = statistics.mean(p[0] for p in pairs), statistics.mean(p[1] for p in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    b = sum((x - mx) * (y - my) for x, y in pairs) / sxx
    s2 = sum((y - my - b * (x - mx)) ** 2 for x, y in pairs) / (n - 2)
    t = b / math.sqrt(s2 / sxx) if s2 > 0 else float("inf")
    used = abs(t) >= BETA_T
    why = ("significant" if used and 0 <= b <= 1 else "significant, clipped to 0..1" if used else "not significant")
    return {"beta": round(min(1.0, max(0.0, b)), 3) if used else 0.0, "beta_raw": round(b, 3),
            "t": round(t, 2) if math.isfinite(t) else None, "n_months": n, "used": used and b > 0, "why": why}


def excess_path(real, world, beta, norm, o, n):
    """El Niño excess of one past event from origin month o over j = 0..n (log): real change minus the normal year
    minus beta x the world real change. Months without data are skipped."""
    base, wb = real.get(o), (world or {}).get(o)
    out = {}
    for j in range(n + 1):
        v = real.get(o + j)
        if not base or v is None:
            continue
        w = 0.0
        if beta:
            wv = (world or {}).get(o + j)
            if wv is None or not wb:
                continue
            w = beta * math.log(wv / wb)
        out[j] = math.log(v / base) - norm[j] - w
    return out


def window_peak(idx_by_j, k0):
    """Peak rise (%) of an index path (j -> index, 100 at j = 0) over the target window December..March+1."""
    win = [idx_by_j[j] for j in range(max(0, -k0), K_END - k0 + 1) if j in idx_by_j]
    return round(max(win) - 100, 1) if len(win) >= MIN_WINDOW else None


def effect_replay(real, world, pt, med, n_years, latest_m, k0, peaks, bench):
    """Forward view, El Niño part only: today's real price x (normal year + each analog event's El Niño excess)."""
    n = K_END - k0
    norm = normal_path(med, latest_m, n)
    has_normal = bool(med)
    normal_pts = [[month(latest_m + j), round(100 * math.exp(norm[j]), 1)] for j in range(n + 1)] if has_normal else None
    paths, adj_y = {}, {}
    for lab in ANALOGS:
        ex = excess_path(real, world, pt["beta"], norm, peaks[lab] + k0, n)
        if len(ex) < 2:
            continue
        idx = {j: 100 * math.exp(norm[j] + e) for j, e in ex.items()}
        pts = [[month(latest_m + j), round(v, 1)] for j, v in sorted(idx.items())]
        top = max(pts[1:], key=lambda q: q[1])
        jt = mi(top[0]) - latest_m
        paths[lab] = {"points": pts, "peak_pct": round(top[1] - 100, 1), "peak_month": top[0],
                      "normal_at_peak_pct": round(100 * math.exp(norm[jt]) - 100, 1) if has_normal else None}
        y = window_peak(idx, k0)
        if y is not None:
            adj_y[lab] = y
    normal_y = window_peak({j: 100 * math.exp(v) for j, v in enumerate(norm)}, k0) if has_normal else None
    out = {"base_month": month(latest_m), "index_base": "latest month = 100 (real)",
           "normal": {"available": has_normal, "points": normal_pts,
                      "end_pct": round(normal_pts[-1][1] - 100, 1) if has_normal else None,
                      "n_years_min": min(n_years.values()), "n_years_max": max(n_years.values()),
                      "why": None if has_normal else f"a calendar month has fewer than {SEASON_MIN_YEARS} non-El-Niño years"},
           "world": {"benchmark": bench, **pt}, "paths": paths}
    if paths:
        months_ = sorted({q[0] for v in paths.values() for q in v["points"]})
        out["band"] = [[mo, min(q[1] for v in paths.values() for q in v["points"] if q[0] == mo),
                        max(q[1] for v in paths.values() for q in v["points"] if q[0] == mo)] for mo in months_]
        hi_lab = max(paths, key=lambda k: paths[k]["peak_pct"])
        out.update({"lo": min(v["peak_pct"] for v in paths.values()), "hi": paths[hi_lab]["peak_pct"],
                    "peak_month": paths[hi_lab]["peak_month"], "n_paths": len(paths)})
    return out, adj_y, normal_y


def evaluate_replay(samples):
    """Leave-one-event-out score of the replays, same target and samples as the model table: each held-out event's
    peak rise vs no change, the plain replay (mean of the series' other analog events' real peak rises), the normal
    year alone, and the El Niño-effect replay (normal year + mean excess of the other analog events)."""
    rows = []
    for s in samples:
        plain = [v for lab, v in s["analog_y"].items() if lab != s["event"]]
        adj = [v for lab, v in (s.get("adj_y") or {}).items() if lab != s["event"]]
        if not plain or not adj or s.get("normal_y") is None:
            continue
        rows.append({**s, "p_nochange": 0.0, "p_plain": statistics.mean(plain), "p_normal": s["normal_y"],
                     "p_adjusted": statistics.mean(adj)})
    kinds = ("adjusted", "plain", "normal", "nochange")

    def score(rs):
        if not rs:
            return None
        out = {"n": len(rs), "n_events": len({r["event"] for r in rs})}
        for k in kinds:
            out[f"mae_{k}"] = round(statistics.mean(abs(r[f"p_{k}"] - r["y"]) for r in rs), 1)
            out[f"hits_{k}"] = sum((r[f"p_{k}"] > 0) == (r["y"] > 0) for r in rs)
        return out

    pooled = score(rows)
    if pooled:
        pooled["by_event"] = {e: {k: v for k, v in score([r for r in rows if r["event"] == e]).items() if k.startswith(("n", "mae"))}
                              for e in sorted({r["event"] for r in rows})}
        pooled["best"] = min(kinds, key=lambda k: pooled[f"mae_{k}"])
    return pooled, {k: score([r for r in rows if r["key"] == k]) for k in {r["key"] for r in rows}}


def ols(X, y):
    """Least squares with intercept via normal equations (tiny p, no numpy dependency)."""
    A = [[1.0] + list(r) for r in X]
    p = len(A[0])
    M = [[sum(a[i] * a[j] for a in A) for j in range(p)] + [sum(a[i] * t for a, t in zip(A, y))] for i in range(p)]
    for c in range(p):
        piv = max(range(c, p), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-9:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(p):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * b for a, b in zip(M[r], M[c])]
    return [M[i][p] / M[i][i] for i in range(p)]


def predict(beta, x):
    return beta[0] + sum(b * v for b, v in zip(beta[1:], x))


def evaluate(samples):
    """Leave-one-event-out skill. samples: dicts {key, event, y, x (list or None), analog_y {label: y}}.
    Returns (pooled skill, per-key skill, LOEO residuals per key, status). Gate: pooled MAE below both baselines
    and the model beats the analog on at least half of the held-out events."""
    usable = [s for s in samples if s["x"] is not None]
    events = sorted({s["event"] for s in usable})
    scored = []
    for e in events:
        train = [s for s in usable if s["event"] != e]
        beta = ols([s["x"] for s in train], [s["y"] for s in train]) if len(train) > len(PREDICTORS) + 1 else None
        pool = [s["y"] for s in train if s["event"] in ANALOGS]
        for s in (s for s in usable if s["event"] == e):
            if beta is None:
                continue
            own = [v for lab, v in s["analog_y"].items() if lab != e]
            a = statistics.mean(own) if own else (statistics.median(pool) if pool else None)
            if a is None:
                continue
            scored.append({**s, "p_model": predict(beta, s["x"]), "p_nochange": 0.0, "p_analog": a})

    def score(rows):
        if not rows:
            return None
        out = {"n": len(rows), "n_events": len({r["event"] for r in rows})}
        for k in ("model", "nochange", "analog"):
            out[f"mae_{k}"] = round(statistics.mean(abs(r[f"p_{k}"] - r["y"]) for r in rows), 1)
            out[f"hits_{k}"] = sum((r[f"p_{k}"] > 0) == (r["y"] > 0) for r in rows)
        return out

    pooled = score(scored)
    per_key = {k: score([r for r in scored if r["key"] == k]) for k in {r["key"] for r in scored}}
    resid = {}
    for r in scored:
        resid.setdefault(r["key"], []).append(r["y"] - r["p_model"])
    by_event = {e: score([r for r in scored if r["event"] == e]) for e in events}
    by_event = {e: v for e, v in by_event.items() if v}
    wins = sum(v["mae_model"] < v["mae_analog"] for v in by_event.values())
    if pooled:
        pooled["events_model_beats_analog"] = f"{wins} of {len(by_event)}"
        pooled["by_event"] = {e: {k: v[k] for k in ("n", "mae_model", "mae_nochange", "mae_analog")} for e, v in by_event.items()}
    if not pooled or pooled["n"] < MIN_SAMPLES or pooled["n_events"] < MIN_EVENTS:
        status = "insufficient_data"
    elif (pooled["mae_model"] < pooled["mae_nochange"] and pooled["mae_model"] < pooled["mae_analog"]
          and 2 * wins >= len(by_event)):
        status = "ok"
    else:
        status = "no_skill"
    return pooled, per_key, resid, status


def quantile(sorted_vals, q):
    i = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def universe(iso_staples_model, regions, effects):
    """{(iso, staple): [reasons]} from the three documented-harm sources."""
    out = {}
    for iso, staple, why in iso_staples_model:
        out.setdefault((iso, staple), []).append(why)
    for r in regions:
        if r.get("effect_direction") == -1 and r.get("confidence") in ("high", "medium"):
            for iso in r.get("iso3") or []:
                for st in REGION_STAPLES.get(r["id"], []):
                    out.setdefault((iso, st), []).append(f"region card: {r.get('label')} ({r['confidence']} confidence)")
    for e in effects:
        hi = e.get("effect_high_pct")
        if hi is not None and hi >= 0:
            continue
        crop = (e.get("crop") or "").lower()
        sts = ["cereals"] if "cereal" in crop else [k for k, rx in STAPLE_RE.items() if k != "cereals" and re.search(rx, crop)]
        for st in sts:
            out.setdefault((e["iso"], st), []).append(f"published effect: {e.get('crop')} ({e.get('confidence')} confidence)")
    return out


def _load(name):
    return json.loads((DATA_DIR / name).read_text())["data"]


def _monthly(s):
    for p in s.get("periodicity") or []:
        if p.get("period") == "monthly" and p.get("end_date"):
            return p.get("start_date") or "", p["end_date"]
    return "", ""


def build(listing, datapoints, enso_hist, model, outlook, regions, effects, today=None, world=None):
    """world: {staple: {month index: world real price}} (see world_real); None leaves pass-through at 0."""
    today_m = mi((today or date.today()).isoformat())
    events = el_nino_events(enso_hist)
    bad = nino_years(enso_hist)
    peaks = {e["label"]: mi(e["peak"]) for e in events}
    missing = [a for a in ANALOGS if a not in peaks]
    if missing:
        raise RuntimeError(f"analog events missing from the ONI history: {missing}")
    p_now = mi(CURRENT[1])
    oni_now = outlook["cases"]["observed"]["oni"]
    shown = [(r["iso"], CROP_STAPLE.get(r["crop"], r["crop"]),
              f"FoodShield harvest model: {r['crop']} El Niño slope {r['slope_pct_per_oni']}% per °C ONI, q={r.get('q_nino')}")
             for r in outlook.get("rows_all") or [] if r.get("status") == "shown" and (r.get("slope_pct_per_oni") or 0) < 0]
    uni = universe(shown, regions, effects)

    rows, samples, excluded, tried = [], [], [], set()
    for iso, market, commodity, ptype, staple in SERIES:
        tried.add((iso, staple))
        reasons = uni.get((iso, staple))
        hits = [s for s in listing if (s.get("iso3_country_code") or "").upper() == iso and s.get("market_name") == market
                and s.get("commodity_name") == commodity and (s.get("price_type") or "").upper() == ptype]
        if not reasons:
            excluded.append({"iso3": iso, "commodity": commodity, "reason": "no documented El Niño harvest harm in the site's data"})
            continue
        if not hits:
            excluded.append({"iso3": iso, "commodity": commodity, "reason": "series no longer listed by FPMA"})
            continue
        s = max(hits, key=lambda h: _monthly(h)[1])
        dps = datapoints.get(s["uuid"]) or []
        real = {mi(d["date"]): d["price_value_real"] for d in dps
                if d.get("date") and isinstance(d.get("price_value_real"), (int, float)) and d["price_value_real"] > 0}
        nom = {mi(d["date"]): d["price_value"] for d in dps if d.get("date") and isinstance(d.get("price_value"), (int, float))}
        trimmed = sorted(real)[-TAIL_TRIM[iso]:] if TAIL_TRIM.get(iso) else []
        for m in trimmed:
            real.pop(m)
            nom.pop(m, None)
        if not real or today_m - max(real) > MAX_AGE_MONTHS:
            excluded.append({"iso3": iso, "commodity": commodity, "reason": "no current CPI-deflated monthly series"})
            continue
        L = max(real)
        k0 = L - p_now
        after = {lab: aftermath(real, peaks[lab], k0) for lab in ANALOGS}
        if not any(after.values()) or k0 < K_BASE:
            excluded.append({"iso3": iso, "commodity": commodity, "reason": "series does not cover the 2015-16 or 2023-24 El Niño"})
            continue
        key = f"{iso}:{commodity}"
        fit = (model.get(iso) or {}).get(MODEL_CROP.get(staple, ""), {})
        slope = fit.get("yield_pct_per_oni_nino") if isinstance(fit, dict) else None
        for e in events:
            p = peaks[e["label"]]
            y = peak_rise(real, p, k0)
            if y is None:
                continue
            rel = rel_3y(real, p + k0)
            x = [round(slope * e["oni"], 2), rel] if slope is not None and rel is not None else None
            samples.append({"key": key, "event": e["label"], "y": y, "x": x, "analog_y": {}})
        ay = {smp["event"]: smp["y"] for smp in samples if smp["key"] == key and smp["event"] in ANALOGS}
        wr = (world or {}).get(staple)
        med, n_years = seasonal_normal(real, bad)
        pt = pass_through(real, wr, med, bad)
        eff, adj_y, normal_y = effect_replay(real, wr, pt, med, n_years, L, k0, peaks,
                                             WORLD[staple][1] if staple in WORLD else None)
        for smp in samples:
            if smp["key"] == key:
                smp.update(analog_y=ay, adj_y=adj_y, normal_y=normal_y)
        rel_now = rel_3y(real, L)
        rows.append({
            "iso3": iso, "country": s.get("country_name"), "commodity": commodity, "staple": staple,
            "universe_basis": reasons,
            "series": {"market": s.get("market_name"), "price_type": ptype.lower(), "currency": s.get("currency"),
                       "unit": s.get("measure_unit_label"), "real": True, "deflator": "FPMA CPI-deflated price",
                       "series_source": s.get("source_name"), "source_url": TOOL_URL, "fpma_uuid": s["uuid"],
                       "first_month": month(min(real)), "trimmed_tail": [month(m) for m in trimmed],
                       "gap": _gap(real)},
            "latest": {"month": month(L), "value": nom.get(L), "real_value": real[L], "months_from_peak": k0},
            "aftermath": {"now": aftermath(real, p_now, k0), **after},
            "analog": analog(real, L, k0, peaks),
            "effect_replay": eff,
            "_x_now": [round(slope * oni_now, 2), rel_now] if slope is not None and rel_now is not None else None,
            "price_vs_3y_median_pct": rel_now,
        })

    for (iso, st), why in sorted(uni.items()):
        if (iso, st) in tried:
            continue
        rx = re.compile(STAPLE_RE.get(st, st), re.I)
        have = [s for s in listing if (s.get("iso3_country_code") or "").upper() == iso and rx.search(s.get("commodity_name") or "")]
        starts = sorted(_monthly(s)[0][:7] for s in have if _monthly(s)[1] >= month(today_m - MAX_AGE_MONTHS))
        reason = ("FPMA has no series for this staple" if not have else
                  "no current FPMA series" if not starts else
                  f"FPMA series start {starts[0]}: no El Niño on record to compare" if starts[0] > "2023-03" else
                  "not in the series table: review SERIES")
        excluded.append({"iso3": iso, "staple": st, "basis": why, "reason": reason})

    pooled, per_key, resid, status = evaluate(samples)
    replay_pooled, replay_key = evaluate_replay(samples)
    usable = [s for s in samples if s["x"] is not None]
    beta = ols([s["x"] for s in usable], [s["y"] for s in usable]) if status == "ok" else None
    for r in rows:
        key = f"{r['iso3']}:{r['commodity']}"
        rk = replay_key.get(key)
        r["effect_replay"]["skill"] = ({k: rk[k] for k in rk if k.startswith(("mae_", "hits_", "n"))} if rk else None)
        x = r.pop("_x_now")
        sk = per_key.get(key)
        r["skill"] = ({"mae_model": sk["mae_model"], "mae_nochange": sk["mae_nochange"], "mae_analog": sk["mae_analog"],
                       "hits": {"model": sk["hits_model"], "nochange": sk["hits_nochange"], "analog": sk["hits_analog"]},
                       "n_events": sk["n_events"]} if sk else None)
        row_ok = bool(sk and sk["n_events"] >= ROW_MIN_EVENTS and sk["mae_model"] < sk["mae_nochange"]
                      and sk["mae_model"] < sk["mae_analog"])
        if r["skill"]:
            r["skill"]["row_gate"] = "pass" if row_ok else "fail"
        if beta and x is not None and row_ok:
            p50, res = predict(beta, x), sorted(resid[key])
            r["model"] = {"p50": round(p50, 1), "p10": round(p50 + quantile(res, 0.1), 1),
                          "p90": round(p50 + quantile(res, 0.9), 1), "status": "ok",
                          "what": "peak real-price rise from the latest month, December..March+1, %"}
        else:
            r["model"] = None
    return {
        "rows": rows,
        "model_status": status,
        "skill": pooled,
        "replay_skill": replay_pooled,
        "nino_years_excluded": sorted(y for y in bad if y >= 1990),
        "coefficients": dict(zip(["intercept"] + PREDICTORS, [round(b, 3) for b in beta])) if beta else None,
        "events": [{**e, "used": any(s["event"] == e["label"] for s in samples)} for e in events],
        "current": {"label": CURRENT[0], "peak_month": CURRENT[1], "oni": oni_now,
                    "oni_label": outlook["cases"]["observed"].get("label")},
        "excluded": excluded,
        "universe_rule": UNIVERSE_RULE,
        "method": {
            "real": "FPMA CPI-deflated price (price_value_real); nominal latest value kept for display.",
            "aftermath": "real change from March of the El Niño year to the latest month's offset from the December peak.",
            "analog": "latest real price carried along each past event's path from the same offset, to March after the peak; a replay, not a forecast.",
            "target": "peak real price over December..March+1 (months 0..15 from the peak) vs the origin month; at least 12 of 16 months observed.",
            "predictors": "shortfall_pct = the country's own fitted El Niño yield slope (enso_model.json, point estimate, significant or not) x the event's DJF ONI; rel_3y_pct = origin price vs the median of the 36 months before it. Import dependence (USDA PSD) left out: the file has no per-year history, so it cannot be known in advance for past events.",
            "n_specs_tried": 1,
            "validation": "leave-one-event-out; baselines: no change (0%) and the analog average (mean of the same series' 2015-16 / 2023-24 peak rises, excluding the held-out event; pooled median if the series has none). Gate: pooled MAE below both baselines, the model beats the analog on at least half of the held-out events, at least 3 events and 10 samples. The half-of-events condition was added after the first run (pooled MAE edge over the analog of about 2 points); it makes the gate stricter, not looser. Row gate (added after the first run showed the pooled fit losing badly to no change on the managed rice markets): a row shows model numbers only if it has at least 5 held-out events of its own and its own MAE beats both baselines; its p10/p90 are its own leave-one-event-out misses around p50.",
            "hits": "direction = rise (>0) or not; no change always calls 'not'.",
            "effect_replay": ("The El Niño-only forward view. Taken out: general inflation (FPMA CPI-deflated prices); the "
                              "normal seasonal swing (per series, the median real log change of each calendar month in "
                              "non-El-Niño years, centred so a year sums to zero (no trend), cumulated from the latest month; El Niño years = every year with a DJF "
                              "ONI >= 0.5 in December of it, the year after, and the current year; n years per month in "
                              "normal.n_years_min/max; a series with any calendar month under 3 years gets no normal year, normal.available = "
                              "false); world-market moves (per series, beta x the World Bank Pink Sheet "
                              "price of the staple deflated by US CPI, beta from an OLS of the deseasonalised monthly "
                              "real log change on the world real log change over non-El-Niño months, kept only with 48+ "
                              "months and |t| >= 1.96, clipped to 0..1; beans and teff have no world benchmark, beta 0). "
                              "Excess path of a past event = its real path minus the normal year minus beta x the world "
                              "change over the same months. Forward view = latest real price x (normal year + that "
                              "excess), i.e. a normal year plus the El Niño effect last time; normal.points is the "
                              "normal year alone. NOT taken out: currency moves beyond what CPI captures, local policy "
                              "(export bans, strategic reserve sales, price controls), conflict and other local shocks "
                              "in the past event; any El Niño part of world-price moves is removed with them. The "
                              "normal year is built from calendar-month steps because full 19-month windows free of "
                              "El Niño years leave only 2-4 years per series. Because the normal year is calendar-based, "
                              "it cancels in the total: normal year + excess = the past real path minus beta x the world "
                              "change; the split says how much of that is the usual season and how much is the El Niño "
                              "effect. A replay, not a forecast. Scored "
                              "leave-one-event-out in replay_skill (same target and events as the model table)."),
            "caveat": "Series in one region move together and each El Niño is one draw, so the effective sample is closer to the number of events than the number of rows.",
        },
        "tool_url": TOOL_URL,
    }


def fetch_prices(uuids):
    out, url = {}, f"{API}/FpmaSeriePrice/"
    params = {"uuid__in": ",".join(uuids), "periodicity": "monthly", "format": "json"}
    while url:
        body = http_get(url, params=params, timeout=120).json()
        for r in (body.get("results") if isinstance(body, dict) else body) or []:
            out[r.get("uuid")] = r.get("datapoints") or []
        url, params = (body.get("next") if isinstance(body, dict) else None), None
    return out


def world_real():
    """{staple: {month index: Pink Sheet nominal USD price / US CPI}} for the staples in WORLD."""
    import refresh_worldbank_pink_sheet as pink
    rows = pink.load_workbook_rows(pink.resolve_workbook_url())
    labels, _units, _codes, data_rows, _i = pink._find_header_block(rows)
    col = {str(c).strip(): i for i, c in enumerate(labels) if c}
    cpi = {}
    for line in http_get(FRED_CPI, params={"id": "CPIAUCSL"}, headers={"Accept": "text/csv"}, timeout=60).text.splitlines()[1:]:
        d, _, v = line.partition(",")
        try:
            cpi[mi(d)] = float(v)
        except ValueError:
            continue
    out = {}
    for staple, (label, _desc) in WORLD.items():
        i = col.get(label)
        if i is None:
            raise RuntimeError(f"Pink Sheet column {label!r} not found")
        ser = {}
        for r in data_rows:
            mo, v = pink.parse_month(r[0] if r else None), pink.normalize_num(r[i] if i < len(r) else None)
            if mo and v and mi(mo) in cpi:
                ser[mi(mo)] = v / cpi[mi(mo)]
        out[staple] = ser
    return out


def main():
    listing = http_get(f"{API}/FpmaSerieDomestic/", params={"format": "json"}, timeout=120, patient=True).json().get("results") or []
    want = {(i, m, c, t) for i, m, c, t, _ in SERIES}
    uuids = [s["uuid"] for s in listing if ((s.get("iso3_country_code") or "").upper(), s.get("market_name"),
                                            s.get("commodity_name"), (s.get("price_type") or "").upper()) in want]
    if not uuids:
        raise RuntimeError("FPMA series list matched none of the outlook series")
    out = build(listing, fetch_prices(uuids), _load("enso.json")["history"], _load("enso_model.json"),
                _load("enso_outlook.json"), _load("enso_regions.json")["regions"], _load("enso_published_effects.json"),
                world=world_real())
    sk = out["skill"] or {}
    print(f"[price outlook] {len(out['rows'])} rows; model {out['model_status']}; "
          f"MAE model {sk.get('mae_model')} / no change {sk.get('mae_nochange')} / analog {sk.get('mae_analog')} "
          f"on {sk.get('n')} samples, {sk.get('n_events')} events")
    rs = out["replay_skill"] or {}
    print(f"[price outlook] replay MAE El Niño-effect {rs.get('mae_adjusted')} / plain {rs.get('mae_plain')} / "
          f"normal year {rs.get('mae_normal')} / no change {rs.get('mae_nochange')} on {rs.get('n')} samples")
    # _meta.status is feed health (the page flags anything but "ok"); the model's own verdict
    # (ok / no_skill / insufficient_data) lives in data.model_status, which the page and validate_data read.
    path = write_json("enso_price_outlook.json", out, source=SOURCE, status="ok",
               notes=("Domestic staple prices (FPMA, CPI-deflated) in countries where El Niño's harvest damage is "
                      "documented, against 2015-16 and 2023-24 at the same stage. The model is published only when "
                      "it passes its leave-one-event-out skill gate against no change and the analog average; see data.method."))
    env = json.loads(path.read_text())
    env["_meta"].update({"method": out["method"], "universe_rule": UNIVERSE_RULE, "sources": [
        {"name": "FAO GIEWS FPMA Tool", "url": TOOL_URL, "api": API},
        {"name": "FoodShield El Niño harvest model", "file": "data/enso_model.json, data/enso_outlook.json"},
        {"name": "World Bank Commodity Markets (Pink Sheet), monthly", "url": "https://www.worldbank.org/en/research/commodity-markets"},
        {"name": "US CPI, all urban consumers (FRED CPIAUCSL)", "url": "https://fred.stlouisfed.org/series/CPIAUCSL"},
        {"name": "El Niño region cards and published effects", "file": "data/enso_regions.json, data/enso_published_effects.json"},
        {"name": "NOAA CPC ONI", "url": "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt", "file": "data/enso.json"}]})
    path.write_text(json.dumps(env, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
