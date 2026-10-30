"""Authenticated routes for the computed loads of one activity."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

import core.database as core_database
import modules.activities.activity.dependencies as activity_dependencies
import modules.analysis.schema as analysis_schema
import modules.analysis.service as analysis_service
import modules.auth.dependencies as auth_dependencies

router = APIRouter()


@router.get(
    "/loads",
    response_model=list[analysis_schema.ActivityLoadRead],
)
def list_activity_loads(
    activity_id: activity_dependencies.ActivityId,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["activities:read"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> list[analysis_schema.ActivityLoadRead]:
    """Return every computed load of this activity, for its owner.

    Read scope follows the parent activity because the resource belongs to it, while
    the service narrows access to the owner: a load is the athlete's own training
    measure, tied to the thresholds they train against.
    """
    return analysis_service.list_activity_loads(activity_id, token_user_id, db)
