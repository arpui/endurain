"""Event subscribers wiring segment matching to activity lifecycle events.

Registered once at startup (before the event bus starts). In the ``local``
profile the in-process bus runs these inline, synchronously, before the
producing request returns; in a ``distributed`` profile a Redis-Streams
consumer runs them later, at-least-once. Either way the scheduled reconcile
(:func:`modules.segments.scheduled_jobs.reconcile_missing_segment_efforts`) is
the safety net for the create path.

The ``*_for_event`` core **raises** on failure so the durable-job runner can
retry and eventually dead-letter it; the ``on_*`` bus subscriber wraps it and
**swallows** errors so a matching failure never breaks activity import.
"""

import jasil.event_versioning as platform_event_versioning
from jasil.events import Event
from jasil.jobs.registry import JobHandlerRegistry
from jasil.providers import EventBusProvider
from jasil.subscribers import best_effort

import core.database as core_database
import core.logger as core_logger
import modules.activities.activity.events as activity_events
import modules.segments.service as segments_service

logger = core_logger.get_logger(__name__)

# Stable durable-subscriber id (independent of module path) so job history and
# dedup survive refactors.
SEGMENTS_MATCH_SUBSCRIBER_ID = "segments.match_activity"


def match_segments_for_event(event: Event) -> None:
    """Match a newly created activity against the segment library; raises on failure.

    The durable-job handler for ``activity.created``: any error propagates so
    the runner retries and eventually dead-letters the job. Activities without
    a GPS stream match nothing and cost one short query.

    Args:
        event: The ``activity.created`` event (payload carries ``activity_id``).
    """
    payload = platform_event_versioning.parse_payload(activity_events.ActivityCreatedPayload, event)
    with core_database.SessionLocal() as db:
        efforts = segments_service.match_activity(payload.activity_id, db)
    logger.debug(
        "Handled activity.created for segment matching",
        extra=core_logger.context(activity_id=payload.activity_id, efforts=efforts),
    )


# Bus subscriber: matches segments, swallowing any error so a matching failure
# never breaks activity import (the hourly reconcile retries the activity).
on_activity_created_match_segments = best_effort(match_segments_for_event)


def register_segments_subscribers(events: EventBusProvider) -> None:
    """Register segment-matching bus subscribers.

    Args:
        events: The event bus to subscribe on.
    """
    events.subscribe(activity_events.ACTIVITY_CREATED, on_activity_created_match_segments)


def register_segments_durable_handlers(registry: JobHandlerRegistry) -> None:
    """Register segment-matching durable handlers.

    Args:
        registry: The durable-handler registry to register on.
    """
    registry.register(activity_events.ACTIVITY_CREATED, SEGMENTS_MATCH_SUBSCRIBER_ID, match_segments_for_event)
