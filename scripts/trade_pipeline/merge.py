"""
trade_pipeline/merge.py — JOB: safely fold a new pull into comtrade_staples.json.

The merge rule (so batched pulls accumulate and a sparse run can't wipe data):
  - Per (country, commodity): the NEW pull wins ONLY if it has a positive
    total_usd_m. An empty/zero new entry never overwrites an existing good one.
  - Countries/commodities not in the new pull are left untouched.
  - A backup (.bak) is written before any change.

This is the only module that writes comtrade_staples.json on a merge, so the
"keep your good data" logic lives in exactly one place.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
STAPLES = DATA / "comtrade_staples.json"
EXPORTS = DATA / "comtrade_exports.json"


def _load(path):
    if path.exists():
        try:
            env = json.loads(path.read_text())
            return env.get("data", {}) or {}, env.get("_meta", {})
        except Exception:
            pass
    return {}, {}


def _merge(new_data, path, source_label):
    """Shared merge rule: new positive value wins; empty never overwrites."""
    existing, _ = _load(path)
    added = updated = kept = 0

    for iso, commodities in new_data.items():
        slot = existing.setdefault(iso, {})
        for cmd, rec in commodities.items():
            new_total = rec.get("total_usd_m") or 0
            if cmd not in slot:
                slot[cmd] = rec
                added += 1
            elif new_total > 0:
                slot[cmd] = rec        # new positive value wins
                updated += 1
            else:
                kept += 1              # new is empty → keep existing

    if path.exists():
        shutil.copy(path, path.with_suffix(".json.bak"))

    out = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source_label,
            "notes": (f"Merged pull: +{added} new, {updated} updated, {kept} kept. "
                      f"{len(existing)} reporters total."),
            "version": "v23",
        },
        "data": existing,
    }
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"[merge] +{added} new, {updated} updated, {kept} kept → "
          f"{len(existing)} reporters in {path.name}")
    return existing


def merge_into_staples(new_data):
    """Imports → suppliers. Folds a pull into comtrade_staples.json."""
    return _merge(new_data, STAPLES,
                  "UN Comtrade Plus (comtradeapi.un.org) — HS6 imports, merged batches")


def merge_into_exports(new_data):
    """Exports → destinations. Folds a pull into comtrade_exports.json."""
    return _merge(new_data, EXPORTS,
                  "UN Comtrade Plus (comtradeapi.un.org) — HS6 exports, merged batches")


if __name__ == "__main__":
    print("merge.py is called by pull.py; nothing to do standalone.")
