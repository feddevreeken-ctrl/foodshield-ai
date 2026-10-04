#!/usr/bin/env python3
"""FoodShield: who replaces a lost export, and who is left short (scenario, not forecast).

For each exposed supplier-crop pair in data/enso_outlook.json `who_pays` (the scenario winter):
  lost exports -> each affected buyer (named buyers, plus the supplier's own extra import need)
  -> candidate replacement exporters -> tonnes replaced by source, tonnes left uncovered.

Pure stdlib, deterministic, about a second. Reads:
  enso_outlook.json   who_pays rows (loss, exports, buyers) and rows_all (which exporters lose their own harvest)
  enso_distribution   forecast quantiles of each pair's harvest change (response capped at the strongest fitted winter):
                      the same allocation is re-run at P10 / P50 / P90 of that forecast (mid headroom only), so a buyer's
                      shortfall carries a range, not only the one record-winter number
  tm/<ISO>.json       FAOSTAT detailed trade matrix: each buyer's usual suppliers of the crop (top eight)
  usda_psd.json       exporter normal exports and ending stocks, buyer stocks and consumption
  trade_restrictions  export bans in force
  enso_lanes.json     Panama Canal: newest advisory, draft, dated slot steps (the one in force today, the next scheduled) and the 2023 floor
  enso_gauges.json    newest advisory id on the Canal Authority's own list (cross-check only)
  enso_freight.json   US Gulf ocean freight, latest against its own baseline (a cost signal only)

Nothing here is typed in by hand except the rule parameters in PARAMS, all echoed into _meta.
The allocation is a proportional-rationing fill, not a cost optimiser: no freight price is invented.
"""
from __future__ import annotations

import json
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PSD_KEY = {"corn": "corn", "wheat": "wheat", "rice": "rice", "soybeans": "soybeans",
           "sorghum": "sorghum", "barley": "barley", "millet": "millet"}
TM_KEY = {"corn": "maize"}
CROP_RX = {"corn": re.compile(r"maize|corn", re.I)}

PARAMS = {
    # Extra tonnes an exporter can ship = STOCK_SHARE x ending stocks + SLACK x its normal exports.
    "sensitivity": [
        {"key": "low", "stock_share": 0.25, "slack_share": 0.0, "panama": "floor_2023"},
        {"key": "mid", "stock_share": 0.50, "slack_share": 0.10, "panama": "latest_advisory"},
        {"key": "high", "stock_share": 1.00, "slack_share": 0.20, "panama": "latest_advisory"},
    ],
    "usual_ramp_x": 3.0,          # scope 'usual': a supplier ships at most 3x its normal volume to that buyer
    "min_usual_share_pct": 1.0,   # a usual supplier: at least 1% of the buyer's imports ...
    "min_usual_t": 10000,         # ... and 10 kt, the floor the Harvests map already uses
    "min_exporter_kt": 1000,      # open-market pool: normal exports of at least 1,000 kt (PSD), i.e. a major exporter
    "export_cap_x": 1.0,          # extra exports never exceed 100% of normal exports (doubling), whatever the stocks
    "stretch_share": 0.25,        # an exporter is "stretched" above +25% on its normal exports
}
# East and Southeast Asian buyers reached from the US Gulf through the Panama Canal (a geographic list, not a number).
PANAMA_ASIA = {"JPN", "KOR", "CHN", "TWN", "HKG", "VNM", "PHL", "THA", "MYS", "IDN", "SGP"}


def load(name):
    return json.loads((DATA / name).read_text())


def body(d):
    return d.get("data", d)


def crop_match(crop, text):
    rx = CROP_RX.get(crop) or re.compile(re.escape(crop), re.I)
    return bool(rx.search(text or ""))


def panama_factors(lanes, gauges, today):
    """Capacity factors for US Gulf -> Asia: slots against the canal's stated normal capacity.

    State comes from the curated lane file: `live_2026.latest_advisory` (the newest advisory a person has read),
    `draft`, and the dated `steps`. The step in force today is the last one whose `effective` date has passed; a step
    keyed to a future date is scheduled, not now, and is carried as slots_next / next_from. (`live_2026.advisory` is the
    older deficit advisory that opened the restrictions; it is not the current state.) The newest advisory id on the
    Canal Authority's own list (enso_gauges acp_advisories) is carried beside it so a stale curated module shows."""
    out = {"latest_advisory": 1.0, "floor_2023": 1.0, "note": "no lane data"}
    try:
        lane = [l for l in body(lanes)["lanes"] if l["id"] == "panama"][0]
        live = lane["live_2026"]
        normal = float(re.findall(r"\d+", live["capacity_vessels_day"])[0])      # '36-38' -> lower end, 36
        steps = sorted((s for s in live["steps"] if s.get("total")), key=lambda s: s["effective"])
        inforce = [s for s in steps if s["effective"] <= today]
        now_step = inforce[-1] if inforce else steps[0]
        nxt = next((s for s in steps if s["effective"] > today), None)
        now_slots = float(now_step["total"])
        floor = float(min(s["total"] for s in lane["precedent_2023"]["steps"]))
        la = live.get("latest_advisory") or {}
        m = re.search(r"\((\d{2}(?:\.\d)?)\s*ft\)", live.get("draft") or "")
        g = ((body(gauges).get("acp_advisories") or {}).get("latest") or {}) if gauges else {}
        out = {"latest_advisory": min(1.0, now_slots / normal), "floor_2023": min(1.0, floor / normal),
               "normal_slots": normal, "slots_now": now_slots, "slots_floor_2023": floor,
               "advisory": la.get("advisory") or (live.get("draft_advisory") or {}).get("advisory") or live.get("advisory"),
               "advisory_date": la.get("advisory_date"),
               "draft_ft": float(m.group(1)) if m else None,
               "step_in_force": {"effective": now_step["effective"], "total": now_step["total"], "neopanamax": now_step.get("neopanamax")},
               "slots_next": nxt["total"] if nxt else None, "next_from": nxt["effective"] if nxt else None,
               "acp_list_newest": g.get("id"),
               "note": "slots in force today / lower end of the canal's stated 36-38 a day; a later scheduled step is shown, not used"}
    except Exception as e:  # pragma: no cover
        out["note"] = f"lane data unreadable: {e}"
    return out


def freight_signal(freight):
    try:
        s = body(freight)["series"]["ocean_gulf_japan"]
        return {"series": s["label"], "unit": s["unit"], "latest": s["latest"], "baseline": s["baseline"]["value"],
                "baseline_label": s["baseline"].get("window_label"), "vs_baseline_pct": s.get("vs_baseline_pct")}
    except Exception:
        return None


def ration(demands, exporters, scope, sens, pan_factor):
    """Proportional rationing. demands: [{id, need, usual:{iso:t}}]; exporters: {iso:{cap}}.
    Stage 1 fills from each buyer's usual suppliers in proportion to their normal tonnes
    (scope 'usual' caps each at usual_ramp_x its normal tonnes to that buyer). Stage 2 (scope 'open')
    fills what is left from any other exporter with spare headroom, in proportion to that headroom.
    An exporter asked for more than it has is rationed pro rata across its askers. Returns
    {demand id: {iso: kt}} and leftover capacity."""
    cap = {i: e["cap"] for i, e in exporters.items()}
    got = {d["id"]: {} for d in demands}
    rem = {d["id"]: d["need"] for d in demands}
    ramp = PARAMS["usual_ramp_x"]

    def route_w(d, iso):
        return pan_factor if (iso == "USA" and d["buyer"] in PANAMA_ASIA) else 1.0

    def round_(stage):
        moved = 0.0
        asks = {}
        for d in demands:
            r = rem[d["id"]]
            if r <= 1e-9:
                continue
            if stage == 1:
                pool = {i: t for i, t in d["usual"].items() if i in cap and cap[i] > 1e-9}
                if scope == "usual":
                    pool = {i: t for i, t in pool.items() if got[d["id"]].get(i, 0) < ramp * t / 1000 - 1e-9}
                w = {i: t * route_w(d, i) for i, t in pool.items()}
            else:
                w = {i: cap[i] * route_w(d, i) for i in cap if i not in d["usual"] and cap[i] > 1e-9}
            tot = sum(w.values())
            if tot <= 0:
                continue
            for i, wi in w.items():
                a = r * wi / tot
                if stage == 1 and scope == "usual":
                    a = min(a, ramp * d["usual"][i] / 1000 - got[d["id"]].get(i, 0))
                if a > 1e-12:
                    asks.setdefault(i, []).append((d["id"], a))
        for i, lst in asks.items():
            total = sum(a for _, a in lst)
            k = min(1.0, cap[i] / total) if total > 0 else 0.0
            for did, a in lst:
                x = a * k
                got[did][i] = got[did].get(i, 0) + x
                rem[did] -= x
                cap[i] -= x
                moved += x
        return moved

    for stage in ([1, 2] if scope == "open" else [1]):
        for _ in range(200):
            if round_(stage) < 1e-6:
                break
    return got, cap


def main() -> int:
    O = body(load("enso_outlook.json"))
    try:
        DIST = {p["key"]: p for p in body(load("enso_distribution.json"))["pairs"]}
    except Exception:
        DIST = {}
    psd = body(load("usda_psd.json"))
    restr = body(load("trade_restrictions.json"))
    pubfx = body(load("enso_published_effects.json"))
    lanes, freight, gauges = load("enso_lanes.json"), load("enso_freight.json"), load("enso_gauges.json")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pan = panama_factors(lanes, gauges, today)
    fr = freight_signal(freight)
    case = O.get("who_pays_case", "record")
    oni = (O.get("cases") or {}).get(case, {}).get("oni")
    rows = O.get("rows_all") or []
    tm_cache = {}

    def tm(iso):
        if iso not in tm_cache:
            p = DATA / "tm" / f"{iso}.json"
            tm_cache[iso] = body(json.loads(p.read_text())) if p.exists() else None
        return tm_cache[iso]

    def why_out(iso, crop, supplier):
        """Why an exporter cannot be counted on, or None. Same tests as the Harvests map."""
        if iso == supplier:
            return "it is the short supplier"
        if any(r["iso"] == iso and r["crop"] == crop and r.get("status") == "shown" and (r.get("change_pct_record") or 0) < 0
               for r in rows):
            return "its own harvest falls in the fit"
        for g in O.get("regions") or []:
            if g.get("effect_direction", 0) < 0 and iso in (g.get("iso3") or []) and crop_match(crop, g.get("label")):
                return "in a published El Niño drought region for the crop"
        for e in pubfx if isinstance(pubfx, list) else []:
            if e.get("iso") == iso and crop_match(crop, e.get("crop")):
                hi = e.get("effect_high_pct")
                if (hi < 0) if isinstance(hi, (int, float)) else bool(re.search(r"\bfalls?\b|declin|\blost\b|loss", e.get("effect_text") or "", re.I)):
                    return "a published study finds its harvest falls in El Niño"
        for m in restr:
            if (m.get("iso") == iso and "ban" in (m.get("measure") or "").lower() and crop_match(crop, m.get("commodity"))
                    and m.get("status") != "historical" and (not m.get("ends_date") or m["ends_date"] >= today)
                    and (not m.get("effective_date") or m["effective_date"][:7] <= today[:7])):
                return f"export ban in force ({m.get('measure')}, {m.get('status')})"
        return None

    def balance(iso, crop):
        p = (psd.get(iso) or {}).get(PSD_KEY.get(crop, crop))
        if not p or p.get("quality_flag") == "stale":
            return None
        return p

    pairs_out, exporters_used, excluded = [], {}, {}
    crops = sorted({w["crop"] for w in O.get("who_pays") or [] if w.get("balance") != "not pulled"})
    aggregate = {}

    for crop in crops:
        W = [w for w in O["who_pays"] if w["crop"] == crop and w.get("balance") != "not pulled"]
        # Buyers: each named buyer of a lost export, plus the supplier's own shortfall beyond its exports.
        demands = []
        for w in W:
            for b in w.get("buyers") or []:
                demands.append({"id": f"{w['iso']}>{b['iso']}", "from": w["iso"], "buyer": b["iso"], "kind": "buyer", "need": float(b["kt"])})
            if (w.get("extra_import_kt") or 0) > 0:
                demands.append({"id": f"{w['iso']}>{w['iso']}", "from": w["iso"], "buyer": w["iso"], "kind": "own", "need": float(w["extra_import_kt"])})
        suppliers = {w["iso"] for w in W}
        key = TM_KEY.get(crop, crop)
        for d in demands:
            im = ((tm(d["buyer"]) or {}).get("imp") or {}).get(key) or {}
            d["tm_year"] = im.get("year")
            d["usual"], d["usual_share"], d["usual_basis"] = {}, {}, {}
            # usual_out: the buyer's real partners that cannot step up, with the reason, so the page can say why
            # "usual suppliers" cover nothing (Botswana's maize comes from South Africa, itself short).
            d["usual_out"] = []
            for p in im.get("partners") or []:
                if p["iso"] == d["buyer"]:
                    continue
                if p["share_pct"] < PARAMS["min_usual_share_pct"]:
                    continue
                out = lambda why: d["usual_out"].append({"iso": p["iso"], "share_pct": round(p["share_pct"], 1), "why": why})
                if p["t"] < PARAMS["min_usual_t"]:
                    out(f"it shipped {p['t'] / 1000:.1f} kt in {d['tm_year']}, under the {PARAMS['min_usual_t'] // 1000} kt a usual supplier must clear")
                    continue
                if p["iso"] in suppliers:
                    out("it is itself short in this scenario")
                    continue
                reason = why_out(p["iso"], crop, d["from"])
                if reason:
                    excluded.setdefault((p["iso"], crop), reason)
                    out(reason)
                    continue
                if not balance(p["iso"], crop):
                    excluded.setdefault((p["iso"], crop), "no current USDA supply balance for it")
                    out("no current USDA supply balance for it")
                    continue
                d["usual"][p["iso"]] = float(p["t"])
                d["usual_share"][p["iso"]] = p["share_pct"]
                d["usual_basis"][p["iso"]] = p.get("basis")
        # Exporter pool: everyone with a current USDA balance, normal exports above the floor, not ruled out.
        pool = {}
        for iso in psd:
            p = balance(iso, crop)
            if not p or (p.get("exports_kt") or 0) < PARAMS["min_exporter_kt"]:
                continue
            reason = why_out(iso, crop, "")
            if iso in suppliers:
                reason = "it is itself short in this scenario"
            if reason:
                excluded.setdefault((iso, crop), reason)
                continue
            pool[iso] = {"exports_kt": float(p["exports_kt"]), "stocks_kt": float(p.get("stocks_kt") or 0)}
        for d in demands:          # usual suppliers below the pool floor still count: they are real partners
            for iso in d["usual"]:
                if iso not in pool:
                    p = balance(iso, crop)
                    pool[iso] = {"exports_kt": float(p.get("exports_kt") or 0), "stocks_kt": float(p.get("stocks_kt") or 0)}

        results = {}     # (sens key, scope) -> (got, leftover cap, exporter headroom)
        for s in PARAMS["sensitivity"]:
            head = {i: min(s["stock_share"] * e["stocks_kt"] + s["slack_share"] * e["exports_kt"], PARAMS["export_cap_x"] * e["exports_kt"])
                    for i, e in pool.items()}
            for scope in ("usual", "open"):
                got, left = ration(demands, {i: {"cap": h} for i, h in head.items()}, scope, s, pan[s["panama"]])
                results[(s["key"], scope)] = (got, left, head)

        # Forecast range: re-run the mid-headroom allocation with each supplier's loss scaled to P10 / P50 / P90 of the forecast
        # harvest change (response capped at the strongest fitted winter), against the record-winter loss that sized the demands.
        # lost exports = min(loss, exports); the rest of the loss is the supplier's own extra import. Named buyers scale with lost exports.
        mid_s = [x for x in PARAMS["sensitivity"] if x["key"] == "mid"][0]
        mid_head = results[("mid", "usual")][2]
        rec_pct = {(r["iso"], r["crop"]): r.get("change_pct_record") for r in rows if r.get("status") == "shown"}
        frange = {}
        for qk in ("p10", "p50", "p90"):
            sc = {}
            for w in W:
                dp = DIST.get(f"{w['iso']}/{crop}") or {}
                q = ((dp.get("change_pct_capped") or {}).get(qk) if isinstance(dp.get("change_pct_capped"), dict) else None)
                if q is None:
                    q = (dp.get("change_pct") or {}).get(qk)
                rp = rec_pct.get((w["iso"], crop))
                if q is None or not rp or rp >= 0:
                    sc[w["iso"]] = None
                    continue
                ratio = max(0.0, q / rp)
                loss = w["loss_kt"] * ratio
                exp_kt = float(w.get("exports_kt") or 0)
                lost = min(loss, exp_kt)
                sc[w["iso"]] = {"loss": loss, "named": min(1.0, lost / w["lost_exports_kt"]) if w.get("lost_exports_kt") else 0.0, "extra": loss - lost}
            if any(v is None for v in sc.values()):
                continue
            dq = [dict(d, need=(d["need"] * sc[d["from"]]["named"] if d["kind"] == "buyer" else sc[d["from"]]["extra"])) for d in demands]
            for scope in ("usual", "open"):
                got, _left = ration(dq, {i: {"cap": h} for i, h in mid_head.items()}, scope, mid_s, pan[mid_s["panama"]])
                for d in dq:
                    frange.setdefault(d["id"], {}).setdefault(qk, {"need_kt": round(d["need"], 1)})[scope + "_residual_kt"] = round(max(0.0, d["need"] - sum(got[d["id"]].values())), 1)

        # Per-buyer output.
        by_pair = {}
        for d in demands:
            buyer_bal = balance(d["buyer"], crop) or {}
            cases = {}
            for s in PARAMS["sensitivity"]:
                cases[s["key"]] = {}
                for scope in ("usual", "open"):
                    got = results[(s["key"], scope)][0][d["id"]]
                    lines = []
                    for iso, kt in sorted(got.items(), key=lambda x: -x[1]):
                        if kt < 0.05:
                            continue
                        n = pool[iso]["exports_kt"]
                        line = {"from": iso, "kt": round(kt, 1), "tier": "usual" if iso in d["usual"] else "new",
                                "of_its_normal_exports_pct": round(kt / n * 100, 1) if n else None}
                        if iso in d["usual"]:
                            line["usual_share_pct"] = d["usual_share"][iso]
                        if iso == "USA" and d["buyer"] in PANAMA_ASIA:
                            line["route"] = {"via": "Panama Canal", "capacity_factor": round(pan[s["panama"]], 3), "state": s["panama"]}
                            if fr:
                                line["route"]["freight_vs_baseline_pct"] = fr["vs_baseline_pct"]
                        lines.append(line)
                    # Conservation to 0.1 kt: replaced + residual = need, rounding absorbed by the largest line.
                    raw_res = d["need"] - sum(got.values())
                    diff = round(d["need"] - sum(x["kt"] for x in lines), 1)
                    if lines and (raw_res < 0.05 or diff < 0):
                        big = max(lines, key=lambda x: x["kt"])
                        big["kt"] = round(big["kt"] + (diff if raw_res < 0.05 else max(diff, -big["kt"])), 1)
                    repl = round(sum(x["kt"] for x in lines), 1)
                    cases[s["key"]][scope] = {"replaced": lines, "replaced_kt": repl, "residual_kt": round(d["need"] - repl, 1)}
            cons, stocks = buyer_bal.get("consumption_kt"), buyer_bal.get("stocks_kt")
            rec = {"iso": d["buyer"], "kind": d["kind"], "from": d["from"], "need_kt": d["need"], "tm_year": d["tm_year"],
                   "usual_suppliers": [{"iso": i, "t": d["usual"][i], "share_pct": d["usual_share"][i], "basis": d["usual_basis"][i]} for i in d["usual"]],
                   "usual_out": d["usual_out"],
                   "stocks_kt": stocks, "consumption_kt": cons, "cases": cases}
            if frange.get(d["id"]):
                rec["forecast_range"] = frange[d["id"]]
            for k in ("low", "mid", "high"):
                for scope in ("usual", "open"):
                    c = cases[k][scope]
                    c["residual_weeks_of_use"] = round(c["residual_kt"] / cons * 52, 1) if cons and c["residual_kt"] > 0 else 0.0
            by_pair.setdefault(d["from"], []).append(rec)
        for w in W:
            named = sum(b["kt"] for b in w.get("buyers") or [])
            pairs_out.append({"iso": w["iso"], "crop": crop, "harvest": w.get("harvest"), "loss_kt": w["loss_kt"],
                              "lost_exports_kt": w["lost_exports_kt"], "own_import_kt": w.get("extra_import_kt") or 0,
                              "unrouted_other_buyers_kt": w.get("other_buyers_kt") or 0, "named_buyers_kt": named,
                              "buyers": by_pair.get(w["iso"], [])})

        # Exporters and aggregates per case.
        ex_rows, agg = [], {}
        for (k, scope), (got, left, head) in sorted(results.items()):
            used = {}
            for did, g in got.items():
                for iso, kt in g.items():
                    used[iso] = used.get(iso, 0) + kt
            need = sum(d["need"] for d in demands)
            resid = {}
            for d in demands:
                r = d["need"] - sum(got[d["id"]].values())
                resid[d["buyer"]] = resid.get(d["buyer"], 0) + max(r, 0)
            short = sorted(((i, round(v, 1)) for i, v in resid.items() if v >= 1), key=lambda x: -x[1])
            stretched = sorted(((i, round(u / pool[i]["exports_kt"] * 100, 1)) for i, u in used.items()
                                if pool[i]["exports_kt"] and u / pool[i]["exports_kt"] > PARAMS["stretch_share"]), key=lambda x: -x[1])
            agg.setdefault(k, {})[scope] = {
                "need_kt": round(need, 1), "replaced_kt": round(sum(used.values()), 1),
                # Global export headroom for the crop in this case: every eligible exporter's headroom before and after all buyers draw on it.
                "headroom_total_kt": round(sum(head.values()), 1), "headroom_left_kt": round(sum(max(0.0, v) for v in left.values()), 1),
                "headroom_exporters": sum(1 for v in head.values() if v > 0.05),
                "residual_kt": round(sum(v for _, v in short), 1),
                "short_countries": [{"iso": i, "residual_kt": v} for i, v in short],
                "stretched": [{"iso": i, "extra_pct_of_normal_exports": v} for i, v in stretched],
                "suppliers": [{"iso": i, "kt": round(u, 1)} for i, u in sorted(used.items(), key=lambda x: -x[1]) if u >= 0.05]}
            for iso, u in used.items():
                if u < 0.05:
                    continue
                exporters_used.setdefault((iso, crop), {"iso": iso, "crop": crop, "normal_exports_kt": pool[iso]["exports_kt"],
                                                        "stocks_kt": pool[iso]["stocks_kt"], "cases": {}})
                exporters_used[(iso, crop)]["cases"][f"{k}_{scope}"] = {"headroom_kt": round(head[iso], 1), "used_kt": round(u, 1)}
        aggregate[crop] = agg

    excl_rows = [{"iso": i, "crop": c, "why": w} for (i, c), w in sorted(excluded.items())]
    n_total = sum(len(p["buyers"]) for p in pairs_out)
    mid = {c: aggregate[c]["mid"] for c in aggregate}
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Derived: enso_outlook who_pays (shortfall, buyers), FAOSTAT Detailed Trade Matrix (data/tm), USDA PSD, trade_restrictions, enso_lanes (Panama Canal Authority advisories), enso_freight (USDA AMS)",
        "version": "v1", "status": "ok",
        "notes": "Scenario arithmetic, not a forecast. Potential import need met by other exporters, or left uncovered. On the cron after the outlook: stdlib only, about a second. Panama state read from enso_lanes live_2026.latest_advisory (" + str(pan.get("advisory")) + ") and its dated steps: " + str((pan.get("step_in_force") or {}).get("total")) + " slots in force on " + today + ", next step " + str(pan.get("slots_next")) + " from " + str(pan.get("next_from")) + ".",
        "assumptions": [
            "Demand: each named buyer's lost-export tonnes from who_pays (already capped at the buyer's usual imports), plus the short supplier's own shortfall beyond its exports (its own import need). Tonnes come from the who_pays row fields, so the rule follows whatever production baseline the outlook uses. 'Other buyers' (past the five named) are not routed and are listed as unrouted.",
            "Candidate exporters, scope 'usual': the buyer's own usual suppliers of the crop in the FAOSTAT trade matrix (top eight partners, at least 1% of the buyer's imports and 10 kt). Each can ship at most 3x its normal volume to that buyer. Weights: normal tonnes.",
            "Candidate exporters, scope 'open': the usual suppliers without the 3x limit, then any other exporter with a current USDA balance and normal exports of at least 1,000 kt, in proportion to its headroom. This is a ceiling: new buyer-seller relationships are not observed trade and ignore freight, contracts and quality (white versus yellow maize).",
            "Exporter headroom = stock share x ending stocks + slack share x normal exports (USDA PSD). Three cases: 25% of stocks and no slack, 50% and 10%, 100% and 20%, never more than a doubling of normal exports (a cap: stocks alone are not exportable, China holds most of the world's rice stock). The shares are sensitivity assumptions, not estimates.",
            "Excluded: the short supplier, any exporter whose own harvest of the crop falls in the fitted outlook, sits in a published El Niño drought region for that crop, has a published study finding a fall, or has an export ban in force (trade_restrictions). Excluded, not haircut. Exporters whose own crop gains (for example US wheat) get no extra headroom.",
            "Rationing: a buyer's need is split across eligible exporters in proportion to weight; an exporter asked for more than its headroom is cut back pro rata across the buyers asking it. Repeated until nothing moves. All buyers of a crop compete for the same headroom.",
            "Routes: US exports to East and Southeast Asian buyers are scaled by the Panama Canal capacity factor: booking slots in the step in force today under the newest advisory, " + str(pan.get("advisory") or "unknown") + " (mid, high cases; a step scheduled for a later date is carried as slots_next but not used), or the 2023 floor (low case), over the lower end of the stated normal 36-38 a day. Gulf ocean freight (USDA AMS, US Gulf to Japan) is carried as a cost signal with its own baseline; no freight cost is invented or used to rank suppliers. Landlocked access, port capacity and overland routes are not modelled.",
            "Residual weeks of use = uncovered tonnes over the buyer's USDA annual consumption x 52. Buyer stocks are shown, not subtracted.",
            "Forecast range (mid headroom): the same allocation re-run with each supplier's loss scaled to the P10, P50 and P90 of its forecast harvest change (response stopped at the strongest fitted winter) over the record-winter change that sized the demands. Lost exports are min(loss, normal exports); the remainder is the supplier's own extra import. Named buyers never scale above their record-winter need, because their usual imports from the supplier already cap it, so the P10 end is understated for them. P10 is the severe end. Scenario arithmetic, not a forecast.",
            "Global export headroom (headroom_total_kt): the summed headroom of every eligible exporter in the case, before buyers draw on it; headroom_left_kt is what remains after all buyers in the scenario. A ceiling shared by every buyer, not a delivered quantity.",
            "Stretched exporters: extra tonnes above 25% of the exporter's normal exports.",
        ],
        "params": PARAMS, "panama": pan, "freight_signal": fr, "reference_date": today,
        "panama_asia_buyers": sorted(PANAMA_ASIA),
    }
    payload = {"case": case, "oni": oni, "harvest_winter": O.get("harvest_winter"), "params": PARAMS,
               "sensitivity": PARAMS["sensitivity"], "pairs": pairs_out, "aggregate": aggregate,
               "exporters": sorted(exporters_used.values(), key=lambda e: (e["crop"], e["iso"])),
               "excluded": excl_rows, "panama": pan, "freight_signal": fr}
    out = {"_meta": meta, "data": payload}
    (DATA / "enso_replacement.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    from pipeline_dag import stamp_file   # records _meta.inputs {file: generated_at}
    stamp_file("enso_replacement.json")
    print(f"[OK] enso_replacement: {len(pairs_out)} pairs, {n_total} buyer lines, crops {crops}")
    for c, m in mid.items():
        for sc, x in m.items():
            print(f"  {c} mid/{sc}: need {x['need_kt']} kt, replaced {x['replaced_kt']}, residual {x['residual_kt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
