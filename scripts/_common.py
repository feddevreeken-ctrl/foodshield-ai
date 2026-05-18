"""
FoodShield AI — Common utilities for data refresh scripts.

All refresh scripts share these helpers for HTTP fetching, retries, and JSON output.
Designed to run in GitHub Actions (Ubuntu, Python 3.11+).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Repo root: scripts/_common.py -> ../
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

UA = "FoodShield-AI/19 (+https://foodshield-ai-fv.vercel.app)"
DEFAULT_TIMEOUT = 30


def http_get(url, *, params=None, headers=None, timeout=DEFAULT_TIMEOUT, retries=3, backoff=2):
    """GET with retries and a polite User-Agent. Returns requests.Response or raises."""
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last_exc = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_exc}")


def write_json(filename, payload, *, source=None, notes=None):
    """Write JSON to data/<filename> with a standard envelope."""
    out = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source or "unknown",
            "notes": notes or "",
            "version": "v19",
        },
        "data": payload,
    }
    path = DATA_DIR / filename
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"[OK] wrote {path} ({len(json.dumps(payload))} bytes payload)")
    return path


def safe_run(label, fn):
    """Run a refresh function and never crash the workflow — log failure, write a stub."""
    print(f"\n=== {label} ===")
    try:
        fn()
    except Exception as e:
        print(f"[FAIL] {label}: {e}", file=sys.stderr)
        # Write a stub so the JSON file exists and the frontend can degrade gracefully
        stub_name = label.lower().replace(" ", "_") + "_FAILED.json"
        write_json(stub_name, {}, source=label, notes=f"Last refresh failed: {e}")


def env(key, default=None, required=False):
    val = os.environ.get(key, default)
    if required and not val:
        print(f"[SKIP] {key} not set in environment; skipping")
        return None
    return val
