"""Trail observation rows against a real PostgreSQL database.

The claims that matter here are storage claims a mocked test cannot see: measuring
the same activity twice must not accumulate stretches, and measuring it under a
different set of detector parameters must keep the older observation readable -
that coexistence is what lets a calibration be compared against what the same
ground measured before. The composite foreign key from stretches to their session
row is also only exercised by a real database.
"""

from dataclasses import replace
from datetime import UTC, date, datetime

from sqlalchemy import select

import modules.activities.activity.contracts as activity_contracts
import modules.activities.activity.crud as activity_crud
import modules.analysis.constants as analysis_constants
import modules.analysis.contracts as analysis_contracts
import modules.analysis.crud as analysis_crud
import modules.analysis.models as analysis_models
import modules.analysis.schema as analysis_schema
import modules.analysis.service as analysis_service
from tests._helpers.db import ensure_user

_USER_ID = 2
_START = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
_METHOD = analysis_constants.trail_model_stamp()
_PRIVACY_FLAGS = tuple(
    f"hide_{name}"
    for name in (
        "start_time",
        "location",
        "map",
        "hr",
        "power",
        "cadence",
        "elevation",
        "speed",
        "pace",
        "laps",
        "workout_sets_steps",
        "gear",
    )
)


def _run_activity(db) -> int:
    """Create one trail-run activity for the test user and return its id."""
    activity = activity_crud.create_activity(
        activity_contracts.ActivityCore(
            user_id=_USER_ID,
            name="Cabdella",
            distance=18_300,
            activity_type=2,
            start_time=_START,
            end_time=_START.replace(hour=11),
            total_elapsed_time=7200.0,
            total_timer_time=7000.0,
            **{flag: False for flag in _PRIVACY_FLAGS},
        ),
        db,
    )
    return activity.id


def _observation(
    sections: int,
) -> tuple[analysis_contracts.TrailSessionRecord, list[analysis_contracts.TrailSectionRecord]]:
    """Return a session record and the given number of stretches."""
    session = analysis_contracts.TrailSessionRecord(
        v_base_ms=2.46,
        cadence_available=True,
        total_m=18693.8,
        technical_m=250.0 * sections,
    )
    rows = [
        analysis_contracts.TrailSectionRecord(
            start_m=7675.0 + index * 25.0,
            length_m=1225.0,
            grade_pct=-15.3,
            avg_speed_ms=1.51,
            gap_deficit=0.68,
            cadence_jitter=0.36,
            brake_score=0.81,
            score=0.83,
            direction="descent",
            core_start_m=8100.0,
            core_end_m=8400.0,
            core_score=0.91,
            v_base_ms=2.46,
            cadence_available=True,
            total_m=18693.8,
            start_t=_START,
            end_t=_START.replace(minute=20),
        )
        for index in range(sections)
    ]
    return session, rows


class TestTrailObservations:
    def test_an_observation_round_trips_with_its_components(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id = _run_activity(db)
            analysis_crud.replace_trail_observation(
                user_id=_USER_ID,
                activity_id=activity_id,
                method=_METHOD,
                observation=analysis_contracts.TrailSessionRecord(
                    v_base_ms=2.46,
                    cadence_available=True,
                    total_m=18693.8,
                    technical_m=1225.0,
                ),
                sections=[
                    analysis_contracts.TrailSectionRecord(
                        start_m=7675.0,
                        length_m=1225.0,
                        grade_pct=-15.3,
                        avg_speed_ms=1.51,
                        gap_deficit=0.68,
                        cadence_jitter=0.36,
                        brake_score=0.81,
                        score=0.83,
                        direction="descent",
                        core_start_m=None,
                        core_end_m=None,
                        core_score=None,
                        v_base_ms=2.46,
                        cadence_available=True,
                        total_m=18693.8,
                        start_t=_START,
                        end_t=_START.replace(minute=20),
                    )
                ],
                db=db,
            )
            db.commit()

            stored = analysis_crud.list_trail_observation(activity_id, _METHOD, db)
        assert stored is not None
        session_row, sections = stored
        assert session_row.technical_m == 1225.0
        assert [(s.start_m, s.score, s.direction) for s in sections] == [(7675.0, 0.83, "descent")]
        assert sections[0].cadence_jitter == 0.36

    def test_measuring_twice_leaves_one_observation(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id = _run_activity(db)
            session, wide = _observation(sections=4)
            analysis_crud.replace_trail_observation(
                user_id=_USER_ID,
                activity_id=activity_id,
                method=_METHOD,
                observation=session,
                sections=wide,
                db=db,
            )
            db.commit()
            _, narrow = _observation(sections=1)
            analysis_crud.replace_trail_observation(
                user_id=_USER_ID,
                activity_id=activity_id,
                method=_METHOD,
                observation=_observation(sections=1)[0],
                sections=narrow,
                db=db,
            )
            db.commit()

            stored = analysis_crud.list_trail_observation(activity_id, _METHOD, db)
            measured = analysis_crud.activity_ids_with_trail_sessions([activity_id], _METHOD, db)
        assert stored is not None
        assert len(stored[1]) == 1
        assert measured == {activity_id}

    def test_the_reconcile_pass_lands_what_it_measured(self, pg_factory, monkeypatch):
        """The scheduled pass must commit, not just measure.

        Its session outlives any request, so rows left uncommitted when it closes
        are rolled back and the next hour measures the same activities in vain.
        An in-memory test sees uncommitted rows inside the same session; only a
        real database can catch the missing commit.
        """
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id = _run_activity(db)
            db.commit()
            session_row, one_section = _observation(sections=1)

            def _measure(measured_activity_id: int, inner_db) -> int:
                analysis_crud.replace_trail_observation(
                    user_id=_USER_ID,
                    activity_id=measured_activity_id,
                    method=_METHOD,
                    observation=session_row,
                    sections=one_section,
                    db=inner_db,
                )
                return len(one_section)

            monkeypatch.setattr(analysis_service, "compute_trail_sections_for_activity", _measure)
            written = analysis_service.reconcile_trail_sections(db)
        assert written == 1
        with pg_factory() as db:
            stored = analysis_crud.list_trail_observation(activity_id, _METHOD, db)
        assert stored is not None
        assert len(stored[1]) == 1

    def test_an_unremarkable_run_is_still_recorded_as_measured(self, pg_factory):
        """A session with no stretches keeps its row, so backfills skip it."""
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id = _run_activity(db)
            session, none = _observation(sections=0)
            analysis_crud.replace_trail_observation(
                user_id=_USER_ID,
                activity_id=activity_id,
                method=_METHOD,
                observation=session,
                sections=none,
                db=db,
            )
            db.commit()

            stored = analysis_crud.list_trail_observation(activity_id, _METHOD, db)
            measured = analysis_crud.activity_ids_with_trail_sessions([activity_id], _METHOD, db)
        assert stored is not None and stored[1] == []
        assert measured == {activity_id}

    def test_an_older_model_keeps_its_own_observation(self, pg_factory):
        """Recalibrating must not rewrite history: the two coexist."""
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id = _run_activity(db)
            # a threshold the model no longer runs with
            old_method = "trail:0.42/0.55/0.2/0.25/25/75"
            assert old_method != _METHOD
            session, sections = _observation(sections=2)
            analysis_crud.replace_trail_observation(
                user_id=_USER_ID,
                activity_id=activity_id,
                method=old_method,
                observation=session,
                sections=sections,
                db=db,
            )
            db.commit()

            assert analysis_crud.list_trail_observation(activity_id, _METHOD, db) is None
            measured_by_old = analysis_crud.activity_ids_with_trail_sessions([activity_id], old_method, db)
            assert measured_by_old == {activity_id}
            assert analysis_crud.activity_ids_with_trail_sessions([activity_id], _METHOD, db) == set()


def _way(way_id: int = 4242, *, lat: float = 42.47) -> analysis_contracts.OsmPathRecord:
    """Return one mapped way, running north, ready to persist."""
    geometry = [(lat + index * 25.0 / 111195.0, 0.99) for index in range(40)]
    return analysis_contracts.OsmPathRecord(
        way_id=way_id,
        name="Camí de la Teula",
        tags={"highway": "path", "sac_scale": "T3", "surface": "rock"},
        geometry=geometry,
        length_m=1000.0,
        min_lat=lat,
        min_lng=0.99,
        max_lat=lat + 1000.0 / 111195.0,
        max_lng=0.99,
        cache_date=date(2026, 11, 1),
    )


class TestTrailWayEvidence:
    def test_the_network_query_pairs_sections_with_their_way_inside_a_viewport(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id = _run_activity(db)
            analysis_crud.replace_osm_paths([_way(way_id=4242, lat=42.47)], db)
            analysis_crud.replace_osm_paths([_way(way_id=99, lat=45.5)], db)
            session_row, sections = _observation(sections=2)
            analysis_crud.replace_trail_observation(
                user_id=_USER_ID,
                activity_id=activity_id,
                method=_METHOD,
                observation=session_row,
                sections=sections,
                db=db,
            )
            db.commit()
            stored_sections = analysis_crud.list_trail_observation(activity_id, _METHOD, db)[1]
            for index, section in enumerate(stored_sections):
                way_id = 4242 if index == 0 else 99
                analysis_crud.set_trail_section_osm_match(
                    section,
                    analysis_contracts.OsmMatchRecord(way_id=way_id, start_m=10.0, end_m=20.0, covered_m=10.0),
                    date(2026, 11, 1),
                    db,
                )
            db.commit()

            pairs = analysis_crud.list_trail_way_evidence(_USER_ID, _METHOD, db)
            near = analysis_crud.list_trail_way_evidence(_USER_ID, _METHOD, db, bbox=(42.4, 0.9, 42.5, 1.0))
        assert {pair[0].way_id for pair in pairs} == {4242, 99}
        assert {pair[0].way_id for pair in near} == {4242}
        assert {pair[1].start_m for pair in near} == {7675.0}


class TestPersonalGraph:
    def test_the_graph_replaces_wholesale_and_reads_back_by_box(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        nodes = [{"x": 0, "y": 0, "lat": 42.0, "lng": 1.5, "ele": 10.0, "visits": 3, "grade": "hard"}]
        edges = [
            {
                "ax": 0,
                "ay": 0,
                "bx": 1,
                "by": 0,
                "length": 15.0,
                "gain": 1.0,
                "loss": 0.0,
                "passes": 2,
                "step_max": 4.0,
                "on_path": 1,
            },
            {
                "ax": 500,
                "ay": 500,
                "bx": 501,
                "by": 500,
                "length": 15.0,
                "gain": 0.0,
                "loss": 0.0,
                "passes": 1,
                "step_max": 4.0,
                "on_path": 0,
            },
        ]
        with pg_factory() as db:
            assert analysis_crud.replace_user_graph(_USER_ID, nodes, edges, db) == (1, 2)
            db.commit()
            got_nodes, got_edges = analysis_crud.list_user_graph_in_bbox(_USER_ID, -10, 10, -10, 10, db)
            assert len(got_nodes) == 1 and len(got_edges) == 1 and got_edges[0].on_path == 1

            # a rebuild replaces: no double counting
            assert analysis_crud.replace_user_graph(_USER_ID, nodes, [], db) == (1, 0)
            db.commit()
            _, again = analysis_crud.list_user_graph_in_bbox(_USER_ID, -100000, 100000, -100000, 100000, db)
            assert again == []

    def test_a_graph_beyond_one_statement_of_parameters_still_lands(self, pg_factory):
        # psycopg caps a statement at 65.535 parameters: a real graph (hundreds of
        # thousands of cells) only fits by inserting in chunks.
        ensure_user(pg_factory, _USER_ID)
        many = [
            {"x": k, "y": 0, "lat": 42.0, "lng": 1.5 + k * 1e-6, "ele": 10.0, "visits": 1, "grade": ""}
            for k in range(4500)
        ]
        with pg_factory() as db:
            assert analysis_crud.replace_user_graph(_USER_ID, many, [], db) == (4500, 0)
            db.commit()
            got_nodes, _ = analysis_crud.list_user_graph_in_bbox(_USER_ID, -10, 5000, -10, 10, db)
            assert len(got_nodes) == 4500


class TestDesignedRoutes:
    def test_a_saved_route_carries_its_timestamp_and_totals(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        payload = analysis_schema.RouteCreate(name="vora del riu", geometry=[[42.0, 1.5], [42.0, 1.501]])
        with pg_factory() as db:
            created = analysis_service.create_route(_USER_ID, payload, db)
            assert created.id is not None and created.created_at
            assert created.distance_m > 0
            listed = analysis_service.list_routes(_USER_ID, db)
            assert [route.id for route in listed] == [created.id]
            assert analysis_service.delete_route(created.id, _USER_ID, db) is True
            assert analysis_service.list_routes(_USER_ID, db) == []


class TestRouteEditing:
    def test_editing_remeasures_and_a_foreign_route_is_not_there(self, pg_factory):
        import math

        ensure_user(pg_factory, _USER_ID)
        ref_lat = analysis_constants.TRAIL_GRAPH_REF_LAT
        ref_lng = analysis_constants.TRAIL_GRAPH_REF_LNG
        cell_m = analysis_constants.TRAIL_GRAPH_CELL_M
        step_lng = cell_m / (111320.0 * math.cos(math.radians(ref_lat)))
        ele = [100.0, 110.0, 105.0, 120.0]
        nodes = [
            {"x": k, "y": 0, "lat": ref_lat, "lng": ref_lng + k * step_lng, "ele": ele[k], "visits": 1, "grade": ""}
            for k in range(4)
        ]
        pts = [[ref_lat, ref_lng + k * step_lng] for k in range(4)]
        with pg_factory() as db:
            analysis_crud.replace_user_graph(_USER_ID, nodes, [], db)
            db.commit()
            created = analysis_service.create_route(
                _USER_ID, analysis_schema.RouteCreate(name="pujada", geometry=pts), db
            )
            assert created.gain_m == 25.0  # 100→110 (+10) and 105→120 (+15)
            assert created.distance_m > 40.0

            shorter = pts[:2] + pts[3:]
            edited = analysis_service.update_route(
                created.id,
                _USER_ID,
                analysis_schema.RouteUpdate(name="rebaixada", geometry=shorter, notes="editada"),
                db,
            )
            assert edited is not None
            assert edited.name == "rebaixada" and edited.notes == "editada"
            assert edited.gain_m == 20.0  # 100→110 (+10) and 110→120 (+10)
            assert len(edited.geometry) == 3 and edited.distance_m == created.distance_m

            # another athlete's eyes: the route is simply not there
            assert (
                analysis_service.update_route(
                    created.id, _USER_ID + 77, analysis_schema.RouteUpdate(name="x", geometry=shorter), db
                )
                is None
            )


class TestRouteConnect:
    def test_drawn_anchors_are_carried_by_the_walked_graph(self, pg_factory):
        import math

        ensure_user(pg_factory, _USER_ID)
        ref_lat = analysis_constants.TRAIL_GRAPH_REF_LAT
        ref_lng = analysis_constants.TRAIL_GRAPH_REF_LNG
        cell_m = analysis_constants.TRAIL_GRAPH_CELL_M
        step_lng = cell_m / (111320.0 * math.cos(math.radians(ref_lat)))
        # a straight walked street of four cells, plus its edges
        nodes = [
            {"x": k, "y": 0, "lat": ref_lat, "lng": ref_lng + k * step_lng, "ele": 100.0, "visits": 2, "grade": ""}
            for k in range(4)
        ]
        edges = [
            {
                "ax": k,
                "ay": 0,
                "bx": k + 1,
                "by": 0,
                "length": cell_m,
                "gain": 0.0,
                "loss": 0.0,
                "passes": 2,
                "step_max": 5.0,
                "on_path": 1,
            }
            for k in range(3)
        ]
        with pg_factory() as db:
            analysis_crud.replace_user_graph(_USER_ID, nodes, edges, db)
            db.commit()
            near_start = [ref_lat, ref_lng + 0.00002]  # ~2 m off cell 0
            near_end = [ref_lat, ref_lng + 3 * step_lng + 0.00002]
            res = analysis_service.connect_route(
                _USER_ID, analysis_schema.RouteConnectCreate(points=[near_start, near_end]), db
            )
            assert res.walked == [True]
            assert len(res.geometry) == 4  # the four cell centres, in order
            assert abs(res.geometry[0][1] - ref_lng) < 1e-9

            # an anchor over ground never crossed keeps the straight line
            far = [ref_lat + 0.02, ref_lng]
            res2 = analysis_service.connect_route(
                _USER_ID, analysis_schema.RouteConnectCreate(points=[near_start, far]), db
            )
            assert res2.walked == [False]
            assert len(res2.geometry) == 2


class TestSegmentCrossings:
    """Named ground on both axes: the along-segment and along-track spans, and
    the evidence join that rides the track axis without any re-matching."""

    @staticmethod
    def _seed(db):
        import modules.segments.constants as segments_constants
        import modules.segments.contracts as segments_contracts
        import modules.segments.crud as segments_crud

        activity_id = _run_activity(db)
        segment = segments_crud.create_segment(
            segments_contracts.SegmentRecord(
                name="PistaHN 400m",
                source=segments_constants.SegmentSource.MANUAL,
                source_id=None,
                distance_m=409.0,
                elevation_gain_m=9.0,
                altitudes=None,
                min_lat=42.0,
                min_lng=1.5,
                max_lat=42.01,
                max_lng=1.51,
                geometry=[[42.0, 1.5], [42.01, 1.5]],
            ),
            _USER_ID,
            db,
        )
        analysis_crud.replace_trail_observation(
            user_id=_USER_ID,
            activity_id=activity_id,
            method=_METHOD,
            observation=analysis_contracts.TrailSessionRecord(
                v_base_ms=2.4, cadence_available=True, total_m=5000.0, technical_m=200.0
            ),
            sections=[
                analysis_contracts.TrailSectionRecord(
                    start_m=1000.0,
                    length_m=200.0,
                    grade_pct=-8.0,
                    avg_speed_ms=3.0,
                    gap_deficit=0.4,
                    cadence_jitter=0.2,
                    brake_score=0.5,
                    score=2.2,
                    direction="descent",
                    core_start_m=None,
                    core_end_m=None,
                    core_score=None,
                    v_base_ms=2.4,
                    cadence_available=True,
                    total_m=5000.0,
                    start_t=_START,
                    end_t=_START,
                )
            ],
            db=db,
        )
        return activity_id, segment.id

    def test_a_pass_and_its_descent_join_and_stay_idempotent(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id, segment_id = self._seed(db)
            db.commit()
            rows = [
                {
                    "segment_id": segment_id,
                    "user_id": _USER_ID,
                    "pass_number": 1,
                    "along_start_m": 20.0,
                    "along_end_m": 380.0,
                    "track_start_m": 950.0,
                    "track_end_m": 1300.0,
                    "direction": "forward",
                    "grade_pct": -7.0,
                }
            ]
            assert analysis_crud.replace_activity_segment_crossings(activity_id, rows, db) == 1
            analysis_crud.insert_segment_cells([(segment_id, 0, 0), (segment_id, 0, 0), (segment_id, 1, 0)], db)
            db.commit()

            assert len(analysis_crud.list_user_segment_crossings(_USER_ID, db)) == 1
            evidence = analysis_crud.list_segment_descent_evidence(_USER_ID, _METHOD, db)
            assert len(evidence) == 1 and evidence[0][1].score == 2.2
            assert analysis_crud.list_segment_preference_cells(_USER_ID, db) == {(0, 0), (1, 0)}

            # re-running the projection converges: no double count
            assert analysis_crud.replace_activity_segment_crossings(activity_id, rows, db) == 1
            db.commit()
            assert len(analysis_crud.list_user_segment_crossings(_USER_ID, db)) == 1

    def test_a_pass_over_another_part_of_the_track_is_not_evidence(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id, segment_id = self._seed(db)
            db.commit()
            rows = [
                {
                    "segment_id": segment_id,
                    "user_id": _USER_ID,
                    "pass_number": 1,
                    "along_start_m": 10.0,
                    "along_end_m": 400.0,
                    "track_start_m": 2000.0,
                    "track_end_m": 2300.0,
                    "direction": "forward",
                    "grade_pct": -6.0,
                }
            ]
            analysis_crud.replace_activity_segment_crossings(activity_id, rows, db)
            db.commit()
            assert analysis_crud.list_segment_descent_evidence(_USER_ID, _METHOD, db) == []


class TestRouteVariantMarks:
    def test_a_mark_belongs_to_a_route_or_an_activity_never_both(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            created = analysis_service.create_route(
                _USER_ID,
                analysis_schema.RouteCreate(
                    name="amb variants", geometry=[[42.3, 1.5], [42.3, 1.51]], source_activity_id=None
                ),
                db,
            )
            analysis_crud.save_variant_mark(_USER_ID, 800.0, "falsa", None, db, route_id=created.id)
            db.commit()
            rows = analysis_crud.list_variant_marks(_USER_ID, db, route_id=created.id)
            assert len(rows) == 1 and rows[0].route_id == created.id and rows[0].activity_id is None
            # the same split on an activity is another, independent verdict
            activity_id = _run_activity(db)
            db.commit()
            analysis_crud.save_variant_mark(_USER_ID, 800.0, "real", "aquest sí", db, activity_id=activity_id)
            db.commit()
            assert len(analysis_crud.list_variant_marks(_USER_ID, db, route_id=created.id)) == 1
            assert len(analysis_crud.list_variant_marks(_USER_ID, db, activity_id=activity_id)) == 1
            assert analysis_crud.delete_variant_mark(_USER_ID, 800.0, db, route_id=created.id)
            db.commit()
            assert analysis_crud.list_variant_marks(_USER_ID, db, route_id=created.id) == []
            # variants search on the designed route answers the empty graph honestly
            vr = analysis_service.get_route_variants(created.id, _USER_ID, db)
            assert vr.route_id == created.id and vr.variants == []
            assert analysis_service.get_route_segments(created.id, _USER_ID, db) == []


class TestWayCrossings:
    def test_crossings_replace_round_trip_and_the_marker_gates_the_pass(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id = _run_activity(db)
            analysis_crud.replace_osm_paths([_way(way_id=4242)], db)
            session_row, sections = _observation(sections=1)
            analysis_crud.replace_trail_observation(
                user_id=_USER_ID,
                activity_id=activity_id,
                method=_METHOD,
                observation=session_row,
                sections=sections,
                db=db,
            )
            db.commit()

            assert analysis_crud.activity_ids_needing_coverage([activity_id], _METHOD, date(2026, 11, 1), db) == {
                activity_id
            }

            first = [
                analysis_contracts.WayCrossingRecord(way_id=4242, start_m=0.0, end_m=500.0, covered_m=500.0),
                analysis_contracts.WayCrossingRecord(way_id=4242, start_m=600.0, end_m=900.0, covered_m=300.0),
            ]
            assert analysis_crud.replace_way_crossings(_USER_ID, activity_id, date(2026, 11, 1), first, db) == 2
            analysis_crud.set_trail_session_coverage_date(activity_id, _METHOD, date(2026, 11, 1), db)
            db.commit()

            assert analysis_crud.list_user_way_crossing_counts(_USER_ID, db) == {4242: 1}
            assert analysis_crud.activity_ids_needing_coverage([activity_id], _METHOD, date(2026, 11, 1), db) == set()
            # a newer extract reopens the question
            assert analysis_crud.activity_ids_needing_coverage([activity_id], _METHOD, date(2026, 12, 1), db) == {
                activity_id
            }

            second = [analysis_contracts.WayCrossingRecord(way_id=4242, start_m=0.0, end_m=900.0, covered_m=900.0)]
            assert analysis_crud.replace_way_crossings(_USER_ID, activity_id, date(2026, 12, 1), second, db) == 1
            db.commit()
            rows = (
                db.execute(
                    select(analysis_models.WayCrossing).where(analysis_models.WayCrossing.activity_id == activity_id)
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1 and rows[0].covered_m == 900.0


class TestOsmPaths:
    def test_an_import_describes_the_same_way_twice_without_duplicating_it(self, pg_factory):
        with pg_factory() as db:
            analysis_crud.replace_osm_paths([_way()], db)
            db.commit()
            analysis_crud.replace_osm_paths(
                [replace(_way(), tags={"highway": "path", "sac_scale": "T4", "surface": "scree"})],
                db,
            )
            db.commit()

            paths = analysis_crud.list_osm_paths_in_box(42.4, 42.5, 0.9, 1.0, db)
            cache = analysis_crud.get_osm_cache_date(db)
        assert len(paths) == 1
        assert paths[0].tags["sac_scale"] == "T4"
        assert cache == date(2026, 11, 1)

    def test_a_way_far_from_the_area_is_not_offered_as_a_candidate(self, pg_factory):
        with pg_factory() as db:
            analysis_crud.replace_osm_paths([_way()], db)
            db.commit()
            nearby = analysis_crud.list_osm_paths_in_box(42.469, 42.471, 0.989, 0.991, db)
            elsewhere = analysis_crud.list_osm_paths_in_box(41.30, 41.32, 1.90, 1.92, db)
        assert [path.way_id for path in nearby] == [4242]
        assert elsewhere == []

    def test_a_stretch_matched_to_no_way_is_still_recorded_as_checked(self, pg_factory):
        """An unmapped gully is a result: a newer extract is what reopens it."""
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id = _run_activity(db)
            session, sections = _observation(sections=1)
            analysis_crud.replace_trail_observation(
                user_id=_USER_ID,
                activity_id=activity_id,
                method=_METHOD,
                observation=session,
                sections=sections,
                db=db,
            )
            db.commit()

            (row,) = analysis_crud.list_sections_needing_osm_match(_METHOD, date(2026, 11, 1), db, limit=10)
            assert row.osm_cache_date is None
            analysis_crud.set_trail_section_osm_match(row, None, date(2026, 11, 1), db)
            db.commit()

            still_open = analysis_crud.list_sections_needing_osm_match(_METHOD, date(2026, 11, 1), db, limit=10)
            reopened = analysis_crud.list_sections_needing_osm_match(_METHOD, date(2026, 12, 1), db, limit=10)
            stored = analysis_crud.list_trail_observation(activity_id, _METHOD, db)
        assert still_open == []
        assert [row.start_m for row in reopened] == [7675.0]
        assert stored is not None
        assert stored[1][0].osm_cache_date == date(2026, 11, 1)
        assert stored[1][0].osm_way_id is None

    def test_a_match_keeps_the_way_and_its_offsets(self, pg_factory):
        ensure_user(pg_factory, _USER_ID)
        with pg_factory() as db:
            activity_id = _run_activity(db)
            analysis_crud.replace_osm_paths([_way()], db)
            session, sections = _observation(sections=1)
            analysis_crud.replace_trail_observation(
                user_id=_USER_ID,
                activity_id=activity_id,
                method=_METHOD,
                observation=session,
                sections=sections,
                db=db,
            )
            db.commit()

            (row,) = analysis_crud.list_sections_needing_osm_match(_METHOD, date(2026, 11, 1), db, limit=10)
            analysis_crud.set_trail_section_osm_match(
                row,
                analysis_contracts.OsmMatchRecord(way_id=4242, start_m=300.0, end_m=1520.0, covered_m=1220.0),
                date(2026, 11, 1),
                db,
            )
            db.commit()

            stored = analysis_crud.list_trail_observation(activity_id, _METHOD, db)
            paths = analysis_crud.get_osm_paths_by_ids([4242, 999999], db)
        assert stored is not None
        assert stored[1][0].osm_way_id == 4242
        assert stored[1][0].osm_end_m == 1520.0
        assert list(paths) == [4242]
