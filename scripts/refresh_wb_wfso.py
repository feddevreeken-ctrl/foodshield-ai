"""
World Bank — World Food Security Outlook (WFSO).

Source: Bo Pieter Johannes Andrée et al. (World Bank DECDG + Agriculture Global Practice).
Quarterly publication tracking historical, preliminary, and projected severe food
insecurity worldwide. Used by the World Bank MENA Flagship Report and widely cited
in policy/academic circles. Methodology: Andrée et al. (2020), "Predicting Food Crises".

Endpoint: https://microdata.worldbank.org/api/tables/data/fcv/wfso
  - No auth required
  - Max 1000 rows per call
  - ~60,000 rows total (~60 paginated calls per refresh)
  - 8 indicators × ~190 countries × ~33 years (1999–2031) = the full table

Indicators we surface:
  - Crisis Affected Population (Number)              IPC/CH 3+ in millions of people
  - Emergency Affected Population (Number)           IPC/CH 4+
  - Prevalence of Food Crises (Rate)                 % of population (IPC/CH 3+)
  - Prevalence of Food Emergencies (Rate)            % (IPC/CH 4+)
  - Prevalence of Severe Food Insecurity (%)         FIES-based aggregate
  - Severely Food Insecure Population (Number)
  - Short-Term Caloric Needs Financing (Thousands)   USD funding needs
  - Short-Term Caloric Needs Financing Burden (Rate) financing need / GDP

Row "type" values we preserve:
  - "Historical model estimate" (1999–2008, pre-FEWS NET coverage)
  - "Actual"                    (2009–2025 most years)
  - "Preliminary model estimate" (current year)
  - "Baseline projection"       (next 5 years, central scenario)
  - "Downside projection"       (next 5 years, stress scenario)

Output: data/wb_wfso.json
  {
    "_meta": { ... },
    "data": {
      iso3: {
        "country": <name>,
        "indicators": {
          "crisis_pop":          { latest_year: 2025, latest_value: 20927088,
                                   latest_flag: "Actual", series: [...8 indicator rows...] },
          "emergency_pop":       { ... },
          "crisis_pct":          { ... },
          ...
        },
        "projection": {
          "year_2027_baseline":  22145721,
          "year_2027_downside":  34995707,
          ...
        }
      }
    }
  }
"""
import time

from _common import http_get, write_json

URL = "https://microdata.worldbank.org/api/tables/data/fcv/wfso"
PAGE_SIZE = 1000
THROTTLE_SECONDS = 0.3   # be polite

# Mapping from WFSO indicator_short → our shorthand key
INDICATOR_KEYS = {
    "Crisis Affected Population (Number)":              "crisis_pop",
    "Emergency Affected Population (Number)":           "emergency_pop",
    "Prevalence of Food Crises (Rate)":                 "crisis_pct",
    "Prevalence of Food Emergencies (Rate)":            "emergency_pct",
    "Prevalence of Severe Food Insecurity (%)":         "severe_fi_pct",
    "Severely Food Insecure Population (Number)":       "severe_fi_pop",
    "Short-Term Caloric Needs Financing (Thousands)":   "financing_need_usd_k",
    "Short-Term Caloric Needs Financing Burden (Rate)": "financing_burden_pct",
}


def main():
    # 1. Discover total row count
    try:
        head_r = http_get(URL, params={"limit": 1, "offset": 0, "format": "json"}, timeout=30)
    except Exception as e:
        write_json("wb_wfso.json", {}, source="World Bank WFSO", notes=f"Initial probe failed: {e}")
        return

    head_j = head_r.json() or {}
    total = head_j.get("total") or head_j.get("found") or 0
    if not total:
        write_json("wb_wfso.json", {}, source="World Bank WFSO",
                   notes=f"No rows reported by API (response keys: {list(head_j.keys())})")
        return

    print(f"[INFO] WFSO total rows: {total}; paginating at {PAGE_SIZE}/call")

    # 2. Paginate
    all_rows = []
    offset = 0
    while offset < total:
        try:
            r = http_get(URL, params={"limit": PAGE_SIZE, "offset": offset, "format": "json"},
                         timeout=60, retries=2)
        except Exception as e:
            print(f"  [warn] page at offset {offset} failed: {e}")
            offset += PAGE_SIZE
            time.sleep(THROTTLE_SECONDS)
            continue
        chunk = (r.json() or {}).get("data") or []
        if not chunk:
            print(f"  [info] page at offset {offset} returned 0 rows — stopping")
            break
        all_rows.extend(chunk)
        if (offset // PAGE_SIZE) % 10 == 0:
            print(f"  [progress] offset {offset} cumulative {len(all_rows)}/{total}")
        offset += len(chunk)
        time.sleep(THROTTLE_SECONDS)

    print(f"[INFO] Fetched {len(all_rows)} WFSO rows")

    # 3. Group by country → indicator → list of {year, value, flag, type}
    by_country = {}   # iso3 → row aggregator
    for row in all_rows:
        iso3 = (row.get("iso3c") or "").upper()
        if not iso3 or len(iso3) != 3:
            continue
        ind_label = row.get("indicator_short") or row.get("indicator")
        ind_key = INDICATOR_KEYS.get(ind_label)
        if not ind_key:
            continue
        slot = by_country.setdefault(iso3, {
            "country": row.get("country"),
            "indicators": {},
            "projection": {},
        })
        # Group by indicator
        ind_slot = slot["indicators"].setdefault(ind_key, {
            "label": ind_label,
            "unit": row.get("unit"),
            "indicator_long": row.get("indicator_long"),
            "series": [],
        })
        year = _int(row.get("year"))
        value = _num(row.get("value"))
        flag = row.get("flag")
        row_type = row.get("type")
        if year is None or value is None:
            continue
        ind_slot["series"].append({
            "year": year,
            "value": value,
            "flag": flag,
            "type": row_type,
        })

    # 4. For each country, compute "latest_year/latest_value" and harvest projection keys
    for iso3, slot in by_country.items():
        for ind_key, ind_slot in slot["indicators"].items():
            # Sort series chronologically
            ind_slot["series"].sort(key=lambda x: (x["year"], 0 if x.get("type") == "Baseline projection" else 1))
            # "Latest actual" = most recent non-projection row
            actuals = [r for r in ind_slot["series"] if r.get("type") in ("Actual", "Preliminary model estimate")]
            if actuals:
                latest = max(actuals, key=lambda r: r["year"])
                ind_slot["latest_year"] = latest["year"]
                ind_slot["latest_value"] = latest["value"]
                ind_slot["latest_flag"] = latest.get("flag")
                ind_slot["latest_type"] = latest.get("type")

        # Build projection blocks (Crisis-Affected Population is the headline)
        crisis = slot["indicators"].get("crisis_pop", {}).get("series", [])
        for row in crisis:
            if row.get("type") == "Baseline projection":
                slot["projection"][f"year_{row['year']}_baseline_crisis_pop"] = row["value"]
            elif row.get("type") == "Downside projection":
                slot["projection"][f"year_{row['year']}_downside_crisis_pop"] = row["value"]

    write_json(
        "wb_wfso.json",
        by_country,
        source="World Bank — World Food Security Outlook (microdata.worldbank.org/api/tables/data/fcv/wfso)",
        notes=(
            f"Quarterly food-security outlook with historical 1999–2025, preliminary 2026, "
            f"and baseline+downside projections 2027–2031. Methodology: Andrée et al. (2020). "
            f"Covered {len(by_country)} countries × up to 8 indicators × up to 33 years. "
            f"Crisis-Affected Population numbers represent IPC/CH Phase 3+ populations "
            f"(3-year centered average, smoothed)."
        ),
    )


def _num(v):
    try:
        return float(v) if v not in (None, "", "..") else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
