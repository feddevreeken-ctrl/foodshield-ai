# FoodShield AI — Rabobank Presentation & Demo Script

_Prepared June 2026. Audience: RaboResearch Food & Agribusiness (Carlos Mera's
agri-commodities desk and adjacent risk/research teams). Goal of the meeting:
not to sell a product, but to show a credible, complementary structural-risk
layer and get expert feedback._

---

## THE ONE-LINE FRAMING (say this early, return to it at the end)

> "Your analysts go deep on one commodity or market at a time. FoodShield is the
> free, transparent, cross-country **structural-risk layer that sits underneath**
> all of that — one comparable score per country, every input traceable. It's not
> a competitor to RaboResearch; it's the context layer your price work assumes."

This positioning is the whole game. It turns every weakness (no price forecasts,
some legacy data) into an honest scope boundary, and puts your real strengths
(synthesis, breadth, provenance) front and centre.

---

## PART 1 — PRESENTATION STRUCTURE (~10–12 min, before the live demo)

**Slide 1 — Who I am / why this exists (60 sec).**
Student at Erasmus Rotterdam, not a food-security expert. Built it because the
data exists but is scattered across 33 agencies, formats and APIs, and no single
source answers: "if something breaks in one country tomorrow, who gets hurt and
how badly?" Keep this honest and short — the curiosity origin is a strength, not
a weakness, with this audience.

**Slide 2 — The problem, in their language.**
150 RaboResearch analysts produce deep but siloed work — a wheat outlook, an FX
piece, a fertiliser note. What's missing is one *comparable* structural-risk
number across 193 countries that fuses trade dependency + supplier concentration
+ climate + conflict + governance + prices. That fusion is the gap FoodShield fills.

**Slide 3 — What it is.**
One 0–100 Food Disruption Risk Score (FDRS) per country, 193 countries + 50 US
states, refreshed every 6h from 33 public pipelines, **every input traceable to
its source**. Seven weighted components (28/18/14/14/9/9/8).

**Slide 4 — The differentiator: provenance discipline.**
This is the slide that earns their trust. Every number is badged
**sourced / modeled / legacy**. The Data Status page shows 24/33 healthy — not a
fake 33/33. "Net food trade: Not sourced" is shown, not hidden. A research desk
respects honest scope far more than a polished black box. *Lead with this as the
credibility anchor.*

**Slide 5 — What's genuinely sourced & current.**
Food inflation (Eurostat, monthly), governance (World Bank WGI, all 6 dims),
observed climate warming (WB CCKP), caloric trade shares (FAOSTAT FBS), commodity
benchmark prices (World Bank Pink Sheet). These are the structured cross-country
datasets their analysts would otherwise assemble by hand.

**Slide 6 — Honest limitations (say them before they ask).**
No back-tested forecast yet (the 2030 view is an illustrative scenario, labelled
as such). Structural baseline is still partly heritage data, clearly badged.
Some live crisis feeds (WFP/IPC) are upstream-down right now — and the dashboard
degrades honestly rather than faking calm. Owning these *raises* credibility.

**Slide 7 — The ask.**
"Could a desk like yours use a structural-risk layer like this? What single data
source would sharpen one component enough to justify it? Where would this lose an
analyst's trust?" Make it a feedback conversation, not a pitch.

→ then: **"Let me show you."**

---

## PART 2 — LIVE DEMO FLOW (~6–8 min)

Open the site already loaded, Quick Start dismissed. Demo in *their* domain first.

**Step 1 — Commodities tab (open here, not the map).**
Why: it speaks their exact language. Show the per-commodity cards — live World
Bank Pink Sheet prices ($/MT, MoM change), concentration scores, ALERT flags on
soybeans (88) and palm oil (85). Point out the **"Today's signal: fertiliser up
18.1% MoM"**. Then the key line: *"notice every card is badged Observed·Comtrade
or Modeled route — you always know if a number is real bilateral trade or an
estimate."* That single distinction is what a markets desk cares about most.

**Step 2 — Trade Flow Atlas (Trade Flows tab).**
Pick a chokepoint story. Egypt is a strong one: "Egypt sources 43% of its wheat
from Russia — HHI 72, no diversified fallback in the top 3 suppliers." Show the
supplier-concentration donut + ranked list (now correctly sorted by share) and
the observed-vs-modeled provenance pill. This is your strongest single screen for
this audience — supplier concentration + chokepoint risk is their bread and butter.

**Step 3 — A country panel (Netherlands — their home turf).**
Search "Netherlands". Show: FDRS 21 "Resilient", **Sourced coverage 80%**,
"Structural 20 +1 nowcast", food inflation **2.7% Eurostat 2025-12** (sourced),
governance and observed-warming cards populated, and net food trade honestly
marked "Not sourced". The point: *this is what one fully-assembled country view
looks like, and you can trace every number.*

**Step 4 — The synthesis payoff (back to the map or Score & Ranking).**
Zoom out: the global map, the most-vulnerable ranking (S. Sudan 80, Yemen 79,
Somalia 78). "One comparable number, 243 places, same methodology — that's the
layer that's hard to assemble and easy to compare."

**Step 5 — (Optional, only if time / asked) Scenario Stress Test.**
"What if a major wheat supplier halts exports — who breaks first?" Frame as a
structural what-if, NOT a forecast.

**Close:** return to the one-line framing. "Free, transparent, structural —
underneath your price and market work. What would make it useful to you?"

---

## PART 3 — THE TWO TRICKY QUESTIONS (rehearse these answers)

**Q1: "Why is DR Congo only 48 when 25M people are in IPC crisis?"**

> "Good catch — and it's a deliberate design point, not a data gap. FDRS measures
> *structural exposure to supply disruption*. DRC grows much of its own food, so
> its structural trade-exposure is genuinely lower than, say, Yemen, which imports
> ~90% of its wheat. The *acute* current crisis is meant to come through the live
> nowcast/IPC layer on top of the structural score — and right now the WFP/IPC
> feed is upstream-down, which the dashboard flags openly rather than hiding. So
> the honest read is: structural score 48, live crisis layer currently
> unavailable and labelled as such. I'd rather show that than a fabricated number."

Why this works: it demonstrates you understand the structural-vs-acute distinction
(which *they* live by) and reinforces the provenance-honesty theme.

**Q2: "Your Data Status says 24/33 — why isn't everything live?"**

> "Because I'd rather show 24/33 honestly than a fake 33/33. Of the 9 that aren't
> green: some are genuine upstream outages today — WFP HungerMap and IPC are both
> returning server errors on their end, which I can't fix from my side. One needs
> an API key I haven't registered (ACLED). FAOSTAT is mid-migration to a new auth
> system. The key thing is the system *degrades honestly* — when a feed is empty,
> affected countries get flagged low-confidence instead of showing a confident
> score built on zeros. For a risk tool, knowing what you *don't* know is the
> whole point."

Why this works: turns the apparent weakness into the single most trust-building
thing about the product for a research audience.

**Bonus prep — likely third question: "How is this different from FAO GIEWS /
WFP HungerMap / FEWS NET?"**

> "Those are excellent domain-expert tools, each deep in its own lane. FoodShield
> doesn't try to beat any of them — it *synthesises across* all of them into one
> comparable score, with the provenance of every input visible. The value is the
> cross-source fusion and the traceability, not the depth in any single feed."

---

## QUICK PRE-DEMO CHECKLIST

- [ ] Hard-refresh the live site (Cmd+Shift+R) so you're on the latest deploy
- [ ] Dismiss Quick Start, tick "don't show again" before they're watching
- [ ] Pre-open Netherlands (`?country=NLD`) and the Commodities tab in tabs
- [ ] Confirm the supplier list shows sorted order (the fix is live)
- [ ] Have the Technical Brief PDF ready to leave with them
- [ ] Know your three numbers cold: FDRS NL 21, Egypt wheat 43% from Russia, fertiliser +18.1% MoM
- [ ] Don't lead with the 2030 forecast — show only if asked, and caveat it

---

## WHAT TO EMPHASISE vs DE-EMPHASISE (for this specific audience)

**Lead with:** Trade Flow Atlas · Commodities tab · provenance badges · the
synthesis pitch · honest limitations.

**Show if asked:** 2030 Modeled Outlook (caveat heavily — they forecast for a
living) · the structural baseline's legacy share (it's badged, don't oversell).

**Don't try to play on:** price forecasting, futures positioning — that's their
core competency and you have none. Stay in the structural/physical-risk lane.
