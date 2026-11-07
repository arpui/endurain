"""Published cross-module face of the segments package.

Reads only: other modules never reach into segments' crud or models. The
analysis network and route designer project stored efforts onto their own
tracks and geometries through these records.
"""

from sqlalchemy.orm import Session

import modules.segments.contracts as segments_contracts
import modules.segments.service as segments_service


def list_segment_shapes(db: Session) -> list[segments_contracts.SegmentShape]:
    """Every stored segment as a published shape (geometry and totals)."""
    return segments_service.list_segment_shapes(db)


def list_segment_shapes_in_bbox(
    min_lat: float,
    min_lng: float,
    max_lat: float,
    max_lng: float,
    db: Session,
) -> list[segments_contracts.SegmentShape]:
    """Segments whose bounding box touches a viewport."""
    return segments_service.list_segment_shapes_in_bbox(min_lat, min_lng, max_lat, max_lng, db)


def list_activity_effort_records(activity_id: int, db: Session) -> list[segments_contracts.SegmentEffortRecord]:
    """One activity's stored efforts, as along-stream ranges."""
    return segments_service.list_activity_effort_records(activity_id, db)
