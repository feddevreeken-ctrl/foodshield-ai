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

# v79j — BATCHED REQUESTS.
# This script used to issue one HTTP request per country: 195 calls per run.
# It works from a residential IP and finishes in ~33s, but it had not produced a
# fresh file since 2026-08-03 while every other feed in the same workflow stayed
# current — i.e. it was failing only from GitHub Actions runners, whose egress
# IPs are shared across every repo on the fleet and collectively blow through
# Open-Meteo's per-IP hourly allowance.
#
# Open-Meteo's forecast endpoint accepts comma-separated latitude/longitude
# lists and returns one result object per coordinate, in request order. Batching
# turns 195 requests into 2, which puts the run three orders of magnitude below
# any rate limit and removes the failure mode entirely rather than retrying into
# it. Measured: 195 countries in 2 calls, ~1.5s total.
#
# BATCH_SIZE is well under Open-Meteo's documented limit; keep it conservative so
# a single oversized URL can never be the thing that breaks the feed.
BATCH_SIZE = 100


def _fetch_batch(chunk):
    """Fetch one batch. Returns a list of per-coordinate result dicts.

    Open-Meteo returns a bare object (not a list) when exactly one coordinate is
    requested, so normalise that here — otherwise a trailing batch of size 1
    would silently drop a country.
    """
    r = http_get(URL, params={
        "latitude": ",".join(str(lat) for _, (lat, _lon) in chunk),
        "longitude": ",".join(str(lon) for _, (_lat, lon) in chunk),
        "daily": "precipitation_sum,temperature_2m_mean,soil_moisture_0_to_10cm_mean",
        "past_days": 30,
        "forecast_days": 1,
        "timezone": "UTC",
    }, timeout=60, retries=3)
    j = r.json()
    return j if isinstance(j, list) else [j]


def main():
    out = {}
    countries = list(COUNTRY_COORDS.items())
    batches = [countries[i:i + BATCH_SIZE] for i in range(0, len(countries), BATCH_SIZE)]
    print(f"[INFO] querying Open-Meteo for {len(countries)} countries "
          f"in {len(batches)} batched request(s)")

    failed_batches = 0
    for bi, chunk in enumerate(batches):
        try:
            results = _fetch_batch(chunk)
        except Exception as e:
            # A whole batch failing is a real outage, not a per-country quirk.
            # Say so loudly; the count is folded into _meta.notes below.
            failed_batches += 1
            print(f"  [ERROR] batch {bi+1}/{len(batches)} "
                  f"({len(chunk)} countries) failed: {e}")
            continue

        if len(results) != len(chunk):
            # Positional pairing is the whole contract of the batched call. If the
            # lengths disagree we cannot know which result belongs to which
            # country, so refuse to guess.
            failed_batches += 1
            print(f"  [ERROR] batch {bi+1}: asked for {len(chunk)} coordinates, "
                  f"got {len(results)} results — dropping batch rather than "
                  f"mis-assigning countries")
            continue

        for (iso3, _coords), res in zip(chunk, results):
            try:
                d = (res or {}).get("daily") or {}
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

        print(f"  [progress] batch {bi+1}/{len(batches)} done ({len(out)} written)")
        if bi + 1 < len(batches):
            time.sleep(0.3)

    write_json(
        "openmeteo.json",
        out,
        source="Open-Meteo (api.open-meteo.com/v1/forecast)",
        notes=(
            "Past-14-day weather summary per country capital. "
            "precip_anomaly_pct compares 7d vs 30d normalised total. "
            "Flags: drought=anomaly<-50% + dry soil; wet=anomaly>+100%; heat=temp_anom>+3°C. "
            f"Covered {len(out)}/{len(COUNTRY_COORDS)} countries "
            f"in {len(batches)} batched request(s)"
            + (f" — {failed_batches} BATCH(ES) FAILED, coverage is incomplete."
               if failed_batches else ".")
        ),
        status=("degraded" if failed_batches else None),
    )


if __name__ == "__main__":
    main()
