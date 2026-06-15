"""
Deterministically rebuild the commodity-flow bundle from the current base file,
reviewed sidecars, and optionally the canonical Beefmap export.

Default behavior is non-destructive:
  python3 scripts/rebuild_commodity_flows.py
    -> writes data/commodity_flows.rebuilt.json

Optional Beefmap refresh:
  python3 scripts/rebuild_commodity_flows.py --beefmap /path/to/beef-data.js
    -> rebuilds beef via build_commodity_flows_from_beefmap.js into a temp file,
       merges that beef object plus sidecars, and writes the rebuilt output.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DEFAULT_BASE = DATA / "commodity_flows.json"
DEFAULT_OUTPUT = DATA / "commodity_flows.rebuilt.json"
SIDECARES = sorted(DATA.glob("commodity_flows_*.json"))
REQUIRED_KEYS = {"hs", "unit", "balances", "flows", "rankings", "companies", "global", "forecast", "scenarios"}


def _load_json(path: Path):
    return json.loads(path.read_text())


def _infer_sidecar_payload(path: Path):
    obj = _load_json(path)
    payload_keys = [key for key in obj.keys() if not key.startswith("_")]
    filename_key = path.stem.replace("commodity_flows_", "")

    # Flat sidecar: the commodity object is the file itself.
    if REQUIRED_KEYS.issubset(set(payload_keys)):
        payload = {key: value for key, value in obj.items() if key != "_sources_patch"}
        return filename_key, payload, obj.get("_sources_patch") or {}

    # Wrapped sidecar: {"maize": {...}, "_sources_patch": {...}}
    if filename_key in obj and isinstance(obj[filename_key], dict):
        return filename_key, obj[filename_key], obj.get("_sources_patch") or {}

    if len(payload_keys) == 1 and isinstance(obj[payload_keys[0]], dict):
        key = payload_keys[0]
        return key, obj[key], obj.get("_sources_patch") or {}

    raise RuntimeError(f"Could not infer commodity payload from {path}")


def _validate_commodity(key: str, payload: dict):
    missing = sorted(REQUIRED_KEYS - set(payload.keys()))
    if missing:
        raise RuntimeError(f"{key} missing required keys: {missing}")


def _merge_sources(target: dict, patch: dict):
    for key, value in (patch or {}).items():
        target[key] = value


def _load_beef_from_beefmap(beefmap_path: Path):
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                "node",
                str(ROOT / "scripts" / "build_commodity_flows_from_beefmap.js"),
                str(beefmap_path),
                str(tmp_path),
            ],
            check=True,
        )
        env = _load_json(tmp_path)
        beef = ((env.get("commodities") or {}).get("beef")) if isinstance(env, dict) else None
        if not beef:
            raise RuntimeError("Beefmap rebuild did not produce commodities.beef")
        return beef, env.get("_sources") or {}
    finally:
        tmp_path.unlink(missing_ok=True)


def rebuild(base_path: Path, output_path: Path, beefmap_path: Path | None = None):
    env = _load_json(base_path)
    if "commodities" not in env or not isinstance(env["commodities"], dict):
        raise RuntimeError(f"{base_path} missing commodities object")
    env.setdefault("_sources", {})

    merged = 0
    if beefmap_path:
        beef, sources = _load_beef_from_beefmap(beefmap_path)
        _validate_commodity("beef", beef)
        env["commodities"]["beef"] = beef
        _merge_sources(env["_sources"], sources)
        merged += 1

    for sidecar in SIDECARES:
        key, payload, sources_patch = _infer_sidecar_payload(sidecar)
        _validate_commodity(key, payload)
        env["commodities"][key] = payload
        _merge_sources(env["_sources"], sources_patch)
        merged += 1

    env.setdefault("_meta", {})
    env["_meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    env["_meta"]["version"] = env["_meta"].get("version") or "v23"
    env["_meta"]["method"] = (
        "Deterministic rebuild from commodity_flows.json base, reviewed sidecars, "
        "and optional Beefmap regeneration via rebuild_commodity_flows.py."
    )
    env["_meta"]["commodities"] = sorted(env["commodities"].keys())

    if output_path == base_path:
        shutil.copy(base_path, base_path.with_name(base_path.name + ".bak_rebuild"))
    output_path.write_text(json.dumps(env, indent=2, ensure_ascii=False))
    print(f"[commodity-rebuild] merged {merged} overlays into {output_path}")
    print(f"[commodity-rebuild] commodities: {len(env['commodities'])}")
    print(f"[commodity-rebuild] sources: {len(env['_sources'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(DEFAULT_BASE), help="base commodity_flows.json file")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="output path")
    ap.add_argument("--beefmap", help="optional beef-data.js path to regenerate beef before merge")
    ap.add_argument("--in-place", action="store_true", help="overwrite the base file (writes a backup first)")
    args = ap.parse_args()

    base_path = Path(args.base)
    output_path = base_path if args.in_place else Path(args.output)
    beefmap_path = Path(args.beefmap) if args.beefmap else None
    rebuild(base_path=base_path, output_path=output_path, beefmap_path=beefmap_path)


if __name__ == "__main__":
    main()
