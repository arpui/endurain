"""Event subscribers wiring load computation to the activity lifecycle.

Registered once at startup (before the event bus starts). In the ``local`` profile
the in-process bus runs these inline, synchronously, before the producing request
returns; in a ``distributed`` profile a Redis-Streams consumer runs them later,
at-least-once. Either way the scheduled reconcile
(:func:`modules.analysis.scheduled_jobs.reconcile_missing_loads`) is the safety net.

The ``*_for_event`` core **raises** on failure so the durable-job runner can retry
and eventually dead-letter it; the ``on_*`` bus subscriber wraps it and **swallows**
errors so a measurement failure never breaks activity import.
"""

import jasil.event_versioning as platform_event_versioning
from jasil.events import Event
from jasil.jobs.registry import JobHandlerRegistry
from jasil.providers import EventBusProvider
from jasil.subscribers import best_effort

import core.database as core_database
import core.logger as core_logger
import modules.activities.activity.events as activity_events
import modules.analysis.service as analysis_service

logger = core_logger.get_logger(__name__)

# Stable durable-subscriber ids (independent of module path) so job history and
# dedup survive refactors.
ANALYSIS_LOAD_SUBSCRIBER_ID = "analysis.compute_loads"
ANALYSIS_UPDATE_SUBSCRIBER_ID = "analysis.recompute_loads"

#: Activity columns whose change invalidates a stored load: the time a load scales
#: with, the sport that decides its threshold, the summaries the heart-rate and
#: power formulas read, and what decides the calendar day the load lands on
#: (``load_date`` is copied at measure time). Anything else (a name, a gear,
#: notes) leaves the load alone.
_LOAD_BEARING_FIELDS = frozenset(
    {
        "total_timer_time",
        "total_elapsed_time",
        "activity_type",
        "average_hr",
        "max_hr",
        "average_power",
        "normalized_power",
        "start_time",
        "timezone",
    }
)


def compute_loads_for_event(event: Event) -> None:
    """Measure a newly created activity; raises on failure.

    Args:
        event: The ``activity.created`` event (payload carries ``activity_id``).
    """
    payload = platform_event_versioning.parse_payload(activity_events.ActivityCreatedPayload, event)
    with core_database.SessionLocal() as db:
        loads = analysis_service.compute_loads_for_activity(payload.activity_id, db)
    logger.debug(
        "Handled activity.created for load analysis",
        extra=core_logger.context(activity_id=payload.activity_id, loads=loads),
    )


def recompute_loads_for_event(event: Event) -> None:
    """Re-measure an activity whose load-bearing fields changed; raises on failure.

    Activities whose edit touched nothing a formula reads are ignored, so renaming an
    activity or reassigning its gear does not walk its streams again.

    Args:
        event: The ``activity.updated`` event.
    """
    payload = platform_event_versioning.parse_payload(activity_events.ActivityUpdatedPayload, event)
    if not _LOAD_BEARING_FIELDS.intersection(payload.changed_fields):
        return
    with core_database.SessionLocal() as db:
        loads = analysis_service.compute_loads_for_activity(payload.activity_id, db)
    logger.debug(
        "Handled activity.updated for load analysis",
        extra=core_logger.context(activity_id=payload.activity_id, loads=loads, changed=sorted(payload.changed_fields)),
    )


# Bus subscribers: measurement failures never break activity import (the hourly
# reconcile retries the activity).
on_activity_created_compute_loads = best_effort(compute_loads_for_event)
on_activity_updated_recompute_loads = best_effort(recompute_loads_for_event)


def register_analysis_subscribers(events: EventBusProvider) -> None:
    """Register analysis bus subscribers.

    Args:
        events: The event bus to subscribe on.
    """
    events.subscribe(activity_events.ACTIVITY_CREATED, on_activity_created_compute_loads)
    events.subscribe(activity_events.ACTIVITY_UPDATED, on_activity_updated_recompute_loads)


def register_analysis_durable_handlers(registry: JobHandlerRegistry) -> None:
    """Register analysis durable handlers.

    Args:
        registry: The durable-handler registry to register on.
    """
    registry.register(activity_events.ACTIVITY_CREATED, ANALYSIS_LOAD_SUBSCRIBER_ID, compute_loads_for_event)
    registry.register(
        activity_events.ACTIVITY_UPDATED,
        ANALYSIS_UPDATE_SUBSCRIBER_ID,
        recompute_loads_for_event,
    )
