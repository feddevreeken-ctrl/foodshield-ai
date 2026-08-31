"""
IPC (Integrated Food Security Phase Classification) — acute food insecurity.

May 2026: IPC restricted their public `api.ipcinfo.org/population` endpoint to
authenticated callers. WFP's HungerMap re-publishes the IPC table at
`api.hungermapdata.org/v2/ipc.json`, no key required (per HungerMap docs).

We use HungerMap as the primary source; if it's down, we attempt the official
IPC endpoint as a fallback (may still 401 without IPC_API_KEY).

Output: data/ipc.json
  {
    iso3: {
      "phase3plus_pct": <%>,
      "phase3plus_count": <people>,
      "phase4_count": <people>,
      "phase5_count": <people>,
      "period": "Mon YYYY - Mon YYYY",
      "analysis_date": "YYYY-MM-DD",
      "source_via": "hungermap" or "ipcinfo"
    }
  }
"""
import json

from _common import DATA_DIR, http_get, write_json, env

# v35 (Jun 2026) — HungerMap migrated public IPC to the ew-tool API; the v2 path
# still answers but with older analyses. Primary = ew-tool, fallbacks = v2 + official.
URL_EWTOOL = "https://ew-tool-api.hungermapdata.org/ew/v1/ipc/food/insecurity/global/recent"
URL_HUNGERMAP = "https://api.hungermapdata.org/v2/ipc.json"
URL_OFFICIAL = "https://api.ipcinfo.org/population"


def _fetch_ewtool():
    """v35 primary: fresh IPC/CH analyses from the HungerMap ew-tool API."""
    r = http_get(URL_EWTOOL, timeout=45)
    rows = r.json()
    if isinstance(rows, dict) and isinstance(rows.get("body"), list):
        rows = rows["body"]
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("ew-tool IPC returned no rows")
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        iso3 = (row.get("iso3Alpha3") or "").upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue
        pct = row.get("phase35Percentage")
        out[iso3] = {
            "phase3plus_pct": round(pct * 100, 2) if isinstance(pct, (int, float)) else None,
            "phase3plus_count": _int(row.get("phase35Population")),
            "phase4_count": _int(row.get("phase45Population")),
            "phase5_count": _int(row.get("phase5Population")),
            "period": row.get("referencePeriod"),
            "analysis_date": row.get("analysisDate") or row.get("dateOfAnalysis"),
            # v79i — the ew-tool IPC payload carries no country name, and this
            # used to hardcode None, leaving all 56 rows nameless. Take the name
            # off the row when the feed does supply one; consumers resolve the
            # rest from wfp_hungermap.json and fall back to the ISO code.
            "country": row.get("adm0_name") or row.get("countryName") or row.get("country") or None,
            "source_via": "hungermap (ew-tool-api)",
            "data_source": row.get("dataSource"),
        }
    if not out:
        raise RuntimeError("ew-tool IPC parsed to zero countries")
    return out


def _merge_palestine(out):
    """Aggregate Gaza (PSG) and the West Bank (PSW) into a single PSE row.

    The upstream feed keys the two Palestinian territories separately. Every
    consumer in this repo — build_nowcast, the country panel, the map — looks up
    PSE, the ISO3 code Palestine actually carries in countries.json. The result
    was that the worst food-security reading on the map (Gaza, 67% of the
    population in Phase 3+, the highest SHARE of any population tracked) reached
    nothing: PSE resolved to no IPC data at all, its nowcast confidence read
    "none", and the -1.0 no-signal adjustment LOWERED Palestine's score.

    Aggregated by population, reconstructing each territory's population from
    its own share and headcount, so the combined percentage is a true weighted
    figure rather than a mean of two percentages over unequal populations.
    Counts are summed. The worst (lowest) analysis date and the more severe
    period label are carried forward so the vintage stays honest.
    """
    parts = [(k, out[k]) for k in ("PSG", "PSW") if isinstance(out.get(k), dict)]
    if not parts:
        return out
    tot_pop = 0.0
    tot_p3 = 0
    tot_p4 = 0
    tot_p5 = 0
    for _k, row in parts:
        pct = row.get("phase3plus_pct")
        cnt = row.get("phase3plus_count")
        if isinstance(pct, (int, float)) and pct > 0 and isinstance(cnt, (int, float)):
            tot_pop += cnt / (pct / 100.0)
        tot_p3 += int(cnt or 0)
        tot_p4 += int(row.get("phase4_count") or 0)
        tot_p5 += int(row.get("phase5_count") or 0)
    if tot_pop <= 0:
        return out
    worst = max(parts, key=lambda kv: kv[1].get("phase3plus_pct") or 0)[1]
    out["PSE"] = {
        "phase3plus_pct": round(tot_p3 / tot_pop * 100.0, 1),
        "phase3plus_count": tot_p3,
        "phase4_count": tot_p4,
        "phase5_count": tot_p5,
        "period": worst.get("period"),
        "analysis_date": worst.get("analysis_date"),
        "country": "Palestine (Gaza + West Bank)",
        "source_via": worst.get("source_via"),
        "data_source": worst.get("data_source"),
        "_aggregated_from": [k for k, _ in parts],
    }
    print(f"  [ipc] PSE synthesised from {'+'.join(k for k, _ in parts)}: "
          f"{out['PSE']['phase3plus_pct']}% of {int(tot_pop):,}")
    return out


def _annotate_coverage(out):
    """Record what each IPC percentage is actually a percentage OF.

    v86 — the feed publishes `phase3plus_pct` against the population the IPC
    analysis COVERED, which is frequently a subset of the country. Nothing said
    so, and the nowcast read it as national prevalence. The distortion is large
    and it runs both ways:

        Uganda   24% of an assessed 1.47M  ->  0.7% of the country
        Ukraine  32% of an assessed 6.26M  ->  5.1%
        Sudan    67% of an assessed 8.3M   -> 10.8%
        Tanzania  5% of an assessed 10.1M  ->  0.7%

    Ten countries are affected. This does not decide which number is right —
    for a country with a partial analysis the national figure UNDERSTATES,
    because the uncounted areas are not known to be fine. It records both, plus
    the coverage ratio, so a consumer can choose knowingly instead of assuming.

    Population comes from the World Bank bulk file already on disk.
    """
    pop = {}
    p = DATA_DIR / "worldbank_bulk.json"
    if p.exists():
        try:
            obj = json.loads(p.read_text())
            data = obj.get("data", obj) if isinstance(obj, dict) else {}
            for iso, row in data.items():
                rec = (row or {}).get("SP.POP.TOTL") if isinstance(row, dict) else None
                v = rec.get("value") if isinstance(rec, dict) else None
                if isinstance(v, (int, float)) and v > 0:
                    pop[iso.upper()] = float(v)
        except Exception as e:
            print(f"  [warn] IPC coverage annotation: population unreadable ({e})")
    # Top up from the conflict feed, which already resolves population for 260
    # countries via a World Bank API call. Sudan in particular is absent from the
    # bulk file, and Sudan is the country this annotation matters most for.
    hp = DATA_DIR / "hapi_conflict.json"
    if hp.exists():
        try:
            obj = json.loads(hp.read_text())
            data = obj.get("data", obj) if isinstance(obj, dict) else {}
            for iso, row in data.items():
                v = (row or {}).get("population") if isinstance(row, dict) else None
                if isinstance(v, (int, float)) and v > 0:
                    pop.setdefault(iso.upper(), float(v))
        except Exception:
            pass

    flagged = 0
    for iso, row in out.items():
        if not isinstance(row, dict):
            continue
        pct = row.get("phase3plus_pct")
        cnt = row.get("phase3plus_count")
        if not isinstance(pct, (int, float)) or not isinstance(cnt, (int, float)) or pct <= 0:
            continue
        assessed = cnt / (pct / 100.0)
        row["assessed_population"] = int(round(assessed))
        national = pop.get(iso)
        if not national:
            continue
        row["national_population"] = int(national)
        row["analysis_coverage_ratio"] = round(min(1.0, assessed / national), 3)
        row["national_phase3plus_pct"] = round(cnt / national * 100.0, 1)
        if assessed / national < 0.6:
            row["coverage_note"] = (
                f"phase3plus_pct is {pct}% of the {int(round(assessed)):,} people the IPC "
                f"analysis covered, not of {iso}'s {int(national):,} population. "
                f"National prevalence of the counted caseload is "
                f"{row['national_phase3plus_pct']}%. Areas outside the analysis are "
                f"unassessed, NOT known to be food-secure."
            )
            flagged += 1
    if flagged:
        print(f"  [ipc] {flagged} countries carry a subnational analysis denominator "
              f"— coverage annotated")
    return out


def main():
    out = {}
    try:
        out = _fetch_ewtool()
        source_label = "WFP HungerMap LIVE re-publishing IPC/CH (ew-tool-api .../ew/v1/ipc/food/insecurity/global/recent)"
    except Exception as e0:
        print(f"  [warn] ew-tool IPC failed ({e0}); trying legacy v2 endpoint")
        try:
            out = _fetch_hungermap()
            source_label = "WFP HungerMap re-publishes IPC (api.hungermapdata.org/v2/ipc.json)"
        except Exception as e:
            print(f"  [warn] HungerMap IPC failed ({e}); falling back to official endpoint")
            try:
                out = _fetch_official()
                source_label = "IPC Info official (api.ipcinfo.org)"
            except Exception as e2:
                print(f"  [warn] Official IPC also failed: {e2}")
                out = {}
                source_label = "IPC sources unavailable"

    out = _merge_palestine(out)
    out = _annotate_coverage(out)

    write_json(
        "ipc.json",
        out,
        source=source_label,
        notes=(
            "Phase 3 = Crisis, Phase 4 = Emergency, Phase 5 = Catastrophe/Famine. "
            "PSE is synthesised: the feed keys Gaza (PSG) and the West Bank (PSW) "
            "separately, and every consumer here looks up PSE. "
            f"Covered {len(out)} countries."
        ),
    )


def _fetch_hungermap():
    r = http_get(URL_HUNGERMAP, timeout=45)
    raw = r.json()
    if isinstance(raw, dict) and isinstance(raw.get("body"), (list, dict)):
        raw = raw["body"]
    rows = raw if isinstance(raw, list) else (raw.get("countries") or [])

    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        iso3 = (row.get("iso3") or row.get("adm0_code") or "").upper()
        if not iso3 or len(iso3) != 3 or not iso3.isalpha():
            continue
        pop_aff = _int(row.get("ipc_population_affected") or row.get("phase3plus_population"))
        ipc_pct = _num(row.get("ipc_percent") or row.get("phase3plus_percentage"))
        out[iso3] = {
            "phase3plus_pct": ipc_pct,
            "phase3plus_count": pop_aff,
            "phase4_count": _int(row.get("phase_4_plus_population")),
            "phase5_count": _int(row.get("phase_5_population")),
            "period": row.get("analysis_period") or row.get("period"),
            "analysis_date": row.get("date_of_analysis"),
            "country": row.get("adm0_name") or row.get("country"),
            "source_via": "hungermap",
        }
    if not out:
        raise RuntimeError("HungerMap IPC returned zero rows")
    return out


def _fetch_official():
    """Original IPC endpoint — gated by IPC_API_KEY since May 2026."""
    key = env("IPC_API_KEY", required=False)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    out = {}
    for year in (2026, 2025):
        try:
            r = http_get(URL_OFFICIAL, params={"type": "A", "year": year, "format": "json"}, headers=headers)
        except Exception:
            continue
        for row in r.json():
            iso3 = (row.get("country") or row.get("iso3") or "").upper()
            if not iso3 or iso3 in out:
                continue
            out[iso3] = {
                "phase3plus_pct": _num(row.get("phase3plus_percentage") or row.get("p3_plus_pct")),
                "phase3plus_count": _int(row.get("phase3plus_population")),
                "phase4_count": _int(row.get("phase4_population")),
                "phase5_count": _int(row.get("phase5_population") or row.get("famine_population")),
                "period": row.get("period_dates") or row.get("period"),
                "analysis_year": year,
                "source_via": "ipcinfo",
            }
    if not out:
        raise RuntimeError("Official IPC returned zero rows")
    return out


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
