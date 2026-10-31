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


@router.get(
    "/wprime",
    response_model=analysis_schema.WprimeBalanceRead,
)
def get_activity_wprime_balance(
    activity_id: activity_dependencies.ActivityId,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["activities:read"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> analysis_schema.WprimeBalanceRead:
    """Return this activity's W' balance and above-CP totals, for its owner.

    Computed on read from the stored power stream and the sport's fitted
    threshold: the series answers for the moment it is asked, so a threshold
    change is reflected immediately instead of needing a recompute pass.
    """
    return analysis_service.get_wprime_balance(activity_id, token_user_id, db)


@router.get(
    "/lap-dynamics",
    response_model=list[analysis_schema.LapDynamicsRead],
)
def get_activity_lap_dynamics(
    activity_id: activity_dependencies.ActivityId,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["activities:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> list[analysis_schema.LapDynamicsRead]:
    """Return per-lap dynamics: power, stride length, elevation split and aRSS.

    Computed for the activity's owner from the laps the device recorded and the
    athlete's own threshold, so the drift of form under fatigue reads against
    their numbers.
    """
    return analysis_service.get_activity_lap_dynamics(activity_id, token_user_id, db)


@router.get(
    "/intervals",
    response_model=analysis_schema.ActivityIntervalsRead,
)
def get_activity_intervals(
    activity_id: activity_dependencies.ActivityId,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["activities:read"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> analysis_schema.ActivityIntervalsRead:
    """Return how this activity breaks down into efforts, for its owner.

    Split on read from the activity's own samples against the athlete's own numbers
    (their threshold and the session's median power), so the segmentation never
    assumes a general population's idea of hard.
    """
    return analysis_service.get_activity_intervals(activity_id, token_user_id, db)
