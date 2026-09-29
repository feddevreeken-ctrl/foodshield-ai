#!/usr/bin/env python3
"""
build_enso_forecast_skill.py — how right have September forecasts of the
December–February El Niño peak been?

For every year IRI's archive has a mid-September plume ("CCSR/IRI Model
Predictions of ENSO from Sep <year>"), this reads the forecast for the coming
Dec–Feb (DJF) season from the plume figure IRI serves at
ensoforecast.iri.columbia.edu/figure4_plot/<year>/8 (month index 8 = September):

- the plume's model average ("COMBINED AVG", the heavy dark-blue line), and
- every single model's DJF value, whose lowest and highest make the range.

The figure is a matplotlib SVG: each line's points are read from its path and
converted to °C with the figure's own y-axis ticks; the DJF column is found by
its x-axis label. Nothing is estimated by eye, and a figure whose axes cannot be
read is skipped, not guessed.

The observed side is NOAA CPC's ONI for the same DJF (data/enso.json history).
The plume forecasts Niño 3.4 on the base period each model uses, not the ONI's
centred base, so a few hundredths of a degree of the error can be base period.

Hand-run (not in the cron): python3 scripts/build_enso_forecast_skill.py
Writes data/enso_forecast_skill.json.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, http_get, write_json  # noqa: E402

URL = "https://ensoforecast.iri.columbia.edu/figure4_plot/{year}/8"
PAGE = "https://iri.columbia.edu/our-expertise/climate/forecasts/enso/current/"
UA = {"User-Agent": "Mozilla/5.0 (FoodShield AI; public food-security dashboard)", "Accept": "*/*"}
FIRST_YEAR = 2002  # IRI's plume starts in 2002


def _pts(block: str) -> list[tuple[float, float]]:
    p = re.search(r'<path d="([^"]*)"', block)
    pts = [(float(x), float(y)) for x, y in re.findall(r"(-?[\d.]+) (-?[\d.]+)", p.group(1))] if p else []
    return pts + [(float(x), float(y)) for x, y in re.findall(r'<use [^>]*x="(-?[\d.]+)" y="(-?[\d.]+)"', block)]


def parse(svg: str, year: int) -> dict | None:
    title = re.search(r"<!-- (CCSR/IRI Model Predictions of ENSO from (\w+) (\d{4})) -->", svg)
    if not title or title.group(2) != "Sep" or int(title.group(3)) != year:
        return None
    legend_at = svg.find('<g id="legend_1"')
    if legend_at < 0:
        return None
    axes, legend = svg[:legend_at], svg[legend_at:]
    xt = {m.group(2): float(re.findall(r'x="([\d.]+)"', m.group(1))[0])
          for m in re.finditer(r'<g id="xtick_\d+">(.*?)<!-- (.*?) -->', axes, re.S)}
    yt = {}
    for m in re.finditer(r'<g id="ytick_\d+">(.*?)<!-- (.*?) -->', axes, re.S):
        ys = re.findall(r'<use [^>]*y="([\d.]+)"', m.group(1))
        if ys:
            yt[float(m.group(2).replace("−", "-"))] = float(ys[0])
    if "DJF" not in xt or len(yt) < 3:
        return None
    (v0, y0), (v1, y1) = min(yt.items()), max(yt.items())
    val = lambda y: v0 + (y - y0) / (y1 - y0) * (v1 - v0)  # noqa: E731
    # Linear axis check: every tick must sit on the line through the two extremes.
    if any(abs(val(y) - v) > 0.01 for v, y in yt.items()):
        return None
    xd = xt["DJF"]
    # The legend names the heavy (width 5) average lines by colour.
    heavy = {}
    for blk, name in re.findall(r'<g id="line2d_\d+">(.*?)</g>\s*<g id="text_\d+">\s*<!-- (.*?) -->', legend, re.S):
        st = re.search(r'style="([^"]*stroke-width: 5[^"]*)"', blk)
        c = st and re.search(r"stroke: (#\w+)", st.group(1))
        if c:
            heavy[c.group(1)] = name.strip()
    models, avg = [], {}
    for m in re.finditer(r'<g id="line2d_\d+">(.*?)</g>\s*(?=<g id=|</g>)', axes, re.S):
        blk = m.group(1)
        st = re.search(r'<path d="[^"]*"[^>]*style="([^"]*)"', blk)
        if not st or "stroke-dasharray" in st.group(1) or "#cccccc" in st.group(1):
            continue
        at = [val(y) for x, y in _pts(blk) if abs(x - xd) < 0.5]
        if not at:
            continue
        c = re.search(r"stroke: (#\w+)", st.group(1))
        if "stroke-width: 5" in st.group(1):
            if c and c.group(1) in heavy:
                avg[heavy[c.group(1)]] = round(at[0], 2)
        elif "stroke-width" not in st.group(1):  # single models: default width
            models.append(round(at[0], 2))
    if "COMBINED AVG" not in avg or len(models) < 5:
        return None
    return {"avg": avg["COMBINED AVG"], "dyn_avg": avg.get("DYN Average"), "stat_avg": avg.get("STAT Average"),
            "models": len(models), "lo": min(models), "hi": max(models)}


def main() -> int:
    hist = json.loads((DATA_DIR / "enso.json").read_text())["data"]["history"]
    oni = {r["year"]: r["anom"] for r in hist if r.get("season") == "DJF"}
    rows, skipped = [], []
    last = datetime.now(timezone.utc).year - 1
    for year in range(FIRST_YEAR, last + 1):
        obs = oni.get(year + 1)
        try:
            svg = http_get(URL.format(year=year), timeout=60, headers=UA, retries=2).text
            f = parse(svg, year)
        except Exception as e:  # noqa: BLE001
            f = None
            print(f"[warn] {year}: {e}")
        if f is None or obs is None:
            skipped.append(year)
            continue
        rows.append({"issued": f"{year}-09", "djf": f"{year}-{str(year + 1)[2:]}", "forecast_avg": f["avg"],
                     "dyn_avg": f["dyn_avg"], "stat_avg": f["stat_avg"], "models": f["models"],
                     "model_lo": f["lo"], "model_hi": f["hi"], "observed_oni": obs,
                     "error": round(f["avg"] - obs, 2), "inside_range": f["lo"] <= obs <= f["hi"]})
    # This year's September plume, when it is out: the forecast the past errors describe.
    current = None
    try:
        y = datetime.now(timezone.utc).year
        f = parse(http_get(URL.format(year=y), timeout=60, headers=UA, retries=2).text, y)
        if f:
            current = {"issued": f"{y}-09", "djf": f"{y}-{str(y + 1)[2:]}", "forecast_avg": f["avg"], "dyn_avg": f["dyn_avg"],
                       "stat_avg": f["stat_avg"], "models": f["models"], "model_lo": f["lo"], "model_hi": f["hi"]}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] current plume: {e}")
    if len(rows) < 10:
        raise RuntimeError(f"only {len(rows)} years read; skipped {skipped}")
    err = [abs(r["error"]) for r in rows]
    warm = [r for r in rows if r["observed_oni"] >= 1.5]
    summary = {
        "years": len(rows), "first": rows[0]["djf"], "last": rows[-1]["djf"],
        "mae": round(statistics.mean(err), 2),
        "median_abs_error": round(statistics.median(err), 2),
        "bias": round(statistics.mean(r["error"] for r in rows), 2),
        "within_0_5": sum(e <= 0.5 for e in err),
        "inside_range": sum(r["inside_range"] for r in rows),
        "strong_years": [r["djf"] for r in warm],
        "strong_mae": round(statistics.mean(abs(r["error"]) for r in warm), 2) if warm else None,
        "strong_bias": round(statistics.mean(r["error"] for r in warm), 2) if warm else None,
    }
    write_json("enso_forecast_skill.json", {
        "summary": summary, "current": current, "rows": rows, "skipped": skipped,
        "lead": "Issued mid-September for the Dec–Feb season, about three months ahead",
        "source_url": PAGE, "figure_url": URL.format(year="<year>"),
    }, source="IRI ENSO prediction plume archive (mid-September issues) against NOAA CPC ONI (DJF)",
        notes=("Hand-run by scripts/build_enso_forecast_skill.py. forecast_avg is the plume's model average "
               "(COMBINED AVG) for DJF from the mid-September plume; model_lo/model_hi are the lowest and highest "
               "single-model DJF values; current is this year's September plume, not yet verified. Skipped years had a figure whose axis could not be read (2002, 2003 label their ticks differently). Values are read from the SVG figure's line coordinates with its own axis "
               "ticks (linear-axis check to 0.01 °C). Observed: NOAA CPC ONI for the same DJF. The plume uses "
               "Niño 3.4 on each model's base period, not the ONI's centred base."), status="ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
