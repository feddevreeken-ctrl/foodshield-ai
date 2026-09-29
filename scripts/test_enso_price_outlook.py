"""Synthetic-fixture tests for build_enso_price_outlook: event list, analog band, skill gate, seasonal normal,
world pass-through gate, El Niño excess path, end-to-end null model."""
import json
import math
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_enso_price_outlook as b  # noqa: E402


def hist(pairs):
    return [{"season": "DJF", "year": y, "anom": a} for y, a in pairs]


def test_events(fails):
    ev = b.el_nino_events(hist([(1998, 2.2), (2003, 0.9), (2004, 0.1), (2015, 0.7), (2016, 2.5), (2017, -0.3),
                                (2019, 0.99), (2020, 0.71), (2024, 1.8), (2027, 2.0)]))
    got = [(e["label"], e["oni"]) for e in ev]
    want = [("2002-03", 0.9), ("2015-16", 2.5), ("2018-19", 0.99), ("2023-24", 1.8)]
    if got != want:
        fails.append(f"events: back-to-back winters must merge at the stronger peak, pre-2000 and current dropped; got {got}")


def series(fn, start="2000-01", end="2026-08"):
    return {m: fn(m) for m in range(b.mi(start), b.mi(end) + 1)}


def test_analog_band(fails):
    p15, p23 = b.mi("2015-12"), b.mi("2023-12")
    # Flat 100 except: 2015-16 doubles from the peak on, 2023-24 rises 50% from the peak on.
    real = series(lambda m: 200.0 if p15 <= m <= p15 + 15 else 150.0 if p23 <= m <= p23 + 15 else 100.0)
    latest = b.mi("2026-08")
    k0 = latest - b.mi("2026-12")
    a = b.analog(real, latest, k0, {"2015-16": p15, "2023-24": p23})
    if not a or a["lo"] != 50.0 or a["hi"] != 100.0:
        fails.append(f"analog band: expected lo 50 / hi 100, got {a and (a['lo'], a['hi'])}")
        return
    if a["peak_month"] != "2026-12":
        fails.append(f"analog peak month: the higher path first peaks at the December peak, got {a['peak_month']}")
    if a["band"][0] != ["2026-08", 100.0, 100.0] or a["band"][-1][0] != "2028-03":
        fails.append(f"analog band must start at the latest month (=100) and end March after the peak: {a['band'][0]}, {a['band'][-1]}")
    dec = dict((m, (lo, hi)) for m, lo, hi in a["band"])["2026-12"]
    if dec != (150.0, 200.0):
        fails.append(f"analog band at the peak month: expected (150, 200), got {dec}")
    aft = b.aftermath(real, p15, k0)
    if not aft or aft["pct"] != 0.0 or aft["from"] != "2015-03" or aft["to"] != "2015-08":
        fails.append(f"aftermath at the matching offset: {aft}")


def samples(y_fn, analog_noise):
    rng = random.Random(7)
    out = []
    events = ["2002-03", "2006-07", "2009-10", "2015-16", "2023-24"]
    for k in range(6):
        ys = {}
        for e in events:
            x = [rng.uniform(-30, 0), rng.uniform(-30, 30)]
            ys[e] = (y_fn(x, rng), x)
        ay = {e: ys[e][0] + analog_noise(rng) for e in ("2015-16", "2023-24")}
        for e, (y, x) in ys.items():
            out.append({"key": f"S{k}", "event": e, "y": y, "x": x, "analog_y": ay})
    return out


def test_gate(fails):
    # Price rises unrelated to the predictors; the analog echoes each series' past closely: no skill.
    noise = samples(lambda x, r: 20 + r.gauss(0, 25), lambda r: r.gauss(0, 1))
    for s in noise:  # the analog baseline = the same series' own analog-event rise; make that the truth
        s["y"] = s["analog_y"]["2015-16"]
        s["analog_y"] = {"2015-16": s["y"], "2023-24": s["y"]}
    status = b.evaluate(noise)[3]
    if status != "no_skill":
        fails.append(f"gate: a model with no signal against a near-perfect analog must be no_skill, got {status}")
    # Price rise driven exactly by the predictors; the analog is noise: the model must pass.
    sig = samples(lambda x, r: 10 - 2 * x[0] - 0.5 * x[1], lambda r: r.gauss(0, 40))
    pooled, per_key, resid, status = b.evaluate(sig)
    if status != "ok" or pooled["mae_model"] > 0.5:
        fails.append(f"gate: an exact linear signal must pass with ~0 error, got {status} {pooled and pooled['mae_model']}")
    few = [s for s in sig if s["event"] in ("2015-16", "2023-24")]
    if b.evaluate(few)[3] != "insufficient_data":
        fails.append("gate: two events are not enough to judge skill")


SEASON = [0.04, 0.03, -0.02, -0.06, -0.05, -0.02, 0.0, 0.01, 0.02, 0.02, 0.01, 0.02]  # log step into each month


def seasonal_series(shock_years, shock=0.2, start="2000-01", end="2026-08"):
    lv, out = 0.0, {}
    for m in range(b.mi(start), b.mi(end) + 1):
        lv += SEASON[m % 12] + (shock if (m // 12) in shock_years else 0.0)
        out[m] = 100 * math.exp(lv)
    return out


def test_seasonal_normal(fails):
    enso_hist = hist([(2003, 0.9), (2010, 1.5), (2016, 2.5), (2024, 1.8), (2012, -0.5)])
    bad = b.nino_years(enso_hist)
    if bad != {2002, 2003, 2009, 2010, 2015, 2016, 2023, 2024, 2026}:
        fails.append(f"nino_years: El Niño year (DJF year - 1), the year after, and the current year; got {sorted(bad)}")
    # Every El Niño year carries a big monthly jump; the normal must ignore it and recover SEASON exactly.
    real = seasonal_series({2002, 2009, 2015, 2023})
    med, n = b.seasonal_normal(real, bad)
    if any(abs(med[c] - SEASON[c]) > 1e-9 for c in range(12)):
        fails.append(f"seasonal normal must recover the non-El-Niño step per calendar month: {med}")
    if min(n.values()) < 10:
        fails.append(f"seasonal normal: expected >= 10 non-El-Niño years per month, got {n}")
    # A steady real trend on top is not season: the steps are centred, so they still recover SEASON.
    trend = {m: v * math.exp(0.01 * (m - b.mi("2000-01"))) for m, v in real.items()}
    med_t, _ = b.seasonal_normal(trend, bad)
    if any(abs(med_t[c] - SEASON[c]) > 1e-9 for c in range(12)):
        fails.append(f"seasonal normal must be centred (a trend is not season): {med_t}")
    path = b.normal_path(med, b.mi("2026-08"), 12)
    if abs(path[12] - sum(SEASON)) > 1e-9 or path[0] != 0.0 or abs(path[1] - SEASON[8]) > 1e-9:
        fails.append(f"normal path must cumulate the steps from the month after the origin: {path[:3]} .. {path[12]}")
    few, _ = b.seasonal_normal({m: v for m, v in real.items() if m >= b.mi("2022-01")}, bad)
    if few:
        fails.append(f"seasonal normal: fewer than {b.SEASON_MIN_YEARS} years in any month must give no normal year, got {few}")


def test_beta_gate(fails):
    rng = random.Random(11)
    bad = {2002, 2003, 2009, 2010, 2015, 2016, 2023, 2024, 2026}
    months = range(b.mi("2000-01"), b.mi("2026-08") + 1)
    wl, world = 0.0, {}
    for m in months:
        wl += rng.gauss(0, 0.05)
        world[m] = 200 * math.exp(wl)

    def dom(beta, noise, rng2):
        lv, out = 0.0, {}
        for m in months:
            lv += (beta * math.log(world[m] / world[m - 1]) if (m - 1) in world else 0.0) + rng2.gauss(0, noise)
            out[m] = 100 * math.exp(lv)
        return out
    pt = b.pass_through(dom(0.6, 0.01, random.Random(1)), world, {}, bad)
    if not pt["used"] or abs(pt["beta"] - 0.6) > 0.05 or pt["n_months"] < b.BETA_MIN_MONTHS:
        fails.append(f"beta: a clean 0.6 pass-through must be kept near 0.6, got {pt}")
    if b.pass_through(dom(1.6, 0.01, random.Random(2)), world, {}, bad)["beta"] != 1.0:
        fails.append("beta: a pass-through above 1 must be clipped to 1")
    if b.pass_through(dom(-0.8, 0.01, random.Random(4)), world, {}, bad)["beta"] != 0.0:
        fails.append("beta: a negative pass-through must be clipped to 0")
    pt = b.pass_through(dom(0.0, 0.05, random.Random(3)), world, {}, bad)
    if pt["beta"] != 0.0 or pt["used"]:
        fails.append(f"beta: an unrelated series must not be significant, got {pt}")
    short = {m: v for m, v in dom(0.6, 0.01, random.Random(1)).items() if m >= b.mi("2020-01")}
    pt = b.pass_through(short, world, {}, bad)
    if pt["beta"] != 0.0 or pt["n_months"] >= b.BETA_MIN_MONTHS:
        fails.append(f"beta: fewer than {b.BETA_MIN_MONTHS} non-El-Niño months must give 0, got {pt}")
    if b.pass_through(short, None, {}, bad)["why"] != "no world benchmark for this staple":
        fails.append("beta: no benchmark must say so")
    # Excess path: an event whose domestic path is exactly normal + 0.5 x world has zero El Niño excess.
    o, n = b.mi("2015-08"), 19
    norm = [0.01 * j for j in range(n + 1)]
    real = {o + j: 100 * math.exp(norm[j] + 0.5 * math.log(world[o + j] / world[o])) for j in range(n + 1)}
    ex = b.excess_path(real, world, 0.5, norm, o, n)
    if len(ex) != n + 1 or max(abs(v) for v in ex.values()) > 1e-9:
        fails.append(f"excess path must be 0 when the event is a normal year plus world pass-through: {ex}")
    real[o + 5] *= 1.3
    if abs(b.excess_path(real, world, 0.5, norm, o, n)[5] - math.log(1.3)) > 1e-9:
        fails.append("excess path must keep a local 30% jump")


def test_build_null_model(fails):
    """End to end on one synthetic series: too few samples, so no model numbers anywhere, analog still present."""
    uid = "u-zaf"
    listing = [{"uuid": uid, "iso3_country_code": "ZAF", "country_name": "South Africa", "market_name": "Randfontein",
                "commodity_name": "Maize (white)", "price_type": "WHOLESALE", "currency": "ZAR", "measure_unit_label": "tonne",
                "source_name": "SAFEX", "periodicity": [{"period": "monthly", "start_date": "2000-01-01", "end_date": "2026-08-01"}]},
               {"uuid": "u-x", "iso3_country_code": "ZWE", "country_name": "Zimbabwe", "market_name": "Epworth",
                "commodity_name": "Maize meal", "price_type": "RETAIL",
                "periodicity": [{"period": "monthly", "start_date": "2024-04-01", "end_date": "2026-07-01"}]}]
    rng = random.Random(3)
    dps = [{"date": f"{b.month(m)}-01", "price_value": 3000 + rng.uniform(-300, 300), "price_value_real": 1500 + rng.uniform(-200, 200)}
           for m in range(b.mi("2000-01"), b.mi("2026-08") + 1)]
    enso_hist = hist([(2003, 0.9), (2010, 1.47), (2016, 2.5), (2019, 0.99), (2024, 1.84)])
    model = {"ZAF": {"corn": {"yield_pct_per_oni_nino": -21.0}}}
    outlook = {"cases": {"observed": {"oni": 1.8, "label": "JJA 2026 observed"}},
               "rows_all": [{"iso": "ZAF", "crop": "corn", "status": "shown", "slope_pct_per_oni": -21.0, "q_nino": 0.02},
                            {"iso": "ZWE", "crop": "corn", "status": "shown", "slope_pct_per_oni": -27.0, "q_nino": 0.02}]}
    out = b.build(listing, {uid: dps}, enso_hist, model, outlook, [], [], today=date(2026, 9, 29))
    json.dumps(out, allow_nan=False)
    if len(out["rows"]) != 1 or out["rows"][0]["iso3"] != "ZAF":
        fails.append(f"build: expected the one ZAF row, got {[r['iso3'] for r in out['rows']]}")
        return
    r = out["rows"][0]
    if out["model_status"] == "ok" or out["coefficients"] is not None or r["model"] is not None:
        fails.append(f"build: 5 samples must not publish model numbers (status {out['model_status']})")
    if not r["analog"] or r["analog"]["n_paths"] != 2 or r["latest"]["month"] != "2026-08":
        fails.append("build: the analog path must still be published")
    if not any(e["iso3"] == "ZWE" and "2024-04" in e["reason"] for e in out["excluded"]):
        fails.append(f"build: ZWE (harm documented, series starts 2024) must be listed as excluded: {out['excluded']}")
    for k in ("iso3", "country", "commodity", "series", "latest", "aftermath", "analog", "effect_replay", "model", "skill"):
        if k not in r:
            fails.append(f"build: row lacks {k}")
    er = r.get("effect_replay") or {}
    if er.get("world", {}).get("beta") != 0.0 or not er.get("normal", {}).get("available") or er.get("n_paths") != 2:
        fails.append(f"build: effect replay must be published with beta 0 when no world series is passed: {er.get('world')}")
    for lab, v in (er.get("paths") or {}).items():
        a = dict(er["normal"]["points"])[v["peak_month"]]
        if round(a - 100, 1) != v["normal_at_peak_pct"]:
            fails.append(f"build: normal_at_peak_pct must be the normal path at the replay's peak month ({lab})")
    if not out.get("replay_skill") or "mae_adjusted" not in out["replay_skill"]:
        fails.append("build: pooled replay skill must be published")
    # With beta 0 the normal year cancels: normal + excess must equal the plain real replay.
    for lab, v in (er.get("paths") or {}).items():
        plain = dict(r["analog"]["paths"][lab]["points"])
        if any(abs(plain[mo] - ix) > 0.11 for mo, ix in v["points"]):
            fails.append(f"build: with no world pass-through the El Niño-effect path must equal the plain replay ({lab})")


def main():
    fails = []
    for t in (test_events, test_analog_band, test_gate, test_seasonal_normal, test_beta_gate, test_build_null_model):
        t(fails)
    for f in fails:
        print("FAIL", f)
    print("PASS — El Niño price outlook" if not fails else f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
