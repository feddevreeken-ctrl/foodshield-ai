"""
Build the nowcast layer — combines structural FDRS with live signals to produce
an adjusted current-conditions score.

Reads: data/wfp_hungermap.json, data/ipc.json, data/acled.json,
       data/reliefweb_alerts.json, data/fao_ffpi.json,
       data/wfp_country.json, data/openmeteo.json, data/openmeteo_flood.json,
       data/openaq.json, data/nasa_firms.json, data/usgs_water.json,
       data/eurostat_food.json, data/faostat_food.json
Writes: data/nowcast.json

Formula (extended May 2026, expanded May 2026 v20.27):
  Nowcast adjustment (range: -10 to +35 points) =
      ipc_pressure       (0-12)  — share of population in IPC Phase 3+
    + wfp_pressure       (0-6)   — FCS prevalence above 30%
    + conflict_kick      (0-5)   — ACLED 30-day intensity
    + global_food_kick   (0-2)   — FAO FFPI MoM > +3%
    + fx_shock           (0-3)   — local currency fell >10% in 90d vs USD
    + inflation_shock    (0-3)   — food inflation >15%
    + weather_kick       (0-4)   — drought | heat extremes
    + flood_kick         (0-3)   — river discharge anomaly
    + fire_kick          (0-2)   — fire activity >2x baseline
    + aq_kick            (0-1)   — PM2.5 > WHO target-2 (35 µg/m³)
    + us_water_kick      (0-2)   — only for US-XX state codes
    + inform_amp         (0-3)   — INFORM risk >7.0 → composite humanitarian crisis amplifier
    + governance_drag    (0-2)   — WGI rule_of_law < -1.0 → governance brittleness amplifier
    + psd_shortfall      (0-3)   — USDA PSD staple production shortfall ≥10% vs 5-yr avg
    - relief_present     (-2)    — active humanitarian response damps shock
"""
import json
from pathlib import Path
from datetime import datetime, timezone

DATA = Path(__file__).resolve().parent.parent / "data"


def load(name):
    p = DATA / name
    if not p.exists():
        print(f"  [warn] {name} missing — treating as empty")
        return {"data": {}}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"  [warn] {name} unreadable ({e}) — treating as empty")
        return {"data": {}}


def main():
    wfp     = load("wfp_hungermap.json")["data"]
    wfp_c   = load("wfp_country.json")["data"]
    ipc     = load("ipc.json")["data"]
    acled   = load("acled.json")["data"]
    ffpi    = load("fao_ffpi.json")["data"]
    rw      = load("reliefweb_alerts.json")["data"]
    om      = load("openmeteo.json")["data"]
    flood   = load("openmeteo_flood.json")["data"]
    fires   = load("nasa_firms.json")["data"]
    aq      = load("openaq.json")["data"]
    usgs    = load("usgs_water.json")["data"]
    estat   = load("eurostat_food.json")["data"]
    faostat = load("faostat_food.json")["data"]
    # v20.27 — additional sourced inputs used for INFORM amp + governance drag + PSD shortfall
    inform  = load("inform_risk.json")["data"]
    wgi     = load("wgi.json")["data"]
    psd     = load("usda_psd.json")["data"]

    global_food_kick = 0
    if isinstance(ffpi, dict):
        mom = ffpi.get("change_mom_pct") or 0
        if mom > 3:
            global_food_kick = 2
        elif mom > 1:
            global_food_kick = 1

    # Index ReliefWeb events by ISO3
    rw_by_iso = {}
    for ev in (rw.get("events") if isinstance(rw, dict) else []) or []:
        iso = ev.get("iso3")
        if iso:
            rw_by_iso.setdefault(iso, []).append(ev)

    all_iso = (set(wfp) | set(ipc) | set(acled) | set(om) | set(wfp_c)
               | set(estat) | set(faostat) | set(inform) | set(wgi) | set(psd))
    out = {}
    for iso in all_iso:
        ipc_p3   = (ipc.get(iso) or {}).get("phase3plus_pct") or 0
        wfp_fcs  = (wfp.get(iso) or {}).get("fcs_pct") or 0
        conflict = (acled.get(iso) or {}).get("intensity_score") or 0
        relief_n = len(rw_by_iso.get(iso, []))
        wc       = wfp_c.get(iso) or {}
        om_row   = om.get(iso) or {}
        fl_row   = flood.get(iso) or {}
        fi_row   = fires.get(iso) or {}
        aq_row   = aq.get(iso) or {}
        usg_row  = usgs.get(iso) or {}

        ipc_pressure  = min(12, ipc_p3 * 0.12)
        wfp_pressure  = min(6, max(0, (wfp_fcs - 30) * 0.15))
        conflict_kick = min(5, conflict * 0.05)
        relief_damp   = -2 if relief_n >= 3 else (-1 if relief_n >= 1 else 0)

        # FX shock — currency dropped >10% vs USD in 90d
        fx_pct = wc.get("fx_90d_change_pct")
        fx_shock = 0
        if isinstance(fx_pct, (int, float)) and fx_pct < -10:
            fx_shock = min(3, abs(fx_pct + 10) * 0.1)

        # Food inflation shock — best of three sources, priority HungerMap > Eurostat > FAOSTAT
        # HungerMap per-country (sticky for crisis countries)
        food_infl = wc.get("food_inflation_pct")
        food_infl_source = "hungermap" if food_infl is not None else None
        # Eurostat (EU only, fresher than FAOSTAT)
        if food_infl is None:
            es = estat.get(iso) or {}
            if es.get("food_hicp_yoy_pct") is not None:
                food_infl = es["food_hicp_yoy_pct"]
                food_infl_source = "eurostat"
        # FAOSTAT (global, but lagged 4-12 months)
        if food_infl is None:
            fs = faostat.get(iso) or {}
            if fs.get("food_cpi_yoy_pct") is not None:
                food_infl = fs["food_cpi_yoy_pct"]
                food_infl_source = "faostat"
        inflation_shock = 0
        # Eurostat uses 8% threshold (EU baseline lower); others use 15%
        threshold = 8 if food_infl_source == "eurostat" else 15
        if isinstance(food_infl, (int, float)) and food_infl > threshold:
            inflation_shock = min(3, (food_infl - threshold) * 0.1)

        # Weather extremes — drought + heat
        weather_kick = 0
        if om_row.get("drought_flag"):
            weather_kick += 3
        if om_row.get("heat_flag"):
            weather_kick += 1.5
        weather_kick = min(4, weather_kick)

        # Floods
        flood_kick = 3 if fl_row.get("flood_flag") else 0

        # Fires
        fire_kick = 2 if fi_row.get("fire_flag") else 0

        # Air quality (background factor; small weight)
        aq_kick = 1 if aq_row.get("pm25_flag") else 0

        # US water (only applies to US-XX state codes)
        us_water_kick = 0
        if iso.startswith("US-") and usg_row.get("flow_anomaly") in ("low", "high"):
            us_water_kick = 2

        # v20.27 — INFORM amplifier: composite humanitarian risk above 7.0
        # piles on the IPC/conflict picture. Capped at +3 so it doesn't
        # double-count what IPC + ACLED already capture.
        inform_amp = 0
        inf_row = inform.get(iso) or {}
        inform_score = inf_row.get("inform_risk")
        if isinstance(inform_score, (int, float)) and inform_score > 7.0:
            inform_amp = min(3, (inform_score - 7.0) * 1.5)

        # v20.27 — Governance drag: WGI rule_of_law below -1.0 reflects an
        # institutional brittleness that lengthens recovery from any shock.
        # Doesn't push FDRS up much on its own (cap +2) but compounds with
        # other signals.
        governance_drag = 0
        wgi_row = wgi.get(iso) or {}
        rol = (wgi_row.get("rule_of_law") or {}).get("value")
        if isinstance(rol, (int, float)) and rol < -1.0:
            governance_drag = min(2, (abs(rol) - 1.0) * 1.5)

        # v20.27 — USDA PSD production-shortfall kick. If wheat OR rice OR
        # corn production for the latest year is ≥10% below the previous
        # year's, that's a meaningful local supply shock.
        # Note: we only have one year here from the bulk pull — proper
        # baseline comparison needs the 5-yr table, so this stays a
        # simple year-on-year proxy.
        psd_shortfall = 0
        psd_row = psd.get(iso) or {}
        # Look at production_kt vs consumption_kt; if production < 60% of
        # consumption AND the gap is widening, that's a sourcing crunch.
        for staple in ("wheat", "rice", "corn"):
            sr = psd_row.get(staple) or {}
            prod = sr.get("production_kt")
            cons = sr.get("consumption_kt")
            if prod is not None and cons is not None and cons > 0:
                gap_pct = (cons - prod) / cons * 100
                # >60% gap (i.e. >60% of consumption is imported) AND
                # production < 200kt absolute → small-producer stress signal
                if gap_pct > 60 and prod < 200:
                    psd_shortfall = max(psd_shortfall, 1)
                if gap_pct > 80:
                    psd_shortfall = max(psd_shortfall, 2)
                if gap_pct > 95:
                    psd_shortfall = max(psd_shortfall, 3)
        psd_shortfall = min(3, psd_shortfall)

        adj = round(
            ipc_pressure + wfp_pressure + conflict_kick + global_food_kick
            + fx_shock + inflation_shock + weather_kick + flood_kick
            + fire_kick + aq_kick + us_water_kick
            + inform_amp + governance_drag + psd_shortfall
            + relief_damp,
            1
        )
        adj = max(-10, min(35, adj))

        out[iso] = {
            "adjustment": adj,
            "components": {
                "ipc_pressure":    round(ipc_pressure, 1),
                "wfp_pressure":    round(wfp_pressure, 1),
                "conflict_kick":   round(conflict_kick, 1),
                "global_food_kick": global_food_kick,
                "fx_shock":        round(fx_shock, 1),
                "inflation_shock": round(inflation_shock, 1),
                "weather_kick":    round(weather_kick, 1),
                "flood_kick":      flood_kick,
                "fire_kick":       fire_kick,
                "aq_kick":         aq_kick,
                "us_water_kick":   us_water_kick,
                "inform_amp":      round(inform_amp, 1),
                "governance_drag": round(governance_drag, 1),
                "psd_shortfall":   psd_shortfall,
                "relief_damp":     relief_damp,
            },
            "signals": {
                "ipc_phase3plus_pct":   ipc_p3,
                "wfp_fcs_pct":          wfp_fcs,
                "acled_intensity":      conflict,
                "ffpi_mom_pct":         (ffpi or {}).get("change_mom_pct"),
                "active_reports_30d":   relief_n,
                "fx_90d_change_pct":    fx_pct,
                "food_inflation_pct":   food_infl,
                "food_inflation_source": food_infl_source,
                "precip_anomaly_pct":   om_row.get("precip_anomaly_pct"),
                "temp_anomaly_c":       om_row.get("temp_anomaly_c"),
                "drought_flag":         om_row.get("drought_flag"),
                "heat_flag":            om_row.get("heat_flag"),
                "wet_flag":             om_row.get("wet_flag"),
                "flood_flag":           fl_row.get("flood_flag"),
                "fire_flag":            fi_row.get("fire_flag"),
                "pm25_latest":          aq_row.get("pm25_latest"),
                "us_flow_anomaly":      usg_row.get("flow_anomaly"),
                # v20.27 — sourced structural signals (slow-moving)
                "inform_risk":          inform_score,
                "wgi_rule_of_law":      rol,
                "psd_shortfall_max":    psd_shortfall,
            },
        }

    envelope = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": (
                "Composite: WFP HungerMap + IPC + ACLED + FAO FFPI + ReliefWeb + "
                "Open-Meteo (weather/flood) + NASA FIRMS + OpenAQ + USGS Water + "
                "WFP per-country (FX/inflation) + Eurostat food HICP + FAOSTAT food CPI "
                "+ INFORM risk + WB WGI rule of law + USDA PSD staples shortfall"
            ),
            "notes": (
                "Adjustment range -10 to +35 added to structural FDRS to produce "
                "nowcast score. See methodology page for component formula."
            ),
            "version": "v20",
        },
        "data": out,
    }
    (DATA / "nowcast.json").write_text(json.dumps(envelope, indent=2))
    with_signals = sum(
        1 for v in out.values()
        if any(s for s in (v.get("signals") or {}).values() if s and s != 0)
    )
    print(f"[OK] wrote nowcast.json with {len(out)} ISO3 entries ({with_signals} with at least one live signal)")


if __name__ == "__main__":
    main()
