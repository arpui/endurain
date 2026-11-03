"""Tests for the activity-streams serializer's read-time power calibration.

Stored power waypoints are raw; a stream tagged ``stryd`` must come out scaled
onto the athlete's current scale, while ``device`` and untagged rows pass
through untouched. Non-power streams never scale, and the stored lists are
never mutated.
"""

from types import SimpleNamespace

import pytest

import modules.activities.activity_streams.constants as stream_constants
import modules.activities.activity_streams.serializers as serializers


def _stream(
    stream_type: int,
    waypoints: list[dict],
    power_source: str | None,
) -> SimpleNamespace:
    """Return an ORM-shaped stream row."""
    return SimpleNamespace(
        id=3,
        activity_id=7,
        stream_type=stream_type,
        stream_waypoints=waypoints,
        power_source=power_source,
        strava_activity_stream_id=None,
    )


class TestPowerCalibration:
    def test_stryd_power_is_scaled_at_read(self):
        waypoints = [{"time": "2026-01-01T00:00:00", "power": 250.0}, {"time": "2026-01-01T00:00:01", "power": 0.0}]

        read = serializers.to_read_schema(_stream(stream_constants.STREAM_TYPE_POWER, waypoints, "stryd"))

        assert read.power_source == "stryd"
        assert read.stream_waypoints[0]["power"] == pytest.approx(250.0 * stream_constants.STRYD_POWER_SCALE, abs=0.1)
        assert read.stream_waypoints[1]["power"] == pytest.approx(0.0)

    def test_device_and_untagged_power_pass_through(self):
        for source in ("device", None):
            read = serializers.to_read_schema(
                _stream(stream_constants.STREAM_TYPE_POWER, [{"time": "t", "power": 250.0}], source)
            )
            assert read.stream_waypoints[0]["power"] == 250.0

    def test_stored_waypoints_are_never_mutated(self):
        waypoints = [{"time": "t", "power": 250.0}]

        serializers.to_read_schema(_stream(stream_constants.STREAM_TYPE_POWER, waypoints, "stryd"))

        assert waypoints[0]["power"] == 250.0

    def test_non_power_streams_are_not_scaled_even_if_mislabeled(self):
        read = serializers.to_read_schema(
            _stream(stream_constants.STREAM_TYPE_SPEED, [{"time": "t", "power": 250.0}], "stryd")
        )

        assert read.stream_waypoints[0]["power"] == 250.0
