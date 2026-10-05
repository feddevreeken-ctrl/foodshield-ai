#!/usr/bin/env python3
"""
build_enso_outlook.py — what this El Nino implies, by region, crop, country and harvest.

Reads what the pipeline already holds and writes data/enso_outlook.json:

  - data/enso_regions.json   published teleconnection regions (curated, cited)
  - data/enso_model.json     fitted yield slopes, log points per ONI, and the El Nino
                             test per pair (nino_signal, q_nino) (build_enso_model.py)
  - data/crop_calendars.json harvest months per country-crop
  - data/enso_neutral.json   ENSO-neutral trend yield + area basis per pair (build_enso_neutral.py, hand-run)
  - data/usda_psd.json       current production / imports / exports
  - data/worldbank_pink_sheet.json  latest world prices, $/t
  - data/enso.json           observed ONI (latest season) and the record DJF
  - data/asap.json, gdacs.json, rtfp.json, reliefweb_alerts.json,
    enso_news.json, trade_restrictions.json   what is being reported now

TWO ANCHORED CASES, NO EXTRAPOLATION
------------------------------------
The fit is linear in ONI and was trained on 1961-2024 winters, the largest of
which is 2015-16 at +2.5. CPC's own RONI outlook for the coming DJF may go past
anything the fit has seen. Its ONI equivalent is a range, not one number:
refresh_cpc_roni_outlook.py adds the June-August ONI-RONI gap plus how that gap
moved from June-August to December-February in past El Ninos
(data/enso_strengths.json, roni_outlook[].oni_equiv). The honesty line states
the RONI median and that ONI range. So this does not weight a probability
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
from _common import stamp_inputs, write_json  # noqa: E402
from pipeline_dag import MATURITY  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PSD_KEY = {"corn": "corn", "wheat": "wheat", "rice": "rice", "soybeans": "soybeans",
           "sorghum": "sorghum", "barley": "barley", "millet": "millet"}
# USDA PSD files each country-crop under a marketing year (MY) that is not
# always the harvest year. Harvest year = MY + offset. Checked against the PSD
# bulk production series on 2026-09-24 using known drought harvests:
#   ZAF corn MY2023 = 13,425 kt (2024 drought harvest), MY2015 = 8,214 (2016)  -> +1
#   ZAF sorghum MY2015 = 71 kt (2016 drought harvest)                           -> +1
#   ZWE corn MY2024 = 635 kt (2024 drought harvest), MY2016 = 512 (2016)       -> 0
#   ZWE sorghum MY2024 = 82 kt (2024 drought harvest)                          -> 0
#   BRA rice MY2024 = 8,675 kt (2025 record crop), MY2015 = 7,210 (2016)       -> +1
#   BRA sorghum MY2015 = 1,032 kt (2016 safrinha drought), same year as corn   -> +1
#   BRA barley: winter crop filed like Brazil wheat (MY2022 = 2022 record)     -> 0
#   USA wheat, USA soybeans: US MY starts at harvest (Jun / Sep)               -> 0
#   IND millet MY2015 = 10,280 kt, MY2018 = 10,236 (poor-monsoon kharif)      -> 0
#   AGO rice: series too flat to check; Angola maize MY2024 = 2,200 kt is the
#   2024 drought harvest, so taken as the same year (inferred)               -> 0
# Pairs not listed get no harvest label and base_is_target_harvest = null.
PSD_HARVEST_OFFSET = {
    ("ZAF", "corn"): 1, ("ZAF", "sorghum"): 1, ("ZWE", "corn"): 0, ("ZWE", "sorghum"): 0,
    ("BRA", "rice"): 1, ("BRA", "sorghum"): 1, ("BRA", "barley"): 0, ("USA", "wheat"): 0,
    ("USA", "soybeans"): 0, ("IND", "millet"): 0, ("AGO", "rice"): 0,
}
PSD_INFERRED = {("AGO", "rice"), ("BRA", "barley")}
# The shock is applied to an ENSO-neutral baseline (enso_neutral.json, build_enso_neutral.py),
# never to USDA's figure, so an allowance USDA already made for El Niño is not counted twice.
NEUTRAL_NOTE = ("Change is applied to an ENSO-neutral trend baseline, not to USDA's estimate, "
                "so any allowance USDA already made for El Niño is not counted twice.")
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
    # 2026-10-05 court: Brazil's wheat, rice and barley grow in the south (Parana, Rio Grande do Sul), where El Nino
    # usually brings more rain, not in the Centre-West drought region; data/enso_regions.json pair_regions already
    # leaves BRA|rice and BRA|barley without a region.
    "brazil_centrewest": {"soybeans", "corn", "sorghum"},
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


def honesty_line(jan_year: int, record: dict) -> str:
    """CPC's DJF RONI median and its season-matched ONI equivalent, as a range
    (roni_outlook[].oni_equiv, refresh_cpc_roni_outlook.py). Never the JJA gap alone:
    the ONI-RONI gap moves between June-August and December-February."""
    rec = f"ONI {record['anom']:+.1f} ({record['year'] - 1}-{str(record['year'])[2:]})"
    winter = f"{jan_year - 1}-{str(jan_year)[2:]}"
    try:
        djf = next(o for o in body(load("enso_strengths.json")).get("roni_outlook") or []
                   if o.get("label") == f"DJF {winter}")
        med = float(djf["median"])
    except (StopIteration, KeyError, OSError, ValueError, TypeError):
        return ("Estimates from a linear fit. The fit is on December–February; CPC's RONI outlook for "
                f"December–February {winter} was not available this run, so how it compares with the strongest "
                f"winter in the fit, {rec}, is not stated.")
    eq = djf.get("oni_equiv") or {}
    head = (f"Estimates from a linear fit on December–February ONI. CPC's December–February {winter} median, "
            f"RONI {med:+.2f}, ")
    if not all(isinstance(eq.get(k), (int, float)) for k in ("median_lo", "median_hi")):
        return head + (f"has no ONI equivalent this run, so how it compares with the strongest winter in the fit, "
                       f"{rec}, is not stated.")
    lo, hi, top = eq["median_lo"], eq["median_hi"], record["anom"]
    line = head + f"is about ONI {lo:+.1f} to {hi:+.1f} given how the ONI–RONI gap moved in past El Niños. "
    if lo > top:
        return line + f"All of that is above the strongest winter in the fit, {rec}. Changes could be larger than those shown."
    if hi > top:
        return line + f"The strongest winter in the fit is {rec}. A winter above it could bring larger changes than those shown."
    return line + f"That is within the fit, whose strongest winter is {rec}."


def neutral_kt_at(nr: dict | None, h: int, usda: bool = True):
    """ENSO-neutral production (kt) for harvest year h, on USDA's basis where the pair's USDA year
    mapping is checked, else on FAOSTAT's. Yield = the model's own trend (no ONI term), area = USDA's
    area for h if it has one (USDA basis), else the latest FAOSTAT area. Returns (kt, basis, area_ha)."""
    if not nr:
        return None, None, None
    y_kg = math.exp(nr["anchor_log"] + nr["slope_log_per_yr"] * (h - nr["anchor_year"]))
    if usda and nr.get("usda_mapped") and nr.get("kp") and nr.get("ka"):
        ua = (nr.get("usda_area_kha_by_harvest") or {}).get(str(h))
        if ua:
            return y_kg * ua * 1000 * nr["kp"] / nr["ka"] / 1e6, "USDA area", ua * 1000
        return y_kg * nr["fao_area_ha"] * nr["kp"] / 1e6, f"FAOSTAT {nr['fao_area_year']} area", nr["fao_area_ha"]
    return y_kg * nr["fao_area_ha"] / 1e6, f"FAOSTAT {nr['fao_area_year']} area, FAOSTAT units", nr["fao_area_ha"]


def usda_reading(gap: float | None, obs90: list | None) -> tuple[str | None, str | None]:
    """Does USDA's figure already sit where the fit puts an El Niño at today's ONI?"""
    if gap is None:
        return None, None
    if gap >= 0:
        return "no_cut", "USDA is at or above the neutral baseline: it shows no cut for El Niño."
    if obs90 and obs90[0] <= gap <= obs90[1]:
        return "consistent", "USDA is below neutral by about what the fit predicts at today's ONI: it likely already allows for the event."
    if obs90 and gap > obs90[1]:
        return "shallower", "USDA is below neutral, but by less than the fit predicts at today's ONI: it may allow for part of the event."
    return "deeper", "USDA is below neutral by more than the fit predicts at today's ONI: other causes are likely in it."


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


def comtrade_year_of(doc: dict) -> int | None:
    """The trade year behind comtrade_exports.json. The merged file carries no
    year field, so fall back to the year its pipeline requests (config.YEAR)."""
    import re
    m = re.search(r"\b(20\d\d)\b", " ".join(str((doc.get("_meta") or {}).get(k, "")) for k in ("source", "notes", "year")))
    if m:
        return int(m.group(1))
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "trade_pipeline"))
        import config as trade_config  # noqa: E402
        return int(trade_config.YEAR)
    except Exception:
        return None


def allocate_buyers(total: float, shares: list[dict], cap_of, top: int = 5) -> list[dict]:
    """Split `total` kt over named buyers by value share. A buyer cannot lose more
    than it normally imports (USDA PSD); the excess a cap removes is re-spread pro
    rata over the named buyers still under their caps, repeated until none is
    left or every buyer is capped. The five largest are returned with integer kt
    whose sum never exceeds round(total); the caller books the rest as others."""
    alloc = {d["iso3"]: total * d["share_pct"] / 100 for d in shares}
    caps = {d["iso3"]: cap_of(d["iso3"]) for d in shares}
    caps = {k: v for k, v in caps.items() if isinstance(v, (int, float)) and v > 0}
    capped: set = set()
    for _ in range(len(shares) + 1):
        excess = 0.0
        for iso3, kt in alloc.items():
            if iso3 in caps and kt > caps[iso3]:
                excess += kt - caps[iso3]
                alloc[iso3] = caps[iso3]
                capped.add(iso3)
            elif iso3 in caps and kt >= caps[iso3]:
                capped.add(iso3)
        open_ = [d for d in shares if d["iso3"] not in capped]
        if excess <= 1e-9 or not open_:
            break
        w = sum(d["share_pct"] for d in open_)
        for d in open_:
            alloc[d["iso3"]] += excess * d["share_pct"] / w
    rows = [{"iso": d["iso3"], "share_pct": d["share_pct"], "kt": round(alloc[d["iso3"]]),
             "capped_at_imports": d["iso3"] in capped} for d in shares if alloc[d["iso3"]] >= 1]
    rows.sort(key=lambda x: -x["kt"])
    rows = rows[:top]
    over = sum(r["kt"] for r in rows) - round(total)
    while over > 0 and rows:            # rounding up can overshoot by a tonne or two
        r = max(rows, key=lambda x: x["kt"]); take = min(over, r["kt"]); r["kt"] -= take; over -= take
    return rows


# Quality tiers for each outlook row, computed from the FORWARD portfolio hindcast only (enso_portfolio_hindcast.json by_pair):
# each pair's El Nino term refitted on earlier harvests, scored in tonnes against the no-El-Nino yardstick.
# One-sided binomial chance of at least k right directions in n winters if the direction were a coin flip.
TIER_RULE = ("Computed from the past-winter test only (each pair refitted on earlier harvests, scored in tonnes against the no-El Niño yardstick). "
             "Supported: direction right with a coin-flip chance of 10% or less (6 of 7 winters), tonnes error below the yardstick's, "
             "and at most 1 false alarm (a predicted fall that became a rise). "
             "Moderate: direction right with a coin-flip chance of 25% or less (5 of 7) and tonnes error below the yardstick's. "
             "Exploratory: anything else, or not scored forward. Exploratory rows are kept but are not headline figures. "
             "The pairs were chosen on the full record, so even Supported is an upper bound on skill.")


def binom_tail(k: int, n: int) -> float:
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def model_quality(pair: str, status: str, by_pair: dict) -> dict:
    s = by_pair.get(pair)
    if status != "shown" or not s or not s.get("events"):
        return {"tier": "exploratory", "headline": False,
                "basis": {"events": 0, "sign_right": None, "beats_yardstick": None, "false_alarms": None},
                "reason": "not scored", "rule": "Not in the past-winter test: its El Niño slope does not pass the test that puts a pair in the outlook."}
    n, k = s["events"], s["sign_right"]
    p = binom_tail(k, n)
    beats = bool(s["mae_kt"] < s["mae_yardstick_kt"])
    fa = s["false_alarms"]
    if p <= 0.10 and beats and fa <= 1:
        tier, reason = "supported", "direction right in " + str(k) + " of " + str(n)
    elif p <= 0.25 and beats:
        tier, reason = "moderate", "direction right in " + str(k) + " of " + str(n)
    else:
        tier = "exploratory"
        reason = ("direction right in only " + str(k) + " of " + str(n)) if p > 0.25 else ("error above the no-El Niño yardstick")
    return {"tier": tier, "headline": tier != "exploratory",
            "basis": {"events": n, "sign_right": k, "beats_yardstick": beats, "false_alarms": fa,
                      "mae_kt": s["mae_kt"], "mae_yardstick_kt": s["mae_yardstick_kt"], "coin_flip_chance": round(p, 3)},
            "reason": reason,
            "rule": (f"Past-winter test, {n} winters: direction right {k} of {n}, tonnes error "
                     f"{'below' if beats else 'above'} the no-El Niño yardstick, {fa} false alarm{'s' if fa != 1 else ''}.")}


def main() -> int:
    try:
        by_pair = body(load("enso_portfolio_hindcast.json")).get("by_pair") or {}
    except (OSError, ValueError):
        by_pair = {}
    enso = body(load("enso.json"))
    model_doc = load("enso_model.json")
    model = body(model_doc)
    n_fitted = ((model_doc.get("_meta") or {}).get("counts") or {}).get("pairs_fitted")
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
    try:
        neutral_doc = load("enso_neutral.json")
        neutral = body(neutral_doc).get("pairs") or {}
    except (OSError, ValueError):
        neutral_doc, neutral = {}, {}

    latest = enso["latest"]
    record = max(enso["history"], key=lambda h: h["anom"])
    cases = {
        "observed": {"oni": latest["anom"], "label": f"{latest['season']} {latest['year']} observed"},
        "record": {"oni": record["anom"], "label": f"DJF {record['year'] - 1}-{str(record['year'])[2:]} record"},
    }
    # The fit is not extrapolated: an observed season stronger than the record
    # winter is reported at the record value, flagged, with the raw ONI kept.
    if latest["anom"] > record["anom"]:
        cases["observed"].update(oni=record["anom"], oni_raw=latest["anom"], clamped=True)
    else:
        cases["observed"]["clamped"] = False
    jan_year = latest["year"] + 1 if latest["season"] not in ("DJF", "JFM", "FMA") else latest["year"]
    now = datetime.now(timezone.utc)

    def price(crop: str):
        s = pink.get(PRICE_KEY.get(crop, ""), {})
        v = s.get("latest_value")
        return (v, s.get("label"), s.get("latest_month")) if isinstance(v, (int, float)) else (None, None, None)

    # The model's `signal` is a joint test on the El Niño AND La Niña slopes, so a
    # pair can carry it on its La Niña side alone. What "this El Niño implies"
    # needs is the El Niño slope, so the model gives it its own Benjamini-Hochberg
    # q across ALL fitted pairs' El Niño-slope p values (q_nino, nino_signal), and
    # a row is shown only when that passes. Until 2026-09-28 this q was computed
    # here over only the pairs the joint test had already kept (audit F3).
    def status_of(iso: str, crop: str, c: dict) -> str:
        harvest = (cal.get(iso) or {}).get(crop, {}).get("harvest") or []
        if harvest and harvest[-1] < harvest[0] and c.get("alignment") == "djf_same_year":
            return "alignment_review"          # wrap-around harvest fitted to the wrong winter; refit pending
        # The El Niño test comes first: a pair whose El Niño slope fails is not "shared with the IOD" (audit N2).
        if not c.get("nino_signal"):
            return "no_el_nino_slope"
        if c.get("enso_specific") is False:
            return "shared_iod"
        return "shown"

    rec_oni = cases["record"]["oni"]

    def fitted_rows(isos: list[str]) -> list[dict]:
        # Every pair with the joint signal or its own El Niño signal: shown, or listed with its reason.
        rows = []
        for iso in isos:
            for crop, c in (model.get(iso) or {}).items():
                if not (isinstance(c, dict) and (c.get("signal") or c.get("nino_signal"))):
                    continue
                slope = c["yield_pct_per_oni_nino"]
                p = (psd.get(iso) or {}).get(PSD_KEY.get(crop, ""), {})
                base_note, base_harvest = None, None
                hyear = jan_year if c.get("alignment") == "djf_same_year" else jan_year - 1
                if isinstance(p.get("production_kt"), (int, float)):
                    my = p.get("_year_production_kt", p.get("year"))
                    off = PSD_HARVEST_OFFSET.get((iso, crop))
                    base_harvest = my + off if isinstance(my, int) and off is not None else None
                    milled = ", milled" if crop == "rice" else ""
                    prod = p["production_kt"]
                    prod_basis = (f"USDA {my}/{str(my + 1)[2:]} estimate{milled}"
                                  + (f" ({base_harvest} harvest)" if base_harvest else ""))
                    prev = p.get("production_kt_prev")
                    my_prev = p.get("_year_production_kt_prev")
                    lab = (lambda y: str(y + off)) if off is not None else (lambda y: f"{y}/{str(y + 1)[2:]}")
                    # A single year that is far from the last one moves the tonnes; say so on the row.
                    if isinstance(prev, (int, float)) and prev > 0 and abs(prod / prev - 1) >= 0.15 and isinstance(my_prev, int):
                        base_note = (f"{lab(my)} crop {abs(round((prod / prev - 1) * 100))}% "
                                     f"{'below' if prod < prev else 'above'} {lab(my_prev)}; "
                                     f"on the {lab(my_prev)} crop the change would be "
                                     f"{'+' if slope > 0 else '−'}{abs(round(prev * (math.exp(slope / 100 * rec_oni) - 1) / 1000, 1))} Mt at ONI {rec_oni:+.1f}")
                else:
                    prod, prod_basis = c.get("mean_production_kt"), "FAOSTAT 2015–2024 mean"
                    base_note = "a ten-year mean, not this year’s crop; a fast-growing crop is understated"
                harvest = (cal.get(iso) or {}).get(crop, {}).get("harvest") or []
                usd, plabel, pmonth = price(crop)
                in_season = hyear < jan_year
                row = {
                    "iso": iso, "crop": crop, "slope_pct_per_oni": slope,
                    "q_value": c.get("q_value"), "p_nino": c.get("p_nino"), "q_nino": round(c.get("q_nino", 1), 4),
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
                    "production_kt": prod, "production_basis": prod_basis, "production_note": base_note,
                    # True when USDA's production figure is its estimate of the very
                    # harvest the row models; null when the PSD year mapping is unchecked.
                    "base_is_target_harvest": (base_harvest == hyear) if base_harvest is not None else None,
                    "production_base_harvest": base_harvest,
                    "production_base_inferred": ((iso, crop) in PSD_INFERRED) if base_harvest is not None else None,
                    "exports_kt": p.get("exports_kt"), "imports_kt": p.get("imports_kt"),
                }
                # The fit is in log-points (pct = 100 x log slope), so the change at
                # a given ONI is exp(b*ONI) - 1, not b*ONI: linear overshoots badly at
                # strong-event magnitudes. The 90% band uses the HAC standard error.
                # The change is applied to the ENSO-neutral baseline (enso_neutral.json),
                # not to USDA's figure, which may already allow for the event. Without a
                # neutral baseline the row falls back to the production figure it had.
                nr = neutral.get(f"{iso}/{crop}")
                n_kt, n_area_basis, n_area = neutral_kt_at(nr, hyear)
                n_alt = None
                if nr and n_kt is not None:
                    alt = dict(nr, anchor_year=nr["alt_anchor_year"], anchor_log=nr["alt_anchor_log"])
                    n_alt = neutral_kt_at(alt, hyear)[0]
                # Sensitivity when USDA's latest area is not the FAOSTAT area the baseline uses (USDA's own harvest
                # is another year): the same trend yield on USDA's most recent area.
                n_uarea = None
                ua_last = ((nr or {}).get("usda_area_kha_by_harvest") or {}).get(str(base_harvest)) if base_harvest else None
                if nr and nr.get("usda_mapped") and ua_last and n_kt is not None and base_harvest != hyear:
                    y_kg = math.exp(nr["anchor_log"] + nr["slope_log_per_yr"] * (hyear - nr["anchor_year"]))
                    n_uarea = y_kg * ua_last * 1000 * nr["kp"] / nr["ka"] / 1e6
                usda_kt = prod if p.get("production_kt") is not None and isinstance(prod, (int, float)) else None
                same = (base_harvest == hyear) if base_harvest is not None else None
                n_usda = neutral_kt_at(nr, base_harvest)[0] if nr and base_harvest is not None else None
                gap = round((usda_kt / n_usda - 1) * 100, 1) if usda_kt and n_usda and nr.get("usda_mapped") else None
                base = n_kt if n_kt is not None else prod
                row.update({
                    "neutral_kt": round(n_kt, 0) if n_kt is not None else None,
                    "neutral_kt_alt": round(n_alt, 0) if n_alt is not None else None,
                    "neutral_kt_on_usda_area": round(n_uarea, 0) if n_uarea is not None else None,
                    "neutral_harvest": hyear, "neutral_area_basis": n_area_basis,
                    "neutral_area_ha": round(n_area) if n_area else None,
                    "usda_kt": usda_kt, "usda_harvest": base_harvest, "usda_same_harvest": same,
                    "usda_area_kha": ((nr or {}).get("usda_area_kha_by_harvest") or {}).get(str(base_harvest)) if base_harvest else None,
                    "neutral_at_usda_harvest_kt": round(n_usda, 0) if n_usda is not None else None,
                    "usda_vs_neutral_pct": gap,
                    "baseline": "neutral" if n_kt is not None else "usda_or_mean",
                })
                b, se = slope / 100, (c.get("se_nino_pct") or 0) / 100
                for k, case in cases.items():
                    pct = (math.exp(b * case["oni"]) - 1) * 100
                    lo = (math.exp((b - 1.645 * se) * case["oni"]) - 1) * 100
                    hi = (math.exp((b + 1.645 * se) * case["oni"]) - 1) * 100
                    row[f"change_pct_{k}"] = round(pct, 1)
                    row[f"change_pct_{k}_90"] = [round(lo, 1), round(hi, 1)]
                    ok = isinstance(base, (int, float)) and not in_season
                    row[f"change_kt_{k}"] = round(base * pct / 100, 0) if ok else None
                    # The old figure, on USDA's (or the mean) production, kept so the two can be compared.
                    row[f"change_kt_{k}_on_usda"] = (round(prod * pct / 100, 0) if isinstance(prod, (int, float)) and not in_season else None)
                    row[f"scenario_kt_{k}"] = round(base * (1 + pct / 100), 0) if ok and n_kt is not None else None
                    row[f"scenario_kt_{k}_90"] = ([round(base * (1 + lo / 100), 0), round(base * (1 + hi / 100), 0)]
                                                  if ok and n_kt is not None else None)
                    if usd and row[f"change_kt_{k}"] is not None:
                        row[f"value_usd_m_{k}"] = round(row[f"change_kt_{k}"] * 1000 * usd / 1e6, 0)
                # Headline aliases: the record-winter scenario.
                row["scenario_kt"], row["scenario_kt_90"] = row["scenario_kt_record"], row["scenario_kt_record_90"]
                key, txt = usda_reading(gap, row["change_pct_observed_90"])
                row["usda_reading"], row["usda_reading_text"] = key, txt
                # Both readings for the same harvest: (A) USDA ignores the event: scenario is the neutral baseline minus the
                # fitted change; (B) USDA already allows for it: USDA is itself the event figure.
                row["scenario_vs_usda_pct"] = (round((row["scenario_kt"] / usda_kt - 1) * 100, 1)
                                               if same and usda_kt and row["scenario_kt"] is not None else None)
                if usd:
                    row["price"] = {"usd_per_t": usd, "series": plabel.strip(), "month": pmonth}
                mq = model_quality(f"{iso}/{crop}", row["status"], by_pair)
                row["model_quality"] = {k: mq[k] for k in ("tier", "basis", "reason", "rule")}
                row["headline"] = mq["headline"]
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
            "id": r["id"], "label": r["label"], "iso3": r["iso3"], "sign": r.get("sign"), "rain": r.get("rain"),
            "effect_direction": r.get("effect_direction"), "damage_season": r.get("damage_season"),
            # None (not []) for a region with no crop list, so readers fall back to the label (Sahel · millet).
            "crops": sorted(REGION_CROPS[r["id"]]) if r["id"] in REGION_CROPS else None,
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
    comtrade_year = comtrade_year_of(load("comtrade_exports.json"))
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
            shares = [d for d in dest if isinstance(d.get("share_pct"), (int, float)) and d["share_pct"] > 0]
            buyers = allocate_buyers(lost_exports, shares, lambda iso3: (
                ((psd.get(iso3) or {}).get(PSD_KEY.get(f["crop"], "")) or {}).get("imports_kt")))
            lost_exports_kt = round(lost_exports)
            b90 = f.get("change_pct_record_90") or [f["change_pct_record"], f["change_pct_record"]]
            prod = f.get("neutral_kt") or f.get("production_kt") or 0     # the loss is taken from the neutral baseline
            chain.append({
                "iso": f["iso"], "crop": f["crop"], "harvest": f["harvest"], "loss_kt": round(loss),
                # The loss at the 90% band ends of the record case, and at today's ONI.
                "loss_kt_90": sorted([round(-b90[0] * prod / 100), round(-b90[1] * prod / 100)]),
                "loss_kt_observed": round(-(f.get("change_kt_observed") or 0)),
                "exports_kt": exports, "lost_exports_kt": lost_exports_kt,
                # "Other buyers" is what the named buyers leave, so the parts add to the whole.
                "buyers": buyers, "other_buyers_kt": lost_exports_kt - sum(b["kt"] for b in buyers),
                "buyers_basis": ("UN Comtrade export shares by value, capped at each buyer's USDA PSD imports; "
                                 "what a cap removes is spread over the uncapped named buyers") if shares else None,
                "comtrade_year": comtrade_year if shares else None,
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

    path = write_json("enso_outlook.json", {
        "cases": cases, "harvest_winter": f"DJF {jan_year - 1}-{str(jan_year)[2:]}",
        "method": ("exp(fitted log-yield slope × ONI) − 1, applied to an ENSO-neutral production baseline, at two ONI values the record contains. "
                   "Neutral baseline = the model's own trend yield for the harvest year (centred 9-year moving average of log yield, extended by its recent slope, no El Niño term) "
                   "× harvested area (USDA's area for that harvest where USDA is the basis, else the latest FAOSTAT area), on USDA's basis where the marketing-year mapping is checked (enso_neutral.json). "
                   "USDA's estimate is shown beside it, not multiplied: USDA may already allow for El Niño. The 90% range is coefficient uncertainty only; it does not include weather or forecast error. "
                   "Value at stake = tonnes × latest World Bank price. No world-price model, no probability weighting."),
        "neutral_source": (neutral_doc.get("_meta") or {}).get("source"),
        "neutral_generated_at": (neutral_doc.get("_meta") or {}).get("generated_at"),
        "neutral_note": NEUTRAL_NOTE,
        "honesty": honesty_line(jan_year, record),
        # The El Niño test's family, stated once for the page: every fitted pair, not the joint-test survivors.
        "q_nino_family": n_fitted,
        "q_nino_rule": ("A row is shown only when its El Niño slope passes Benjamini–Hochberg control on its own: "
                        + (f"q < 0.10 across all {n_fitted} fitted pairs." if n_fitted else "q < 0.10 across all fitted pairs.")),
        "model_quality_rule": TIER_RULE,
        "regions": out_regions,
        "crops": sorted(by_crop.values(), key=lambda c: c["loss_kt_record"]),
        "rows_all": rows_all,
        "who_pays": chain, "who_pays_totals": who_pays_totals,
        "who_pays_case": "record",
        "who_pays_rule": f"At the fit's strongest winter (ONI {rec_oni:+.1f}). The shortfall is the fitted change on the ENSO-neutral baseline, not on USDA's figure. It cuts exports first, allocated to buyers by Comtrade value share, each capped at its usual imports (USDA) with the capped excess spread over the other named buyers; any remainder is extra import need. Priced at the latest World Bank price. Stocks shown, not subtracted.",
    }, source="Derived: enso_regions, enso_model, crop_calendars, USDA PSD, World Bank Pink Sheet; live signals from JRC ASAP, GDACS, World Bank RTFP, ReliefWeb, El Niño news feed, trade_restrictions",
       notes="Tonnes first, on an ENSO-neutral baseline with USDA shown beside it; value at stake is tonnes × latest World Bank price, not a price forecast.", status="ok")
    doc = json.loads(path.read_text())
    doc["_meta"]["model_quality_rule"] = TIER_RULE
    doc["_meta"]["maturity"] = MATURITY
    doc["_meta"]["production_ready"] = False   # replaced by maturity (per surface); false as in the model and exposure files
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    stamp_inputs("enso_outlook.json")
    print(f"[OK] enso_outlook: {len(out_regions)} regions, {sum(len(r['fitted']) for r in out_regions)} fitted rows, "
          f"cases {cases['observed']['oni']} / {cases['record']['oni']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
