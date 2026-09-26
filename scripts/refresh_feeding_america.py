"""
Feeding America — Map the Meal Gap (US food insecurity by state/county).

Feeding America publishes annual MMG reports (PDF + dataset) but does not expose a public API.
The dataset is published as XLSX on https://map.feedingamerica.org

Strategy: a small lookup table of state-level food insecurity % from the latest MMG release.
This is updated manually once per year when MMG publishes (typically May).

Source: Map the Meal Gap 2026 release (data year 2024), published July 28, 2026.
  Press release: https://www.feedingamerica.org/about-us/press-room/Map-the-Meal-Gap-2026
  State pages:   https://map.feedingamerica.org/county/2024/overall/<state>

Note: USDA has stopped collecting food security data; Feeding America says it is
exploring alternative sources for 2027+. Next edition timing is uncertain.

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

# Source: Feeding America Map the Meal Gap 2026 release (data year 2024).
# State-level food insecurity rates (%). Released July 28, 2026.
# Read 2026-09-26 from the official state pages
#   https://map.feedingamerica.org/county/2024/overall/<state>
#   https://map.feedingamerica.org/county/2024/child/<state>
# (embedded selected.insecurity values, x100, 1 dp).
STATE_MMG = {
    "AL": {"fi_pct": 17.8, "child_fi_pct": 24.4},
    "AK": {"fi_pct": 15.6, "child_fi_pct": 20.0},
    "AZ": {"fi_pct": 14.9, "child_fi_pct": 20.6},
    "AR": {"fi_pct": 20.7, "child_fi_pct": 27.0},
    "CA": {"fi_pct": 15.0, "child_fi_pct": 20.0},
    "CO": {"fi_pct": 13.3, "child_fi_pct": 18.1},
    "CT": {"fi_pct": 15.3, "child_fi_pct": 18.8},
    "DE": {"fi_pct": 15.0, "child_fi_pct": 20.7},
    "FL": {"fi_pct": 15.4, "child_fi_pct": 20.9},
    "GA": {"fi_pct": 15.6, "child_fi_pct": 21.7},
    "HI": {"fi_pct": 13.7, "child_fi_pct": 21.5},
    "ID": {"fi_pct": 14.2, "child_fi_pct": 18.1},
    "IL": {"fi_pct": 13.7, "child_fi_pct": 18.0},
    "IN": {"fi_pct": 15.8, "child_fi_pct": 20.7},
    "IA": {"fi_pct": 12.4, "child_fi_pct": 17.8},
    "KS": {"fi_pct": 15.8, "child_fi_pct": 20.9},
    "KY": {"fi_pct": 18.2, "child_fi_pct": 25.3},
    "LA": {"fi_pct": 19.3, "child_fi_pct": 25.7},
    "ME": {"fi_pct": 14.5, "child_fi_pct": 20.7},
    "MD": {"fi_pct": 14.6, "child_fi_pct": 19.7},
    "MA": {"fi_pct": 12.4, "child_fi_pct": 16.9},
    "MI": {"fi_pct": 15.1, "child_fi_pct": 19.7},
    "MN": {"fi_pct": 11.7, "child_fi_pct": 16.1},
    "MS": {"fi_pct": 20.1, "child_fi_pct": 26.3},
    "MO": {"fi_pct": 16.0, "child_fi_pct": 20.6},
    "MT": {"fi_pct": 13.8, "child_fi_pct": 18.3},
    "NE": {"fi_pct": 15.8, "child_fi_pct": 21.4},
    "NV": {"fi_pct": 16.6, "child_fi_pct": 21.6},
    "NH": {"fi_pct": 11.2, "child_fi_pct": 15.5},
    "NJ": {"fi_pct": 13.0, "child_fi_pct": 16.1},
    "NM": {"fi_pct": 18.0, "child_fi_pct": 24.2},
    "NY": {"fi_pct": 15.2, "child_fi_pct": 21.1},
    "NC": {"fi_pct": 15.5, "child_fi_pct": 21.5},
    "ND": {"fi_pct": 11.4, "child_fi_pct": 16.9},
    "OH": {"fi_pct": 15.3, "child_fi_pct": 20.8},
    "OK": {"fi_pct": 20.2, "child_fi_pct": 27.3},
    "OR": {"fi_pct": 15.5, "child_fi_pct": 19.4},
    "PA": {"fi_pct": 13.5, "child_fi_pct": 19.3},
    "RI": {"fi_pct": 13.9, "child_fi_pct": 18.4},
    "SC": {"fi_pct": 15.8, "child_fi_pct": 20.6},
    "SD": {"fi_pct": 14.1, "child_fi_pct": 19.1},
    "TN": {"fi_pct": 16.3, "child_fi_pct": 22.0},
    "TX": {"fi_pct": 19.4, "child_fi_pct": 26.1},
    "UT": {"fi_pct": 15.2, "child_fi_pct": 19.1},
    "VT": {"fi_pct": 12.9, "child_fi_pct": 17.3},
    "VA": {"fi_pct": 12.7, "child_fi_pct": 16.1},
    "WA": {"fi_pct": 14.3, "child_fi_pct": 19.7},
    "WV": {"fi_pct": 17.7, "child_fi_pct": 24.4},
    "WI": {"fi_pct": 13.5, "child_fi_pct": 20.2},
    "WY": {"fi_pct": 16.1, "child_fi_pct": 20.6},
}


def main():
    out = {}
    for state, vals in STATE_MMG.items():
        out["US-" + state] = {
            "food_insecurity_pct": vals["fi_pct"],
            "child_food_insecurity_pct": vals["child_fi_pct"],
            "year": 2024,
        }
    write_json(
        "feeding_america_states.json",
        out,
        source="Feeding America — Map the Meal Gap 2026 release (data year 2024, published July 28, 2026)",
        notes=(
            "Values read from official state pages at https://map.feedingamerica.org/county/2024/overall/<state> "
            "(press release: https://www.feedingamerica.org/about-us/press-room/Map-the-Meal-Gap-2026). "
            "USDA has stopped food-security data collection; next edition timing uncertain. "
            "Provenance class: MANUAL (hand-maintained from annual public release)."
        ),
    )


if __name__ == "__main__":
    main()
