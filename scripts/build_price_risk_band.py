"""
Price-risk band for the El Niño Prices lens.

Court verdict 2026-09-30 (point 1, frozen spec). A forward view of real staple prices that is NOT an El Niño
forecast: what seasonality and the pull back toward the 3-year level say about the next 3, 6 and 12 months.

Model (SPEC below, hashed into every forecast-log entry):
  target      log real price change over h months, y = ln R[t+h] - ln R[t]; R = FAO GIEWS FPMA CPI-deflated price
  predictors  x1 = 12-month log real change, x2 = log gap to the median of the 36 months before t (>= 24 of them)
  fit         ONE pooled linear quantile regression per (h, tau), tau 0.1 / 0.5 / 0.9, series intercepts, on every
              series with >= 24 training rows; expanding window (rows whose target was known at the origin only)
  horizons    3, 6, 12 months (12 is the one the page shows)

Test (rolling origin, first origin 2012-01, refit every REFIT_EVERY months, nothing after the origin is used):
  baselines   unconditional: the series' own expanding-window h-month change quantiles;
              calendar-month: the same, restricted to origins in the same calendar month (>= 5 of them)
  pass rule   decided ONLY on the earlier folds (origins up to SELECT_END): pinball loss at tau 0.1 and 0.9 below
              BOTH baselines and 70-90% coverage of the 80% band. The metrics the page shows come from the later
              folds (DISPLAY_FROM on), which played no part in the decision.
  windows     coverage inside the 2015-16 and 2023-24 El Niño windows (origins March of the El Niño year to March
              after the December peak); a series under 70% there has its band hidden.
  ONI         the same model with the real-time ONI added is scored against the one without it; the verdict says
              whether it adds anything.

Also here:
  decomposition  for ZAF, HND, ZMB (import-parity maize): the real move since March split into world price,
                 exchange rate (net of local inflation) and local, by FPMA's own USD price and the Pink Sheet.
  forecast log   data/enso_price_forecast_log.json, append-only: one entry per run date, with the spec hash.
  data fixes     the last 3 months of MDG and ETH (extrapolated deflator) are dropped; gaps of 12+ months are flagged.

Output: data/enso_price_risk.json, data/enso_price_forecast_log.json. Needs numpy (imported lazily so run_all still
imports; without it the step fails and the last good file stays).
"""
import hashlib
import json
import math
import statistics
from datetime import date

from _common import DATA_DIR, http_get, write_json
import build_enso_price_outlook as bo

try:
    import numpy as np
except ImportError:  # run_all imports this module; main() refuses to run without numpy
    np = None

TAUS = (0.1, 0.5, 0.9)
TAUS_I = ((0.1, 0), (0.5, 1), (0.9, 2))     # (tau, index into a sorted quantile triple)
HORIZONS = (3, 6, 12)
DEFAULT_H = 12
MIN_TRAIN = 24            # training rows a series needs before it gets an intercept and is scored
MIN_MONTHS = 24           # observed months a series needs to enter at all
MIN_CAL = 5               # origins in the same calendar month for the calendar-month baseline
FIRST_ORIGIN = "2012-01"
SELECT_END = "2018-12"    # origins up to here decide the pass; later origins are what the page displays
DISPLAY_FROM = "2019-01"
MIN_SELECT_POINTS = 36
COVER_LO, COVER_HI = 0.70, 0.90
WINDOWS = {"2015-16": ("2015-03", "2016-03"), "2023-24": ("2023-03", "2024-03")}
WINDOW_MIN_POINTS = 8
REFIT_EVERY = 3           # months between refits inside the rolling test
SPEC = {
    "model": "pooled linear quantile regression, series intercepts, expanding window",
    "target": "ln real price change over h months",
    "taus": list(TAUS), "horizons": list(HORIZONS),
    "predictors": ["ln R[t] - ln R[t-12]", "ln R[t] - ln median(R[t-36..t-1]), >= 24 of 36 months"],
    "min_training_rows_per_series": MIN_TRAIN, "min_months_per_series": MIN_MONTHS,
    "first_origin": FIRST_ORIGIN, "select_end": SELECT_END, "display_from": DISPLAY_FROM, "refit_every_months": REFIT_EVERY,
    "pass_rule": "pinball at tau 0.1 and 0.9 below the unconditional and the calendar-month baselines on the selection "
                 "folds; 80% band coverage 70-90% on the selection folds; coverage >= 70% inside the 2015-16 and "
                 "2023-24 windows",
    "windows": {k: list(v) for k, v in WINDOWS.items()},
}
SPEC_HASH = hashlib.sha256(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()[:16]
DECOMP = {"ZAF": "maize", "HND": "maize", "ZMB": "maize"}   # import-parity maize series (cointegrated with the world price); HND beans has no world benchmark
SINCE = "2026-03"
LOG_FILE = "enso_price_forecast_log.json"
OUT_FILE = "enso_price_risk.json"


# ── quantile regression ────────────────────────────────────────────────────────────────────────────────────────

def pinball(y, q, tau):
    d = y - q
    return np.where(d >= 0, tau * d, (tau - 1) * d)


def qreg(X, y, tau, beta0=None, max_iter=200, tol=1e-9):
    """Linear quantile regression by iteratively reweighted least squares on the check loss (weights
    tau or 1-tau over max(|residual|, eps)). No numpy-only exact solver exists, so this is approximate; the test
    suite checks its objective against scipy's linprog optimum. X includes any intercept columns."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    beta = np.linalg.lstsq(X, y, rcond=None)[0] if beta0 is None else np.asarray(beta0, float)
    eps = 1e-6 * max(float(np.std(y)), 1e-9)
    last = float(pinball(y, X @ beta, tau).sum())
    for _ in range(max_iter):
        r = y - X @ beta
        w = np.where(r >= 0, tau, 1 - tau) / np.maximum(np.abs(r), eps)
        sw = np.sqrt(w)
        new = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)[0]
        obj = float(pinball(y, X @ new, tau).sum())
        if obj > last + 1e-12:      # IRLS is not monotone at kinks: keep the better point and stop
            break
        done = last - obj <= tol * max(1.0, abs(last))
        beta, last = new, obj
        if done:
            break
    return beta


# ── series prep ────────────────────────────────────────────────────────────────────────────────────────────────

def features(real):
    """{month: (x1, x2)} for every month with a 12-month-ago price and >= 24 of the 36 months before it."""
    lr = {m: math.log(v) for m, v in real.items() if v > 0}
    out = {}
    for m in lr:
        if (m - 12) not in lr:
            continue
        win = [real[j] for j in range(m - 36, m) if j in real and real[j] > 0]
        if len(win) >= 24:
            out[m] = (lr[m] - lr[m - 12], lr[m] - math.log(statistics.median(win)))
    return out, lr


def prep(real):
    feats, lr = features(real)
    ys = {h: {m: lr[m + h] - lr[m] for m in feats if (m + h) in lr} for h in HORIZONS}
    return {"real": real, "lr": lr, "feats": feats, "y": ys, "last": max(lr) if lr else None}


def train_set(P, h, t, extra=None):
    """Design rows for every series with >= MIN_TRAIN rows whose target was known at origin t (m + h <= t)."""
    keys, rows, ys, ids = [], [], [], []
    for k, s in P.items():
        ms = [m for m in s["y"][h] if m + h <= t]
        if len(ms) < MIN_TRAIN:
            continue
        keys.append(k)
        for m in ms:
            x = list(s["feats"][m])
            if extra is not None:
                v = extra.get(m - 1)
                if v is None:
                    v = 0.0
                x.append(v)
            rows.append(x)
            ys.append(s["y"][h][m])
            ids.append(len(keys) - 1)
    return keys, np.array(rows), np.array(ys), np.array(ids)


def fit_pooled(P, h, t, betas=None, extra=None):
    """{tau: beta} plus the series order; beta = [x coefficients..., series intercepts...]."""
    keys, Xr, y, ids = train_set(P, h, t, extra)
    if not keys:
        return None
    D = np.zeros((len(y), len(keys)))
    D[np.arange(len(y)), ids] = 1.0
    X = np.hstack([Xr, D])
    out = {}
    for tau in TAUS:
        b0 = None if betas is None or betas.get("keys") != keys else betas[tau]
        out[tau] = qreg(X, y, tau, b0)
    out["keys"] = keys
    out["npred"] = Xr.shape[1]
    return out


def predict_q(fit, key, x):
    """Sorted (crossing-free) quantiles at features x for series `key`, log scale."""
    if fit is None or key not in fit["keys"]:
        return None
    j = fit["keys"].index(key)
    q = [float(np.dot(fit[tau][:fit["npred"]], x) + fit[tau][fit["npred"] + j]) for tau in TAUS]
    return sorted(q)


def baseline_q(s, h, t, taus=TAUS):
    """Expanding-window baselines for series s at origin t: (unconditional, calendar-month), each None or 3 quantiles."""
    ys = [(m, y) for m, y in s["y"][h].items() if m + h <= t]
    allv = [y for _, y in ys]
    cal = [y for m, y in ys if m % 12 == t % 12]
    u = list(np.quantile(allv, taus)) if len(allv) >= MIN_TRAIN else None
    c = list(np.quantile(cal, taus)) if len(cal) >= MIN_CAL else None
    return u, c


def mi(iso):
    return bo.mi(iso)


def mo(m):
    return bo.month(m)


# ── rolling-origin test ────────────────────────────────────────────────────────────────────────────────────────

def rolling(P, oni=None, origins=None):
    """One record per (series, origin, h) with a realised target: model quantiles, both baselines, realised y.
    With `oni` (month index -> anomaly) the model gets the ONI at t-1 as a third predictor."""
    last = max(s["last"] for s in P.values())
    T0 = mi(FIRST_ORIGIN)
    recs = []
    for h in HORIZONS:
        fit, betas = None, None
        for t in range(T0, last - h + 1):
            if fit is None or (t - T0) % REFIT_EVERY == 0:
                fit = fit_pooled(P, h, t, betas, oni)
                betas = fit
            for k, s in P.items():
                if t not in s["feats"] or (t + h) not in s["lr"]:
                    continue
                x = list(s["feats"][t])
                if oni is not None:
                    if (t - 1) not in oni:
                        continue
                    x.append(oni[t - 1])
                q = predict_q(fit, k, x)
                if q is None:
                    continue
                u, c = baseline_q(s, h, t)
                recs.append({"k": k, "t": t, "h": h, "y": s["y"][h][t], "q": q, "u": u, "c": c})
    return recs


def _arr(recs, field, i=None):
    return np.array([r[field] if i is None else r[field][i] for r in recs], float)


def score(recs):
    """Pinball at tau 0.1 / 0.9 for model and both baselines and 80% coverage, over the records that have both
    baselines (so the three are scored on the same points)."""
    rs = [r for r in recs if r["u"] is not None and r["c"] is not None]
    if not rs:
        return None
    y = _arr(rs, "y")
    out = {"n": len(rs)}
    for lab, fld in (("model", "q"), ("uncond", "u"), ("calmonth", "c")):
        for tau, i in ((0.1, 0), (0.9, 2)):
            out[f"{lab}_p{int(tau * 100)}"] = float(pinball(y, _arr(rs, fld, i), tau).mean())
    lo, hi = _arr(rs, "q", 0), _arr(rs, "q", 2)
    out["coverage"] = float(((y >= lo) & (y <= hi)).mean())
    return out


def window_cover(recs):
    """Coverage of the 80% band inside each El Niño window (model records only)."""
    out = {}
    for lab, (a, b) in WINDOWS.items():
        rs = [r for r in recs if mi(a) <= r["t"] <= mi(b)]
        if rs:
            y, lo, hi = _arr(rs, "y"), _arr(rs, "q", 0), _arr(rs, "q", 2)
            out[lab] = {"n": len(rs), "coverage": float(((y >= lo) & (y <= hi)).mean())}
        else:
            out[lab] = {"n": 0, "coverage": None}
    return out


def gate(sel, win):
    """Pass/fail with the reason. sel = score() on the selection folds, win = window_cover()."""
    if not sel or sel["n"] < MIN_SELECT_POINTS:
        return False, f"too little history to test ({sel['n'] if sel else 0} early-fold points, need {MIN_SELECT_POINTS})"
    for p in ("p10", "p90"):
        for base, name in (("uncond", "unconditional"), ("calmonth", "calendar-month")):
            if not sel[f"model_{p}"] < sel[f"{base}_{p}"]:
                return False, f"did not beat the {name} baseline at tau 0.{p[1:2]} on the early folds"
    if not COVER_LO <= sel["coverage"] <= COVER_HI:
        return False, f"80% band covered {sel['coverage'] * 100:.0f}% on the early folds (need 70-90%)"
    tot = sum(w["n"] for w in win.values())
    if tot >= WINDOW_MIN_POINTS:
        hit = sum(w["n"] * w["coverage"] for w in win.values() if w["n"])
        if hit / tot < COVER_LO:
            return False, f"under-covers inside the El Niño windows ({hit / tot * 100:.0f}% of {tot} months)"
    return True, "passes"


# ── ONI test ───────────────────────────────────────────────────────────────────────────────────────────────────

def nw_t(d, lag):
    """t-statistic of the mean of d with a Newey-West variance, `lag` lags."""
    d = np.asarray(d, float)
    n = len(d)
    if n < 10:
        return None
    e = d - d.mean()
    v = float(e @ e) / n
    for L in range(1, lag + 1):
        v += 2 * (1 - L / (lag + 1)) * float(e[L:] @ e[:-L]) / n
    return float(d.mean() / math.sqrt(v / n)) if v > 0 else None


def erf_p(t):
    return math.erfc(abs(t) / math.sqrt(2)) if t is not None else None


def oni_test(base, withoni, oni):
    """Does the real-time ONI improve the band? Pooled pinball (all series, all origins 2012 on) at each h and tau,
    the same points for both; NW t on the per-origin mean difference; and the same at El Niño-onset origins
    (ONI >= 0.5 and rising over 3 months at the origin)."""
    idx = {(r["k"], r["t"], r["h"]): r for r in withoni}
    rows = []
    for h in HORIZONS:
        for tau, i in TAUS_I:
            pairs = [(r, idx[(r["k"], r["t"], r["h"])]) for r in base if r["h"] == h and (r["k"], r["t"], h) in idx]
            if not pairs:
                continue
            y = np.array([a["y"] for a, _ in pairs])
            la = pinball(y, np.array([a["q"][i] for a, _ in pairs]), tau)
            lb = pinball(y, np.array([b["q"][i] for _, b in pairs]), tau)
            ts = np.array([a["t"] for a, _ in pairs])
            u = sorted(set(ts))
            d = [float((la - lb)[ts == v].mean()) for v in u]
            on = np.array([(oni.get(v - 1, 0) >= 0.5 and oni.get(v - 1, 0) > oni.get(v - 4, 0)) for v in ts])
            row = {"h": h, "tau": tau, "n": len(pairs), "pinball_without": round(float(la.mean()), 5),
                   "pinball_with": round(float(lb.mean()), 5),
                   "gain_pct": round(float((1 - lb.mean() / la.mean()) * 100), 1),
                   "t": round(nw_t(d, 12), 2) if nw_t(d, 12) is not None else None,
                   "p": round(erf_p(nw_t(d, 12)), 3) if nw_t(d, 12) is not None else None}
            if on.sum() >= 20:
                row["onset_n"] = int(on.sum())
                row["onset_gain_pct"] = round(float((1 - lb[on].mean() / la[on].mean()) * 100), 1)
            rows.append(row)
    head = next((r for r in rows if r["h"] == DEFAULT_H and r["tau"] == 0.9), None)
    sig = [r for r in rows if r["gain_pct"] > 0 and r["p"] is not None and r["p"] < 0.05]
    adds = bool(sig) or bool(head and head["gain_pct"] >= 2)
    return {"rows": rows, "adds_value": adds,
            "verdict": "improves" if adds else "adds_nothing",
            "what": ("The same pooled model with the ONI of the month before the origin as a third predictor, scored on the "
                     "same points. gain_pct = pinball loss saved; t/p = Newey-West (12 lags) on the per-origin mean "
                     "difference; onset = origins with ONI >= 0.5 and rising over three months.")}



def oni_monthly(hist_rows):
    """{month index: ONI anomaly} centred on the middle month of each 3-month season (CPC oni.ascii.txt rows)."""
    c = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6, "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}
    return {r["year"] * 12 + c[r["season"]] - 1: r["anom"] for r in hist_rows if r["season"] in c}


# ── decomposition ──────────────────────────────────────────────────────────────────────────────────────────────

def shapley_pct(parts):
    """Split a total log change into percentage-point contributions that add up to exp(total)-1, averaging over the
    six orders in which the three parts can be applied."""
    import itertools
    names = list(parts)
    tot = {n: 0.0 for n in names}
    perms = list(itertools.permutations(names))
    for perm in perms:
        acc = 0.0
        for n in perm:
            nxt = acc + parts[n]
            tot[n] += (math.exp(nxt) - math.exp(acc)) * 100
            acc = nxt
    return {n: v / len(perms) for n, v in tot.items()}


def decompose(datapoints, world_usd, since=SINCE):
    """Real move since `since` = world price + exchange rate (net of local inflation) + local.
      ln R = ln P - ln C          P local nominal price, C implied deflator (P / real price)
      ln P = ln D + ln E          D FPMA's USD price, E local currency per USD (P / D)
      ln D = ln W + ln B          W Pink Sheet USD price, B the local basis over the world price
    Import parity means B is stable, so B carries whatever is local: transport, margins, tariffs, policy, harvest.
    World = d ln W; exchange rate = d ln E - d ln C; local = d ln B. The three sum exactly to d ln R."""
    rows = {}
    for d in datapoints:
        if not d.get("date"):
            continue
        p, r, u = d.get("price_value"), d.get("price_value_real"), d.get("price_value_dollar")
        if all(isinstance(v, (int, float)) and v > 0 for v in (p, r, u)):
            rows[mi(d["date"])] = (p, r, u)
    a = mi(since)
    end = min(max(rows) if rows else -1, max(world_usd) if world_usd else -1)
    if a not in rows or end <= a or end not in rows or a not in world_usd or end not in world_usd:
        return None
    (p0, r0, u0), (p1, r1, u1) = rows[a], rows[end]
    e0, e1, c0, c1 = p0 / u0, p1 / u1, p0 / r0, p1 / r1
    w = math.log(world_usd[end] / world_usd[a])
    fx = math.log(e1 / e0) - math.log(c1 / c0)
    local = math.log(u1 / u0) - w
    total = math.log(r1 / r0)
    pp = shapley_pct({"world": w, "fx": fx, "local": local})
    return {"from": since, "to": mo(end), "total_pct": round((math.exp(total) - 1) * 100, 1),
            "world_pct": round(pp["world"], 1), "fx_pct": round(pp["fx"], 1), "local_pct": round(pp["local"], 1),
            "world_log": round(w, 4), "fx_log": round(fx, 4), "local_log": round(local, 4),
            "inflation_log": round(math.log(c1 / c0), 4), "currency_log": round(math.log(e1 / e0), 4)}


# ── build ──────────────────────────────────────────────────────────────────────────────────────────────────────

def seasonal_index(real, base_month=SINCE):
    """Median real price of each calendar month over the latest three years of the series (the 3-year seasonal
    median), on the maize plate's scale: the real price of `base_month` = 100. {calendar month 1..12: index}."""
    L, b = max(real), real.get(mi(base_month))
    if not b:
        return None
    out = {}
    for c in range(12):
        vs = [real[m] for m in range(L - 35, L + 1) if m % 12 == c and m in real]
        if len(vs) >= 2:
            out[c + 1] = round(statistics.median(vs) / b * 100, 1)
    return out if len(out) == 12 else None


def pct_from_log(q):
    return [round((math.exp(v) - 1) * 100, 1) for v in q]


def build(series, world_usd, oni, today=None):
    """series: [{"key", "iso3", "country", "commodity", "staple", "market", "price_type", "datapoints"}] with FPMA
    datapoints already fetched; world_usd {staple: {month index: USD/t}}; oni {month index: anomaly}."""
    today = today or date.today()
    P, meta, excluded = {}, {}, []
    for s in series:
        dps = s["datapoints"]
        real = {mi(d["date"]): d["price_value_real"] for d in dps
                if d.get("date") and isinstance(d.get("price_value_real"), (int, float)) and d["price_value_real"] > 0}
        cut = bo.TAIL_TRIM.get(s["iso3"], 0)
        dropped = sorted(real)[-cut:] if cut else []
        for m in dropped:
            real.pop(m)
        if len(real) < MIN_MONTHS:
            excluded.append({"key": s["key"], "reason": f"only {len(real)} months of real prices"})
            continue
        P[s["key"]] = prep(real)
        gap = bo._gap(real)
        meta[s["key"]] = {**{k: s[k] for k in ("iso3", "country", "commodity", "staple", "market", "price_type")},
                          "first_month": mo(min(real)), "latest_month": mo(max(real)), "n_months": len(real),
                          "dropped_tail": [mo(m) for m in dropped],
                          "gap": gap}

    if not P:
        return {"run_date": today.isoformat(), "spec": SPEC, "spec_hash": SPEC_HASH, "default_horizon": DEFAULT_H,
                "horizons": list(HORIZONS), "series": [], "headline": {}, "oni_test": None, "excluded": excluded,
                "last_origin": None}
    recs = rolling(P)
    sel_end, disp = mi(SELECT_END), mi(DISPLAY_FROM)
    final = {h: fit_pooled(P, h, max(x["last"] for x in P.values()) + h) for h in HORIZONS}
    out_series = []
    for k, s in P.items():
        m = meta[k]
        L = s["last"]
        entry = {"key": k, **m, "latest_real": round(s["real"][L], 4),
                 "now": ({"chg12_pct": round((math.exp(s["feats"][L][0]) - 1) * 100, 1),
                          "vs_3y_median_pct": round((math.exp(s["feats"][L][1]) - 1) * 100, 1)} if L in s["feats"] else None),
                 "h": {}}
        for h in HORIZONS:
            mine = [r for r in recs if r["k"] == k and r["h"] == h]
            sel = score([r for r in mine if r["t"] <= sel_end])
            win = window_cover(mine)
            ok, why = gate(sel, win)
            disp_s = score([r for r in mine if r["t"] >= disp])
            q = predict_q(final[h], k, list(s["feats"][L])) if L in s["feats"] else None
            he = {"target_month": mo(L + h), "pass": ok, "why": why,
                  "select": _round(sel), "display": _round(disp_s), "windows": _round_w(win)}
            if q is not None:
                lo, p50, hi = pct_from_log(q)
                he.update({"p10": lo, "p50": p50, "p90": hi})
            entry["h"][str(h)] = he
        ov = seasonal_index(s["real"])
        if ov:
            entry["seasonal_median_3y"] = {"base_month": SINCE, "by_calendar_month": ov}
        out_series.append(entry)

    dec = {}
    for s in series:
        st = DECOMP.get(s["iso3"])
        if st and s["staple"] == st and s["key"] in P and st in world_usd:
            d = decompose(s["datapoints"], world_usd[st])
            if d:
                dec[s["key"]] = d
    for e in out_series:
        if e["key"] in dec:
            e["decomposition"] = dec[e["key"]]

    withoni = rolling(P, oni=oni)
    ot = oni_test(recs, withoni, oni)
    head = {}
    for h in HORIZONS:
        allr = [r for r in recs if r["h"] == h]
        okk = {e["key"] for e in out_series if e["h"][str(h)]["pass"]}
        head[str(h)] = {"all_series_display": _round(score([r for r in allr if r["t"] >= disp])),
                        "all_series_full_2012_on": _round(score(allr)),
                        "passing_series_display": _round(score([r for r in allr if r["t"] >= disp and r["k"] in okk])),
                        "passing_n_series": len(okk), "tested_n_series": len(P),
                        "beats_calendar_p90_display": sum(
                            1 for e in out_series if (e["h"][str(h)]["display"] or {}).get("model_p90") is not None
                            and e["h"][str(h)]["display"]["model_p90"] < e["h"][str(h)]["display"]["calmonth_p90"]),
                        "n_with_display": sum(1 for e in out_series if e["h"][str(h)]["display"])}
    return {"run_date": today.isoformat(), "spec": SPEC, "spec_hash": SPEC_HASH, "default_horizon": DEFAULT_H,
            "horizons": list(HORIZONS), "series": out_series, "headline": head, "oni_test": ot, "excluded": excluded,
            "last_origin": mo(max(r["t"] for r in recs)) if recs else None}


def _round(d):
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in d.items()} if d else None


def _round_w(w):
    return {k: {"n": v["n"], "coverage": None if v["coverage"] is None else round(v["coverage"], 3)} for k, v in w.items()}


# ── forecast log ───────────────────────────────────────────────────────────────────────────────────────────────

def log_entry(out):
    """The numbers frozen today: for every series and horizon, the latest month, the target month and p10/p50/p90 (real
    % change from the latest month), and whether the band passed the test."""
    return {"run_date": out["run_date"], "spec_hash": out["spec_hash"],
            "series": {e["key"]: {"latest_month": e["latest_month"], "latest_real": e["latest_real"],
                                  "h": {h: {k: v for k, v in x.items() if k in ("target_month", "p10", "p50", "p90", "pass")}
                                        for h, x in e["h"].items()}}
                       for e in out["series"]}}


def append_log(entry, path=None):
    """Append-only: an existing entry for the same run date is never rewritten. Returns the full list."""
    path = path or (DATA_DIR / LOG_FILE)
    entries = []
    if path.exists():
        entries = json.loads(path.read_text()).get("data", {}).get("entries", [])
    if not any(e["run_date"] == entry["run_date"] for e in entries):
        entries.append(entry)
    return entries


def check_log(entries, P_real):
    """Compare every logged band with the price that has since arrived. P_real {series key: {month index: real price}}.
    One row per (entry, series, horizon) whose target month is now observed."""
    rows = []
    for e in entries:
        for k, s in e["series"].items():
            real = P_real.get(k) or {}
            for h, x in s["h"].items():
                tm, lm = mi(x["target_month"]), mi(s["latest_month"])
                if tm in real and lm in real and "p10" in x:
                    v = (real[tm] / real[lm] - 1) * 100
                    rows.append({"logged": e["run_date"], "key": k, "h": int(h), "target_month": x["target_month"],
                                 "realised_pct": round(v, 1), "inside": x["p10"] <= v <= x["p90"], "band_shown": x["pass"]})
    return rows


# ── inputs and main ────────────────────────────────────────────────────────────────────────────────────────────

def world_nominal():
    """{staple: {month index: Pink Sheet USD price}} for the staples in bo.WORLD (nominal, unlike bo.world_real)."""
    import refresh_worldbank_pink_sheet as pink
    rows = pink.load_workbook_rows(pink.resolve_workbook_url())
    labels, _u, _c, data_rows, _i = pink._find_header_block(rows)
    col = {str(c).strip(): i for i, c in enumerate(labels) if c}
    out = {}
    for staple, (label, _d) in bo.WORLD.items():
        i = col.get(label)
        if i is None:
            raise RuntimeError(f"Pink Sheet column {label!r} not found")
        ser = {}
        for r in data_rows:
            m, v = pink.parse_month(r[0] if r else None), pink.normalize_num(r[i] if i < len(r) else None)
            if m and v:
                ser[mi(m)] = v
        out[staple] = ser
    return out


def fetch_oni():
    import refresh_enso
    return oni_monthly(refresh_enso.parse_oni(http_get(refresh_enso.ONI_URL, headers=refresh_enso.UA, timeout=60).text))


def fetch_series():
    listing = http_get(f"{bo.API}/FpmaSerieDomestic/", params={"format": "json"}, timeout=120, patient=True).json().get("results") or []
    want = {(i, m, c, t) for i, m, c, t, _ in bo.SERIES}
    uuids = [s["uuid"] for s in listing if ((s.get("iso3_country_code") or "").upper(), s.get("market_name"),
                                            s.get("commodity_name"), (s.get("price_type") or "").upper()) in want]
    dps = bo.fetch_prices(uuids)
    out = []
    for iso, market, commodity, ptype, staple in bo.SERIES:
        hits = [s for s in listing if (s.get("iso3_country_code") or "").upper() == iso and s.get("market_name") == market
                and s.get("commodity_name") == commodity and (s.get("price_type") or "").upper() == ptype]
        if not hits:
            continue
        s = max(hits, key=lambda h: bo._monthly(h)[1])
        out.append({"key": f"{iso}:{commodity}", "iso3": iso, "country": s.get("country_name"), "commodity": commodity,
                    "staple": staple, "market": market, "price_type": ptype.lower(), "datapoints": dps.get(s["uuid"]) or []})
    return out


METHOD = {
    "band": ("A pooled linear quantile regression (tau 0.1, 0.5, 0.9) of the h-month log change in the CPI-deflated FPMA price on "
             "the 12-month log change and the log gap to the median of the previous 36 months, with an intercept per series, "
             "fitted on every series with 24+ training rows. h = 3, 6, 12. Real-time: each rolling-origin fit uses only rows "
             "whose target was known at the origin (expanding window), refitted every 3 months from 2012-01. The quantile "
             "regression is solved by iteratively reweighted least squares (numpy only) and checked against an exact linear "
             "program in the test suite. Quantiles are sorted so they never cross. Bands are real % change from each "
             "series' latest month."),
    "pass_rule": SPEC["pass_rule"] + ". The pass is decided on origins up to " + SELECT_END + "; the pinball losses and "
                 "coverage shown on the page are from origins " + DISPLAY_FROM + " on, which had no say in it. A series with "
                 "fewer than " + str(MIN_SELECT_POINTS) + " early-fold points cannot be tested and gets no band.",
    "baselines": "Unconditional: the series' own expanding-window quantiles of the same h-month change. Calendar-month: the same, "
                 "for origins in the same calendar month only (at least 5). Both are scored on the same points as the model.",
    "oni": "The same model with the ONI of the month before the origin (CPC oni.ascii.txt, real time) as a third predictor.",
    "decomposition": ("For ZAF, HND, ZMB maize (import-parity series; FPMA carries the USD price). Real move since March = "
                      "d ln(world price, Pink Sheet maize, US Gulf, USD) + [d ln(local currency per USD) - d ln(implied CPI "
                      "deflator)] + local. Local = d ln(FPMA USD price) - d ln(world price): the local basis, so transport, "
                      "margins, tariffs, harvest and policy. Pass-through is fixed at 1 (import parity), not fitted. The three "
                      "logs sum exactly to the real change; percentage points are the average over the six orders the three "
                      "can be applied. The world price ends at the Pink Sheet's latest month if that is earlier than the series'."),
    "data_fixes": ("The last 3 months of MDG and ETH are dropped in this file and in enso_price_outlook.json: FPMA's CPI "
                   "deflator for them steps near-constantly at the tail, which looks extrapolated. Gaps of 12+ months are flagged "
                   "(BRA maize has a 48-month gap; rows spanning it are lost). Headline CPI is kept: food CPI would deflate "
                   "the shock away."),
    "cadence": f"Refit every {REFIT_EVERY} months in the test, so bands for a month between refits use a fit at most two months old.",
    "forecast_log": "data/enso_price_forecast_log.json is append-only: one entry per run date with the spec hash. Later runs "
                    "compare every logged band with the price that has arrived (data.checks).",
}


def main():
    if np is None:
        raise RuntimeError("numpy is not installed: the price-risk band is not rebuilt (the last good file stays)")
    series = fetch_series()
    world = world_nominal()
    oni = fetch_oni()
    out = build(series, world, oni)
    entries = append_log(log_entry(out))
    P_real = {}
    for s in series:
        P_real[s["key"]] = {mi(d["date"]): d["price_value_real"] for d in s["datapoints"] if d.get("date")
                            and isinstance(d.get("price_value_real"), (int, float)) and d["price_value_real"] > 0}
        cut = bo.TAIL_TRIM.get(s["iso3"], 0)
        for m in (sorted(P_real[s["key"]])[-cut:] if cut else []):
            P_real[s["key"]].pop(m)
    out["checks"] = check_log(entries, P_real)
    out["first_published"] = entries[0]["run_date"]
    out["log_entries"] = len(entries)
    shown = sum(e["h"][str(DEFAULT_H)]["pass"] for e in out["series"])
    print(f"[price risk] {len(out['series'])} series; h=12 band passes for {shown}; ONI {out['oni_test']['verdict']}; "
          f"log {len(entries)} entries, first {out['first_published']}")
    path = write_json(OUT_FILE, out, source="FAO GIEWS FPMA (CPI-deflated), World Bank Pink Sheet, NOAA CPC ONI; FoodShield price-risk model",
                      status="ok", notes="Grey price-risk band: seasonality and the pull back toward the 3-year level, not an El Niño forecast. See data.spec and _meta.method.")
    env = json.loads(path.read_text())
    env["_meta"]["method"] = METHOD
    path.write_text(json.dumps(env, indent=2, ensure_ascii=False))
    write_json(LOG_FILE, {"entries": entries, "spec": SPEC, "rule": "append-only: one entry per run date, never edited"},
               source="FoodShield price-risk model", status="ok",
               notes="Frozen forecasts. Entries are never rewritten; data/enso_price_risk.json checks them against arriving prices.")


if __name__ == "__main__":
    main()
