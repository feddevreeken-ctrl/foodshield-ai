"""
World Bank Real-Time Food Prices (RTFP) — market-level food-price inflation.

WHY THIS FEED EXISTS (Aug 2026)
-------------------------------
The FDRS food-inflation component c[3] was reading data/faostat_food.json, and
that feed is measuring the wrong thing. 158 of its 162 countries carry
year_latest: 2026 with months_in_latest_year: 3 — a three-month average being
compared against a full prior year. That is a seasonality artefact, not
inflation, and it drove the component for 133 countries. On top of that, 146 of
176 rows flagged "sourced" carried as_of: null, so nothing downstream could
even tell how old the number was.

RTFP fixes both problems where it has coverage:
  * It is a genuine year-on-year comparison, published monthly at market level.
  * Every row here carries a REAL as_of taken from the data's own DATES column.

WHAT IT IS NOT
--------------
RTFP covers 37 crisis-exposed countries. It does NOT replace FAOSTAT globally —
it is a better source where it exists, and the integrator must blend, not swap
(see the note in _meta and the header of main()).

SOURCE
------
Dataset  : global-real-time-food-prices on HDX, org world-bank-group, CC BY 4.0
Grain    : one row per (market, month). ~25k rows / ~38 MB for the current year.
Method   : MEDIAN of inflation_food_price_index across a country's markets at
           that country's latest DATES. Median, not mean — market-level price
           series carry blown-out outliers (a single besieged or currency-
           collapsed market) and one of them must not set a country's number.

The resource UUID is resolved through the CKAN API rather than hardcoded so the
script survives a re-upload; the direct 2026 URL is kept as a documented
fallback below.

OUTPUT: data/rtfp.json
  { "data": { "ETH": {"food_inflation_pct": 46.7, "markets": 129,
              "as_of": "2026-08-01", "confidence": 0.95, "method": "...",
              "source": "...", "source_url": "...",
              "quality_flag": "sourced"} } }
"""
import csv
import re
import statistics
from decimal import Decimal, ROUND_HALF_UP

from _common import http_get, write_json

CKAN_PACKAGE = ("https://data.humdata.org/api/3/action/package_show"
                "?id=global-real-time-food-prices")

# Documented fallback: the 2026 resource verified 200 / ~38 MB / 25,296 rows on
# 2026-08-30. Only used if the CKAN API is unreachable or returns no usable
# CSV resource. It WILL go stale at year rollover — that is why CKAN is tried
# first, and why a stale-but-real answer beats no answer at all.
FALLBACK_URL = ("https://data.humdata.org/dataset/82efaf85-d581-4fa8-a6f9-e5b4fe2e8b94/"
                "resource/7632eab9-f823-46d6-9305-b7beb42a04fb/download/global_food_2026.csv")

SOURCE = "World Bank Real-Time Food Prices (RTFP) via HDX"
SOURCE_URL = "https://data.humdata.org/dataset/global-real-time-food-prices"
METHOD = "median of market-level year-on-year food price index inflation"

# Columns we actually read. Everything else in the ~700-column file is ignored,
# which is why this parses with csv.reader over fixed indices instead of
# DictReader — a 700-key dict per row x 25k rows is pure waste.
NEEDED = ("ISO3", "DATES", "geo_id", "mkt_name",
          "index_confidence_score", "inflation_food_price_index")

# Reference values computed from the 2026-08-01 snapshot, checked by hand
# against the published country pages. Printed on every run; a drift here means
# either the upstream revised or this parse broke, and it must be investigated,
# never tuned away.
REFERENCE = {
    "ETH": 46.7, "SDN": 21.1, "LBN": 8.0, "SOM": 5.7,
    "AFG": 3.0, "HTI": 2.2, "NGA": -1.7, "MOZ": -3.0,
}


def _resource_urls():
    """CSV download URLs from the HDX package, newest year first.

    Falls back to the hardcoded 2026 URL if CKAN is unreachable or shape-shifts.
    """
    try:
        pkg = http_get(CKAN_PACKAGE, timeout=60).json()
        if not pkg.get("success"):
            raise RuntimeError("CKAN returned success=false")
        candidates = []
        for res in pkg["result"].get("resources") or []:
            if (res.get("format") or "").upper() != "CSV":
                continue
            url = res.get("url")
            if not url:
                continue
            m = re.search(r"(20\d{2})", res.get("name") or url)
            if not m:
                continue
            candidates.append((int(m.group(1)), url))
        if not candidates:
            raise RuntimeError("no year-tagged CSV resources in package")
        candidates.sort(reverse=True)
        print(f"  [ok] CKAN resolved {len(candidates)} yearly CSVs; "
              f"newest {candidates[0][0]}")
        return [u for _, u in candidates]
    except Exception as e:
        print(f"  [warn] CKAN resolve failed ({e}); using hardcoded fallback URL")
        return [FALLBACK_URL]


def _collect(url):
    """Stream one yearly CSV -> {iso3: {date: [(geo_id, inflation, confidence)]}}.

    The response body is iterated line-by-line rather than materialising
    r.text — a second fully-decoded 38 MB copy of a file we only need six
    columns from.
    """
    r = http_get(url, timeout=180, retries=3, patient=True)
    reader = csv.reader(r.iter_lines(decode_unicode=True))
    try:
        header = next(reader)
    except StopIteration:
        raise RuntimeError(f"{url}: empty response")
    try:
        idx = {c: header.index(c) for c in NEEDED}
    except ValueError as e:
        raise RuntimeError(f"{url}: expected column missing from header ({e})")

    by_country = {}
    rows = 0
    for row in reader:
        if len(row) <= idx["inflation_food_price_index"]:
            continue
        rows += 1
        infl = _num(row[idx["inflation_food_price_index"]])
        if infl is None:
            continue
        iso3 = row[idx["ISO3"]].strip().upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue
        when = row[idx["DATES"]].strip()
        if not when:
            continue
        market = row[idx["geo_id"]].strip() or row[idx["mkt_name"]].strip()
        conf = _num(row[idx["index_confidence_score"]])
        by_country.setdefault(iso3, {}).setdefault(when, []).append((market, infl, conf))

    print(f"  [ok] {url.rsplit('/', 1)[-1]}: {rows} rows -> {len(by_country)} countries")
    return by_country


def main():
    errors = []
    by_country = {}
    for url in _resource_urls()[:2]:  # newest year, then last year at rollover
        try:
            by_country = _collect(url)
        except Exception as e:
            errors.append(f"{url.rsplit('/', 1)[-1]}: {e}")
            print(f"  [warn] {e}")
            continue
        if by_country:
            break
        errors.append(f"{url.rsplit('/', 1)[-1]}: parsed 0 countries")

    if not by_country:
        # Fail loudly: an empty payload carrying the reason, never a silent
        # partial or a fabricated number.
        write_json("rtfp.json", {}, source=SOURCE,
                   notes="RTFP fetch/parse failed: " + ("; ".join(errors) or "unknown error"),
                   status="fetch_failed")
        return 1

    out = {}
    for iso3, per_date in by_country.items():
        # Latest month this country actually reports — per country, not global.
        # A country lagging the others must carry its own honest as_of.
        as_of = max(per_date)
        rows = per_date[as_of]
        seen, vals, confs = set(), [], []
        for market, infl, conf in rows:
            if market in seen:      # defensive; upstream is unique per (iso3, date)
                continue
            seen.add(market)
            vals.append(infl)
            if conf is not None:
                confs.append(conf)
        if not vals:
            continue
        out[iso3] = {
            "food_inflation_pct": _round1(statistics.median(vals)),
            "markets": len(vals),
            "as_of": as_of,
            "confidence": round(statistics.median(confs), 2) if confs else None,
            "method": METHOD,
            "source": SOURCE,
            "source_url": SOURCE_URL,
            "quality_flag": "sourced",
        }

    if not out:
        write_json("rtfp.json", {}, source=SOURCE,
                   notes="RTFP parsed rows but produced no country medians",
                   status="no_rows")
        return 1

    as_of_dates = sorted({d["as_of"] for d in out.values()})
    print(f"[INFO] RTFP: {len(out)} countries | as_of {as_of_dates[0]}"
          + (f" .. {as_of_dates[-1]}" if len(as_of_dates) > 1 else "")
          + f" | {sum(d['markets'] for d in out.values())} markets total")
    if len(out) < 30:
        print(f"  [warn] only {len(out)} countries — RTFP normally publishes ~37. "
              f"Coverage may have been cut upstream.")

    # --- reference check -----------------------------------------------------
    print("\n  reference check (expected values verified 2026-08-30):")
    drift = []
    for iso3, expected in REFERENCE.items():
        got = out.get(iso3, {}).get("food_inflation_pct")
        if got is None:
            drift.append(f"{iso3} missing (expected {expected:+.1f})")
            print(f"    [MISS] {iso3}: absent from this run, expected {expected:+.1f}%")
            continue
        ok = abs(got - expected) <= 0.1
        print(f"    [{'ok ' if ok else 'DRIFT'}] {iso3}: {got:+.1f}%  "
              f"expected {expected:+.1f}%  (n={out[iso3]['markets']} markets, "
              f"as_of {out[iso3]['as_of']})")
        if not ok:
            drift.append(f"{iso3} {got:+.1f} vs expected {expected:+.1f}")
    if drift:
        # Reported, not corrected. A disagreement here is a finding about the
        # data or the parse, and tuning until it matches would destroy the only
        # signal that either has changed.
        print(f"  [WARN] {len(drift)} reference value(s) disagree: {'; '.join(drift)}")
    else:
        print("    all 8 reference countries match within 0.1pp\n")

    write_json(
        "rtfp.json", out,
        source=SOURCE,
        notes=(
            "Year-on-year food price inflation from the World Bank Real-Time Food "
            "Prices index, distributed via HDX (CC BY 4.0). One row per country: "
            "the MEDIAN of inflation_food_price_index across that country's markets "
            "at that country's own latest reporting month — median, not mean, "
            "because market-level price series carry outliers a single blown-out "
            "market must not be allowed to set. Every row's as_of is a real date "
            "read from the source's DATES column. "
            f"Coverage is {len(out)} crisis-exposed countries "
            f"(as_of {as_of_dates[0]}"
            + (f" .. {as_of_dates[-1]}" if len(as_of_dates) > 1 else "")
            + "). This does NOT replace FAOSTAT globally — FAOSTAT still carries "
            "the other ~125 countries, and RTFP is simply the better source where "
            "it exists. RTFP is a market-basket price index, not a national CPI "
            "food component: it is measured where humanitarian monitoring runs, "
            "so it reflects the markets poor households actually buy in, and it "
            "is not directly comparable with a FAOSTAT consumer price index."
            + (f" Partial run: {'; '.join(errors)}" if errors else "")
        ),
        status="ok",
    )
    return 0


def _num(v):
    try:
        return float(v) if v not in (None, "", "NA", "NaN", "..", "_Z") else None
    except (TypeError, ValueError):
        return None


def _round1(x):
    """Round half away from zero to 1dp.

    Python's round() is banker's rounding: round(46.65, 1) is 46.6, which would
    make the published number disagree with the hand-checked reference for no
    reason anyone reading it could reconstruct.
    """
    return float(Decimal(repr(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


if __name__ == "__main__":
    raise SystemExit(main())
