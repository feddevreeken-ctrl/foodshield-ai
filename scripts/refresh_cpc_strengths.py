#!/usr/bin/env python3
"""
refresh_cpc_strengths.py — NOAA CPC's own odds for how strong this ENSO event
gets, season by season (RONI strength probabilities).

The page quoted one CPC number ("more than 90% very strong") from prose. The
question a reader asks next is how long it lasts; CPC answers it in a table on
its RONI strength page, nine overlapping seasons by nine strength bins. There is
no CSV, so this parses the one <table id="probabilities-table"> and refuses to
write if the table shape or the issue month looks wrong.

This is the agency's forecast, published as is. Nothing here is modelled.
"""
from __future__ import annotations

import html as htmlmod
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, http_get, write_json  # noqa: E402

URL = "https://cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/strengths/"
UA = {"User-Agent": "Mozilla/5.0 (FoodShield AI; public food-security dashboard)", "Accept": "*/*"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
BINS = ["≤ −2.0", "−2.0 to −1.5", "−1.5 to −1.0", "−1.0 to −0.5", "neutral", "+0.5 to +1.0",
        "+1.0 to +1.5", "+1.5 to +2.0", "≥ +2.0"]
CLASSES = [("strong La Niña", [0, 1]), ("La Niña", [2, 3]), ("neutral", [4]), ("weak El Niño", [5]),
           ("moderate El Niño", [6]), ("strong El Niño", [7]), ("very strong El Niño", [8])]


def _cells(row: str) -> list[str]:
    out = []
    for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S):
        c = re.sub(r'<span class="tooltip[^"]*">.*?</span>', "", c, flags=re.S)
        out.append(re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", c))).strip())
    return out


def main() -> int:
    page = http_get(URL, timeout=60, headers=UA, retries=3).text
    issued = None
    for m in re.finditer(r"Issued\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", page):
        d = datetime(int(m.group(2)), MONTHS.index(m.group(1)) + 1, 1, tzinfo=timezone.utc)
        issued = d if issued is None or d > issued else issued
    if issued is None:
        raise RuntimeError("no 'Issued <Month> <Year>' on the CPC strengths page")
    age = (datetime.now(timezone.utc) - issued).days
    if age > 62:
        raise RuntimeError(f"CPC strengths issued {issued:%B %Y}, {age} days ago: stale")
    i = page.find('<table id="probabilities-table"')
    if i < 0:
        raise RuntimeError("probabilities-table not found")
    table = page[i:page.find("</table>", i)]
    rows = [_cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.S)]
    seasons = []
    for cells in rows[1:]:
        if len(cells) != 10:
            continue
        code = cells[0].split()[0]
        try:
            probs = [int(float(x)) for x in cells[1:]]
        except ValueError:
            continue
        if not re.fullmatch(r"[A-Z]{3}", code) or not 95 <= sum(probs) <= 105:
            raise RuntimeError(f"row {cells} does not look like a probability row")
        seasons.append({"season": code, "months": " ".join(cells[0].split()[1:]), "probs": probs,
                        "classes": {name: sum(probs[k] for k in idx) for name, idx in CLASSES}})
    if len(seasons) < 6:
        raise RuntimeError(f"only {len(seasons)} season rows parsed")
    payload = {
        "issued": issued.strftime("%B %Y"), "index": "RONI (relative Oceanic Niño Index)",
        "bins": BINS, "classes": [c[0] for c in CLASSES], "seasons": seasons, "url": URL,
    }
    # refresh_cpc_roni_outlook.py owns data.roni_outlook in this file; keep its last-good value.
    try:
        prev = json.loads((DATA_DIR / "enso_strengths.json").read_text()).get("data", {})
        if prev.get("roni_outlook"):
            payload["roni_outlook"] = prev["roni_outlook"]
    except (OSError, ValueError):
        pass
    write_json("enso_strengths.json", payload, source="NOAA Climate Prediction Center, ENSO strength probabilities (RONI)",
       notes="CPC's published odds per season and strength bin; parsed from its table, not modelled.",
       status="ok")
    print(f"[OK] enso_strengths: {len(seasons)} seasons, issued {issued:%B %Y}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
