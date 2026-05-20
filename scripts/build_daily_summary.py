"""
Build the 'what changed today' daily summary headline.

Reads the latest nowcast.json + supporting feeds and emits a short structured
summary (data/daily_summary.json) that the frontend renders at the top of the
Disturbances tab. No LLM call — this is deterministic text from actual deltas,
so it's always auditable.

OUTPUT: data/daily_summary.json
  {
    "_meta": {...},
    "data": {
      "headline":     "...",   # 1 punchy sentence (~120 chars)
      "subhead":      "...",   # 1 supporting sentence (~180 chars)
      "bullets":      [...]    # 3-5 concrete deltas
      "highlights":   [...]    # {iso, country, kind, value, note} per top movers
      "as_of":        "ISO date"
    }
  }

The frontend treats this as just-in-time editorial content. The signal sources
are listed under each bullet so users can verify.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from _common import DATA_DIR


def load(name):
    p = DATA_DIR / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def main():
    today = date.today().isoformat()
    nc      = (load("nowcast.json") or {}).get("data") or {}
    ipc     = (load("ipc.json") or {}).get("data") or {}
    acled   = (load("acled.json") or {}).get("data") or {}
    ffpi    = (load("fao_ffpi.json") or {}).get("data") or {}
    wfp_c   = (load("wfp_country.json") or {}).get("data") or {}
    estat   = (load("eurostat_food.json") or {}).get("data") or {}
    om      = (load("openmeteo.json") or {}).get("data") or {}
    flood   = (load("openmeteo_flood.json") or {}).get("data") or {}
    fires   = (load("nasa_firms.json") or {}).get("data") or {}
    inform  = (load("inform_risk.json") or {}).get("data") or {}

    bullets = []
    highlights = []
    counters = defaultdict(int)

    # Top FDRS movers (largest positive nowcast adjustments)
    movers = []
    for iso, row in nc.items():
        adj = row.get("adjustment") or 0
        sig = row.get("signals") or {}
        # Skip US states for the global summary
        if iso.startswith("US-"):
            continue
        if adj >= 8:
            movers.append((iso, adj, sig))
    movers.sort(key=lambda x: -x[1])

    if movers:
        top = movers[:3]
        names_by_iso = {}
        # Quick reverse-lookup for country names via WFP HungerMap if present
        hm = (load("wfp_hungermap.json") or {}).get("data") or {}
        for iso, _, _ in top:
            names_by_iso[iso] = (hm.get(iso) or {}).get("country") or iso
        worst = top[0]
        bullets.append({
            "text": f"{len(movers)} countries on +8 or worse nowcast adjustment vs structural baseline today. Top mover: {names_by_iso.get(worst[0], worst[0])} at +{worst[1]}.",
            "source": "FoodShield nowcast composite",
        })
        for iso, adj, sig in top:
            highlights.append({
                "iso": iso,
                "country": names_by_iso.get(iso, iso),
                "kind": "fdrs_mover",
                "value": adj,
                "note": "Nowcast adjustment vs structural baseline",
            })

    # IPC Phase 3+ countries
    high_ipc = []
    for iso, row in ipc.items():
        pct = (row or {}).get("phase3plus_pct") or 0
        if pct >= 25:
            high_ipc.append((iso, pct, (row or {}).get("country", iso)))
    high_ipc.sort(key=lambda x: -x[1])
    if high_ipc:
        worst = high_ipc[0]
        bullets.append({
            "text": f"{len(high_ipc)} countries with ≥25% population in IPC Phase 3+ food crisis. Worst: {worst[2]} at {worst[1]:.0f}%.",
            "source": "WFP HungerMap (IPC mirror)",
        })

    # FAO FFPI MoM movement
    if isinstance(ffpi, dict):
        mom = ffpi.get("change_mom_pct")
        latest = ffpi.get("latest", {}).get("value") if isinstance(ffpi.get("latest"), dict) else None
        if mom is not None and abs(mom) >= 1.5:
            direction = "up" if mom > 0 else "down"
            bullets.append({
                "text": f"FAO Food Price Index {direction} {abs(mom):.1f}% month-on-month. Index level: {latest:.1f}." if latest else f"FAO Food Price Index {direction} {abs(mom):.1f}% month-on-month.",
                "source": "FAO FFPI",
            })

    # Active drought / heat / flood / fire / fx shocks (count countries flagged)
    drought_count = sum(1 for r in om.values() if isinstance(r, dict) and r.get("drought_flag"))
    flood_count = sum(1 for r in flood.values() if isinstance(r, dict) and r.get("flood_flag"))
    fire_count = sum(1 for r in fires.values() if isinstance(r, dict) and r.get("fire_flag"))
    fx_count = sum(1 for r in wfp_c.values() if isinstance(r, dict) and r.get("fx_currency_shock"))

    env_parts = []
    if drought_count >= 1: env_parts.append(f"{drought_count} drought-flagged")
    if flood_count >= 1: env_parts.append(f"{flood_count} flood-flagged")
    if fire_count >= 1: env_parts.append(f"{fire_count} fire-active")
    if env_parts:
        bullets.append({
            "text": "Active environmental signals: " + ", ".join(env_parts) + " countries today.",
            "source": "Open-Meteo + Open-Meteo Flood + NASA FIRMS",
        })

    if fx_count >= 1:
        bullets.append({
            "text": f"{fx_count} countries with currency shock flag (>10% drop vs USD in 90d).",
            "source": "WFP HungerMap per-country FX",
        })

    # Top INFORM-risk countries (slow-moving but worth surfacing)
    if isinform := (inform if isinstance(inform, dict) else None):
        ranked = []
        for iso, row in isinform.items():
            risk = (row or {}).get("inform_risk")
            if isinstance(risk, (int, float)) and risk >= 7.5:
                ranked.append((iso, risk, (row or {}).get("country", iso)))
        if ranked:
            ranked.sort(key=lambda x: -x[1])
            bullets.append({
                "text": f"{len(ranked)} countries at INFORM Risk ≥ 7.5/10 (severe humanitarian risk). Top of list: {ranked[0][2]} ({ranked[0][1]:.1f}).",
                "source": "EU JRC INFORM",
            })

    # Headline + subhead
    if movers:
        worst_iso = movers[0][0]
        worst_name = (hm.get(worst_iso) or {}).get("country") or worst_iso
        headline = f"{len(movers)} countries with active nowcast pressure; {worst_name} leads."
    elif high_ipc:
        headline = f"{len(high_ipc)} countries in active IPC Phase 3+ crisis; quiet day on the nowcast layer."
    elif drought_count or flood_count or fire_count:
        headline = "Quiet on FDRS movements; environmental signals firing across multiple regions."
    else:
        headline = "No major shocks today; structural FDRS unchanged at most countries."

    subhead_bits = []
    if movers:
        subhead_bits.append(f"{len(movers)} country nowcast moves")
    if high_ipc:
        subhead_bits.append(f"{len(high_ipc)} IPC Phase 3+ crises")
    if drought_count + flood_count + fire_count:
        subhead_bits.append(f"{drought_count + flood_count + fire_count} environmental flags")
    if fx_count:
        subhead_bits.append(f"{fx_count} currency shocks")
    subhead = ("Today: " + " · ".join(subhead_bits) + ".") if subhead_bits else "All sources nominal."

    payload = {
        "headline": headline,
        "subhead": subhead,
        "bullets": bullets[:6],
        "highlights": highlights[:10],
        "as_of": today,
    }

    # Write envelope
    out_path = DATA_DIR / "daily_summary.json"
    envelope = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "FoodShield daily summary (composite of nowcast + IPC + FFPI + Open-Meteo + INFORM)",
            "notes": (
                "Deterministic text built from actual signal deltas. No LLM. "
                "Regenerates every workflow tick (6h). Surfaced at the top of the "
                "Disturbances tab."
            ),
            "version": "v20.29",
        },
        "data": payload,
    }
    out_path.write_text(json.dumps(envelope, indent=2))
    print(f"[OK] wrote {out_path}")
    print(f"  headline: {headline}")
    print(f"  bullets: {len(bullets)}")
    print(f"  highlights: {len(highlights)}")


if __name__ == "__main__":
    main()
