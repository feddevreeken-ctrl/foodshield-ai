#!/usr/bin/env python3
"""
refresh_shipping_gauges.py — the measured water behind the El Nino shipping lanes.

Every lane on the Shipping lens used to carry a hand-typed "Now" reading scraped
out of curated prose. This collects the gauges themselves, each from the agency
that owns it, with no key:

  Panama   Gatún Lake level, daily since 1965, and the Canal's 60-day projection
           with its estimated maximum drafts (Panama Canal Authority CSVs)
  Mississippi  St. Louis stage, observed and the NWS forecast (NOAA NWPS, EADM7)
               plus the weekly St. Louis barge rate (USDA AgTransport, Socrata)
  Rhine    Kaub gauge, 30 days of readings and the station's own reference
           levels (German waterways agency, PEGELONLINE)
  Paraná   Rosario daily height (Argentina INA, series 34)
  Amazon   Manaus / Rio Negro level (Brazil ANA telemetry, station 14990000)

Each gauge is independent: one that fails is recorded under `unavailable` and
the others still publish. Nothing is back-filled from a previous run.

What the Gatún record adds is a climatology: for today's day of year, the
1966-2025 median and 10th/90th percentiles, and the same calendar path in the
El Nino years 1997-98, 2015-16 and 2023-24, so this year's lake can be read
against both normal and the events that forced slot and draft cuts.
"""
from __future__ import annotations

import csv
import io
import re
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import http_get, write_json  # noqa: E402

# The Canal server answers 406 to a request without a plain Accept header.
UA = {"User-Agent": "Mozilla/5.0 (FoodShield AI; public food-security dashboard)", "Accept": "*/*"}
GATUN_HIST = "https://evtms-rpts.pancanal.com/eng/h2o/Download_Gatun_Lake_Water_Level_History.csv"
GATUN_PROJ = "https://evtms-rpts.pancanal.com/eng/h2o/Gatun_Water_Level_Projection.csv"
NWPS_OBS = "https://api.water.noaa.gov/nwps/v1/gauges/eadm7/stageflow/observed"
NWPS_FC = "https://api.water.noaa.gov/nwps/v1/gauges/eadm7/stageflow/forecast"
BARGE = "https://agtransport.usda.gov/resource/deqi-uken.json"
KAUB = "https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations/KAUB/W"
INA = "https://alerta.ina.gob.ar/a5/obs/puntual/series/34/observaciones"
ANA = "https://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"
ANALOGS = (1997, 2015, 2023)   # El Nino onset years whose dry season forced Panama restrictions


def get(url: str, **params):
    return http_get(url, timeout=60, headers=UA, retries=2, params=params or None)


def _same_day(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:            # 29 Feb in a non-leap year
        return d.replace(year=year, day=28)


def gatun(today: date) -> dict:
    rows = []
    for r in csv.DictReader(io.StringIO(get(GATUN_HIST).text)):
        try:
            rows.append((date.fromisoformat(r["DATE_LOG"].strip()), float(r["GATUN_LAKE_LEVEL(FEET)"])))
        except (KeyError, ValueError):
            continue
    if len(rows) < 10000:
        raise RuntimeError(f"Gatún history parse got {len(rows)} rows")
    rows.sort()
    by = dict(rows)
    last_day, last_ft = rows[-1]
    if (today - last_day).days > 10:
        raise RuntimeError(f"Gatún history ends {last_day}: frozen feed")

    def clim(d: date) -> dict | None:
        vals = [by[x] for y in range(1966, 2026) if (x := _same_day(d, y)) in by]
        if len(vals) < 30:
            return None
        q = statistics.quantiles(vals, n=10)
        return {"p10": round(q[0], 2), "p50": round(statistics.median(vals), 2), "p90": round(q[-1], 2), "n_years": len(vals)}

    # A Jun-to-May window around this event's dry season, sampled weekly.
    start = date(last_day.year if last_day.month >= 6 else last_day.year - 1, 6, 1)
    days = [start + timedelta(days=7 * i) for i in range(52)]
    band = [dict(day=d.isoformat()[5:], **(clim(d) or {})) for d in days]
    this = [{"date": d.isoformat(), "ft": by[d]} for d in days if d in by]
    if not this or this[-1]["date"] != last_day.isoformat():
        this.append({"date": last_day.isoformat(), "ft": last_ft})
    analogs = {}
    for y in ANALOGS:
        pts = []
        for d in days:
            x = _same_day(d, y + (d.year - start.year))
            if x in by:
                pts.append({"day": d.isoformat()[5:], "ft": by[x]})
        analogs[f"{y}-{str(y + 1)[2:]}"] = pts
    # The true low of each analog year from the DAILY record over the same
    # Jun-May window, with its date: the weekly samples above miss the bottom,
    # and the month matters (2023's low came in the wet season, July).
    analog_min = {}
    for y in ANALOGS:
        lo = date(y, 6, 1)
        window = [(d, v) for d, v in rows if lo <= d < lo + timedelta(days=366)]
        if window:
            d_min, v_min = min(window, key=lambda t: t[1])
            analog_min[f"{y}-{str(y + 1)[2:]}"] = {"ft": v_min, "date": d_min.isoformat()}

    proj = []
    lines = [ln for ln in get(GATUN_PROJ).text.splitlines() if ln.strip()]
    hdr = next((i for i, ln in enumerate(lines) if ln.lower().startswith("projected_date")), None)
    if hdr is not None:
        for r in csv.DictReader(io.StringIO("\n".join(lines[hdr:])), skipinitialspace=True):
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            try:
                proj.append({"date": datetime.strptime(r["projected_date"], "%m/%d/%Y").date().isoformat(),
                             "ft": float(r["projected_gatun_water_level"]),
                             "neopanamax_draft_ft": float(r["max_neopanamax_draft_ft"])})
            except (KeyError, ValueError):
                continue
        proj.sort(key=lambda p: p["date"])
    now_clim = clim(last_day) or {}
    return {
        "name": "Gatún Lake", "unit": "ft", "source": "Panama Canal Authority", "url": GATUN_HIST,
        "latest": {"date": last_day.isoformat(), "value": last_ft},
        "climatology_today": now_clim,
        "vs_median_ft": round(last_ft - now_clim["p50"], 2) if now_clim else None,
        "window_start": start.isoformat(), "band": band, "this_year": this, "analogs": analogs, "analog_min": analog_min,
        "projection": proj, "projection_url": GATUN_PROJ,
        "projection_note": "ACP's own estimate; official drafts are set only by Advisories to Shipping.",
    }


def stlouis() -> dict:
    def series(url):
        j = get(url).json()
        return j, [{"t": p["validTime"], "ft": p["primary"], "kcfs": p.get("secondary")}
                   for p in j.get("data", []) if isinstance(p.get("primary"), (int, float)) and p["primary"] > -900]
    _, obs = series(NWPS_OBS)
    fj, fc = series(NWPS_FC)
    if not obs:
        raise RuntimeError("St. Louis observed series empty")
    daily = {}
    for p in obs:                      # last reading of each day
        daily[p["t"][:10]] = p
    fmin = min(fc, key=lambda p: p["ft"]) if fc else None
    fmax = max(fc, key=lambda p: p["ft"]) if fc else None
    return {
        "name": "Mississippi at St. Louis", "unit": "ft", "source": "NOAA National Water Prediction Service (EADM7)",
        "url": "https://water.noaa.gov/gauges/eadm7",
        "latest": {"date": obs[-1]["t"], "value": obs[-1]["ft"], "kcfs": obs[-1].get("kcfs")},
        "observed_30d": [{"date": k, "ft": v["ft"]} for k, v in sorted(daily.items())][-30:],
        "forecast": [{"t": p["t"], "ft": p["ft"]} for p in fc],
        "forecast_issued": fj.get("issuedTime"),
        "forecast_min": {"t": fmin["t"], "ft": fmin["ft"], "is_last_point": fmin is fc[-1]} if fmin else None,
        "forecast_max": {"t": fmax["t"], "ft": fmax["ft"]} if fmax else None,
    }


def barge() -> dict:
    rows = get(BARGE, **{"location": "St. Louis", "$order": "date DESC", "$limit": "60"}).json()
    # `rate` is a text column: sort it as a number or "999" outranks "2653".
    peak = get(BARGE, **{"location": "St. Louis", "$select": "date,rate", "$where": "rate IS NOT NULL",
                         "$order": "rate::number DESC", "$limit": "1"}).json()
    pts = [{"date": r["date"][:10], "pct_tariff": round(float(r["rate"]), 1)} for r in rows if r.get("rate")]
    if not pts:
        raise RuntimeError("barge rate series empty")
    return {
        "name": "St. Louis downbound grain barge rate", "unit": "% of 1976 benchmark tariff",
        "source": "USDA AMS AgTransport", "url": "https://agtransport.usda.gov/Barge/Downbound-Grain-Barge-Rates/deqi-uken",
        "latest": {"date": pts[0]["date"], "value": pts[0]["pct_tariff"]},
        "weekly_52": list(reversed(pts[:52])),
        "record": {"date": peak[0]["date"][:10], "value": round(float(peak[0]["rate"]), 1)} if peak else None,
    }


def kaub() -> dict:
    meta = get(KAUB + ".json", includeCurrentMeasurement="true", includeCharacteristicValues="true").json()
    meas = get(KAUB + "/measurements.json", start="P30D").json()
    daily = {}
    for m in meas:
        daily.setdefault(m["timestamp"][:10], []).append(m["value"])
    cur = meta.get("currentMeasurement") or {}
    if not isinstance(cur.get("value"), (int, float)):
        raise RuntimeError("Kaub current reading missing")
    ref = {}
    for c in meta.get("characteristicValues", []):
        if c.get("shortname") in ("NW", "GlW", "MNW", "MW"):
            ref[c["shortname"]] = {"value": c.get("value"), "label": c.get("longname"),
                                   "from": (c.get("timespanStart") or "")[:10], "to": (c.get("timespanEnd") or "")[:10],
                                   "occurred": (c.get("occurences") or [None])[0]}
    return {
        "name": "Rhine at Kaub", "unit": "cm", "source": "German Federal Waterways and Shipping Administration (PEGELONLINE)",
        "url": "https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=2335",
        "latest": {"date": cur.get("timestamp"), "value": cur["value"], "state": cur.get("stateMnwMhw")},
        "daily_30d": [{"date": d, "cm": round(statistics.mean(v))} for d, v in sorted(daily.items())],
        "reference": ref,
    }


def rosario(today: date) -> dict:
    j = get(INA, timestart=(today - timedelta(days=45)).isoformat(), timeend=(today + timedelta(days=1)).isoformat()).json()
    pts = sorted(({"date": o["timestart"][:10], "m": o["valor"]} for o in j if isinstance(o.get("valor"), (int, float))),
                 key=lambda p: p["date"])
    if not pts:
        raise RuntimeError("Rosario series empty")
    return {
        "name": "Paraná at Rosario", "unit": "m",
        "source": "Instituto Nacional del Agua (INA), series 34; gauge operated by Prefectura Naval Argentina",
        "url": "https://alerta.ina.gob.ar/a5/obs/puntual/series/?estacion_id=34&var_id=2&proc_id=1",
        "latest": {"date": pts[-1]["date"], "value": pts[-1]["m"]}, "daily_45d": pts,
    }


def manaus(today: date) -> dict:
    xml = get(ANA, codEstacao="14990000", dataInicio=(today - timedelta(days=30)).strftime("%d/%m/%Y"),
              dataFim=today.strftime("%d/%m/%Y")).text
    pts = {}
    for block in re.findall(r"<DadosHidrometereologicos[^>]*>(.*?)</DadosHidrometereologicos>", xml, flags=re.S):
        t = re.search(r"<DataHora>([^<]+)</DataHora>", block)
        n = re.search(r"<Nivel>([^<]+)</Nivel>", block)
        if t and n and n.group(1).strip():
            try:
                pts.setdefault(t.group(1).strip()[:10], float(n.group(1).strip()) / 100)
            except ValueError:
                pass
    if not pts:
        raise RuntimeError("Manaus series empty")
    series = sorted(({"date": d, "m": round(v, 2)} for d, v in pts.items()), key=lambda p: p["date"])
    return {
        "name": "Rio Negro at Manaus", "unit": "m", "source": "Agência Nacional de Águas (ANA) telemetry, station 14990000",
        "url": "https://www.snirh.gov.br/hidrotelemetria/",
        "latest": {"date": series[-1]["date"], "value": series[-1]["m"]}, "daily_30d": series,
    }


def main() -> int:
    today = datetime.now(timezone.utc).date()
    gauges, unavailable = {}, []
    for key, fn in (("gatun", lambda: gatun(today)), ("stlouis", stlouis), ("barge_stlouis", barge),
                    ("kaub", kaub), ("rosario", lambda: rosario(today)), ("manaus", lambda: manaus(today))):
        try:
            gauges[key] = fn()
            print(f"  ok   {key}: {gauges[key]['latest']}")
        except Exception as e:  # noqa: BLE001 -- one agency down must not blank the rest
            unavailable.append({"key": key, "reason": f"{type(e).__name__}: {e}"})
            print(f"  FAIL {key}: {e}")
    if not gauges:
        raise RuntimeError("no gauge reachable -- keeping the previous file")
    write_json("enso_gauges.json", {"gauges": gauges, "unavailable": unavailable},
               source="Panama Canal Authority; NOAA NWPS; USDA AMS AgTransport; WSV PEGELONLINE; INA Argentina; ANA Brazil",
               notes="Measured water levels behind the El Niño shipping lanes, each from the agency that owns the gauge.",
               status="ok" if not unavailable else "partial")
    return 0


if __name__ == "__main__":
    sys.exit(main())
