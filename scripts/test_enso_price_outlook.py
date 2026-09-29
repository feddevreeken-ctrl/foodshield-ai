"""Synthetic-fixture tests for build_enso_price_outlook: event list, analog band, skill gate, end-to-end null model."""
import json
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
    for k in ("iso3", "country", "commodity", "series", "latest", "aftermath", "analog", "model", "skill"):
        if k not in r:
            fails.append(f"build: row lacks {k}")


def main():
    fails = []
    for t in (test_events, test_analog_band, test_gate, test_build_null_model):
        t(fails)
    for f in fails:
        print("FAIL", f)
    print("PASS — El Niño price outlook" if not fails else f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
