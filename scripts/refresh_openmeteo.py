"""
Open-Meteo — weather & soil moisture nowcast for ~180 countries.

No API key required. Generous free tier (10k calls/day non-commercial).
Endpoint: https://api.open-meteo.com/v1/forecast

For each country, fetches the past 14 days of:
  - daily precipitation_sum (mm)
  - daily temperature_2m_mean (°C)
  - daily soil_moisture_0_to_10cm (m³/m³)

Then computes simple anomalies (last-7-day mean vs. trailing-30-day mean) and
flags drought/flood/heat extremes.

Output: data/openmeteo.json
  {
    iso3: {
      "precip_7d_mm": <sum>,
      "precip_anomaly_pct": <% vs 30-day baseline>,
      "temp_7d_c": <mean>,
      "temp_anomaly_c": <delta vs baseline>,
      "soil_moisture_7d": <mean m3/m3>,
      "drought_flag": <bool>,        # precip_anomaly_pct < -50 AND soil < 0.15
      "wet_flag": <bool>,            # precip_anomaly_pct > +100
      "heat_flag": <bool>,           # temp_anomaly_c > +3
    }
  }
"""
from _common import http_get, write_json, COUNTRY_COORDS
import time

URL = "https://api.open-meteo.com/v1/forecast"


def main():
    out = {}
    countries = list(COUNTRY_COORDS.items())
    print(f"[INFO] querying Open-Meteo for {len(countries)} countries")

    for i, (iso3, (lat, lon)) in enumerate(countries):
        try:
            r = http_get(URL, params={
                "latitude": lat,
                "longitude": lon,
                "daily": "precipitation_sum,temperature_2m_mean,soil_moisture_0_to_10cm_mean",
                "past_days": 30,
                "forecast_days": 1,
                "timezone": "UTC",
            }, timeout=20, retries=2)
            d = r.json().get("daily") or {}
            precip = [v for v in (d.get("precipitation_sum") or []) if isinstance(v, (int, float))]
            temp   = [v for v in (d.get("temperature_2m_mean") or []) if isinstance(v, (int, float))]
            soil   = [v for v in (d.get("soil_moisture_0_to_10cm_mean") or []) if isinstance(v, (int, float))]
            if not precip or not temp:
                continue

            p7  = sum(precip[-7:])
            p30 = sum(precip[-30:]) * (7/30) if len(precip) >= 30 else None
            anomaly_pct = round((p7 - p30) / p30 * 100, 1) if p30 and p30 > 1 else None

            t7  = sum(temp[-7:]) / max(1, len(temp[-7:]))
            t30 = sum(temp[-30:]) / max(1, len(temp[-30:])) if len(temp) >= 30 else None
            t_anom = round(t7 - t30, 2) if t30 is not None else None

            s7  = round(sum(soil[-7:]) / max(1, len(soil[-7:])), 3) if soil else None

            drought = bool(anomaly_pct is not None and anomaly_pct < -50 and (s7 is None or s7 < 0.15))
            wet     = bool(anomaly_pct is not None and anomaly_pct > 100)
            heat    = bool(t_anom is not None and t_anom > 3)

            out[iso3] = {
                "precip_7d_mm": round(p7, 1),
                "precip_anomaly_pct": anomaly_pct,
                "temp_7d_c": round(t7, 2),
                "temp_anomaly_c": t_anom,
                "soil_moisture_7d": s7,
                "drought_flag": drought,
                "wet_flag": wet,
                "heat_flag": heat,
            }
        except Exception as e:
            print(f"  [warn] {iso3} skipped: {e}")
            continue

        # Be polite — Open-Meteo is free, don't hammer
        if (i + 1) % 25 == 0:
            print(f"  [progress] {i+1}/{len(countries)} ({len(out)} written)")
            time.sleep(0.3)

    write_json(
        "openmeteo.json",
        out,
        source="Open-Meteo (api.open-meteo.com/v1/forecast)",
        notes=(
            "Past-14-day weather summary per country capital. "
            "precip_anomaly_pct compares 7d vs 30d normalised total. "
            "Flags: drought=anomaly<-50% + dry soil; wet=anomaly>+100%; heat=temp_anom>+3°C. "
            f"Covered {len(out)}/{len(COUNTRY_COORDS)} countries."
        ),
    )


if __name__ == "__main__":
    main()
