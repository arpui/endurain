"""Tests for the segments service: import rules, access rules, match orchestration."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import modules.segments.constants as segments_constants
import modules.segments.contracts as segments_contracts

_T0 = datetime(2026, 5, 1, 8, 0, 0)


def _segment_row(segment_id: int = 1, **overrides) -> SimpleNamespace:
    """Return an ORM-shaped segment row for schema validation."""
    row = SimpleNamespace(
        id=segment_id,
        name="Up the hill",
        source=segments_constants.SegmentSource.STRAVA.value,
        source_id="7071770",
        distance_m=840.0,
        elevation_gain_m=60.0,
        created_by=2,
        created_at=_T0,
        updated_at=_T0,
        min_lat=41.0,
        min_lng=2.0,
        max_lat=41.0,
        max_lng=2.01,
        geometry=[[41.0, 2.0], [41.0, 2.01]],
        altitudes=[10.0, 25.0],
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _effort_row(activity_id: int = 5, segment_id: int = 1, **overrides) -> SimpleNamespace:
    """Return an ORM-shaped effort row for schema validation."""
    row = SimpleNamespace(
        segment_id=segment_id,
        activity_id=activity_id,
        direction=segments_constants.SegmentDirection.FORWARD.value,
        pass_number=1,
        elapsed_seconds=180,
        avg_speed_mps=4.667,
        coverage=0.95,
        time_start=_T0,
        time_end=_T0.replace(minute=3),
        stream_index_start=10,
        stream_index_end=200,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _import_payload(**overrides):
    """Return an import body; a short straight polyline unless overridden."""
    from modules.segments import schema as segments_schema

    body = {
        "segment_id": 7071770,
        "name": "Up the hill",
        "latlng": [[41.0, 2.0], [41.0, 2.001], [41.0, 2.002]],
    }
    body.update(overrides)
    return segments_schema.SegmentImport(**body)


class TestImportSegment:
    @patch("modules.segments.service.segments_crud")
    def test_already_imported_source_id_is_refused(self, mock_crud):
        import core.exceptions as core_exceptions
        from modules.segments import service

        db = MagicMock()
        mock_crud.get_segment_by_source_id.return_value = _segment_row()

        with pytest.raises(core_exceptions.ConflictError):
            service.import_segment(_import_payload(), 2, db)

        mock_crud.create_segment.assert_not_called()

    @patch("modules.segments.service.segments_crud")
    def test_derives_length_gain_and_bbox_from_the_polyline(self, mock_crud):
        from modules.segments import service

        db = MagicMock()
        mock_crud.get_segment_by_source_id.return_value = None
        mock_crud.create_segment.return_value = _segment_row()

        result = service.import_segment(_import_payload(altitude=[10.0, 25.0, 20.0]), 2, db)

        record = mock_crud.create_segment.call_args.args[0]
        assert record.distance_m == pytest.approx(2 * 0.001 * 111_320.0 * 0.7547, rel=0.01)
        assert record.elevation_gain_m == 15.0
        assert record.altitudes == [10.0, 25.0, 20.0]
        assert (record.min_lat, record.max_lat) == (41.0, 41.0)
        assert (record.min_lng, record.max_lng) == (2.0, 2.002)
        assert record.source_id == "7071770"
        db.commit.assert_called_once()
        assert result.id == 1

    @patch("modules.segments.service.segments_crud")
    def test_a_segment_without_altitudes_stores_none(self, mock_crud):
        from modules.segments import service

        db = MagicMock()
        mock_crud.get_segment_by_source_id.return_value = None
        mock_crud.create_segment.return_value = _segment_row(elevation_gain_m=None, altitudes=None)

        result = service.import_segment(_import_payload(), 2, db)

        assert mock_crud.create_segment.call_args.args[0].altitudes is None
        assert result.altitudes is None

    @patch("modules.segments.service.segments_crud")
    def test_source_distance_array_wins_over_the_polyline_sum(self, mock_crud):
        from modules.segments import service

        db = MagicMock()
        mock_crud.get_segment_by_source_id.return_value = None
        mock_crud.create_segment.return_value = _segment_row()

        service.import_segment(_import_payload(distance=[0.0, 400.0, 800.0]), 2, db)

        assert mock_crud.create_segment.call_args.args[0].distance_m == 800.0

    @patch("modules.segments.service.segments_crud")
    def test_misaligned_altitude_is_refused(self, mock_crud):
        import core.exceptions as core_exceptions
        from modules.segments import service

        db = MagicMock()
        mock_crud.get_segment_by_source_id.return_value = None

        with pytest.raises(core_exceptions.InvalidInputError):
            service.import_segment(_import_payload(altitude=[10.0, 20.0]), 2, db)

        mock_crud.create_segment.assert_not_called()


class TestSegmentReadsAndDeletes:
    @patch("modules.segments.service.segments_crud")
    def test_get_missing_segment_is_not_found(self, mock_crud):
        import core.exceptions as core_exceptions
        from modules.segments import service

        mock_crud.get_segment_by_id.return_value = None
        with pytest.raises(core_exceptions.NotFoundError):
            service.get_segment(9, MagicMock())

    @patch("modules.segments.service.segments_crud")
    def test_delete_missing_segment_is_not_found(self, mock_crud):
        import core.exceptions as core_exceptions
        from modules.segments import service

        db = MagicMock()
        mock_crud.get_segment_by_id.return_value = None

        with pytest.raises(core_exceptions.NotFoundError):
            service.delete_segment(9, db)

        mock_crud.delete_segment.assert_not_called()
        db.commit.assert_not_called()

    @patch("modules.segments.service.segments_crud")
    def test_delete_existing_segment_commits(self, mock_crud):
        from modules.segments import service

        db = MagicMock()
        mock_crud.get_segment_by_id.return_value = _segment_row()

        service.delete_segment(1, db)

        mock_crud.delete_segment.assert_called_once_with(1, db)
        db.commit.assert_called_once()

    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.service.segments_crud")
    def test_library_is_global_so_no_owner_filter(self, mock_crud, mock_activities):
        from modules.segments import service

        db = MagicMock()
        mock_activities.list_user_activities.return_value = [SimpleNamespace(id=5), SimpleNamespace(id=10)]
        mock_crud.list_segments.return_value = []
        mock_crud.count_segments.return_value = 0

        service.list_segments(db, page_number=2, num_records=5, requester_user_id=2)

        kwargs = mock_crud.list_segments.call_args.kwargs
        assert kwargs["page_number"] == 2
        assert kwargs["num_records"] == 5
        # The library is not filtered by owner; only the counted efforts are.
        assert kwargs["activity_ids"] == [5, 10]


class TestSegmentEfforts:
    @patch("modules.segments.service.segments_crud")
    def test_efforts_of_missing_segment_are_not_found(self, mock_crud):
        import core.exceptions as core_exceptions
        from modules.segments import service

        mock_crud.get_segment_by_id.return_value = None
        with pytest.raises(core_exceptions.NotFoundError):
            service.list_segment_efforts(9, 2, MagicMock(), page_number=1, num_records=10)

    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.service.segments_crud")
    def test_efforts_are_limited_to_the_requester_activities(self, mock_crud, mock_activities):
        from modules.segments import service

        db = MagicMock()
        mock_crud.get_segment_by_id.return_value = _segment_row()
        mock_activities.list_user_activities.return_value = [
            SimpleNamespace(id=5),
            SimpleNamespace(id=10),
        ]
        mock_crud.list_efforts_for_segment.return_value = []
        mock_crud.count_efforts_for_segment.return_value = 0

        service.list_segment_efforts(1, 2, db, page_number=1, num_records=10)

        assert mock_crud.list_efforts_for_segment.call_args.args[1] == [5, 10]

    @pytest.mark.parametrize(
        ("direction", "expected"),
        [
            pytest.param("forward", "forward", id="forward"),
            pytest.param("reverse", "reverse", id="reverse"),
            pytest.param("all", None, id="both-directions"),
        ],
    )
    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.service.segments_crud")
    def test_effort_direction_filter_reaches_both_queries(self, mock_crud, mock_activities, direction, expected):
        """The page and its total must be counted under the same filter."""
        from modules.segments import schema as segments_schema
        from modules.segments import service

        db = MagicMock()
        mock_crud.get_segment_by_id.return_value = _segment_row()
        mock_activities.list_user_activities.return_value = [SimpleNamespace(id=5)]
        mock_crud.list_efforts_for_segment.return_value = []
        mock_crud.count_efforts_for_segment.return_value = 0

        service.list_segment_efforts(
            1,
            2,
            db,
            page_number=1,
            num_records=10,
            direction=segments_schema.EffortDirectionFilter(direction),
        )

        assert mock_crud.list_efforts_for_segment.call_args.kwargs["direction"] == expected
        assert mock_crud.count_efforts_for_segment.call_args.kwargs["direction"] == expected

    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.service.segments_crud")
    def test_activity_view_defaults_to_counted_direction(self, mock_crud, mock_activities):
        from modules.segments import service

        db = MagicMock()
        mock_activities.resolve_readable_parent.return_value = SimpleNamespace(id=5)
        mock_crud.list_efforts_for_activity_with_segments.return_value = []

        service.list_segments_for_activity(5, 2, db)

        assert mock_crud.list_efforts_for_activity_with_segments.call_args.kwargs["direction"] == "forward"

    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.service.segments_crud")
    def test_activity_view_groups_efforts_per_segment(self, mock_crud, mock_activities):
        from modules.segments import service

        db = MagicMock()
        mock_activities.resolve_readable_parent.return_value = SimpleNamespace(id=5)
        mock_activities.list_user_activities.return_value = [SimpleNamespace(id=5), SimpleNamespace(id=10)]
        mock_crud.list_efforts_for_activity_with_segments.return_value = [
            (_effort_row(activity_id=5, segment_id=1), _segment_row(1)),
            (_effort_row(activity_id=5, segment_id=1, pass_number=2), _segment_row(1)),
            (_effort_row(activity_id=5, segment_id=2), _segment_row(2)),
        ]
        mock_crud.list_best_forward_efforts.return_value = [(1, 10, 900), (2, 5, 400)]

        result = service.list_segments_for_activity(5, 2, db)

        assert [entry.segment.id for entry in result] == [1, 2]
        assert len(result[0].efforts) == 2
        assert len(result[1].efforts) == 1
        assert (result[0].best_activity_id, result[0].best_elapsed_seconds) == (10, 900)
        assert (result[1].best_activity_id, result[1].best_elapsed_seconds) == (5, 400)

    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.service.segments_crud")
    def test_activity_view_leaves_no_reference_time_without_own_efforts(self, mock_crud, mock_activities):
        from modules.segments import service

        db = MagicMock()
        mock_activities.resolve_readable_parent.return_value = SimpleNamespace(id=5)
        mock_activities.list_user_activities.return_value = []
        mock_crud.list_efforts_for_activity_with_segments.return_value = [
            (_effort_row(activity_id=5, segment_id=1), _segment_row(1)),
        ]
        mock_crud.list_best_forward_efforts.return_value = []

        result = service.list_segments_for_activity(5, 3, db)

        assert (result[0].best_activity_id, result[0].best_elapsed_seconds) == (None, None)

    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.service.segments_crud")
    def test_unreadable_activity_is_not_found(self, mock_crud, mock_activities):
        import core.exceptions as core_exceptions
        from modules.segments import service

        db = MagicMock()
        mock_activities.resolve_readable_parent.return_value = None

        with pytest.raises(core_exceptions.NotFoundError):
            service.list_segments_for_activity(5, 3, db)

        mock_crud.list_efforts_for_activity_with_segments.assert_not_called()


class TestMatchOrchestration:
    def _target(self, segment_id: int = 1) -> segments_contracts.SegmentMatchTarget:
        geometry = [[41.0, 2.0 + i * 0.0005] for i in range(21)]
        return segments_contracts.SegmentMatchTarget(id=segment_id, distance_m=840.0, geometry=geometry)

    def _waypoints(self, reverse: bool = False) -> list[dict]:
        indices = list(range(22, -3, -1)) if reverse else list(range(-2, 23))
        return [
            {
                "lat": 41.0,
                "lon": 2.0 + index * 0.0005,
                "time": (_T0 + timedelta(seconds=position * 8)).isoformat(),
            }
            for position, index in enumerate(indices)
        ]

    @patch("modules.segments.service.activity_streams_integration")
    @patch("modules.segments.service.segments_crud")
    def test_match_without_segments_stores_nothing(self, mock_crud, mock_streams):
        from modules.segments import service

        db = MagicMock()
        mock_crud.list_matchable_segments.return_value = []

        assert service.match_activity(5, db) == 0
        mock_streams.get_gps_waypoints_for_activities.assert_not_called()
        mock_crud.replace_activity_efforts.assert_not_called()

    @patch("modules.segments.service.activity_streams_integration")
    @patch("modules.segments.service.segments_crud")
    def test_match_stores_the_forward_effort_and_commits(self, mock_crud, mock_streams):
        from modules.segments import service

        db = MagicMock()
        mock_crud.list_matchable_segments.return_value = [self._target()]
        mock_streams.get_gps_waypoints_for_activities.return_value = {5: self._waypoints()}
        mock_crud.replace_activity_efforts.side_effect = lambda _a, matches, _d: len(matches)

        assert service.match_activity(5, db) == 1

        matches = mock_crud.replace_activity_efforts.call_args.args[1]
        assert matches[0].direction is segments_constants.SegmentDirection.FORWARD
        db.commit.assert_called_once()

    @patch("modules.segments.service.activity_streams_integration")
    @patch("modules.segments.service.segments_crud")
    def test_activity_without_gps_still_replaces_efforts(self, mock_crud, mock_streams):
        """Re-matching clears stale rows: a no-GPS activity must end with none."""
        from modules.segments import service

        db = MagicMock()
        mock_crud.list_matchable_segments.return_value = [self._target()]
        mock_streams.get_gps_waypoints_for_activities.return_value = {}
        mock_crud.replace_activity_efforts.side_effect = lambda _a, matches, _d: len(matches)

        assert service.match_activity(5, db) == 0
        assert mock_crud.replace_activity_efforts.call_args.args[1] == []

    @patch("modules.segments.service.activity_streams_integration")
    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.service.segments_crud")
    def test_reconcile_skips_activities_that_already_have_efforts(self, mock_crud, mock_activities, mock_streams):
        from modules.segments import service

        db = MagicMock()
        mock_crud.list_matchable_segments.return_value = [self._target()]
        mock_activities.list_activity_ids_after.side_effect = [[1, 2], []]
        mock_crud.activity_ids_with_efforts.return_value = {1}
        mock_streams.get_gps_waypoints_for_activities.return_value = {2: self._waypoints()}
        mock_crud.replace_activity_efforts.side_effect = lambda _a, matches, _d: len(matches)

        assert service.reconcile_missing_efforts(db) == 1

        assert mock_crud.replace_activity_efforts.call_args.args[0] == 2
        mock_crud.activity_ids_with_efforts.assert_called_once()

    @patch("modules.segments.service.activity_streams_integration")
    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.service.segments_crud")
    def test_rescan_rematches_everything_and_pages_by_id(self, mock_crud, mock_activities, mock_streams):
        from modules.segments import service

        db = MagicMock()
        mock_crud.list_matchable_segments.return_value = [self._target()]
        mock_activities.list_activity_ids_after.side_effect = [[1, 2], [3], []]
        mock_streams.get_gps_waypoints_for_activities.return_value = {
            1: self._waypoints(),
            2: self._waypoints(reverse=True),
            3: [],
        }
        mock_crud.replace_activity_efforts.side_effect = lambda _a, matches, _d: len(matches)

        assert service.rescan_all_efforts(db) == 2

        # The walk advances past the last id of each page instead of looping.
        second_page = mock_activities.list_activity_ids_after.call_args_list[1]
        assert second_page.kwargs["after_id"] == 2
        assert mock_crud.replace_activity_efforts.call_count == 3

    @patch("modules.segments.service.segments_crud")
    def test_walk_without_segments_is_a_no_op(self, mock_crud):
        from modules.segments import service

        db = MagicMock()
        mock_crud.list_matchable_segments.return_value = []

        assert service.rescan_all_efforts(db) == 0
        mock_crud.activity_ids_with_efforts.assert_not_called()
