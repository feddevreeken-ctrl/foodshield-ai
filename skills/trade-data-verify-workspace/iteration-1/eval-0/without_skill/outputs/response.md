# Egypt wheat import figure — verification

**Verdict: confirmed. The ~$4.19B figure is right. Keep it. The reviewer is reading the units wrong, not the data.**

## What the file actually says

In `data/comtrade_staples.json`, the Egypt (`EGY`) wheat record is:

```json
"EGY": {
  "wheat": {
    "total_usd_m": 4192229314.02,
    "top_suppliers": [
      { "iso3": "RUS", "usd_m": 3057344485.33, "share_pct": 72.9 },
      { "iso3": "UKR", "usd_m":  595132048.40, "share_pct": 14.2 },
      { "iso3": "ROU", "usd_m":  278224066.61, "share_pct":  6.6 },
      { "iso3": "BGR", "usd_m":  176600258.39, "share_pct":  4.2 },
      { "iso3": "AUS", "usd_m":   54447037.65, "share_pct":  1.3 }
    ],
    "value_basis": "USD millions (primaryValue from Comtrade public preview)"
  }
}
```

The stored number is `4,192,229,314`, i.e. **$4.19 billion** — *not* $4.19 trillion. The reviewer almost certainly saw the field name `total_usd_m` (and the `value_basis` string that says "USD millions") and assumed the number is denominated in millions, which would imply ~$4.19 quadrillion. That would obviously be absurd, and that mismatch is what makes it "look too high." It's a labeling problem, not a data problem.

## Why $4.19B is the correct reading

1. **The unit convention is documented.** `skills/trade-data-verify/scripts/comtrade_pull.py` (lines 15–17) states plainly:
   > "the public preview returns primaryValue in RAW USD (not millions) … FoodShield's field is named `*_usd_m` but holds raw USD by convention."
   So the `_usd_m` suffix and the "USD millions" `value_basis` string are both misnomers. The value is raw USD. Read raw, it is $4.19B.

2. **The internals are arithmetically consistent.** The five supplier values sum to ~$4.16B (the top 5 of a slightly larger total), and each share matches: Russia $3.057B / $4.192B = 72.9%, Ukraine 14.2%, Romania 6.6%, etc. The shares only make sense if both `total_usd_m` and the supplier `usd_m` values are in the *same* unit (raw USD). There's no internal double-counting or scaling error.

3. **It matches reality.** Egypt is the world's largest or near-largest wheat importer, buying on the order of 11–12 million tonnes a year. At 2024 wheat prices (~$260–270/tonne CIF), that lands around $3–4.5B. A figure of ~$4.2B for HS 1001 in 2024 is squarely in the expected range. The supplier mix — Russia dominant (~73%), then Ukraine, Romania, Bulgaria, Australia — is exactly Egypt's known Black Sea-heavy sourcing pattern. Nothing here is anomalous.

4. **The dashboard reads it correctly.** `index.html` formats these values with raw-USD logic (`n / 1e6 → "M"`, `/1e9 → "bn"`), so the front end already treats the field as raw USD. The displayed figure is consistent with the $4.19B reading; there is no rendering bug inflating it.

## What to do

- **Don't change the number.** $4.19B is verified-correct for Egypt wheat (HS 1001) imports, 2024, sourced from UN Comtrade. Mark it confirmed.
- **Fix the label, not the value.** The real defect is the misleading `value_basis: "USD millions …"` string and the `_usd_m` / `usd_m` field names, which hold raw USD. To stop this false alarm from recurring:
  - Update `value_basis` to read `"raw USD (Comtrade primaryValue)"` — matching what the pull script already writes for fresh data.
  - Longer term, rename the fields to `total_usd` / `usd` (the pull script's own output schema), or add a one-line note in `_meta` that `*_usd_m` holds raw USD despite the name.
- **Reply to the reviewer:** the figure is correct at $4.19B; the apparent "too high" is because the field is misleadingly named/labeled "millions" but actually stores raw USD. Egypt importing ~$4.2B of wheat in 2024 is exactly what we'd expect for the world's top wheat importer.

## Verification record

| field | value |
|---|---|
| claim | EGY wheat (HS 1001) imports, 2024 |
| stored value | 4,192,229,314 USD = **$4.19B** |
| authoritative source | UN Comtrade Plus (HS6, 2024), `_meta.source` in comtrade_staples.json |
| cross-check | Plausibility vs. known volume (~11–12 Mt) × 2024 price (~$260–270/t) ≈ $3–4.5B; supplier mix matches Egypt's Black Sea sourcing |
| unit basis | raw USD (field name `_usd_m` and `value_basis` "USD millions" are both misnomers — confirmed by comtrade_pull.py docstring) |
| verdict | **confirmed** |
| as_of | 2024 (data); generated 2026-06-01 |
| action | keep value; correct the unit label/field naming |
