"""API routes for analysis settings, threshold refresh and recomputation."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Security
from sqlalchemy.orm import Session

import core.database as core_database
import core.rate_limit as core_rate_limit
import modules.analysis.scheduled_jobs as analysis_scheduled_jobs
import modules.analysis.schema as analysis_schema
import modules.analysis.service as analysis_service
import modules.auth.dependencies as auth_dependencies

router = APIRouter()


@router.get(
    "/settings",
    response_model=analysis_schema.AnalysisSettingsRead,
)
def get_analysis_settings(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> analysis_schema.AnalysisSettingsRead:
    """Return the caller's analysis parameters, including derived thresholds."""
    return analysis_service.get_settings(token_user_id, db)


@router.put(
    "/settings",
    response_model=analysis_schema.AnalysisSettingsRead,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def update_analysis_settings(
    request: Request,
    payload: analysis_schema.AnalysisSettingsUpdate,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:write"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> analysis_schema.AnalysisSettingsRead:
    """Update the caller's analysis parameters.

    A stored load records the threshold it was computed with, so changing a
    threshold does not reinterpret history: queue a recompute afterwards to measure
    every activity against the new value.
    """
    return analysis_service.update_settings(token_user_id, payload, db)


@router.post(
    "/thresholds/refresh",
    response_model=analysis_schema.RecomputeQueued,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def queue_threshold_refresh(
    request: Request,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:write"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
) -> analysis_schema.RecomputeQueued:
    """Queue re-deriving the caller's thresholds from their own records."""
    analysis_scheduled_jobs.schedule_analysis_threshold_refresh(token_user_id)
    return analysis_schema.RecomputeQueued(queued=True)


@router.post(
    "/recompute",
    response_model=analysis_schema.RecomputeQueued,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def queue_load_recompute(
    request: Request,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:write"])],
) -> analysis_schema.RecomputeQueued:
    """Queue re-measuring every activity with the current thresholds and formulas."""
    analysis_scheduled_jobs.schedule_analysis_recompute()
    return analysis_schema.RecomputeQueued(queued=True)
