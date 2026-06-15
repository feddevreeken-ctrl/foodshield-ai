#!/usr/bin/env python3
"""
========================================================================
FoodShield — diagnose the 3 dead crisis feeds (IPC / WFP HungerMap / WFP country)
RUN ON YOUR MAC. The sandbox can't reach api.hungermapdata.org (blocks datacenter
IPs), so I can't see the live response shape from here — this prints it for me.
========================================================================

The feeds fail with "'str' object has no attribute 'get'" → the HungerMap v2 API
changed its JSON shape and the parsers no longer match it. This script fetches each
endpoint LIVE, prints the actual current structure (so the parser can be fixed against
real data, not a guess), and saves the raw responses to data/_feed_diag/ for inspection.

It does NOT modify any project data — purely diagnostic.

USAGE
  cd "/path/to/FoodSecurity AI"
  python3 scripts/diagnose_crisis_feeds_MAC.py

Then paste me the printed output (or share the saved files) and I'll fix the parsers.
"""
import json, os, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "_feed_diag")
os.makedirs(OUT, exist_ok=True)

ENDPOINTS = {
    "wfp_hungermap_adm0data":  "https://api.hungermapdata.org/v2/adm0data.json",
    "wfp_hungermap_adm0summary":"https://api.hungermapdata.org/v2/adm0summary.json",
    "ipc_hungermap":           "https://api.hungermapdata.org/v2/ipc.json",
    # WFP per-country (FX / inflation / wasting) — sample one country (Kenya=KEN)
    "wfp_country_KEN":         "https://api.hungermapdata.org/v2/iso3/KEN/countryIso3Data.json",
}

def describe(obj, depth=0, maxdepth=3):
    """Compact structural description of a JSON value."""
    pad = "  " * depth
    if isinstance(obj, dict):
        keys = list(obj.keys())
        out = f"dict[{len(keys)} keys]: {keys[:12]}" + (" …" if len(keys) > 12 else "")
        if depth < maxdepth and keys:
            # describe the first value, and if keys look like ISO3, a sample row
            first = obj[keys[0]]
            out += f"\n{pad}  [{keys[0]!r}] -> " + describe(first, depth+1, maxdepth)
        return out
    if isinstance(obj, list):
        out = f"list[{len(obj)} items]"
        if obj and depth < maxdepth:
            out += f"\n{pad}  [0] -> " + describe(obj[0], depth+1, maxdepth)
        return out
    if isinstance(obj, str):
        return f"str({obj[:60]!r})"
    return f"{type(obj).__name__}({obj})"

def main():
    for name, url in ENDPOINTS.items():
        print("=" * 70)
        print(f"{name}\n  {url}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 FoodShield-diag"})
            with urllib.request.urlopen(req, timeout=60) as r:
                status = r.status
                body = r.read().decode("utf-8", "replace")
        except Exception as e:
            print(f"  FETCH ERROR: {e}")
            continue
        print(f"  HTTP {status}, {len(body)} bytes")
        # save raw
        raw_path = os.path.join(OUT, name + ".json")
        open(raw_path, "w").write(body)
        try:
            j = json.loads(body)
        except Exception as e:
            print(f"  NOT JSON: {e}; first 200 chars: {body[:200]!r}")
            continue
        print("  STRUCTURE:")
        print("  " + describe(j).replace("\n", "\n  "))
        # if it's a list or has features, show one full sample row so I can map fields
        sample = None
        if isinstance(j, list) and j:
            sample = j[0]
        elif isinstance(j, dict):
            for k in ("features", "body", "data", "countries"):
                v = j.get(k)
                if isinstance(v, list) and v: sample = v[0]; break
                if isinstance(v, dict) and v: sample = next(iter(v.values())); break
            if sample is None and j:
                sample = next(iter(j.values()))
        if sample is not None:
            print("  SAMPLE ROW (first 1200 chars of JSON):")
            print("  " + json.dumps(sample, indent=1)[:1200].replace("\n", "\n  "))
    print("=" * 70)
    print(f"Raw responses saved to {OUT}/ . Paste the STRUCTURE + SAMPLE ROW output back to me.")

if __name__ == "__main__":
    main()
