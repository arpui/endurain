"""Scheduled work owned by the segments module."""

import core.database as core_database
import core.logger as core_logger
import core.scheduler as core_scheduler
import modules.segments.service as segments_service

logger = core_logger.get_logger(__name__)


def reconcile_missing_segment_efforts() -> None:
    """Match every activity that has no stored segment efforts yet.

    Scheduler-invoked, with no request to carry a session, so this entry point
    opens it. The backfill for the ``segments.match_activity`` net: catches
    activities whose ``activity.created`` handling was missed or failed.
    """
    with core_database.SessionLocal() as db:
        segments_service.reconcile_missing_efforts(db)


def rescan_all_segment_efforts() -> None:
    """Re-match every activity against the current segment library.

    Expensive relative to the reconcile pass (no activities are skipped), so it
    is not scheduled: it runs when an operator queues it or after an import
    needs existing activities re-evaluated against a new segment.
    """
    with core_database.SessionLocal() as db:
        segments_service.rescan_all_efforts(db)


def recurring_jobs() -> tuple[core_scheduler.ScheduledJob, ...]:
    """Return the segments reconciliation jobs.

    Returns:
        The jobs to schedule.
    """
    return (
        core_scheduler.ScheduledJob(
            reconcile_missing_segment_efforts,
            60,
            "match segments for activities without stored efforts",
        ),
    )


def schedule_segments_reconcile() -> None:
    """Queue one segment reconciliation pass (startup catch-up)."""
    core_scheduler.run_once(
        reconcile_missing_segment_efforts,
        job_id="endurain_reconcile_segment_efforts_oneshot",
        description="segment effort reconciliation",
    )


def schedule_segments_rescan() -> None:
    """Queue one full re-match of every activity."""
    core_scheduler.run_once(
        rescan_all_segment_efforts,
        job_id="endurain_rescan_segment_efforts_oneshot",
        description="segment effort rescan",
    )
