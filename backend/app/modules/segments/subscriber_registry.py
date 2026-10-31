"""Subscriber registration and reconciliation for segments."""

from jasil.jobs.reconciliation import DurableSubscriberNet
from jasil.jobs.registry import JobHandlerRegistry
from jasil.providers import EventBusProvider

import modules.segments.scheduled_jobs as segments_scheduled_jobs
import modules.segments.subscribers as segments_subscribers


def register_bus_subscribers(events: EventBusProvider) -> None:
    """Register segments bus subscribers.

    Args:
        events: The event bus to subscribe on.
    """
    segments_subscribers.register_segments_subscribers(events)


def register_durable_handlers(registry: JobHandlerRegistry) -> None:
    """Register segments durable handlers.

    Args:
        registry: The durable-handler registry to register on.
    """
    segments_subscribers.register_segments_durable_handlers(registry)


DURABLE_SUBSCRIBER_NETS: tuple[DurableSubscriberNet, ...] = (
    DurableSubscriberNet(
        segments_subscribers.SEGMENTS_MATCH_SUBSCRIBER_ID,
        segments_scheduled_jobs.reconcile_missing_segment_efforts,
    ),
)
