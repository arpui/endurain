"""Subscriber registration and reconciliation for the analysis module."""

from jasil.jobs.reconciliation import DurableSubscriberNet
from jasil.jobs.registry import JobHandlerRegistry
from jasil.providers import EventBusProvider

import modules.analysis.scheduled_jobs as analysis_scheduled_jobs
import modules.analysis.subscribers as analysis_subscribers


def register_bus_subscribers(events: EventBusProvider) -> None:
    """Register analysis bus subscribers.

    Args:
        events: The event bus to subscribe on.
    """
    analysis_subscribers.register_analysis_subscribers(events)


def register_durable_handlers(registry: JobHandlerRegistry) -> None:
    """Register analysis durable handlers.

    Args:
        registry: The durable-handler registry to register on.
    """
    analysis_subscribers.register_analysis_durable_handlers(registry)


DURABLE_SUBSCRIBER_NETS: tuple[DurableSubscriberNet, ...] = (
    DurableSubscriberNet(
        analysis_subscribers.ANALYSIS_LOAD_SUBSCRIBER_ID,
        analysis_scheduled_jobs.reconcile_missing_loads,
    ),
    DurableSubscriberNet(
        analysis_subscribers.ANALYSIS_UPDATE_SUBSCRIBER_ID,
        None,
        exempt_reason=(
            "A re-measure after an edit has nothing to backfill to: the hourly pass "
            "measures every activity that carries no load at all, and a threshold "
            "change queues an explicit full recompute, which is the only case where "
            "already-measured activities go stale."
        ),
    ),
)
