"""Authenticated routes for the computed loads of one activity."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Security, status
from sqlalchemy.orm import Session

import core.database as core_database
import core.rate_limit as core_rate_limit
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
    "/trail-sections/marks",
    response_model=list[analysis_schema.TrailMarkRead],
)
def get_trail_section_marks(
    activity_id: activity_dependencies.ActivityId,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["analysis:read"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> list[analysis_schema.TrailMarkRead]:
    """Return what the athlete has already said about this activity's stretches."""
    return analysis_service.get_trail_marks(activity_id, token_user_id, db)


@router.post(
    "/trail-sections/marks",
    response_model=analysis_schema.TrailMarkRead,
    status_code=201,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def post_trail_section_mark(
    request: Request,
    activity_id: activity_dependencies.ActivityId,
    mark: analysis_schema.TrailMarkCreate,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["analysis:write"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> analysis_schema.TrailMarkRead:
    """Judge one measured stretch: agreed, overstated, or never technical.

    These labels are the calibration: a weight or threshold change is accepted or
    rejected against them, so the three verdicts stay apart rather than collapsing
    into a yes/no.
    """
    return analysis_service.save_trail_mark(activity_id, token_user_id, mark, db)


@router.get(
    "/trail-variants",
    response_model=analysis_schema.TrailVariantsRead,
)
def get_trail_variants(
    activity_id: activity_dependencies.ActivityId,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["analysis:read"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> analysis_schema.TrailVariantsRead:
    """Suggest reconnection variants along this activity's own track.

    The suggestions can only come from ground the athlete has physically crossed
    (the personal graph), and each carries how firmly it is evidenced; what to
    do with them is decided by the verdict placed below.
    """
    return analysis_service.get_activity_variants(activity_id, token_user_id, db)


@router.post(
    "/trail-variants/marks",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def post_variant_mark(
    request: Request,
    activity_id: activity_dependencies.ActivityId,
    mark: analysis_schema.VariantMarkCreate,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["analysis:write"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> None:
    """Judge one suggested variant: real ground, or a suggestion to bury."""
    analysis_service.save_variant_mark(activity_id, token_user_id, mark, db)


@router.delete(
    "/trail-variants/marks",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def delete_variant_mark(
    request: Request,
    activity_id: activity_dependencies.ActivityId,
    split_m: Annotated[float, Query(ge=0)],
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["analysis:write"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> None:
    """Take back the verdict on one variant."""
    analysis_service.clear_variant_mark(activity_id, token_user_id, split_m, db)


@router.delete(
    "/trail-sections/marks",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def delete_trail_section_mark(
    request: Request,
    activity_id: activity_dependencies.ActivityId,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["analysis:write"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    start_m: Annotated[float, Query(description="Ground position of the judged stretch")],
) -> None:
    """Take back a verdict on one stretch."""
    analysis_service.clear_trail_mark(activity_id, token_user_id, start_m, db)


@router.get(
    "/trail-sections",
    response_model=analysis_schema.TrailSectionsRead,
)
def get_trail_sections(
    activity_id: activity_dependencies.ActivityId,
    _check_scopes: Annotated[
        Callable,
        Security(auth_dependencies.check_scopes, scopes=["analysis:read"]),
    ],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> analysis_schema.TrailSectionsRead:
    """Return the technical stretches of one trail run (experimental).

    Spatial windows where the pace falls short of what the grade explains,
    with stride mechanics changed, are read as technical terrain: the ground,
    not the gradient, is what cost. Polylines included for the map.
    """
    return analysis_service.get_trail_sections(activity_id, token_user_id, db)


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
