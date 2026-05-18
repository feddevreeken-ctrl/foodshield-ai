"""
Feeding America — Map the Meal Gap (US food insecurity by state/county).

Feeding America publishes annual MMG reports (PDF + dataset) but does not expose a public API.
The dataset is published as XLSX on https://map.feedingamerica.org

Strategy: a small lookup table of state-level food insecurity % from the latest MMG release.
This is updated manually once per year when MMG publishes (typically May).

Output: data/feeding_america_states.json
  {
    state_code: {
      "food_insecurity_pct": <% of state population>,
      "child_food_insecurity_pct": <%>,
      "year": <data year>
    }
  }

To refresh: download the latest "Map the Meal Gap" state summary CSV from
https://www.feedingamerica.org/research/map-the-meal-gap/by-county and update STATE_MMG below.
"""
from _common import write_json

# Source: Feeding America Map the Meal Gap 2024 release (data year 2022).
# State-level food insecurity rates (%). Source URL above; numbers verified May 2026.
STATE_MMG = {
    "AL": {"fi_pct": 16.9, "child_fi_pct": 22.6},
    "AK": {"fi_pct": 13.5, "child_fi_pct": 18.1},
    "AZ": {"fi_pct": 13.6, "child_fi_pct": 19.2},
    "AR": {"fi_pct": 18.9, "child_fi_pct": 24.0},
    "CA": {"fi_pct": 11.2, "child_fi_pct": 15.6},
    "CO": {"fi_pct": 11.5, "child_fi_pct": 16.2},
    "CT": {"fi_pct": 12.2, "child_fi_pct": 16.1},
    "DE": {"fi_pct": 11.9, "child_fi_pct": 15.7},
    "FL": {"fi_pct": 13.7, "child_fi_pct": 19.5},
    "GA": {"fi_pct": 15.0, "child_fi_pct": 20.4},
    "HI": {"fi_pct": 14.2, "child_fi_pct": 17.8},
    "ID": {"fi_pct": 13.1, "child_fi_pct": 16.7},
    "IL": {"fi_pct": 13.0, "child_fi_pct": 17.2},
    "IN": {"fi_pct": 14.0, "child_fi_pct": 18.0},
    "IA": {"fi_pct": 11.6, "child_fi_pct": 14.6},
    "KS": {"fi_pct": 13.1, "child_fi_pct": 16.6},
    "KY": {"fi_pct": 16.2, "child_fi_pct": 20.4},
    "LA": {"fi_pct": 19.6, "child_fi_pct": 26.0},
    "ME": {"fi_pct": 13.4, "child_fi_pct": 18.3},
    "MD": {"fi_pct": 11.0, "child_fi_pct": 14.6},
    "MA": {"fi_pct": 11.4, "child_fi_pct": 14.7},
    "MI": {"fi_pct": 13.6, "child_fi_pct": 17.4},
    "MN": {"fi_pct": 10.0, "child_fi_pct": 12.6},
    "MS": {"fi_pct": 18.7, "child_fi_pct": 24.3},
    "MO": {"fi_pct": 14.5, "child_fi_pct": 18.8},
    "MT": {"fi_pct": 11.7, "child_fi_pct": 14.6},
    "NE": {"fi_pct": 11.7, "child_fi_pct": 14.7},
    "NV": {"fi_pct": 12.5, "child_fi_pct": 18.3},
    "NH": {"fi_pct": 9.7,  "child_fi_pct": 12.4},
    "NJ": {"fi_pct": 11.4, "child_fi_pct": 15.5},
    "NM": {"fi_pct": 14.7, "child_fi_pct": 21.1},
    "NY": {"fi_pct": 12.5, "child_fi_pct": 17.4},
    "NC": {"fi_pct": 13.9, "child_fi_pct": 19.2},
    "ND": {"fi_pct": 10.7, "child_fi_pct": 13.0},
    "OH": {"fi_pct": 14.0, "child_fi_pct": 18.4},
    "OK": {"fi_pct": 16.6, "child_fi_pct": 22.1},
    "OR": {"fi_pct": 12.4, "child_fi_pct": 16.2},
    "PA": {"fi_pct": 11.6, "child_fi_pct": 15.8},
    "RI": {"fi_pct": 12.7, "child_fi_pct": 16.4},
    "SC": {"fi_pct": 13.6, "child_fi_pct": 18.5},
    "SD": {"fi_pct": 11.5, "child_fi_pct": 14.7},
    "TN": {"fi_pct": 14.4, "child_fi_pct": 19.0},
    "TX": {"fi_pct": 14.3, "child_fi_pct": 20.5},
    "UT": {"fi_pct": 11.3, "child_fi_pct": 14.6},
    "VT": {"fi_pct": 13.0, "child_fi_pct": 16.6},
    "VA": {"fi_pct": 10.7, "child_fi_pct": 14.0},
    "WA": {"fi_pct": 11.4, "child_fi_pct": 14.9},
    "WV": {"fi_pct": 15.3, "child_fi_pct": 19.3},
    "WI": {"fi_pct": 10.8, "child_fi_pct": 13.4},
    "WY": {"fi_pct": 11.0, "child_fi_pct": 13.6},
}


def main():
    out = {}
    for state, vals in STATE_MMG.items():
        out["US-" + state] = {
            "food_insecurity_pct": vals["fi_pct"],
            "child_food_insecurity_pct": vals["child_fi_pct"],
            "year": 2022,
        }
    write_json(
        "feeding_america_states.json",
        out,
        source="Feeding America — Map the Meal Gap 2024 (data year 2022)",
        notes="Updated annually when MMG publishes (typically May). Override values manually here.",
    )


if __name__ == "__main__":
    main()
