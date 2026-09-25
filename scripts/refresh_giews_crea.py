"""
FAO GIEWS — Countries requiring external assistance for food (CREA).

Source: https://www.fao.org/giews/country-analysis/external-assistance/en/
(official FAO GIEWS list, updated three times a year alongside the Crop
Prospects and Food Situation report; no key). The page is server-rendered
HTML: one block per country carrying the GIEWS country code (ISO3, from the
country-brief link), the region, the FAO "nature of food insecurity" category
and the main reasons. We parse exactly those fields and nothing else.

What this adds: an official, FAO-assessed list of countries in food crisis
needing external assistance, with the TYPE of crisis (production shortfall vs
widespread access vs localized). The site links to GIEWS briefs statically
but carries none of GIEWS's own classification.

Output: data/giews_crea.json
  { iso3: { country, region, category, category_code, main_reasons,
            summary, list_period, new_entry, source_url, brief_url } }
category_code: 1 = exceptional shortfall in aggregate food production/supplies,
               2 = widespread lack of access, 3 = severe localized food insecurity
(FAO's own ordering, most to least systemic; not a FoodShield score).
"""
import html
import re

from _common import http_get, write_json

URL = "https://www.fao.org/giews/country-analysis/external-assistance/en/"
BRIEF = "https://www.fao.org/giews/countrybrief/country.jsp?code={}"
CATEGORY_CODE = {
    "exceptional shortfall in aggregate food production/supplies": 1,
    "widespread lack of access": 2,
    "severe localized food insecurity": 3,
}


def _text(fragment):
    t = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def parse(page):
    periods = re.findall(r'<div class="list-period">([^<]+)</div>', page)
    if not periods:
        raise RuntimeError("CREA page has no list-period marker; layout changed")
    period = periods[0].strip()
    m = re.search(r"total:\s*(\d+)\s*countries", page)
    declared = int(m.group(1)) if m else None

    out = {}
    for block in re.split(r'<div class="div-table" id="\d+"', page)[1:]:
        iso = re.search(r"country\.jsp\?code=([A-Z]{3})", block)
        cat = re.search(r'class="div-category" value="([^"]+)"', block)
        if not iso or not cat:
            continue
        region = re.search(r'value="([^"]+)" class="div-region"', block)
        name = re.search(r'div-cell country">(.*?)</div>', block, re.S)
        cell = re.search(r'div-cell text">(.*?)</div>', block, re.S)
        reasons = re.search(r"<b>(.*?)</b>", cell.group(1), re.S) if cell else None
        items = re.findall(r"<li>(.*?)</li>", cell.group(1), re.S) if cell else []
        change = re.search(r'div-cell changes">(.*?)</div>', block, re.S)
        category = _text(cat.group(1))
        iso3 = iso.group(1)
        out[iso3] = {
            "country": _text(name.group(1)) if name else None,
            "region": region.group(1) if region else None,
            "category": category,
            "category_code": CATEGORY_CODE.get(category.lower()),
            "main_reasons": [r.strip() for r in _text(reasons.group(1)).split(",") if r.strip()]
                            if reasons else [],
            "summary": " ".join(_text(i) for i in items)[:1200] or None,
            "list_period": period,
            "new_entry": bool(_text(change.group(1))) if change else None,
            "source_url": URL,
            "brief_url": BRIEF.format(iso3),
        }
    return period, declared, out


def main():
    page = http_get(URL, headers={"Accept": "text/html"}, timeout=60).text
    period, declared, out = parse(page)
    if not out:
        raise RuntimeError("CREA page parsed to zero countries")
    # The page states its own total; a mismatch means the layout moved under us.
    status = "ok" if declared in (None, len(out)) else "degraded_partial"
    cats = {}
    for v in out.values():
        v["list_period"] = period   # the page shows which list a badge comes from
        cats[v["category"]] = cats.get(v["category"], 0) + 1
    write_json(
        "giews_crea.json", out,
        source=f"FAO GIEWS — Countries requiring external assistance for food ({URL})",
        status=status,
        notes=(
            f"List of {period}: {len(out)} countries parsed"
            + (f" (page declares {declared})" if declared is not None else "")
            + ". Categories: " + "; ".join(f"{k} {n}" for k, n in sorted(cats.items()))
            + ". Updated by FAO three times a year; a country absent from the list is not "
              "assessed as food-secure, only as not requiring external assistance per GIEWS."
        ),
    )


if __name__ == "__main__":
    main()
