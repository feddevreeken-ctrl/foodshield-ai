# START HERE — FoodShield AI project orientation

_If you're a new chat picking up this project, read this first. It's the map to
everything else._

---

## What FoodShield AI is (in 30 seconds)

A free, public food-security dashboard built solo by **Fedde Vreeken** (International
Economics student, Erasmus University Rotterdam). It wires **33 public data
pipelines** (FAO, World Bank, WFP, USDA, ACLED, INFORM, CCKP, Comtrade, etc.) into
**one 0–100 Food Disruption Risk Score (FDRS) per country**, covering 193 countries
+ 50 US states, refreshed every 6 hours. Every input is traceable to its source and
badged sourced / modeled / legacy. The pitch: it's the **structural-risk layer
beneath market/commodity research** — synthesis + transparency, not forecasting.

- **Live:** https://foodshield-ai-fv.vercel.app  (Vercel auto-deploys on push to `main`)
- **Repo:** https://github.com/feddevreeken-ctrl/foodshield-ai
- **Project folder:** `/Users/fedde/Documents/Claudes Files/Projects/FoodSecurity AI`
- **Source of truth:** `index.html` (~25k lines, single-file app). Keep
  `foodshield-v21.html` in sync (copy one to the other after every change).

---

## The ground rules that govern HOW work happens here

1. **Assistant sandbox has no internet** and **can't reliably write to `scripts/`
   or run `git push`.** So the assistant writes & verifies code logic offline;
   **Fedde runs live data fetches, file placement, and all git on his Mac.**
2. A **data-refresh bot** commits `data/*.json` every 6h. Always `git pull --rebase`
   before pushing. On a `data/*.json` conflict in a rebase, keep your version with
   `git checkout --theirs data/<file>` (in a rebase, "theirs" = your commit).
3. After any data push, **verify it's not empty**:
   `git show HEAD:data/inform_risk.json | python3 -c "import sys,json;print(len(json.load(sys.stdin)['data']))"`
   (should be ~191). We once shipped blank files via the ours/theirs trap.
4. Clear stale lock if git hangs: `rm -f .git/index.lock`.
5. JS-syntax-check `index.html` before every push (inline <script> blocks).

---

## Which doc to read for what

| You need… | Read |
|-----------|------|
| Full record of the latest session (June 2026) — every change, current state, what's pending | **`HANDOFF_SESSION_JUNE2026.md`** |
| The ordered backlog / what to fix next (from a verified external audit) | **`REMEDIATION_PLAN.md`** |
| How to present it to Rabobank — slide structure, demo flow, tricky-Q answers | **`DEMO_SCRIPT.md`** |
| The leave-behind technical brief (methodology, data inventory, limitations) | **`FoodShield_AI_Technical_Brief.docx`** |
| The presentation deck (15 slides) | **`FoodShield_Rabobank_Deck.pptx`** |
| Original/older project handoff (note: its path reference is stale — use the one above) | `HANDOFF.md` |
| Data-source roadmap, setup, conversation log | `DATA_SOURCES_ROADMAP.md`, `SETUP.md`, `CONVERSATION_LOG.md` |

---

## Current status (June 2026)

- **Demo-ready.** Lead surfaces (Commodities, Trade Flow Atlas) are polished and
  data-clean; Netherlands (the Rabobank home-country) verified accurate.
- **4 pipelines revived** this session (INFORM, Aqueduct, CCKP, WGI via WB Data360).
  Source health now **24/33** healthy.
- **Honest degradation** built in: when crisis feeds (WFP/IPC) are upstream-down,
  affected countries are flagged low-confidence rather than shown as falsely calm.
- **Still genuinely down (upstream, not our bug):** WFP HungerMap, IPC, FAOSTAT,
  ND-GAIN, net_food_trade; ACLED needs an API key.

## What's NOT done yet (the open thread)

Fedde's most recent requests for the **deck** are unbuilt:
1. Embed real **screenshots** from the live site (Trade Atlas / Egypt donut,
   Commodities cards, NL panel, global map). Chrome MCP works on the vercel domain.
2. **Deepen** the FDRS + Trade Atlas slides (more explanation/depth).
3. Add a **data-sources showcase** slide ("what exists, where the data comes from").
4. **Multi-AI** slide — Fedde used several AIs for different jobs; needs his
   specifics (which model for coding vs review vs writing).
5. **Live disturbances / news** slide — the real-time event feed (GDACS, ReliefWeb,
   FEWS NET) that overlays structural scores and feeds the nowcast.
6. Reference / screenshot **WFP HungerMap** (hungermap.wfp.org/food?w=ipc-phase-3)
   as a data-source example.

→ To resume the deck work, first get Fedde's answers on #4 (which AIs did what),
#5 (show the feed vs how it feeds the nowcast), and #6 (screenshot HungerMap or
just reference it), then build.

## Golden rules of this project (the throughline of every decision)

- **Honesty over polish.** Badge everything; show 24/33 not a fake 33/33; never
  fabricate a number to look complete. This is the credibility differentiator.
- **Don't hand-edit country data to "look better"** — fix at the source/render layer.
- **Keep it free, transparent, structural** — it complements price/forecast desks,
  it doesn't compete with them.
