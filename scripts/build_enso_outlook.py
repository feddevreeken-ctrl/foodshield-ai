#!/usr/bin/env python3
"""
build_enso_outlook.py — what this El Nino implies, by region, crop, country and harvest.

Reads what the pipeline already holds and writes data/enso_outlook.json:

  - data/enso_regions.json   published teleconnection regions (curated, cited)
  - data/enso_model.json     fitted yield slopes, % per ONI (build_enso_model.py)
  - data/crop_calendars.json harvest months per country-crop
  - data/usda_psd.json       current production / imports / exports
  - data/worldbank_pink_sheet.json  latest world prices, $/t
  - data/enso.json           observed ONI (latest season) and the record DJF
  - data/asap.json, gdacs.json, rtfp.json, reliefweb_alerts.json,
    enso_news.json, trade_restrictions.json   what is being reported now

TWO ANCHORED CASES, NO EXTRAPOLATION
------------------------------------
The fit is linear in ONI and was trained on 1961-2024 winters, the largest of
which is 2015-16 at +2.5. CPC's own outlook puts OND 2026 at RONI +2.67 (median),
which is beyond anything the fit has seen. So this does not weight a probability
table into a single forecast. It reports each fitted pair at two ONI values the
record contains: the latest observed season, and the record winter. Anything
stronger is stated as outside the fitted range.

TONNES FIRST, VALUE SECOND, NO WORLD-PRICE MODEL
------------------------------------------------
See build_enso_exposure.py: strong El Ninos have not raised world grain prices.
The value line is the harvest change priced at today's World Bank price -- a
size of what is at stake, not a forecast of a price or of an import bill.

HARVEST TIMING
--------------
enso_model.json aligns each crop to a DJF winter: "djf_same_year" means the
harvest falls in the calendar year of that winter's January, "djf_next_year"
means the harvest in the year before it. For the 2026-27 winter (January 2027)
that is harvest 2027 and harvest 2026 respectively.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PSD_KEY = {"corn": "corn", "wheat": "wheat", "rice": "rice", "soybeans": "soybeans"}
PRICE_KEY = {"corn": "maize", "wheat": "wheat", "rice": "rice", "soybeans": "soybeans"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# The crops each published region is about. A country's other fitted crops are
# kept but flagged, so Indonesian corn does not appear as the palm-oil signal.
REGION_CROPS = {
    "southern_africa_maize": {"corn", "sorghum", "millet"},
    "australia_wheat": {"wheat", "barley", "sorghum"},
    "india_monsoon": {"rice", "millet", "soybeans", "sorghum", "wheat"},
    "indonesia_rice": {"rice"}, "philippines_rice": {"rice"}, "sea_palm_oil": set(),
    "argentina_soy_maize": {"corn", "soybeans", "wheat", "rice"},
    "brazil_centrewest": {"soybeans", "corn", "wheat", "rice", "sorghum", "barley"},
    "us_southern_plains": {"wheat", "sorghum"}, "us_corn_belt": {"corn", "soybeans"},
    "central_america_dry_corridor": {"corn", "rice", "sorghum", "beans"},
}
FOOD_WORDS = ("maize", "corn", "wheat", "rice", "soy", "sorghum", "millet", "crop", "harvest",
              "food", "grain", "drought", "famine", "hunger", "price", "palm", "sugar", "rain",
              "flood", "farm", "livestock", "cereal")


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


def body(d: dict):
    return d.get("data", d)


def month_span(months: list[int]) -> str:
    if not months:
        return ""
    return MONTHS[months[0] - 1] + ("–" + MONTHS[months[-1] - 1] if len(months) > 1 else "")


def parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def main() -> int:
    enso = body(load("enso.json"))
    model = body(load("enso_model.json"))
    regions = body(load("enso_regions.json"))["regions"]
    cal = body(load("crop_calendars.json"))
    psd = body(load("usda_psd.json"))
    pink = body(load("worldbank_pink_sheet.json"))["series"]
    asap = body(load("asap.json"))
    gdacs = body(load("gdacs.json"))
    rtfp = body(load("rtfp.json"))
    relief = body(load("reliefweb_alerts.json")).get("events", [])
    news = body(load("enso_news.json")).get("items", [])
    restr = body(load("trade_restrictions.json"))
    exports_dest = body(load("comtrade_exports.json"))

    latest = enso["latest"]
    record = max(enso["history"], key=lambda h: h["anom"])
    cases = {
        "observed": {"oni": latest["anom"], "label": f"{latest['season']} {latest['year']} observed"},
        "record": {"oni": record["anom"], "label": f"DJF {record['year'] - 1}-{str(record['year'])[2:]} record"},
    }
    jan_year = latest["year"] + 1 if latest["season"] not in ("DJF", "JFM", "FMA") else latest["year"]
    now = datetime.now(timezone.utc)

    def price(crop: str):
        s = pink.get(PRICE_KEY.get(crop, ""), {})
        v = s.get("latest_value")
        return (v, s.get("label"), s.get("latest_month")) if isinstance(v, (int, float)) else (None, None, None)

    # The model's `signal` is a joint test on the El Niño AND La Niña slopes, so a
    # pair can carry it on its La Niña side alone. What "this El Niño implies"
    # needs is the El Niño slope, so it gets its own Benjamini-Hochberg q across
    # every fitted pair's p_nino, and a row is shown only when that q < 0.10.
    pn = sorted(((c["p_nino"], (iso, crop)) for iso, cs in model.items() for crop, c in cs.items()
                 if isinstance(c, dict) and isinstance(c.get("p_nino"), (int, float))), key=lambda t: t[0])
    q_nino, m_tests, running = {}, len(pn), 1.0
    for rank in range(m_tests, 0, -1):
        p_val, key = pn[rank - 1]
        running = min(running, p_val * m_tests / rank)
        q_nino[key] = running

    def status_of(iso: str, crop: str, c: dict) -> str:
        harvest = (cal.get(iso) or {}).get(crop, {}).get("harvest") or []
        if harvest and harvest[-1] < harvest[0] and c.get("alignment") == "djf_same_year":
            return "alignment_review"          # wrap-around harvest fitted to the wrong winter; refit pending
        if c.get("enso_specific") is False:
            return "shared_iod"
        if q_nino.get((iso, crop), 1) >= 0.10:
            return "no_el_nino_slope"
        return "shown"

    def fitted_rows(isos: list[str]) -> list[dict]:
        rows = []
        for iso in isos:
            for crop, c in (model.get(iso) or {}).items():
                if not (isinstance(c, dict) and c.get("signal")):
                    continue
                slope = c["yield_pct_per_oni_nino"]
                p = (psd.get(iso) or {}).get(PSD_KEY.get(crop, ""), {})
                if isinstance(p.get("production_kt"), (int, float)):
                    prod, prod_basis = p["production_kt"], f"USDA PSD {p.get('_year_production_kt', p.get('year'))}"
                else:
                    prod, prod_basis = c.get("mean_production_kt"), "FAOSTAT 2015–2024 mean"
                harvest = (cal.get(iso) or {}).get(crop, {}).get("harvest") or []
                hyear = jan_year if c.get("alignment") == "djf_same_year" else jan_year - 1
                usd, plabel, pmonth = price(crop)
                in_season = hyear < jan_year
                row = {
                    "iso": iso, "crop": crop, "slope_pct_per_oni": slope,
                    "q_value": c.get("q_value"), "p_nino": c.get("p_nino"), "q_nino": round(q_nino.get((iso, crop), 1), 4),
                    "status": status_of(iso, crop, c), "enso_specific": c.get("enso_specific", True),
                    # A harvest of the onset year is already in USDA's in-season
                    # estimate; the fit's share of it is shown as a percentage,
                    # not added again as tonnes.
                    "in_season": in_season,
                    # A harvest that runs Dec-Jan straddles the year. Paired with the DJF
                    # of its own year, the fit's year is its end; paired with the DJF that
                    # follows (djf_next_year), the fit's year is its start.
                    "harvest": ((MONTHS[harvest[0] - 1] + " " + str(hyear - (0 if c.get("alignment") == "djf_next_year" else 1)) + "–" + MONTHS[harvest[-1] - 1] + " " + str(hyear + (1 if c.get("alignment") == "djf_next_year" else 0)))
                                if len(harvest) > 1 and harvest[-1] < harvest[0]
                                else (month_span(harvest) + " " + str(hyear)).strip()),
                    "harvest_year": hyear,
                    "production_kt": prod, "production_basis": prod_basis,
                    "exports_kt": p.get("exports_kt"), "imports_kt": p.get("imports_kt"),
                }
                # The fit is in log-points (pct = 100 x log slope), so the change at
                # a given ONI is exp(b*ONI) - 1, not b*ONI: linear overshoots badly at
                # strong-event magnitudes. The 90% band uses the HAC standard error.
                b, se = slope / 100, (c.get("se_nino_pct") or 0) / 100
                for k, case in cases.items():
                    pct = (math.exp(b * case["oni"]) - 1) * 100
                    lo = (math.exp((b - 1.645 * se) * case["oni"]) - 1) * 100
                    hi = (math.exp((b + 1.645 * se) * case["oni"]) - 1) * 100
                    row[f"change_pct_{k}"] = round(pct, 1)
                    row[f"change_pct_{k}_90"] = [round(lo, 1), round(hi, 1)]
                    row[f"change_kt_{k}"] = (round(prod * pct / 100, 0) if isinstance(prod, (int, float)) and not in_season else None)
                    if usd and row[f"change_kt_{k}"] is not None:
                        row[f"value_usd_m_{k}"] = round(row[f"change_kt_{k}"] * 1000 * usd / 1e6, 0)
                if usd:
                    row["price"] = {"usd_per_t": usd, "series": plabel.strip(), "month": pmonth}
                rows.append(row)
        rows.sort(key=lambda r: abs(r["change_kt_record"] or 0), reverse=True)
        return rows

    def live(isos: list[str]) -> dict:
        s = set(isos)
        hot = [{"iso": i, "code": asap[i]["hotspot_code"], "date": asap[i].get("assessment_date")}
               for i in isos if i in asap and asap[i].get("hotspot_code")]
        alerts = [{"type": e.get("event_type_label"), "level": e.get("alert_level"),
                   "iso": [i for i in (e.get("affected_iso3") or [e.get("iso3")]) if i in s], "from": e.get("from_date")}
                  for e in gdacs.values() if e.get("is_current") and s.intersection(e.get("affected_iso3") or [e.get("iso3")])]
        prices = [{"iso": i, "pct": rtfp[i]["food_inflation_pct"], "as_of": rtfp[i].get("as_of")}
                  for i in isos if i in rtfp and isinstance(rtfp[i].get("food_inflation_pct"), (int, float))]
        prices.sort(key=lambda x: x["pct"], reverse=True)
        cut = now - timedelta(days=30)
        rel = [e for e in relief if e.get("iso3") in s and (parse_date(e.get("date")) or now) >= cut]
        stories = []
        for n in news:
            mentions = set(n.get("countries_mentioned") or [])
            text = (n.get("title") or "").lower()
            if mentions & s:
                stories.append({"title": n.get("title"), "source": n.get("source"), "url": n.get("url"),
                                "published_at": n.get("published_at"),
                                "food": any(w in text for w in FOOD_WORDS)})
        stories.sort(key=lambda x: x.get("published_at") or "", reverse=True)
        stories.sort(key=lambda x: not x["food"])
        rs = [{"iso": r["iso"], "commodity": r.get("commodity"), "measure": r.get("measure"),
               "status": r.get("status"), "ends": r.get("ends_date")} for r in restr if r.get("iso") in s]
        return {"asap": hot, "gdacs": alerts, "rtfp": prices,
                "relief_30d": [{"iso": e.get("iso3"), "title": e.get("title"), "date": e.get("date"), "url": e.get("url")} for e in rel][:6],
                "relief_30d_count": len(rel), "stories": stories[:6], "stories_count": len(stories),
                "restrictions": rs}

    out_regions = []
    for r in regions:
        out_regions.append({
            "id": r["id"], "label": r["label"], "iso3": r["iso3"], "sign": r.get("sign"),
            "effect_direction": r.get("effect_direction"), "damage_season": r.get("damage_season"),
            "lag_months": r.get("lag_months"), "confidence": r.get("confidence"),
            "precedent": r.get("quantified"), "sources": r.get("sources", [])[:3],
            "fitted": [dict(f, in_region=f["crop"] in REGION_CROPS.get(r["id"], set()))
                       for f in fitted_rows(r["iso3"])],
            "live": live(r["iso3"]),
        })

    # Every signal pair in the model, not only the region-listed crops, so a
    # country the Harvests list names (Egypt, Iran, Pakistan, Angola) is either
    # shown or has a stated reason for not being shown.
    region_of = {}
    for r in regions:
        for iso in r["iso3"]:
            for crop in REGION_CROPS.get(r["id"], set()):
                region_of.setdefault((iso, crop), r["label"])
    rows_all = [dict(f, region=region_of.get((f["iso"], f["crop"]))) for f in fitted_rows(sorted(model))]

    # ── Who pays: a stated accounting, not a model ─────────────────────────
    # A producer's shortfall first cuts its exports (up to what it exports),
    # and those lost exports fall on its buyers in proportion to their Comtrade
    # share; any shortfall beyond its exports is extra import need at home.
    # Priced at today's World Bank price. Stocks are reported as a buffer in
    # weeks of use, not subtracted, because how much is drawn is a policy choice.
    COMTRADE_KEY = {"corn": "maize", "wheat": "wheat", "rice": "rice", "soybeans": "soybeans"}
    chain, seen_chain = [], set()
    for reg in out_regions:
        for f in reg["fitted"]:
            key = (f["iso"], f["crop"])
            if key in seen_chain or not f["in_region"] or f["status"] != "shown" or f["in_season"]:
                continue
            loss = -(f["change_kt_record"] or 0)
            if loss < 150:
                continue
            seen_chain.add(key)
            p = (psd.get(f["iso"]) or {}).get(PSD_KEY.get(f["crop"], ""), {})
            usd = (f.get("price") or {}).get("usd_per_t")
            if not p:
                # No USDA balance for the pair (e.g. Brazilian sorghum): the trade
                # split is unknown, so nothing is allocated rather than "exports nothing".
                chain.append({"iso": f["iso"], "crop": f["crop"], "harvest": f["harvest"], "loss_kt": round(loss),
                              "balance": "not pulled", "exports_kt": None, "lost_exports_kt": None, "buyers": [],
                              "extra_import_kt": None, "extra_import_usd_m": None, "lost_exports_usd_m": None,
                              "stocks_kt": None, "stocks_weeks": None, "consumption_kt": None, "price": f.get("price")})
                continue
            exports, stocks, use = p.get("exports_kt") or 0, p.get("stocks_kt"), p.get("consumption_kt")
            lost_exports = min(loss, exports)
            extra_import = loss - lost_exports
            dest = ((exports_dest.get(f["iso"]) or {}).get(COMTRADE_KEY.get(f["crop"], "")) or {}).get("top_destinations") or []
            shares = [d for d in dest if isinstance(d.get("share_pct"), (int, float))]
            # A buyer cannot lose more than it normally imports (USDA PSD); what the
            # value shares would push past that goes to "other buyers".
            buyers, other = [], lost_exports
            for d in shares:
                kt = lost_exports * d["share_pct"] / 100
                cap = ((psd.get(d["iso3"]) or {}).get(PSD_KEY.get(f["crop"], "")) or {}).get("imports_kt")
                if isinstance(cap, (int, float)) and cap > 0:
                    kt = min(kt, cap)
                if kt >= 1:
                    buyers.append({"iso": d["iso3"], "share_pct": d["share_pct"], "kt": round(kt),
                                   "capped_at_imports": isinstance(cap, (int, float)) and cap > 0 and kt == cap})
                    other -= kt
            # Five largest after the cap; the rest joins "other buyers".
            buyers.sort(key=lambda x: -x["kt"])
            other += sum(x["kt"] for x in buyers[5:])
            buyers = buyers[:5]
            chain.append({
                "iso": f["iso"], "crop": f["crop"], "harvest": f["harvest"], "loss_kt": round(loss),
                "exports_kt": exports, "lost_exports_kt": round(lost_exports),
                "buyers": buyers, "other_buyers_kt": round(other) if other >= 1 else 0,
                "buyers_basis": "UN Comtrade export shares by value, capped at each buyer's USDA PSD imports" if shares else None,
                "extra_import_kt": round(extra_import),
                "extra_import_usd_m": round(extra_import * 1000 * usd / 1e6) if usd else None,
                "lost_exports_usd_m": round(lost_exports * 1000 * usd / 1e6) if usd else None,
                "stocks_kt": stocks, "stocks_weeks": round(stocks / use * 52, 1) if stocks and use else None,
                "consumption_kt": use,
                "price": f.get("price"),
            })
    chain.sort(key=lambda c: -c["loss_kt"])
    # New import demand per crop: producers' extra imports plus buyers replacing
    # lost exports from elsewhere. Pairs with no balance are left out and named.
    totals = {}
    for c in chain:
        t = totals.setdefault(c["crop"], {"crop": c["crop"], "extra_import_kt": 0, "replaced_kt": 0, "usd_m": 0,
                                          "priced": True, "not_pulled": []})
        if c.get("balance") == "not pulled":
            t["not_pulled"].append(c["iso"])
            continue
        t["extra_import_kt"] += c["extra_import_kt"]
        t["replaced_kt"] += c["lost_exports_kt"]
        usd = (c.get("price") or {}).get("usd_per_t")
        if usd:
            t["usd_m"] += round((c["extra_import_kt"] + c["lost_exports_kt"]) * 1000 * usd / 1e6)
        else:
            t["priced"] = False
    who_pays_totals = [dict(t, total_kt=t["extra_import_kt"] + t["replaced_kt"]) for t in totals.values()
                       if t["extra_import_kt"] + t["replaced_kt"] > 0]

    by_crop: dict[str, dict] = {}
    seen = set()
    for reg in out_regions:
        for f in reg["fitted"]:
            key = (f["iso"], f["crop"])
            if key in seen or f["status"] != "shown" or f["in_season"]:
                continue
            seen.add(key)
            c = by_crop.setdefault(f["crop"], {"crop": f["crop"], "loss_kt_record": 0, "gain_kt_record": 0, "countries": []})
            kt = f["change_kt_record"] or 0
            c["loss_kt_record" if kt < 0 else "gain_kt_record"] += kt
            c["countries"].append({"iso": f["iso"], "change_kt_record": kt, "harvest": f["harvest"]})
    for c in by_crop.values():
        c["countries"].sort(key=lambda x: x["change_kt_record"])

    write_json("enso_outlook.json", {
        "cases": cases, "harvest_winter": f"DJF {jan_year - 1}-{str(jan_year)[2:]}",
        "method": "exp(fitted log-yield slope × ONI) − 1, × production, at two ONI values the record contains; value at stake = tonnes × latest World Bank price. No world-price model, no probability weighting.",
        "honesty": "Conditional estimates from a linear fit without out-of-sample validation. CPC's OND 2026 RONI median (+2.67) sits above the fit's strongest winter (ONI +2.5, 2015-16); the fit has no data beyond it, so a stronger winter could bring larger changes than these.",
        "regions": out_regions,
        "crops": sorted(by_crop.values(), key=lambda c: c["loss_kt_record"]),
        "rows_all": rows_all,
        "who_pays": chain, "who_pays_totals": who_pays_totals,
        "who_pays_case": "record",
        "who_pays_rule": "At the fit's strongest winter (ONI +2.5). Shortfall cuts exports first, allocated to buyers by Comtrade value share; any remainder is extra import need. Priced at the latest World Bank price. Stocks shown, not subtracted.",
    }, source="Derived: enso_regions, enso_model, crop_calendars, USDA PSD, World Bank Pink Sheet; live signals from JRC ASAP, GDACS, World Bank RTFP, ReliefWeb, El Niño news feed, trade_restrictions",
       notes="Tonnes first; value at stake is tonnes × latest World Bank price, not a price forecast.", status="ok")
    print(f"[OK] enso_outlook: {len(out_regions)} regions, {sum(len(r['fitted']) for r in out_regions)} fitted rows, "
          f"cases {cases['observed']['oni']} / {cases['record']['oni']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
