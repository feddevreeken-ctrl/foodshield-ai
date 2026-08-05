"""
Build the nowcast layer — combines structural FDRS with live signals to produce
an adjusted current-conditions score.

Reads: data/wfp_hungermap.json, data/ipc.json, data/acled.json,
       data/reliefweb_alerts.json, data/fao_ffpi.json,
       data/wfp_country.json, data/openmeteo.json, data/openmeteo_flood.json,
       data/openaq.json, data/usgs_water.json,
       data/eurostat_food.json, data/faostat_food.json
Writes: data/nowcast.json

Formula (extended May 2026, expanded May 2026 v20.27):
  Nowcast adjustment (range: -10 to +35 points) =
      ipc_pressure       (0-12)  — share of population in IPC Phase 3+
    + fews_kick          (0-6)   — FEWS NET forward projection: gap-fills the crisis
                                   level where IPC is absent, plus a deterioration nudge
                                   when the near-term projection is worse than current
    + wfp_pressure       (0-6)   — FCS prevalence above 30%
    + displacement_kick  (0-4)   — HDX HAPI internal-displacement magnitude band (new v43)
    + conflict_kick      (0-5)   — ACLED 30-day intensity
    + global_food_kick   (0-2)   — FAO FFPI MoM > +3%
    + fx_shock           (0-3)   — local currency fell >10% in 90d vs USD
    + inflation_shock    (0)     — DISABLED v79: the YoY level now feeds
                                   structural c[3]; charging it here too was a
                                   double-count. Re-enable as an acceleration
                                   signal only. Field retained for consumers.
    + weather_kick       (0-4)   — drought | heat extremes
    + flood_kick         (0-3)   — river discharge anomaly
    + aq_kick            (0-1)   — PM2.5 > WHO target-2 (35 µg/m³)
    + us_water_kick      (0-2)   — only for US-XX state codes
    + us_fi_kick          (0-3)   — US-state food insecurity (Feeding America)
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


def _age_days(as_of):
    """Days between an ISO date string (YYYY-MM-DD or full ISO) and now, or None.

    v79 — used to gate time-sensitive signals whose collectors keep the latest
    row available regardless of vintage.
    """
    if not isinstance(as_of, str) or not as_of.strip():
        return None
    txt = as_of.strip().replace("Z", "+00:00")
    for parse in (datetime.fromisoformat,
                  lambda s: datetime.strptime(s[:10], "%Y-%m-%d")):
        try:
            dt = parse(txt)
        except (ValueError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    return None


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
    fews    = load("fews.json")["data"]   # v42 — FEWS NET forward projection (crisis gap-fill + deterioration)
    idps    = load("hapi_idps.json")["data"]   # v43 — HDX HAPI internal displacement (new source)
    acled   = load("acled.json")["data"]
    ffpi    = load("fao_ffpi.json")["data"]
    rw      = load("reliefweb_alerts.json")["data"]
    om      = load("openmeteo.json")["data"]
    flood   = load("openmeteo_flood.json")["data"]
    aq      = load("openaq.json")["data"]
    usgs    = load("usgs_water.json")["data"]
    estat   = load("eurostat_food.json")["data"]
    faostat = load("faostat_food.json")["data"]
    # v79 — the FX signal was dead. refresh_fx.py has written a real 90-day
    # depreciation for 156/157 countries since v20, but this file only ever read
    # wfp_country.fx_90d_change_pct, which is null in all 172 WFP rows — so
    # fx_shock came out 0.0 for all 264 countries in every build. Note the sign
    # convention differs: fx_rates uses POSITIVE = depreciation (stated in its
    # own _meta.notes), WFP used NEGATIVE = depreciation.
    fx      = load("fx_rates.json")["data"]
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

    feed_iso = (set(wfp) | set(ipc) | set(fews) | set(idps) | set(acled) | set(om) | set(wfp_c)
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
        aq_row   = aq.get(iso) or {}
        usg_row  = usgs.get(iso) or {}

        ipc_pressure  = min(12, ipc_p3 * 0.12)
        wfp_pressure  = min(6, max(0, (wfp_fcs - 30) * 0.15))
        conflict_kick = min(5, conflict * 0.05)
        relief_damp   = -2 if relief_n >= 3 else (-1 if relief_n >= 1 else 0)

        # v42 — FEWS NET forward projection. FEWS is the best forward-looking famine
        # signal and was previously display-only. To avoid double-counting IPC's
        # CURRENT Phase 3+ reading, FEWS contributes to the nowcast in two honest ways:
        #   (a) crisis GAP-FILL — only when IPC has no reading for the country, FEWS'
        #       observed current_phase (>=3) backstops the crisis level; and
        #   (b) a forward DETERIORATION nudge — when the near-term projected_phase is
        #       worse than the current_phase, add a small kick regardless of IPC.
        # Capped at +6 so a projection can't dominate observed current conditions.
        fw_row      = fews.get(iso) or {}
        fews_cur    = fw_row.get("current_phase")
        fews_proj   = fw_row.get("projected_phase")
        ipc_present = (ipc.get(iso) or {}).get("phase3plus_pct") is not None
        fews_kick   = 0
        fews_basis  = None
        if not ipc_present and isinstance(fews_cur, (int, float)) and fews_cur >= 3:
            fews_kick  = min(6, (fews_cur - 2) * 2)   # phase 3->2, 4->4, 5->6
            fews_basis = "current_phase_gapfill"
        if isinstance(fews_proj, (int, float)) and isinstance(fews_cur, (int, float)):
            if fews_proj > fews_cur:
                fews_kick  = min(6, fews_kick + 2)     # near-term deterioration
                fews_basis = fews_basis or "projected_deterioration"
            elif fews_proj >= 3 and ipc_present:
                # v42 — sustained crisis the forward projection still flags at Phase 3+,
                # even where IPC already covers the CURRENT state. Half-credit (+1) so
                # FEWS isn't inert on the ~26 countries IPC also tracks, without
                # double-counting IPC's current-phase reading.
                fews_kick  = min(6, fews_kick + 1)
                fews_basis = fews_basis or "sustained_projection"

        # v43 — internal displacement (HDX HAPI). Magnitude-banded on ABSOLUTE IDP
        # count (countries.json has no population, so this is not per-capita — a
        # disclosed simplification). Acute livelihood/market disruption; capped +4 so
        # it complements IPC/WFP rather than dominating.
        # v79 — FRESHNESS GATE. The collector keeps whatever the latest available
        # row is, with no age limit, so pre-2020 snapshots were still scoring in
        # 2026: Indonesia's 110,373 IDPs are dated 2018-12-30 (2,762 days stale)
        # and were both adding a kick AND acting as the country's only core
        # signal, which promoted it to "high" confidence. Full weight inside
        # 18 months, linear decay to zero at 36, nothing after that — and a stale
        # reading can never confer confidence (see has_idp below).
        _idp_row = idps.get(iso) or {}
        idp_n = _idp_row.get("idps") or 0
        idp_age_days = _age_days(_idp_row.get("as_of"))
        idp_weight = 1.0
        if idp_age_days is None:
            idp_weight = 0.0          # undated → unusable for a time-sensitive signal
        elif idp_age_days > 1095:     # > 36 months
            idp_weight = 0.0
        elif idp_age_days > 548:      # 18-36 months → decay
            idp_weight = max(0.0, 1.0 - (idp_age_days - 548) / (1095 - 548))
        idp_stale = idp_weight < 1.0
        displacement_kick = (4 if idp_n >= 2_000_000 else 3 if idp_n >= 1_000_000
                             else 2 if idp_n >= 500_000 else 1 if idp_n >= 100_000 else 0)
        displacement_kick = round(displacement_kick * idp_weight, 1)

        # FX shock — currency dropped >10% vs USD in 90d.
        # Primary source is fx_rates.json (positive depr_90d_pct = depreciation);
        # WFP stays as a fallback under its own negative-is-depreciation sign.
        # Both are normalised to fx_pct with NEGATIVE = depreciation so the
        # published inputs.fx_90d_change_pct keeps its established meaning.
        fx_pct = None
        fx_src = None
        depr = ((fx.get(iso) or {}).get("shock") or {}).get("depr_90d_pct")
        if isinstance(depr, (int, float)):
            fx_pct = -float(depr)
            fx_src = "fx_rates"
        elif isinstance(wc.get("fx_90d_change_pct"), (int, float)):
            fx_pct = wc["fx_90d_change_pct"]
            fx_src = "wfp"
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
        # v79 — THE SAME READING WAS BEING CHARGED TWICE.
        #
        # Until today, food inflation only ever reached the display field `fi`,
        # so this nowcast kick was the sole place it touched a score. v79 wired
        # the same WFP/Eurostat/FAOSTAT reading into structural component c[3]
        # (build_countries_dataset._fi_to_component) — the component literally
        # named food_infl — which made this a duplicate charge on one number.
        # Argentina: a single 251.3% YoY reading becomes c[3]=100 -> 9.0
        # structural points AND min(3,(251.3-15)*0.1) = 3.0 here. 12.0 points
        # from one observation, across 18 countries.
        #
        # A level/delta split would justify keeping both, but only if this side
        # measured ACCELERATION. It does not: all three feeds expose only the
        # latest YoY figure, so this is a second level test on the same value.
        # The structural component is the better home — it is weighted, it
        # renormalises when absent, and it is what the component is named for.
        #
        # The field stays in the payload (the "why +N?" breakdown enumerates
        # every component) but contributes 0. Re-enable only once a feed retains
        # a comparable prior-period YoY, making this a true delta:
        # inflation_shock = f(yoy_now - yoy_prior).
        inflation_shock = 0
        # Eurostat uses 8% threshold (EU baseline lower); others use 15%.
        # Retained: the confidence branch below reads food_infl, and the
        # threshold documents what the re-enabled delta should clear.
        threshold = 8 if food_infl_source == "eurostat" else 15

        # Weather extremes — drought + heat
        weather_kick = 0
        if om_row.get("drought_flag"):
            weather_kick += 3
        if om_row.get("heat_flag"):
            weather_kick += 1.5
        weather_kick = min(4, weather_kick)

        # Floods
        flood_kick = 3 if fl_row.get("flood_flag") else 0

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

        # USDA PSD production-shortfall kick — a TRUE year-on-year anomaly.
        #
        # v79 REWRITE. The old version measured (consumption - production) /
        # consumption, i.e. the share of consumption that must be imported. That
        # is a persistent structural fact, not an event, and FDRS already charges
        # it as component c[0] at weight 0.23 — so 108 of 264 countries were
        # billed twice for the same import dependence, inside a signal the
        # methodology described as a fast-moving shock. Yemen scored the full +3
        # every single build purely because it grows no rice.
        # refresh_usda_psd.py now retains production_kt_prev, so we can score
        # what the comment always claimed: a real drop against last marketing
        # year. Only negative deviations count; growth is not a risk signal.
        psd_shortfall = 0
        psd_row = psd.get(iso) or {}
        worst_drop_pct = 0.0
        psd_drop_staple = None
        for staple in ("wheat", "rice", "corn"):
            sr = psd_row.get(staple) or {}
            prod = sr.get("production_kt")
            prev = sr.get("production_kt_prev")
            if not isinstance(prod, (int, float)) or not isinstance(prev, (int, float)):
                continue
            # Ignore trivial crops: a 40kt -> 20kt hobby harvest is a -50% swing
            # that says nothing about national food supply.
            if prev < 100:
                continue
            drop_pct = (prev - prod) / prev * 100.0
            if drop_pct > worst_drop_pct:
                worst_drop_pct = drop_pct
                psd_drop_staple = staple
        if worst_drop_pct >= 35:
            psd_shortfall = 3
        elif worst_drop_pct >= 20:
            psd_shortfall = 2
        elif worst_drop_pct >= 10:
            psd_shortfall = 1

        # v43 — crisis-cluster cap. ipc_pressure, fews_kick, displacement_kick,
        # inform_amp and conflict_kick all load onto the SAME underlying acute-crisis
        # reality — a famine/conflict/displacement country trips all of them, and summed
        # unbounded they double-count one crisis. Cap their COMBINED contribution at +18
        # so the cluster can't dominate, while independent signals (weather, FX, price,
        # governance) still add on top. Disclosed in the methodology.
        # v79 — wfp_pressure belongs INSIDE the cluster. It is another reading of
        # the same acute-crisis state (household food-consumption scores in the
        # very countries IPC and FEWS already classify), but it was summed
        # outside the cap, so up to +6 more could stack on top of the capped +18
        # — 24 points from one crisis, which is exactly what the cap exists to
        # prevent. Note the cap is currently non-binding on live data (0 of 264
        # rows reach it; the largest cluster is Sudan at 14.2), so this changes
        # no published number today — it closes the path before it opens.
        crisis_cluster  = (ipc_pressure + fews_kick + displacement_kick
                           + inform_amp + conflict_kick + wfp_pressure)
        cluster_overage = max(0, crisis_cluster - 18)

        adj = round(
            ipc_pressure + fews_kick + wfp_pressure + displacement_kick + conflict_kick + global_food_kick
            + fx_shock + inflation_shock + weather_kick + flood_kick
            + aq_kick + us_water_kick + us_fi_kick
            + inform_amp + governance_drag + psd_shortfall
            + relief_damp - cluster_overage,
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
        has_fews = isinstance(fews_cur, (int, float))   # v42 — FEWS is an authoritative crisis feed
        # v43 — significant displacement is an authoritative crisis signal, but
        # v79 requires it to be CURRENT: a 2018 snapshot says nothing about 2026,
        # and must not be the sole reason a country reads "high" confidence.
        has_idp = idp_n >= 100_000 and not idp_stale
        # v25 — US states have their own core feed (Feeding America food insecurity),
        # so a US- row with FA data is high-confidence on its own terms.
        has_us_core = iso.startswith("US-") and isinstance(fa_pct, (int, float))
        # v25 — a current, sourced food-price reading is a legitimate live signal in
        # its own right. Stable developed economies (NL, DE, ...) never trip an IPC
        # crisis feed, but a fresh Eurostat/FAOSTAT food-inflation figure IS live
        # monitoring — so a present reading counts toward confidence rather than
        # leaving these countries mislabelled "no live signal".
        has_food_price = isinstance(food_infl, (int, float))
        core_signals = sum([has_ipc, has_wfp, has_us_core, has_fews, has_idp])
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
            "core_signals_present": {"ipc": has_ipc, "wfp_hungermap": has_wfp,
                                     "fews": has_fews, "idp": has_idp},
            "components": {
                "ipc_pressure":    round(ipc_pressure, 1),
                "wfp_pressure":    round(wfp_pressure, 1),
                "conflict_kick":   round(conflict_kick, 1),
                "fews_kick":       round(fews_kick, 1),
                "displacement_kick": displacement_kick,
                "global_food_kick": global_food_kick,
                "fx_shock":        round(fx_shock, 1),
                "inflation_shock": round(inflation_shock, 1),
                "weather_kick":    round(weather_kick, 1),
                "flood_kick":      flood_kick,
                "aq_kick":         aq_kick,
                "us_water_kick":   us_water_kick,
                "us_fi_kick":      round(us_fi_kick, 1),
                "inform_amp":      round(inform_amp, 1),
                "governance_drag": round(governance_drag, 1),
                "psd_shortfall":   psd_shortfall,
                "relief_damp":     relief_damp,
                "crisis_cluster_cap": -round(cluster_overage, 1),
            },
            "signals": {
                "ipc_phase3plus_pct":   ipc_p3,
                "wfp_fcs_pct":          wfp_fcs,
                "acled_intensity":      conflict,
                "fews_current_phase":   fews_cur,
                "fews_projected_phase": fews_proj,
                "fews_basis":           fews_basis,
                "idps_total":           idp_n or None,
                "idps_as_of":           _idp_row.get("as_of"),
                "idps_age_days":        idp_age_days,
                "idps_stale":           idp_stale if idp_n else None,
                "ffpi_mom_pct":         (ffpi or {}).get("change_mom_pct"),
                "active_reports_30d":   relief_n,
                "fx_90d_change_pct":    fx_pct,
                "fx_source":            fx_src,
                "food_inflation_pct":   food_infl,
                "food_inflation_source": food_infl_source,
                "precip_anomaly_pct":   om_row.get("precip_anomaly_pct"),
                "temp_anomaly_c":       om_row.get("temp_anomaly_c"),
                "drought_flag":         om_row.get("drought_flag"),
                "heat_flag":            om_row.get("heat_flag"),
                "wet_flag":             om_row.get("wet_flag"),
                "flood_flag":           fl_row.get("flood_flag"),
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
    # v79 — "live" must mean the feed carries SIGNAL, not merely rows.
    #
    # These were bool(dict): true whenever the file had any keys at all. WFP is
    # currently in its IPC fallback (public FCS endpoints are down), so it ships
    # 56 rows in which every fcs_pct is null — the field wfp_pressure actually
    # scores. Shape without substance: the dict is non-empty, the signal is
    # absent, and the coverage meta cheerfully reported the crisis feed as live
    # while every country's wfp_pressure was 0. That is the precise shape of
    # "absence of data presented as calm" the QA honesty gate exists to catch,
    # and it caught it.
    #
    # Count the field each feed is scored on instead of the rows it happens to
    # contain. Same principle as validate_data's scored-field coverage rule.
    def _n_scored(feed, field):
        return sum(1 for v in (feed or {}).values()
                   if isinstance(v, dict) and isinstance(v.get(field), (int, float)))

    n_ipc_scored = _n_scored(ipc, "phase3plus_pct")
    n_wfp_scored = _n_scored(wfp, "fcs_pct")
    ipc_live = n_ipc_scored > 0
    wfp_live = n_wfp_scored > 0
    crisis_feeds_live = ipc_live or wfp_live

    envelope = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": (
                "Composite: WFP HungerMap + IPC + ACLED + FAO FFPI + ReliefWeb + "
                "Open-Meteo (weather/flood) + NASA FIRMS + OpenAQ + USGS Water + "
                "WFP per-country (FX/inflation) + Eurostat food HICP + FAOSTAT food CPI "
                "+ FEWS NET forward projection + HDX HAPI internal displacement "
                "+ INFORM risk + WB WGI rule of law "
                "+ USDA PSD staples shortfall"
            ),
            "notes": (
                "Adjustment range -10 to +35 added to structural FDRS to produce "
                "nowcast score. See methodology page for component formula. v25: each "
                "country carries a 'confidence' flag (high/monitored/low/none). 'high' = a "
                "core crisis feed (IPC, WFP HungerMap, FEWS NET, or >=100k internally "
                "displaced) backs the adjustment; 'low' = only "
                "secondary signals present; 'none' = no live signal, so the ~0 adjustment "
                "reflects absence of data, NOT confirmed calm."
            ),
            "coverage": {
                "ipc_feed_live": ipc_live,
                "wfp_hungermap_feed_live": wfp_live,
                "crisis_feeds_live": crisis_feeds_live,
                # v79 — publish the counts the flags are derived from, so a
                # consumer can tell "feed absent" from "feed present but
                # carrying no scored values" without re-reading the raw file.
                "ipc_scored_countries": n_ipc_scored,
                "wfp_fcs_scored_countries": n_wfp_scored,
                "countries_high_confidence": n_high,
                "countries_monitored": n_mon,
                "countries_low_confidence": n_low,
                "countries_no_live_signal": n_none,
                # v41 — record secondary feeds that were EMPTY at build time, so an
                # aq_kick of 0 is auditable as "feed down" rather than reading as
                # "clean air" (audit 2026-07-01). v79: nasa_firms removed entirely.
                "secondary_feeds_empty": [name for name, feed in (
                    ("openaq", aq), ("usgs_water", usgs),
                ) if not feed],
                # v42 — ACLED is licence-gated: on a lagged/unlicensed tier its
                # conflict_kick is 0 for every country. Surface the live-contribution
                # count explicitly so a blanket 0 reads as "conflict feed not live",
                # not "no conflict anywhere on Earth".
                "acled_conflict_live_countries": sum(
                    1 for v in out.values() if v["components"].get("conflict_kick", 0) > 0),
                "fews_scored_countries": sum(
                    1 for v in out.values() if v["components"].get("fews_kick", 0) > 0),
                "displacement_scored_countries": sum(
                    1 for v in out.values() if v["components"].get("displacement_kick", 0) > 0),
            },
            "version": "v42",
        },
        "data": out,
    }
    (DATA / "nowcast.json").write_text(json.dumps(envelope, indent=2))
    print(f"[OK] wrote nowcast.json with {len(out)} entries "
          f"(confidence: {n_high} high, {n_mon} monitored, {n_low} low, {n_none} none | "
          f"IPC live: {ipc_live}, WFP live: {wfp_live})")


if __name__ == "__main__":
    main()
