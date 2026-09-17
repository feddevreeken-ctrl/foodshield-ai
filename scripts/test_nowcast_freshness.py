"""Signal freshness boundaries and eligibility using a frozen observation clock."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
import build_nowcast as n

class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 17, tzinfo=timezone.utc)

class FreshnessTest(unittest.TestCase):
    def test_boundaries(self):
        with patch.object(n, "datetime", Clock):
            for date, expected in [("2026-08-18", 1), ("2026-07-19", .5), ("2026-06-19", 0), (None, 0), ("bad", 0)]:
                self.assertEqual(n._freshness_weight(date), expected)
            self.assertEqual(n._freshness_weight(None, outer_bound=.2), .2)
            self.assertEqual(n._freshness_weight("2026-09-10", 7, 30), 1)
            self.assertEqual(n._freshness_weight("2026-08-18", 7, 30), 0)
            self.assertAlmostEqual(n._freshness_weight("2026-08-30", 7, 30), 12/23)
            self.assertEqual(n._period_end("Jun–Sep 2026"), "2026-09-30")
            self.assertEqual(n._period_end("Aug 2025 - Oct 2025 (Current)"), "2025-10-31")
            self.assertEqual(n._period_end("2026-02-01 - 2026-02-28"), "2026-02-28")
            self.assertEqual(n._period_weight("Apr-Apr 2023"), 0)
            self.assertEqual(n._period_weight("Jun-Sep 2026"), 1)

    def test_builder_eligibility(self):
        # The same expired IPC must cease to score, confer confidence or block FEWS.
        feeds={"ipc.json":{"OLD":{"phase3plus_pct":50,"analysis_date":"2026-01-01","period":"Jan-Mar 2026"},
                           "VALID":{"phase3plus_pct":50,"analysis_date":"2026-01-01","period":"Jun-Sep 2026"},
                           "OUTER":{"phase3plus_pct":50,"analysis_date":"2023-01-01","period":"Jun-Sep 2026"}},
               "fews.json":{"OLD":{"current_phase":4,"current_period":"Jun-Sep 2026"},"STALE":{"current_phase":4,"current_period":"Apr-Apr 2023"}},
               "wfp_hungermap.json":{"UNDATED":{"fcs_pct":80,"as_of":"2026-09-17"},"DATED":{"fcs_pct":80,"observation_date":"2026-07-19"}},
               "openmeteo.json":{"UNDATED":{"heat_flag":True,"as_of":"2026-09-17"}},
               "hapi_conflict.json":{"STALE":{"is_live":True,"intensity_score":100,"window_end":"2026-06-19"}}}
        isos={iso for f in feeds.values() for iso in f}
        with tempfile.TemporaryDirectory() as tmp, patch.object(n,"datetime",Clock), patch.object(n,"DATA",Path(tmp)), patch.object(n,"load",lambda name:{"data":feeds.get(name,{})}):
            Path(tmp,"countries.json").write_text(json.dumps({"data":{"countries":dict.fromkeys(isos,{})}}))
            n.main();rows=json.loads(Path(tmp,"nowcast.json").read_text())["data"]
        self.assertEqual(rows["OLD"]["components"]["ipc_pressure"],0)
        self.assertEqual(rows["OLD"]["components"]["fews_kick"],4)
        self.assertEqual(rows["OLD"]["confidence"],"high")
        self.assertEqual(rows["VALID"]["components"]["ipc_pressure"],6)
        for iso in ("OUTER","STALE","UNDATED"):
            self.assertEqual(rows[iso]["adjustment"],0)
            self.assertEqual(rows[iso]["confidence"],"none")
        self.assertEqual(rows["DATED"]["components"]["wfp_pressure"],3)
        self.assertEqual(rows["DATED"]["confidence"],"high")
        self.assertIn("collection date",rows["UNDATED"]["freshness"]["weather_kick"]["basis"])

if __name__ == '__main__':
    unittest.main()
