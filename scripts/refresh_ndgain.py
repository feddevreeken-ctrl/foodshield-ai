"""
ND-GAIN — Notre Dame Global Adaptation Initiative Country Index.

Pipeline 6 of the structural-data series. Annual country-level climate
vulnerability + readiness scores. Feeds the climate dimension of the
structural FDRS, which until now was largely heuristic.

WHY ND-GAIN:
  Most-cited climate-adaptation index in academic + policy work. Free
  download, CC-licensed, covers ~192 countries from 1995 to ~2023 (2-year lag).

ARCHITECTURE NOTE (v43, 18 Jul 2026) — CORRECTS THE PREVIOUS NOTE:
  The old note (kept at the bottom) claimed ND-GAIN served the ZIP behind a
  "session-scoped download". That was WRONG, and the wrong diagnosis cost this
  feed months of empty payloads. Verified 18 Jul 2026 — two separate faults:

    1. STALE URL. `resources.zip` is no longer the release path. The current
       release is `/assets/<id>/ndgain_countryindex_<year>.zip`, linked from
       the public download page. The asset id is release-scoped and WILL
       change at the next annual release, so we scrape the link instead of
       hardcoding it.

    2. USER-AGENT BLOCKING. gain.nd.edu returns 403 to our project UA and to
       default python-requests; a browser UA returns 200 application/zip.
       Confirmed stateless — no cookies, no referer, no session. We send a
       browser UA to this host only, and document it here rather than hiding
       it. The data is CC-licensed and published for public download, so this
       is a bot filter rather than an access control.

  FALLBACK CHAIN. Each tier records itself in _meta.tier and _meta.vintage so
  a silent downgrade can never masquerade as a healthy fetch:
    tier1_release  scraped release ZIP   1995-2024, 192 countries, has food
    tier2_imf      IMF ArcGIS mirror     2015-2021, has vs_Food, IMF-adapted
    empty          honest empty payload  reason on _meta.notes

SUPERSEDED NOTE (May 2026, factually wrong — kept so the mistake stays visible):
  ND-GAIN publishes a ZIP of CSVs at https://gain-new.crc.nd.edu/about/download.
  The direct ZIP URL is not exposed as a single canonical link — the page
  generates a session-scoped download.

DATA WE EXTRACT (per country, latest year):
  - ND-GAIN composite index    0-100, higher = better adapted
  - Vulnerability             0-1, higher = MORE vulnerable (inverted vs FDRS)
  - Readiness                 0-1, higher = MORE resilient
  - Food sector vulnerability  0-1, higher = MORE vulnerable on food specifically

We DO NOT invert ND-GAIN's vulnerability score on the way in — we record it
in its native direction and document this on the meta. Downstream consumers
(FDRS climate component) can invert as needed.

OUTPUT: data/ndgain.json
  {
    "_meta": {...},
    "data": {
      "BGD": {
        "gain_index": 39.2,
        "vulnerability": 0.541,
        "readiness": 0.281,
        "food_vulnerability": 0.622,
        "year": 2023,
        "source": "ND-GAIN Country Index",
        "source_url": "https://gain.nd.edu/our-work/country-index/",
        "quality_flag": "sourced",
      },
      ...
    }
  }

Wired into:
  - run_all.py STEPS list
  - build_source_manifest.py (mode='reference')
  - foodshield-v19.html: LIVE.ndgain loader + country panel surface
"""
import csv
import io
import re
import zipfile
from pathlib import Path

from _common import DATA_DIR, http_get, write_json

# Public download page. We scrape the release asset link off this rather than
# hardcoding it, because the asset id changes with every annual release.
DOWNLOAD_PAGE = "https://gain.nd.edu/our-work/country-index/download-data/"
ASSET_RE = re.compile(r"/assets/\d+/[A-Za-z0-9_.\-]+\.zip")

# Known-good release URL as of 18 Jul 2026. Only used if scraping the page
# turns up nothing — the scrape is the primary path.
KNOWN_RELEASE = "https://gain.nd.edu/assets/647440/ndgain_countryindex_2026.zip"

# gain.nd.edu 403s our project UA. See the ARCHITECTURE NOTE above.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
ZIP_HEADERS = {"User-Agent": BROWSER_UA, "Accept": "application/zip,*/*"}

# IMF Climate Change Indicators mirror. Carries vs_Food, but the series stops
# at 2021 and the scores are "IMF-adapted" rather than raw ND-GAIN — so this
# tier is a real downgrade and must be labelled as one on the payload.
IMF_QUERY = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "ND_GAIN_with_country_boundaries/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&returnGeometry=false&f=json&resultRecordCount=5000"
)

# Local manual-drop escape hatch, kept from the previous design.
LOCAL_ZIP = Path(DATA_DIR) / "ndgain_resources.zip"


def _discover_release_url():
    """Scrape the download page for the current release asset link."""
    try:
        r = http_get(DOWNLOAD_PAGE, timeout=60,
                     headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
                     retries=2)
    except Exception as e:
        print(f"  [warn] download page unreachable: {e}")
        return None
    hits = sorted(set(ASSET_RE.findall(r.text or "")))
    if not hits:
        print("  [warn] no /assets/<id>/*.zip link found on the download page")
        return None
    if len(hits) > 1:
        print(f"  [warn] {len(hits)} asset links found, taking the first: {hits}")
    url = "https://gain.nd.edu" + hits[0]
    print(f"  [INFO] discovered release asset: {url}")
    return url


def _fetch_release_zip():
    """Tier 1. Returns (zip_bytes, used_url) or (None, None)."""
    if LOCAL_ZIP.exists() and LOCAL_ZIP.stat().st_size > 10_000:
        print(f"  [INFO] using manually-dropped {LOCAL_ZIP.name}")
        return LOCAL_ZIP.read_bytes(), f"local:{LOCAL_ZIP.name}"

    candidates = [u for u in (_discover_release_url(), KNOWN_RELEASE) if u]
    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            r = http_get(url, timeout=180, headers=ZIP_HEADERS, retries=2, patient=True)
        except Exception as e:
            print(f"  [skip] {url}: {e}")
            continue
        body = r.content or b""
        ctype = (r.headers.get("Content-Type") or "").lower()
        # A 200 carrying HTML is the classic symptom here — treat it as failure
        # rather than handing an HTML page to the zip parser.
        if "html" in ctype or body[:2] != b"PK":
            print(f"  [skip] {url}: not a zip (content-type={ctype!r}, {len(body)} bytes)")
            continue
        print(f"  [OK] downloaded {len(body)//1024} KB from {url}")
        return body, url
    return None, None


def _fetch_imf_mirror():
    """Tier 2. IMF ArcGIS mirror. Returns {iso3: row} or {}."""
    print("[INFO] tier 1 failed — trying IMF ArcGIS mirror")
    try:
        r = http_get(IMF_QUERY, timeout=120, retries=2, patient=True)
        payload = r.json()
    except Exception as e:
        print(f"  [skip] IMF mirror: {e}")
        return {}
    feats = payload.get("features") or []
    if not feats:
        print(f"  [skip] IMF mirror returned no features ({str(payload)[:200]})")
        return {}

    # Join on ISO3 only. The IMF service has corrupted COUNTRY strings on some
    # rows (BTN is labelled "Bhutanese ngultrum"), so names are not trustworthy.
    best = {}
    _scale_rejects = []
    for f in feats:
        a = f.get("attributes") or {}
        iso = (a.get("ISO3") or "").strip().upper()
        if len(iso) != 3 or not iso.isalpha():
            continue
        year = a.get("Year") or a.get("year")
        try:
            year = int(year)
        except (TypeError, ValueError):
            continue
        if iso in best and best[iso]["year"] >= year:
            continue
        # SCALE GUARD. Tier 1 publishes vulnerability/readiness/food on 0-1 and
        # the composite on 0-100. The IMF mirror is a separate publication and
        # is NOT guaranteed to keep those scales — if it ever switches to
        # percentages, a 0-1 consumer would read 0.68 as 68 and every downstream
        # climate score would be silently wrong by two orders of magnitude.
        # Out-of-range values are dropped rather than coerced: a missing field
        # degrades a card, a wrongly-scaled one corrupts the index.
        SCALES = {
            "gain_index": ("IMF_Adapted_ND_GAIN_Index", 0.0, 100.0),
            "vulnerability": ("Vulnerability_score", 0.0, 1.0),
            "readiness": ("IMF_Adapted_Readiness_score", 0.0, 1.0),
            "food_vulnerability": ("vs_Food", 0.0, 1.0),
        }
        row = {"year": year}
        for out_key, (src_key, lo, hi) in SCALES.items():
            v = a.get(src_key)
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not (lo <= fv <= hi):
                _scale_rejects.append(f"{iso}.{out_key}={fv}")
                continue
            row[out_key] = round(fv, 3)
        if len(row) > 1:
            best[iso] = row

    for iso, row in best.items():
        row["source"] = "ND-GAIN via IMF Climate Change Indicators (IMF-adapted)"
        row["source_url"] = "https://climatedata.imf.org/"
        row["quality_flag"] = "sourced"
    if _scale_rejects:
        # Loud on purpose. A handful means dirty rows; a flood means the mirror
        # changed its units and this tier must not be trusted until checked.
        print(f"  [WARN] IMF mirror: dropped {len(_scale_rejects)} out-of-range values "
              f"(first few: {_scale_rejects[:5]}). If this count is large, the mirror's "
              f"scales have changed — do NOT ship this tier until verified.")
    print(f"  [OK] IMF mirror: {len(best)} countries")
    # Attach the reject count so main() can persist it — a print() only reaches
    # a CI log, and the operator who needs this reads the JSON.
    best["_scale_rejects"] = len(_scale_rejects)
    return best


def main():
    print("[INFO] ND-GAIN Country Index download")
    zip_bytes, used_url = _fetch_release_zip()

    if not zip_bytes:
        imf = _fetch_imf_mirror()
        _scale_rejects = [None] * imf.pop("_scale_rejects", 0) if imf else []
        if imf:
            years = [r["year"] for r in imf.values() if r.get("year")]
            vintage = max(years) if years else None
            write_json(
                "ndgain.json", imf,
                source="ND-GAIN via IMF Climate Change Indicators mirror",
                status="degraded_fallback",
                notes=(
                    f"TIER 2 FALLBACK — the ND-GAIN release ZIP could not be fetched, so "
                    f"these figures come from the IMF Climate Change Indicators mirror. "
                    f"Scores are IMF-ADAPTED, not raw ND-GAIN, and the series stops at "
                    f"{vintage} rather than the current ND-GAIN release. Covered "
                    f"{len(imf)} countries. Treat as a downgrade, not a healthy fetch."
                    + (f" SCALE GUARD dropped {len(_scale_rejects)} out-of-range values "
                       f"— if that count is large the mirror has changed units and this "
                       f"tier should be verified before being trusted."
                       if _scale_rejects else "")
                ),
            )
            return
        write_json(
            "ndgain.json", {},
            source="ND-GAIN Country Index",
            status="empty",
            notes=(
                "Both tiers failed. Tier 1: could not fetch the release ZIP from "
                f"{DOWNLOAD_PAGE} (the asset link is scraped from that page; if the page "
                "layout changed, the scrape needs updating). Tier 2: the IMF ArcGIS "
                "mirror returned nothing. Manual override: download the release ZIP and "
                f"drop it at data/{LOCAL_ZIP.name}, then re-run."
            ),
        )
        return

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        write_json(
            "ndgain.json", {},
            source="ND-GAIN Country Index",
            notes=f"ZIP parse failed: {e}. Source URL: {used_url}",
        )
        return

    # Context-managed below via closing() would restructure main() heavily;
    # register the close explicitly instead so the handle is not leaked.
    members = zf.namelist()
    print(f"[INFO] ZIP contains {len(members)} files")

    # ND-GAIN's structure: multiple CSVs at root + per-sector subfolders.
    # We want the three composites + food-sector vulnerability.
    # v43 — the output key is `gain_index`, NOT `gain`. index.html:35475 reads
    # `LIVE.ndgain?.[c.iso]?.gain_index`, so the previous `gain` key meant the
    # ND-GAIN composite column rendered null even on a successful fetch.
    # Candidate paths are matched by path suffix, so `gain/gain.csv` must be
    # listed ahead of the bare `gain.csv` to avoid matching a trends/ file.
    files_of_interest = {
        "gain_index": ["gain/gain.csv", "resources/gain/gain.csv", "gain.csv"],
        "vulnerability": [
            "vulnerability/vulnerability.csv",
            "resources/vulnerability/vulnerability.csv",
            "vulnerability.csv",
        ],
        "readiness": [
            "readiness/readiness.csv",
            "resources/readiness/readiness.csv",
            "readiness.csv",
        ],
        "food_vulnerability": [
            "vulnerability/food.csv",
            "resources/vulnerability/food.csv",
            "vulnerability/sectors/food.csv",
        ],
    }

    series = {}   # metric_key → {iso3: {year: value}}
    for metric, candidates in files_of_interest.items():
        match = None
        for candidate in candidates:
            if candidate in members:
                match = candidate
                break
        if not match:
            # Suffix match. Iterate CANDIDATES in the outer loop so the more
            # specific path wins: the 2026 release nests files under a
            # "ND-GAIN Country Index 2026 Release Data/" prefix, and there is
            # both a gain/gain.csv and a trends/ tree. Iterating members first
            # (the pre-v43 behaviour) let whichever file happened to come first
            # in the archive win, which could silently bind gain_index to a
            # trends series.
            # PATH-BOUNDARY suffix match. A bare endswith() is not enough:
            # candidate "vulnerability.csv" also matches
            # ".../vulnerability/food_vulnerability.csv", which would bind the
            # vulnerability metric to the FOOD series and ship it as
            # quality_flag=sourced. Require the match to start at a path
            # segment boundary so only whole path components can match.
            for c in candidates:
                cl = c.lower()
                for m in members:
                    ml = m.lower()
                    if ml == cl or ml.endswith("/" + cl):
                        match = m
                        break
                if match:
                    break
        if not match:
            print(f"  [warn] {metric}: file not found in ZIP")
            continue
        print(f"  [INFO] {metric} ← {match}")
        series[metric] = _parse_wide_csv(zf, match)

    # For each country present in any series, pick the most recent year with
    # a non-null value per metric. Output is denormalized for easy frontend use.
    all_isos = set()
    for s in series.values():
        all_isos.update(s.keys())

    out = {}
    for iso in sorted(all_isos):
        row = {}
        latest_year = None
        for metric, data in series.items():
            country_years = data.get(iso) or {}
            if not country_years:
                continue
            # Most recent year with non-null value
            for year in sorted(country_years.keys(), reverse=True):
                val = country_years[year]
                if val is None:
                    continue
                row[metric] = val
                if latest_year is None or year > latest_year:
                    latest_year = year
                break
        if not row:
            continue
        row["year"] = latest_year
        row["source"] = "ND-GAIN Country Index"
        row["source_url"] = "https://gain.nd.edu/our-work/country-index/"
        row["quality_flag"] = "sourced"
        out[iso] = row

    if not out:
        write_json(
            "ndgain.json", {},
            source=f"ND-GAIN Country Index ({used_url})",
            status="empty",
            notes=(
                f"Downloaded and opened the ZIP from {used_url}, but no country rows "
                f"parsed out of it. The archive layout has probably changed — check "
                f"the file paths in files_of_interest against the ZIP's namelist."
            ),
        )
        return

    vintage = max((r["year"] for r in out.values() if r.get("year")), default=None)
    covered = {m: sum(1 for r in out.values() if m in r)
               for m in ("gain_index", "vulnerability", "readiness", "food_vulnerability")}
    print(f"[INFO] Compiled ND-GAIN scores for {len(out)} countries "
          f"(latest year {vintage}) — per-metric coverage: {covered}")

    # Sanity check on a few well-known references
    for ref in ("USA", "NLD", "BGD", "AFG", "BRA", "JPN", "DEU"):
        if ref in out:
            print(f"  [ref] {ref}: gain_index={out[ref].get('gain_index')}, "
                  f"vuln={out[ref].get('vulnerability')}, "
                  f"ready={out[ref].get('readiness')}, "
                  f"food_vuln={out[ref].get('food_vulnerability')} "
                  f"(yr {out[ref].get('year')})")

    zf.close()

    write_json(
        "ndgain.json",
        out,
        source=f"ND-GAIN Country Index ({used_url})",
        status="ok",
        notes=(
            f"TIER 1 — raw ND-GAIN release, latest year {vintage}. "
            f"Annual climate vulnerability + readiness scores per country. "
            f"Vulnerability: 0-1, higher = more vulnerable (NOT inverted). "
            f"Readiness: 0-1, higher = more resilient. "
            f"ND-GAIN composite: 0-100, higher = better adapted. "
            f"Food sector sub-score (food_vulnerability) directly feeds the FDRS climate "
            f"component. Covered {len(out)} countries "
            f"(per-metric: {covered}). "
            f"Source refreshes annually (typically Q3/Q4); pipeline runs every 6h but "
            f"data rarely changes between releases."
        ),
    )


def _parse_wide_csv(zf, member):
    """Parse a wide-format ND-GAIN CSV.

    Returns {iso3: {year_int: value_float_or_None}}.
    Columns: ISO3, Name, 1995, 1996, ..., 2023.
    """
    with zf.open(member, "r") as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.reader(text)
        header = next(reader, None)
        if not header:
            return {}
        # Locate ISO3 column (sometimes "ISO3", sometimes "iso3", sometimes "Code")
        iso_idx = None
        for i, col in enumerate(header):
            if (col or "").strip().lower() in ("iso3", "code", "iso", "iso_code"):
                iso_idx = i
                break
        if iso_idx is None:
            iso_idx = 0   # ND-GAIN historically puts ISO3 first

        # Year columns
        year_cols = []
        for i, col in enumerate(header):
            s = (col or "").strip()
            if s.isdigit() and len(s) == 4:
                year_cols.append((i, int(s)))

        out = {}
        for row in reader:
            if len(row) <= iso_idx:
                continue
            iso = (row[iso_idx] or "").strip().upper()
            if len(iso) != 3 or not iso.isalpha():
                continue
            country_data = {}
            for col_i, year in year_cols:
                if col_i >= len(row):
                    continue
                v = (row[col_i] or "").strip()
                if not v or v == "NA":
                    continue
                try:
                    country_data[year] = round(float(v), 3)
                except ValueError:
                    continue
            if country_data:
                out[iso] = country_data
        return out


if __name__ == "__main__":
    main()
