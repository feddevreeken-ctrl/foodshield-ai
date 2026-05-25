"""
Data integrity entry point (alias for validate_data.py).

The repo has two equally-valid filenames for the same script for historical
reasons:
  - scripts/validate_data.py            (original, wired into run_all.py)
  - scripts/validate_data_integrity.py  (alias, requested by audit tasks)

Both run the full set of structural envelope checks PLUS the USDA PSD
content-integrity checks introduced May 2026 after the BOL↔Belarus /
NER↔Nigeria / NGA↔Niger ISO-swap bug recurred.

Usage:
  python scripts/validate_data_integrity.py
"""
from validate_data import main


if __name__ == "__main__":
    main()
