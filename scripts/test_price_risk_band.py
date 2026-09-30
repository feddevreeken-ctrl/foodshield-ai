"""Tests for build_price_risk_band.py on synthetic fixtures (no network).

What they pin down:
  - the quantile regression (intercept-only = the sample quantile; recovers a known conditional quantile; matches an
    exact linear program's objective when scipy is installed);
  - the features and the row alignment (what is known at an origin);
  - no look-ahead: cutting the series after an origin leaves every earlier band unchanged;
  - the pass gate: each condition on its own, the early-fold rule (later data cannot change the score the pass rests on),
    the El Niño-window hide rule, and a fixture where mean reversion makes bands pass;
  - the ONI verdict, the decomposition identity, the append-only log and its check against arriving prices,
    and the MDG/ETH tail trim.

Run: python3 scripts/test_price_risk_band.py
"""
import json
import math
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_price_risk_band as b  # noqa: E402

np = b.np
M0 = 24000   # 2000-01


def ar_series(seed, n=320):
    """A slow random-walk level plus transitory AR(1) noise: prices that fall back toward where they have been."""
    r = np.random.default_rng(seed)
    m = np.cumsum(r.normal(0, 0.012, n))
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.6 * e[i - 1] + r.normal(0, 0.1)
    return {M0 + i: float(math.exp(4 + m[i] + e[i])) for i in range(n)}


def rw_series(seed, sd=0.09, n=320):
    u = np.cumsum(np.random.default_rng(seed).normal(0, sd, n))
    return {M0 + i: float(math.exp(4 + u[i])) for i in range(n)}


def panel(n_ar=5, n_rw=0, cut=None):
    P = {}
    for i in range(n_ar):
        P[f"AR{i}"] = ar_series(i)
    for i in range(n_rw):
        P[f"RW{i}"] = rw_series(10 + i)
    if cut is not None:
        P = {k: {m: v for m, v in s.items() if m <= cut} for k, s in P.items()}
    return {k: b.prep(s) for k, s in P.items()}


@unittest.skipIf(np is None, "numpy not installed")
class QuantileRegression(unittest.TestCase):
    def test_intercept_only_is_the_sample_quantile(self):
        y = np.random.default_rng(0).normal(size=501)
        for tau in b.TAUS:
            beta = b.qreg(np.ones((len(y), 1)), y, tau)
            best = float(b.pinball(y, np.full(len(y), np.quantile(y, tau)), tau).sum())
            got = float(b.pinball(y, np.full(len(y), beta[0]), tau).sum())
            self.assertLess(got / best - 1, 1e-6, tau)

    def test_recovers_a_known_conditional_quantile(self):
        r = np.random.default_rng(1)
        x = r.normal(size=20000)
        y = 1.0 + 2.0 * x + r.normal(size=20000)
        beta = b.qreg(np.column_stack([np.ones_like(x), x]), y, 0.9)
        self.assertAlmostEqual(beta[1], 2.0, delta=0.05)
        self.assertAlmostEqual(beta[0], 1.0 + 1.2816, delta=0.05)

    def test_matches_the_exact_linear_program(self):
        try:
            from scipy.optimize import linprog
        except ImportError:
            self.skipTest("scipy not installed")
        r = np.random.default_rng(2)
        n, ns = 400, 4
        D = np.eye(ns)[r.integers(0, ns, n)]
        x = r.normal(size=(n, 2))
        y = D @ r.normal(size=ns) + x @ [0.5, -0.8] + r.standard_t(3, size=n) * 0.5 * (1 + abs(x[:, 0]))
        X = np.hstack([x, D])
        for tau in b.TAUS:
            c = np.concatenate([np.zeros(2 * X.shape[1]), tau * np.ones(n), (1 - tau) * np.ones(n)])
            A = np.hstack([X, -X, np.eye(n), -np.eye(n)])
            exact = linprog(c, A_eq=A, b_eq=y, bounds=[(0, None)] * A.shape[1], method="highs").fun
            got = float(b.pinball(y, X @ b.qreg(X, y, tau), tau).sum())
            self.assertLess(got / exact - 1, 1e-5, tau)
            self.assertGreaterEqual(got / exact - 1, -1e-9)


@unittest.skipIf(np is None, "numpy not installed")
class Features(unittest.TestCase):
    def test_features_and_targets(self):
        real = {m: 100.0 * (1.01 ** (m - M0)) for m in range(M0, M0 + 80)}
        f, lr = b.features(real)
        self.assertNotIn(M0 + 23, f)      # needs >= 24 of the 36 months before it (and 12 months back)
        self.assertIn(M0 + 24, f)
        x1, x2 = f[M0 + 40]
        self.assertAlmostEqual(x1, 12 * math.log(1.01), places=9)
        past = [real[j] for j in range(M0 + 4, M0 + 40)]
        self.assertAlmostEqual(x2, lr[M0 + 40] - math.log(sorted(past)[17] * 0.5 + sorted(past)[18] * 0.5), places=9)
        P = b.prep(real)
        self.assertAlmostEqual(P["y"][12][M0 + 40], 12 * math.log(1.01), places=9)
        self.assertNotIn(M0 + 79, P["y"][3])   # the target month is beyond the data

    def test_training_rows_are_only_what_was_known(self):
        P = panel(2)
        t = M0 + 200
        keys, X, y, ids = b.train_set(P, 12, t)
        self.assertEqual(len(keys), 2)
        self.assertGreater(len(y), 100)
        # every row's target month is at or before the origin
        for k, s in P.items():
            n = sum(1 for m in s["y"][12] if m + 12 <= t)
            self.assertEqual(int((ids == keys.index(k)).sum()), n)

    def test_short_series_gets_no_intercept(self):
        P = panel(1)
        short = {m: v for m, v in ar_series(9).items() if m < M0 + 45}
        P["SHORT"] = b.prep(short)
        keys, *_ = b.train_set(P, 12, M0 + 250)
        self.assertNotIn("SHORT", keys)


@unittest.skipIf(np is None, "numpy not installed")
class NoLookAhead(unittest.TestCase):
    def test_cutting_the_data_after_an_origin_changes_no_earlier_band(self):
        full = b.rolling(panel(3))
        cut_at = b.mi("2020-06")
        short = b.rolling(panel(3, cut=cut_at))
        by = {(r["k"], r["t"], r["h"]): r for r in full}
        checked = 0
        for r in short:
            f = by[(r["k"], r["t"], r["h"])]
            np.testing.assert_allclose(f["q"], r["q"], rtol=1e-6, atol=1e-8)
            np.testing.assert_allclose(f["u"], r["u"], rtol=1e-9)
            checked += 1
        self.assertGreater(checked, 300)

    def test_later_data_cannot_change_the_early_fold_score(self):
        P = panel(3)
        base = {r_key: b.score([r for r in b.rolling(P) if r["k"] == r_key[0] and r["h"] == r_key[1] and r["t"] <= b.mi(b.SELECT_END)])
                for r_key in [("AR0", 12)]}
        rng = np.random.default_rng(5)
        Q = {}
        for k, s in P.items():
            real = dict(s["real"])
            for m in real:
                if m >= b.mi(b.SELECT_END) + 13:      # after every early-fold target
                    real[m] *= float(np.exp(rng.normal(0, 0.5)))
            Q[k] = b.prep(real)
        pert = {r_key: b.score([r for r in b.rolling(Q) if r["k"] == r_key[0] and r["h"] == r_key[1] and r["t"] <= b.mi(b.SELECT_END)])
                for r_key in [("AR0", 12)]}
        for key, v in base.items():
            for f, x in v.items():
                self.assertAlmostEqual(x, pert[key][f], places=6, msg=f)


def _sel(**kw):
    s = {"n": 60, "model_p10": 1.0, "model_p90": 1.0, "uncond_p10": 1.2, "uncond_p90": 1.2,
         "calmonth_p10": 1.1, "calmonth_p90": 1.1, "coverage": 0.8}
    s.update(kw)
    return s


WIN_OK = {"2015-16": {"n": 13, "coverage": 0.85}, "2023-24": {"n": 13, "coverage": 0.8}}


class Gate(unittest.TestCase):
    def test_passes(self):
        self.assertEqual(b.gate(_sel(), WIN_OK), (True, "passes"))

    def test_each_condition_fails_on_its_own(self):
        for fld in ("model_p10", "model_p90"):
            for base in ("uncond", "calmonth"):
                ok, why = b.gate(_sel(**{fld: 1.15 if base == "uncond" else 1.05 if fld == "model_p10" else 1.15,
                                         f"{base}_{fld[-3:]}": 1.0}), WIN_OK)
                self.assertFalse(ok, (fld, base))
                self.assertIn("baseline", why)
        self.assertFalse(b.gate(_sel(coverage=0.65), WIN_OK)[0])
        self.assertFalse(b.gate(_sel(coverage=0.93), WIN_OK)[0])
        self.assertTrue(b.gate(_sel(coverage=0.70), WIN_OK)[0])
        self.assertTrue(b.gate(_sel(coverage=0.90), WIN_OK)[0])

    def test_equal_to_a_baseline_is_not_beating_it(self):
        self.assertFalse(b.gate(_sel(model_p10=1.2), WIN_OK)[0])

    def test_too_little_history(self):
        ok, why = b.gate(_sel(n=b.MIN_SELECT_POINTS - 1), WIN_OK)
        self.assertFalse(ok)
        self.assertIn("too little history", why)
        self.assertFalse(b.gate(None, WIN_OK)[0])

    def test_window_hide_rule(self):
        bad = {"2015-16": {"n": 13, "coverage": 0.4}, "2023-24": {"n": 13, "coverage": 0.8}}
        ok, why = b.gate(_sel(), bad)
        self.assertFalse(ok)
        self.assertIn("El Niño windows", why)
        # a window with too few months to judge is not held against the series
        thin = {"2015-16": {"n": 3, "coverage": 0.0}, "2023-24": {"n": 0, "coverage": None}}
        self.assertTrue(b.gate(_sel(), thin)[0])


@unittest.skipIf(np is None, "numpy not installed")
class MeanReversionFixture(unittest.TestCase):
    def test_reverting_series_pass_and_beat_the_baselines_on_the_display_folds(self):
        P = panel(5)
        recs = b.rolling(P)
        passed = 0
        for k in P:
            mine = [r for r in recs if r["k"] == k and r["h"] == 12]
            ok, _ = b.gate(b.score([r for r in mine if r["t"] <= b.mi(b.SELECT_END)]), b.window_cover(mine))
            passed += ok
        self.assertGreaterEqual(passed, 3)   # of 5: the early folds are 7 years, so the gate is noisy per series
        disp = b.score([r for r in recs if r["h"] == 12 and r["t"] >= b.mi(b.DISPLAY_FROM)])
        self.assertLess(disp["model_p90"], disp["uncond_p90"])
        self.assertLess(disp["model_p10"], disp["uncond_p10"])
        self.assertTrue(0.6 < disp["coverage"] < 0.95, disp["coverage"])

    def test_random_walks_do_not_beat_the_unconditional_baseline_pooled(self):
        recs = b.rolling(panel(0, n_rw=4))
        s = b.score([r for r in recs if r["h"] == 12])
        self.assertGreater(s["model_p10"] / s["uncond_p10"], 0.98)

    def test_quantiles_never_cross(self):
        for r in b.rolling(panel(2)):
            self.assertTrue(r["q"][0] <= r["q"][1] <= r["q"][2])


@unittest.skipIf(np is None, "numpy not installed")
class OniVerdict(unittest.TestCase):
    def _recs(self, gain):
        """Two record sets on the same points; the ONI set's p10/p90 are closer to the truth by `gain`."""
        base, with_ = [], []
        rng = np.random.default_rng(3)
        for t in range(b.mi("2012-01"), b.mi("2024-01")):
            for k in ("A", "B", "C"):
                y = float(rng.normal(0, 0.2))
                lo, hi = -0.26, 0.26
                base.append({"k": k, "t": t, "h": 12, "y": y, "q": [lo, 0.0, hi]})
                with_.append({"k": k, "t": t, "h": 12, "y": y, "q": [lo * (1 - gain), 0.0, hi * (1 - gain)]})
        return base, with_

    def test_no_gain_is_adds_nothing(self):
        base, w = self._recs(0.0)
        oni = {m: 0.0 for m in range(M0, M0 + 330)}
        r = b.oni_test(base, w, oni)
        self.assertEqual(r["verdict"], "adds_nothing")
        self.assertFalse(r["adds_value"])

    def test_a_clear_gain_is_reported(self):
        base, w = self._recs(0.0)
        # give the "with ONI" set exact quantiles: its pinball loss must be lower
        for a, c in zip(base, w):
            c["q"] = [c["y"] - 0.01, c["y"], c["y"] + 0.01]
        oni = {m: 0.0 for m in range(M0, M0 + 330)}
        self.assertEqual(b.oni_test(base, w, oni)["verdict"], "improves")

    def test_oni_monthly_centres_the_season(self):
        m = b.oni_monthly([{"season": "DJF", "year": 2024, "anom": 1.8}, {"season": "NDJ", "year": 2023, "anom": 1.9}])
        self.assertEqual(m[2024 * 12 + 0], 1.8)     # DJF is centred on January
        self.assertEqual(m[2023 * 12 + 11], 1.9)    # NDJ on December


class Decomposition(unittest.TestCase):
    @staticmethod
    def _dps(months):
        """months: {ym: (fx, usd price, cpi)} -> FPMA-shaped datapoints with price = usd * fx, real = price / cpi."""
        out = []
        for ym, (fx, usd, cpi) in months.items():
            p = usd * fx
            out.append({"date": ym + "-01", "price_value": p, "price_value_real": p / cpi, "price_value_dollar": usd})
        return out

    def test_parts_add_up_and_pin_the_known_case(self):
        a, e = b.mi("2026-03"), b.mi("2026-08")
        dps = self._dps({"2026-03": (10.0, 200.0, 1.00), "2026-08": (11.0, 220.0, 1.0)})
        world = {a: 300.0, e: 315.0}     # +5%
        d = b.decompose(dps, world)
        self.assertAlmostEqual(d["total_pct"], (1.1 * 1.1 - 1) * 100, places=1)
        self.assertAlmostEqual(d["world_log"], math.log(1.05), places=3)
        self.assertAlmostEqual(d["fx_log"], math.log(1.1), places=3)
        self.assertAlmostEqual(d["local_log"], math.log(1.1) - math.log(1.05), places=3)
        self.assertAlmostEqual(d["world_pct"] + d["fx_pct"] + d["local_pct"], d["total_pct"], delta=0.11)

    def test_inflation_is_taken_out_of_the_exchange_rate_part(self):
        # currency weakens 10% but local prices level rises 10%: no real exchange-rate move, and no real price move
        dps = self._dps({"2026-03": (10.0, 200.0, 1.0), "2026-08": (11.0, 200.0, 1.1)})
        world = {b.mi("2026-03"): 300.0, b.mi("2026-08"): 300.0}
        d = b.decompose(dps, world)
        self.assertAlmostEqual(d["fx_pct"], 0.0, delta=0.1)
        self.assertAlmostEqual(d["total_pct"], 0.0, delta=0.1)

    def test_returns_none_without_the_march_or_world_price(self):
        dps = self._dps({"2026-04": (10.0, 200.0, 1.0), "2026-08": (11.0, 210.0, 1.0)})
        self.assertIsNone(b.decompose(dps, {b.mi("2026-03"): 1.0, b.mi("2026-08"): 1.0}))
        dps = self._dps({"2026-03": (10.0, 200.0, 1.0), "2026-08": (11.0, 210.0, 1.0)})
        self.assertIsNone(b.decompose(dps, {b.mi("2026-08"): 1.0}))

    def test_world_price_ends_where_the_pink_sheet_ends(self):
        dps = self._dps({"2026-03": (10.0, 200.0, 1.0), "2026-07": (10.0, 210.0, 1.0), "2026-08": (10.0, 250.0, 1.0)})
        d = b.decompose(dps, {b.mi("2026-03"): 100.0, b.mi("2026-07"): 100.0})
        self.assertEqual(d["to"], "2026-07")

    def test_shapley_sums_exactly(self):
        p = {"world": 0.10, "fx": -0.25, "local": 0.4}
        s = b.shapley_pct(p)
        self.assertAlmostEqual(sum(s.values()), (math.exp(0.25) - 1) * 100, places=9)


@unittest.skipIf(np is None, "numpy not installed")
class LogAndChecks(unittest.TestCase):
    def _entry(self, day, p10=-10.0, p90=20.0):
        return {"run_date": day, "spec_hash": b.SPEC_HASH,
                "series": {"X:m": {"latest_month": "2026-06", "latest_real": 10.0,
                                   "h": {"3": {"target_month": "2026-09", "p10": p10, "p50": 0.0, "p90": p90, "pass": True}}}}}

    def test_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "log.json"
            first = self._entry("2026-09-30")
            path.write_text(json.dumps({"data": {"entries": b.append_log(first, path)}}))
            # same run date again with different numbers: the first entry stays
            again = self._entry("2026-09-30", p10=-99.0)
            es = b.append_log(again, path)
            self.assertEqual(es, [first])
            # a new date is appended after it, the old entry untouched
            path.write_text(json.dumps({"data": {"entries": es}}))
            es = b.append_log(self._entry("2026-10-01"), path)
            self.assertEqual([e["run_date"] for e in es], ["2026-09-30", "2026-10-01"])
            self.assertEqual(es[0], first)

    def test_checks_compare_with_the_price_that_arrived(self):
        e = self._entry("2026-09-30")
        lm, tm = b.mi("2026-06"), b.mi("2026-09")
        rows = b.check_log([e], {"X:m": {lm: 10.0, tm: 11.0}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["realised_pct"], 10.0)
        self.assertTrue(rows[0]["inside"])
        rows = b.check_log([e], {"X:m": {lm: 10.0, tm: 13.0}})
        self.assertFalse(rows[0]["inside"])
        self.assertEqual(b.check_log([e], {"X:m": {lm: 10.0}}), [])     # the target month has not arrived

    def test_spec_hash_moves_with_the_spec(self):
        import hashlib
        other = dict(b.SPEC, taus=[0.05, 0.5, 0.95])
        self.assertNotEqual(hashlib.sha256(json.dumps(other, sort_keys=True).encode()).hexdigest()[:16], b.SPEC_HASH)


@unittest.skipIf(np is None, "numpy not installed")
class DataFixes(unittest.TestCase):
    @staticmethod
    def _series(iso, n=100):
        dps = [{"date": f"{2018 + (i // 12)}-{i % 12 + 1:02d}-01", "price_value": 10.0 + i, "price_value_real": 10.0 + i,
                "price_value_dollar": 1.0} for i in range(n)]
        return {"key": f"{iso}:m", "iso3": iso, "country": iso, "commodity": "m", "staple": "maize", "market": "x",
                "price_type": "retail", "datapoints": dps}

    def test_last_three_months_of_mdg_and_eth_are_dropped(self):
        out = b.build([self._series("MDG"), self._series("ETH"), self._series("ZAF")], {}, {})
        by = {e["key"]: e for e in out["series"]}
        last = "2026-04"        # 100 months from 2018-01
        self.assertEqual(by["ZAF:m"]["latest_month"], last)
        for iso in ("MDG", "ETH"):
            self.assertEqual(by[f"{iso}:m"]["latest_month"], "2026-01")
            self.assertEqual(by[f"{iso}:m"]["dropped_tail"], ["2026-02", "2026-03", "2026-04"])
        self.assertEqual(by["ZAF:m"]["dropped_tail"], [])

    def test_a_long_gap_is_flagged(self):
        s = self._series("BRA", 110)
        s["datapoints"] = [d for i, d in enumerate(s["datapoints"]) if not 30 <= i < 78]
        out = b.build([s], {}, {})
        self.assertEqual(out["series"][0]["gap"]["months"], 48)

    def test_short_series_is_left_out_with_a_reason(self):
        out = b.build([self._series("XXX", 20)], {}, {})
        self.assertEqual(out["series"], [])
        self.assertIn("20 months", out["excluded"][0]["reason"])

    def test_outlook_builder_uses_the_same_trim_and_gap_rules(self):
        import build_enso_price_outlook as bo
        self.assertEqual(bo.TAIL_TRIM, {"MDG": 3, "ETH": 3})
        real = {m: 1.0 for m in list(range(100, 130)) + list(range(200, 230))}
        g = bo._gap(real)
        self.assertEqual(g["months"], 70)
        self.assertIsNone(bo._gap({m: 1.0 for m in range(100, 130)}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
