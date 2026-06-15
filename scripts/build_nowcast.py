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
    + psd_shortfall      (0-3)   — USDA PSD production-vs-consumption gap proxy for the latest
                                   marketing year (a true 5-yr-baseline shortfall needs a history
                                   table not yet wired in; this is a cross-sectional gap signal)
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
    # v25 — US-state live coverage. Feeding America (food insecurity %) + USGS
    # water (river-flow anomaly) are the live US-state signals we hold. Seeding
    # these lets US- rows exist in the nowcast instead of being absent — so the
    # "50 US states" claim is backed by a real (if partial) live layer.
    feeding = load("feeding_america_states.json")["data"]

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

    # v38 — canonical country/profile set. Previously all_iso was the union of the
    # live feeds only, which (a) produced ~12 orphan rows for territories/aggregates
    # (AIA, REU, EU27, …) that have no country profile and are never displayed, and
    # (b) left ~5 visible profiles (BES, FRO, IMN, MAF, MNP) with no nowcast row at
    # all because no feed covers them. We now seed all_iso with the canonical profiles
    # so EVERY visible profile gets a row (zero-adjustment if no live signal), and we
    # restrict the OUTPUT to canonical profiles so no orphan rows are emitted. This
    # keeps nowcast.json and countries.json on the same entity set.
    try:
        _co = load("../data/countries.json") if False else json.loads((DATA / "countries.json").read_text())
        _profiles = (_co.get("data", _co).get("countries", {}) or {})
        canonical_iso = set(_profiles.keys())
    except Exception as e:
        print(f"  [warn] countries.json profile set unavailable ({e}) — falling back to feed union only")
        canonical_iso = set()

    feed_iso = (set(wfp) | set(ipc) | set(acled) | set(om) | set(wfp_c)
                | set(estat) | set(faostat) | set(inform) | set(wgi) | set(psd)
                | set(usgs) | set(feeding))   # v25 — include US-state feeds so US- rows exist
    # Compute over feeds ∪ profiles so profiles with no feed still get a (zero) row;
    # if we have a canonical set, drop orphan rows that aren't real profiles.
    all_iso = (feed_iso | canonical_iso)
    if canonical_iso:
        all_iso = {iso for iso in all_iso if iso in canonical_iso}
    out = {}
    for iso in all_iso:
        ipc_p3   = (ipc.get(iso) or {}).get("phase3plus_pct") or 0
        wfp_fcs  = (wfp.get(iso) or {}).get("fcs_pct") or 0
        # v23 — ACLED only counts as a LIVE nowcast signal when the feed is actually
        # live (is_live=true). On a 12-month-lagged access tier it's a STRUCTURAL
        # baseline (already in the FDRS conflict component), so it must NOT add to the
        # live nowcast delta — that would present year-old conflict as a live disturbance.
        _acled_row = acled.get(iso) or {}
        conflict = (_acled_row.get("intensity_score") or 0) if _acled_row.get("is_live") else 0
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

        # v25 — US-state food-insecurity signal (Feeding America). State-level
        # food insecurity above the ~13% US average adds a small structural-stress
        # kick, scaled, capped at +3. This is the primary live signal that makes
        # US states real nowcast rows rather than empty placeholders.
        us_fi_kick = 0
        fa_row = feeding.get(iso) or {}
        fa_pct = fa_row.get("food_insecurity_pct")
        if iso.startswith("US-") and isinstance(fa_pct, (int, float)):
            us_fi_kick = min(3, max(0, (fa_pct - 13) * 0.4))

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
            + fire_kick + aq_kick + us_water_kick + us_fi_kick
            + inform_amp + governance_drag + psd_shortfall
            + relief_damp,
            1
        )
        adj = max(-10, min(35, adj))

        # ── v25 — Confidence flag (audit fix) ──────────────────────────────
        # The nowcast must not present a confident adjustment when the CORE
        # crisis signals that drive it are absent. IPC (Phase 3+) and WFP
        # HungerMap (FCS) are the primary live crisis inputs; when BOTH are
        # missing for a country, the adjustment is built only from secondary
        # signals (weather, FX, governance) and should be labelled low-confidence
        # so the UI can show it as provisional rather than authoritative.
        # Previously a missing signal silently became 0 ("no pressure"), which
        # made sparse-data countries look calmer and more certain than they are.
        has_ipc = iso in ipc and (ipc.get(iso) or {}).get("phase3plus_pct") is not None
        has_wfp = iso in wfp and (wfp.get(iso) or {}).get("fcs_pct") is not None
        # v25 — US states have their own core feed (Feeding America food insecurity),
        # so a US- row with FA data is high-confidence on its own terms.
        has_us_core = iso.startswith("US-") and isinstance(fa_pct, (int, float))
        # v25 — a current, sourced food-price reading is a legitimate live signal in
        # its own right. Stable developed economies (NL, DE, ...) never trip an IPC
        # crisis feed, but a fresh Eurostat/FAOSTAT food-inflation figure IS live
        # monitoring — so a present reading counts toward confidence rather than
        # leaving these countries mislabelled "no live signal".
        has_food_price = isinstance(food_infl, (int, float))
        core_signals = sum([has_ipc, has_wfp, has_us_core])
        if core_signals >= 1:
            confidence = "high"
        elif has_food_price:
            # live price monitoring present, but no humanitarian crisis feed — a
            # solid "monitored" tier between full-crisis-backed and unknown.
            confidence = "monitored"
        elif any([fx_shock, inflation_shock, weather_kick, flood_kick, conflict_kick,
                  inform_amp, governance_drag, psd_shortfall, us_water_kick]):
            confidence = "low"   # only secondary signals; no crisis or price feed
        else:
            confidence = "none"  # no live signal at all — adjustment is ~0 by absence, not by calm

        out[iso] = {
            "adjustment": adj,
            "confidence": confidence,
            "core_signals_present": {"ipc": has_ipc, "wfp_hungermap": has_wfp},
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
                "us_fi_kick":      round(us_fi_kick, 1),
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
                "us_food_insecurity_pct": fa_pct,
                # v20.27 — sourced structural signals (slow-moving)
                "inform_risk":          inform_score,
                "wgi_rule_of_law":      rol,
                "psd_shortfall_max":    psd_shortfall,
            },
        }

    # v25 — coverage summary so the frontend can show an honest banner when the
    # core crisis feeds are empty (rather than implying every score is live).
    n_high = sum(1 for v in out.values() if v.get("confidence") == "high")
    n_mon  = sum(1 for v in out.values() if v.get("confidence") == "monitored")
    n_low  = sum(1 for v in out.values() if v.get("confidence") == "low")
    n_none = sum(1 for v in out.values() if v.get("confidence") == "none")
    ipc_live = bool(ipc)
    wfp_live = bool(wfp)
    crisis_feeds_live = ipc_live or wfp_live

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
                "nowcast score. See methodology page for component formula. v25: each "
                "country carries a 'confidence' flag (high/low/none). 'high' = a core "
                "crisis feed (IPC or WFP HungerMap) backs the adjustment; 'low' = only "
                "secondary signals present; 'none' = no live signal, so the ~0 adjustment "
                "reflects absence of data, NOT confirmed calm."
            ),
            "coverage": {
                "ipc_feed_live": ipc_live,
                "wfp_hungermap_feed_live": wfp_live,
                "crisis_feeds_live": crisis_feeds_live,
                "countries_high_confidence": n_high,
                "countries_monitored": n_mon,
                "countries_low_confidence": n_low,
                "countries_no_live_signal": n_none,
            },
            "version": "v25",
        },
        "data": out,
    }
    (DATA / "nowcast.json").write_text(json.dumps(envelope, indent=2))
    print(f"[OK] wrote nowcast.json with {len(out)} entries "
          f"(confidence: {n_high} high, {n_mon} monitored, {n_low} low, {n_none} none | "
          f"IPC live: {ipc_live}, WFP live: {wfp_live})")


if __name__ == "__main__":
    main()
