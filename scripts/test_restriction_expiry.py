"""Boundary test for refresh_trade_restrictions.expire_measures and the validator's check."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refresh_trade_restrictions as r  # noqa: E402


def rows():
    return [
        {"country": "India", "commodity": "Sugar", "status": "official", "ends_date": "2026-09-30", "source_url": "x", "reviewed_at": "2026-09-20"},
        {"country": "A", "commodity": "Rice", "status": "reported", "ends_date": "2026-10-05"},
        {"country": "B", "commodity": "Wheat", "status": "historical", "ends_date": "2020-01-01"},
        {"country": "C", "commodity": "Maize", "status": "official"},
    ]


def main():
    fails = []
    x = rows()
    if r.expire_measures(x, date(2026, 9, 30)) or x[0]["status"] != "official":
        fails.append("a measure ending 30 Sep must still be in force on 30 Sep")
    x = rows()
    changed = r.expire_measures(x, date(2026, 10, 1))
    if changed != ["India Sugar"] or x[0]["status"] != "historical" or not x[0].get("auto_expired"):
        fails.append(f"a measure ending 30 Sep must expire on 1 Oct, got {changed}")
    if x[0].get("source_url") != "x" or x[0].get("reviewed_at") != "2026-09-20" or x[0].get("status_before_expiry") != "official":
        fails.append("expiry must keep the source, the review stamp and the prior status")
    if x[1]["status"] != "reported" or x[2]["status"] != "historical" or x[3]["status"] != "official":
        fails.append("only lapsed in-force measures may change")
    if r.expire_measures(x, date(2026, 10, 1)):
        fails.append("expiry must be idempotent")
    for f in fails:
        print("FAIL", f)
    print("PASS — restriction expiry boundary" if not fails else f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
