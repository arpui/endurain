"""API routes for analysis settings, threshold refresh and recomputation."""

from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Security
from sqlalchemy.orm import Session

import core.database as core_database
import core.rate_limit as core_rate_limit
import modules.analysis.constants as analysis_constants
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


@router.get(
    "/fitness",
    response_model=analysis_schema.FitnessSeriesRead,
)
def get_fitness_series(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    method: Annotated[analysis_constants.LoadMethod | None, Query()] = None,
    acute_days: Annotated[int | None, Query(ge=1, le=180)] = None,
    chronic_days: Annotated[int | None, Query(ge=1, le=365)] = None,
    sport: Annotated[str | None, Query(max_length=32)] = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> analysis_schema.FitnessSeriesRead:
    """Return the caller's daily load series with fatigue, fitness and balance.

    Every parameter is optional: without them the athlete's stored settings decide
    the formula, the time constants and the whole sports, and the window defaults
    to the recent one. Naming them changes only this response, not the settings.
    """
    return analysis_service.get_fitness_series(
        token_user_id,
        db,
        method=method,
        acute_days=acute_days,
        chronic_days=chronic_days,
        sport=sport,
        from_date=from_date,
        to_date=to_date,
    )


@router.get(
    "/power-duration",
    response_model=analysis_schema.PowerDurationRead,
)
def get_power_duration(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    sport: Annotated[str, Query(max_length=32)],
    fit_from: Annotated[int | None, Query(ge=10, le=7200)] = None,
    fit_to: Annotated[int | None, Query(ge=30, le=21600)] = None,
) -> analysis_schema.PowerDurationRead:
    """Return the caller's duration-intensity curve for one sport.

    The points are the cached best sustained averages; the fit is recomputed for
    the request, so ``fit_from``/``fit_to`` preview a different window without
    changing anything. The response also reports the threshold the sport's loads
    actually use and the sessions that built the curve.
    """
    return analysis_service.get_power_duration(token_user_id, sport, db, fit_from_s=fit_from, fit_to_s=fit_to)


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
