"""Authenticated routes for the segments traversed by one activity."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

import core.database as core_database
import modules.activities.activity.dependencies as activity_dependencies
import modules.auth.dependencies as auth_dependencies
import modules.segments.schema as segments_schema
import modules.segments.service as segments_service

router = APIRouter()


@router.get(
    "/segments",
    response_model=list[segments_schema.ActivitySegmentRead],
)
def list_activity_segments(
    activity_id: activity_dependencies.ActivityId,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["activities:read"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> list[segments_schema.ActivitySegmentRead]:
    """Return the segments this activity traversed, with its efforts on each.

    Read scope follows the parent activity: these efforts belong to the
    activity, and its privacy rules decide who may see them.
    """
    return segments_service.list_segments_for_activity(activity_id, token_user_id, db)
