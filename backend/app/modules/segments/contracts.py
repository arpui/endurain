"""Internal contracts consumed across the segments module's layers.

``SegmentRecord`` is what ``service`` computes from an import request and hands
to ``crud`` to persist; ``SegmentMatch`` is what the matcher emits and the
service turns into efforts. Both stay module-internal: what other modules may
consume goes through ``integration_service``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import modules.segments.constants as segments_constants


@dataclass(frozen=True)
class SegmentRecord:
    """A validated segment definition ready to persist.

    Attributes:
        name: Display name.
        source: Definition origin.
        source_id: External identifier within the source, or ``None``.
        distance_m: Length along the geometry.
        elevation_gain_m: Positive elevation gain, when known.
        altitudes: Per-point altitude, index-aligned with ``geometry``, when known.
        min_lat: Bounding box south edge.
        min_lng: Bounding box west edge.
        max_lat: Bounding box north edge.
        max_lng: Bounding box east edge.
        geometry: Ordered ``[[lat, lng], ...]`` polyline.
    """

    name: str
    source: segments_constants.SegmentSource
    source_id: str | None
    distance_m: float
    elevation_gain_m: float | None
    altitudes: Sequence[float] | None
    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float
    geometry: Sequence[Sequence[float]]


@dataclass(frozen=True)
class SegmentMatchTarget:
    """A stored segment reduced to what the matcher reads.

    A service must not hold a live ORM row, so ``crud`` hands out this DTO
    instead of the model.

    Attributes:
        id: Segment identifier, echoed into each match.
        distance_m: Length along the geometry, used for average speed.
        geometry: Ordered ``[[lat, lng], ...]`` polyline.
    """

    id: int
    distance_m: float
    geometry: Sequence[Sequence[float]]


@dataclass(frozen=True)
class SegmentMatch:
    """One matching pass of an activity over a segment.

    Attributes:
        segment_id: The segment the pass ran over.
        direction: ``forward`` or ``reverse`` relative to the geometry order.
        pass_number: 1-based index among passes in this direction.
        elapsed_seconds: Time between the bracket waypoints.
        avg_speed_mps: Segment distance over elapsed time.
        coverage: Fraction of distinct segment points touched.
        span: Fraction of the segment index range the pass extended over.
        time_start: Wall-clock time of the first bracket waypoint (naive).
        time_end: Wall-clock time of the last bracket waypoint (naive).
        stream_index_start: GPS-stream index of the first bracket waypoint.
        stream_index_end: GPS-stream index of the last bracket waypoint (inclusive).
    """

    segment_id: int
    direction: segments_constants.SegmentDirection
    pass_number: int
    elapsed_seconds: int
    avg_speed_mps: float | None
    coverage: float
    span: float
    time_start: datetime
    time_end: datetime
    stream_index_start: int
    stream_index_end: int
