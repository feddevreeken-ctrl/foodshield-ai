"""
Build the nowcast layer — combines structural FDRS with live signals to produce
an adjusted current-conditions score.

Reads: data/wfp_hungermap.json, data/ipc.json, data/acled.json,
       data/reliefweb_alerts.json, data/fao_ffpi.json,
       data/wfp_country.json, data/openmeteo.json, data/openmeteo_flood.json,
       data/openaq.json, data/nasa_firms.json, data/usgs_water.json
Writes: data/nowcast.json

Formula (extended May 2026):
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

    all_iso = set(wfp) | set(ipc) | set(acled) | set(om) | set(wfp_c)
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

        # Food inflation shock
        food_infl = wc.get("food_inflation_pct")
        inflation_shock = 0
        if isinstance(food_infl, (int, float)) and food_infl > 15:
            inflation_shock = min(3, (food_infl - 15) * 0.1)

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

        adj = round(
            ipc_pressure + wfp_pressure + conflict_kick + global_food_kick
            + fx_shock + inflation_shock + weather_kick + flood_kick
            + fire_kick + aq_kick + us_water_kick + relief_damp,
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
                "precip_anomaly_pct":   om_row.get("precip_anomaly_pct"),
                "temp_anomaly_c":       om_row.get("temp_anomaly_c"),
                "drought_flag":         om_row.get("drought_flag"),
                "heat_flag":            om_row.get("heat_flag"),
                "wet_flag":             om_row.get("wet_flag"),
                "flood_flag":           fl_row.get("flood_flag"),
                "fire_flag":            fi_row.get("fire_flag"),
                "pm25_latest":          aq_row.get("pm25_latest"),
                "us_flow_anomaly":      usg_row.get("flow_anomaly"),
            },
        }

    envelope = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": (
                "Composite: WFP HungerMap + IPC + ACLED + FAO FFPI + ReliefWeb + "
                "Open-Meteo (weather/flood) + NASA FIRMS + OpenAQ + USGS Water + "
                "WFP per-country (FX/inflation)"
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
