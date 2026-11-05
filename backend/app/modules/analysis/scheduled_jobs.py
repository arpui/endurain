"""Scheduled work owned by the analysis module."""

import functools

import core.database as core_database
import core.logger as core_logger
import core.scheduler as core_scheduler
import modules.analysis.service as analysis_service

logger = core_logger.get_logger(__name__)


def reconcile_missing_loads() -> None:
    """Measure every activity that has no stored load yet.

    Scheduler-invoked, with no request to carry a session, so this entry point opens
    it. The backfill for the ``analysis.compute_loads`` net: catches activities whose
    ``activity.created`` handling was missed or failed.
    """
    with core_database.SessionLocal() as db:
        analysis_service.reconcile_missing_loads(db)


def recompute_all_loads() -> None:
    """Re-measure every activity with the current thresholds and formulas.

    Expensive relative to the reconcile pass (no activities are skipped), so it is not
    scheduled: it runs when an athlete changes a threshold or a formula is added.
    """
    with core_database.SessionLocal() as db:
        analysis_service.recompute_all_loads(db)


def reconcile_trail_sections() -> None:
    """Snapshot the technical stretches of every run activity that has none.

    Scheduler-invoked, so this entry point opens its own session. The trail
    detector's answer is always re-measured when the UI reads it; these rows are
    the evidence kept alongside it, and they are what lets a calibration be judged
    afterwards against what the same ground measured before.
    """
    with core_database.SessionLocal() as db:
        analysis_service.reconcile_trail_sections(db)


def reconcile_way_crossings() -> None:
    """Match every run activity's whole track against the mapped ways.

    Technical stretches alone would make the network show only remarkable
    ground; the paths between them are the network, and they come from this
    coverage pass. Scheduler-invoked, so it opens its own session.
    """
    with core_database.SessionLocal() as db:
        analysis_service.reconcile_way_crossings(db)


def match_trail_cartography() -> None:
    """Say which mapped way each measured technical stretch lies on.

    Scheduler-invoked, so it opens its own session. It has nothing to do until an
    extract has been imported (``devscripts/osm-trail-cache.py``), and afterwards it
    only handles stretches not yet matched against that extract's date.
    """
    with core_database.SessionLocal() as db:
        analysis_service.match_trail_sections_to_osm(db)


def refresh_user_thresholds(user_id: int) -> None:
    """Re-derive one athlete's thresholds and refresh their duration peaks.

    Args:
        user_id: The athlete whose records are read.
    """
    with core_database.SessionLocal() as db:
        analysis_service.refresh_thresholds(user_id, db)


def recurring_jobs() -> tuple[core_scheduler.ScheduledJob, ...]:
    """Return the analysis reconciliation jobs.

    Returns:
        The jobs to schedule.
    """
    return (
        core_scheduler.ScheduledJob(
            reconcile_missing_loads,
            60,
            "measure activities without stored loads",
        ),
        core_scheduler.ScheduledJob(
            reconcile_trail_sections,
            3600,
            "observe run activities the trail detector has never measured",
        ),
        core_scheduler.ScheduledJob(
            match_trail_cartography,
            3600,
            "match measured stretches to the imported OpenStreetMap extract",
        ),
        core_scheduler.ScheduledJob(
            reconcile_way_crossings,
            3600,
            "cover run activities' full tracks against the mapped ways",
        ),
    )


def schedule_analysis_reconcile() -> None:
    """Queue one analysis reconciliation pass (startup catch-up)."""
    core_scheduler.run_once(
        reconcile_missing_loads,
        job_id="endurain_reconcile_analysis_loads_oneshot",
        description="analysis load reconciliation",
    )


def schedule_analysis_recompute() -> None:
    """Queue one full re-measure of every activity."""
    core_scheduler.run_once(
        recompute_all_loads,
        job_id="endurain_recompute_analysis_loads_oneshot",
        description="analysis load recompute",
    )


def schedule_analysis_threshold_refresh(user_id: int) -> None:
    """Queue re-deriving one athlete's thresholds from their own records.

    Args:
        user_id: The athlete who asked.
    """
    core_scheduler.run_once(
        functools.partial(refresh_user_thresholds, user_id),
        job_id=f"endurain_refresh_analysis_thresholds_{user_id}_oneshot",
        description="analysis threshold refresh",
    )
