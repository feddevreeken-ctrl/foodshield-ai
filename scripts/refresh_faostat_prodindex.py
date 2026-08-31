"""
FAOSTAT Production Indices — per-country food production TREND.

v83 — replaces the last of the three hand-authored FDRS components.

WHY THIS EXISTS
  FDRS component c[2] `prod_trend` carries weight 0.11 and, like c[0] and c[1]
  before it, was a hand-authored literal for all 214 countries: no source, no
  data year, stamped `as_of: "2026-05"` — an authoring date, not a vintage.
  Together the three accounted for 0.50 of the score. An audit put it plainly:
  nothing measurable determined half of some countries' risk, so nothing
  measurable could move it.

WHY THIS SOURCE
  FAOSTAT's Gross Production Index (2014-2016 = 100) is a single chained index,
  already comparable across countries, so a trend is a slope over the index
  rather than a bespoke tonnage aggregation with its own weighting choices to
  defend. The "Food" item aggregate is the right scope for a food-security
  dashboard — narrower than "Agriculture" (which carries fibre and tobacco).

  Rejected: WDI AG.PRD.FOOD.XD, which is frozen at 2022. USDA PSD, which covers
  ~40 countries for wheat and none of the small island states.

METHOD
  Ordinary least-squares slope of the index over the most recent 10 years,
  expressed as average annual % change against the window's mean level, then
  mapped to a 0-100 fragility score.

  The mapping is ASYMMETRIC and saturates at -5%/yr:
      -5%/yr or worse -> 100      (production genuinely collapsing)
      -1%/yr          ->  20
       0%/yr or better ->   0     (flat or growing is not a production problem)

  The first cut of this was symmetric and saturated at +/-3%/yr, which anchored
  FLAT production at 50 — mid-fragility — and blew up ordinary drift into large
  scores. Most countries sit inside +/-1%/yr, so it was amplifying noise: Germany
  at -0.75%/yr scored 63 while Sudan at +0.45%/yr scored 43, i.e. Germany read as
  the more fragile food producer. Wrong on its face.

  Flat output in a mature, diversified agricultural economy is not fragility;
  only sustained DECLINE is. Hence one-sided, and a wider band so that a real
  collapse still has room to separate from a wobble.

  A slope is used rather than first-vs-last because a single bad harvest year
  at either end would otherwise set the trend for a decade.

  Countries with fewer than 5 usable index points are emitted with a null score
  and `quality_flag: "partial"` — NOT a 0. A missing trend is not a healthy one.

OUTPUT: data/faostat_prod_index.json
"""
import csv
import io
import zipfile

from _common import http_get, write_json

BULK_URL = ("https://bulks-faostat.fao.org/production/"
            "Production_Indices_E_All_Data_(Normalized).zip")

ELEMENT = "Gross Production Index Number (2014-2016 = 100)"
ITEM = "Food"
WINDOW_YEARS = 10
MIN_POINTS = 5


def _load_area_iso3():
    """FAO area code -> ISO3, reusing the canonical map already in the repo."""
    try:
        from refresh_faostat_fbs import FAO_AREA_TO_ISO3, NAME_TO_ISO3
        return FAO_AREA_TO_ISO3, NAME_TO_ISO3
    except Exception:
        return {}, {}


def _slope_pct_per_year(points):
    """OLS slope of value on year, as % of the window mean."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    num = sum((p[0] - mx) * (p[1] - my) for p in points)
    den = sum((p[0] - mx) ** 2 for p in points)
    if den == 0 or my <= 0:
        return None
    return (num / den) / my * 100.0


DECLINE_SATURATION = 5.0   # %/yr fall that reads as total production collapse


def _to_score(pct_per_year):
    """Falling production -> high fragility. Flat or growing -> 0. See module docstring."""
    if pct_per_year is None:
        return None
    if pct_per_year >= 0:
        return 0
    decline = min(DECLINE_SATURATION, -pct_per_year)
    return int(round(decline / DECLINE_SATURATION * 100.0))


def main():
    area_map, name_map = _load_area_iso3()
    try:
        r = http_get(BULK_URL, timeout=300, retries=3, patient=True)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = next(n for n in z.namelist()
                        if n.endswith(".csv") and "AreaCodes" not in n
                        and "Elements" not in n and "Flags" not in n
                        and "ItemCodes" not in n)
    except Exception as e:
        # v90 — DO NOT write an empty payload before failing. safe_run()
        # preserves the last good file only when a step RAISES; writing {}
        # first destroys that payload and the preservation has nothing left
        # to restore. The exception alone carries the diagnosis.
        raise RuntimeError(f"FAOSTAT Production Indices bulk download failed: {e}") from e

    series = {}   # iso3 -> {year: value}
    rows_seen = 0
    with z.open(csv_name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
        for row in reader:
            rows_seen += 1
            if row.get("Element") != ELEMENT or row.get("Item") != ITEM:
                continue
            area = (row.get("Area") or "").strip()
            try:
                ac = int(row.get("Area Code") or 0)
            except ValueError:
                ac = 0
            iso3 = name_map.get(area) or area_map.get(ac)
            if not iso3:
                continue
            try:
                yr = int(row.get("Year") or 0)
                val = float(row.get("Value"))
            except (TypeError, ValueError):
                continue
            if yr <= 0 or val <= 0:
                continue
            series.setdefault(iso3, {})[yr] = val

    out = {}
    for iso3, by_year in series.items():
        years = sorted(by_year)
        if not years:
            continue
        latest = years[-1]
        window = [(y, by_year[y]) for y in years if y > latest - WINDOW_YEARS]
        if len(window) < MIN_POINTS:
            out[iso3] = {
                "trend_pct_per_year": None, "prod_trend_score": None,
                "points": len(window), "year_latest": latest,
                "source": "FAOSTAT Production Indices (Gross Production Index, Food)",
                "source_url": "https://www.fao.org/faostat/en/#data/QI",
                "method": f"OLS slope over the last {WINDOW_YEARS} years; too few points here.",
                "quality_flag": "partial",
            }
            continue
        pct = _slope_pct_per_year(window)
        out[iso3] = {
            "trend_pct_per_year": None if pct is None else round(pct, 2),
            "prod_trend_score": _to_score(pct),
            "index_latest": round(by_year[latest], 1),
            "points": len(window),
            "year_latest": latest,
            "year_from": window[0][0],
            "source": "FAOSTAT Production Indices (Gross Production Index, Food, 2014-2016 = 100)",
            "source_url": "https://www.fao.org/faostat/en/#data/QI",
            "method": (f"OLS slope of the gross food production index over {window[0][0]}-{latest}, "
                       "expressed as average annual % change against the window mean, then mapped "
                       "to 0-100 fragility where only sustained DECLINE scores high "
                       "(-5%/yr -> 100, -1%/yr -> 20, flat or growing -> 0)."),
            "quality_flag": "sourced" if pct is not None else "partial",
        }

    scored = sum(1 for v in out.values() if v.get("prod_trend_score") is not None)
    print(f"[INFO] Production index: {rows_seen} rows -> {len(out)} countries, {scored} with a trend")
    for ref in ("NGA", "COD", "EGY", "SOM", "YEM", "UKR", "USA", "IND"):
        if ref in out:
            v = out[ref]
            print(f"  [ref] {ref}: {v.get('trend_pct_per_year')}%/yr -> score "
                  f"{v.get('prod_trend_score')} ({v.get('year_from')}-{v.get('year_latest')})")

    write_json(
        "faostat_prod_index.json", out,
        source="FAOSTAT Production Indices bulk download",
        notes=(
            "Per-country food production trend from FAOSTAT's Gross Production Index "
            "(Food, 2014-2016 = 100). Replaces the hand-authored FDRS c[2] literal, which "
            "carried weight 0.11 with no source and no data year. Falling production scores "
            "high. Countries with fewer than 5 index points in the window carry a null score "
            f"and quality_flag 'partial' — never 0. Covered {len(out)} countries, {scored} scored."
        ),
    )


if __name__ == "__main__":
    main()
