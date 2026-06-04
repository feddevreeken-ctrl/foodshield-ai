# trade_pipeline/ — sourcing country trade fields, in clear steps

Four small modules, each with ONE job, so changing one thing never ripples into
the others. Data flows one direction: **pull → merge → build_fields → (audit)**.

```
config.py        ← THE FILE YOU EDIT. Country list, commodities, year, throttle.
pull.py          ← JOB: fetch supplier data from UN Comtrade. Writes nowhere but the staples file (via merge).
merge.py         ← JOB: fold a pull into comtrade_staples.json safely (never wipes good data).
build_fields.py  ← JOB: read sourced files → write countries.json suppliers/supPct/imports + audit.
```

`pull.py` and `build_fields.py` are independent: a bad pull can't corrupt
`countries.json`, and re-running `build_fields.py` never re-pulls. That's the
whole point of the split.

## The normal workflow

**1. Pull supplier data** (quota-limited; batch it). Public endpoint ≈ 500 calls/day,
10 commodities per country → ~50 countries/day. Each run merges, so batches add up:

```bash
cd scripts/trade_pipeline
python3 pull.py --batch 45               # day 1: top 45 importers
python3 pull.py --batch 45 --start 45    # day 2: next 45
python3 pull.py --batch 45 --start 90    # day 3 … and so on
```

Or pull a specific few to re-verify:
```bash
python3 pull.py --only EGY,NLD,VNM,IND,PAK
```

If you use your keyed Comtrade subscription (higher quota), export it first:
```bash
export COMTRADE_KEY=your-subscription-key
python3 pull.py                          # whole universe in one run
```

Preview the plan without fetching:
```bash
python3 pull.py --dry-run --batch 45
```

**1b. (optional) Pull EXPORT data** so `exports` / `exportDests` get sourced too.
Imports tell you who *supplies* a country; exports need a separate reporter-side
pull (`--flow X`), which writes to `comtrade_exports.json`:
```bash
python3 pull.py --flow X --batch 45            # export destinations, same batching
python3 pull.py --flow X --only BRA,USA,UKR    # or a few big exporters
```

**2. Build the country fields** from whatever sourced data now exists:
```bash
python3 build_fields.py
```
This sources `suppliers`/`supPct`/`imports` from the import pulls and
`exports`/`exportDests` from the export pulls — each only where real data exists.
This refreshes `suppliers` / `supPct` / `imports` in `countries.json` and writes
`reverify_records.json` (the audit trail). Run it after every pull batch — it
picks up the newly-merged countries automatically.

**3. Check progress:**
```bash
cd ..
python3 flag_legacy_countries.py         # watch the legacy ratio fall
python3 qa_checks.py                     # confirm nothing regressed
```

## What "honest" means here

- A country gets **sourced** suppliers only when Comtrade actually returned its
  partner data. PSD-only countries get a sourced **imports** basket but their
  suppliers stay legacy + flagged — because PSD has no partner breakdown. We do
  not invent supplier shares.
- Shares may sum to <100% (top-5 of a diversified importer). That's accurate; we
  don't pad to 100.
- The supplier-concentration basis is each country's **largest staple import** —
  recorded in `_supplier_basis` so it's auditable.

## If you want to change something

- More/fewer countries, a new commodity, slower throttle → **edit `config.py` only.**
- The merge safety rule → `merge.py` (one place).
- How a country field is shaped/labelled → `build_fields.py` (one place).
- Nothing requires touching `index.html`; the frontend already reads these fields
  with provenance badges.
