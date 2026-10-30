"""CRUD operations for segments and segment efforts."""

from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import core.logger as core_logger
import modules.segments.contracts as segments_contracts
import modules.segments.models as segments_models
import modules.segments.schema as segments_schema

logger = core_logger.get_logger(__name__)


def create_segment(
    record: segments_contracts.SegmentRecord,
    created_by: int | None,
    db: Session,
) -> segments_models.Segment:
    """Insert one segment definition.

    Flushes without committing: the service owns the transaction.

    Args:
        record: The validated definition.
        created_by: Importing/creating user id, when known.
        db: Database session.

    Returns:
        The persisted segment.
    """
    segment = segments_models.Segment(
        name=record.name,
        source=record.source.value,
        source_id=record.source_id,
        created_by=created_by,
        distance_m=record.distance_m,
        elevation_gain_m=record.elevation_gain_m,
        altitudes=[float(value) for value in record.altitudes] if record.altitudes is not None else None,
        min_lat=record.min_lat,
        min_lng=record.min_lng,
        max_lat=record.max_lat,
        max_lng=record.max_lng,
        geometry=[list(point) for point in record.geometry],
    )
    db.add(segment)
    db.flush()
    return segment


def get_segment_by_id(segment_id: int, db: Session) -> segments_models.Segment | None:
    """Get a segment by primary key.

    Args:
        segment_id: The segment identifier.
        db: Database session.

    Returns:
        The segment, or ``None`` when it does not exist.
    """
    return db.get(segments_models.Segment, segment_id)


def get_segment_by_source_id(
    source: str,
    source_id: str,
    db: Session,
) -> segments_models.Segment | None:
    """Get a segment by its external identifier within a source.

    Args:
        source: The definition origin.
        source_id: The external identifier.
        db: Database session.

    Returns:
        The segment, or ``None`` when it does not exist.
    """
    stmt = select(segments_models.Segment).where(
        segments_models.Segment.source == source,
        segments_models.Segment.source_id == source_id,
    )
    return db.scalars(stmt).first()


def list_segments(
    db: Session,
    *,
    page_number: int,
    num_records: int,
) -> list[segments_schema.SegmentRead]:
    """List segments ordered by name, paginated.

    Args:
        db: Database session.
        page_number: 1-based page number.
        num_records: Page size.

    Returns:
        The page of segments.
    """
    stmt = (
        select(segments_models.Segment)
        .order_by(segments_models.Segment.name, segments_models.Segment.id)
        .offset((page_number - 1) * num_records)
        .limit(num_records)
    )
    segments: list[segments_models.Segment] = list(db.scalars(stmt).all())
    return [segments_schema.SegmentRead.model_validate(segment) for segment in segments]


def count_segments(db: Session) -> int:
    """Count every stored segment.

    Args:
        db: Database session.

    Returns:
        The total number of segments.
    """
    stmt = select(func.count()).select_from(segments_models.Segment)
    return int(db.scalar(stmt) or 0)


def list_matchable_segments(db: Session) -> list[segments_contracts.SegmentMatchTarget]:
    """Load every segment as a matcher DTO.

    The segment base is global: any activity may traverse any segment.

    Args:
        db: Database session.

    Returns:
        All segments as ``SegmentMatchTarget`` (id, distance, geometry).
    """
    return [
        segments_contracts.SegmentMatchTarget(id=segment.id, distance_m=segment.distance_m, geometry=segment.geometry)
        for segment in db.scalars(select(segments_models.Segment)).all()
    ]


def delete_segment(segment_id: int, db: Session) -> None:
    """Delete a segment; its efforts cascade.

    Flushes without committing: the service owns the transaction.

    Args:
        segment_id: The segment identifier.
        db: Database session.
    """
    stmt = delete(segments_models.Segment).where(segments_models.Segment.id == segment_id)
    db.execute(stmt)
    db.flush()


def replace_activity_efforts(
    activity_id: int,
    matches: Sequence[segments_contracts.SegmentMatch],
    db: Session,
) -> int:
    """Replace every effort stored for one activity.

    Delete-then-insert makes matching idempotent: re-running it for the same
    activity and segment set converges to the same rows. Flushes without
    committing: the service owns the transaction.

    Args:
        activity_id: The activity whose efforts to rewrite.
        matches: The matching passes to store.
        db: Database session.

    Returns:
        The number of efforts stored.
    """
    db.execute(
        delete(segments_models.SegmentEffort).where(segments_models.SegmentEffort.activity_id == activity_id),
    )
    for match in matches:
        db.add(
            segments_models.SegmentEffort(
                segment_id=match.segment_id,
                activity_id=activity_id,
                direction=match.direction.value,
                pass_number=match.pass_number,
                elapsed_seconds=match.elapsed_seconds,
                avg_speed_mps=match.avg_speed_mps,
                coverage=match.coverage,
                time_start=match.time_start,
                time_end=match.time_end,
                stream_index_start=match.stream_index_start,
                stream_index_end=match.stream_index_end,
            )
        )
    db.flush()
    return len(matches)


def activity_ids_with_efforts(activity_ids: Sequence[int], db: Session) -> set[int]:
    """Return which of the given activities already have stored efforts.

    Args:
        activity_ids: Activities to check.
        db: Database session.

    Returns:
        The subset that has at least one effort.
    """
    if not activity_ids:
        return set()
    stmt = select(segments_models.SegmentEffort.activity_id).where(
        segments_models.SegmentEffort.activity_id.in_(activity_ids),
    )
    return {int(activity_id) for activity_id in db.scalars(stmt).all()}


def list_efforts_for_activity_with_segments(
    activity_id: int,
    db: Session,
    *,
    direction: str | None = None,
) -> list[tuple[segments_models.SegmentEffort, segments_models.Segment]]:
    """List an activity's efforts joined with their segments.

    Args:
        activity_id: The activity to read.
        db: Database session.
        direction: Keep only this direction, or ``None`` for both.

    Returns:
        ``(effort, segment)`` pairs ordered by segment name, then by time.
    """
    stmt = (
        select(segments_models.SegmentEffort, segments_models.Segment)
        .join(
            segments_models.Segment,
            segments_models.Segment.id == segments_models.SegmentEffort.segment_id,
        )
        .where(segments_models.SegmentEffort.activity_id == activity_id)
        .order_by(segments_models.Segment.name, segments_models.SegmentEffort.time_start)
    )
    if direction is not None:
        stmt = stmt.where(segments_models.SegmentEffort.direction == direction)
    return [(effort, segment) for effort, segment in db.execute(stmt).all()]


def list_efforts_for_segment(
    segment_id: int,
    activity_ids: Sequence[int],
    db: Session,
    *,
    page_number: int,
    num_records: int,
    direction: str | None = None,
) -> list[segments_schema.SegmentEffortRead]:
    """List one segment's efforts restricted to a set of activities, paginated.

    The activity set carries the caller's scoping; the CRUD layer applies none.

    Args:
        segment_id: The segment to read.
        activity_ids: Activities to include (empty short-circuits).
        db: Database session.
        page_number: 1-based page number.
        num_records: Page size.
        direction: Keep only this direction, or ``None`` for both.

    Returns:
        The page of efforts, fastest first.
    """
    if not activity_ids:
        return []
    stmt = (
        select(segments_models.SegmentEffort)
        .where(
            segments_models.SegmentEffort.segment_id == segment_id,
            segments_models.SegmentEffort.activity_id.in_(activity_ids),
        )
        .order_by(segments_models.SegmentEffort.elapsed_seconds, segments_models.SegmentEffort.id)
        .offset((page_number - 1) * num_records)
        .limit(num_records)
    )
    if direction is not None:
        stmt = stmt.where(segments_models.SegmentEffort.direction == direction)
    efforts: list[segments_models.SegmentEffort] = list(db.scalars(stmt).all())
    return [segments_schema.SegmentEffortRead.model_validate(effort) for effort in efforts]


def count_efforts_for_segment(
    segment_id: int,
    activity_ids: Sequence[int],
    db: Session,
    *,
    direction: str | None = None,
) -> int:
    """Count one segment's efforts within a set of activities.

    Args:
        segment_id: The segment to count.
        activity_ids: Activities to include (empty short-circuits).
        db: Database session.
        direction: Keep only this direction, or ``None`` for both.

    Returns:
        The matching effort count.
    """
    if not activity_ids:
        return 0
    stmt = (
        select(func.count())
        .select_from(segments_models.SegmentEffort)
        .where(
            segments_models.SegmentEffort.segment_id == segment_id,
            segments_models.SegmentEffort.activity_id.in_(activity_ids),
        )
    )
    if direction is not None:
        stmt = stmt.where(segments_models.SegmentEffort.direction == direction)
    return int(db.scalar(stmt) or 0)
