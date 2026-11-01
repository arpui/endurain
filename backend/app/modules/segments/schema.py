"""Pydantic schemas for segments and segment efforts."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

import core.pagination as core_pagination
import modules.segments.constants as segments_constants


class EffortDirectionFilter(StrEnum):
    """Query vocabulary selecting which effort directions to read.

    ``ALL`` exists because a segment's stored reverse passes are real traverses
    in the opposite direction, and a caller that wants them must ask: the
    library pages default to ``FORWARD``, the direction that counts.

    Attributes:
        FORWARD: Only efforts that follow the stored geometry order.
        REVERSE: Only efforts against it.
        ALL: Both directions.
    """

    FORWARD = "forward"
    REVERSE = "reverse"
    ALL = "all"


class SegmentImport(BaseModel):
    """Body of a segment import (the shape of a Fase-0 Strava artifact).

    Attributes:
        segment_id: External (Strava) segment identifier.
        name: Display name.
        latlng: Ordered ``[[lat, lng], ...]`` polyline from the segment stream.
        altitude: Per-point altitude in meters, when the source provides it.
        distance: Per-point cumulative distance in meters, when provided; the
            last value is used as the segment length, otherwise it is summed
            from the geometry.
        source: Definition origin; ``strava`` for imported segments.
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: StrictInt
    name: str = Field(min_length=1, max_length=255)
    latlng: list[list[StrictFloat]] = Field(min_length=2)
    altitude: list[StrictFloat] | None = None
    distance: list[StrictFloat] | None = None
    source: segments_constants.SegmentSource = segments_constants.SegmentSource.STRAVA

    @field_validator("latlng")
    @classmethod
    def _latlng_pairs(cls, value: list[list[StrictFloat]]) -> list[list[StrictFloat]]:
        """Reject any entry that is not a ``[lat, lng]`` pair.

        Args:
            value: The submitted polyline.

        Returns:
            The polyline, unchanged.

        Raises:
            ValueError: If an entry is not a two-element pair.
        """
        if any(len(point) != 2 for point in value):
            raise ValueError("latlng entries must be [lat, lng] pairs")
        return value


class SegmentRead(BaseModel):
    """Serialized segment without its geometry.

    Attributes:
        id: Segment identifier.
        name: Display name.
        source: Definition origin.
        source_id: External identifier within the source, or ``None``.
        distance_m: Length along the stored geometry.
        elevation_gain_m: Positive elevation gain, when known.
        elevation_loss_m: Positive elevation dropped along the geometry, read from
            the stored altitudes; ``None`` when the source provided none.
        created_by: Importing/creating user, when known.
        created_at: Creation timestamp.
        updated_at: Last-update timestamp.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: StrictInt
    name: StrictStr
    source: segments_constants.SegmentSource
    source_id: str | None
    distance_m: StrictFloat
    elevation_gain_m: StrictFloat | None
    elevation_loss_m: StrictFloat | None = None
    created_by: StrictInt | None
    created_at: datetime
    updated_at: datetime


class SegmentListItemRead(SegmentRead):
    """Serialized segment as the library list shows it.

    The effort count is scoped to whoever is reading, because the library is
    shared while "how many times have I done this" is not.

    Attributes:
        effort_count: The reader's counted (forward) efforts on this segment.
    """

    effort_count: StrictInt = 0


class SegmentDetailRead(SegmentRead):
    """Serialized segment including its geometry and bounding box.

    Attributes:
        min_lat: Bounding box south edge.
        min_lng: Bounding box west edge.
        max_lat: Bounding box north edge.
        max_lng: Bounding box east edge.
        geometry: Ordered ``[[lat, lng], ...]`` polyline.
        altitudes: Per-point altitude in meters, index-aligned with
            ``geometry``, when the source provided it.
    """

    min_lat: StrictFloat
    min_lng: StrictFloat
    max_lat: StrictFloat
    max_lng: StrictFloat
    geometry: list[list[StrictFloat]]
    altitudes: list[StrictFloat] | None


class SegmentEffortRead(BaseModel):
    """Serialized effort of one activity over one segment.

    Attributes:
        segment_id: Segment traversed.
        activity_id: Activity that traversed it.
        direction: ``forward`` (counts) or ``reverse`` (informational).
        pass_number: 1-based pass index within (segment, activity, direction).
        elapsed_seconds: Effort duration.
        avg_speed_mps: Segment distance over elapsed time.
        coverage: Fraction of distinct segment points touched.
        time_start: Wall-clock time of the first effort waypoint.
        time_end: Wall-clock time of the last effort waypoint.
        stream_index_start: GPS-stream index of the first effort waypoint.
        stream_index_end: GPS-stream index of the last effort waypoint.
        activity_type: Sport of the effort's activity, so the client can choose
            pace or speed for the tempo column; filled on read, ``None`` if the
            activity could not be resolved.
        avg_power_w: Mean power over the effort's window, ``None`` without a
            power stream; filled on read from the activity's own samples.
        avg_hr_bpm: Mean heart rate over the effort's window, dropouts excluded,
            ``None`` without a heart-rate stream.
        rss_per_min: The shared aRSS score per minute over the effort's window,
            under the requester's threshold for the sport; ``None`` without
            power, a threshold or gained stress.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    segment_id: StrictInt
    activity_id: StrictInt
    direction: segments_constants.SegmentDirection
    pass_number: StrictInt
    elapsed_seconds: StrictInt
    avg_speed_mps: StrictFloat | None
    coverage: StrictFloat | None
    time_start: datetime
    time_end: datetime
    stream_index_start: StrictInt
    stream_index_end: StrictInt
    activity_type: StrictInt | None = None
    avg_power_w: StrictFloat | None = None
    avg_hr_bpm: StrictFloat | None = None
    rss_per_min: StrictFloat | None = None


class ActivitySegmentRead(BaseModel):
    """One segment with the efforts one activity made over it.

    Attributes:
        segment: The segment definition (without geometry).
        efforts: The activity's passes, chronological.
        best_elapsed_seconds: The reader's fastest forward effort on this segment
            across all their activities, or ``None`` when they have none. It lets
            an activity say whether this pass is a personal record.
        best_activity_id: Activity holding that best effort, or ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    segment: SegmentRead
    efforts: list[SegmentEffortRead]
    best_elapsed_seconds: StrictInt | None
    best_activity_id: StrictInt | None


class BackfillQueued(BaseModel):
    """Response of a backfill request.

    Attributes:
        queued: Always ``True``: the rescan runs off the request path.
    """

    model_config = ConfigDict(extra="forbid")

    queued: StrictBool


#: Page envelopes matching the rest of the API. The library page carries the
#: reader's effort count, so its item type is the list schema, not the plain one.
SegmentPage = core_pagination.Page[SegmentListItemRead]
SegmentEffortPage = core_pagination.Page[SegmentEffortRead]
