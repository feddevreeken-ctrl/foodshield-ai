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

  - data/enso_strengths.json  data.roni_outlook and data.oni_roni_gap (other
    keys left as they are)
  - data/enso_mechanism.json  the two "CPC RONI outlook for OND/DJF" context
    rows, in the exact text format the page parses

Nothing is written unless every check passes. The agency's numbers, as published.

THE ONI EQUIVALENT OF A RONI FORECAST (2026-09-28, audit F4 / D4)
-----------------------------------------------------------------
The site's fits run on ONI; CPC forecasts RONI. The gap between them is known
only for seasons already observed (June-August now), and it moves through an
event: from June-August to December-February it changed by -0.49 (1991-92) to
+0.25 (2023-24). So a December-February RONI value is never moved to ONI with
the June-August gap alone. This collector does the conversion once, for every
reader: from CPC's oni.ascii.txt and RONI.ascii.txt it takes the latest
June-August gap and, for each later season, how the gap changed from June-August
to that season in every past El Nino (June-August ONI of +0.5 or more). Each
roni_outlook row then carries `oni_equiv`, a range, not one number:

    median    = RONI median + JJA gap + median change
    lo / hi   = RONI 5th / 95th percentile + JJA gap + smallest / largest change
    median_lo / median_hi = RONI median + JJA gap + smallest / largest change

If the two index files cannot be read, the last good table in the file is used.
"""
from __future__ import annotations

import html as htmlmod
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, http_get  # noqa: E402

URL = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/outlook/"
DISC = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"
ONI_TXT = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
RONI_TXT = "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt"
EVENT_JJA_ONI = Decimal("0.5")   # a past El Nino: June-August ONI at or above this
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


SEASONS = ["".join(LETTERS[(m + k) % 12] for k in range(3)) for m in range(12)]
# The seasons after June-August, up to May-July of the next year. CPC labels a
# season by the year of its middle month, so DJF 2016 is Dec 2015 - Feb 2016.
AFTER_JJA = [SEASONS[(5 + i) % 12] for i in range(1, 12)]


def _middle(code: str) -> int:
    """1-based middle month of a season code (DJF -> 1)."""
    return (_start_month(code) + 1) % 12 + 1


def _index(txt: str, name: str) -> dict:
    """(season, CPC year label) -> anomaly, from oni.ascii.txt or RONI.ascii.txt (anomaly is the last column)."""
    out = {}
    for line in txt.splitlines():
        f = line.split()
        if len(f) < 3 or f[0] not in SEASONS or not f[1].isdigit():
            continue
        try:
            out[(f[0], int(f[1]))] = Decimal(f[-1])
        except InvalidOperation:
            continue
    if len(out) < 600:
        raise RuntimeError(f"{name} parse got {len(out)} seasons: feed shape changed")
    return out


def gap_table(oni_txt: str, roni_txt: str) -> dict:
    """The latest June-August ONI-RONI gap, and how the gap moved after June-August in past El Ninos."""
    oni, roni = _index(oni_txt, "ONI"), _index(roni_txt, "RONI")
    gap = {k: oni[k] - roni[k] for k in oni.keys() & roni.keys()}
    ref = max(y for s, y in gap if s == "JJA")
    events = sorted(y for s, y in gap if s == "JJA" and y < ref and oni[("JJA", y)] >= EVENT_JJA_ONI)
    if len(events) < 8:
        raise RuntimeError(f"only {len(events)} past El Ninos with June-August ONI >= +{EVENT_JJA_ONI}")
    deltas = {}
    for code in AFTER_JJA:
        k = 0 if _middle(code) > 7 else 1          # Aug-Dec middle months: same year; Jan-Jun: the next
        deltas[code] = [{"year": y, "delta": float(gap[(code, y + k)] - gap[("JJA", y)])}
                        for y in events if (code, y + k) in gap]
    return {
        "jja_gap": float(gap[("JJA", ref)]), "jja_window": f"JJA {ref}", "jja_year": ref,
        "events": events, "event_rule": f"June-August ONI of +{EVENT_JJA_ONI} or more",
        "deltas": deltas,
        "method": ("ONI minus RONI, season by season, from CPC's two index files. jja_gap is that gap in the "
                   "latest June-August. A past El Niño is a year whose June-August ONI was +0.5 or more; "
                   "for each later season, delta is how much the gap changed from June-August of that year "
                   "to that season (December-February and later seasons fall in the next calendar year). "
                   "Each roni_outlook row's oni_equiv adds jja_gap and the median, smallest or largest delta "
                   "to CPC's RONI median, 5th or 95th percentile."),
        "sources": {"oni": ONI_TXT, "roni": RONI_TXT},
        "fetched": datetime.now(timezone.utc).date().isoformat(),
    }


def oni_equiv(row: dict, gap: dict) -> dict | None:
    """The season-matched ONI range for one roni_outlook row, or None when the table cannot cover it."""
    code = row["season"]
    ds = [Decimal(str(d["delta"])) for d in (gap.get("deltas") or {}).get(code, [])]
    try:
        start_year = int(str(row["label"]).split()[1][:4])
        jja, ref = Decimal(str(gap["jja_gap"])), int(gap["jja_year"])
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    label_year = start_year + (1 if _start_month(code) == 11 else 0)    # DJF takes its January's year
    months_after = (label_year - ref) * 12 + _middle(code) - 7
    if not ds or not 1 <= months_after <= 11:
        return None
    q = lambda x: float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))  # noqa: E731
    med, p05, p95 = (Decimal(str(row[k])) for k in ("median", "p05", "p95"))
    return {"median": q(med + jja + statistics.median(ds)), "lo": q(p05 + jja + min(ds)), "hi": q(p95 + jja + max(ds)),
            "median_lo": q(med + jja + min(ds)), "median_hi": q(med + jja + max(ds))}


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
    prev_gap = strengths["data"].get("oni_roni_gap")
    try:
        gap = gap_table(http_get(ONI_TXT, timeout=30, headers=UA, retries=2).text,
                        http_get(RONI_TXT, timeout=30, headers=UA, retries=2).text)
    except Exception as e:  # noqa: BLE001 -- the outlook itself must not be lost to the index files
        gap = prev_gap
        print(f"[WARN] ONI-RONI gap: {type(e).__name__}: {e}; "
              + (f"keeping the last good table ({prev_gap.get('fetched')})" if prev_gap else "no ONI equivalent this run"))
    for o in outlook:
        eq = oni_equiv(o, gap) if gap else None
        if eq:
            o["oni_equiv"] = eq
    strengths["data"]["roni_outlook"] = outlook
    if gap:
        strengths["data"]["oni_roni_gap"] = gap
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
        eq = o.get("oni_equiv") or {}
        print(f"  {o['label']:<14} median {o['median']:+.2f}  p05 {o['p05']:+.2f}  p95 {o['p95']:+.2f}  issued {o['issued']}"
              + (f"  | ONI {eq['median']:+.2f} ({eq['median_lo']:+.2f} to {eq['median_hi']:+.2f}), range {eq['lo']:+.2f} to {eq['hi']:+.2f}" if eq else ""))
    if gap:
        print(f"[OK] oni_roni_gap: {gap['jja_window']} gap {gap['jja_gap']:+.2f}, {len(gap['events'])} past El Ninos, fetched {gap.get('fetched')}")
    print(f"[OK] roni_outlook: {len(outlook)} seasons, issued {outlook[0]['issued']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
