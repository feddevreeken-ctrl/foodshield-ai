"""
EU JRC INFORM Risk Index.

Pipeline 9 of the structural-data series. Composite humanitarian risk score
per country, updated annually by the EU Joint Research Centre. Feeds the
conflict/logistics dimension of the structural FDRS — today still mostly
heuristic.

WHY INFORM:
  INFORM is the standard cross-country humanitarian risk composite used by
  OCHA, ECHO, World Bank, and most major NGOs. 0-10 scale. Released
  annually (autumn) with a mid-year revision (spring).

ARCHITECTURE NOTE:
  INFORM publishes a multi-sheet XLSX. We want sheet 'INFORM Risk 2026'
  (one row per country, ~191 rows). HDX hosts a stable mirror; we try the
  JRC primary first (version-suffixed filename), then HDX as fallback.

DOWNLOAD:
  Filename pattern: INFORM_Risk_2026_v0XX.xlsx (XX = version, increments
  on each release). We try a few known-good versions; the HDX mirror at
  data.humdata.org keeps a stable resource URL we fall back to.

INDICATORS WE EXTRACT:
  inform_risk           — composite 0-10
  hazard_exposure       — first dimension 0-10
  vulnerability         — second dimension 0-10
  lack_coping_capacity  — third dimension 0-10
  (optional sub-categories — not surfaced today but kept in payload)

OUTPUT: data/inform_risk.json
  {
    "_meta": {...},
    "data": {
      "AFG": {
        "inform_risk": 8.5,
        "hazard_exposure": 8.9,
        "vulnerability": 7.4,
        "lack_coping_capacity": 9.1,
        "rank": 3,
        "trend": "stable",
        "year": 2026,
        "source": "EU JRC INFORM Risk Index",
        "source_url": "https://drmkc.jrc.ec.europa.eu/inform-index/INFORM-Risk",
        "quality_flag": "sourced",
      },
      ...
    }
  }

Wired into:
  - run_all.py STEPS list
  - build_source_manifest.py (mode='reference')
  - foodshield-v19.html: LIVE.inform loader + informCardHTML(iso3) renderer
"""
import io
from datetime import datetime

from _common import http_get, write_json

# Candidate URLs in order. JRC filenames are versioned (v0XX); we guess a few
# plausible ones based on the May 2026 release window. HDX mirror is stable.
JRC_URLS = [
    "https://drmkc.jrc.ec.europa.eu/Portals/0/InfoRM/2026/INFORM_Risk_2026_v067.xlsx",
    "https://drmkc.jrc.ec.europa.eu/Portals/0/InfoRM/2026/INFORM_Risk_2026_v068.xlsx",
    "https://drmkc.jrc.ec.europa.eu/Portals/0/InfoRM/2026/INFORM_Risk_2026_v070.xlsx",
    "https://drmkc.jrc.ec.europa.eu/Portals/0/InfoRM/2026/INFORM_Risk_2026_v071.xlsx",
    "https://drmkc.jrc.ec.europa.eu/Portals/0/InfoRM/2026/INFORM_Risk_2026.xlsx",
]
HDX_BASE = "https://data.humdata.org/dataset/inform-risk-index-2021"  # human-facing dataset page
# HDX serves resources via /api/3/action/package_show — we attempt direct CSV mirror as well
HDX_CSV_FALLBACK = "https://data.humdata.org/dataset/4b860b3f-7c45-43a9-b9c5-23a7a9fa45e1/resource/13a55b78-aa9b-4f5b-99b8-c6a4f54168f8/download/inform-risk-2026.xlsx"


def main():
    xlsx_bytes = None
    used_url = None
    for url in (*JRC_URLS, HDX_CSV_FALLBACK):
        try:
            r = http_get(url, timeout=120, retries=2, patient=True)
            if r.content and len(r.content) > 5_000:
                xlsx_bytes = r.content
                used_url = url
                print(f"[OK] downloaded {len(xlsx_bytes)//1024} KB from {url}")
                break
            print(f"  [skip] {url}: response too small ({len(r.content) if r.content else 0} bytes)")
        except Exception as e:
            print(f"  [skip] {url}: {e}")

    if not xlsx_bytes:
        write_json(
            "inform_risk.json", {},
            source="EU JRC INFORM Risk Index",
            notes=(
                "All JRC + HDX download URLs failed. JRC versions filenames "
                "(INFORM_Risk_YYYY_vXXX.xlsx) so the candidate list needs updating "
                "with the current version. Check "
                "https://drmkc.jrc.ec.europa.eu/inform-index/INFORM-Risk/Results-and-data "
                "for the latest filename."
            ),
        )
        return

    try:
        import openpyxl
    except ImportError:
        write_json(
            "inform_risk.json", {},
            source="EU JRC INFORM Risk Index",
            notes="openpyxl not installed; cannot parse XLSX",
        )
        return

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    except Exception as e:
        write_json(
            "inform_risk.json", {},
            source="EU JRC INFORM Risk Index",
            notes=f"XLSX parse failed: {e}. URL was {used_url}.",
        )
        return

    # Find the sheet — naming is "INFORM Risk 2026" but year may differ.
    target_sheet = None
    for name in wb.sheetnames:
        low = name.lower().replace(" ", "")
        if "informrisk" in low and any(yr in low for yr in ("2025", "2026", "2027")):
            target_sheet = name
            break
    if not target_sheet:
        # Fall back to the first sheet that mentions INFORM
        for name in wb.sheetnames:
            if "inform" in name.lower():
                target_sheet = name
                break
    if not target_sheet:
        target_sheet = wb.sheetnames[0]
    print(f"[INFO] Reading sheet '{target_sheet}' (available: {wb.sheetnames})")

    ws = wb[target_sheet]

    # Header row detection: scan rows 1-5 for one containing both 'ISO3' and 'INFORM RISK'.
    header_row = None
    header_cells = None
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=8, values_only=True), start=1):
        cells = [str(c or "").strip() for c in row]
        cells_upper = [c.upper() for c in cells]
        if any("ISO3" in c for c in cells_upper) and any("INFORM" in c and "RISK" in c for c in cells_upper):
            header_row = row_idx
            header_cells = cells
            break
    if header_row is None:
        write_json(
            "inform_risk.json", {},
            source="EU JRC INFORM Risk Index",
            notes=f"Could not locate header row in sheet '{target_sheet}'.",
        )
        return
    print(f"[INFO] Header found at row {header_row}")

    # Map column index to our shorthand key. Column names in INFORM are uppercase
    # with spaces and '&' — normalize before matching.
    def norm(s):
        return (s or "").strip().upper().replace("&", "AND").replace(" ", "").replace("-", "")

    col_map = {}
    for i, cell in enumerate(header_cells):
        n = norm(cell)
        if n == "ISO3":
            col_map["iso3"] = i
        elif n == "COUNTRY":
            col_map["country"] = i
        elif n == "INFORMRISK":
            col_map["inform_risk"] = i
        elif n == "HAZARDANDEXPOSURE":
            col_map["hazard_exposure"] = i
        elif n == "VULNERABILITY":
            col_map["vulnerability"] = i
        elif n == "LACKOFCOPINGCAPACITY":
            col_map["lack_coping_capacity"] = i
        elif n == "RANK":
            col_map["rank"] = i
        elif n == "TREND" or n.startswith("INFORMTREND"):
            col_map["trend"] = i

    if "iso3" not in col_map or "inform_risk" not in col_map:
        write_json(
            "inform_risk.json", {},
            source="EU JRC INFORM Risk Index",
            notes=f"Required columns missing. Found: {list(col_map.keys())}. Header: {header_cells[:12]}",
        )
        return

    out = {}
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if not row:
            continue
        iso3_raw = row[col_map["iso3"]] if col_map["iso3"] < len(row) else None
        if not iso3_raw:
            continue
        iso3 = str(iso3_raw).strip().upper()
        if len(iso3) != 3 or not iso3.isalpha():
            continue

        payload = {
            "country": _str(row[col_map.get("country", -1)] if "country" in col_map else None),
            "inform_risk":         _num(row[col_map["inform_risk"]] if col_map["inform_risk"] < len(row) else None),
            "hazard_exposure":     _num(row[col_map.get("hazard_exposure", -1)] if "hazard_exposure" in col_map else None),
            "vulnerability":       _num(row[col_map.get("vulnerability", -1)] if "vulnerability" in col_map else None),
            "lack_coping_capacity": _num(row[col_map.get("lack_coping_capacity", -1)] if "lack_coping_capacity" in col_map else None),
            "rank":                _int(row[col_map.get("rank", -1)] if "rank" in col_map else None),
            "trend":               _str(row[col_map.get("trend", -1)] if "trend" in col_map else None),
            "year":                _detect_year(target_sheet),
            "source":              "EU JRC INFORM Risk Index",
            "source_url":          "https://drmkc.jrc.ec.europa.eu/inform-index/INFORM-Risk",
            "quality_flag":        "sourced",
        }
        # Skip rows where the composite is null — those are aggregate regions or no-data
        if payload["inform_risk"] is None:
            continue
        out[iso3] = payload

    print(f"[INFO] Compiled INFORM scores for {len(out)} countries")

    # Sanity check
    for ref in ("AFG", "SOM", "YEM", "SDN", "USA", "NLD", "BGD"):
        if ref in out:
            p = out[ref]
            print(f"  [ref] {ref}: risk={p.get('inform_risk')}, hazard={p.get('hazard_exposure')}, "
                  f"vuln={p.get('vulnerability')}, cope={p.get('lack_coping_capacity')}")

    write_json(
        "inform_risk.json",
        out,
        source=f"EU JRC INFORM Risk Index ({used_url})",
        notes=(
            f"Composite humanitarian risk score per country, 0-10 scale. "
            f"4 dimensions: Hazard & Exposure, Vulnerability, Lack of Coping Capacity, "
            f"and the INFORM Risk composite. Released annually by EU JRC; "
            f"mid-year revisions common. Covered {len(out)} countries. "
            f"Higher score = greater humanitarian risk."
        ),
    )


def _num(v):
    if v in (None, "", "x", "X", "N.D.", "n.d.", "NA"):
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _int(v):
    if v in (None, "", "x", "X", "N.D.", "n.d.", "NA"):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _str(v):
    if v is None or v in ("", "x", "X", "N.D."):
        return None
    return str(v).strip() or None


def _detect_year(sheet_name):
    for y in ("2026", "2027", "2025", "2024"):
        if y in sheet_name:
            return int(y)
    return datetime.utcnow().year


if __name__ == "__main__":
    main()
