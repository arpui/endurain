"""Trail observation rows against a real PostgreSQL database.

The claims that matter here are storage claims a mocked test cannot see: measuring
the same activity twice must not accumulate stretches, and measuring it under a
different set of detector parameters must keep the older observation readable -
that coexistence is what lets a calibration be compared against what the same
ground measured before. The composite foreign key from stretches to their session
row is also only exercised by a real database.
"""

from datetime import UTC, datetime

import modules.activities.activity.contracts as activity_contracts
import modules.activities.activity.crud as activity_crud
import modules.analysis.constants as analysis_constants
import modules.analysis.contracts as analysis_contracts
import modules.analysis.crud as analysis_crud
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
