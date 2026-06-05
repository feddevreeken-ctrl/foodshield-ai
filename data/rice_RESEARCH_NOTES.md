# Rice (HS 1006) — research notes

Sidecar: `data/commodity_flows_rice.json`. Built to the `COMMODITY_RESEARCH_SPEC.md`
"Beefmap method": every bilateral flow is `observed` (published/corroborated figure,
cited) or `modeled` (curated route with the basis stated). No flow without `src` + `note`.

## Headline counts

- **Flows: 62** bilateral lines.
- **Observed / modeled split: 42 observed (68%) / 20 modeled (32%).**
  - "Observed" here means a published customs / trade-association / WITS-Comtrade /
    OEC figure exists and is cited in the note (often with the exact tonnage or value).
    The 68% is genuinely backed — rice trade is unusually well-documented at the
    bilateral level because importers publish supplier breakdowns (PSA Philippines,
    Indonesia customs, Senegal customs, China customs, Malaysia/WITS) and the Thai
    Rice Exporters Association publishes destination tonnages. It is NOT inflated:
    every "observed" flow names its source figure. Modeled flows are overland/transit
    corridors (Nepal, Afghanistan, Benin→Nigeria, Cambodia paddy) where clean published
    bilateral tonnages don't exist.
- **Balances: 36 countries** (milled-basis kt, mostly USDA FAS PSD MY2024/25 with
  customs cross-checks). Covers all top exporters, all top importers, plus West-African
  buyers, Mercosur exporters, MENA basmati buyers, and East/North Asian TRQ importers.
- **Rankings: top 10 exporters + top 10 importers** (USDA PSD 2024, customs-checked).
- **Companies: 6** (Olam Agri, Louis Dreyfus, ITC Ltd, KRBL/India Gate, Phoenix/REAP
  cluster, CP/Thai exporter cluster).
- **Scenarios: 6** cited shock scenarios.
- **commodities_split:** all rice / milled white & parboiled / aromatic-basmati /
  broken / paddy (with a paddy-route panel), mirroring beef's `commodities_split`.
- **New sources (`_sources_patch`): 9** — phlpsa, apeda, thrice, usdaERS, olam, ldc,
  itcltd, krbl, phoenix. (All other srcs reuse beef's 26: usda, comtrade, eurostat,
  oec, oecd, fao, itc, wb, etc.)

## Units & basis (important)

- All volumes are **milled-basis, thousand metric tonnes (kt)** unless a flow note
  says "paddy". USDA PSD reports rice milled; trade is calendar-year.
- The default `commodities_split` view ("rice", HS 1006) is the all-rice aggregate.

## Anchor figures verified (triangulated)

- **India #1 exporter ~17,000 kt CY2024** = ~40% of world trade (USDA FAS PSD;
  ITC/WITS value $11.6bn / 18.0 Mt gross weight). India is now also the **#1 producer
  (~147 Mt milled MY2024/25), having overtaken China (~145 Mt)** — USDA WASDE/FAS New Delhi.
- **India export ban Jul-2023 → Sep-2024** (non-basmati white rice; 20% parboiled duty;
  basmati MEP). USDA ERS: Sep23–Aug24 India exported 14.3 Mt vs 21.3 Mt prior year
  (−⅓; non-basmati white −87%). Thai 5% price +~20% during the ban, then fell back.
  This drives the `india_export_ban` scenario (severity 0.45).
- **Philippines #1 importer, record ~4.7 Mt 2024**, ~80% from Vietnam (~3.56–4.15 Mt),
  Thailand ~498–598 kt, Pakistan ~284 kt (PSA / Vietnam customs).
- **Indonesia 2024 import spike to ~4.52 Mt** (El Niño): Thailand 1,364 / Vietnam 1,248 /
  Myanmar 831 / Pakistan 804 / India ~270 kt (Indonesia customs / WITS). Flagged as a
  spike year in the balance note and ranking note — normal Indonesian imports are far lower.
- **Africa took a record ~3 Mt of Thai rice in 2024** (+23%): South Africa 833 / Senegal
  462 / Côte d'Ivoire 311 / Mozambique 287 / Benin 287 kt (Thai Rice Exporters Assoc.).
- **China imports 2024:** Thailand 433 / Myanmar 564 (top by volume) / Vietnam 281 /
  Pakistan 159 kt (China customs / WITS).
- **Saudi Arabia now India's #1 basmati market**, overtaking Iran (~1.2 Mt; APEDA).
- **Bangladesh FY24/25 imports surged ~2,600% to ~2.65 Mt**, near-all from India (Bangladesh Bank / USDA).
- **Senegal 2024 ~1.38 Mt** ($564.7m, +4.3%): Thailand 31% / India 28% / Pakistan 17.6% by value (Senegal customs / Ecofin).
- **Malaysia 2024 ~1.3 Mt:** Vietnam 39% / Pakistan 21% / India 21% / Thailand 13% / Cambodia 5% by value (WITS).
- **Olam–LDC 2024 rice combination** created a ~$40bn-revenue origination platform; Olam
  trades >40 Mt of agri commodities/yr; ITC Ltd ~9.8% of India's rice exports (Feb 2025).

## Figures I'm less certain about — flagged honestly

1. **Vietnam exports ~9,000 kt (2024).** USDA forecasts range 7,600–9,000 kt across
   releases; Vietnamese customs reported a record near 9 Mt. I used ~9,000 in the
   balance/ranking but flag the spread. Thailand ~9,900–10,000 kt is firmer.
2. **Indonesia 4,500 kt balance is a 2024 spike**, not a structural level. Noted in
   the balance and ranking; if the dataset is meant to show "normal" years, Indonesia
   should drop well down the importer list (~1–1.5 Mt typical).
3. **Benin (BEN) figures are understated by informal re-exports into Nigeria.** Official
   Comtrade shows ~1.2–1.4 Mt imported and ~0.6 Mt re-exported, but the true Nigeria
   transit volume is larger and poorly captured. Flagged in the balance note. The
   `IND→NGA` direct flow (400 kt, modeled) deliberately understates total Indian rice
   reaching Nigeria because much arrives via Benin/Togo.
4. **Cambodia (KHM) exports.** Milled-rice exports are ~700 kt (the cited, real figure),
   but USDA's exportable-surplus framing implies ~2,000 kt once cross-border paddy to
   Vietnam/Thailand is counted. I used 2,000 in the balance (PSD basis) and put the
   ~700 kt milled reality and the paddy flows in the notes / paddy route panel. This is
   the single biggest "definition" judgement call in the file.
5. **Myanmar exports ~2,400 kt** — USDA and customs diverge (2,200–2,600) and a lot is
   informal over-border to China; used 2,400.
6. **China import total (~1,700 kt).** China's imports fell sharply from 2021–22 highs;
   2024 customs sum across listed suppliers is lower (~1.4 Mt). I used ~1,700 to stay
   inside USDA PSD; the supplier flows (Myanmar 564 + Thailand 433 + Vietnam 281 +
   Pakistan 159 = ~1.44 Mt) don't fully reconcile to it — gap is unlisted small suppliers
   and broken rice. Flagged.
7. **Some West/Central African modeled flows** (IND→GIN, IND→CMR, CHN→CIV, MMR→CIV,
   PAK→KEN, VNM→GHA) are curated from OEC/WITS partner data and regional trade patterns,
   not a single clean published tonnage — correctly marked `modeled` with the basis stated.
8. **EU treated as a single node** (EU-27, ~2.2–2.4 Mt imports) per the spec's EU-bloc
   convention; Italy and Spain are the internal producers (Italy carried as its own balance).
9. **Forecast CAGRs are reasoned assumptions** inside the OECD-FAO 2024–2033 envelope
   (global rice demand ~+1%/yr; Africa the fastest-growing import region; Asian per-capita
   flat-to-declining; Japan/Korea declining). Stated openly in `forecast.method`; they
   are not published country-level numbers and can be challenged.

## Sources used

USDA FAS PSD / Grain: World Markets & Trade · USDA ERS Rice Outlook & Rice Sector at a
Glance · USDA FAS GATS · FAOSTAT · UN Comtrade / World Bank WITS · ITC Trade Map · OEC ·
OECD-FAO Agricultural Outlook · Eurostat · national customs (Philippines PSA, Indonesia,
China, Senegal, Malaysia, Bangladesh Bank) · APEDA (India basmati) · Thai Rice Exporters
Association · S&P Global Commodity Insights / Ecofin (Africa) · company disclosures
(Olam Agri, LDC, ITC Ltd, KRBL, REAP).

## Reliability caveats (for the reviewer)

- Rice bilateral data is **better documented than beef** at the importer level (importers
  publish supplier splits), which is why observed share is ~68% — that is real, not padded.
- The **weakest reliability is on transit/informal flows** (Benin→Nigeria, Cambodia paddy,
  overland Nepal/Afghanistan) — all correctly `modeled` with the basis in the note.
- **Definition risk** (milled vs paddy vs gross product weight; calendar vs marketing
  year; Cambodia surplus framing) is the main thing a reviewer should scrutinise. Every
  balance note states the year and basis used.
