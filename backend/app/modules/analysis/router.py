"""API routes for analysis settings, threshold refresh and recomputation."""

from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Security, status
from sqlalchemy.orm import Session

import core.database as core_database
import core.dependencies as core_dependencies
import core.exceptions as core_exceptions
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
    "/loads",
    response_model=list[analysis_schema.ActivityLoadsSummaryRead],
)
def list_loads_summaries(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    activity_ids: Annotated[list[int], Query(min_length=1, max_length=50)],
) -> list[analysis_schema.ActivityLoadsSummaryRead]:
    """Return the loads and power provenance of several of the caller's activities.

    One call per list page: the activities view asks for the ids it is showing and
    renders each row's own score without one request per activity.
    """
    return analysis_service.list_loads_summaries(token_user_id, activity_ids, db)


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


@router.get(
    "/power-flags",
    response_model=list[analysis_schema.PowerFlagRead],
)
def get_power_flags(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    sport: Annotated[str, Query(max_length=32)],
) -> list[analysis_schema.PowerFlagRead]:
    """Return the caller's sessions the duration-intensity curve held out.

    A session is held out when its best average for a duration is implausible
    against its own normalized power and an absolute floor; the curve stops
    trusting it and this says so, with the numbers.
    """
    return analysis_service.get_power_flags(token_user_id, sport, db)


@router.get(
    "/cp-trend",
    response_model=analysis_schema.CpTrendRead,
)
def get_cp_trend(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    sport: Annotated[str, Query(max_length=32)],
    window: Annotated[int | None, Query(ge=30, le=3650, description="Days of history; omit for all")] = None,
) -> analysis_schema.CpTrendRead:
    """Return the caller's critical-power evolution for one sport.

    One point per session, each fit from that session's own sustained efforts;
    the stored threshold comes back as the reference line. ``window`` narrows
    the history without dropping sessions outside it from other views.
    """
    return analysis_service.get_cp_trend(token_user_id, sport, db, window_days=window)


@router.get(
    "/best-times",
    response_model=analysis_schema.BestTimesRead,
)
def get_best_times(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    sport: Annotated[str, Query(max_length=32)] = "run",
    months: Annotated[int | None, Query(ge=1, le=1200)] = None,
) -> analysis_schema.BestTimesRead:
    """Return the caller's best splits over the canonical race distances.

    The splits are found in the activities' own samples, and a split running
    downhill more than the bound for its length is discarded, so the table shows
    what the athlete actually ran rather than what gravity lent them.
    """
    return analysis_service.get_best_times(token_user_id, sport, db, months=months)


@router.get(
    "/trail-network",
    response_model=analysis_schema.TrailNetworkRead,
)
def get_trail_network(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
    south: Annotated[float | None, Query(ge=-90, le=90)] = None,
    west: Annotated[float | None, Query(ge=-180, le=180)] = None,
    north: Annotated[float | None, Query(ge=-90, le=90)] = None,
    east: Annotated[float | None, Query(ge=-180, le=180)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = analysis_constants.TRAIL_NETWORK_MAX_WAYS,
) -> analysis_schema.TrailNetworkRead:
    """Return the caller's crossed ways as a network, each judged by its own descents.

    Experimental, and deliberately descent-first: an ascent over a broken path
    says little, so ascents only corroborate. The geometry of every claimed way
    travels with its numbers so the same payload can later seed a route built by
    joining ways, without a second query.
    """
    edges = (south, west, north, east)
    if any(edge is None for edge in edges):
        if any(edge is not None for edge in edges):
            raise core_exceptions.InvalidInputError("A viewport needs south, west, north and east together")
        bbox = None
    else:
        bbox = (south, west, north, east)
        if north <= south or east <= west:
            raise core_exceptions.InvalidInputError("A viewport needs north above south and east beyond west")
    return analysis_service.get_trail_network(token_user_id, db, bbox=bbox, limit=limit)


@router.get(
    "/routes",
    response_model=list[analysis_schema.RouteRead],
)
def list_routes(
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:read"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> list[analysis_schema.RouteRead]:
    """The caller's saved designed routes."""
    return analysis_service.list_routes(token_user_id, db)


@router.post(
    "/routes",
    response_model=analysis_schema.RouteRead,
    status_code=201,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def create_route(
    request: Request,
    payload: analysis_schema.RouteCreate,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:write"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> analysis_schema.RouteRead:
    """Save a drawn route; its distance is measured here, not trusted from the client."""
    return analysis_service.create_route(token_user_id, payload, db)


@router.put(
    "/routes/{route_id}",
    response_model=analysis_schema.RouteRead,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def update_route(
    request: Request,
    route_id: core_dependencies.PositiveId,
    payload: analysis_schema.RouteUpdate,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:write"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> analysis_schema.RouteRead:
    """Edit one saved route: name, redrawn polyline, notes.

    Unknown or foreign ids answer 404: the route is simply not there for this
    athlete. The totals are measured again from the new geometry.
    """
    route = analysis_service.update_route(route_id, token_user_id, payload, db)
    if route is None:
        raise core_exceptions.NotFoundError
    return route


@router.delete(
    "/routes/{route_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
)
@core_rate_limit.limiter.limit(core_rate_limit.WRITE)
def delete_route(
    request: Request,
    route_id: core_dependencies.PositiveId,
    _check_scopes: Annotated[Callable, Security(auth_dependencies.check_scopes, scopes=["analysis:write"])],
    token_user_id: Annotated[int, Depends(auth_dependencies.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
) -> None:
    """Delete one saved route."""
    analysis_service.delete_route(route_id, token_user_id, db)


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
