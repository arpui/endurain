"""Business rules and transaction ownership for the segments module.

Reading and importing segments is request-driven; matching is event- and
scheduler-driven (:mod:`modules.segments.subscribers` and
:mod:`modules.segments.scheduled_jobs` open their own sessions and call the
match entry points here). Every public function that writes commits; the CRUD
layer only flushes.
"""

from sqlalchemy.orm import Session

import core.exceptions as core_exceptions
import core.logger as core_logger
import modules.activities.activity.integration_service as activity_integration
import modules.activities.activity_streams.integration_service as activity_streams_integration
import modules.segments.contracts as segments_contracts
import modules.segments.crud as segments_crud
import modules.segments.matcher as segments_matcher
import modules.segments.schema as segments_schema

logger = core_logger.get_logger(__name__)


def import_segment(
    payload: segments_schema.SegmentImport,
    user_id: int,
    db: Session,
) -> segments_schema.SegmentDetailRead:
    """Store a segment definition from an import payload.

    Args:
        payload: The submitted definition (a Fase-0 Strava artifact shape).
        user_id: The importing user, stored as ``created_by``.
        db: Database session.

    Returns:
        The persisted segment, with geometry.

    Raises:
        InvalidInputError: If per-point arrays disagree with the polyline length.
        ConflictError: If this ``(source, source_id)`` segment is already stored.
    """
    source = payload.source
    source_id = str(payload.segment_id)
    if segments_crud.get_segment_by_source_id(source.value, source_id, db) is not None:
        raise core_exceptions.ConflictError(f"Segment '{source_id}' from '{source}' is already imported")

    geometry = [list(point) for point in payload.latlng]
    if payload.altitude is not None and len(payload.altitude) != len(geometry):
        raise core_exceptions.InvalidInputError("altitude must have one entry per latlng point")
    if payload.distance is not None and len(payload.distance) != len(geometry):
        raise core_exceptions.InvalidInputError("distance must have one entry per latlng point")

    distance_m = payload.distance[-1] if payload.distance else segments_matcher.polyline_length_m(geometry)
    elevation_gain_m = segments_matcher.elevation_gain_m(payload.altitude) if payload.altitude else None
    lats = [point[0] for point in geometry]
    lngs = [point[1] for point in geometry]
    record = segments_contracts.SegmentRecord(
        name=payload.name,
        source=source,
        source_id=source_id,
        distance_m=float(distance_m),
        elevation_gain_m=elevation_gain_m,
        altitudes=list(payload.altitude) if payload.altitude is not None else None,
        min_lat=min(lats),
        min_lng=min(lngs),
        max_lat=max(lats),
        max_lng=max(lngs),
        geometry=geometry,
    )
    segment = segments_crud.create_segment(record, user_id, db)
    db.commit()
    logger.info(
        "Segment imported",
        extra=core_logger.context(segment_id=segment.id, source=source.value, source_id=source_id),
    )
    return segments_schema.SegmentDetailRead.model_validate(segment)


def get_segment(segment_id: int, db: Session) -> segments_schema.SegmentDetailRead:
    """Read one segment with its geometry.

    Args:
        segment_id: The segment to read.
        db: Database session.

    Returns:
        The serialized segment.

    Raises:
        NotFoundError: If the segment does not exist.
    """
    segment = segments_crud.get_segment_by_id(segment_id, db)
    if segment is None:
        raise core_exceptions.NotFoundError(f"Segment {segment_id} not found")
    return segments_schema.SegmentDetailRead.model_validate(segment)


def delete_segment(segment_id: int, db: Session) -> None:
    """Delete a segment and cascade its efforts.

    Args:
        segment_id: The segment to delete.
        db: Database session.

    Raises:
        NotFoundError: If the segment does not exist.
    """
    if segments_crud.get_segment_by_id(segment_id, db) is None:
        raise core_exceptions.NotFoundError(f"Segment {segment_id} not found")
    segments_crud.delete_segment(segment_id, db)
    db.commit()
    logger.info("Segment deleted", extra=core_logger.context(segment_id=segment_id))


def list_segments(db: Session, *, page_number: int, num_records: int) -> segments_schema.SegmentPage:
    """List the segment library, paginated.

    The library is global: every stored segment is visible to every user.

    Args:
        db: Database session.
        page_number: 1-based page number.
        num_records: Page size.

    Returns:
        One page of segments with the matching total.
    """
    items = segments_crud.list_segments(db, page_number=page_number, num_records=num_records)
    total = segments_crud.count_segments(db)
    return segments_schema.SegmentPage.build(items, total, page_number, num_records)


def list_segments_for_activity(
    activity_id: int,
    requester_user_id: int,
    db: Session,
    *,
    direction: segments_schema.EffortDirectionFilter = segments_schema.EffortDirectionFilter.FORWARD,
) -> list[segments_schema.ActivitySegmentRead]:
    """List the segments one activity traversed, with its efforts on each.

    Args:
        activity_id: The activity to read.
        requester_user_id: The user asking, used for the activity's privacy check.
        db: Database session.
        direction: Which effort directions to include (both when ``ALL``).

    Returns:
        One entry per traversed segment, ordered by segment name.

    Raises:
        NotFoundError: If the activity is not readable by the requester.
    """
    if activity_integration.resolve_readable_parent(activity_id, requester_user_id, db) is None:
        raise core_exceptions.NotFoundError(f"Activity {activity_id} not found")
    rows = segments_crud.list_efforts_for_activity_with_segments(activity_id, db, direction=_kept_direction(direction))
    grouped: dict[int, segments_schema.ActivitySegmentRead] = {}
    for effort, segment in rows:
        entry = grouped.get(segment.id)
        if entry is None:
            entry = segments_schema.ActivitySegmentRead(
                segment=segments_schema.SegmentRead.model_validate(segment),
                efforts=[],
            )
            grouped[segment.id] = entry
        entry.efforts.append(segments_schema.SegmentEffortRead.model_validate(effort))
    return list(grouped.values())


def list_segment_efforts(
    segment_id: int,
    requester_user_id: int,
    db: Session,
    *,
    page_number: int,
    num_records: int,
    direction: segments_schema.EffortDirectionFilter = segments_schema.EffortDirectionFilter.FORWARD,
) -> segments_schema.SegmentEffortPage:
    """List one segment's efforts limited to the requester's activities.

    Args:
        segment_id: The segment to read.
        requester_user_id: The user asking; only their activities are considered.
        db: Database session.
        page_number: 1-based page number.
        num_records: Page size.
        direction: Which effort directions to include (both when ``ALL``).

    Returns:
        One page of efforts (fastest first) with the matching total.

    Raises:
        NotFoundError: If the segment does not exist.
    """
    if segments_crud.get_segment_by_id(segment_id, db) is None:
        raise core_exceptions.NotFoundError(f"Segment {segment_id} not found")
    activity_ids = [activity.id for activity in activity_integration.list_user_activities(requester_user_id, db)]
    kept = _kept_direction(direction)
    items = segments_crud.list_efforts_for_segment(
        segment_id,
        activity_ids,
        db,
        page_number=page_number,
        num_records=num_records,
        direction=kept,
    )
    total = segments_crud.count_efforts_for_segment(segment_id, activity_ids, db, direction=kept)
    return segments_schema.SegmentEffortPage.build(items, total, page_number, num_records)


def _kept_direction(direction: segments_schema.EffortDirectionFilter) -> str | None:
    """Translate the query filter into the stored direction to keep.

    Args:
        direction: The requested directions.

    Returns:
        The direction value to filter on, or ``None`` when both are wanted.
    """
    return None if direction is segments_schema.EffortDirectionFilter.ALL else direction.value


def match_activity(activity_id: int, db: Session) -> int:
    """Match one activity against every stored segment and store its efforts.

    Entry point for the ``activity.created`` subscribers. Idempotent: the
    activity's efforts are replaced wholesale.

    Args:
        activity_id: The activity to match.
        db: Database session.

    Returns:
        The number of efforts stored.
    """
    segments = segments_crud.list_matchable_segments(db)
    if not segments:
        return 0
    waypoints_by_activity = activity_streams_integration.get_gps_waypoints_for_activities([activity_id], db)
    return _store_matches_for_activity(activity_id, segments, waypoints_by_activity.get(activity_id, []), db)


def reconcile_missing_efforts(db: Session) -> int:
    """Match every activity that has no stored efforts yet.

    The safety net for missed ``activity.created`` events: activities that
    already carry efforts are skipped, so the pass stays cheap on installed
    bases. New segments over old activities need :func:`rescan_all_efforts`.

    Args:
        db: Database session.

    Returns:
        The number of efforts stored.
    """
    return _walk_and_match(db, only_missing=True)


def rescan_all_efforts(db: Session) -> int:
    """Re-match every activity against the current segment library.

    Args:
        db: Database session.

    Returns:
        The number of efforts stored.
    """
    return _walk_and_match(db, only_missing=False)


def _walk_and_match(db: Session, *, only_missing: bool) -> int:
    """Match activities page by page, optionally skipping those with efforts.

    Args:
        db: Database session.
        only_missing: Skip activities that already have stored efforts.

    Returns:
        The number of efforts stored.
    """
    segments = segments_crud.list_matchable_segments(db)
    if not segments:
        return 0
    stored = 0
    after_id = 0
    while True:
        activity_ids = activity_integration.list_activity_ids_after(db, after_id=after_id)
        if not activity_ids:
            break
        after_id = max(activity_ids)
        if only_missing:
            done = segments_crud.activity_ids_with_efforts(activity_ids, db)
            activity_ids = [activity_id for activity_id in activity_ids if activity_id not in done]
        if not activity_ids:
            continue
        waypoints_by_activity = activity_streams_integration.get_gps_waypoints_for_activities(activity_ids, db)
        for activity_id in activity_ids:
            waypoints = waypoints_by_activity.get(activity_id, [])
            stored += _store_matches_for_activity(activity_id, segments, waypoints, db)
    logger.info(
        "Segment matching pass finished",
        extra=core_logger.context(only_missing=only_missing, efforts=stored),
    )
    return stored


def _store_matches_for_activity(
    activity_id: int,
    segments: list[segments_contracts.SegmentMatchTarget],
    waypoints: list,
    db: Session,
) -> int:
    """Run the matcher over one activity and replace its stored efforts.

    Args:
        activity_id: The activity to match.
        segments: The segments to match against.
        waypoints: The activity's map-stream waypoints (empty when it has no GPS).
        db: Database session.

    Returns:
        The number of efforts stored.
    """
    matches: list[segments_contracts.SegmentMatch] = []
    if waypoints:
        for segment in segments:
            matches.extend(
                segments_matcher.match_segment_passes(
                    segment.id,
                    segment.geometry,
                    segment.distance_m,
                    waypoints,
                )
            )
    count = segments_crud.replace_activity_efforts(activity_id, matches, db)
    db.commit()
    if count:
        logger.info(
            "Stored segment efforts for activity",
            extra=core_logger.context(activity_id=activity_id, efforts=count),
        )
    return count
