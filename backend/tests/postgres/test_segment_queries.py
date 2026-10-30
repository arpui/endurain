"""Segment effort queries against a real PostgreSQL database.

Two library answers rest on SQL that mocked tests cannot see. The page's effort
count is a correlated subquery that must ignore reverse passes — otherwise the
number on the list contradicts the leaderboard on the segment page. And the
per-segment "best" picks a row with ``DISTINCT ON`` ordered by elapsed time, which
must never land on a reverse pass even when that one is the fastest row stored.
"""

from datetime import UTC, datetime

import pytest

import modules.activities.activity.contracts as activity_contracts
import modules.activities.activity.crud as activity_crud
import modules.segments.constants as segments_constants
import modules.segments.contracts as segments_contracts
import modules.segments.crud as segments_crud
from tests._helpers.db import ensure_user

_USER_ID = 2
_START = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
_PRIVACY_FLAGS = (
    "hide_start_time",
    "hide_location",
    "hide_map",
    "hide_hr",
    "hide_power",
    "hide_cadence",
    "hide_elevation",
    "hide_speed",
    "hide_pace",
    "hide_laps",
    "hide_workout_sets_steps",
    "hide_gear",
)


def _activity_core(name: str) -> activity_contracts.ActivityCore:
    """Return a minimal valid activity input owned by the test user."""
    return activity_contracts.ActivityCore(
        user_id=_USER_ID,
        name=name,
        distance=10_000,
        activity_type=1,
        start_time=_START,
        end_time=_START.replace(hour=10),
        total_elapsed_time=3600.0,
        total_timer_time=3540.0,
        **{flag: False for flag in _PRIVACY_FLAGS},
    )


def _segment_record(
    name: str = "Hill",
    *,
    source: segments_constants.SegmentSource = segments_constants.SegmentSource.STRAVA,
    source_id: str | None = "39270619",
) -> segments_contracts.SegmentRecord:
    """Return a two-point segment definition ready to persist."""
    return segments_contracts.SegmentRecord(
        name=name,
        source=source,
        source_id=source_id,
        distance_m=1947.2,
        elevation_gain_m=128.6,
        altitudes=[355.4, 484.0],
        min_lat=41.36,
        min_lng=1.94,
        max_lat=41.37,
        max_lng=1.96,
        geometry=[[41.36, 1.94], [41.37, 1.96]],
    )


def _match(
    segment_id: int,
    direction: segments_constants.SegmentDirection,
    elapsed_seconds: int,
    pass_number: int = 1,
) -> segments_contracts.SegmentMatch:
    """Return one matching pass ready to store as an effort."""
    return segments_contracts.SegmentMatch(
        segment_id=segment_id,
        direction=direction,
        pass_number=pass_number,
        elapsed_seconds=elapsed_seconds,
        avg_speed_mps=round(1947.2 / elapsed_seconds, 3),
        coverage=0.95,
        span=0.95,
        time_start=_START.replace(tzinfo=None),
        time_end=_START.replace(tzinfo=None, minute=elapsed_seconds // 60, second=elapsed_seconds % 60),
        stream_index_start=0,
        stream_index_end=elapsed_seconds,
    )


@pytest.fixture
def seeded(pg_factory):
    """Two activities, one segment, and efforts that pit a fast reverse against slower forwards."""
    ensure_user(pg_factory, _USER_ID)
    with pg_factory() as db:
        first = activity_crud.create_activity(_activity_core("First"), db)
        second = activity_crud.create_activity(_activity_core("Second"), db)
        segment = segments_crud.create_segment(_segment_record(), _USER_ID, db)
        segments_crud.replace_activity_efforts(
            first.id,
            [
                _match(segment.id, segments_constants.SegmentDirection.FORWARD, 700),
                # The fastest row stored is a reverse pass: it must never be the
                # reference time, and it must not inflate the counted total.
                _match(segment.id, segments_constants.SegmentDirection.REVERSE, 500),
            ],
            db,
        )
        segments_crud.replace_activity_efforts(
            second.id,
            [_match(segment.id, segments_constants.SegmentDirection.FORWARD, 640)],
            db,
        )
        db.commit()
        return segment.id, first.id, second.id


class TestListSegmentsEffortCount:
    def test_counts_only_the_forward_efforts_of_the_given_activities(self, pg_factory, seeded):
        segment_id, first_id, second_id = seeded

        with pg_factory() as db:
            (item,) = segments_crud.list_segments(db, page_number=1, num_records=10, activity_ids=[first_id])

            assert item.id == segment_id
            assert item.effort_count == 1

            both = segments_crud.list_segments(db, page_number=1, num_records=10, activity_ids=[first_id, second_id])
            assert both[0].effort_count == 2

            none = segments_crud.list_segments(db, page_number=1, num_records=10, activity_ids=[])
            assert none[0].effort_count == 0

    def test_keeps_geometry_out_of_the_list_and_orders_by_name(self, pg_factory, seeded):
        segment_id, _, _ = seeded

        with pg_factory() as db:
            segments_crud.create_segment(
                _segment_record(name="A nother hill", source=segments_constants.SegmentSource.MANUAL, source_id=None),
                _USER_ID,
                db,
            )
            db.commit()

            items = segments_crud.list_segments(db, page_number=1, num_records=10, activity_ids=[1])

        assert [item.name for item in items] == ["A nother hill", "Hill"]
        assert items[1].id == segment_id
        assert not hasattr(items[0], "geometry")


class TestBestForwardEfforts:
    def test_picks_the_fastest_forward_pass_not_the_fastest_row(self, pg_factory, seeded):
        segment_id, first_id, second_id = seeded

        with pg_factory() as db:
            best = segments_crud.list_best_forward_efforts([segment_id], [first_id, second_id], db)

        assert best == [(segment_id, second_id, 640)]

    def test_returns_nothing_when_no_activity_is_in_scope(self, pg_factory, seeded):
        segment_id, _, _ = seeded

        with pg_factory() as db:
            assert segments_crud.list_best_forward_efforts([segment_id], [], db) == []
            assert segments_crud.list_best_forward_efforts([], [1], db) == []
