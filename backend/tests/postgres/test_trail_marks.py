"""Trail verdicts against a real PostgreSQL database.

The claims that matter here are storage claims: re-marking a stretch must replace
rather than accumulate, an unmark must actually remove, and one athlete's verdicts
must never surface for another - the check constraint on the verdict is also only
exercised by a real database.
"""

from datetime import UTC, datetime

import modules.activities.activity.contracts as activity_contracts
import modules.activities.activity.crud as activity_crud
import modules.analysis.constants as analysis_constants
import modules.analysis.crud as analysis_crud
from tests._helpers.db import ensure_user

_T0 = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
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


def _run_activity(db, user_id: int) -> int:
    """Create one trail-run activity owned by ``user_id`` and return its id."""
    activity = activity_crud.create_activity(
        activity_contracts.ActivityCore(
            user_id=user_id,
            name="Cabdella",
            distance=18_300,
            activity_type=2,
            start_time=_T0,
            end_time=_T0.replace(hour=11),
            total_elapsed_time=7200.0,
            total_timer_time=7000.0,
            **{flag: False for flag in _PRIVACY_FLAGS},
        ),
        db,
    )
    return activity.id


class TestTrailMarks:
    def test_a_verdict_round_trips_and_keeps_ground_order(self, pg_factory):
        ensure_user(pg_factory, 2)
        with pg_factory() as db:
            activity_id = _run_activity(db, 2)
            analysis_crud.replace_trail_mark(2, activity_id, 7675.0, "agree", db)
            analysis_crud.replace_trail_mark(2, activity_id, 9600.0, "wrong", db)
            db.commit()

            marks = analysis_crud.list_trail_marks(activity_id, 2, db)
        assert [(mark.start_m, mark.verdict) for mark in marks] == [(7675.0, "agree"), (9600.0, "wrong")]

    def test_re_marking_a_stretch_replaces_instead_of_accumulating(self, pg_factory):
        ensure_user(pg_factory, 2)
        with pg_factory() as db:
            activity_id = _run_activity(db, 2)
            analysis_crud.replace_trail_mark(2, activity_id, 7675.0, "agree", db)
            db.commit()
            analysis_crud.replace_trail_mark(2, activity_id, 7675.0, "overstated", db)
            db.commit()

            marks = analysis_crud.list_trail_marks(activity_id, 2, db)
        assert len(marks) == 1
        assert marks[0].verdict == "overstated"

    def test_a_verdict_can_be_taken_back_once(self, pg_factory):
        ensure_user(pg_factory, 2)
        with pg_factory() as db:
            activity_id = _run_activity(db, 2)
            analysis_crud.replace_trail_mark(2, activity_id, 7675.0, "wrong", db)
            db.commit()

            removed = analysis_crud.delete_trail_mark(2, activity_id, 7675.0, db)
            db.commit()
            again = analysis_crud.delete_trail_mark(2, activity_id, 7675.0, db)
            db.commit()
            marks = analysis_crud.list_trail_marks(activity_id, 2, db)
        assert removed is True
        assert again is False
        assert marks == []

    def test_another_athlete_never_sees_them(self, pg_factory):
        ensure_user(pg_factory, 2)
        ensure_user(pg_factory, 3)
        with pg_factory() as db:
            activity_id = _run_activity(db, 2)
            analysis_crud.replace_trail_mark(2, activity_id, 7675.0, "agree", db)
            db.commit()

            theirs = analysis_crud.list_trail_marks(activity_id, 3, db)
        assert theirs == []

    def test_the_verdict_vocabulary_is_the_same_as_the_constraint(self, pg_factory):
        """The schema cannot drift from the words the service writes."""
        assert set(analysis_constants.TRAIL_VERDICTS) == {
            "agree",
            "overstated",
            "wrong",
        }
        ensure_user(pg_factory, 2)
        with pg_factory() as db:
            activity_id = _run_activity(db, 2)
            analysis_crud.replace_trail_mark(2, activity_id, 100.0, "overstated", db)
            db.commit()
            assert analysis_crud.list_trail_marks(activity_id, 2, db)[0].verdict == "overstated"
