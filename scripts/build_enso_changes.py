#!/usr/bin/env python3
"""
build_enso_changes.py -- the server-side "What changed" for the El Niño tab.

Until now the page compared itself with a snapshot kept in the viewer's own browser, so two readers saw two
different stories and a first-time reader saw none. This step does it once, for everyone:

  1. It reads the El Niño feeds as they stand now (weekly Niño 3.4, CPC odds, Panama slots and draft, Gatún vs
     median, curated impact reports, IPC and WFP figures, price-model status, fitted tonnes for each crop pair,
     "is the expected pattern showing up" statuses, export measures in force) into one snapshot.
  2. It appends the snapshot to data/enso_snapshots.json (rolling, keyed by UTC run time).
  3. It compares now with the newest snapshot at least 6 h, 24 h and 7 d old and writes
     data/enso_changes.json: a ranked list of plain sentences per window.

No number in a sentence is typed here. Every figure comes from a feed or from the difference between two
snapshots. The food link after each sentence is a fixed clause with no figures in it.

Ranking (written out, also in _meta.rank_rule): score = materiality x confidence x relevance, each 0 to 1.
  materiality  size of the move against the scale that would count as "full" for that measure (SCALE below);
               discrete events (a measure starts or ends, a new advisory) are 1.
  confidence   how firm the source is (CONF below): measurement 1.0, official notice 0.9, agency figure in a curated
               file 0.8, FoodShield fit or model 0.6, automated extraction pending review 0.5.
  relevance    how directly it touches food supply or access (REL below): harvest tonnes and export bans 1.0,
               hunger counts 0.9, pattern confirmation and shipping 0.8, forecast odds 0.7, sea temperature 0.6.
Changes below materiality 0.1 are dropped as noise. Nothing is hand-ranked.

Stdlib only (write_json comes from _common, which needs requests, present in CI).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, write_json  # noqa: E402

WINDOWS = (("6h", 6), ("24h", 24), ("7d", 168))
SNAP_CAP = 700            # hard cap on stored snapshots
KEEP_DAYS = 30            # nothing older is kept
FULL_RES_HOURS = 48       # every run is kept for two days, then one per 6 h
MAX_PER_WINDOW = 8
MIN_MATERIALITY = 0.1
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# How large a move counts as "full" materiality (1.0). Anything smaller scales linearly.
SCALE = {
    "n34": (0.5, "deg C of weekly Niño 3.4 anomaly"),
    "cpc_vs": (20.0, "percentage points in CPC's odds of a very strong winter"),
    "panama_slots": (3.0, "booking slots a day"),
    "panama_draft": (1.0, "feet of Neopanamax draft"),
    "gatun": (2.0, "feet against Gatún's median for the date"),
    "impact_reports": (3.0, "checked impact reports"),
    "ipc": (0.05, "share of the people in IPC Phase 3+ across the El Niño regions"),
    "wfp": (5.0, "million added people in WFP's projection"),
    "pair_kt": (0.25, "share of a fitted crop-pair change in tonnes"),
    "confirm": (2.0, "steps on the five-step confirmation ladder"),
}
CONF = {"measured": 1.0, "official": 0.9, "agency": 0.8, "fitted": 0.6, "extracted": 0.5}
REL = {"n34": 0.6, "cpc_vs": 0.7, "panama_slots": 0.8, "panama_draft": 0.8, "acp_new": 0.8, "gatun": 0.7,
       "impact_reports": 0.7, "ipc": 0.9, "wfp": 0.9, "price_status": 0.8, "pair_kt": 1.0, "confirm": 0.8,
       "export_measure": 1.0}
LADDER = {"against": 0, "notyet": 1, "leaning": 2, "showing": 3}
LADDER_WORD = {"against": "running against", "notyet": "not yet", "leaning": "leaning that way", "showing": "showing up"}


# --------------------------------------------------------------------------------------------------------------
def load(name: str):
    p = DATA_DIR / name
    if not p.exists():
        return None, {}
    try:
        j = json.loads(p.read_text())
    except (OSError, ValueError):
        return None, {}
    if isinstance(j, dict) and "data" in j and "_meta" in j:
        return j["data"], j["_meta"]
    return j, (j.get("_meta") if isinstance(j, dict) else {}) or {}


def dm(iso: str) -> str:
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
        return f"{d.day} {MON[d.month - 1]}"
    except (ValueError, TypeError):
        return str(iso)


def sgn(v: float, nd: int = 1) -> str:
    return ("+" if v > 0 else "−" if v < 0 else "") + f"{abs(v):.{nd}f}"


def kt(v: float) -> str:
    return ("+" if v > 0 else "−" if v < 0 else "") + f"{abs(round(v)):,} kt"


def iso_names() -> dict:
    """ISO3 -> short country name from Natural Earth (the same file the page draws), with the page's SHORT_NAMES."""
    geo = DATA_DIR / "geo" / "ne_50m_admin_0_countries.geojson"
    out = {}
    if geo.exists():
        for f in json.loads(geo.read_text())["features"]:
            p = f.get("properties") or {}
            iso = next((p[k] for k in ("ISO_A3_EH", "ISO_A3", "ADM0_A3") if p.get(k) and p[k] != "-99"), None)
            if iso and (p.get("NAME") or p.get("ADMIN")):
                out.setdefault("iso:" + iso, p.get("NAME") or p.get("ADMIN"))
    out.update({"iso:USA": "United States", "iso:COD": "DR Congo", "iso:CAF": "Central African Rep."})
    return out


def region_rain_status(now: datetime) -> dict:
    """Server copy of the page's confirmRows(): median 30-day SPI of the 2.5 degree land cells inside each region's
    countries, read on the same ladder (>= +0.5 showing, 0..0.5 leaning, 0..-0.5 not yet, beyond that against)."""
    rain, _ = load("rain_anomaly.json")
    regs, _ = load("enso_regions.json")
    geo = DATA_DIR / "geo" / "ne_50m_admin_0_countries.geojson"
    if not (rain and regs and geo.exists() and rain.get("spi")):
        return {}
    G, spi = rain["grid"], rain["spi"]
    feats = json.loads(geo.read_text())["features"]
    by_iso: dict = {}
    for f in feats:
        p = f.get("properties") or {}
        for k in ("ISO_A3_EH", "ISO_A3", "ADM0_A3"):
            v = p.get(k)
            if v and v != "-99":
                by_iso.setdefault(v, f)
                break

    def in_ring(ring, x, y):
        ins = False
        j = len(ring) - 1
        for i in range(len(ring)):
            a, b = ring[i], ring[j]
            if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
                ins = not ins
            j = i
        return ins

    def cells(iso):
        f = by_iso.get(iso)
        if not f or not f.get("geometry"):
            return []
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
        xs = [c[0] for pl in polys for c in pl[0]]
        ys = [c[1] for pl in polys for c in pl[0]]
        if not xs:
            return []
        out = []
        step, lat0, lon0, nlon, nlat = G["step_deg"], G["lat0"], G["lon0"], G["nlon"], G["nlat"]
        i = max(0, int((min(ys) - lat0) // step))
        while i < nlat and lat0 + i * step <= max(ys):
            j = max(0, int((min(xs) - lon0) // step))
            while j < nlon and lon0 + j * step <= max(xs):
                v = spi[i * nlon + j]
                if v is not None:
                    cy, cx = lat0 + i * step, lon0 + j * step
                    if any(in_ring(pl[0], cx, cy) and not any(in_ring(h, cx, cy) for h in pl[1:]) for pl in polys):
                        out.append(v)
                j += 1
            i += 1
        return out

    month = now.month
    res = {}
    for r in regs.get("regions", []):
        if r.get("rain") not in ("drier", "wetter"):
            continue
        months = r.get("rain_season_months")
        e = -1 if r["rain"] == "drier" else 1
        if months and month not in months:
            res[r["id"]] = {"st": "season", "label": r["label"], "e": e}
            continue
        vals = sorted(v for iso in r.get("iso3", []) for v in cells(iso))
        if not vals:
            res[r["id"]] = {"st": "small", "label": r["label"], "e": e}
            continue
        n = len(vals)
        med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        v = med * e
        st = "showing" if v >= 50 else "leaning" if v > 0 else "notyet" if v > -50 else "against"
        res[r["id"]] = {"st": st, "label": r["label"], "e": e, "spi": round(med / 100, 2), "n": n}
    return res


def restriction_live(r: dict, now: datetime) -> bool:
    """Server copy of the page's restrictionState() === 'live'."""
    if r.get("status") == "historical" and not r.get("auto_expired"):
        return False
    end = r.get("ends_date")
    if not end:
        return True
    try:
        return datetime.fromisoformat(end + "T23:59:59+00:00") >= now
    except ValueError:
        return True


def take_snapshot(now: datetime) -> dict:
    """Current values of every tracked measure, plain JSON, each with the facts a sentence needs."""
    v: dict = {}
    today = now.date().isoformat()
    enso, _ = load("enso.json")
    w = (enso or {}).get("weekly_nino34") or {}
    if isinstance(w.get("anom"), (int, float)):
        d = w.get("date", "")
        try:
            d = datetime.strptime(d, "%d%b%Y").date().isoformat()
        except ValueError:
            pass
        v["n34"] = {"v": w["anom"], "date": d}

    st, _ = load("enso_strengths.json")
    djf = next((s for s in (st or {}).get("seasons", []) if s.get("season") == "DJF"), None)
    if djf and "very strong El Niño" in djf.get("classes", {}):
        v["cpc_vs"] = {"v": djf["classes"]["very strong El Niño"], "issued": (st or {}).get("issued")}

    lanes, _ = load("enso_lanes.json")
    pan = next((x for x in (lanes or {}).get("lanes", []) if x.get("id") == "panama"), None)
    live = (pan or {}).get("live_2026") or {}
    steps = sorted(({"from": s.get("booking_from") or s.get("effective"), "total": s.get("total"),
                     "neo": s.get("neopanamax")} for s in live.get("steps", []) if s.get("total")), key=lambda s: s["from"])
    if steps:
        v["panama_steps"] = {"steps": steps,
                             "advisory": (live.get("draft_advisory") or live.get("latest_advisory") or {}).get("advisory")}
    m = re.search(r"\((\d{2}(?:\.\d)?)\s*ft\)", live.get("draft", "") or "")
    if m:
        v["panama_draft"] = {"v": float(m.group(1)), "advisory": (live.get("draft_advisory") or {}).get("advisory")}
    g, _ = load("enso_gauges.json")
    acp = ((g or {}).get("acp_advisories") or {}).get("latest")
    if acp:
        ex = (acp.get("extracted") or {}).get("fields", {})
        v["acp_latest"] = {"number": acp["number"], "id": acp["id"], "title": acp["title"],
                           "draft_ft": (ex.get("draft_ft") or {}).get("value"),
                           "first_booking": (ex.get("first_booking_date") or {}).get("value"),
                           "total_slots": (ex.get("total_slots") or {}).get("value"),
                           "curated_seen": (live.get("latest_advisory") or {}).get("advisory")}
    gat = ((g or {}).get("gauges") or {}).get("gatun")
    if gat and isinstance(gat.get("vs_median_ft"), (int, float)):
        v["gatun"] = {"v": gat["vs_median_ft"], "date": (gat.get("latest") or {}).get("date")}

    rec, _ = load("enso_recent_events.json")
    if rec and isinstance(rec.get("events"), list):
        v["impact_reports"] = {"n": len(rec["events"]), "ids": [e.get("id") for e in rec["events"]]}

    sit, _ = load("enso_situation.json")
    regs, _ = load("enso_regions.json")
    isos = {i for r in (regs or {}).get("regions", []) for i in r.get("iso3", [])}
    ipc, _ = load("ipc.json")
    upd = (sit or {}).get("ipc_updates") or {}
    per = {}
    for i in sorted(isos):
        c = (upd.get(i) or {}).get("phase3plus_count") or ((ipc or {}).get(i) or {}).get("phase3plus_count") or 0
        if c > 0:
            per[i] = c
    if per:
        v["ipc"] = {"total": sum(per.values()), "countries": len(per), "by_iso": per}
    wfp = (sit or {}).get("wfp_projection") or {}
    if isinstance(wfp.get("added_m"), (int, float)):
        v["wfp"] = {"v": wfp["added_m"], "date": wfp.get("date")}

    po, _ = load("enso_price_outlook.json")
    if po:
        v["price_status"] = {"status": po.get("model_status"), "rows": len(po.get("rows", []))}

    out, _ = load("enso_outlook.json")
    if out:
        pairs = {f"{r['iso']}:{r['crop']}": {"kt": r["change_kt_record"], "harvest": r.get("harvest"),
                                               "pct": r.get("change_pct_record")}
                 for r in out.get("rows_all", []) if r.get("status") == "shown" and isinstance(r.get("change_kt_record"), (int, float))}
        if pairs:
            v["pairs"] = {"oni": (out.get("cases", {}).get("record") or {}).get("oni"), "by_pair": pairs}

    conf = region_rain_status(now)
    if conf:
        v["confirm"] = conf

    tr, _ = load("trade_restrictions.json")
    if isinstance(tr, list):
        v["measures"] = {f"{r.get('iso')}|{r.get('commodity')}|{r.get('measure')}":
                         {"country": r.get("country"), "commodity": r.get("commodity"), "measure": r.get("measure"),
                          "ends": r.get("ends_date"), "status": r.get("status")}
                         for r in tr if restriction_live(r, now)}
    return v


# --------------------------------------------------------------------------------------------------------------
def mk(key, text, direction, food, pressure, mat, conf, link, source):
    rel = REL[key.split(":")[0]] if key.split(":")[0] in REL else 0.7
    mat = max(0.0, min(1.0, mat))
    return {"key": key, "text": text, "direction": direction, "food_link": food, "food_pressure": pressure,
            "materiality": round(mat, 2), "confidence": CONF[conf], "relevance": rel,
            "score": round(mat * CONF[conf] * rel, 3), "lens": link, "source": source}


def compare(prev: dict, cur: dict, names: dict) -> list:
    out = []
    p, c = prev, cur

    if "n34" in p and "n34" in c and p["n34"]["v"] != c["n34"]["v"]:
        d = c["n34"]["v"] - p["n34"]["v"]
        out.append(mk("n34", f"Weekly Niño 3.4 {'warmer' if d > 0 else 'cooler'}: {sgn(p['n34']['v'])} to {sgn(c['n34']['v'])} °C, week of {dm(c['n34']['date'])}",
                      "up" if d > 0 else "down", "a stronger event puts more harvest regions at risk of drought or flood" if d > 0 else "a weaker event lowers the risk to harvests",
                      "adds" if d > 0 else "eases", abs(d) / SCALE["n34"][0], "measured", "elnino", "NOAA CPC weekly Niño 3.4"))
    if "cpc_vs" in p and "cpc_vs" in c and p["cpc_vs"]["v"] != c["cpc_vs"]["v"]:
        d = c["cpc_vs"]["v"] - p["cpc_vs"]["v"]
        out.append(mk("cpc_vs", f"CPC odds of a very strong winter {'up' if d > 0 else 'down'}: {p['cpc_vs']['v']}% to {c['cpc_vs']['v']}% (December to February, {c['cpc_vs'].get('issued') or 'latest issue'})",
                      "up" if d > 0 else "down", "a stronger winter peak means larger fitted harvest losses" if d > 0 else "a weaker winter peak means smaller fitted harvest losses",
                      "adds" if d > 0 else "eases", abs(d) / SCALE["cpc_vs"][0], "official", "elnino", "NOAA CPC RONI outlook"))

    ps, cs = (p.get("panama_steps") or {}).get("steps"), (c.get("panama_steps") or {}).get("steps")
    if ps is not None and cs is not None and ps != cs:
        adv = (c["panama_steps"].get("advisory")) or "ACP advisory"
        pmap = {s["from"]: s["total"] for s in ps}
        for s in cs:
            if pmap.get(s["from"]) == s["total"]:
                continue
            before = [x for x in cs if x["from"] < s["from"]]
            was = pmap.get(s["from"], before[-1]["total"] if before else None)
            if was is None or was == s["total"]:
                continue
            d = s["total"] - was
            out.append(mk("panama_slots", f"Panama slots {was} to {s['total']} from {dm(s['from'])}: ACP {adv}", "up" if d > 0 else "down",
                          "more daily transits for grain, containers and fuel through the canal" if d > 0 else "fewer daily transits for grain, containers and fuel through the canal",
                          "eases" if d > 0 else "adds", abs(d) / SCALE["panama_slots"][0], "official", "ensowater", f"Panama Canal Authority {adv}"))
    if "panama_draft" in p and "panama_draft" in c and p["panama_draft"]["v"] != c["panama_draft"]["v"]:
        d = c["panama_draft"]["v"] - p["panama_draft"]["v"]
        adv = c["panama_draft"].get("advisory") or "ACP advisory"
        out.append(mk("panama_draft", f"Neopanamax draft limit {p['panama_draft']['v']:.1f} to {c['panama_draft']['v']:.1f} ft: ACP {adv}", "up" if d > 0 else "down",
                      "deeper loading for grain and container ships through the canal" if d > 0 else "lighter loads for grain and container ships through the canal",
                      "eases" if d > 0 else "adds", abs(d) / SCALE["panama_draft"][0], "official", "ensowater", f"Panama Canal Authority {adv}"))
    pa, ca = p.get("acp_latest"), c.get("acp_latest")
    if pa and ca and ca["number"] > pa["number"]:
        bits = []
        if ca.get("draft_ft") is not None and ca["draft_ft"] != (c.get("panama_draft") or {}).get("v"):
            bits.append(f"draft {ca['draft_ft']:.1f} ft")
        if ca.get("total_slots") is not None and ca.get("first_booking"):
            bits.append(f"{ca['total_slots']} slots a day for booking dates from {dm(ca['first_booking'])}")
        tail = f"; automated extraction, pending review: {', '.join(bits)}" if bits else ""
        out.append(mk("acp_new", f"New Advisory to Shipping {ca['id']}: {ca['title']}{tail}", "new",
                      "the canal's operating limits set how much grain and cargo can cross", "info", 1.0,
                      "extracted" if bits else "official", "ensowater", "Panama Canal Authority advisory list"))
    if "gatun" in p and "gatun" in c and p["gatun"]["v"] != c["gatun"]["v"]:
        d = c["gatun"]["v"] - p["gatun"]["v"]
        now_v = c["gatun"]["v"]
        out.append(mk("gatun", f"Gatún Lake {abs(now_v):.1f} ft {'below' if now_v < 0 else 'above'} its median for {dm(c['gatun']['date'])} (was {abs(p['gatun']['v']):.1f} ft {'below' if p['gatun']['v'] < 0 else 'above'})",
                      "up" if d > 0 else "down", "a lower lake tightens the canal's slots and drafts" if d < 0 else "a higher lake eases pressure on the canal's slots and drafts",
                      "adds" if d < 0 else "eases", abs(d) / SCALE["gatun"][0], "measured", "ensowater", "Panama Canal Authority lake level"))
    if "impact_reports" in p and "impact_reports" in c and set(p["impact_reports"]["ids"]) != set(c["impact_reports"]["ids"]):
        new = [i for i in c["impact_reports"]["ids"] if i not in set(p["impact_reports"]["ids"])]
        gone = [i for i in p["impact_reports"]["ids"] if i not in set(c["impact_reports"]["ids"])]
        titles = [names.get(i) for i in new[:2] if names.get(i)]
        d = len(new) - len(gone)
        txt = f"Checked El Niño impact reports {p['impact_reports']['n']} to {c['impact_reports']['n']}"
        if titles:
            txt += ": " + "; ".join(titles)
        out.append(mk("impact_reports", txt, "up" if d > 0 else "down" if d < 0 else "changed",
                      "each is an event with a named source, placed on the map", "adds" if d > 0 else "info",
                      max(len(new), len(gone)) / SCALE["impact_reports"][0], "agency", "ensolive", "curated impact report list"))
    if "ipc" in p and "ipc" in c and p["ipc"]["total"] and p["ipc"]["total"] != c["ipc"]["total"]:
        d = c["ipc"]["total"] - p["ipc"]["total"]
        movers = sorted(((i, c["ipc"]["by_iso"].get(i, 0) - p["ipc"]["by_iso"].get(i, 0)) for i in set(c["ipc"]["by_iso"]) | set(p["ipc"]["by_iso"])),
                        key=lambda x: -abs(x[1]))
        mv = movers[0] if movers and movers[0][1] else None
        txt = f"People in IPC Phase 3 or worse across the El Niño regions {p['ipc']['total'] / 1e6:.1f} m to {c['ipc']['total'] / 1e6:.1f} m"
        if mv:
            txt += f" (most: {names.get('iso:' + mv[0], mv[0])} {sgn(mv[1] / 1e6)} m)"
        out.append(mk("ipc", txt, "up" if d > 0 else "down", "people already short of food before any harvest loss", "adds" if d > 0 else "eases",
                      abs(d) / p["ipc"]["total"] / SCALE["ipc"][0], "agency", "ensolive", "IPC analyses"))
    if "wfp" in p and "wfp" in c and p["wfp"]["v"] != c["wfp"]["v"]:
        d = c["wfp"]["v"] - p["wfp"]["v"]
        out.append(mk("wfp", f"WFP projected added acute food insecurity {p['wfp']['v']} m to {c['wfp']['v']} m people ({dm(c['wfp'].get('date') or '')})",
                      "up" if d > 0 else "down", "WFP's own count of extra people who will need food help", "adds" if d > 0 else "eases",
                      abs(d) / SCALE["wfp"][0], "agency", "ensolive", "WFP"))
    if "price_status" in p and "price_status" in c and p["price_status"]["status"] != c["price_status"]["status"]:
        w = lambda s: str(s or "unset").replace("_", " ")  # noqa: E731
        out.append(mk("price_status", f"Staple-price model status {w(p['price_status']['status'])} to {w(c['price_status']['status'])}", "changed",
                      "it decides whether FoodShield shows a model price path or only past El Niño paths", "info", 1.0, "fitted", "ensomoney", "FoodShield price model validation"))
    if "pairs" in p and "pairs" in c:
        for k, cv in c["pairs"]["by_pair"].items():
            pv = p["pairs"]["by_pair"].get(k)
            if not pv or pv["kt"] == cv["kt"]:
                continue
            base = max(abs(pv["kt"]), 1.0)
            rel = abs(cv["kt"] - pv["kt"]) / base
            if abs(cv["kt"] - pv["kt"]) < 50:
                continue
            iso, crop = k.split(":")
            crop = {"corn": "maize"}.get(crop, crop)
            worse = cv["kt"] < pv["kt"]
            out.append(mk("pair_kt", f"{names.get('iso:' + iso, iso)} {crop}: fitted {cv.get('harvest') or ''} harvest change {kt(pv['kt'])} to {kt(cv['kt'])} (record-winter case)".replace("  ", " "),
                          "down" if worse else "up", "tonnes that buyers would have to replace from other suppliers" if worse else "tonnes of harvest loss the fit no longer expects",
                          "adds" if worse else "eases", rel / SCALE["pair_kt"][0], "fitted", "ensoharvest", "FoodShield fit, enso_outlook.json"))
    if "confirm" in p and "confirm" in c:
        for rid, cv in c["confirm"].items():
            pv = p["confirm"].get(rid)
            if not pv or pv["st"] == cv["st"]:
                continue
            if pv["st"] in LADDER and cv["st"] in LADDER:
                d = LADDER[cv["st"]] - LADDER[pv["st"]]
                kind = "drier" if cv["e"] < 0 else "wetter"
                out.append(mk("confirm", f"{cv['label']}: expected {kind} pattern now \"{LADDER_WORD[cv['st']]}\" in observed 30-day rain (was \"{LADDER_WORD[pv['st']]}\")",
                              "up" if d > 0 else "down", "observed rain is the test of whether El Niño's usual effect is arriving", "info",
                              abs(d) / SCALE["confirm"][0], "measured", "ensolive", "NOAA CPC gauge rain, SPI"))
    if "measures" in p and "measures" in c:
        for k, m in c["measures"].items():
            if k not in p["measures"]:
                out.append(mk("export_measure", f"{m['country']} {str(m['commodity']).lower()}: {m['measure']} now in force" + (f" until {dm(m['ends'])}" if m.get("ends") else ""),
                              "new", "a supplier that closes its exports leaves importers with fewer sellers", "adds", 1.0, "official", "ensolive", "trade_restrictions.json"))
        for k, m in p["measures"].items():
            if k not in c["measures"]:
                out.append(mk("export_measure", f"{m['country']} {str(m['commodity']).lower()}: {m['measure']} no longer in force", "ended",
                              "a supplier reopening its exports gives importers more sellers", "eases", 1.0, "official", "ensolive", "trade_restrictions.json"))
    return out


def rank(changes: list) -> list:
    keep = [x for x in changes if x["materiality"] >= MIN_MATERIALITY]
    keep.sort(key=lambda x: (-x["score"], x["key"], x["text"]))
    return keep[:MAX_PER_WINDOW]


# --------------------------------------------------------------------------------------------------------------
def thin(snaps: list, now: datetime) -> list:
    """Every run for two days, then the first run of each 6 h block, nothing past KEEP_DAYS, never over SNAP_CAP."""
    out, seen = [], set()
    for s in sorted(snaps, key=lambda s: s["t"]):
        t = datetime.fromisoformat(s["t"])
        age_h = (now - t).total_seconds() / 3600
        if age_h > KEEP_DAYS * 24:
            continue
        if age_h > FULL_RES_HOURS:
            blk = (t.date().isoformat(), t.hour // 6)
            if blk in seen:
                continue
            seen.add(blk)
        out.append(s)
    return out[-SNAP_CAP:]


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cur = take_snapshot(now)
    if len(cur) < 6:
        raise RuntimeError(f"only {len(cur)} measures readable; keeping the previous files")
    sp = DATA_DIR / "enso_snapshots.json"
    snaps: list = []
    if sp.exists():
        try:
            snaps = json.loads(sp.read_text()).get("data", {}).get("snapshots", [])
        except (OSError, ValueError):
            snaps = []
    if not (snaps and snaps[-1]["v"] == cur and (now - datetime.fromisoformat(snaps[-1]["t"])) < timedelta(minutes=30)):
        snaps.append({"t": now.isoformat(), "v": cur})
    snaps = thin(snaps, now)
    write_json("enso_snapshots.json", {"snapshots": snaps, "cap": SNAP_CAP, "keep_days": KEEP_DAYS,
                                       "full_resolution_hours": FULL_RES_HOURS},
               source="Derived from the El Niño feeds in data/ at each run; see build_enso_changes.py",
               notes="Rolling archive of the values the El Niño tab compares against, keyed by UTC run time. Every run for 48 h, then one per 6 h block, 30 days at most.",
               status="ok")

    names = {}
    rec, _ = load("enso_recent_events.json")
    for e in (rec or {}).get("events", []):
        names[e.get("id")] = e.get("title")
    names.update(iso_names())

    windows = {}
    for label, hours in WINDOWS:
        cutoff = now - timedelta(hours=hours * 0.8)
        base = [s for s in snaps if datetime.fromisoformat(s["t"]) <= cutoff]
        if not base:
            windows[label] = {"hours": hours, "status": "baseline building", "baseline_t": None, "span_hours": None, "changes": [],
                              "note": f"no snapshot at least {hours * 0.8:g} h old yet"}
            continue
        b = base[-1]
        span = round((now - datetime.fromisoformat(b["t"])).total_seconds() / 3600, 1)
        windows[label] = {"hours": hours, "status": "ok", "baseline_t": b["t"], "span_hours": span,
                          "changes": rank(compare(b["v"], cur, names))}

    inputs = {}
    for n in ("enso.json", "enso_strengths.json", "enso_lanes.json", "enso_gauges.json", "enso_recent_events.json", "enso_situation.json",
              "enso_price_outlook.json", "enso_outlook.json", "rain_anomaly.json", "trade_restrictions.json", "ipc.json"):
        _, m = load(n)
        inputs[n] = (m or {}).get("generated_at")
    write_json("enso_changes.json",
               {"now": now.isoformat(), "snapshots_kept": len(snaps), "oldest_snapshot": snaps[0]["t"], "windows": windows, "inputs": inputs,
                "rank_rule": {
                    "score": "materiality x confidence x relevance, each 0 to 1; changes under materiality 0.1 are dropped; top 8 per window",
                    "materiality": "size of the move divided by the scale that counts as full (capped at 1); discrete events (a measure starts or ends, a new advisory, a status change) are 1",
                    "scales": {k: f"{v[0]:g} = full: {v[1]}" for k, v in SCALE.items()},
                    "confidence": "measurement 1.0, official notice 0.9, agency figure in a curated file 0.8, FoodShield fit or model 0.6, automated extraction pending review 0.5",
                    "relevance": "fitted harvest tonnes and export measures 1.0, hunger counts 0.9, shipping and pattern confirmation 0.8, forecast odds and lake level 0.7, sea temperature 0.6",
                    "window_rule": "each window compares now with the newest snapshot at least 80% of the window old; with none that old the window says 'baseline building' and lists nothing",
                    "text": "sentences are built from the two snapshots' values; the food link is a fixed clause with no figures"}},
               source="Derived from the El Niño feeds in data/ (see enso_changes.json inputs for each feed's own generated_at)",
               notes="What changed in the El Niño tab's inputs over the last 6 hours, 24 hours and 7 days, ranked by the rule in rank_rule. Built by scripts/build_enso_changes.py on every refresh.",
               status="ok")
    for k, w in windows.items():
        print(f"  {k}: {w['status']}, {len(w['changes'])} changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
