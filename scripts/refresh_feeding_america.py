"""
Feeding America — Map the Meal Gap (US food insecurity by state/county).

Feeding America publishes annual MMG reports (PDF + dataset) but does not expose a public API.
The dataset is published as XLSX on https://map.feedingamerica.org

Strategy: a small lookup table of state-level food insecurity % from the latest MMG release.
This is updated manually once per year when MMG publishes (typically May).

Source: Map the Meal Gap 2025 release (data year 2023), published May 14, 2025.
  Landing page: https://www.feedingamerica.org/research/map-the-meal-gap/overall-executive-summary
  State summary: https://www.feedingamerica.org/research/map-the-meal-gap

Next scheduled release: late July 2026 (will contain data year 2024 — update STATE_MMG then).

Output: data/feeding_america_states.json
  {
    state_code: {
      "food_insecurity_pct": <% of state population>,
      "child_food_insecurity_pct": <%>,
      "year": <data year>
    }
  }
"""
from _common import write_json

# Source: Feeding America Map the Meal Gap 2025 release (data year 2023).
# State-level food insecurity rates (%). Released May 14, 2025.
# These are the headline state totals from the MMG 2025 state summary tables.
# Verified May 2026 against the official Feeding America executive summary.
STATE_MMG = {
    "AL": {"fi_pct": 18.6, "child_fi_pct": 24.7},
    "AK": {"fi_pct": 14.8, "child_fi_pct": 19.4},
    "AZ": {"fi_pct": 14.9, "child_fi_pct": 20.5},
    "AR": {"fi_pct": 20.4, "child_fi_pct": 25.6},
    "CA": {"fi_pct": 12.7, "child_fi_pct": 17.2},
    "CO": {"fi_pct": 13.1, "child_fi_pct": 17.8},
    "CT": {"fi_pct": 13.6, "child_fi_pct": 17.6},
    "DE": {"fi_pct": 13.3, "child_fi_pct": 17.3},
    "FL": {"fi_pct": 15.1, "child_fi_pct": 21.1},
    "GA": {"fi_pct": 16.5, "child_fi_pct": 22.0},
    "HI": {"fi_pct": 15.7, "child_fi_pct": 19.5},
    "ID": {"fi_pct": 14.4, "child_fi_pct": 18.0},
    "IL": {"fi_pct": 14.5, "child_fi_pct": 18.8},
    "IN": {"fi_pct": 15.5, "child_fi_pct": 19.5},
    "IA": {"fi_pct": 13.0, "child_fi_pct": 16.0},
    "KS": {"fi_pct": 14.5, "child_fi_pct": 18.0},
    "KY": {"fi_pct": 17.6, "child_fi_pct": 21.7},
    "LA": {"fi_pct": 21.1, "child_fi_pct": 27.4},
    "ME": {"fi_pct": 14.8, "child_fi_pct": 19.7},
    "MD": {"fi_pct": 12.4, "child_fi_pct": 16.0},
    "MA": {"fi_pct": 12.8, "child_fi_pct": 16.1},
    "MI": {"fi_pct": 15.0, "child_fi_pct": 18.8},
    "MN": {"fi_pct": 11.4, "child_fi_pct": 14.0},
    "MS": {"fi_pct": 20.3, "child_fi_pct": 25.9},
    "MO": {"fi_pct": 16.0, "child_fi_pct": 20.3},
    "MT": {"fi_pct": 13.1, "child_fi_pct": 16.0},
    "NE": {"fi_pct": 13.1, "child_fi_pct": 16.1},
    "NV": {"fi_pct": 13.9, "child_fi_pct": 19.7},
    "NH": {"fi_pct": 11.1, "child_fi_pct": 13.7},
    "NJ": {"fi_pct": 12.8, "child_fi_pct": 16.9},
    "NM": {"fi_pct": 16.2, "child_fi_pct": 22.6},
    "NY": {"fi_pct": 13.9, "child_fi_pct": 18.8},
    "NC": {"fi_pct": 15.4, "child_fi_pct": 20.6},
    "ND": {"fi_pct": 12.1, "child_fi_pct": 14.4},
    "OH": {"fi_pct": 15.5, "child_fi_pct": 19.8},
    "OK": {"fi_pct": 18.0, "child_fi_pct": 23.5},
    "OR": {"fi_pct": 13.9, "child_fi_pct": 17.7},
    "PA": {"fi_pct": 13.0, "child_fi_pct": 17.2},
    "RI": {"fi_pct": 14.1, "child_fi_pct": 17.8},
    "SC": {"fi_pct": 15.1, "child_fi_pct": 19.9},
    "SD": {"fi_pct": 12.9, "child_fi_pct": 16.1},
    "TN": {"fi_pct": 15.9, "child_fi_pct": 20.4},
    "TX": {"fi_pct": 15.7, "child_fi_pct": 21.9},
    "UT": {"fi_pct": 12.7, "child_fi_pct": 16.0},
    "VT": {"fi_pct": 14.4, "child_fi_pct": 18.0},
    "VA": {"fi_pct": 12.1, "child_fi_pct": 15.4},
    "WA": {"fi_pct": 12.8, "child_fi_pct": 16.3},
    "WV": {"fi_pct": 16.7, "child_fi_pct": 20.7},
    "WI": {"fi_pct": 12.2, "child_fi_pct": 14.8},
    "WY": {"fi_pct": 12.4, "child_fi_pct": 15.0},
}


def main():
    out = {}
    for state, vals in STATE_MMG.items():
        out["US-" + state] = {
            "food_insecurity_pct": vals["fi_pct"],
            "child_food_insecurity_pct": vals["child_fi_pct"],
            "year": 2023,
        }
    write_json(
        "feeding_america_states.json",
        out,
        source="Feeding America — Map the Meal Gap 2025 release (data year 2023, published May 14, 2025)",
        notes=(
            "Next release scheduled late July 2026 (will contain data year 2024). "
            "Source: https://www.feedingamerica.org/research/map-the-meal-gap/overall-executive-summary. "
            "Provenance class: MANUAL (hand-maintained from annual public release)."
        ),
    )


if __name__ == "__main__":
    main()
