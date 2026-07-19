"""
UN Comtrade — bilateral trade in cereal staples (wheat, maize, rice, soy).

PUBLIC PREVIEW ENDPOINT (v20.8, May 2026) — no API key required.

Earlier versions used /data/v1/get/C/A/HS which requires a paid Comtrade subscription
(the "Free APIs" subscription doesn't actually grant access; both header- and
query-string auth modes returned HTTP 403). The free public preview endpoint
/public/v1/preview/C/A/HS works without authentication for read access. Verified
May 19 2026 against Egypt 2024 wheat (HS 1001) → 16 supplier rows returned.

Endpoint: https://comtradeapi.un.org/public/v1/preview/C/A/HS

Rate limits on the public endpoint appear less strict than the v1/get path, but
we still throttle conservatively (~1 req/sec).

Output: data/comtrade_staples.json
  {
    iso3_importer: {
      commodity_name: {
        "total_kt": <thousand tonnes imported>,
        "total_value_usd": <USD value>,
        "top_suppliers": [{"iso3", "share_pct", "value_usd"}, ...]
      }
    }
  }

Commodity HS codes:
  1001  Wheat
  1005  Maize / corn
  1006  Rice
  1201  Soybeans
  1511  Palm oil
  1701  Sugar (cane / beet, raw or refined)
  0901  Coffee
  1801  Cocoa beans (raw)
  3102  Nitrogenous fertilizers (covers urea)
  0201  Bovine meat, fresh/chilled
"""
import json
import time
from collections import defaultdict, deque
from pathlib import Path

import requests
from _common import env, write_json, UA

URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

# M49 numeric → ISO3 — needed because the public preview endpoint returns
# partnerCode (numeric) but partnerISO is null.
#
# CANONICAL SOURCE (v23): the single source of truth for this map is
# trade_pipeline/config.py (M49_TO_ISO3), which includes the corrected USA code
# (842, not 840), the alternate codes Comtrade uses (699=IND, 251=FRA, 757=CHE),
# and deliberately EXCLUDES non-country aggregates (490/899). We import it so this
# legacy refresher and the trade_pipeline can never diverge — the bug that made
# the same country resolve differently in two scripts. The inline dict below is a
# fallback only, used if the import fails (e.g. run from an odd CWD).
try:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "trade_pipeline"))
    from config import M49_TO_ISO3 as _CANON_M49
except Exception:
    _CANON_M49 = None

_M49_TO_ISO3_FALLBACK = {
    4:"AFG",8:"ALB",12:"DZA",24:"AGO",32:"ARG",36:"AUS",40:"AUT",50:"BGD",
    51:"ARM",56:"BEL",68:"BOL",70:"BIH",72:"BWA",76:"BRA",84:"BLZ",90:"SLB",
    96:"BRN",100:"BGR",104:"MMR",108:"BDI",112:"BLR",116:"KHM",120:"CMR",124:"CAN",
    132:"CPV",140:"CAF",144:"LKA",148:"TCD",152:"CHL",156:"CHN",158:"TWN",170:"COL",
    178:"COG",180:"COD",188:"CRI",191:"HRV",192:"CUB",196:"CYP",203:"CZE",208:"DNK",
    214:"DOM",218:"ECU",222:"SLV",226:"GNQ",231:"ETH",232:"ERI",233:"EST",242:"FJI",
    246:"FIN",250:"FRA",262:"DJI",266:"GAB",268:"GEO",270:"GMB",276:"DEU",288:"GHA",
    300:"GRC",320:"GTM",324:"GIN",328:"GUY",332:"HTI",340:"HND",344:"HKG",348:"HUN",
    352:"ISL",356:"IND",360:"IDN",364:"IRN",368:"IRQ",372:"IRL",376:"ISR",380:"ITA",
    384:"CIV",388:"JAM",392:"JPN",398:"KAZ",400:"JOR",404:"KEN",408:"PRK",410:"KOR",
    414:"KWT",417:"KGZ",418:"LAO",422:"LBN",426:"LSO",428:"LVA",430:"LBR",434:"LBY",
    440:"LTU",442:"LUX",446:"MAC",450:"MDG",454:"MWI",458:"MYS",462:"MDV",466:"MLI",
    470:"MLT",478:"MRT",480:"MUS",484:"MEX",496:"MNG",498:"MDA",499:"MNE",504:"MAR",
    508:"MOZ",512:"OMN",516:"NAM",524:"NPL",528:"NLD",548:"VUT",554:"NZL",558:"NIC",
    562:"NER",566:"NGA",578:"NOR",586:"PAK",591:"PAN",598:"PNG",600:"PRY",604:"PER",
    608:"PHL",616:"POL",620:"PRT",624:"GNB",626:"TLS",634:"QAT",642:"ROU",643:"RUS",
    646:"RWA",682:"SAU",686:"SEN",688:"SRB",694:"SLE",702:"SGP",703:"SVK",704:"VNM",
    705:"SVN",706:"SOM",710:"ZAF",716:"ZWE",724:"ESP",728:"SSD",729:"SDN",732:"ESH",
    740:"SUR",748:"SWZ",752:"SWE",756:"CHE",760:"SYR",762:"TJK",764:"THA",768:"TGO",
    780:"TTO",784:"ARE",788:"TUN",792:"TUR",795:"TKM",800:"UGA",804:"UKR",818:"EGY",
    826:"GBR",834:"TZA",840:"USA",854:"BFA",858:"URY",860:"UZB",862:"VEN",882:"WSM",
    887:"YEM",894:"ZMB",
}
# Use the canonical map when available; otherwise the fallback above.
M49_TO_ISO3 = _CANON_M49 if _CANON_M49 else _M49_TO_ISO3_FALLBACK

COMMODITIES = {
    "1001": "wheat",
    "1005": "maize",
    "1006": "rice",
    "1201": "soybeans",
    # v21 expansion (May 2026): six more commodities so the drilldown can
    # show observed bilateral trade for palm oil, sugar, coffee, cocoa, fertilizer
    # and beef. ~25 importers × 10 commodities = 250 calls/day, still under the
    # free-tier 500/day quota.
    "1511": "palm_oil",
    "1701": "sugar",
    "0901": "coffee",
    "1801": "cocoa",
    "3102": "fertilizer",
    "0201": "beef",
}

# Free tier rate limit: appears to be ~1 request per second.
# Sleep generously between calls and back off hard on 429.
THROTTLE_SECONDS = 1.5

# Priority importers (M.49 codes), chosen for high import dependency or strategic
# relevance to FoodShield's nowcast layer. ~25 countries × 4 commodities = 100 calls/day,
# well under the 500/day quota.
PRIORITY_IMPORTERS = {
    818: "EGY",  # Egypt — largest wheat importer
    360: "IDN",  # Indonesia
    156: "CHN",  # China
    792: "TUR",  # Turkey
    50:  "BGD",  # Bangladesh
    231: "ETH",  # Ethiopia
    566: "NGA",  # Nigeria
    24:  "AGO",  # Angola
    646: "RWA",  # Rwanda
    729: "SDN",  # Sudan
    682: "SAU",  # Saudi Arabia
    784: "ARE",  # UAE
    400: "JOR",  # Jordan
    422: "LBN",  # Lebanon
    887: "YEM",  # Yemen
    332: "HTI",  # Haiti
    862: "VEN",  # Venezuela
    192: "CUB",  # Cuba
    192: "CUB",
    608: "PHL",  # Philippines
    458: "MYS",  # Malaysia
    704: "VNM",  # Vietnam
    410: "KOR",  # South Korea
    392: "JPN",  # Japan
    826: "GBR",
    276: "DEU",
    250: "FRA",
    380: "ITA",
    724: "ESP",
    528: "NLD",
}


# A 429 is a temporary "come back later", not an answer. Re-queue it instead of
# dropping the call (see the requeue loop in main()).
MAX_RETRIES_PER_CALL = 3
BACKOFF_SECONDS = 30

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "comtrade_staples.json"


def _coverage(payload):
    """(importers, importer×commodity pairs) in a comtrade_staples data dict."""
    if not isinstance(payload, dict):
        return 0, 0
    pairs = sum(len(v) for v in payload.values() if isinstance(v, dict))
    return len(payload), pairs


# A KEEP protects good data from our own rate-limiting. But it must never be
# able to hold a stale snapshot forever: if the source genuinely shrinks, every
# subsequent run would also shrink, and (because 429s are routine here — 65-69
# skipped calls is typical) every run would KEEP. Past this age we publish
# whatever we have, loudly, rather than serving a file that can never refresh.
MAX_KEEP_AGE_DAYS = 7


def _existing_age_days():
    """Age of the file on disk in days, or None if absent/unreadable/undated."""
    if not OUTPUT_PATH.exists():
        return None
    try:
        from datetime import datetime, timezone
        meta = json.loads(OUTPUT_PATH.read_text()).get("_meta") or {}
        stamp = meta.get("generated_at")
        if not stamp:
            return None
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - then).total_seconds() / 86400.0
    except Exception as e:
        print(f"  [warn] could not read age of {OUTPUT_PATH.name} ({e})")
        return None


def _existing_coverage():
    """Coverage of the file already on disk, or (0, 0) if there is none."""
    if not OUTPUT_PATH.exists():
        return 0, 0
    try:
        return _coverage(json.loads(OUTPUT_PATH.read_text()).get("data") or {})
    except Exception as e:
        print(f"  [warn] could not read existing {OUTPUT_PATH.name} ({e}); "
              f"treating existing coverage as zero")
        return 0, 0


def _is_aggregate(row, key):
    """True when `key` marks this row as the un-broken-out aggregate.

    Absent/null/empty counts as aggregate: the endpoint omits the field entirely
    when it returns no breakdown. An unparseable value counts as a breakout and
    is dropped — double-counting is a silent correctness bug, while dropping one
    odd row shows up plainly in the totals.
    """
    v = row.get(key)
    if v in (None, "", 0, "0"):
        return True
    try:
        return int(v) == 0
    except (TypeError, ValueError):
        return False


def _clean_rows(rows):
    """Drop Comtrade breakout rows so the same trade is not counted twice.

    The public preview endpoint returns the same trade three ways: once as an
    aggregate (motCode=0, partner2Code=0), again split per transport mode
    (motCode=1,2,...), and again per second partner (partner2Code=899). The
    per-mode rows sum exactly to the motCode=0 total, so summing every returned
    row multiplies the real figure.

    Measured inflation before this filter (2024 wheat imports, saved vs clean):
    GBR 3,548,448,325 vs 887,112,081 (4.00x); ESP 10,021,151,433 vs 942,444,829
    (~11x); DEU 3.05x — while EGY/ITA/NLD were already 1.00x. The saved file
    therefore mixed correct and inflated importers, which is worse than being
    uniformly wrong: no single scale factor corrects it.

    Cross-check: UKR wheat exports 2024 clean = 20.66 Mt / $3.74bn = $181/t,
    consistent with reality (~16-20 Mt at ~$200/t). The naive sum gave $7.47bn,
    exactly 2x.
    """
    return [
        row for row in rows
        if _is_aggregate(row, "motCode") and _is_aggregate(row, "partner2Code")
    ]


def main():
    # v20.8: public preview endpoint requires no auth. We still read COMTRADE_API_KEY
    # for backward compat — if you later upgrade to a paid subscription, the key can
    # be used to bump rate limits on the protected endpoint.
    key = env("COMTRADE_API_KEY", required=False)
    if key:
        print("  [info] COMTRADE_API_KEY present but public endpoint used (no auth needed)")

    out = defaultdict(lambda: defaultdict(lambda: {"total_kt": 0, "total_value_usd": 0, "by_supplier": defaultdict(lambda: {"kt": 0, "value_usd": 0})}))
    year = 2024  # most recent full year for free tier as of May 2026
    skipped = 0
    succeeded = 0
    dropped_rows = 0   # v45: breakout rows filtered by _clean_rows()

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})

    # v20.8: public preview endpoint, no auth needed.
    # Note: omit partnerCode parameter entirely — leaving it blank returns 0 rows on the
    # public endpoint; omitting it returns the full supplier breakdown.

    # Work queue rather than a nested for-loop, so a rate-limited call can go to
    # the BACK of the queue instead of being abandoned. Previously a 429 hit
    # `skipped += 1; continue` and that importer/commodity pair simply vanished
    # from the run. Because which calls get throttled is effectively arbitrary,
    # consecutive runs lost and gained different pairs (a verified rerun churned
    # 24 pairs each way), so the published coverage wandered at random.
    queue = deque(
        (reporter_code, importer_iso, cmd_code, cmd_name, 0)
        for reporter_code, importer_iso in PRIORITY_IMPORTERS.items()
        for cmd_code, cmd_name in COMMODITIES.items()
    )
    total_calls = len(queue)
    requeued = 0
    exhausted = []

    while queue:
        reporter_code, importer_iso, cmd_code, cmd_name, attempt = queue.popleft()
        # Throttle to stay polite to the public endpoint (~1 req/sec).
        time.sleep(THROTTLE_SECONDS)
        params = {
            "cmdCode": cmd_code,
            "flowCode": "M",
            "reporterCode": reporter_code,
            "period": year,
            "max": 500,
        }

        def _retry(reason):
            """Send this call to the back of the queue, or give up on it."""
            nonlocal requeued, skipped
            if attempt + 1 < MAX_RETRIES_PER_CALL:
                requeued += 1
                queue.append((reporter_code, importer_iso, cmd_code, cmd_name,
                              attempt + 1))
                print(f"    {importer_iso}/{cmd_name}: {reason} — re-queued "
                      f"(attempt {attempt + 2}/{MAX_RETRIES_PER_CALL})")
            else:
                skipped += 1
                exhausted.append(f"{importer_iso}/{cmd_name}")
                print(f"    {importer_iso}/{cmd_name}: {reason} — giving up after "
                      f"{MAX_RETRIES_PER_CALL} attempts")

        try:
            r = session.get(URL, params=params, timeout=45)
        except Exception as e:
            _retry(f"network: {e}")
            continue
        if r.status_code == 429:
            # Temporary, not an answer. Back off, then retry this exact call
            # later in the run rather than dropping it from coverage.
            time.sleep(BACKOFF_SECONDS)
            _retry("429 rate-limited")
            continue
        if r.status_code in (500, 502, 503, 504):
            time.sleep(BACKOFF_SECONDS)
            _retry(f"HTTP {r.status_code}")
            continue
        if r.status_code != 200:
            # 4xx other than 429 is a real answer ("no such series"): not retryable.
            print(f"    {importer_iso}/{cmd_name}: HTTP {r.status_code}")
            skipped += 1
            continue
        try:
            payload = r.json()
        except Exception as e:
            _retry(f"unparseable body: {e}")
            continue
        raw_rows = payload.get("data", []) or []
        # v45: filter transport-mode / second-partner breakouts before any
        # accumulation. Without this the same trade is summed several times.
        rows = _clean_rows(raw_rows)
        dropped_rows += len(raw_rows) - len(rows)
        succeeded += 1
        for row in rows:
            # Public preview endpoint returns partnerISO as null; only partnerCode
            # (numeric M49) is populated. Resolve to ISO3 via the reverse lookup.
            # netWgt is also null on the public endpoint — primaryValue is the only
            # value signal. We store it as raw USD in total_value_usd/value_usd and
            # keep the legacy total_usd_m/usd_m aliases for backward compatibility.
            # Skip the kt conversion that the paid endpoint supported.
            p_code = row.get("partnerCode")
            if not p_code or p_code == 0:
                continue
            sup = M49_TO_ISO3.get(int(p_code))
            if not sup:
                continue
            value_usd = row.get("primaryValue") or 0
            if value_usd <= 0:
                continue
            # Public preview values in current saved files are stored as raw USD.
            # Example: Egypt 2024 wheat total is ~4.44e9 in data/comtrade_staples.json.
            entry = out[importer_iso][cmd_name]
            entry["total_value_usd"] += value_usd
            # netWgt is null on public preview — we cannot compute kt. Set to 0
            # so downstream code knows volumes aren't available; UI must label as
            # "obs · aggregate (USD only)" rather than implying kt accuracy.
            s = entry["by_supplier"][sup]
            s["value_usd"] += value_usd

    print(f"  Fetched {succeeded}/{total_calls} commodity-importer combos; "
          f"re-queued {requeued}; skipped {skipped}; "
          f"dropped {dropped_rows} breakout rows (mot/partner2 duplicates)")
    if exhausted:
        print(f"  Calls exhausted after {MAX_RETRIES_PER_CALL} attempts "
              f"({len(exhausted)}): {', '.join(exhausted[:20])}"
              + (" ..." if len(exhausted) > 20 else ""))

    # v20.8: public preview endpoint does not return netWgt — share_pct is USD-based.
    # Top-5 suppliers per (importer, commodity), ranked by USD value.
    final = {}
    for imp, commodities in out.items():
        final[imp] = {}
        for cmd_name, e in commodities.items():
            total_usd = e["total_value_usd"]
            suppliers = [
                {"iso3": s, "value_usd": round(v["value_usd"], 2),
                 "usd_m": round(v["value_usd"], 2),
                 "share_pct": round(v["value_usd"] / total_usd * 100, 1) if total_usd else 0}
                for s, v in e["by_supplier"].items()
            ]
            suppliers.sort(key=lambda x: -x["value_usd"])
            final[imp][cmd_name] = {
                "total_kt": None,   # not available on public preview endpoint
                "total_value_usd": round(total_usd, 2),
                "total_usd_m": round(total_usd, 2),
                "top_suppliers": suppliers[:5],
                "value_basis": ("USD (primaryValue, aggregate rows only: "
                                "motCode=0 AND partner2Code=0; v45 dedup)"),
            }

    # ── Coverage-regression guard ────────────────────────────────────────────
    # The old guard only refused to overwrite when this run produced FEWER THAN
    # FIVE importers. That number has no relationship to what is already on disk,
    # so a run returning 19 of 29 importers with 69 calls lost to rate-limiting
    # sailed straight through and replaced a more complete file. Because
    # throttling hits arbitrary calls, reruns churned coverage in both directions
    # (a verified rerun lost 24 pairs and gained 24 others).
    #
    # The comparison is now against the EXISTING FILE, at importer×commodity pair
    # granularity — the level at which the churn actually happened; importer count
    # alone stays flat while pairs rotate underneath it.
    #
    # A genuine upstream shrink still gets through: when nothing was lost to
    # retryable failures (skipped == 0), a smaller result is real news about the
    # source rather than an artefact of our own rate-limiting, and is published.
    new_importers, new_pairs = _coverage(final)
    old_importers, old_pairs = _existing_coverage()
    print(f"  Coverage: {new_importers} importers / {new_pairs} pairs this run "
          f"vs {old_importers} / {old_pairs} on disk")

    shrank = new_pairs < old_pairs or new_importers < old_importers
    lost_pairs = max(0, old_pairs - new_pairs)
    age_days = _existing_age_days()

    # Each failed call can account for AT MOST one importer-commodity pair. If we
    # lost more pairs than we lost calls, our own rate-limiting cannot explain the
    # shrink and it is real news about the source.
    explained_by_our_failures = lost_pairs <= skipped

    # Backstop against permanent freeze. The previous guard only published a
    # shrink when skipped == 0, but 429s are routine here (65-69 skipped calls is
    # typical), so that branch was effectively unreachable: a genuine upstream
    # shrink would have been rejected on every future run, forever, while the
    # stale file kept being served.
    too_old_to_keep = age_days is not None and age_days > MAX_KEEP_AGE_DAYS

    if shrank and skipped and explained_by_our_failures and not too_old_to_keep:
        age_note = f"{age_days:.1f}d old" if age_days is not None else "age unknown"
        print(f"  [KEEP] coverage regressed ({old_pairs} -> {new_pairs} pairs, "
              f"{old_importers} -> {new_importers} importers) while {skipped} call(s) "
              f"failed after retries, so our rate-limiting explains it "
              f"({lost_pairs} pairs lost <= {skipped} calls failed). Keeping the "
              f"existing file ({age_note}) rather than churning coverage.")
        return

    if shrank:
        if not skipped:
            why = "zero failed calls"
        elif not explained_by_our_failures:
            why = (f"{lost_pairs} pairs lost exceeds {skipped} failed call(s), so "
                   f"rate-limiting cannot explain it")
        else:
            why = (f"the file on disk is {age_days:.1f}d old (limit "
                   f"{MAX_KEEP_AGE_DAYS}d) and must not freeze indefinitely")
        print(f"  [WARN] coverage shrank ({old_pairs} -> {new_pairs} pairs, "
              f"{old_importers} -> {new_importers} importers) — publishing anyway: "
              f"{why}.")

    write_json(
        "comtrade_staples.json",
        final,
        source=f"UN Comtrade Plus (comtradeapi.un.org) — HS6, year {year}",
        notes=(f"Top 5 suppliers per importer-commodity. ~25 priority importers (free-tier quota). "
               f"Succeeded: {succeeded}/{total_calls}, re-queued: {requeued}, skipped: {skipped}. "
               f"Coverage this run: {new_importers} importers / {new_pairs} importer-commodity "
               f"pairs (previous file: {old_importers} / {old_pairs}). "
               f"Rate-limited (429) calls are re-queued up to {MAX_RETRIES_PER_CALL} times rather "
               f"than dropped, and a coverage regression caused by failed calls keeps the previous "
               f"file instead of overwriting it. "
               f"v45: values count Comtrade aggregate rows only (motCode=0 AND partner2Code=0). "
               f"The public-preview endpoint also returns per-transport-mode and per-second-partner "
               f"copies of the same trade; summing all rows overstated USD totals for a subset of "
               f"importers by ~3-11x (GBR 4.00x, DEU 3.05x, ESP ~11x; EGY/ITA/NLD were unaffected). "
               f"Dropped {dropped_rows} breakout rows this run. Values are raw USD despite the "
               f"legacy usd_m / total_usd_m field names."),
    )


if __name__ == "__main__":
    main()
