"""ORM↔schema transformation for activity streams.

The counterpart of :mod:`modules.activities.activity.serializers`, and named to
match: ``crud`` calls the transformation at its edges so ORM rows never leave the
persistence layer. Visibility masking is an access decision and lives in
:mod:`modules.activities.activity_streams.service`. Stryd-era power is
recalibrated onto the athlete's native scale here — the single point every
stream read (client charts, analysis derivation, segment enrichment, profile
export) flows through — leaving the stored waypoints raw.
"""

from typing import overload

import modules.activities.activity_streams.constants as activity_streams_constants
import modules.activities.activity_streams.models as activity_streams_models
import modules.activities.activity_streams.schema as activity_streams_schema


def _calibrated_waypoints(
    stream: activity_streams_models.ActivityStreams,
) -> list[dict]:
    """Return the stream's waypoints with stryd power scaled to the native scale.

    Args:
        stream: The power-stream ORM row (already known to be a power stream).

    Returns:
        A fresh list of waypoint dicts with the ``power`` values multiplied by
        :data:`~modules.activities.activity_streams.constants.STRYD_POWER_SCALE`
        when the row is tagged stryd; the stored dicts are never mutated.
    """
    raw = stream.stream_waypoints or []
    if stream.power_source != activity_streams_constants.POWER_SOURCE_STRYD:
        return [dict(point) for point in raw]
    scale = activity_streams_constants.STRYD_POWER_SCALE
    calibrated = []
    for point in raw:
        copied = dict(point)
        if copied.get("power") is not None:
            copied["power"] = round(float(copied["power"]) * scale, 1)
        calibrated.append(copied)
    return calibrated


@overload
def to_read_schema(
    activity_streams: list[activity_streams_models.ActivityStreams],
) -> list[activity_streams_schema.ActivityStreamsRead]: ...


@overload
def to_read_schema(
    activity_streams: activity_streams_models.ActivityStreams,
) -> activity_streams_schema.ActivityStreamsRead: ...


def to_read_schema(
    activity_streams: activity_streams_models.ActivityStreams | list[activity_streams_models.ActivityStreams],
) -> activity_streams_schema.ActivityStreamsRead | list[activity_streams_schema.ActivityStreamsRead]:
    """Transform a stream or list of streams to a Pydantic schema or list of schemas.

    Power streams come out recalibrated to the athlete's native scale when their
    row is tagged stryd; every consumer of power — clients, analysis, segments —
    therefore reads one consistent scale.

    Args:
        activity_streams: The stream ORM instance or list of stream ORM instances.

    Returns:
        The activity stream as a schema or list of schemas.
    """

    def one(stream: activity_streams_models.ActivityStreams) -> activity_streams_schema.ActivityStreamsRead:
        read = activity_streams_schema.ActivityStreamsRead.model_validate(stream)
        if read.stream_type == activity_streams_constants.STREAM_TYPE_POWER:
            read.stream_waypoints = _calibrated_waypoints(stream)
        return read

    if isinstance(activity_streams, list):
        return [one(stream) for stream in activity_streams]
    return one(activity_streams)
