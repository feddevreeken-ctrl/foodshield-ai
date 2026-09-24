#!/usr/bin/env python3
"""
refresh_cpc_roni_outlook.py — NOAA CPC's official RONI outlook: median and
5th/95th percentile per upcoming season.

The El Niño tab quoted CPC's DJF and OND medians and 90% ranges from rows typed
by hand into data/enso_mechanism.json. CPC publishes them in one table on its
RONI outlook page (<table id="outlook-table">: nine overlapping seasons by
seven percentiles, no CSV). This parses that table, takes the issue day from
the ENSO Diagnostic Discussion the outlook is released with (same month
required), and writes:

  - data/enso_strengths.json  data.roni_outlook  (other keys left as they are)
  - data/enso_mechanism.json  the two "CPC RONI outlook for OND/DJF" context
    rows, in the exact text format the page parses

Nothing is written unless every check passes. The agency's numbers, as published.
"""
from __future__ import annotations

import html as htmlmod
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, http_get  # noqa: E402

URL = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/outlook/"
DISC = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"
UA = {"User-Agent": "Mozilla/5.0 (FoodShield AI; public food-security dashboard)", "Accept": "*/*"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
LETTERS = "JFMAMJJASOND"
HEADER = ["Season", "5%", "15%", "25%", "50%", "75%", "85%", "95%"]
ROWS_TO_TEXT = ("OND", "DJF")   # context rows the page reads


def _cells(row: str) -> list[str]:
    return [re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S)]


def _start_month(code: str) -> int:
    """0-based first month of a 3-letter season code (DJF -> 11)."""
    for m in range(12):
        if "".join(LETTERS[(m + k) % 12] for k in range(3)) == code:
            return m
    raise RuntimeError(f"unknown season code {code!r}")


def _fmt(x: float) -> str:
    return f"{x:+.2f}".replace("-", "−")


def parse(page: str, disc: str) -> list[dict]:
    issued = None
    for m in re.finditer(r"Issued\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", page):
        d = (int(m.group(2)), MONTHS.index(m.group(1)) + 1)
        issued = d if issued is None or d > issued else issued
    if issued is None:
        raise RuntimeError("no 'Issued <Month> <Year>' on the CPC RONI outlook page")
    txt = re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", disc)))
    m = re.search(r"issued by CLIMATE PREDICTION CENTER/NCEP/NWS\s+(\d{1,2} [A-Z][a-z]+ \d{4})", txt)
    if not m:
        raise RuntimeError("no issue date on the CPC ENSO Diagnostic Discussion")
    day = datetime.strptime(m.group(1), "%d %B %Y").replace(tzinfo=timezone.utc)
    if (day.year, day.month) != issued:
        raise RuntimeError(f"outlook issued {MONTHS[issued[1] - 1]} {issued[0]} but the discussion is "
                           f"dated {day:%d %b %Y}: pages out of step, not guessing the day")
    if (datetime.now(timezone.utc) - day).days > 62:
        raise RuntimeError(f"CPC RONI outlook issued {day:%d %b %Y}: stale")

    i = page.find('<table id="outlook-table"')
    if i < 0:
        raise RuntimeError("outlook-table not found")
    rows = [_cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", page[i:page.find("</table>", i)], flags=re.S)]
    if not rows or rows[0] != HEADER:
        raise RuntimeError(f"outlook-table header is {rows[:1]}, expected {HEADER}")
    out, month, year = [], None, None
    for cells in rows[1:]:
        if len(cells) != len(HEADER):
            raise RuntimeError(f"row {cells} has {len(cells)} cells")
        code = cells[0].split()[0]
        vals = [float(x.replace("−", "-")) for x in cells[1:]]
        if vals != sorted(vals) or not all(-5 < v < 5 for v in vals):
            raise RuntimeError(f"row {cells} is not an ordered set of RONI percentiles")
        start = _start_month(code)
        if month is None:
            # First season is the one around the issue month: it may start the year before (DJF issued January).
            month, year = start, issued[0] - (1 if start > issued[1] else 0)
        elif start != (month + 1) % 12:
            raise RuntimeError(f"season {code} does not follow the previous one")
        else:
            year += 1 if start == 0 else 0
            month = start
        label = f"{code} {year}-{str(year + 1)[2:]}" if start >= 10 else f"{code} {year}"
        out.append({"season": code, "label": label, "median": vals[3], "p05": vals[0], "p95": vals[6],
                    "issued": day.date().isoformat()})
    if len(out) < 6:
        raise RuntimeError(f"only {len(out)} season rows parsed")
    return out


def main() -> int:
    outlook = parse(http_get(URL, timeout=30, headers=UA, retries=2).text,
                    http_get(DISC, timeout=30, headers=UA, retries=2).text)

    # Build both files in memory first; write only if both are ready.
    s_path, m_path = DATA_DIR / "enso_strengths.json", DATA_DIR / "enso_mechanism.json"
    strengths, mech = json.loads(s_path.read_text()), json.loads(m_path.read_text())
    strengths["data"]["roni_outlook"] = outlook
    rows = mech["data"]["context"]["rows"]
    for code in ROWS_TO_TEXT:
        o = next((x for x in outlook if x["season"] == code), None)
        row = next((r for r in rows if str(r.get("k", "")).startswith(f"CPC RONI outlook for {code}")), None)
        if o is None or row is None:
            print(f"[WARN] {code}: {'not in the current CPC table' if o is None else 'no context row'}; row left as is")
            continue
        d = datetime.fromisoformat(o["issued"])
        row["k"] = f"CPC RONI outlook for {o['label']}"
        row["v"] = (f"median {_fmt(o['median'])} °C; 5th to 95th percentile {_fmt(o['p05'])} to "
                    f"{_fmt(o['p95'])} °C ({d.day} {d:%b %Y})")
    s_path.write_text(json.dumps(strengths, indent=2, ensure_ascii=False))
    new_mech = json.dumps(mech, indent=2, ensure_ascii=False)
    if new_mech != m_path.read_text():
        m_path.write_text(new_mech)
    for o in outlook:
        print(f"  {o['label']:<14} median {o['median']:+.2f}  p05 {o['p05']:+.2f}  p95 {o['p95']:+.2f}  issued {o['issued']}")
    print(f"[OK] roni_outlook: {len(outlook)} seasons, issued {outlook[0]['issued']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
