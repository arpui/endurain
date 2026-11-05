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
