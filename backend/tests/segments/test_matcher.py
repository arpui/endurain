"""Tests for the geometric matcher, run on synthetic traces.

The geometry mirrors the Fase-1 POC's shape check: a straight east-west segment
at 41 deg N, walked at a plausible speed. Everything the matcher decides
(direction, partial coverage, U-turns, time gaps, implausible speed, the
bounding-box pre-filter) is exercised here without any database.
"""

import math
from datetime import datetime, timedelta

import pytest

import modules.segments.constants as segments_constants
from modules.segments.matcher import elevation_gain_m, match_segment_passes, polyline_length_m

_SEG_LAT = 41.0
_SEG_START_LNG = 2.0
_LNG_STEP = 0.0005  # ~42 m at this latitude
_N_POINTS = 21  # segment point indices 0..20
_STEP_M = _LNG_STEP * 111_320.0 * math.cos(math.radians(_SEG_LAT))
_T0 = datetime(2026, 5, 1, 8, 0, 0)


def _geometry() -> list[list[float]]:
    """Return the test segment polyline (west to east)."""
    return [[_SEG_LAT, _SEG_START_LNG + i * _LNG_STEP] for i in range(_N_POINTS)]


def _length_m() -> float:
    """Return the test segment's length along its geometry."""
    return polyline_length_m(_geometry())


def _trace(
    start_index: float,
    end_index: float,
    *,
    speed_mps: float = 5.0,
    sample_m: float = 10.0,
    t0: datetime = _T0,
) -> list[dict]:
    """Return map-stream waypoints walking the segment line from one index to another.

    Rising indices mean a west-to-east (forward) walk; reversing the bounds
    makes a reverse traverse. Traces start and end slightly outside the segment
    so the matcher sees the approach and the exit, like a real activity does.
    """
    direction = 1.0 if end_index >= start_index else -1.0
    step = sample_m / _STEP_M
    waypoints: list[dict] = []
    index, t = start_index, t0
    while True:
        lat, lng = _point_at(index)
        waypoints.append({"lat": lat, "lon": lng, "time": t.isoformat()})
        if (index - end_index) * direction >= 0:
            break
        nxt = index + step * direction
        if (nxt - end_index) * direction > 0:
            nxt = end_index
        t += timedelta(seconds=(nxt - index) * direction * _STEP_M / speed_mps)
        index = nxt
    return waypoints


def _point_at(seg_index: float) -> tuple[float, float]:
    """Return the coordinate at a (possibly fractional, possibly out of range) point index."""
    return _SEG_LAT, _SEG_START_LNG + seg_index * _LNG_STEP


def _last_time(waypoints: list[dict]) -> datetime:
    """Return the timestamp of a trace's last waypoint."""
    return datetime.fromisoformat(waypoints[-1]["time"])


class TestStraightTraverse:
    def test_forward_traverse_yields_one_forward_effort(self):
        waypoints = _trace(-2.0, float(_N_POINTS + 1))

        matches = match_segment_passes(7, _geometry(), _length_m(), waypoints)

        assert len(matches) == 1
        match = matches[0]
        assert match.segment_id == 7
        assert match.direction is segments_constants.SegmentDirection.FORWARD
        assert match.pass_number == 1
        assert match.coverage >= 0.9
        # The effort brackets segment point 0 to point 20 (~840 m) at 5 m/s.
        expected = _length_m() / 5.0
        assert expected * 0.8 < match.elapsed_seconds < expected * 1.2
        assert match.stream_index_start < match.stream_index_end
        assert match.time_start < match.time_end

    def test_reverse_traverse_yields_one_reverse_effort_with_plausible_time(self):
        waypoints = _trace(float(_N_POINTS + 1), -2.0)

        matches = match_segment_passes(7, _geometry(), _length_m(), waypoints)

        assert len(matches) == 1
        match = matches[0]
        assert match.direction is segments_constants.SegmentDirection.REVERSE
        # The bracket must run from the geometry's far end to its start; a
        # forward-style bracket would measure a fraction of the traverse and
        # trip the plausibility filter.
        expected = _length_m() / 5.0
        assert expected * 0.8 < match.elapsed_seconds < expected * 1.2


class TestBracketingAnchorsOnTheSegmentEndpoints:
    def test_the_approach_and_the_run_past_the_finish_are_not_counted(self):
        """A trace that enters early and keeps going still measures the segment.

        The candidate corridor is 30 m wide, so bracketing on its edges adds a few
        tens of metres at both ends — around 10 s per end at running speed. The
        bracket anchors on the geometry's endpoints instead, so the elapsed stays
        within a stride of the segment itself.
        """
        speed = 2.5
        waypoints = _trace(-3.0, float(_N_POINTS + 3), speed_mps=speed, sample_m=5.0)

        (match,) = match_segment_passes(7, _geometry(), _length_m(), waypoints)

        expected = _length_m() / speed
        assert abs(match.elapsed_seconds - expected) <= 8.0

    def test_a_slow_finish_still_counts_its_last_metres(self):
        """The clock runs to the far end the trace reached, not to 95 % of the geometry.

        A climb tops out slowly: the last segment metres take far longer than the
        fast approach. Bracketing at a fixed fraction of the polyline would stop
        the effort at point 19 of 20 and report a time shorter than the traverse
        that actually happened.
        """
        fast = _trace(-2.0, 19.0, speed_mps=5.0)
        slow = _trace(19.0, float(_N_POINTS + 3), speed_mps=0.5, t0=_last_time(fast))
        waypoints = fast + slow[1:]

        (match,) = match_segment_passes(7, _geometry(), _length_m(), waypoints)

        assert match.elapsed_seconds > _length_m() / 5.0 + 60.0

    def test_a_pass_that_never_anchors_on_an_endpoint_keeps_its_extremes(self):
        """GPS that stays off the endpoints must still produce the traverse."""
        offset = 15.0 / 111_320.0  # ~15 m aside: inside the corridor, outside the anchor
        waypoints = [
            {"lat": point["lat"] + offset, "lon": point["lon"], "time": point["time"]}
            for point in _trace(-1.0, float(_N_POINTS + 1), speed_mps=5.0)
        ]

        (match,) = match_segment_passes(7, _geometry(), _length_m(), waypoints)

        assert match.direction is segments_constants.SegmentDirection.FORWARD
        expected = _length_m() / 5.0
        assert expected * 0.8 < match.elapsed_seconds < expected * 1.3


class TestRejectedTraverses:
    def test_partial_traverse_is_not_an_effort(self):
        waypoints = _trace(-2.0, 8.0)  # only 10 of 20 index steps covered

        assert match_segment_passes(7, _geometry(), _length_m(), waypoints) == []

    def test_impossibly_fast_traverse_is_noise(self):
        waypoints = _trace(-2.0, float(_N_POINTS + 1), speed_mps=60.0)

        assert match_segment_passes(7, _geometry(), _length_m(), waypoints) == []

    def test_far_away_activity_never_reaches_the_matcher(self):
        # Same shape, one degree of latitude away: the padded bbox rejects it.
        waypoints = [
            {"lat": wp["lat"] + 1.0, "lon": wp["lon"], "time": wp["time"]} for wp in _trace(-2.0, float(_N_POINTS + 1))
        ]

        assert match_segment_passes(7, _geometry(), _length_m(), waypoints) == []

    def test_degenerate_inputs_match_nothing(self):
        assert match_segment_passes(7, [[_SEG_LAT, _SEG_START_LNG]], _length_m(), _trace(-2.0, 22.0)) == []
        assert match_segment_passes(7, _geometry(), _length_m(), _trace(0.0, 0.4)) == []


class TestMultiplePasses:
    def test_u_turn_counts_a_forward_and_a_reverse_pass(self):
        out = _trace(-2.0, float(_N_POINTS))
        back = _trace(float(_N_POINTS), -2.0, t0=_last_time(out) + timedelta(seconds=2))
        waypoints = out + back[1:]

        matches = match_segment_passes(7, _geometry(), _length_m(), waypoints)

        assert [m.direction for m in matches] == [
            segments_constants.SegmentDirection.FORWARD,
            segments_constants.SegmentDirection.REVERSE,
        ]
        assert [m.pass_number for m in matches] == [1, 1]

    def test_two_laps_number_the_forward_efforts(self):
        lap1 = _trace(-2.0, float(_N_POINTS))
        lap2 = _trace(-2.0, float(_N_POINTS), t0=_T0 + timedelta(minutes=10))
        waypoints = lap1 + lap2

        matches = match_segment_passes(7, _geometry(), _length_m(), waypoints)

        forward = [m for m in matches if m.direction is segments_constants.SegmentDirection.FORWARD]
        assert len(forward) == 2
        assert [m.pass_number for m in forward] == [1, 2]
        assert forward[0].time_end < forward[1].time_start


class TestGeometryHelpers:
    def test_polyline_length_follows_the_metric_grid(self):
        assert polyline_length_m(_geometry()) == pytest.approx((_N_POINTS - 1) * _STEP_M, rel=1e-9)

    def test_polyline_length_of_nothing(self):
        assert polyline_length_m([]) == 0.0
        assert polyline_length_m([[1.0, 2.0]]) == 0.0

    def test_elevation_gain_ignores_descents(self):
        assert elevation_gain_m([10.0, 20.0, 15.0, 30.0]) == 25.0
        assert elevation_gain_m([]) == 0.0
