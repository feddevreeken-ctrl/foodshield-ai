# HS commodity codes for food trade verification

Use these when pulling commodity-level flows from UN Comtrade, WITS, Eurostat, or
national portals. HS (Harmonized System) is the international standard; the first
2 digits are the chapter, 4 = heading, 6 = sub-heading. Match the granularity of
the claim — use HS4 for "wheat" broadly, HS6 when a specific form matters.

These cover the FoodShield staples + the major traded commodities on the Companies tab.

## Cereals (staples — the FDRS core)
| Commodity | HS4 | Common HS6 | Notes |
|---|---|---|---|
| Wheat | 1001 | 100199 (other wheat), 100119 (durum) | The project's primary import-dependency staple |
| Rice | 1006 | 100630 (semi/wholly milled), 100620 (husked) | Use milled (100630) to match USDA PSD "rice, milled" |
| Maize / corn | 1005 | 100590 (other), 100510 (seed) | "maize" and "corn" are the same; FoodShield uses both terms |
| Barley | 1003 | — | |
| Sorghum | 1007 | — | |

## Oilseeds & vegetable oils
| Commodity | HS4 | Common HS6 | Notes |
|---|---|---|---|
| Soybeans | 1201 | 120190 | Trader-heavy (Cargill, ADM, Bunge) |
| Soybean oil | 1507 | — | |
| Palm oil | 1511 | 151110 (crude), 151190 (refined) | Wilmar, Olam, Cargill |
| Sunflower oil | 1512 | — | Black Sea exposure (UKR/RUS) |
| Rapeseed/canola | 1205 | — | |

## Sugar, coffee, cocoa
| Commodity | HS4 | Common HS6 | Notes |
|---|---|---|---|
| Raw cane sugar | 1701 | 170114 (raw cane), 170199 (refined) | |
| Coffee | 0901 | 090111 (not roasted) | Olam, LDC |
| Cocoa beans | 1801 | — | Cargill, Olam; CIV/Ghana origin |

## Meat & livestock
| Commodity | HS4 | Common HS6 | Notes |
|---|---|---|---|
| Bovine meat (beef) | 0201 (fresh/chilled), 0202 (frozen) | — | JBS, Tyson, Cargill |
| Poultry | 0207 | — | Tyson, JBS (Pilgrim's) |
| Live cattle | 0102 | — | |
| Pork | 0203 | — | |

## Fertilizers (input-side exposure)
| Commodity | HS4 | Common HS6 | Notes |
|---|---|---|---|
| Urea | 3102 | 310210 | Nutrien, Yara |
| Potash (KCl) | 3104 | 310420 | Nutrien; BLR/RUS/CAN concentrated |
| Phosphates (DAP/MAP) | 3105 | 310530 (DAP), 310540 (MAP) | |
| Ammonia | 2814 | — | Yara |

## Aggregates (for FAOSTAT, not Comtrade)
FAOSTAT pre-aggregates these as item codes, not HS:
- **1842 = Food, Total** — the preferred net-food-trade basis.
- **1841 = Agricultural Products, Total** — broader fallback.

## Usage notes
- For a country's TOTAL wheat imports, pull HS 1001 with partner=World (Comtrade
  partnerCode=0) — that's the denominator for supplier-share / concentration.
- For supplier concentration (HHI), pull HS 1001 by each partner, rank by value,
  take top-5 shares. This is what `comtrade_staples.json` stores.
- When FAOSTAT and Comtrade disagree, check whether FAOSTAT used the food-total
  aggregate (1842) while Comtrade used a single HS line — different scope, expected gap.
- Re-exporters (NLD, BEL, SGP, HKG) show inflated gross flows; note this when a
  small country shows implausibly large trade.
