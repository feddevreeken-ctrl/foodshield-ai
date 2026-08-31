"""
JRC ASAP — Current-season agricultural production hotspots.

WHY THIS FEED EXISTS. The FDRS climate component had no current-season signal
in it. It was 40% heritage, plus WRI Aqueduct at "year": "baseline" (1979-2019
hydrology), plus CCKP 1991-2020 normals. Every one of those is structural: they
describe the climate a country has, not the season it is having. A dashboard
built to spot food crises was therefore unable to tell whether crops were
failing THIS month. ASAP is the fix — the JRC's Anomaly hot Spots of
Agricultural Production, a monthly expert-reviewed assessment of whether crops
and rangeland in each monitored country are in trouble right now.

SOURCE: https://agricultural-production-hotspots.ec.europa.eu/files/hotspots_ts.zip
~1.1 MB, no auth, EU open data. One member, hotspots_ts.csv, SEMICOLON-delimited
(not comma — a comma reader returns one column), ~8,900 rows, 81 countries,
monthly since 2016-10. Handled in memory; nothing is written to disk but the
JSON output.

TRAP 1 — hs_code IS NOT AN ORDINAL SCALE. Verified against the file's own
hs_name column on 2026-08-30:

    0 = No hotspot     (7099 rows)
    1 = Hotspot        (1339 rows)
    2 = Major hotspot  ( 229 rows)
    3 = Not assessed   ( 192 rows)   <-- NOT a severity above "major"

Read hs_code as a number and 3 outranks 2. On the July 2026 assessment that
puts Thailand (code 3, not assessed) above Sudan (code 2, major hotspot) —
the single most food-insecure country in the dataset ranked below a country
ASAP simply did not evaluate. So 3 is mapped to null, never to a score.

TRAP 2 — NO ISO3 IN THE FILE. The CSV carries asap0_id (an internal ASAP id)
and asap0_name only, and the site's getCountries.php lookup 404s. The 81
country names are mapped to ISO3 explicitly below. Several are non-obvious:
"C. African Rep.", "D.R. Congo", "Côte d'Ivoire", "Equat.Guinea", "Syrian A.R.",
"Viet Nam", "North Korea", "Timor-Leste", and a bare "Congo" that means
Congo-Brazzaville (COG), not the DRC. Any name that fails to map is PRINTED,
loudly, and counted — an unmapped crisis country dropped in silence is exactly
the failure this dashboard exists to prevent.

NO DATA IS NOT "NO STRESS". A country at hs_code 3 gets hotspot_code: null and
stress_score: null. It never gets 0. Conflating absence with safety is a
recurring bug in this repo and it must not be reproduced here: the eight
countries currently unassessed (Thailand, Viet Nam, Myanmar, Cambodia, Laos,
Philippines, Indonesia, Timor-Leste) include several with dozens of subnational
crop warnings standing.

STRESS_SCORE DERIVATION (0-100, null when not assessed). Two ingredients, in
strict priority order:

  1. The JRC expert verdict (hotspot_code) sets a BAND. The bands do not
     overlap, so the verdict always dominates the ranking and no amount of
     unit-count noise can lift a "no hotspot" country above a "hotspot" one:

         code 0  No hotspot     ->   0 - 20
         code 1  Hotspot        ->  40 - 70
         code 2  Major hotspot  ->  75 - 100

  2. g1_w_crop (count of GAUL1 subnational units under crop warning) spreads a
     country WITHIN its band. Raw counts are not comparable across countries —
     Ethiopia has far more GAUL1 units than Djibouti — so the count is
     normalised against that country's own maximum g1_w_crop over the whole
     2016-2026 series. The fraction therefore reads "how much of this country
     is under crop warning relative to the worst it has ever been", which is
     both size-independent and honest about what the number actually is.
     A country whose historical maximum is 0 contributes 0 spread rather than
     dividing by zero.

The gaps between bands are deliberate. They keep the three verdicts visually
and numerically separable for anyone consuming the score as a category, and
they stop a heavily-warned "no hotspot" country from colliding with a lightly-
warned "hotspot" one. The score is an ordering aid; hotspot_code remains the
authoritative signal and is always carried alongside it.

COVERAGE IS NOT GLOBAL. ASAP monitors ~81 food-insecure countries. The absence
of a country from this payload means ASAP does not watch it, NOT that it is
unstressed — the USA, Australia, Ukraine, Brazil and India are all absent.

OUTPUT: data/asap.json
  { "data": { "SDN": {"hotspot_code": 2, "hotspot_label": "major hotspot",
              "units_crop_warning": 9, "units_range_warning": 10,
              "units_any_warning": 12, "assessment_date": "2026-07-11",
              "comment": "...", "stress_score": 92.3,
              "source": "JRC ASAP", "quality_flag": "sourced"} } }
"""
import csv
import io
import zipfile

from _common import http_get, write_json

ZIP_URL = "https://agricultural-production-hotspots.ec.europa.eu/files/hotspots_ts.zip"
SOURCE_URL = "https://agricultural-production-hotspots.ec.europa.eu/"

# hs_code -> (label, band_low, band_high). Verified against the file's own
# hs_name column — see TRAP 1. Code 3 ("Not assessed") is deliberately absent:
# it resolves to null, not to a band.
HOTSPOT_BANDS = {
    0: ("no hotspot", 0.0, 20.0),
    1: ("hotspot", 40.0, 70.0),
    2: ("major hotspot", 75.0, 100.0),
}
NOT_ASSESSED = 3

# ASAP asap0_name -> ISO3. All 81 monitored countries, enumerated explicitly.
# Anything the upstream adds or renames will fail to map and be printed rather
# than silently dropped (see _iso3 and main).
NAME_TO_ISO3 = {
    "Afghanistan": "AFG",
    "Algeria": "DZA",
    "Angola": "AGO",
    "Bangladesh": "BGD",
    "Benin": "BEN",
    "Bolivia": "BOL",
    "Botswana": "BWA",
    "Burkina Faso": "BFA",
    "Burundi": "BDI",
    "C. African Rep.": "CAF",
    "Cambodia": "KHM",
    "Cameroon": "CMR",
    "Chad": "TCD",
    "Colombia": "COL",
    "Congo": "COG",              # Congo-Brazzaville, NOT the DRC (see "D.R. Congo")
    "Cuba": "CUB",
    "Côte d'Ivoire": "CIV",
    "D.R. Congo": "COD",
    "Djibouti": "DJI",
    "Ecuador": "ECU",
    "Egypt": "EGY",
    "El Salvador": "SLV",
    "Equat.Guinea": "GNQ",
    "Eritrea": "ERI",
    "Eswatini": "SWZ",
    "Ethiopia": "ETH",
    "Gambia": "GMB",
    "Ghana": "GHA",
    "Guatemala": "GTM",
    "Guinea": "GIN",
    "Guinea-Bissau": "GNB",
    "Haiti": "HTI",
    "Honduras": "HND",
    "Indonesia": "IDN",
    "Iran": "IRN",
    "Iraq": "IRQ",
    "Kazakhstan": "KAZ",
    "Kenya": "KEN",
    "Kyrgyzstan": "KGZ",
    "Laos": "LAO",
    "Lesotho": "LSO",
    "Liberia": "LBR",
    "Libya": "LBY",
    "Madagascar": "MDG",
    "Malawi": "MWI",
    "Mali": "MLI",
    "Mauritania": "MRT",
    "Morocco": "MAR",
    "Mozambique": "MOZ",
    "Myanmar": "MMR",
    "Namibia": "NAM",
    "Nepal": "NPL",
    "Nicaragua": "NIC",
    "Niger": "NER",
    "Nigeria": "NGA",
    "North Korea": "PRK",
    "Pakistan": "PAK",
    "Peru": "PER",
    "Philippines": "PHL",
    "Rwanda": "RWA",
    "Senegal": "SEN",
    "Sierra Leone": "SLE",
    "Somalia": "SOM",
    "South Africa": "ZAF",
    "South Sudan": "SSD",
    "Sri Lanka": "LKA",
    "Sudan": "SDN",
    "Syrian A.R.": "SYR",
    "Tajikistan": "TJK",
    "Tanzania": "TZA",
    "Thailand": "THA",
    "Timor-Leste": "TLS",
    "Togo": "TGO",
    "Tunisia": "TUN",
    "Turkmenistan": "TKM",
    "Uganda": "UGA",
    "Uzbekistan": "UZB",
    "Viet Nam": "VNM",
    "Yemen": "YEM",
    "Zambia": "ZMB",
    "Zimbabwe": "ZWE",
}

# Aliases the upstream has used or might plausibly switch to. Kept separate from
# the canonical table so the two can't be confused, and a hit here is reported
# as an alias hit rather than passing silently — a rename upstream is worth
# knowing about even when it doesn't break anything.
NAME_ALIASES = {
    "Korea, DPR": "PRK",
    "Dem. Rep. of Korea": "PRK",
    "Central African Republic": "CAF",
    "Democratic Republic of the Congo": "COD",
    "DR Congo": "COD",
    "Congo, Rep.": "COG",
    "Cote d'Ivoire": "CIV",
    "Ivory Coast": "CIV",
    "Syria": "SYR",
    "Syrian Arab Republic": "SYR",
    "Vietnam": "VNM",
    "Equatorial Guinea": "GNQ",
    "Lao PDR": "LAO",
    "Timor Leste": "TLS",
    "East Timor": "TLS",
    "Swaziland": "SWZ",
    "United Republic of Tanzania": "TZA",
}

REFERENCE_COUNTRIES = ("SDN", "SSD", "SOM", "ETH", "MOZ", "MDG", "AFG", "YEM")


def _download_csv_text():
    """Fetch the zip and return hotspots_ts.csv as text. In memory, no temp files."""
    r = http_get(ZIP_URL, timeout=120, retries=3, patient=True)
    blob = r.content or b""
    if len(blob) < 100_000:
        raise RuntimeError(f"zip is only {len(blob)} bytes — upstream is serving an error page")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not members:
            raise RuntimeError(f"no CSV in zip; members were {zf.namelist()}")
        name = "hotspots_ts.csv" if "hotspots_ts.csv" in members else members[0]
        raw = zf.read(name)
    # Decode explicitly. requests would guess the charset from a header the
    # upstream doesn't send, and a wrong guess mangles "Côte d'Ivoire" into a
    # name that then fails to map.
    text = raw.decode("utf-8-sig")
    print(f"  [ok] {name}: {len(blob)} bytes zipped -> {len(text)} chars")
    return text


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _iso3(name, alias_hits, unmapped):
    """Map an ASAP country name to ISO3, recording anything unusual."""
    if name in NAME_TO_ISO3:
        return NAME_TO_ISO3[name]
    if name in NAME_ALIASES:
        iso3 = NAME_ALIASES[name]
        alias_hits.append(f"{name} -> {iso3}")
        return iso3
    unmapped.add(name)
    return None


def _stress_score(code, units_crop, hist_max_crop):
    """Band from the expert verdict; spread within band by relative crop warnings.

    Returns None for a not-assessed country. See the module docstring for why
    the bands don't overlap and why the count is self-normalised.
    """
    band = HOTSPOT_BANDS.get(code)
    if band is None:
        return None
    _label, low, high = band
    frac = 0.0
    if hist_max_crop and units_crop:
        frac = min(1.0, units_crop / hist_max_crop)
    return round(low + (high - low) * frac, 1)


def main():
    try:
        text = _download_csv_text()
    except Exception as e:
        write_json("asap.json", {}, source="JRC ASAP",
                   notes=f"ASAP hotspots_ts.zip fetch failed: {e}")
        raise

    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    if not rows or "hs_code" not in (rows[0] or {}):
        write_json("asap.json", {}, source="JRC ASAP",
                   notes=("ASAP CSV parsed to no usable rows — check the delimiter "
                          f"(must be ';') and the header. Columns seen: "
                          f"{list(rows[0].keys()) if rows else 'none'}"))
        raise RuntimeError("ASAP CSV parsed to no usable rows")

    alias_hits, unmapped = [], set()
    latest = {}          # iso3 -> row dict of the most recent assessment
    hist_max_crop = {}   # iso3 -> max g1_w_crop ever seen (band-spread denominator)
    label_counts = {}

    for row in rows:
        name = (row.get("asap0_name") or "").strip().strip('"')
        date = (row.get("date") or "").strip()
        if not name or not date:
            continue
        iso3 = _iso3(name, alias_hits, unmapped)
        if iso3 is None:
            continue
        crop = _int(row.get("g1_w_crop")) or 0
        hist_max_crop[iso3] = max(hist_max_crop.get(iso3, 0), crop)
        prev = latest.get(iso3)
        if prev is None or date > prev["_date"]:
            latest[iso3] = {
                "_date": date,
                "_name": name,
                "code": _int(row.get("hs_code")),
                "crop": crop,
                "range": _int(row.get("g1_w_range")) or 0,
                "any": _int(row.get("g1_w_any")) or 0,
                "comment": (row.get("comment") or "").strip().strip('"'),
            }

    out = {}
    for iso3, rec in latest.items():
        code = rec["code"]
        assessed = code in HOTSPOT_BANDS
        # NOT ASSESSED IS NULL, NOT ZERO. hs_code 3 (and anything unrecognised)
        # carries no score at all — see the module docstring.
        label = HOTSPOT_BANDS[code][0] if assessed else "not assessed"
        label_counts[label] = label_counts.get(label, 0) + 1
        out[iso3] = {
            "hotspot_code": code if assessed else None,
            "hotspot_label": label,
            "units_crop_warning": rec["crop"],
            "units_range_warning": rec["range"],
            "units_any_warning": rec["any"],
            "assessment_date": rec["_date"],
            "comment": rec["comment"],
            "stress_score": _stress_score(code, rec["crop"], hist_max_crop.get(iso3, 0))
                            if assessed else None,
            "source": "JRC ASAP",
            "source_url": SOURCE_URL,
            "quality_flag": "sourced",
        }

    total_names = len({(r.get("asap0_name") or "").strip().strip('"')
                       for r in rows if (r.get("asap0_name") or "").strip()})
    latest_date = max((v["assessment_date"] for v in out.values()), default="none")

    print(f"[INFO] ASAP: {len(out)} countries mapped of {total_names} names in file "
          f"| latest assessment {latest_date}")
    print(f"[INFO] latest verdicts: " +
          " ".join(f"{k}={v}" for k, v in sorted(label_counts.items())))
    if alias_hits:
        print(f"[INFO] mapped via alias (upstream renamed something): "
              f"{'; '.join(sorted(alias_hits))}")
    if unmapped:
        print(f"[WARN] {len(unmapped)} ASAP country name(s) FAILED to map to ISO3 "
              f"and were DROPPED — add them to NAME_TO_ISO3:")
        for name in sorted(unmapped):
            print(f"  [WARN]   unmapped: {name!r}")
    else:
        print("[INFO] all ASAP country names mapped to ISO3 (0 unmapped)")

    for ref in REFERENCE_COUNTRIES:
        d = out.get(ref)
        if not d:
            print(f"  [ref] {ref}: ABSENT from ASAP payload")
            continue
        print(f"  [ref] {ref}: code={d['hotspot_code']} ({d['hotspot_label']}) "
              f"stress={d['stress_score']} crop={d['units_crop_warning']} "
              f"range={d['units_range_warning']} any={d['units_any_warning']} "
              f"@ {d['assessment_date']}")

    not_assessed = label_counts.get("not assessed", 0)
    write_json(
        "asap.json", out,
        source="JRC ASAP (Anomaly hot Spots of Agricultural Production)",
        notes=(
            "Current-season agricultural stress from the EC Joint Research Centre's "
            "ASAP monthly expert assessment. Latest assessment per country from the "
            f"full 2016-present series; most recent date in this run {latest_date}. "
            "hotspot_code: 0 no hotspot, 1 hotspot, 2 major hotspot. "
            "ASAP's raw hs_code 3 means NOT ASSESSED, not a severity above 2 — those "
            f"countries ({not_assessed} in this run) carry hotspot_code null and "
            "stress_score null. NULL IS NOT ZERO: no assessment is not an all-clear, "
            "and several unassessed countries have dozens of subnational crop "
            "warnings standing (units_crop_warning is populated regardless of code). "
            "stress_score 0-100 is banded by the JRC verdict (0-20 / 40-70 / 75-100 "
            "for codes 0/1/2) and spread within band by units_crop_warning normalised "
            "against that country's own historical maximum — so the expert verdict "
            "always dominates the ranking and the count only breaks ties. "
            f"COVERAGE IS NOT GLOBAL: ASAP monitors {len(out)} food-insecure "
            "countries only. A country absent from this payload is one ASAP does not "
            "watch (USA, Ukraine, Brazil, India, Australia among them) — absence "
            "must NOT be presented as an absence of stress. "
            "Unlike Aqueduct baseline and CCKP normals, this is a current-season "
            "signal and goes stale monthly; ASAP publishes around the 10th."
        ),
    )


if __name__ == "__main__":
    main()
