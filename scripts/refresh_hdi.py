"""
UNDP Human Development Index (HDI) + composite indices.

Pipeline 15. Annual composite per country: HDI, life expectancy, schooling,
GNI per capita, gender inequality, inequality-adjusted HDI, planetary HDI.
Pairs nicely with INFORM's vulnerability dimension.

WHY HDI:
  HDI is the canonical cross-country human-development benchmark. Released
  annually by UNDP. The bulk Composite Indices CSV is the right interface —
  wide format, one row per country, dozens of indicators × years 1990-latest.

ARCHITECTURE NOTE (May 2026):
  HDR 2023-24 is the latest released. HDR 2025 may be out by now. We try
  a few candidate URLs in version-suffix order; fall back to the HDR23-24
  CSV which is the stable known-good fallback.

INDICATORS WE EXTRACT (per country, latest year):
  hdi        — Human Development Index (0-1)
  le         — Life expectancy at birth (years)
  eys        — Expected years of schooling (years)
  mys        — Mean years of schooling (years)
  gnipc      — GNI per capita (2017 PPP $)
  gii        — Gender Inequality Index (0-1, higher = worse)
  ihdi       — Inequality-adjusted HDI
  coi        — Overall loss in HDI from inequality (%)
  phdi       — Planetary-pressures-adjusted HDI

OUTPUT: data/hdi.json
  {
    "_meta": {...},
    "data": {
      "BGD": {
        "hdi":   {"value": 0.661, "year": 2022},
        "le":    {"value": 73.4, "year": 2022},
        "eys":   {"value": 12.4, "year": 2022},
        "mys":   {"value": 7.4,  "year": 2022},
        "gnipc": {"value": 6520, "year": 2022},
        "gii":   {"value": 0.498, "year": 2022},
        "ihdi":  {"value": 0.495, "year": 2022},
        "phdi":  {"value": 0.638, "year": 2022},
        "year":  2022,
        "hdi_rank": 129,
        "hdi_group": "Medium human development",
        "source": "UNDP Human Development Report",
        ...
      },
      ...
    }
  }

Wired into:
  - run_all.py STEPS list
  - build_source_manifest.py (mode='reference')
  - foodshield-v19.html: LIVE.hdi loader + hdiCardHTML(iso3) renderer
"""
import csv
import io

from _common import http_get, write_json

# Candidate URLs in preferred order (newest vintage first).
# UNDP rotates filename versions; HDR25 may be live by May 2026.
URLS = [
    # Try newest first
    "https://hdr.undp.org/sites/default/files/2025_HDR/HDR25_Composite_indices_complete_time_series.csv",
    "https://hdr.undp.org/sites/default/files/2024-25_HDR/HDR24-25_Composite_indices_complete_time_series.csv",
    "https://hdr.undp.org/sites/default/files/2023-24_HDR/HDR23-24_Composite_indices_complete_time_series.csv",
    # Stable historical fallback
    "https://hdr.undp.org/sites/default/files/2021-22_HDR/HDR21-22_Composite_indices_complete_time_series.csv",
]

# Column-name prefixes in the wide CSV → our shorthand key.
# UNDP columns look like: hdi_2022, le_2022, eys_2022, mys_2022, gnipc_2022,
#                         gii_2022, ihdi_2022, coi_2022, phdi_2022, etc.
SERIES_PREFIXES = {
    "hdi":   ("hdi",   "Human Development Index"),
    "le":    ("le",    "Life expectancy at birth"),
    "eys":   ("eys",   "Expected years of schooling"),
    "mys":   ("mys",   "Mean years of schooling"),
    "gnipc": ("gnipc", "GNI per capita (2017 PPP $)"),
    "gii":   ("gii",   "Gender Inequality Index"),
    "ihdi":  ("ihdi",  "Inequality-adjusted HDI"),
    "coi":   ("coi",   "Coefficient of human inequality"),
    "phdi":  ("phdi",  "Planetary-pressures-adjusted HDI"),
}


def main():
    text = None
    used_url = None
    for url in URLS:
        try:
            r = http_get(url, timeout=120, retries=2)
            if r.text and len(r.text) > 5000:
                text = r.text
                used_url = url
                print(f"[OK] downloaded {len(text)//1024} KB from {url}")
                break
            print(f"  [skip] {url}: response too small")
        except Exception as e:
            print(f"  [skip] {url}: {e}")

    if not text:
        write_json(
            "hdi.json", {},
            source="UNDP HDR Composite Indices",
            notes="All bulk-download URLs failed. UNDP rotates filename versions; check https://hdr.undp.org/data-center/documentation-and-downloads for the latest.",
        )
        return

    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    if not header:
        write_json("hdi.json", {}, source="UNDP HDR", notes="CSV had no header")
        return

    # Identify year columns per series. UNDP uses lowercase prefix + _YYYY.
    # Build: {short_key: [(year_int, column_name), ...]} sorted newest first.
    series_columns = {key: [] for key in SERIES_PREFIXES}
    for col in header:
        col_low = col.strip().lower()
        for prefix in SERIES_PREFIXES:
            # Match exact 'prefix_YYYY' (avoid e.g. 'hdi_rank' being matched by 'hdi')
            if col_low.startswith(prefix + "_"):
                suffix = col_low[len(prefix) + 1:]
                if suffix.isdigit() and len(suffix) == 4:
                    series_columns[prefix].append((int(suffix), col))
                    break
    for prefix in series_columns:
        series_columns[prefix].sort(key=lambda x: x[0], reverse=True)

    # Also try to find HDI rank + group columns
    rank_col = next((c for c in header if c.lower() in ("hdi_rank", "rank")), None)
    group_col = next((c for c in header if c.lower() in ("hdicode", "hdi_group", "group")), None)
    iso_col = next((c for c in header if c.strip().lower() in ("iso3", "country code", "code")), None)
    name_col = next((c for c in header if c.strip().lower() in ("country", "name", "country_name")), None)
    if not iso_col:
        # UNDP usually puts ISO3 first
        iso_col = header[0]

    out = {}
    rows_seen = 0
    for row in reader:
        rows_seen += 1
        iso = (row.get(iso_col) or "").strip().upper()
        if len(iso) != 3 or not iso.isalpha():
            continue
        country = (row.get(name_col, "") if name_col else "").strip()

        payload = {
            "country": country or None,
            "source": "UNDP Human Development Report",
            "source_url": "https://hdr.undp.org/data-center/documentation-and-downloads",
            "quality_flag": "sourced",
        }
        latest_overall_year = None
        for prefix, cols in series_columns.items():
            short_key, label = SERIES_PREFIXES[prefix]
            for year, col_name in cols:
                cell = (row.get(col_name) or "").strip()
                if not cell or cell == "..":
                    continue
                try:
                    val = float(cell)
                except ValueError:
                    continue
                payload[short_key] = {
                    "value": round(val, 3) if prefix != "gnipc" else round(val, 1),
                    "year": year,
                    "label": label,
                }
                if latest_overall_year is None or year > latest_overall_year:
                    latest_overall_year = year
                break  # most-recent non-null per series

        if not any(k in payload for k in SERIES_PREFIXES):
            continue

        if latest_overall_year is not None:
            payload["year"] = latest_overall_year
        if rank_col:
            try:
                rk = int(float(row.get(rank_col) or 0))
                if rk > 0:
                    payload["hdi_rank"] = rk
            except (TypeError, ValueError):
                pass
        if group_col:
            g = (row.get(group_col) or "").strip()
            if g:
                payload["hdi_group"] = g

        out[iso] = payload

    print(f"[INFO] Parsed {rows_seen} CSV rows; kept {len(out)} countries")

    for ref in ("USA", "NOR", "CHE", "NLD", "DEU", "BGD", "AFG", "SOM", "NER"):
        if ref in out:
            p = out[ref]
            hdi = p.get("hdi", {}).get("value")
            le = p.get("le", {}).get("value")
            gni = p.get("gnipc", {}).get("value")
            print(f"  [ref] {ref}: hdi={hdi}, life_exp={le}, gni_pc={gni} (yr {p.get('year')})")

    write_json(
        "hdi.json",
        out,
        source=f"UNDP Human Development Report ({used_url})",
        notes=(
            f"Annual human-development composite + sub-indices per country. "
            f"HDI score 0-1 (higher = better). Latest year populated per series. "
            f"Covered {len(out)} countries. "
            f"Series: HDI, Life expectancy, Expected/Mean years of schooling, "
            f"GNI per capita, Gender Inequality, Inequality-adjusted HDI, "
            f"Coefficient of human inequality, Planetary-pressures HDI."
        ),
    )


if __name__ == "__main__":
    main()
