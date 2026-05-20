"""
ND-GAIN — Notre Dame Global Adaptation Initiative Country Index.

Pipeline 6 of the structural-data series. Annual country-level climate
vulnerability + readiness scores. Feeds the climate dimension of the
structural FDRS, which until now was largely heuristic.

WHY ND-GAIN:
  Most-cited climate-adaptation index in academic + policy work. Free
  download, CC-licensed, covers ~192 countries from 1995 to ~2023 (2-year lag).

ARCHITECTURE NOTE (May 2026):
  ND-GAIN publishes a ZIP of CSVs at https://gain-new.crc.nd.edu/about/download.
  The direct ZIP URL is not exposed as a single canonical link — the page
  generates a session-scoped download. We hit a small set of known mirror
  filenames; if they fail we fall back to writing an empty payload with a
  clear `_meta.notes` rather than crashing the workflow.

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
import zipfile

from _common import http_get, write_json

# ND-GAIN download URLs to try in order. The first is the historical canonical
# link from the previous CRC mirror; the others are reasonable guesses based on
# the published filename pattern. If none work, we write an empty payload with
# a clear note — the workflow keeps going.
URLS = [
    "https://gain-new.crc.nd.edu/api/v1/resources/zip",
    "https://gain-new.crc.nd.edu/download/resources.zip",
    "https://gain.nd.edu/sites/default/files/resources.zip",
]


def main():
    print("[INFO] ND-GAIN Country Index download")
    zip_bytes = None
    used_url = None
    for url in URLS:
        try:
            r = http_get(url, timeout=120, headers={"Accept": "application/zip,*/*"}, retries=2)
            if r.content and len(r.content) > 10_000:
                zip_bytes = r.content
                used_url = url
                print(f"  [OK] downloaded {len(zip_bytes)//1024} KB from {url}")
                break
            print(f"  [skip] {url}: response too small ({len(r.content) if r.content else 0} bytes)")
        except Exception as e:
            print(f"  [skip] {url}: {e}")

    if not zip_bytes:
        write_json(
            "ndgain.json", {},
            source="ND-GAIN Country Index",
            notes=(
                "All bulk-download URLs failed; ND-GAIN hosts the dataset behind a "
                "session-scoped redirect that needs browser-style fetching. "
                "Manual download: https://gain.nd.edu/our-work/country-index/download-data/ — "
                "drop resources.zip into data/ndgain_resources.zip and re-run."
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

    members = zf.namelist()
    print(f"[INFO] ZIP contains {len(members)} files")

    # ND-GAIN's structure: multiple CSVs at root + per-sector subfolders.
    # We want the three composites + food-sector vulnerability.
    files_of_interest = {
        "gain": ["gain.csv", "resources/gain.csv"],
        "vulnerability": ["vulnerability.csv", "resources/vulnerability.csv"],
        "readiness": ["readiness.csv", "resources/readiness.csv"],
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
            # Fuzzy match: any file whose path ends with the candidate basename
            for m in members:
                if any(m.lower().endswith(c.lower()) for c in candidates):
                    match = m
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

    print(f"[INFO] Compiled ND-GAIN scores for {len(out)} countries")

    # Sanity check on a few well-known references
    for ref in ("USA", "NLD", "BGD", "AFG", "BRA", "JPN", "DEU"):
        if ref in out:
            print(f"  [ref] {ref}: gain={out[ref].get('gain_index')}, "
                  f"vuln={out[ref].get('vulnerability')}, "
                  f"ready={out[ref].get('readiness')}, "
                  f"food_vuln={out[ref].get('food_vulnerability')} "
                  f"(yr {out[ref].get('year')})")

    write_json(
        "ndgain.json",
        out,
        source=f"ND-GAIN Country Index ({used_url})",
        notes=(
            f"Annual climate vulnerability + readiness scores per country. "
            f"Vulnerability: 0-1, higher = more vulnerable (NOT inverted). "
            f"Readiness: 0-1, higher = more resilient. "
            f"ND-GAIN composite: 0-100, higher = better adapted. "
            f"Food sector sub-score (food_vulnerability) directly feeds the FDRS climate "
            f"component. Covered {len(out)} countries. "
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
