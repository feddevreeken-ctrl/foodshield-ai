"""
Build per-country scenario-tab analyst profiles.

This is a build-time prose generator. The model may phrase an analyst profile,
but it may not originate a number: all measured values are computed in Python,
passed as locked facts, and every accepted sentence is validated by the same
guards used by build_news_interpretation.py. Scenario profiles add an even
stricter gate: any digit in the prose rejects the model output.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from _common import DATA_DIR
from build_news_interpretation import (
    PROVIDERS,
    call_llm_rotating,
    resolve_provider,
    validate_text,
    _is_quota_error,
)

OUTPUT = "scenario_profiles.json"

COMMODITIES = (
    ("wheat", "wheat"),
    ("rice", "rice"),
    ("maize", "corn"),
)

MAX_LLM_PAIRS = int(os.environ.get("SCENARIO_PROFILE_LLM_PAIR_LIMIT", "380"))
CALL_SPACING_SECONDS = float(
    os.environ.get("SCENARIO_LLM_SPACING", os.environ.get("NEWS_LLM_SPACING", "2"))
)
MAX_CONSECUTIVE_API_ERRORS = int(os.environ.get("SCENARIO_MAX_API_ERRORS", "3"))

DIGIT_RE = re.compile(r"\d")
NUMBER_WORD_RE = re.compile(
    r"\b("
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|million|billion|trillion|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"once|twice|thrice|single|double|doubled|doubling|triple|tripled|"
    r"tripling|quadruple|quadrupled|quadrupling|half|halves|halved|"
    r"quarter|quarters|dozen|score|percent|percentage"
    r")\b",
    re.I,
)


def _load(name):
    path = DATA_DIR / name
    if not path.exists():
        print(f"[WARN] data/{name} missing; treating as empty.")
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f"[WARN] data/{name} unreadable ({type(e).__name__}: {e}); treating as empty.")
        return {}


def _payload(envelope):
    if isinstance(envelope, dict) and "data" in envelope:
        return envelope["data"]
    return envelope


def _value(node):
    if isinstance(node, dict) and "value" in node:
        return node.get("value")
    return node


def _r(value, places=1):
    if value is None:
        return None
    try:
        v = round(float(value), places)
    except (TypeError, ValueError):
        return None
    return int(v) if places == 0 else v


def _countries_by_iso(payload):
    countries = (payload or {}).get("countries") if isinstance(payload, dict) else payload
    if isinstance(countries, dict):
        return countries
    if isinstance(countries, list):
        out = {}
        for row in countries:
            if not isinstance(row, dict):
                continue
            iso = row.get("iso") or row.get("iso3") or row.get("code")
            if iso:
                out[str(iso).upper()] = row
        return out
    return {}


def _country_name(iso, country_row, psd_row):
    if isinstance(country_row, dict):
        for key in ("name", "country", "label", "n"):
            value = _value(country_row.get(key))
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(psd_row, dict):
        for _, psd_key in COMMODITIES:
            balance = psd_row.get(psd_key)
            name = balance.get("country") if isinstance(balance, dict) else None
            if isinstance(name, str) and name.strip():
                return name.strip()
    return iso


def _econ_access(country_row):
    if not isinstance(country_row, dict):
        return None
    direct = _r(_value(country_row.get("econ_access")), 1)
    if direct is not None:
        return direct
    vector = _value(country_row.get("c"))
    if isinstance(vector, (list, tuple)) and len(vector) > 7:
        return _r(vector[7], 1)
    return None


def _usable_balance(balance):
    if not isinstance(balance, dict):
        return False
    if balance.get("imports_kt") is None:
        return False
    try:
        return float(balance.get("consumption_kt")) > 0
    except (TypeError, ValueError):
        return False


def _event_matches_commodity(event, commodity):
    text = str((event or {}).get("commodity") or "").lower()
    if commodity == "maize":
        return "maize" in text or "corn" in text
    return commodity in text


def _precedent_records(events, iso, commodity):
    records = []
    for event in events or []:
        if not isinstance(event, dict) or not _event_matches_commodity(event, commodity):
            continue
        for response in event.get("responses") or []:
            if not isinstance(response, dict):
                continue
            if str(response.get("iso") or "").upper() != iso:
                continue
            what = (response.get("what") or "").strip()
            outcome = (response.get("outcome") or "").strip()
            record = " ".join(part for part in (what, outcome) if part).strip()
            if record:
                records.append(record)
    return records


def _binding_constraint(import_dependence_ratio, stock_cover_months, econ_access):
    econ_high = econ_access is not None and econ_access >= 50
    if import_dependence_ratio < 0.35:
        return "domestic production"
    if stock_cover_months is not None and stock_cover_months < 2 and econ_high:
        return "physical supply"
    if econ_high:
        return "import cost"
    return "absorbed"


def build_facts(iso, commodity, psd_key, balance, country_row, psd_row, events):
    imports_kt = _r(balance.get("imports_kt"), 1)
    consumption_kt = _r(balance.get("consumption_kt"), 1)
    production_kt = _r(balance.get("production_kt"), 1)
    stocks_kt = _r(balance.get("stocks_kt"), 1)

    import_dependence_ratio = float(balance["imports_kt"]) / float(balance["consumption_kt"])
    import_dependence_pct = _r(import_dependence_ratio * 100, 1)
    stock_cover_months = (
        _r((float(balance["stocks_kt"]) / float(balance["consumption_kt"])) * 12, 1)
        if balance.get("stocks_kt") is not None
        else None
    )
    econ_access = _econ_access(country_row)

    return {
        "country": _country_name(iso, country_row, psd_row),
        "commodity": commodity,
        "imports_kt": imports_kt,
        "consumption_kt": consumption_kt,
        "production_kt": production_kt,
        "stocks_kt": stocks_kt,
        "import_dependence_pct": import_dependence_pct,
        "stock_cover_months": stock_cover_months,
        "econ_access": econ_access,
        "binding_constraint": _binding_constraint(
            import_dependence_ratio, stock_cover_months, econ_access
        ),
        "precedent_records": _precedent_records(events, iso, commodity),
        "analogue": "none here",
    }


def iter_fact_pairs(psd, countries, events):
    for iso, psd_row in sorted((psd or {}).items()):
        if not isinstance(psd_row, dict):
            continue
        iso = str(iso).upper()
        country_row = countries.get(iso) or {}
        for commodity, psd_key in COMMODITIES:
            balance = psd_row.get(psd_key)
            if not _usable_balance(balance):
                continue
            yield iso, commodity, build_facts(
                iso, commodity, psd_key, balance, country_row, psd_row, events
            )


def system_prompt(facts):
    return (
        f"You are writing a 2-3 sentence country profile for how {facts['country']} "
        f"would respond to a {facts['commodity']} supply/price shock.\n\n"
        "STRICT RULES: use NO digits or number words whatsoever (no percentages, "
        "tonnages, years, 'doubled', 'half'); do not restate the supplied facts; "
        "describe institutional behaviour and response repertoire ONLY if supported "
        "by the precedent records supplied; if no precedent records are supplied, "
        "describe the response posture implied by the binding constraint class in "
        "general terms and say plainly that no documented episode backs it. Plain "
        "prose, no headers, no bullet lists."
    )


def build_prompt(facts, violation=None):
    prompt = (
        "FACTS (locked; these facts are for validation and must not be restated):\n"
        + json.dumps(facts, indent=2, ensure_ascii=False)
        + "\n\nWrite the profile. Keep it plain, specific to institutional behavior, "
          "and free of digits and number words."
    )
    if violation:
        prompt += (
            "\n\nYour previous response was rejected for this violation: "
            + violation
            + ". Rewrite from scratch with no digits, no number words, no field names, "
              "and no unsupported claims."
        )
    return prompt


def numbers_free_violations(text):
    return {
        "digits": sorted(set(DIGIT_RE.findall(text or ""))),
        "number_words": sorted(
            {m.group(0).lower() for m in NUMBER_WORD_RE.finditer(text or "")}
        ),
    }


def validate_profile_text(text, facts):
    ok, detail = validate_text(text, facts)
    stricter = numbers_free_violations(text)
    detail.update(stricter)
    return ok and not stricter["digits"] and not stricter["number_words"], detail


def violation_summary(detail):
    parts = []
    for key in (
        "unsupported_numbers",
        "sign_inversions",
        "word_quantities",
        "field_names",
        "digits",
        "number_words",
    ):
        values = detail.get(key) or []
        if values:
            shown = ", ".join(str(v) for v in values[:6])
            if len(values) > 6:
                shown += ", ..."
            parts.append(f"{key}: {shown}")
    return "; ".join(parts) or "validation failed"


def deterministic_text(facts):
    country = facts["country"]
    commodity = facts["commodity"]
    constraint = facts["binding_constraint"]
    if facts.get("precedent_records"):
        return (
            f"The supplied records show a documented administrative response pattern "
            f"for {country} in past {commodity} shocks. With the pressure centered on "
            f"{constraint}, the profile should emphasize that observed repertoire "
            f"while treating any future move as contingent."
        )
    return (
        f"No documented episode in the supplied records backs a country-specific "
        f"response pattern for {country} in {commodity}. With the pressure centered "
        f"on {constraint}, the posture is best read as a general exposure class "
        f"rather than an observed playbook."
    )


def safe_deterministic_text(facts):
    text = deterministic_text(facts)
    ok, detail = validate_profile_text(text, facts)
    if ok:
        return text
    fallback = (
        f"No documented profile can be published for {facts['country']} in "
        f"{facts['commodity']} from the locked inputs."
    )
    ok, detail = validate_profile_text(fallback, facts)
    if ok:
        return fallback
    raise RuntimeError(f"deterministic profile failed validation: {detail}")


def generate_profile(provider, api_key, facts):
    last_detail = None
    for attempt in range(2):
        prompt = build_prompt(
            facts,
            violation_summary(last_detail) if last_detail and attempt else None,
        )
        try:
            raw = call_llm_rotating(provider, api_key, system_prompt(facts), prompt)
        except Exception as e:
            return safe_deterministic_text(facts), "deterministic", {
                "reason": f"api_error: {type(e).__name__}: {e}",
                "quota_error": _is_quota_error(e),
            }

        text = (raw or "").strip()
        if not text:
            last_detail = {"empty_response": ["empty model response"]}
        else:
            ok, detail = validate_profile_text(text, facts)
            if ok:
                return text, "llm", {"reason": None, "quota_error": False}
            last_detail = detail

        if attempt == 0:
            time.sleep(min(CALL_SPACING_SECONDS, 2))

    return safe_deterministic_text(facts), "deterministic", {
        "reason": "validation_failed: " + violation_summary(last_detail or {}),
        "quota_error": False,
        "rejected": True,
    }


def output_status(llm_count, deterministic_count, forced_mixed=False):
    total = llm_count + deterministic_count
    if forced_mixed and total:
        return "mixed"
    if total and llm_count == total:
        return "llm"
    if llm_count == 0:
        return "deterministic_only"
    return "mixed"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build scenario analyst profiles.")
    parser.add_argument("--limit", type=int, default=None, help="maximum profile pairs to build")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do not call the LLM; write deterministic profiles only",
    )
    args = parser.parse_args([] if argv is None else argv)

    provider, api_key = (None, None) if args.dry_run else resolve_provider()
    if args.dry_run:
        print("[INFO] dry run: writing deterministic scenario profiles only.")
    elif provider:
        print(f"[INFO] LLM provider: {provider} ({PROVIDERS[provider]['model']})")
    else:
        print("[INFO] no LLM provider configured; writing deterministic scenario profiles only.")

    psd = _payload(_load("usda_psd.json")) or {}
    precedent_payload = _load("precedents.json") or {}
    countries = _countries_by_iso(_payload(_load("countries.json")) or {})
    events = precedent_payload.get("events") if isinstance(precedent_payload, dict) else []

    pairs = list(iter_fact_pairs(psd, countries, events))
    if args.limit is not None:
        pairs = pairs[: max(args.limit, 0)]

    profiles = {}
    llm_count = 0
    deterministic_count = 0
    llm_pairs_started = 0
    consecutive_api_errors = 0
    llm_disabled_reason = None
    quota_stop = False

    for idx, (iso, commodity, facts) in enumerate(pairs):
        can_call_llm = (
            provider is not None
            and not args.dry_run
            and llm_disabled_reason is None
            and llm_pairs_started < MAX_LLM_PAIRS
        )

        if can_call_llm:
            if llm_pairs_started:
                time.sleep(CALL_SPACING_SECONDS)
            llm_pairs_started += 1
            text, status, detail = generate_profile(provider, api_key, facts)
            if status == "llm":
                llm_count += 1
                consecutive_api_errors = 0
            else:
                deterministic_count += 1
                reason = detail.get("reason") or "deterministic fallback"
                if detail.get("quota_error"):
                    quota_stop = True
                    llm_disabled_reason = (
                        "quota/rate-limit error after provider key rotation; "
                        "remaining profiles are deterministic"
                    )
                    print(f"  [429] {iso} {commodity}: {reason}")
                elif reason.startswith("api_error"):
                    consecutive_api_errors += 1
                    print(f"  [TEMPLATE] {iso} {commodity} — {reason}")
                    if consecutive_api_errors >= MAX_CONSECUTIVE_API_ERRORS:
                        llm_disabled_reason = (
                            f"{consecutive_api_errors} consecutive provider errors; "
                            "remaining profiles are deterministic"
                        )
                else:
                    consecutive_api_errors = 0
                    print(f"  [TEMPLATE] {iso} {commodity} — {reason}")
        else:
            text = safe_deterministic_text(facts)
            status = "deterministic"
            deterministic_count += 1
            if provider and llm_disabled_reason is None and llm_pairs_started >= MAX_LLM_PAIRS:
                llm_disabled_reason = (
                    f"LLM pair budget reached at {MAX_LLM_PAIRS}; "
                    "remaining profiles are deterministic"
                )

        profiles.setdefault(iso, {})[commodity] = {
            "text": text,
            "status": status,
            "binding": facts["binding_constraint"],
            "has_precedent": bool(facts.get("precedent_records")),
        }

        if status == "llm":
            print(f"  [AI] {iso} {commodity}")
        elif not can_call_llm and (idx < 10 or idx == len(pairs) - 1):
            print(f"  [TEMPLATE] {iso} {commodity}")

        if llm_disabled_reason and can_call_llm:
            print(f"[GUARD] {llm_disabled_reason}")

    model = PROVIDERS[provider]["model"] if provider else None
    payload = {
        "_meta": {
            "generated": datetime.now(timezone.utc).date().isoformat(),
            "status": output_status(
                llm_count,
                deterministic_count,
                forced_mixed=quota_stop
                or (
                    provider is not None
                    and llm_pairs_started >= MAX_LLM_PAIRS
                    and deterministic_count > 0
                ),
            ),
            "model": model,
            "validated": True,
            "note": (
                "numbers-free by construction: any digit rejects; prose validated "
                "against locked facts"
            ),
            "pairs": len(pairs),
            "llm_count": llm_count,
            "deterministic_count": deterministic_count,
        },
        "profiles": profiles,
    }

    path = DATA_DIR / OUTPUT
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(
        f"[OK] wrote {path} — {len(pairs)} pairs, {llm_count} llm, "
        f"{deterministic_count} deterministic"
    )
    if quota_stop:
        print("[INFO] LLM calls stopped after repeated 429/rate-limit behaviour.")
    elif llm_disabled_reason:
        print(f"[INFO] LLM calls stopped early: {llm_disabled_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
