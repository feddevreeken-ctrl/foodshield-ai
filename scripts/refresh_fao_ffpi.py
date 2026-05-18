"""
FAO Food Price Index (FFPI) — monthly global commodity price indices.

Public CSV at fao.org/food-price-index. We fetch the latest XLSX/CSV release.

Output: data/fao_ffpi.json
  {
    "latest": {month: "YYYY-MM", value: 127.1, change_mom_pct: ...},
    "series": [{month, fpi, meat, dairy, cereals, oils, sugar}, ...]  (last 24 months)
  }
"""
import csv
import io

from _common import http_get, write_json

# FAO publishes the index as XLSX at a stable URL; CSV mirror provided here.
# If the CSV mirror changes, point this at https://www.fao.org/fileadmin/templates/worldfood/Reports_and_docs/Food_price_indices_data.csv
URL = "https://www.fao.org/fileadmin/templates/worldfood/Reports_and_docs/Food_price_indices_data_dec25.csv"


def main():
    # Try a couple of common URL patterns; FAO rotates the suffix monthly.
    candidates = [
        "https://www.fao.org/fileadmin/templates/worldfood/Reports_and_docs/Food_price_indices_data.csv",
        "https://www.fao.org/fileadmin/templates/worldfood/Reports_and_docs/Food_price_indices_data_oct25.csv",
        URL,
    ]
    text = None
    used = None
    for u in candidates:
        try:
            r = http_get(u, headers={"Accept": "text/csv,*/*"})
            text = r.text
            used = u
            break
        except Exception as e:
            print(f"  miss {u}: {e}")
    if not text:
        raise RuntimeError("All FAO FFPI URLs failed")

    rows = list(csv.reader(io.StringIO(text)))
    # FAO format: header row + monthly rows: Date, Food Price Index, Meat, Dairy, Cereals, Oils, Sugar
    series = []
    for row in rows:
        if not row or len(row) < 7:
            continue
        date = row[0].strip()
        try:
            fpi = float(row[1])
        except ValueError:
            continue
        try:
            series.append({
                "month": date,
                "fpi": round(fpi, 2),
                "meat": _num(row[2]),
                "dairy": _num(row[3]),
                "cereals": _num(row[4]),
                "oils": _num(row[5]),
                "sugar": _num(row[6]),
            })
        except (IndexError, ValueError):
            continue

    series = series[-24:]  # last 24 months
    latest = series[-1] if series else None
    prev = series[-2] if len(series) > 1 else None
    payload = {
        "latest": latest,
        "change_mom_pct": (
            round((latest["fpi"] - prev["fpi"]) / prev["fpi"] * 100, 2)
            if latest and prev and prev["fpi"]
            else None
        ),
        "series": series,
    }
    write_json("fao_ffpi.json", payload, source=f"FAO Food Price Index ({used})")


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
