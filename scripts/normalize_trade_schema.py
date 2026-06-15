"""
Normalize trade metadata in data/countries.json without rebuilding the whole file.

Useful when:
  - the pipeline rules changed
  - countries.json already exists and we want to tighten provenance/basis tags
  - we want the validator to enforce trade metadata immediately, not only after
    the next full rebuild
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from _common import DATA_DIR
from trade_schema import normalize_trade_surface, summarize_trade_surface, validate_trade_surface

COUNTRIES_PATH = DATA_DIR / "countries.json"


def _dump_text(payload, pretty: bool):
    if pretty:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(COUNTRIES_PATH), help="countries.json path")
    ap.add_argument("--check", action="store_true", help="report changes/failures but do not write")
    ap.add_argument("--pretty", action="store_true", help="force pretty JSON output")
    ap.add_argument("--no-backup", action="store_true", help="skip .bak_trade_schema backup")
    args = ap.parse_args()

    path = Path(args.path)
    env = json.loads(path.read_text())
    countries = (((env.get("data") or {}).get("countries")) if isinstance(env, dict) else None) or {}
    if not isinstance(countries, dict) or not countries:
        raise RuntimeError(f"{path} does not contain data.countries")

    original = json.loads(json.dumps(env, ensure_ascii=False))
    changes = normalize_trade_surface(countries)
    failures = validate_trade_surface(countries)
    pretty = args.pretty or ("\n" in path.read_text())

    print(f"[trade-normalize] field updates: {len(changes)}")
    summary = summarize_trade_surface(countries)
    for field, counts in summary.items():
        print(
            f"  {field:12s} total={counts['total']:3d} "
            f"sourced={counts['sourced']:3d} partial={counts['partial']:3d} legacy={counts['legacy']:3d}"
        )
    print(f"[trade-normalize] validation failures after normalization: {len(failures)}")

    if args.check:
        return

    if env != original and not args.no_backup:
        shutil.copy(path, path.with_name(path.name + ".bak_trade_schema"))
    path.write_text(_dump_text(env, pretty=pretty))
    print(f"[trade-normalize] wrote {path}")


if __name__ == "__main__":
    main()
