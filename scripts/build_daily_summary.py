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

_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _ipc_period_ended(period, today=None):
    """True when an IPC period string's last month ('... Jun 2026 (Projection)') is over."""
    import re
    m = re.search(r"([A-Z][a-z]{2}) (\d{4})\s*\(", str(period or ""))
    if not m or m.group(1) not in _MONTHS:
        return False
    y, mo = int(m.group(2)), _MONTHS[m.group(1)]
    end = date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
    return end <= (today or date.today())


def load(name):
    p = DATA_DIR / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _feed_age_days(name):
    """Age in whole days of a feed's _meta.generated_at, or None if unreadable."""
    env = load(name) or {}
    ts = ((env.get("_meta") or {}).get("generated_at")) if isinstance(env, dict) else None
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def main():
    today = date.today().isoformat()
    nc      = (load("nowcast.json") or {}).get("data") or {}
    ipc     = (load("ipc.json") or {}).get("data") or {}
    ffpi    = (load("fao_ffpi.json") or {}).get("data") or {}
    wfp_c   = (load("wfp_country.json") or {}).get("data") or {}
    estat   = (load("eurostat_food.json") or {}).get("data") or {}
    om      = (load("openmeteo.json") or {}).get("data") or {}
    flood   = (load("openmeteo_flood.json") or {}).get("data") or {}
    inform  = (load("inform_risk.json") or {}).get("data") or {}
    _ctry   = ((load("countries.json") or {}).get("data") or {}).get("countries") or {}

    def fdrs_of(iso):
        v = (_ctry.get(iso) or {}).get("fdrs_displayed")
        v = v.get("value") if isinstance(v, dict) else v
        return v if isinstance(v, (int, float)) else None

    # v79i — ONE country-name resolver for every bullet in this file.
    # Each bullet used to resolve names on its own, against whichever feed it
    # happened to have in hand, so the same summary could read "Top mover: SSD"
    # in one line and "Top of list: South Sudan" in the next. inform_risk.json is
    # the only feed that names ~all 191 countries; ipc.json ships
    # `country: null` on every row. Chain them, then fall back to the ISO
    # code — never to None.

    def name_of(iso):
        for src in (inform, ipc, nc):
            nm = (src.get(iso) or {}).get("country") if isinstance(src, dict) else None
            if nm:
                return nm
        return iso

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
        names_by_iso = {iso: name_of(iso) for iso, _, _ in top}
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
    # v79i — `.get("country", iso)` only falls back when the KEY is absent. Every
    # row in ipc.json carries the key with an explicit null (the ew-tool IPC feed
    # ships no country name), so the default never fired and the bullet rendered
    # "Worst: None at 67%" on the live Disturbances tab. Resolve the name the same
    # way the nowcast bullet above does, and fall back to the ISO code — never to
    # None.
    high_ipc = []
    for iso, row in ipc.items():
        # Palestine appears three times (PSE plus the Gaza/West Bank analyses PSG, PSW); count it once.
        if iso in ("PSG", "PSW"):
            continue
        pct = (row or {}).get("phase3plus_pct") or 0
        # Same rule as the Disturbances list: an analysis whose validity period has ended
        # ("Apr 2026 - Jun 2026 (Projection)") is no longer a current crisis count.
        if _ipc_period_ended((row or {}).get("period")):
            continue
        if pct >= 25:
            high_ipc.append((iso, pct, name_of(iso)))
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
        # The index level lives under `fpi`; there has never been a `value` key,
        # so this bullet silently dropped its level before v45.
        latest = ffpi.get("latest", {}).get("fpi") if isinstance(ffpi.get("latest"), dict) else None
        if mom is not None and abs(mom) >= 1.5:
            direction = "up" if mom > 0 else "down"
            bullets.append({
                "text": f"FAO Food Price Index {direction} {abs(mom):.1f}% month-on-month. Index level: {latest:.1f}." if latest else f"FAO Food Price Index {direction} {abs(mom):.1f}% month-on-month.",
                "source": "FAO FFPI",
            })

    # Active drought / heat / flood / fire / fx shocks (count countries flagged)
    # Dry weeks count only where the page shows them: structurally exposed countries
    # (FDRS >= 40). A dry week in Copenhagen is not a signal.
    def _om_shown(iso, r):
        if not (isinstance(r, dict) and r.get("drought_flag")):
            return False
        return (fdrs_of(iso) or 0) >= 40
    drought_count = sum(1 for iso, r in om.items() if _om_shown(iso, r))
    flood_count = sum(1 for r in flood.values() if isinstance(r, dict) and r.get("flood_flag"))
    fx_count = sum(1 for r in wfp_c.values() if isinstance(r, dict) and r.get("fx_currency_shock"))

    env_parts = []
    if drought_count >= 1: env_parts.append(f"{drought_count} countries with a dry week at the capital")
    if flood_count >= 1: env_parts.append(f"{flood_count} with a river-flood flag")
    if env_parts:
        # v79i — this bullet said "...countries today" unconditionally, while the
        # weather feed behind it can be weeks old (Open-Meteo Weather has not
        # refreshed since 2026-08-03 and the run still succeeds on last-good data).
        # Asserting "today" over a stale snapshot is the one thing this summary is
        # not allowed to do, so date the claim when the underlying feed is stale.
        _om_age = _feed_age_days("openmeteo.json")
        _when = (
            "today"
            if _om_age is not None and _om_age <= 2
            else f"as of the last weather refresh ({_om_age}d ago)"
            if _om_age is not None
            else "(weather refresh date unknown)"
        )
        bullets.append({
            "text": f"Weather flags {_when}: " + " and ".join(env_parts) + ".",
            "source": "Open-Meteo + Open-Meteo Flood",
        })

    if fx_count >= 1:
        bullets.append({
            "text": f"{fx_count} countries with a currency shock (US-dollar rate up more than 10% in 90 days).",
            "source": "open.er-api.com + Frankfurter FX (derived)",
        })

    # Top INFORM-risk countries (slow-moving but worth surfacing)
    if isinform := (inform if isinstance(inform, dict) else None):
        ranked = []
        for iso, row in isinform.items():
            risk = (row or {}).get("inform_risk")
            if isinstance(risk, (int, float)) and risk >= 7.5:
                ranked.append((iso, risk, name_of(iso)))
        if ranked:
            ranked.sort(key=lambda x: -x[1])
            bullets.append({
                "text": f"{len(ranked)} countries at INFORM Risk ≥ 7.5/10 (severe humanitarian risk). Top of list: {ranked[0][2]} ({ranked[0][1]:.1f}).",
                "source": "EU JRC INFORM",
            })

    # Headline + subhead
    if movers:
        worst_iso = movers[0][0]
        worst_name = name_of(worst_iso)
        headline = f"{len(movers)} countries with active nowcast pressure; {worst_name} leads."
    elif high_ipc:
        headline = f"{len(high_ipc)} countries in active IPC Phase 3+ crisis; quiet day on the nowcast layer."
    elif drought_count or flood_count:
        headline = "Quiet on FDRS movements; environmental signals firing across multiple regions."
    else:
        headline = "No major shocks today; structural FDRS unchanged at most countries."

    subhead_bits = []
    if movers:
        subhead_bits.append(f"{len(movers)} country nowcast moves")
    if high_ipc:
        subhead_bits.append(f"{len(high_ipc)} IPC Phase 3+ crises")
    if drought_count + flood_count:
        subhead_bits.append(f"{drought_count + flood_count} environmental flags")
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
