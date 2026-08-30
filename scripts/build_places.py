"""
Map place labels — capitals, regional capitals and cities.

Builds data/places.json, the gazetteer behind the Global Map's zoom-tiered
label layer. At world zoom the map shows country names only; zooming in brings
in national capitals, then regional (admin-1) capitals, then ordinary cities —
the progressive disclosure every general-reference web map does, which the
hand-rolled country-name layer could not do on its own because it had no place
data to disclose.

WHY A BUILD STEP AND NOT A RUNTIME API
  The obvious alternatives are a live gazetteer call (GeoNames, REST Countries)
  or a vector basemap that carries its own labels. Both were rejected:

    - A runtime API is a fourth third-party dependency on the map's critical
      render path, and the map already carries a three-mirror fallback chain
      for its polygons precisely because that path is fragile. A capital-city
      list does not change; fetching it on every page load buys nothing.
    - A labelled vector/raster basemap is what this map deliberately does NOT
      use. Its labels are baked UNDER the choropleth (see the long note in
      index.html's map init), which is exactly the problem that made us draw
      country names ourselves in the first place.

  So the gazetteer is a static, same-origin data file like every other feed,
  fetched through _fetchDataOptional() so a missing file degrades to
  country-names-only rather than breaking the map.

SOURCE
  Natural Earth 50m populated places (public domain, no attribution required,
  no key). Natural Earth is the reference dataset for exactly this job: it
  ships a hand-tuned `min_zoom` per place — the zoom at which a cartographer
  judged the place worth drawing — so label density at each zoom is Natural
  Earth's editorial call rather than a population threshold we invent.

  Pulled from the nvkelso/natural-earth-vector GitHub mirror because the
  canonical naciscdn.org host is not reachable from CI. The full layer rather
  than the `_simple` one, because only the full layer carries NAME_EN.

FEATURE CLASSES → OUR KINDS
  Admin-0 capital, Admin-0 capital alt   -> capital   (national)
  Admin-1 capital, Admin-1 region capital,
  Admin-0 region capital                 -> region    (regional/state capital)
  Populated place                        -> city
  Scientific station, Historic place      -> dropped (not places users look for)

OUTPUT: data/places.json
  {
    "_meta": {...},
    "data": [
      {"n": "Cairo", "iso": "EGY", "y": 30.05, "x": 31.25,
       "k": "capital", "z": 1.7, "p": 15600000, "a1": "Al Qahirah"},
      ...
    ]
  }
  Keys are short and coordinates are rounded to 3dp (~100m, far finer than a
  label anchor needs) because this file is parsed on every cold load.

Run: python3 scripts/build_places.py
"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_json  # noqa: E402

SOURCE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_50m_populated_places.geojson"
)
SOURCE_LABEL = "Natural Earth 50m populated places (public domain)"

KIND_BY_FEATURECLA = {
    "Admin-0 capital": "capital",
    "Admin-0 capital alt": "capital",
    "Admin-1 capital": "region",
    "Admin-1 region capital": "region",
    "Admin-0 region capital": "region",
    "Populated place": "city",
}

# Natural Earth's own min_zoom is authored against a full basemap that goes far
# deeper than this map (which caps at zoom 6). Anything Natural Earth would not
# draw until past our maximum zoom can never appear, so it is dead weight.
MAX_MAP_ZOOM = 6.0


def prop(props, *names):
    """Read a property tolerating Natural Earth's case inconsistency.

    The `_simple` variant of this layer ships lowercase keys and the full one
    ships uppercase, and which one carries which field has changed between
    editions. Ask for the field, not for a spelling of it.
    """
    for n in names:
        for key in (n, n.upper(), n.lower()):
            if key in props and props[key] not in (None, ""):
                return props[key]
    return None


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "foodshield-ai/places"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def build():
    print(f"[places] fetching {SOURCE_URL}")
    fc = fetch(SOURCE_URL)
    feats = fc.get("features") or []
    print(f"[places] {len(feats)} source features")

    rows = []
    dropped = {}
    for f in feats:
        p = f.get("properties") or {}
        featurecla = prop(p, "featurecla")
        kind = KIND_BY_FEATURECLA.get(featurecla)
        if not kind:
            dropped[featurecla] = dropped.get(featurecla, 0) + 1
            continue

        # NAME_EN first: Natural Earth's NAME is the local endonym for ~106
        # places, so without this the map reads "Kobenhavn", "Wien", "Moskva"
        # to an English-speaking audience. NAMEASCII is not a substitute — it
        # transliterates the endonym rather than translating it.
        name = str(prop(p, "name_en", "name", "nameascii") or "").strip()
        iso = str(prop(p, "adm0_a3") or "").strip().upper()
        lat, lng = prop(p, "latitude"), prop(p, "longitude")
        if not name or not iso or lat is None or lng is None:
            dropped["incomplete"] = dropped.get("incomplete", 0) + 1
            continue

        # Prefer the geometry over the latitude/longitude columns where they
        # disagree — the geometry is what Natural Earth actually draws.
        geom = f.get("geometry") or {}
        if geom.get("type") == "Point" and isinstance(geom.get("coordinates"), list):
            lng, lat = geom["coordinates"][0], geom["coordinates"][1]

        z = prop(p, "min_zoom")
        try:
            z = float(z)
        except (TypeError, ValueError):
            z = 5.0
        if z > MAX_MAP_ZOOM:
            dropped["beyond_max_zoom"] = dropped.get("beyond_max_zoom", 0) + 1
            continue

        row = {
            "n": name,
            "iso": iso,
            "y": round(float(lat), 3),
            "x": round(float(lng), 3),
            "k": kind,
            "z": round(z, 1),
        }
        pop = prop(p, "pop_max")
        if isinstance(pop, (int, float)) and pop > 0:
            row["p"] = int(pop)
        a1 = str(prop(p, "adm1name") or "").strip()
        if a1 and a1 != name:
            row["a1"] = a1
        rows.append(row)

    # A national capital must never lose a collision to a nearby city, and the
    # renderer walks this list in order, so rank it here once: capitals first,
    # then regional capitals, then cities; within a kind, the place Natural
    # Earth would draw earliest wins, then the larger population.
    kind_rank = {"capital": 0, "region": 1, "city": 2}
    rows.sort(key=lambda r: (kind_rank[r["k"]], r["z"], -(r.get("p") or 0), r["n"]))

    counts = {}
    for r in rows:
        counts[r["k"]] = counts.get(r["k"], 0) + 1
    print(f"[places] kept {len(rows)}: {counts}")
    print(f"[places] dropped: {dropped}")

    # A capital list that has lost most of its capitals is worse than no file at
    # all, because the map would silently show a thinner world. Refuse to write.
    if counts.get("capital", 0) < 150:
        raise SystemExit(
            f"[places] ABORT: only {counts.get('capital', 0)} national capitals "
            f"(expected ~200) — source schema may have changed; refusing to "
            f"overwrite data/places.json"
        )

    write_json(
        "places.json",
        rows,
        source=SOURCE_LABEL + " — " + SOURCE_URL,
        notes=(
            "Zoom-tiered map label gazetteer. k=capital|region|city, "
            "z=Natural Earth min_zoom (the zoom at or above which the place is "
            "drawn), y/x=lat/lng, p=pop_max, a1=admin-1 name. "
            f"Places Natural Earth would not draw until past zoom {MAX_MAP_ZOOM} "
            "(this map's maximum) are omitted. Static reference data — rebuild "
            "only when Natural Earth publishes a new edition."
        ),
        status="ok",
    )


if __name__ == "__main__":
    build()
