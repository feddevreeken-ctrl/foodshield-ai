#!/usr/bin/env python3
"""Do the derived El Niño files agree with their inputs and with each other? (stdlib)

Fails (exit 1, FAIL lines) when
  1. a derived file is older than an input it was built from, or an input was rebuilt after the file
     recorded it (`_meta.inputs`, or `data.inputs` in enso_changes.json);
  2. two files disagree on a shared fact: Panama advisory id, draft, slot counts (lanes, gauges, replacement,
     changes); the ONI scenario (outlook, distribution, replacement); the neutral-baseline vintage (neutral
     against the outlook); the set of pairs (outlook against distribution, hindcast, portfolio);
  3. a file was assembled from cron feeds more than WINDOW_DAYS apart, or is marked hand-run with no review date.
Warns (never fails) when a hand-run or curated file is past its review date.

  python3 scripts/check_consistency.py            exit 1 on any FAIL
  python3 scripts/check_consistency.py --soft     print, always exit 0 (run_all uses this, loudly)
Tests call `check_all(reader)` with an in-memory reader to prove a broken copy fails.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_dag as dag  # noqa: E402

TOL_S = 2.0   # a file stamps itself at the end of its build; inputs are older by at least that


def _ts(reader, name):
    doc = reader(name)
    m = dag.meta_of(doc)
    return dag.parse_ts(m.get("generated_at") or m.get("generated") or m.get("loaded_at") or m.get("fetched_at"))


def _recorded(doc) -> dict:
    rec = dict(dag.meta_of(doc).get("inputs") or {})
    if not rec:
        rec = dict((dag.body_of(doc) or {}).get("inputs") or {}) if isinstance(dag.body_of(doc), dict) else {}
    return {k: v for k, v in rec.items() if isinstance(v, str)}


def panama_state(lanes_doc, today: str) -> dict:
    """Current Panama state from the curated lane file: newest advisory, draft, step in force today, next scheduled step.
    A step keyed to a future date is not 'now'. Shared with build_enso_replacement."""
    lane = next((l for l in dag.body_of(lanes_doc).get("lanes", []) if l.get("id") == "panama"), None)
    live = (lane or {}).get("live_2026") or {}
    steps = sorted((s for s in live.get("steps", []) if s.get("total")), key=lambda s: s["effective"])
    now_step = next((s for s in reversed(steps) if s["effective"] <= today), steps[0] if steps else None)
    nxt = next((s for s in steps if s["effective"] > today), None)
    latest = live.get("latest_advisory") or {}
    m = re.search(r"\((\d{2}(?:\.\d)?)\s*ft\)", live.get("draft") or "")
    return {"advisory": latest.get("advisory") or (live.get("draft_advisory") or {}).get("advisory") or live.get("advisory"),
            "advisory_date": latest.get("advisory_date"), "draft_ft": float(m.group(1)) if m else None,
            "steps": steps, "in_force": now_step, "next": nxt, "deficit_advisory": live.get("advisory")}


def check_all(reader=dag.read, now: datetime | None = None) -> list[tuple[str, str, str]]:
    """Returns [(level, code, message)], level FAIL or WARN."""
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    out: list[tuple[str, str, str]] = []

    def fail(code, msg):
        out.append(("FAIL", code, msg))

    def warn(code, msg):
        out.append(("WARN", code, msg))

    # 1 + 3: order, recorded stamps, window, hand-run marking
    for name, node in dag.NODES.items():
        doc = reader(name)
        if doc is None:
            continue
        meta = dag.meta_of(doc)
        mine = _ts(reader, name)
        rec = _recorded(doc)
        hand = node["mode"] == dag.HAND
        if hand or meta.get("hand_run"):
            if not meta.get("next_review_due"):
                fail("hand_run_no_review", f"{name} is hand-run but has no _meta.next_review_due")
            elif meta["next_review_due"] < today:
                warn("review_overdue", f"{name} hand-run review was due {meta['next_review_due']}")
        if not mine:
            fail("no_generated_at", f"{name} has no _meta.generated_at")
            continue
        cron_inputs = []
        for inp in node.get("inputs") or []:
            its = _ts(reader, inp)
            if not its:
                continue
            if not hand and (mine - its).total_seconds() < -TOL_S:
                fail("older_than_input", f"{name} ({mine.isoformat()[:19]}) is older than its input {inp} ({its.isoformat()[:19]})")
            r = dag.parse_ts(rec.get(inp))
            if r and abs((r - its).total_seconds()) > TOL_S:
                fail("input_rebuilt", f"{name} recorded {inp} as of {r.isoformat()[:19]} but it is now {its.isoformat()[:19]}")
            if not dag.is_hand_kept(inp, dag.meta_of(reader(inp))):
                cron_inputs.append((inp, its))
        if cron_inputs and not hand:
            oldest = min(cron_inputs, key=lambda x: x[1])
            gap = (mine - oldest[1]).total_seconds() / 86400
            if gap > dag.WINDOW_DAYS:
                fail("window", f"{name} was built {gap:.1f} days after its oldest feed {oldest[0]} (window {dag.WINDOW_DAYS} days) and is not marked hand-run")
    for name, days in dag.CURATED.items():
        ts = _ts(reader, name)
        if ts and (now - ts).days > days:
            warn("review_overdue", f"{name} is curated by hand, read {ts.date()}, review window {days} days")

    # 2a: Panama
    lanes, gauges, repl, chg = reader("enso_lanes.json"), reader("enso_gauges.json"), reader("enso_replacement.json"), reader("enso_changes.json")
    if lanes and gauges and repl:
        ps = panama_state(lanes, today)
        g = (dag.body_of(gauges).get("acp_advisories") or {}).get("latest") or {}
        gf = (g.get("extracted") or {}).get("fields") or {}
        gv = lambda k: (gf.get(k) or {}).get("value")
        rp = dag.body_of(repl).get("panama") or {}
        if g.get("id") and ps["advisory"] != g["id"]:
            fail("panama_advisory", f"lanes say the newest Panama advisory is {ps['advisory']}, gauges.acp_advisories say {g['id']}")
        if rp.get("advisory") != ps["advisory"]:
            fail("panama_advisory", f"replacement used Panama advisory {rp.get('advisory')}, lanes say {ps['advisory']}")
        if gv("draft_ft") is not None and ps["draft_ft"] is not None and abs(gv("draft_ft") - ps["draft_ft"]) > 1e-6:
            fail("panama_draft", f"lanes draft {ps['draft_ft']} ft, gauges {gv('draft_ft')} ft")
        if rp.get("draft_ft") != ps["draft_ft"]:
            fail("panama_draft", f"replacement draft {rp.get('draft_ft')} ft, lanes {ps['draft_ft']} ft")
        now_slots = (ps["in_force"] or {}).get("total")
        if rp.get("slots_now") != now_slots:
            fail("panama_slots", f"replacement uses {rp.get('slots_now')} slots now, lanes step in force on {today} is {now_slots}")
        nxt = ps["next"]
        if (rp.get("slots_next"), rp.get("next_from")) != ((nxt or {}).get("total"), (nxt or {}).get("effective")):
            fail("panama_slots", f"replacement next step {rp.get('slots_next')} from {rp.get('next_from')}, lanes {(nxt or {}).get('total')} from {(nxt or {}).get('effective')}")
        gtot, gfrom = gv("total_slots"), gv("first_booking_date")
        if gtot is not None and gfrom:
            lane_at = next((s for s in reversed(ps["steps"]) if s["effective"] <= gfrom), None)
            if (lane_at or {}).get("total") != gtot:
                fail("panama_slots", f"gauges read {gtot} slots a day from {gfrom}, lanes have {(lane_at or {}).get('total')} then")
    if lanes and chg:
        ps = panama_state(lanes, today)
        v = None
        snaps = (dag.body_of(reader("enso_snapshots.json")) or {}).get("snapshots") or []
        if snaps:
            v = snaps[-1].get("v") or {}
        if v:
            a = (v.get("acp_latest") or {}).get("id")
            g = ((dag.body_of(gauges).get("acp_advisories") or {}).get("latest") or {}).get("id") if gauges else None
            if a and g and a != g:
                fail("panama_advisory", f"what-changed snapshot has ACP advisory {a}, gauges {g}")
            cs = (v.get("acp_latest") or {}).get("curated_seen")
            if cs and ps["advisory"] and cs != ps["advisory"]:
                fail("panama_advisory", f"what-changed snapshot saw curated advisory {cs}, lanes say {ps['advisory']}")

    # 2b: ONI scenario
    outl, dist = reader("enso_outlook.json"), reader("enso_distribution.json")
    if outl:
        O = dag.body_of(outl)
        case = O.get("who_pays_case", "record")
        o_oni = ((O.get("cases") or {}).get(case) or {}).get("oni")
        rec_oni = ((O.get("cases") or {}).get("record") or {}).get("oni")
        if dist and dag.body_of(dist).get("target", {}).get("record_oni") != rec_oni:
            fail("oni_scenario", f"distribution record ONI {dag.body_of(dist).get('target', {}).get('record_oni')}, outlook {rec_oni}")
        if repl:
            R = dag.body_of(repl)
            if R.get("oni") != o_oni or R.get("case") != case:
                fail("oni_scenario", f"replacement case {R.get('case')} ONI {R.get('oni')}, outlook {case} ONI {o_oni}")
            if R.get("harvest_winter") != O.get("harvest_winter"):
                fail("harvest_winter", f"replacement harvest winter {R.get('harvest_winter')}, outlook {O.get('harvest_winter')}")

        # 2c: neutral baseline vintage
        neu = reader("enso_neutral.json")
        if neu:
            n_ts = dag.meta_of(neu).get("generated_at")
            if O.get("neutral_generated_at") != n_ts:
                fail("neutral_vintage", f"outlook rows use the neutral baseline of {O.get('neutral_generated_at')}, enso_neutral.json is {n_ts}")
            pairs = dag.body_of(neu).get("pairs") or {}
            miss = [f"{r['iso']}/{r['crop']}" for r in O.get("rows_all", []) if r.get("baseline") == "neutral" and f"{r['iso']}/{r['crop']}" not in pairs]
            if miss:
                fail("neutral_vintage", f"outlook rows on a neutral baseline with no neutral pair: {miss[:5]}")

        # 2d: pair sets
        shown = {f"{r['iso']}/{r['crop']}": r for r in O.get("rows_all", []) if r.get("status") == "shown"}
        for name, getter in (("enso_distribution.json", lambda b: {p["key"] for p in b.get("pairs", [])}),
                             ("enso_hindcast.json", lambda b: set((b.get("pairs") or {}).keys()))):
            doc = reader(name)
            if doc:
                have = getter(dag.body_of(doc))
                if have != set(shown):
                    fail("pair_set", f"{name} covers {sorted(have ^ set(shown))[:6]} differently from the outlook's shown pairs")
        if dist:
            for p in dag.body_of(dist).get("pairs", []):
                r = shown.get(p["key"])
                if r and p.get("production_kt") != r.get("production_kt"):
                    fail("distribution_baseline", f"distribution {p['key']} production {p.get('production_kt')} kt, outlook {r.get('production_kt')} kt")
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    soft = "--soft" in argv
    res = check_all()
    fails = [r for r in res if r[0] == "FAIL"]
    for lvl, code, msg in res:
        print(f"[{lvl}] {code}: {msg}")
    a = dag.data_as_of()
    if a.get("newest"):
        print(f"[INFO] data as of {a['newest'][:16]} UTC (newest: {a['newest_file']}); oldest cron feed {a['oldest'][:16]} ({a['oldest_file']}); "
              f"spread {a['spread_days']} d, window {a['window_days']} d" + (" WIDE" if a["wide"] else ""))
    if a.get("wide"):
        print(f"[WARN] stamp_spread: cron feeds are {a['spread_days']} days apart (window {a['window_days']})")
    print(f"[{'FAIL' if fails else 'OK'}] consistency: {len(fails)} failure(s), {len(res) - len(fails)} warning(s)")
    return 0 if soft else (1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
