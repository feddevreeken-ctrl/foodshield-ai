"""Regression test for the OpenAQ v3 two-phase join (v43).

WHY THIS EXISTS:
  refresh_openaq.py shipped an empty payload for months because it read
  `sensors[].latest.value` off /v3/locations, where the SensorBase schema has
  no `latest` key at all. Nothing raised — the feed just reported "0 countries
  from 5000 stations" and that was read as a missing API key.

  The v43 fix pulls values from /v3/parameters/2/latest and joins them back to
  countries on locationsId. That join cannot be exercised locally without an
  API key, so this test drives it with recorded-shape fixtures instead. The
  fixtures mirror the schemas in api.openaq.org/openapi.json (version 3.0.0).

  The point is narrow and deliberate: if someone "simplifies" this back to
  reading latest off the locations response, this test fails.

Run: python3 tests/test_refresh_openaq.py
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import refresh_openaq  # noqa: E402


def _fresh(hours_ago=1):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


# Phase A shape: Location. sensors[] entries are SensorBase — {id, name,
# parameter} — with NO `latest`. That absence is the whole point.
LOCATIONS = [
    {"id": 100, "name": "Cairo", "country": {"id": 1, "code": "EG", "name": "Egypt"},
     "sensors": [{"id": 900, "name": "pm25", "parameter": {"id": 2, "name": "pm25"}}]},
    {"id": 101, "name": "Giza", "country": {"id": 1, "code": "EG", "name": "Egypt"},
     "sensors": [{"id": 901, "name": "pm25", "parameter": {"id": 2, "name": "pm25"}}]},
    {"id": 102, "name": "Amsterdam", "country": {"id": 2, "code": "NL", "name": "Netherlands"},
     "sensors": [{"id": 902, "name": "pm25", "parameter": {"id": 2, "name": "pm25"}}]},
    # No country code — must be skipped, not crash.
    {"id": 103, "name": "Orphan", "country": {}, "sensors": []},
]

LATEST = [
    {"datetime": {"utc": _fresh()}, "value": 40.0, "sensorsId": 900, "locationsId": 100},
    {"datetime": {"utc": _fresh()}, "value": 60.0, "sensorsId": 901, "locationsId": 101},
    # Outlier beside a fire: median must resist it, mean would not.
    {"datetime": {"utc": _fresh()}, "value": 900.0, "sensorsId": 901, "locationsId": 101},
    {"datetime": {"utc": _fresh()}, "value": 8.0, "sensorsId": 902, "locationsId": 102},
    # Location that appeared between phases — dropped, not fatal.
    {"datetime": {"utc": _fresh()}, "value": 12.0, "sensorsId": 999, "locationsId": 55555},
    # Stale reading — excluded by the freshness cutoff.
    {"datetime": {"utc": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()},
     "value": 500.0, "sensorsId": 900, "locationsId": 100},
    # Malformed value — skipped.
    {"datetime": {"utc": _fresh()}, "value": None, "sensorsId": 900, "locationsId": 100},
]


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def fake_http_get(url, params=None, headers=None, **kwargs):
    """Serve one full page then an empty page, per endpoint."""
    page = (params or {}).get("page", 1)
    if "locations" in url:
        return FakeResponse({"results": LOCATIONS if page == 1 else []})
    if "latest" in url:
        return FakeResponse({"results": LATEST if page == 1 else []})
    raise AssertionError(f"unexpected URL requested: {url}")


class TestOpenAQJoin(unittest.TestCase):
    def setUp(self):
        self.written = {}

        def capture(filename, payload, **kw):
            self.written = {"filename": filename, "payload": payload, "meta": kw}

        self.capture = capture

    def _run(self):
        with mock.patch.object(refresh_openaq, "http_get", fake_http_get), \
             mock.patch.object(refresh_openaq, "write_json", self.capture), \
             mock.patch.object(refresh_openaq, "env", lambda *a, **k: "test-key"):
            refresh_openaq.main()
        return self.written["payload"]

    def test_countries_are_populated(self):
        """The regression itself: this used to be {}."""
        out = self._run()
        self.assertTrue(out, "payload is empty — the locationsId join is broken again")
        self.assertIn("EGY", out)
        self.assertIn("NLD", out)

    def test_median_resists_the_outlier(self):
        """EGY has 40, 60, 900. Median is 60; the mean would be 333."""
        out = self._run()
        self.assertEqual(out["EGY"]["pm25_latest"], 60.0)
        self.assertEqual(out["EGY"]["stations_reporting"], 3)

    def test_flag_threshold(self):
        out = self._run()
        self.assertTrue(out["EGY"]["pm25_flag"])    # 60 > 35
        self.assertFalse(out["NLD"]["pm25_flag"])   # 8 < 35

    def test_stale_and_unmatched_are_dropped(self):
        """The 400-day-old 500 µg/m³ reading and the orphan location must not land."""
        out = self._run()
        self.assertEqual(out["NLD"]["stations_reporting"], 1)
        # locationsId 55555 belonged to no country and must not invent one.
        self.assertEqual(set(out.keys()), {"EGY", "NLD"})

    def test_partial_fetch_is_not_reported_as_ok(self):
        """A pagination failure must degrade the status, not pass as complete.

        The original _paginate returned silently mid-pagination, so a run that
        died on page 3 produced a payload the caller described with confident
        counts. That is indistinguishable from a healthy run to every consumer
        downstream, which is precisely the failure this project exists not to
        make.
        """
        def flaky_http_get(url, params=None, headers=None, **kwargs):
            page = (params or {}).get("page", 1)
            if "locations" in url:
                # full first page -> caller asks for page 2 -> that one dies
                if page == 1:
                    return FakeResponse({"results": LOCATIONS * 250})   # >= PAGE_LIMIT
                raise RuntimeError("upstream 503")
            if "latest" in url:
                return FakeResponse({"results": LATEST if page == 1 else []})
            raise AssertionError(url)

        with mock.patch.object(refresh_openaq, "http_get", flaky_http_get), \
             mock.patch.object(refresh_openaq, "write_json", self.capture), \
             mock.patch.object(refresh_openaq, "env", lambda *a, **k: "test-key"):
            refresh_openaq.main()

        meta = self.written["meta"]
        self.assertEqual(meta.get("status"), "degraded_partial",
                         "a truncated fetch must not be status=ok")
        self.assertIn("PARTIAL FETCH", meta.get("notes", ""),
                      "the payload must say the country list is incomplete")

    def test_missing_key_writes_honest_stub(self):
        with mock.patch.object(refresh_openaq, "write_json", self.capture), \
             mock.patch.object(refresh_openaq, "env", lambda *a, **k: None):
            refresh_openaq.main()
        self.assertEqual(self.written["payload"], {})
        self.assertEqual(self.written["meta"].get("status"), "empty")


if __name__ == "__main__":
    unittest.main(verbosity=2)
