"""
HDX HAPI — Internal displacement (IDPs) per country.

Source: OCHA Humanitarian API (HAPI) v2, affected-people/idps, NATIONAL level.
  https://hapi.humdata.org/api/v2/affected-people/idps
Auth: a self-serve `app_identifier` (base64 of "appname:email") — NOT an approved
  key, just an identifier HDX asks callers to send so they can rate-limit. Override
  via env HAPI_APP_IDENTIFIER. No registration/approval step.

WHY: internal displacement is a distinct acute food-security signal not otherwise in
the model — large IDP populations mean disrupted livelihoods, abandoned harvests and
broken market access. Feeds the nowcast `displacement_kick` (magnitude-banded, ≤+4).

Honesty note: countries.json carries no population field, so the kick is banded on
ABSOLUTE displacement magnitude, not per-capita. This is disclosed in the methodology
and the source row — a deliberate, stated simplification.

Output: data/hapi_idps.json
  {"data": {ISO3: {"idps": int, "as_of": "YYYY-MM-DD",
                    "assessment_type": str, "source_hdx_id": str}}}
"""
import base64
from _common import env, http_get, write_json

BASE = "https://hapi.humdata.org/api/v2/affected-people/idps"
# Self-serve identifier; HDX documents this base64("appname:email") form as sufficient.
DEFAULT_APPID = base64.b64encode(b"foodshield:fedde.vreeken@gmail.com").decode()


def main():
    appid = env("HAPI_APP_IDENTIFIER", DEFAULT_APPID)
    rows, offset, limit = [], 0, 1000
    for _ in range(25):  # hard safety cap (25k rows)
        r = http_get(BASE, params={"output_format": "json", "admin_level": 0,
                                   "app_identifier": appid, "limit": limit, "offset": offset})
        batch = r.json().get("data", []) or []
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    # Keep the latest national total per country (max reference_period_start).
    latest = {}
    for row in rows:
        iso = row.get("location_code")
        pop = row.get("population")
        start = row.get("reference_period_start") or ""
        if not iso or pop is None:
            continue
        if iso not in latest or start > latest[iso]["_s"]:
            latest[iso] = {
                "_s": start,
                "idps": int(pop),
                "as_of": (row.get("reference_period_end") or start or "")[:10],
                "assessment_type": row.get("assessment_type"),
                "source_hdx_id": row.get("resource_hdx_id"),
            }
    out = {iso: {k: v for k, v in d.items() if k != "_s"} for iso, d in latest.items()}

    write_json(
        "hapi_idps.json", out,
        source="HDX HAPI v2 affected-people/idps (national, latest reporting round)",
        notes=(f"Internal-displacement totals for {len(out)} countries; feeds the "
               "nowcast displacement_kick (absolute-magnitude band, ≤+4, not per-capita)."),
    )
    print(f"[OK] hapi_idps.json — {len(out)} countries with IDP totals")


if __name__ == "__main__":
    main()
