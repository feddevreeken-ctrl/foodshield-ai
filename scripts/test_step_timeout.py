#!/usr/bin/env python3
"""Regression gate for safe_run's per-feed wall-clock cap.

WHY THIS FILE EXISTS
--------------------
On 2026-08-03 the scheduled refresh stopped committing. The site went stale for
days — news included — and every run ended as a failure email with no feed named
as the culprit.

The cause was not a broken upstream. `_StepTimeout` inherited from `Exception`,
and collectors legitimately guard their per-item loops with
`except Exception: continue` (refresh_wfp_country iterates 195 ISO3 codes;
territories whose endpoint 404s are normal and must not abort the whole feed).
So SIGALRM fired exactly on schedule, the collector's own error handling
swallowed it, and the loop continued to the next country — where the next alarm
was swallowed the same way. Observed locally: a feed still running 13 minutes
into a 300-second cap.

The cap was decorative. It had never stopped a feed, and nothing detected that,
because "no timeout fired" and "timeout fired and got eaten" look identical from
outside. The pipeline then overran the job ceiling and was killed before
validate / QA / commit ever ran.

The fix: `_StepTimeout` derives from BaseException, so it passes straight
through `except Exception` and reaches safe_run's explicit handler.

This test pins that behaviour. It deliberately does NOT just assert
`safe_run(sleep) is False` — that weaker test would have passed throughout the
entire outage. It reproduces the swallowing collector that made the bug
invisible.

Run:  python test_step_timeout.py    (exit 0 = pass, 1 = fail)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import _StepTimeout, safe_run

CAP = 3          # seconds — short enough for CI, long enough to be unambiguous
TOLERANCE = 5.0  # allow scheduler jitter on a loaded runner
# How far past the cap the simulated feed keeps going. Must exceed TOLERANCE so
# a regressed _StepTimeout produces a clear FAIL, and must stay small so the
# gate finishes in seconds either way.
FAIL_FAST_MULTIPLIER = 4


def _feed_that_swallows_exceptions():
    """The exact shape that defeated the old cap.

    A loop whose body is wrapped in `except Exception: continue` — the idiom
    every per-country collector uses so one dead endpoint cannot kill the whole
    feed. Under the old Exception-based _StepTimeout the alarm was caught here
    and the loop simply carried on.

    Bounded by WALL CLOCK, not iteration count, and deliberately so: when the
    bug is present this must FAIL FAST rather than hang. An earlier version
    looped 100,000 times, which meant a regressed _StepTimeout made this gate
    run for ~5,000 seconds — the gate would have consumed the very CI job it
    exists to protect, reporting nothing. A test for a hang must not hang.
    """
    deadline = time.time() + (CAP * FAIL_FAST_MULTIPLIER)
    while time.time() < deadline:
        try:
            time.sleep(0.05)
        except Exception:
            continue
    return "should never be reached"


def main():
    failures = 0
    checks = 0
    print("=== safe_run wall-clock cap regression ===\n")

    # 1. The class must sit outside Exception, or collector error handling eats it.
    checks += 1
    if issubclass(_StepTimeout, Exception):
        print("  [FAIL] _StepTimeout inherits Exception — collector "
              "`except Exception` blocks will swallow the cap. This is the "
              "2026-08 outage: the timeout fires and nothing stops.")
        failures += 1
    else:
        print(f"  [ok  ] _StepTimeout base is {_StepTimeout.__mro__[1].__name__}, "
              f"outside `except Exception`")

    # 2. The cap must actually stop a feed that swallows Exception.
    checks += 1
    started = time.time()
    ok = safe_run("swallowing feed (regression)", _feed_that_swallows_exceptions,
                  output_name=None, timeout=CAP)
    elapsed = time.time() - started
    if ok:
        print("  [FAIL] safe_run reported success on a feed that never returns")
        failures += 1
    elif elapsed > TOLERANCE:
        print(f"  [FAIL] cap was {CAP}s but the feed ran {elapsed:.1f}s — "
              f"the timeout is being swallowed")
        failures += 1
    else:
        print(f"  [ok  ] swallowing feed stopped at {elapsed:.1f}s (cap {CAP}s), "
              f"safe_run returned False")

    # 3. A feed that finishes inside its cap must not be reported as a timeout.
    checks += 1
    started = time.time()
    ok = safe_run("fast feed (control)", lambda: "fine",
                  output_name=None, timeout=CAP)
    if not ok:
        print("  [FAIL] a fast feed was reported as failed — the cap fires early")
        failures += 1
    else:
        print(f"  [ok  ] fast feed returned cleanly in {time.time() - started:.2f}s")

    # 4. The alarm must be cleared afterwards, or a later step inherits it and
    #    dies partway through for no visible reason.
    checks += 1
    safe_run("cleanup check", lambda: "fine", output_name=None, timeout=CAP)
    time.sleep(CAP + 0.5)
    print("  [ok  ] no stray alarm fired after the step completed")

    print(f"\n{checks} checks, {failures} failed.")
    if failures:
        print("FAIL — the per-feed wall-clock cap is not enforceable. A hung "
              "upstream can consume the whole CI job again.")
        return 1
    print("PASS — hung feeds are bounded even when the collector swallows "
          "exceptions.")
    print("NOTE — a collector using a BARE `except:` can still swallow this; "
          "run_all.RUN_BUDGET_SECONDS is the backstop for that case.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
