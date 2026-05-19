"""
USGS Water Services — gage height & discharge on major US rivers.

No API key required.
Endpoint: https://waterservices.usgs.gov/nwis/iv (instantaneous values, last 7d).

Used to feed the US-state water-stress sub-signal and as an early warning for
US grain-belt flood/drought conditions (Mississippi, Missouri, Ohio).

Output: data/usgs_water.json
  {
    "US-XX": {
      "river": <name>,
      "gage_ft_latest": <ft>,
      "discharge_cfs_latest": <cfs>,
      "discharge_7d_change_pct": <%>,
      "flow_anomaly": "normal" | "low" | "high"
    },
    ...
  }

Lows can signal drought stress on irrigated crops; highs can signal flooding
of low-lying farmland. Both move the US-state nowcast adjustment.
"""
from _common import http_get, write_json

URL = "https://waterservices.usgs.gov/nwis/iv/"

# (state_iso, river, USGS gage site ID)
# Stations chosen for size and relevance to ag belts.
SITES = [
    ("US-MN", "Mississippi @ Minneapolis",   "05288500"),
    ("US-IA", "Mississippi @ Davenport",     "05420500"),
    ("US-MO", "Missouri @ St Joseph",        "06818000"),
    ("US-NE", "Missouri @ Omaha",            "06610000"),
    ("US-IL", "Mississippi @ Thebes",        "07022000"),
    ("US-LA", "Mississippi @ Baton Rouge",   "07374000"),
    ("US-AR", "Arkansas @ Little Rock",      "07263620"),
    ("US-OK", "Arkansas @ Tulsa",            "07164500"),
    ("US-KS", "Kansas @ Topeka",             "06889000"),
    ("US-OH", "Ohio @ Cincinnati",           "03255000"),
    ("US-KY", "Ohio @ Louisville",           "03294500"),
    ("US-TN", "Tennessee @ Chattanooga",     "03568000"),
    ("US-WA", "Columbia @ The Dalles",       "14105700"),
    ("US-CA", "Sacramento @ Verona",         "11425500"),
    ("US-TX", "Rio Grande @ El Paso",        "08364000"),
    ("US-GA", "Chattahoochee @ Atlanta",     "02336300"),
    ("US-FL", "Apalachicola @ Chattahoochee","02358000"),
]


def main():
    out = {}
    for state, river, site in SITES:
        try:
            # Parameters: 00065=gage height (ft), 00060=discharge (cfs)
            r = http_get(URL, params={
                "format": "json",
                "sites": site,
                "parameterCd": "00060,00065",
                "period": "P7D",
            }, timeout=20, retries=2)
            ts = (r.json().get("value") or {}).get("timeSeries") or []
            gage_latest = None
            disch_latest = None
            disch_first = None
            for series in ts:
                code = ((series.get("variable") or {}).get("variableCode") or [{}])[0].get("value")
                values = ((series.get("values") or [{}])[0].get("value") or [])
                nums = [_num(v.get("value")) for v in values if v.get("value") not in (None, "", "-999999")]
                nums = [n for n in nums if n is not None]
                if not nums:
                    continue
                if code == "00065":
                    gage_latest = round(nums[-1], 2)
                elif code == "00060":
                    disch_latest = round(nums[-1], 0)
                    disch_first = nums[0]

            change_pct = None
            if disch_latest and disch_first:
                change_pct = round((disch_latest - disch_first) / disch_first * 100, 1)
            anomaly = "normal"
            if change_pct is not None:
                if change_pct < -25:
                    anomaly = "low"
                elif change_pct > 50:
                    anomaly = "high"

            # Aggregate per state — last writer wins, fine for a single signal
            out[state] = {
                "river": river,
                "site": site,
                "gage_ft_latest": gage_latest,
                "discharge_cfs_latest": disch_latest,
                "discharge_7d_change_pct": change_pct,
                "flow_anomaly": anomaly,
            }
        except Exception as e:
            print(f"  [warn] USGS {state}/{site} skipped: {e}")
            continue

    write_json(
        "usgs_water.json",
        out,
        source="USGS Water Services (waterservices.usgs.gov/nwis/iv)",
        notes=(
            "Instantaneous gage height + 7d discharge change for 17 major US river gages. "
            "flow_anomaly: low=<-25% drop (drought signal), high=>+50% rise (flood signal)."
        ),
    )


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
