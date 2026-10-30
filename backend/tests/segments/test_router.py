"""Tests for the segments routers: status codes, scopes wiring, page envelopes."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

_T0 = datetime(2026, 5, 1, 8, 0, 0)


def _build_app(mock_db):
    import core.database as core_db
    import core.exceptions as core_exceptions
    import modules.auth.dependencies as auth_deps
    import modules.segments.activity_router as activity_router
    import modules.segments.router as segments_router

    app = FastAPI()
    app.include_router(segments_router.router, prefix="/segments")
    app.include_router(activity_router.router, prefix="/activities/{activity_id}")
    # Same domain-error boundary the real app registers, so these tests assert
    # the status codes clients actually receive.
    core_exceptions.register_exception_handlers(app)

    app.dependency_overrides[auth_deps.check_scopes] = lambda: None
    app.dependency_overrides[auth_deps.get_sub_from_access_token] = lambda: 2
    app.dependency_overrides[core_db.get_db] = lambda: mock_db
    return app


def _segment_detail_json(**overrides) -> dict:
    """Return a stored-segment detail payload."""
    body = {
        "id": 1,
        "name": "Up the hill",
        "source": "strava",
        "source_id": "7071770",
        "distance_m": 840.0,
        "elevation_gain_m": 60.0,
        "created_by": 2,
        "created_at": _T0.isoformat(),
        "updated_at": _T0.isoformat(),
        "min_lat": 41.0,
        "min_lng": 2.0,
        "max_lat": 41.0,
        "max_lng": 2.01,
        "geometry": [[41.0, 2.0], [41.0, 2.01]],
    }
    body.update(overrides)
    return body


def _segment_row(**overrides) -> SimpleNamespace:
    """Return an ORM-shaped segment row."""
    row = SimpleNamespace(
        id=1,
        name="Up the hill",
        source="strava",
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
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _effort_row(**overrides) -> SimpleNamespace:
    """Return an ORM-shaped effort row."""
    row = SimpleNamespace(
        segment_id=1,
        activity_id=5,
        direction="forward",
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


class TestListSegments:
    @patch("modules.segments.crud.count_segments")
    @patch("modules.segments.crud.list_segments")
    def test_library_is_paged(self, mock_list, mock_count, mock_db):
        from modules.segments import schema as segments_schema

        mock_list.return_value = [segments_schema.SegmentRead.model_validate(_segment_row())]
        mock_count.return_value = 1

        response = TestClient(_build_app(mock_db)).get("/segments", headers={"Authorization": "Bearer x"})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == 1
        # The list view never carries the polyline: only the detail does.
        assert "geometry" not in body["items"][0]

    def test_page_size_is_capped(self, mock_db):
        response = TestClient(_build_app(mock_db)).get(
            "/segments?num_records=5000",
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 422


class TestImportSegment:
    @patch("modules.segments.crud.create_segment")
    @patch("modules.segments.crud.get_segment_by_source_id")
    def test_import_returns_the_stored_detail(self, mock_by_source, mock_create, mock_db):
        mock_by_source.return_value = None
        mock_create.return_value = _segment_row()

        response = TestClient(_build_app(mock_db)).post(
            "/segments",
            json={"segment_id": 7071770, "name": "Up the hill", "latlng": [[41.0, 2.0], [41.0, 2.01]]},
            headers={"Authorization": "Bearer x"},
        )

        assert response.status_code == 201
        assert response.json()["geometry"] == [[41.0, 2.0], [41.0, 2.01]]

    @patch("modules.segments.crud.get_segment_by_source_id")
    def test_duplicate_import_conflicts(self, mock_by_source, mock_db):
        mock_by_source.return_value = _segment_row()

        response = TestClient(_build_app(mock_db)).post(
            "/segments",
            json={"segment_id": 7071770, "name": "Up the hill", "latlng": [[41.0, 2.0], [41.0, 2.01]]},
            headers={"Authorization": "Bearer x"},
        )

        assert response.status_code == 409

    def test_short_polyline_is_rejected_before_the_service(self, mock_db):
        response = TestClient(_build_app(mock_db)).post(
            "/segments",
            json={"segment_id": 7071770, "name": "Up the hill", "latlng": [[41.0, 2.0]]},
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 422

    def test_unknown_extra_field_is_rejected(self, mock_db):
        response = TestClient(_build_app(mock_db)).post(
            "/segments",
            json={
                "segment_id": 7071770,
                "name": "Up the hill",
                "latlng": [[41.0, 2.0], [41.0, 2.01]],
                "polyline": "abc",
            },
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 422


class TestGetAndDeleteSegment:
    @patch("modules.segments.crud.get_segment_by_id")
    def test_detail_carries_the_geometry(self, mock_get, mock_db):
        mock_get.return_value = _segment_row()

        response = TestClient(_build_app(mock_db)).get("/segments/1", headers={"Authorization": "Bearer x"})

        assert response.status_code == 200
        assert response.json()["geometry"] == [[41.0, 2.0], [41.0, 2.01]]

    @patch("modules.segments.crud.get_segment_by_id")
    def test_missing_segment_is_not_found(self, mock_get, mock_db):
        mock_get.return_value = None

        response = TestClient(_build_app(mock_db)).get("/segments/9", headers={"Authorization": "Bearer x"})
        assert response.status_code == 404

    @patch("modules.segments.crud.get_segment_by_id")
    def test_delete_answers_no_content(self, mock_get, mock_db):
        mock_get.return_value = _segment_row()

        response = TestClient(_build_app(mock_db)).delete("/segments/1", headers={"Authorization": "Bearer x"})

        assert response.status_code == 204
        mock_db.execute.assert_called()

    def test_non_positive_id_is_rejected(self, mock_db):
        response = TestClient(_build_app(mock_db)).get("/segments/0", headers={"Authorization": "Bearer x"})
        assert response.status_code == 422

    @patch("modules.segments.scheduled_jobs.schedule_segments_rescan")
    def test_backfill_is_queued_off_the_request_path(self, mock_schedule, mock_db):
        """The literal /backfill route wins over /{segment_id}, so no id is looked up."""
        response = TestClient(_build_app(mock_db)).post("/segments/backfill", headers={"Authorization": "Bearer x"})

        assert response.status_code == 200
        assert response.json() == {"queued": True}
        mock_schedule.assert_called_once()


class TestSegmentEfforts:
    @patch("modules.segments.crud.count_efforts_for_segment")
    @patch("modules.segments.crud.list_efforts_for_segment")
    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.crud.get_segment_by_id")
    def test_efforts_are_a_page_of_the_requester_rows(self, mock_get, mock_activities, mock_list, mock_count, mock_db):
        from modules.segments import schema as segments_schema

        mock_get.return_value = _segment_row()
        mock_activities.list_user_activities.return_value = [SimpleNamespace(id=5)]
        mock_list.return_value = [segments_schema.SegmentEffortRead.model_validate(_effort_row())]
        mock_count.return_value = 1

        response = TestClient(_build_app(mock_db)).get("/segments/1/efforts", headers={"Authorization": "Bearer x"})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["activity_id"] == 5

    @patch("modules.segments.crud.count_efforts_for_segment")
    @patch("modules.segments.crud.list_efforts_for_segment")
    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.crud.get_segment_by_id")
    def test_default_direction_is_forward(self, mock_get, mock_activities, mock_list, mock_count, mock_db):
        from modules.segments import schema as segments_schema

        mock_get.return_value = _segment_row()
        mock_activities.list_user_activities.return_value = [SimpleNamespace(id=5)]
        mock_list.return_value = [segments_schema.SegmentEffortRead.model_validate(_effort_row())]
        mock_count.return_value = 1

        response = TestClient(_build_app(mock_db)).get("/segments/1/efforts", headers={"Authorization": "Bearer x"})

        assert response.status_code == 200
        assert mock_list.call_args.kwargs["direction"] == "forward"

    @patch("modules.segments.crud.count_efforts_for_segment")
    @patch("modules.segments.crud.list_efforts_for_segment")
    @patch("modules.segments.service.activity_integration")
    @patch("modules.segments.crud.get_segment_by_id")
    def test_direction_all_reaches_the_queries_unfiltered(
        self, mock_get, mock_activities, mock_list, mock_count, mock_db
    ):
        from modules.segments import schema as segments_schema

        mock_get.return_value = _segment_row()
        mock_activities.list_user_activities.return_value = [SimpleNamespace(id=5)]
        mock_list.return_value = [segments_schema.SegmentEffortRead.model_validate(_effort_row())]
        mock_count.return_value = 1

        response = TestClient(_build_app(mock_db)).get(
            "/segments/1/efforts?direction=all",
            headers={"Authorization": "Bearer x"},
        )

        assert response.status_code == 200
        assert mock_list.call_args.kwargs["direction"] is None
        assert mock_count.call_args.kwargs["direction"] is None

    def test_unknown_direction_is_rejected(self, mock_db):
        response = TestClient(_build_app(mock_db)).get(
            "/segments/1/efforts?direction=sideways",
            headers={"Authorization": "Bearer x"},
        )
        assert response.status_code == 422

    @patch("modules.segments.crud.get_segment_by_id")
    def test_efforts_of_missing_segment_are_not_found(self, mock_get, mock_db):
        mock_get.return_value = None

        response = TestClient(_build_app(mock_db)).get("/segments/9/efforts", headers={"Authorization": "Bearer x"})
        assert response.status_code == 404


class TestActivitySegments:
    @patch("modules.segments.crud.list_efforts_for_activity_with_segments")
    @patch("modules.segments.service.activity_integration")
    def test_traversed_segments_group_their_efforts(self, mock_activities, mock_rows, mock_db):
        mock_activities.resolve_readable_parent.return_value = SimpleNamespace(id=5)
        mock_rows.return_value = [
            (_effort_row(), _segment_row()),
            (_effort_row(pass_number=2), _segment_row()),
        ]

        response = TestClient(_build_app(mock_db)).get("/activities/5/segments", headers={"Authorization": "Bearer x"})

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["segment"]["id"] == 1
        assert len(body[0]["efforts"]) == 2
        assert "geometry" not in body[0]["segment"]

    @patch("modules.segments.crud.list_efforts_for_activity_with_segments")
    @patch("modules.segments.service.activity_integration")
    def test_direction_param_reaches_the_read(self, mock_activities, mock_rows, mock_db):
        mock_activities.resolve_readable_parent.return_value = SimpleNamespace(id=5)
        mock_rows.return_value = []

        response = TestClient(_build_app(mock_db)).get(
            "/activities/5/segments?direction=all",
            headers={"Authorization": "Bearer x"},
        )

        assert response.status_code == 200
        assert mock_rows.call_args.kwargs["direction"] is None

    @patch("modules.segments.crud.list_efforts_for_activity_with_segments")
    @patch("modules.segments.service.activity_integration")
    def test_unreadable_activity_is_not_found(self, mock_activities, mock_rows, mock_db):
        mock_activities.resolve_readable_parent.return_value = None

        response = TestClient(_build_app(mock_db)).get("/activities/5/segments", headers={"Authorization": "Bearer x"})

        assert response.status_code == 404
        mock_rows.assert_not_called()
