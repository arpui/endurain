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
