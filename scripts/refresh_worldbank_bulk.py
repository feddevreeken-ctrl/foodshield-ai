"""
World Bank WDI — bulk extension.

Pipeline 4 of the structural-data series. The original refresh_worldbank.py
pulls 5 indicators via the REST API (5 round-trips × per_page=400, ~10s).
This script pulls ~20 indicators in a single bulk download (~64 MB ZIP) so
adding more indicators stays cheap and the FDRS structural component layer
gets sourced inputs instead of heuristics.

WHY BULK INSTEAD OF API:
  - API: 1 call per indicator × ~5s each = ~100s for 20 indicators
  - Bulk: 1 download (~30s) + stream-parse (~5s) = ~35s flat, scales free
  - WDI refreshes quarterly upstream — the bulk file is the canonical source
  - Per-indicator REST returns one most-recent value; bulk gives full history
    so we can carry forward stale-but-sparse series (e.g. stunting, refreshed
    every 3-5 years per country)

INDICATOR SET (20 series, all confirmed valid in WDI catalog May 2026):

  STRUCTURAL ECONOMY:
    NY.GDP.PCAP.CD        GDP per capita, current USD
    NV.AGR.TOTL.ZS        Agriculture, forestry, fishing value-added (% of GDP)
    FP.CPI.TOTL.ZG        Inflation, consumer prices (annual %)
    NE.IMP.GNFS.ZS        Imports of goods & services (% of GDP)

  TRADE — FOOD:
    TM.VAL.FOOD.ZS.UN     Food imports (% of merchandise imports)
    TX.VAL.FOOD.ZS.UN     Food exports (% of merchandise exports)

  AGRICULTURE — PRODUCTION:
    AG.PRD.FOOD.XD        Food production index (2014-16 = 100)
    AG.PRD.LVSK.XD        Livestock production index (2014-16 = 100)
    AG.YLD.CREL.KG        Cereal yield (kg/hectare)
    AG.LND.AGRI.ZS        Agricultural land (% of land area)
    AG.LND.IRIG.AG.ZS     Agricultural irrigated land (% of total ag land)
    AG.CON.FERT.ZS        Fertilizer consumption (kg/ha of arable land)

  FOOD SECURITY — HUMAN OUTCOMES:
    SN.ITK.DEFC.ZS        Prevalence of undernourishment (% of population)
    SN.ITK.MSFI.ZS        Prevalence of moderate/severe food insecurity (FIES)
    SH.STA.STNT.ZS        Stunting prevalence, height-for-age (% under 5)
    SH.STA.WAST.ZS        Wasting prevalence, weight-for-height (% under 5)

  WATER / CLIMATE / GOVERNANCE:
    ER.H2O.FWST.ZS        Level of water stress: freshwater withdrawn / available (SDG 6.4.2)

  POPULATION:
    SP.POP.TOTL           Total population
    SP.POP.GROW           Population growth (annual %)
    SP.RUR.TOTL.ZS        Rural population (% of total)

GOTCHAS HANDLED:
  - WB country codes include aggregates (WLD, EUU, HIC, etc.) — we filter
    against a known ISO3 set.
  - Many indicators have multi-year null tails. We pick the most recent
    NON-NULL year per (country, indicator), recording the year so the UI
    can show vintage.
  - Stunting / wasting refresh every 3-5 years per country — same logic
    catches them, no special-casing needed.

OUTPUT: data/worldbank_bulk.json
  {
    "_meta": {...},
    "data": {
      "BGD": {
        "NY.GDP.PCAP.CD": {"value": 2530.0, "year": 2023, "name": "GDP per capita (current USD)"},
        "NV.AGR.TOTL.ZS": {"value": 11.7, "year": 2023, "name": "Agriculture, value added (% of GDP)"},
        ...
      },
      ...
    }
  }

Wired into:
  - run_all.py STEPS list (runs every 6h alongside other refreshes)
  - build_source_manifest.py (one feed entry, mode='reference')
  - foodshield-v19.html: new LIVE.wdi_bulk loader + selected indicators on
    the country panel.
"""
import csv
import io
import zipfile

from _common import http_get, write_json

BULK_URL = "https://databank.worldbank.org/data/download/WDI_CSV.zip"

# Wide-CSV main file inside the ZIP. Naming has been stable since 2019.
WIDE_CSV_FILENAME_PATTERN = "WDICSV.csv"

# Indicator code → human-readable name (so we don't have to parse WDISeries.csv).
# Names mirror the WDI catalog exactly. If WB renames an indicator we'd see it
# in the CSV's "Indicator Name" column; we prefer that when present.
INDICATORS = {
    # Structural economy
    "NY.GDP.PCAP.CD":      "GDP per capita (current US$)",
    "NV.AGR.TOTL.ZS":      "Agriculture, forestry, and fishing, value added (% of GDP)",
    "FP.CPI.TOTL.ZG":      "Inflation, consumer prices (annual %)",
    "NE.IMP.GNFS.ZS":      "Imports of goods and services (% of GDP)",
    # Trade — food
    "TM.VAL.FOOD.ZS.UN":   "Food imports (% of merchandise imports)",
    "TX.VAL.FOOD.ZS.UN":   "Food exports (% of merchandise exports)",
    # Agriculture — production
    "AG.PRD.FOOD.XD":      "Food production index (2014-2016 = 100)",
    "AG.PRD.LVSK.XD":      "Livestock production index (2014-2016 = 100)",
    "AG.YLD.CREL.KG":      "Cereal yield (kg per hectare)",
    "AG.LND.AGRI.ZS":      "Agricultural land (% of land area)",
    "AG.LND.IRIG.AG.ZS":   "Agricultural irrigated land (% of total agricultural land)",
    "AG.CON.FERT.ZS":      "Fertilizer consumption (kilograms per hectare of arable land)",
    # Food security — human outcomes
    "SN.ITK.DEFC.ZS":      "Prevalence of undernourishment (% of population)",
    "SN.ITK.MSFI.ZS":      "Prevalence of moderate or severe food insecurity (%)",
    "SH.STA.STNT.ZS":      "Prevalence of stunting, height for age (% of children under 5)",
    "SH.STA.WAST.ZS":      "Prevalence of wasting, weight for height (% of children under 5)",
    # Water / climate
    "ER.H2O.FWST.ZS":      "Level of water stress: freshwater withdrawal as a proportion of available freshwater resources",
    # Population
    "SP.POP.TOTL":         "Population, total",
    "SP.POP.GROW":         "Population growth (annual %)",
    "SP.RUR.TOTL.ZS":      "Rural population (% of total population)",
}

# Known ISO3 set for filtering. WDI includes ~80 regional/income aggregates
# (WLD, EUU, HIC, MIC, LIC, SSA, EAS, etc.) — keep them out. We reuse the
# canonical FAOSTAT mapping (already 174 countries) plus a few small states
# that FBS skips but WDI covers.
KNOWN_ISO3 = set()
try:
    from refresh_faostat_fbs import FAO_AREA_TO_ISO3
    KNOWN_ISO3.update(FAO_AREA_TO_ISO3.values())
except Exception:
    pass
# Augment with countries WDI tracks that FAOSTAT FBS skips. Pulled from the
# WDICountry.csv reference list (~217 economies). Hand-curated to ISO3 only.
KNOWN_ISO3.update({
    "AND", "ABW", "BMU", "BES", "VGB", "CYM", "CHI", "CUW", "FRO", "GIB",
    "GRL", "GUM", "IMN", "JPN", "LIE", "MCO", "NCL", "PRI", "SXM", "SMR",
    "MAF", "SOM", "SSD", "SXM", "PSE", "TKL", "TCA", "VIR", "ESH", "BRN",
    "SGP", "PSE", "MNP", "AIA", "ASM", "FRO", "MTQ", "REU", "MYT", "ALA",
})


def main():
    print(f"[INFO] WB WDI bulk download → {BULK_URL}")
    try:
        r = http_get(
            BULK_URL,
            timeout=180,
            headers={"Accept": "application/zip,*/*"},
            retries=3,
        )
    except Exception as e:
        write_json(
            "worldbank_bulk.json", {},
            source="World Bank WDI bulk",
            notes=f"Bulk download failed: {e}",
        )
        return

    zip_bytes = r.content
    if not zip_bytes or len(zip_bytes) < 1024:
        write_json(
            "worldbank_bulk.json", {},
            source="World Bank WDI bulk",
            notes=f"Empty body ({len(zip_bytes) if zip_bytes else 0} bytes)",
        )
        return
    print(f"[INFO] Downloaded {len(zip_bytes)//1024//1024} MB ZIP")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        write_json(
            "worldbank_bulk.json", {},
            source="World Bank WDI bulk",
            notes=f"ZIP parse failed: {e}",
        )
        return

    csv_name = None
    for name in zf.namelist():
        # Be tolerant — WB has shipped both WDICSV.csv and WDIData.csv variants.
        lower = name.lower()
        if lower.endswith(".csv") and ("wdicsv" in lower or "wdidata" in lower):
            csv_name = name
            break
    if not csv_name:
        # Fall back to first CSV
        for name in zf.namelist():
            if name.endswith(".csv"):
                csv_name = name
                break
    if not csv_name:
        write_json(
            "worldbank_bulk.json", {},
            source="World Bank WDI bulk",
            notes=f"No CSV in ZIP. Members: {zf.namelist()}",
        )
        return
    print(f"[INFO] Reading {csv_name}")

    # Stream-parse the wide CSV. Columns: Country Name, Country Code,
    # Indicator Name, Indicator Code, 1960, 1961, ..., 2024.
    out = {}   # iso3 → {indicator_code: {value, year, name}}
    rows_seen = 0
    rows_kept = 0

    with zf.open(csv_name, "r") as f:
        text_stream = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text_stream)
        header = next(reader, None)
        if not header:
            write_json(
                "worldbank_bulk.json", {},
                source="World Bank WDI bulk",
                notes="CSV had no header row",
            )
            return

        # Identify year columns (everything that's a 4-digit integer)
        year_indices = []
        for i, col in enumerate(header):
            col = (col or "").strip()
            if col.isdigit() and len(col) == 4:
                year_indices.append((i, int(col)))
        # Sort newest year first so we find latest non-null fast
        year_indices.sort(key=lambda x: x[1], reverse=True)

        if not year_indices:
            write_json(
                "worldbank_bulk.json", {},
                source="World Bank WDI bulk",
                notes=f"No year columns in header: {header[:8]}",
            )
            return

        # Column indices for fixed columns
        try:
            i_country_code = header.index("Country Code")
            i_indicator_code = header.index("Indicator Code")
            i_indicator_name = header.index("Indicator Name")
        except ValueError as e:
            write_json(
                "worldbank_bulk.json", {},
                source="World Bank WDI bulk",
                notes=f"Required header column missing: {e}. Header: {header[:10]}",
            )
            return

        for row in reader:
            rows_seen += 1
            if rows_seen % 50000 == 0:
                print(f"  [progress] {rows_seen} rows scanned, {rows_kept} kept, "
                      f"{len(out)} countries so far")

            if len(row) < len(header):
                continue
            iso3 = (row[i_country_code] or "").strip().upper()
            if not iso3 or iso3 not in KNOWN_ISO3:
                continue
            ind_code = (row[i_indicator_code] or "").strip()
            if ind_code not in INDICATORS:
                continue

            ind_name = (row[i_indicator_name] or "").strip() or INDICATORS[ind_code]

            # Find the most recent year with a non-empty value
            latest_year = None
            latest_val = None
            for idx, year in year_indices:
                cell = row[idx] if idx < len(row) else ""
                if cell is None:
                    continue
                cell_s = str(cell).strip()
                if not cell_s:
                    continue
                try:
                    latest_val = float(cell_s)
                    latest_year = year
                    break
                except (TypeError, ValueError):
                    continue

            if latest_val is None:
                continue

            out.setdefault(iso3, {})[ind_code] = {
                "value": _round(latest_val, ind_code),
                "year": latest_year,
                "name": ind_name,
            }
            rows_kept += 1

    print(f"[INFO] Parsed {rows_seen} rows total; kept {rows_kept} indicator-country "
          f"cells across {len(out)} countries")

    # Per-country indicator coverage histogram (a couple of bands)
    coverage_buckets = {">15": 0, "10-15": 0, "5-9": 0, "1-4": 0, "0": 0}
    for iso, payload in out.items():
        n = len(payload)
        if n > 15:        coverage_buckets[">15"] += 1
        elif n >= 10:     coverage_buckets["10-15"] += 1
        elif n >= 5:      coverage_buckets["5-9"] += 1
        elif n >= 1:      coverage_buckets["1-4"] += 1
        else:             coverage_buckets["0"] += 1
    print(f"[INFO] Country indicator coverage: {coverage_buckets}")

    # Sanity check — log a couple of well-known reference points
    for ref in ("USA", "BGD", "BRA", "NLD", "AFG"):
        if ref in out:
            n = len(out[ref])
            gdp = out[ref].get("NY.GDP.PCAP.CD", {}).get("value")
            stunt = out[ref].get("SH.STA.STNT.ZS", {}).get("value")
            print(f"  [ref] {ref}: {n} indicators, gdp_pc=${gdp}, stunting%={stunt}")

    write_json(
        "worldbank_bulk.json",
        out,
        source="World Bank WDI bulk download (databank.worldbank.org/data/download/WDI_CSV.zip)",
        notes=(
            f"20 indicators × ~{len(out)} countries. Most-recent non-null year "
            f"per (country, indicator). Source refreshes quarterly upstream "
            f"(Jan, Apr, Jul, Oct); we re-download every 6h but the file rarely "
            f"changes between WB releases. ISO3-only — WB aggregates (WLD, EUU, "
            f"HIC, etc.) are filtered out."
        ),
    )


def _round(value, ind_code):
    """Round sensibly per indicator unit. Some series are percentages (0-100),
    some are indices, some are absolute populations. Avoid spurious precision."""
    if ind_code in ("SP.POP.TOTL",):
        return int(round(value))
    if ind_code in ("NY.GDP.PCAP.CD", "AG.CON.FERT.ZS", "AG.YLD.CREL.KG"):
        return round(value, 1)
    return round(value, 2)


if __name__ == "__main__":
    main()
