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
                    prod, prod_basis = c.get("mean_production_kt"), "FAOSTAT 1961–2024 mean"
                harvest = (cal.get(iso) or {}).get(crop, {}).get("harvest") or []
                hyear = jan_year if c.get("alignment") == "djf_same_year" else jan_year - 1
                usd, plabel, pmonth = price(crop)
                row = {
                    "iso": iso, "crop": crop, "slope_pct_per_oni": slope,
                    "q_value": c.get("q_value"), "enso_specific": c.get("enso_specific", True),
                    # A harvest that runs Dec-Jan straddles the year; the fit's year is its end.
                    "harvest": ((MONTHS[harvest[0] - 1] + " " + str(hyear - 1) + "–" + MONTHS[harvest[-1] - 1] + " " + str(hyear))
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
                    row[f"change_kt_{k}"] = round(prod * pct / 100, 0) if isinstance(prod, (int, float)) else None
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

    by_crop: dict[str, dict] = {}
    seen = set()
    for reg in out_regions:
        for f in reg["fitted"]:
            key = (f["iso"], f["crop"])
            if key in seen or not f["enso_specific"]:
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
        "honesty": "Conditional estimates from a linear fit without out-of-sample validation. CPC's OND 2026 RONI median (+2.67) is beyond the fitted range; figures at the record ONI are the largest the fit can support, not a ceiling on the event.",
        "regions": out_regions,
        "crops": sorted(by_crop.values(), key=lambda c: c["loss_kt_record"]),
    }, source="Derived: enso_regions, enso_model, crop_calendars, USDA PSD, World Bank Pink Sheet; live signals from JRC ASAP, GDACS, World Bank RTFP, ReliefWeb, El Niño news feed, trade_restrictions",
       notes="Tonnes first; value at stake is tonnes × latest World Bank price, not a price forecast.", status="ok")
    print(f"[OK] enso_outlook: {len(out_regions)} regions, {sum(len(r['fitted']) for r in out_regions)} fitted rows, "
          f"cases {cases['observed']['oni']} / {cases['record']['oni']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
