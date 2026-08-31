"""Scoring primitives for scenario-engine validation (v47).

WHY THIS EXISTS:
  The scenario/shock engine has never been validated — not once, against
  anything. The structural FDRS has concurrent validation (ROC-AUC 0.88 vs
  IPC); the shock engine has nothing. data/scenario_spec.json carries 12
  magnitude constants, ALL of them `"calibrated": false`, and its own
  _meta.honesty concedes the cardinal sizes are "a reasoned modeling choice".
  That is why this module exists before any formula is touched: an
  uncalibrated engine cannot be improved, because "improved" is unmeasurable.

WHAT THIS CAN AND CANNOT ESTABLISH:
  There are roughly 3-6 usable historical episodes. That sample can establish
  SET MEMBERSHIP and RANK — did the engine name the countries actually drawn
  in — and cannot establish MAGNITUDE accuracy. A design court of an applied
  mathematician, a trade economist and an adversarial reviewer reached that
  conclusion independently, so there is deliberately NO magnitude score
  anywhere in this file. Do not add one.

THE NULL MODEL IS THE WHOLE POINT:
  precision@10 alone is close to meaningless here. The trade network is dense
  and the same ~20 countries appear in every food crisis, so "name the biggest
  importers" scores respectably while knowing nothing. Every metric is
  therefore reported against a permutation null: shuffle the ranking 1,000
  times and report where the real score falls. A score that does not clear the
  95th percentile of that null has demonstrated nothing, however good it looks
  in isolation.

NEGATIVE FIXTURES MATTER MOST:
  The first-order model is estimated to overstate impact 2-4x because it
  ignores substitution and stocks. The episodes that expose that are the ones
  where a large exporter withdrew supply and NOTHING much followed — India
  2023 rice, COVID 2020, Indonesia palm oil 2022. An engine that fires hard on
  those is wrong in the exact direction it is already suspected of being
  wrong. Scoring only on crisis episodes would hide it.

NOT WIRED IN (as of 2026-08-31):
  Nothing imports this module. data/backtest_scenarios.json is produced by
  scripts/backtest_scenarios.py, which computes its own ranks and never calls
  anything here, so the figures the Scenario panel publishes carry NO
  permutation null and NO negative fixtures. Three of the five precedents in
  data/precedents.json — india_rice_2023, indonesia_palm_2022, gfpc_2007_08 —
  are exactly the negative and base-rate cases this docstring argues matter
  most, and none of them is scored anywhere. Read what follows as a
  specification, not as a description of what the published panel did.

Pure stdlib — no numpy, so it runs wherever the rest of the pipeline does.
"""
import random


def precision_at_k(ranked_isos, truth_set, k):
    """Share of the top-k predicted that are in the observed set."""
    if k <= 0 or not ranked_isos:
        return None
    top = ranked_isos[:k]
    if not top:
        return None
    return sum(1 for iso in top if iso in truth_set) / len(top)


def recall_at_k(ranked_isos, truth_set, k):
    """Share of the observed set captured in the top-k."""
    if not truth_set:
        return None
    top = set(ranked_isos[:k])
    return sum(1 for iso in truth_set if iso in top) / len(truth_set)


def _ranks(xs):
    """Tie-aware average ranks. Mirrors validate_fdrs.py so the two agree."""
    idx = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[idx[k]] = avg
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 3 or len(a) != len(b):
        return None
    ra, rb = _ranks(a), _ranks(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else None


def permutation_null(ranked_isos, truth_set, k, draws=1000, seed=0):
    """Where does the real precision@k sit against random orderings?

    Returns {p_at_k, null_mean, null_p95, percentile, beats_null}.

    `beats_null` is the only claim worth making from a handful of episodes: it
    says the ORDERING carries information the candidate pool does not already
    supply. It says nothing about magnitude, and nothing here should ever be
    read as saying so.
    """
    real = precision_at_k(ranked_isos, truth_set, k)
    if real is None:
        return None
    rng = random.Random(seed)          # seeded — a validation run must reproduce
    pool = list(ranked_isos)
    null_scores = []
    for _ in range(draws):
        rng.shuffle(pool)
        null_scores.append(precision_at_k(pool, truth_set, k))
    null_scores.sort()
    mean = sum(null_scores) / len(null_scores)
    p95 = null_scores[int(0.95 * (len(null_scores) - 1))]
    at_or_below = sum(1 for s in null_scores if s <= real)
    return {
        "p_at_k": round(real, 4),
        "null_mean": round(mean, 4),
        "null_p95": round(p95, 4),
        "percentile": round(at_or_below / len(null_scores), 4),
        "beats_null": bool(real > p95),
        "draws": draws,
    }


def score_episode(ranked_isos, truth_set, k=10, draws=1000, seed=0):
    """Full scorecard for one POSITIVE episode (something did happen).

    ranked_isos: engine output, most-affected first, ISO3.
    truth_set:   observed affected/responding countries, ISO3.
    """
    truth = set(truth_set or ())
    return {
        "n_ranked": len(ranked_isos),
        "n_truth": len(truth),
        # Split reachability from ranking quality. If a country is absent from
        # the engine's universe, no ordering can surface it — that is a data
        # coverage failure, not a model failure, and conflating the two would
        # let one hide behind the other.
        "truth_reachable": len(truth & set(ranked_isos)),
        "truth_unreachable": sorted(truth - set(ranked_isos)),
        "precision_at_5": precision_at_k(ranked_isos, truth, 5),
        "precision_at_10": precision_at_k(ranked_isos, truth, k),
        "recall_at_10": recall_at_k(ranked_isos, truth, k),
        "null_test": permutation_null(ranked_isos, truth, k, draws=draws, seed=seed),
    }


def score_negative_episode(ranked_isos, coped_set, magnitude_by_iso=None, k=10):
    """Scorecard for a NEGATIVE episode — a shock that did NOT cascade.

    These are the fixtures that catch an over-firing model, so they are scored
    on the opposite question: how loudly did the engine shout about countries
    that turned out fine?

    `false_alarm_at_k` is the share of the top-k that are in the observed
    coped-anyway set. HIGH is BAD here. There is no null test — the meaningful
    comparison is against the positive episodes' behaviour, not against chance.
    """
    coped = set(coped_set or ())
    top = ranked_isos[:k]
    fa = (sum(1 for iso in top if iso in coped) / len(top)) if top else None
    out = {
        "n_ranked": len(ranked_isos),
        "n_coped": len(coped),
        "false_alarm_at_10": fa,
        "coped_in_top10": sorted(iso for iso in top if iso in coped),
    }
    if magnitude_by_iso:
        vals = [magnitude_by_iso.get(iso) for iso in coped if iso in magnitude_by_iso]
        vals = [v for v in vals if isinstance(v, (int, float))]
        if vals:
            # How big a delta did the engine assign to countries that were
            # fine? On a negative episode this should be small; if it is not,
            # the 2-4x overstatement is showing up exactly where predicted.
            out["mean_delta_on_coped"] = round(sum(vals) / len(vals), 2)
            out["max_delta_on_coped"] = round(max(vals), 2)
    return out
