"""Business rules and transaction ownership for the analysis module.

Reading settings and loads is request-driven; computing them is event- and
scheduler-driven (:mod:`modules.analysis.subscribers` and
:mod:`modules.analysis.scheduled_jobs` open their own sessions and call the entry
points here). Every public function that writes commits; the CRUD layer only flushes.

A load is only meaningful against a threshold, so this module also owns deriving
them: the duration-intensity curve of each sport yields critical power and the
anaerobic work capacity, and the best sustained 30-minute heart rate yields the
threshold heart rate. A value the athlete typed in is never overwritten by a derived
one, and every stored load records the threshold it was divided by.
"""

from datetime import datetime
from statistics import mean as arithmetic_mean

from sqlalchemy.orm import Session

import core.exceptions as core_exceptions
import core.logger as core_logger
import modules.activities.activity.constants as activity_constants
import modules.activities.activity.integration_service as activity_integration
import modules.activities.activity.schema as activity_schema
import modules.activities.activity_streams.constants as activity_streams_constants
import modules.activities.activity_streams.integration_service as activity_streams_integration
import modules.analysis.constants as analysis_constants
import modules.analysis.contracts as analysis_contracts
import modules.analysis.crud as analysis_crud
import modules.analysis.maths as analysis_maths
import modules.analysis.methods as analysis_methods
import modules.analysis.schema as analysis_schema
import modules.users.users.integration_service as users_integration

logger = core_logger.get_logger(__name__)

#: Activity type -> sport group, reversed once from the vocabulary the activities
#: module publishes (the same grouping its own statistics use, so an activity that
#: counts towards a sport's totals is measured against that sport's threshold).
_SPORT_BY_ACTIVITY_TYPE: dict[int, str] = {
    activity_type: sport
    for sport, activity_types in activity_constants.ACTIVITY_TYPES_BY_SPORT.items()
    for activity_type in activity_types
}


def sport_group(activity_type: int) -> str | None:
    """Return the sport group an activity type belongs to, or ``None`` when uncategorized.

    Args:
        activity_type: Numeric activity type code.

    Returns:
        The sport group name used for thresholds and curves.
    """
    return _SPORT_BY_ACTIVITY_TYPE.get(activity_type)


def get_settings(user_id: int, db: Session) -> analysis_schema.AnalysisSettingsRead:
    """Return an athlete's analysis parameters, defaults included.

    Args:
        user_id: The athlete.
        db: Database session.

    Returns:
        The stored settings, or the module defaults when the athlete has no row yet.
    """
    stored = analysis_crud.get_settings(user_id, db)
    if stored is not None:
        return stored
    return analysis_schema.AnalysisSettingsRead(
        acute_days=analysis_constants.ACUTE_DAYS_DEFAULT,
        chronic_days=analysis_constants.CHRONIC_DAYS_DEFAULT,
        primary_method=analysis_constants.PRIMARY_METHOD_DEFAULT,
        resting_hr=None,
    )


def update_settings(
    user_id: int,
    payload: analysis_schema.AnalysisSettingsUpdate,
    db: Session,
) -> analysis_schema.AnalysisSettingsRead:
    """Apply a partial settings update and store it.

    Args:
        user_id: The athlete.
        payload: The fields to change; omitted fields keep their value.
        db: Database session.

    Returns:
        The settings after the update.
    """
    current = get_settings(user_id, db)
    if payload.acute_days is not None:
        current.acute_days = payload.acute_days
    if payload.chronic_days is not None:
        current.chronic_days = payload.chronic_days
    if payload.primary_method is not None:
        current.primary_method = payload.primary_method
    if payload.resting_hr is not None:
        current.resting_hr = payload.resting_hr
    if payload.method_params is not None:
        current.method_params = {key: float(value) for key, value in payload.method_params.items()}
    if payload.power_threshold_w is not None:
        for sport, watts in payload.power_threshold_w.items():
            if watts is None:
                current.power_thresholds.pop(sport, None)
            else:
                current.power_thresholds[sport] = analysis_schema.PowerThresholdRead(
                    value_w=watts,
                    w_prime_j=None,
                    source=analysis_constants.ThresholdSource.MANUAL,
                    cv_pct=None,
                )
    if payload.hr_threshold_bpm is not None:
        current.hr_threshold = analysis_schema.HeartRateThresholdRead(
            value_bpm=payload.hr_threshold_bpm,
            source=analysis_constants.ThresholdSource.MANUAL,
        )
    stored = analysis_crud.store_settings(user_id, current, db)
    db.commit()
    logger.info(
        "Analysis settings updated",
        extra=core_logger.context(user_id=user_id, primary_method=stored.primary_method.value),
    )
    return stored


def _stream_samples(
    activity_id: int,
    stream_type: int,
    value_key: str,
    db: Session,
    *,
    keep_zeros: bool,
) -> tuple[float, tuple[float, ...]]:
    """Return one activity stream as ``(sample_period_s, values)``.

    Power keeps its zeros (a stopped athlete does no work, and that is what the
    stored Normalized Power counts); heart-rate streams drop zeros, which are the
    sensor's "no signal" sentinel.

    Args:
        activity_id: The activity whose stream is read.
        stream_type: Stream type code.
        value_key: Waypoint key to read (``power``, ``hr``).
        db: Database session.
        keep_zeros: Whether zero samples are kept.

    Returns:
        The sample period and values; ``(0.0, ())` when the activity has no such stream.
    """
    stream = activity_streams_integration.get_stream_for_derivation(activity_id, stream_type, db)
    waypoints = (stream.stream_waypoints if stream is not None else None) or []
    stamps: list[datetime] = []
    values: list[float] = []
    for point in waypoints:
        raw = point.get(value_key) if isinstance(point, dict) else None
        if raw is None:
            continue
        value = float(raw)
        if not keep_zeros and value == 0.0:
            continue
        stamps.append(datetime.fromisoformat(point["time"]))
        values.append(value)
    if not values:
        return 0.0, ()
    return analysis_maths.sample_period_s(stamps), tuple(values)


def _moving_seconds(activity: activity_schema.ActivityRead) -> float:
    """Return the active time a load scales with, falling back to elapsed time."""
    return float(activity.total_timer_time or activity.total_elapsed_time or 0.0)


def _load_input(
    activity: activity_schema.ActivityRead,
    sport: str | None,
    db: Session,
) -> analysis_methods.LoadInput:
    """Assemble what the formulas need about one activity.

    Args:
        activity: The activity being measured.
        sport: Its sport group, or ``None`` when uncategorized.
        db: Database session.

    Returns:
        A :class:`~modules.analysis.methods.LoadInput` ready for the registry.
    """
    settings = get_settings(activity.user_id or 0, db)
    period_s, power_w = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_POWER,
        "power",
        db,
        keep_zeros=True,
    )
    _, hr_values = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_HR,
        "hr",
        db,
        keep_zeros=False,
    )
    threshold = settings.power_thresholds.get(sport) if sport else None
    max_hr = activity.max_hr
    if not max_hr and activity.user_id:
        owner = users_integration.get_user(activity.user_id, db)
        max_hr = owner.max_heart_rate if owner is not None else None
    # The stored average is the norm; a stream is only read to cover an activity
    # whose summary is missing while its samples are not.
    average_hr = (
        float(activity.average_hr) if activity.average_hr else (arithmetic_mean(hr_values) if hr_values else None)
    )
    return analysis_methods.LoadInput(
        moving_seconds=_moving_seconds(activity),
        power_w=power_w or None,
        period_s=period_s or 1.0,
        stored_np_w=float(activity.normalized_power) if activity.normalized_power else None,
        average_hr=average_hr,
        max_hr=float(max_hr) if max_hr else None,
        power_threshold_w=float(threshold.value_w) if threshold else None,
        hr_threshold_bpm=float(settings.hr_threshold.value_bpm) if settings.hr_threshold else None,
        resting_hr_bpm=float(settings.resting_hr) if settings.resting_hr else None,
        parameters=dict(settings.method_params),
    )


def _store_loads(
    activity: activity_schema.ActivityRead,
    db: Session,
) -> int:
    """Compute every applicable load for one activity and replace its stored rows.

    Args:
        activity: The activity to measure.
        db: Database session.

    Returns:
        The number of load rows written.
    """
    sport = sport_group(activity.activity_type)
    data = _load_input(activity, sport, db)
    computed = analysis_methods.compute_all(data)
    records = [
        analysis_contracts.LoadRecord(
            method=method,
            load=round(value.load, 2),
            intensity_factor=round(value.intensity_factor, 4) if value.intensity_factor is not None else None,
            threshold_used=round(value.threshold_used, 2) if value.threshold_used is not None else None,
        )
        for method, value in computed
    ]
    written = analysis_crud.replace_activity_loads(activity.id, _moving_seconds(activity), records, db)
    db.commit()
    if written:
        logger.debug(
            "Stored activity loads",
            extra=core_logger.context(
                activity_id=activity.id,
                sport=sport,
                loads=[record.method.value for record in records],
            ),
        )
    return written


def compute_loads_for_activity(activity_id: int, db: Session) -> int:
    """Compute and store one activity's loads. Entry point for subscribers.

    Args:
        activity_id: The activity to measure.
        db: Database session.

    Returns:
        The number of load rows written (0 for an activity that has since been
        removed or carries nothing any formula can read).
    """
    activity = activity_integration.get_activity_for_derivation(activity_id, db)
    if activity is None:
        return 0
    return _store_loads(activity, db)


def refresh_thresholds(user_id: int, db: Session) -> analysis_schema.AnalysisSettingsRead:
    """Derive an athlete's thresholds from their own records, and cache their curves.

    Power thresholds come from fitting the hyperbolic model to each sport's
    duration-intensity curve; the threshold heart rate is the best sustained
    30-minute average heart rate. Manual values are left alone, and a sport whose
    peaks come from too few activities gets no derived threshold at all: a curve
    through one activity's samples invents a plausible-looking number.

    Args:
        user_id: The athlete.
        db: Database session.

    Returns:
        The settings after the refresh.
    """
    activities = activity_integration.list_user_activities(user_id, db)
    settings = get_settings(user_id, db)
    peaks_by_sport: dict[str, dict[int, float]] = {}
    holder_by_peak: dict[tuple[str, int], int] = {}
    activities_by_sport: dict[str, int] = {}
    best_hr = 0.0

    for activity in activities:
        sport = sport_group(activity.activity_type)
        if sport is None:
            continue
        period_s, power_w = _stream_samples(
            activity.id,
            activity_streams_constants.STREAM_TYPE_POWER,
            "power",
            db,
            keep_zeros=True,
        )
        if power_w:
            activities_by_sport[sport] = activities_by_sport.get(sport, 0) + 1
            merged = peaks_by_sport.setdefault(sport, {})
            for duration, value in analysis_maths.best_means(power_w, period_s).items():
                if value >= merged.get(duration, 0.0):
                    merged[duration] = value
                    holder_by_peak[(sport, duration)] = activity.id
        hr_period_s, hr_values = _stream_samples(
            activity.id,
            activity_streams_constants.STREAM_TYPE_HR,
            "hr",
            db,
            keep_zeros=False,
        )
        if hr_values:
            best_hr = max(
                best_hr,
                analysis_maths.best_window_mean(hr_values, hr_period_s, analysis_constants.LTHR_WINDOW_S),
            )

    for sport, peaks in peaks_by_sport.items():
        analysis_crud.replace_duration_peaks(
            user_id,
            sport,
            [
                analysis_contracts.PeakRecord(
                    duration_s=duration,
                    value=round(value, 2),
                    activity_id=holder_by_peak.get((sport, duration)),
                )
                for duration, value in sorted(peaks.items())
            ],
            db,
        )

    derived = 0
    for sport, peaks in peaks_by_sport.items():
        existing = settings.power_thresholds.get(sport)
        if existing is not None and existing.source is analysis_constants.ThresholdSource.MANUAL:
            continue
        if activities_by_sport.get(sport, 0) < analysis_constants.FIT_MIN_ACTIVITIES:
            continue
        fit = analysis_maths.fit_cp_wprime(peaks)
        if fit is None:
            continue
        critical_power, w_prime_j, cv_pct = fit
        settings.power_thresholds[sport] = analysis_schema.PowerThresholdRead(
            value_w=round(critical_power, 1),
            w_prime_j=round(w_prime_j, 1),
            source=analysis_constants.ThresholdSource.AUTO,
            cv_pct=round(cv_pct, 2),
        )
        derived += 1

    if best_hr > 0 and (
        settings.hr_threshold is None or settings.hr_threshold.source is not analysis_constants.ThresholdSource.MANUAL
    ):
        settings.hr_threshold = analysis_schema.HeartRateThresholdRead(
            value_bpm=round(best_hr, 1),
            source=analysis_constants.ThresholdSource.AUTO,
        )
        derived += 1

    analysis_crud.store_settings(user_id, settings, db)
    db.commit()
    logger.info(
        "Analysis thresholds refreshed",
        extra=core_logger.context(user_id=user_id, sports=sorted(peaks_by_sport), derived=derived),
    )
    return settings


def _has_power_threshold(user_id: int, db: Session) -> bool:
    """Return whether the athlete has any power threshold stored.

    Args:
        user_id: The athlete.
        db: Database session.

    Returns:
        True when at least one sport has a threshold, derived or manual.
    """
    return bool(get_settings(user_id, db).power_thresholds)


def reconcile_missing_loads(db: Session) -> int:
    """Measure every activity that has no stored load yet.

    The safety net for missed ``activity.created`` events. An athlete with no
    threshold at all gets their thresholds derived first, because a power formula
    without a threshold computes nothing.

    Args:
        db: Database session.

    Returns:
        The number of load rows written.
    """
    return _walk_and_measure(db, only_missing=True)


def recompute_all_loads(db: Session) -> int:
    """Re-measure every activity against the current thresholds and formulas.

    Queued when thresholds change or a formula is added: stored loads are the product
    of the threshold they were divided by, so a new threshold reinterprets history.

    Args:
        db: Database session.

    Returns:
        The number of load rows written.
    """
    return _walk_and_measure(db, only_missing=False)


def _walk_and_measure(db: Session, *, only_missing: bool) -> int:
    """Walk activities in pages, measuring each one.

    Args:
        db: Database session.
        only_missing: Skip activities that already carry a load row.

    Returns:
        The number of load rows written.
    """
    written = 0
    after_id = 0
    prepared: set[int] = set()
    while True:
        activity_ids = activity_integration.list_activity_ids_after(db, after_id=after_id)
        if not activity_ids:
            break
        after_id = max(activity_ids)
        if only_missing:
            measured = analysis_crud.activity_ids_with_loads(activity_ids, db)
            activity_ids = [activity_id for activity_id in activity_ids if activity_id not in measured]
        for activity_id in activity_ids:
            activity = activity_integration.get_activity_for_derivation(activity_id, db)
            if activity is None or activity.user_id is None:
                continue
            if activity.user_id not in prepared:
                prepared.add(activity.user_id)
                if not _has_power_threshold(activity.user_id, db):
                    refresh_thresholds(activity.user_id, db)
                    activity = activity_integration.get_activity_for_derivation(activity_id, db)
                    if activity is None:
                        continue
            written += _store_loads(activity, db)
    logger.info(
        "Analysis measurement pass finished", extra=core_logger.context(only_missing=only_missing, loads=written)
    )
    return written


def list_activity_loads(
    activity_id: int,
    requester_user_id: int,
    db: Session,
) -> list[analysis_schema.ActivityLoadRead]:
    """Return every stored load of one activity, for its owner.

    Loads are the athlete's own analysis -- they name the thresholds they train to --
    so the gate is ownership rather than the activity's visibility.

    Args:
        activity_id: The activity to read.
        requester_user_id: The user asking.
        db: Database session.

    Returns:
        The activity's loads, in formula order.

    Raises:
        NotFoundError: If the activity is not the requester's.
    """
    if not activity_integration.owns_activity(activity_id, requester_user_id, db):
        raise core_exceptions.NotFoundError(f"Activity {activity_id} not found")
    return analysis_crud.list_activity_loads(activity_id, db)
