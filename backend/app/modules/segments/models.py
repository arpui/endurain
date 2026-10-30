"""SQLAlchemy ORM models for segments and their per-activity efforts."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

import modules.segments.constants as segments_constants
from core.database import Base, TimestampMixin


class Segment(Base, TimestampMixin):
    """A named route section defined by an ordered polyline.

    Attributes:
        id: Surrogate primary key.
        name: Display name of the segment.
        source: Where the definition came from (``strava`` or ``manual``).
        source_id: External identifier within ``source`` (the Strava segment id);
            ``None`` for segments defined inside Endurain.
        created_by: User that created/imported the segment.
        distance_m: Segment length in meters along the stored geometry order.
        elevation_gain_m: Positive elevation gain along the geometry, when known.
        min_lat: South edge of the geometry bounding box (cheap pre-filter).
        min_lng: West edge of the geometry bounding box.
        max_lat: North edge of the geometry bounding box.
        max_lng: East edge of the geometry bounding box.
        geometry: Ordered ``[[lat, lng], ...]`` polyline; index 0 is the start
            of the canonical direction.
        altitudes: Per-point altitude in meters, index-aligned with
            ``geometry``, when the source provides it.
    """

    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_segments_source_source_id"),
        CheckConstraint(
            f"source IN ('{segments_constants.SegmentSource.STRAVA}', '{segments_constants.SegmentSource.MANUAL}')",
            name="ck_segments_source",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        comment="Segment display name",
    )
    source: Mapped[str] = mapped_column(
        String(length=20),
        nullable=False,
        default=segments_constants.SegmentSource.STRAVA.value,
        server_default=segments_constants.SegmentSource.STRAVA.value,
        comment="Definition origin: strava or manual",
    )
    source_id: Mapped[str | None] = mapped_column(
        String(length=64),
        nullable=True,
        comment="External identifier within the source (e.g. Strava segment id)",
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="ID of the user that created or imported the segment",
    )
    distance_m: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Segment length in meters along the stored geometry order",
    )
    elevation_gain_m: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Positive elevation gain in meters, when the source provides it",
    )
    altitudes: Mapped[list[float] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Per-point altitude in meters, index-aligned with geometry, when the source provides it",
    )
    min_lat: Mapped[float] = mapped_column(Float, nullable=False, comment="Bounding box south edge")
    min_lng: Mapped[float] = mapped_column(Float, nullable=False, comment="Bounding box west edge")
    max_lat: Mapped[float] = mapped_column(Float, nullable=False, comment="Bounding box north edge")
    max_lng: Mapped[float] = mapped_column(Float, nullable=False, comment="Bounding box east edge")
    geometry: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        comment="Ordered polyline as [[lat, lng], ...]; index 0 is the canonical start",
    )


class SegmentEffort(Base, TimestampMixin):
    """One pass of one activity over one segment.

    An effort is the result of matching an activity's GPS stream against the
    segment geometry: the effort spans the stream waypoints from
    ``stream_index_start`` to ``stream_index_end`` (inclusive). ``time_start``
    and ``time_end`` are the wall-clock times of those waypoints, stored naive
    exactly as the stream records them; ``elapsed_seconds`` is their difference.

    Attributes:
        id: Surrogate primary key.
        segment_id: The segment traversed.
        activity_id: The activity that traversed it.
        direction: ``forward`` (counts as an effort) or ``reverse`` (informational).
        pass_number: 1-based index of this pass among the activity's matching
            passes over the segment in this direction, in chronological order.
        elapsed_seconds: Effort duration.
        avg_speed_mps: Segment distance over elapsed time, when computable.
        coverage: Fraction of distinct segment points the pass touched (audit).
        time_start: Wall-clock time of the first effort waypoint (naive).
        time_end: Wall-clock time of the last effort waypoint (naive).
        stream_index_start: Index of the first effort waypoint in the GPS stream.
        stream_index_end: Index of the last effort waypoint in the GPS stream.
    """

    __tablename__ = "segment_efforts"
    __table_args__ = (
        UniqueConstraint(
            "segment_id",
            "activity_id",
            "direction",
            "pass_number",
            name="uq_segment_efforts_segment_activity_direction_pass",
        ),
        CheckConstraint(
            f"direction IN ('{segments_constants.SegmentDirection.FORWARD}', "
            f"'{segments_constants.SegmentDirection.REVERSE}')",
            name="ck_segment_efforts_direction",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        index=True,
    )
    segment_id: Mapped[int] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="ID of the segment traversed",
    )
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="ID of the activity that traversed the segment",
    )
    direction: Mapped[str] = mapped_column(
        String(length=10),
        nullable=False,
        comment="forward (counts as an effort) or reverse (informational only)",
    )
    pass_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="1-based pass index within (segment, activity, direction)",
    )
    elapsed_seconds: Mapped[int] = mapped_column(Integer, nullable=False, comment="Effort duration in seconds")
    avg_speed_mps: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Segment distance over elapsed time in m/s",
    )
    coverage: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Fraction of distinct segment points touched by the pass (audit)",
    )
    time_start: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        comment="Wall-clock time of the first effort waypoint (naive, as the stream stores it)",
    )
    time_end: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        comment="Wall-clock time of the last effort waypoint (naive, as the stream stores it)",
    )
    stream_index_start: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Index of the first effort waypoint in the activity GPS stream",
    )
    stream_index_end: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Index of the last effort waypoint in the activity GPS stream (inclusive)",
    )
