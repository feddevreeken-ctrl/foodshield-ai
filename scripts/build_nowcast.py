"""
Build the nowcast layer — combines structural FDRS with live signals to produce
an adjusted current-conditions score.

Reads: data/wfp_hungermap.json, data/ipc.json, data/acled.json, data/reliefweb_alerts.json,
       data/worldbank_wdi.json, data/fao_ffpi.json
Writes: data/nowcast.json

Formula (Blueprint section 8.1 extended):
  Structural FDRS comes from the static dataset baked into index.html.
  Nowcast adjustment (range: -10 to +25 points) =
      ipc_pressure  (0-12)  — share of population in IPC Phase 3+
    + wfp_pressure  (0-6)   — FCS prevalence above 30%
    + conflict_kick (0-5)   — ACLED 30-day intensity
    + global_food_kick (0-2)— FAO FFPI MoM > +3%
    - relief_present (-2)   — active humanitarian response damps shock penalty
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
    return json.loads(p.read_text())


def main():
    wfp = load("wfp_hungermap.json")["data"]
    ipc = load("ipc.json")["data"]
    acled = load("acled.json")["data"]
    ffpi = load("fao_ffpi.json")["data"]
    rw = load("reliefweb_alerts.json")["data"]

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

    all_iso = set(wfp) | set(ipc) | set(acled)
    out = {}
    for iso in all_iso:
        ipc_p3 = (ipc.get(iso) or {}).get("phase3plus_pct") or 0
        wfp_fcs = (wfp.get(iso) or {}).get("fcs_pct") or 0
        conflict = (acled.get(iso) or {}).get("intensity_score") or 0
        relief_n = len(rw_by_iso.get(iso, []))

        ipc_pressure = min(12, ipc_p3 * 0.12)         # 100% pop in P3+ -> 12 pts
        wfp_pressure = min(6, max(0, (wfp_fcs - 30) * 0.15))
        conflict_kick = min(5, conflict * 0.05)
        relief_damp = -2 if relief_n >= 3 else (-1 if relief_n >= 1 else 0)

        adj = round(ipc_pressure + wfp_pressure + conflict_kick + global_food_kick + relief_damp, 1)
        adj = max(-10, min(25, adj))

        out[iso] = {
            "adjustment": adj,
            "components": {
                "ipc_pressure": round(ipc_pressure, 1),
                "wfp_pressure": round(wfp_pressure, 1),
                "conflict_kick": round(conflict_kick, 1),
                "global_food_kick": global_food_kick,
                "relief_damp": relief_damp,
            },
            "signals": {
                "ipc_phase3plus_pct": ipc_p3,
                "wfp_fcs_pct": wfp_fcs,
                "acled_intensity": conflict,
                "ffpi_mom_pct": (ffpi or {}).get("change_mom_pct"),
                "active_reports_30d": relief_n,
            },
        }

    envelope = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Composite: WFP HungerMap + IPC + ACLED + FAO FFPI + ReliefWeb",
            "notes": "Adjustment range -10 to +25 added to structural FDRS to produce nowcast score.",
            "version": "v19",
        },
        "data": out,
    }
    (DATA / "nowcast.json").write_text(json.dumps(envelope, indent=2))
    # Distinguish actual UN-member countries from total ISO3 entries
    # (the upstream feeds include dependencies, disputed territories, etc.)
    with_signals = sum(
        1 for v in out.values()
        if any(s for s in (v.get("signals") or {}).values() if s and s != 0)
    )
    print(f"[OK] wrote nowcast.json with {len(out)} ISO3 entries ({with_signals} with at least one live signal)")


if __name__ == "__main__":
    main()
