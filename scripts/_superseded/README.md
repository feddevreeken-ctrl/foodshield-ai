# Superseded scripts

These were the first-pass trade re-verification scripts. They've been replaced by
the clean, modular `scripts/trade_pipeline/` (config / pull / merge / build_fields).
Use that instead — see `scripts/trade_pipeline/README.md`.

- reverify_demo_trade.py   → replaced by trade_pipeline/build_fields.py (was demo-only: EGY/NLD)
- reverify_trade_fields.py → replaced by trade_pipeline/build_fields.py (general version, now modular)

Kept for reference only. Safe to delete once you're comfortable with the pipeline.
