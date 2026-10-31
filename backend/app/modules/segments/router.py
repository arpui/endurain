"""API routes for the segment library."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Security, status
from sqlalchemy.orm import Session

import core.database as core_database
import core.dependencies as core_dependencies
import core.pagination as core_pagination
import core.rate_limit as core_rate_limit
import modules.auth.dependencies as auth_dependencies
import modules.segments.scheduled_jobs as segments_scheduled_jobs
import modules.segments.schema as segments_schema
import modules.segments.service as segments_service

# Define the API router
router = APIRouter()


@router.get(
    "",
    response_model=segments_schema.SegmentPage,
)
def list_segments(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["segments:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    page: Annotated[core_pagination.PageParams, Depends(core_pagination.list_page_params)],
) -> segments_schema.SegmentPage:
    """Return one page of the segment library, with the matching total."""
    return segments_service.list_segments(
        db,
        page_number=page.page_number,
        num_records=page.num_records,
        requester_user_id=token_user_id,
    )


@router.post(
    "",
    response_model=segments_schema.SegmentDetailRead,
    status_code=status.HTTP_201_CREATED,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def import_segment(
    request: Request,
    segment: segments_schema.SegmentImport,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["segments:write"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> segments_schema.SegmentDetailRead:
    """Store a segment definition from an import payload.

    Matching existing activities against the new segment is not on the request
    path: the hourly reconcile pass picks up activities that never had efforts,
    and a full re-match can be queued through the backfill route afterwards.
    """
    return segments_service.import_segment(segment, token_user_id, db)


@router.post(
    "/backfill",
    response_model=segments_schema.BackfillQueued,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def queue_backfill(
    request: Request,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["segments:write"])],
) -> segments_schema.BackfillQueued:
    """Queue a full re-match of every activity against the segment library.

    Deliberately declared before ``/{segment_id}`` so the literal path is not
    swallowed by the dynamic route.
    """
    segments_scheduled_jobs.schedule_segments_rescan()
    return segments_schema.BackfillQueued(queued=True)


@router.get(
    "/{segment_id}",
    response_model=segments_schema.SegmentDetailRead,
)
def get_segment(
    segment_id: core_dependencies.PositiveId,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["segments:read"])],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> segments_schema.SegmentDetailRead:
    """Return one segment, with its geometry and bounding box."""
    return segments_service.get_segment(segment_id, db)


@router.delete(
    "/{segment_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def delete_segment(
    request: Request,
    segment_id: core_dependencies.PositiveId,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["segments:write"])],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> None:
    """Delete a segment; its stored efforts cascade with it."""
    segments_service.delete_segment(segment_id, db)


@router.get(
    "/{segment_id}/efforts",
    response_model=segments_schema.SegmentEffortPage,
)
def list_segment_efforts(
    segment_id: core_dependencies.PositiveId,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["segments:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    page: Annotated[core_pagination.PageParams, Depends(core_pagination.child_page_params)],
    direction: Annotated[segments_schema.EffortDirectionFilter, Query()] = (
        segments_schema.EffortDirectionFilter.FORWARD
    ),
) -> segments_schema.SegmentEffortPage:
    """Return one page of the requester's efforts on the given segment, fastest first.

    Reverse passes are stored for information only, so they are only listed when
    asked for (``direction=reverse`` or ``direction=all``).
    """
    return segments_service.list_segment_efforts(
        segment_id,
        token_user_id,
        db,
        page_number=page.page_number,
        num_records=page.num_records,
        direction=direction,
    )
