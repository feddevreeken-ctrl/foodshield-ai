"""
Price counterfactual for southern-Africa maize: what the local price did in 2026 against what a normal year,
the currency and world maize prices would have led us to expect.

Sources (same feeds as the rest of the tab): FAO GIEWS FPMA domestic price API (local price in LCU, CPI-deflated
price, and FAO's own USD price for the same quantity), the World Bank Pink Sheet monthly workbook (maize, nominal
USD/mt) and NOAA CPC ONI (data/enso.json). Nothing is typed in: every number is computed below.

Why this exists: enso_price_analogs.json compares 2026 with earlier El Niño paths. That cannot say how much of a
move is just the season, a weaker currency or a dearer world market. This file does.

Method, per series (the eight series of refresh_enso_price_analogs.py, wholesale/retail kept as FPMA labels them):
  Window      March of the current year (the plate's base month, index 100) to the latest month. Real (CPI-deflated)
              local price, log scale.
  Normal years  Every past calendar year whose DJF ONI is below +0.5 in the year before, in the year itself and
              the year after (i.e. not an El Niño onset year, not an El Niño winter year; same exclusion as
              build_enso_price_outlook.nino_years). The current year is excluded. Each normal year gives one
              path: log(real price in month m / real price in March).
  Expected, seasonal  Median over normal years of that path, month by month. Band = 25th to 75th percentile
              of the normal years' own paths. This is the season PLUS whatever drift normal years had.
  Currency    FX = local currency per USD = FPMA price_value / price_value_dollar (FAO's conversion for the same
              datapoint; months where the USD value is under 0.15 are dropped, rounding error above 3.3 percent).
  World       Pink Sheet maize, nominal USD/mt.
  Adjustment  For each normal year and month: deviation of the real local path from the median path, regressed
              with no intercept on (FX change minus the median FX change of normal years, world change minus the
              median world change). Both regressors are log changes since March. Ordinary least squares, standard
              errors clustered by year (the months of one year overlap). A coefficient is kept only when |t| >= 2.0
              and it is positive, and is capped at 1; otherwise it is set to 0 and the series is shown without it. With fewer than 6
              normal years no coefficient is used (clustered errors are meaningless on so few years).
              Expected, FX-adjusted = seasonal median + b_fx x (actual FX change - median FX change).
              Expected, FX + world = the above + b_world x (actual world change - median world change).
  Excess      actual real path minus expected (FX + world) path, in log, shown as percent of expected. Range =
              the same excess measured against the 25th and the 75th percentile of the normal years' residuals
              (the expected band). Placebo: the same excess computed for each normal year with that year left out
              of the median; the page prints where 2026 sits among them.
  What it is not: an El Niño effect. It is movement not explained by season, currency or world prices. Policy,
              trade bans, regional weather, stocks and measurement all sit in it. Samples are small (one path per
              year); coefficients are fitted on those years only, so treat the range, not the point, as the result.

Output: data/enso_price_counterfactual.json
"""
import json
import math
import statistics
from datetime import date

from _common import DATA_DIR, http_get, stamp_inputs, write_json
import build_enso_price_outlook as po

API = po.API
TOOL_URL = po.TOOL_URL
SOURCE = ("FAO GIEWS FPMA Tool (domestic prices API: real price, LCU price and FAO USD price); World Bank Pink Sheet "
          "maize (nominal USD/mt); NOAA CPC ONI")
ORIGIN_MONTH = 3                 # March
MIN_YEARS = 3                    # normal years needed per series
THIN_YEARS = 6                   # fewer than this is flagged thin
MIN_USD = 0.15                   # USD value below this: rounding error above 3.3 percent, month dropped from the FX series
T_KEEP = 2.0
LABEL = "Movement not explained by season, currency or world prices"
SERIES = [s for s in po.SERIES if s[4] == "maize" and s[0] in ("ZAF", "ZMB", "MWI", "MOZ", "SWZ", "LSO", "NAM", "BWA")]


def q(vals, p):
    v = sorted(vals)
    if not v:
        return None
    k = (len(v) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def world_maize():
    import refresh_worldbank_pink_sheet as pink
    rows = pink.load_workbook_rows(pink.resolve_workbook_url())
    labels, _u, _c, data_rows, _i = pink._find_header_block(rows)
    col = {str(c).strip(): i for i, c in enumerate(labels) if c}
    i = col["Maize"]
    out = {}
    for r in data_rows:
        mo, v = pink.parse_month(r[0] if r else None), pink.normalize_num(r[i] if i < len(r) else None)
        if mo and v:
            out[po.mi(mo)] = v
    return out


def cluster_ols(rows, keys):
    """rows: [(year, x1, x2, y)]; no intercept; returns (b, se, r2) for two regressors, year-clustered SEs."""
    S = [[0.0, 0.0], [0.0, 0.0]]
    xy = [0.0, 0.0]
    for _, a, b, y in rows:
        S[0][0] += a * a; S[0][1] += a * b; S[1][1] += b * b
        xy[0] += a * y; xy[1] += b * y
    S[1][0] = S[0][1]
    det = S[0][0] * S[1][1] - S[0][1] ** 2
    if abs(det) < 1e-12:
        return None
    inv = [[S[1][1] / det, -S[0][1] / det], [-S[1][0] / det, S[0][0] / det]]
    beta = [inv[0][0] * xy[0] + inv[0][1] * xy[1], inv[1][0] * xy[0] + inv[1][1] * xy[1]]
    by = {}
    for yr, a, b, y in rows:
        e = y - beta[0] * a - beta[1] * b
        g = by.setdefault(yr, [0.0, 0.0])
        g[0] += a * e; g[1] += b * e
    meat = [[0.0, 0.0], [0.0, 0.0]]
    for g in by.values():
        for i in range(2):
            for j in range(2):
                meat[i][j] += g[i] * g[j]
    n_c = len(by)
    adj = n_c / (n_c - 1) if n_c > 1 else 1.0
    V = [[sum(inv[i][k] * meat[k][l] * inv[l][j] for k in range(2) for l in range(2)) * adj for j in range(2)] for i in range(2)]
    se = [math.sqrt(max(V[0][0], 0)), math.sqrt(max(V[1][1], 0))]
    ss_tot = sum(y * y for *_, y in rows)
    ss_res = sum((y - beta[0] * a - beta[1] * b) ** 2 for _, a, b, y in rows)
    return beta, se, (1 - ss_res / ss_tot) if ss_tot else None


def build_series(meta, dps, world, bad, this_year):
    real, fx = {}, {}
    for d in dps:
        if not d.get("date"):
            continue
        m = po.mi(d["date"])
        if isinstance(d.get("price_value_real"), (int, float)) and d["price_value_real"] > 0:
            real[m] = d["price_value_real"]
        pv, pd_ = d.get("price_value"), d.get("price_value_dollar")
        if isinstance(pv, (int, float)) and isinstance(pd_, (int, float)) and pv > 0 and pd_ >= MIN_USD:
            fx[m] = pv / pd_
    if not real:
        return None, "no real price series"
    latest = max(real)
    o = this_year * 12 + ORIGIN_MONTH - 1
    if o not in real or latest <= o or o not in fx or o not in world:
        return None, "March of the current year missing in price, FX or world series"
    offs = [j for j in range(1, latest - o + 1) if (o + j) in real]
    # normal-year paths
    years = {}
    for y in range(2005, this_year):
        if y in bad:
            continue
        yo = y * 12 + ORIGIN_MONTH - 1
        if yo not in real or yo not in fx or yo not in world:
            continue
        rec = {}
        for j in offs:
            m = yo + j
            if m in real and m in fx and m in world:
                rec[j] = (math.log(real[m] / real[yo]), math.log(fx[m] / fx[yo]), math.log(world[m] / world[yo]))
        if len(rec) == len(offs):
            years[y] = rec
    if len(years) < MIN_YEARS:
        return None, f"only {len(years)} normal years with a full window"
    if any((o + j) not in fx or (o + j) not in world for j in offs):
        return None, "FX or world series missing in the current window"

    def med(ys, j, k):
        return statistics.median(years[y][j][k] for y in ys)

    ys_all = sorted(years)
    med_all = {j: [med(ys_all, j, k) for k in range(3)] for j in offs}
    rows = [(y, years[y][j][1] - med_all[j][1], years[y][j][2] - med_all[j][2], years[y][j][0] - med_all[j][0])
            for y in ys_all for j in offs]
    fit = cluster_ols(rows, None)
    if not fit:
        return None, "regression singular"
    b, se, r2 = fit
    t = [b[i] / se[i] if se[i] else 0.0 for i in range(2)]
    keep = [abs(t[i]) >= T_KEEP and b[i] > 0 and len(ys_all) >= THIN_YEARS for i in range(2)]
    bk = [min(1.0, b[i]) if keep[i] else 0.0 for i in range(2)]
    act = {j: (math.log(real[o + j] / real[o]), math.log(fx[o + j] / fx[o]), math.log(world[o + j] / world[o])) for j in offs}

    def expected(j, ys, bb, use):
        """Expected log path using medians over ys; use = how many adjustments."""
        m_ = [med(ys, j, k) for k in range(3)]
        e = m_[0]
        if use >= 1:
            e += bb[0] * (act[j][1] - m_[1])
        if use >= 2:
            e += bb[1] * (act[j][2] - m_[2])
        return e

    # residuals of normal years around the full-model expectation, leave-one-year-out
    def resid_year(y, bb, j):
        ys = [z for z in ys_all if z != y]
        m_ = [med(ys, j, k) for k in range(3)]
        e = m_[0] + bb[0] * (years[y][j][1] - m_[1]) + bb[1] * (years[y][j][2] - m_[2])
        return years[y][j][0] - e

    jl = offs[-1]
    plc = {y: resid_year(y, bk, jl) for y in ys_all}
    pl_pct = {y: round((math.exp(v) - 1) * 100, 1) for y, v in plc.items()}
    r25, r75 = q(plc.values(), 0.25), q(plc.values(), 0.75)

    months = []
    for j in offs:
        e0, e1, e2 = expected(j, ys_all, bk, 0), expected(j, ys_all, bk, 1), expected(j, ys_all, bk, 2)
        seas = [sorted(years[y][j][0] for y in ys_all)]
        idx = lambda v: round(math.exp(v) * 100, 1)
        months.append({
            "month": po.month(o + j),
            "actual": idx(act[j][0]),
            "seasonal": idx(e0),
            "band": [idx(q(seas[0], 0.25)), idx(q(seas[0], 0.75))],
            "fx_adj": idx(e1),
            "full_adj": idx(e2),
            "excess_pct": round((math.exp(act[j][0] - e2) - 1) * 100, 1),
        })
    e_seas, e_fx, e_full = (expected(jl, ys_all, bk, u) for u in (0, 1, 2))
    pct = lambda a, e: round((math.exp(a - e) - 1) * 100, 1)
    full = pct(act[jl][0], e_full)
    lo, hi = pct(act[jl][0], e_full + r75), pct(act[jl][0], e_full + r25)
    rank_lo = sum(1 for v in plc.values() if v < math.log(1 + full / 100))
    return {
        "latest_month": po.month(latest),
        "base_month": po.month(o),
        "window_months": len(offs),
        "normal_years": {"n": len(ys_all), "years": ys_all, "thin": len(ys_all) < THIN_YEARS},
        "actual_index": months[-1]["actual"],
        "expected_index": {"seasonal": months[-1]["seasonal"], "fx_adj": months[-1]["fx_adj"], "full_adj": months[-1]["full_adj"]},
        "excess_pct": {"vs_seasonal": pct(act[jl][0], e_seas), "vs_fx_adjusted": pct(act[jl][0], e_fx), "vs_fx_and_world": full,
                       "range_fx_and_world": [min(lo, hi), max(lo, hi)]},
        "moves_since_base_pct": {"fx_actual": round((math.exp(act[jl][1]) - 1) * 100, 1),
                                 "fx_normal_median": round((math.exp(med_all[jl][1]) - 1) * 100, 1),
                                 "world_actual": round((math.exp(act[jl][2]) - 1) * 100, 1),
                                 "world_normal_median": round((math.exp(med_all[jl][2]) - 1) * 100, 1)},
        "regression": {"b_fx": round(b[0], 3), "t_fx": round(t[0], 2), "b_world": round(b[1], 3), "t_world": round(t[1], 2),
                       "used_fx": keep[0], "used_world": keep[1], "b_fx_used": round(bk[0], 3), "b_world_used": round(bk[1], 3),
                       "n_obs": len(rows), "n_years": len(ys_all), "r2": round(r2, 3) if r2 is not None else None},
        "placebo": {"n": len(plc), "min": min(pl_pct.values()), "p25": round((math.exp(r25) - 1) * 100, 1),
                    "median": round((math.exp(statistics.median(plc.values())) - 1) * 100, 1),
                    "p75": round((math.exp(r75) - 1) * 100, 1), "max": max(pl_pct.values()),
                    "n_below_2026": rank_lo, "by_year": pl_pct},
        "months": months,
    }, None


def build(listing, datapoints, enso_hist, world, today=None):
    today = today or date.today()
    bad = po.nino_years(enso_hist)
    this_year = int(po.CURRENT[1][:4])
    want = {(i, m, c, t): i for i, m, c, t, _ in SERIES}
    rows, excluded, pick = [], [], {}
    for s in listing:
        key = ((s.get("iso3_country_code") or "").upper(), s.get("market_name"), s.get("commodity_name"), (s.get("price_type") or "").upper())
        if key in want and (key not in pick or po._monthly(s)[1] > po._monthly(pick[key])[1]):
            pick[key] = s          # one series per key: the one running to the latest month (same rule as the analogs)
    for key, s in pick.items():
        res, why = build_series(s, datapoints.get(s["uuid"], []), world, bad, this_year)
        if not res:
            excluded.append({"iso": key[0], "reason": why})
            continue
        rows.append({"iso": key[0], "name": s.get("country_name"), "market": s.get("market_name"),
                     "commodity": s.get("commodity_name"), "price_type": (s.get("price_type") or "").lower(),
                     "currency": s.get("currency"), "series_source": s.get("source_name"), **res})
    order = [i for i, *_ in SERIES]
    rows.sort(key=lambda r: order.index(r["iso"]))
    return {
        "label": LABEL,
        "never_el_nino": "This is not an El Niño effect. Policy, trade bans, regional weather, stocks and measurement all sit in it.",
        "base": f"March {this_year} = 100, real (CPI-deflated) local price",
        "excluded_years_rule": "Years excluded from the normal set: any year with DJF ONI >= +0.5 in that year or the next (El Niño onset and winter years), and the current year.",
        "excluded_years": sorted(bad),
        "rows": rows, "excluded": excluded,
        "method": {
            "expected_seasonal": "Median over normal years of log(real price in month / real price in March); band = 25th to 75th percentile of those years.",
            "fx": "Local currency per USD = FPMA price_value / price_value_dollar; months with the USD value under 0.15 dropped.",
            "world": "World Bank Pink Sheet maize, nominal USD/mt.",
            "regression": ("Normal years' deviations from the median path regressed, no intercept, on the FX and world changes since March "
                           "(each minus its normal-year median). OLS, errors clustered by year. A coefficient is used only if positive and "
                           f"|t| >= {T_KEEP} and the series has 6 or more normal years, capped at 1; else 0."),
            "range": "Excess measured against the 25th and 75th percentile of the normal years' leave-one-year-out residuals.",
            "caveat": "One path per normal year, so n is small and months within a year overlap; read the range, not the point. The regression describes normal years only.",
        },
        "tool_url": TOOL_URL,
    }


def main():
    listing = http_get(f"{API}/FpmaSerieDomestic/", params={"format": "json"}, timeout=120, patient=True).json().get("results") or []
    want = {(i, m, c, t) for i, m, c, t, _ in SERIES}
    uuids = [s["uuid"] for s in listing if ((s.get("iso3_country_code") or "").upper(), s.get("market_name"),
                                            s.get("commodity_name"), (s.get("price_type") or "").upper()) in want]
    uuids = [u for u in uuids if u]
    if not uuids:
        raise RuntimeError("FPMA series list matched none of the counterfactual series")
    enso = json.loads((DATA_DIR / "enso.json").read_text())["data"]["history"]
    out = build(listing, po.fetch_prices(uuids), enso, world_maize())
    for r in out["rows"]:
        e = r["excess_pct"]
        print(f"[counterfactual] {r['iso']} {r['price_type']}: vs season {e['vs_seasonal']:+}%, fx {e['vs_fx_adjusted']:+}%, "
              f"fx+world {e['vs_fx_and_world']:+}% range {e['range_fx_and_world']} (n years {r['normal_years']['n']}, R2 {r['regression']['r2']})")
    write_json("enso_price_counterfactual.json", out, source=SOURCE, status="ok",
               notes=("Southern-Africa maize: actual real local price against a normal-year expectation, then adjusted for currency and "
                      "world maize. Excess is movement not explained by season, currency or world prices, never an El Niño effect. See data.method."))
    stamp_inputs("enso_price_counterfactual.json")


if __name__ == "__main__":
    main()
