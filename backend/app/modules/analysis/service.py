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

import calendar
import math
import statistics
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import accumulate
from statistics import mean as arithmetic_mean
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

import core.exceptions as core_exceptions
import core.logger as core_logger
import modules.activities.activity.constants as activity_constants
import modules.activities.activity.integration_service as activity_integration
import modules.activities.activity.schema as activity_schema
import modules.activities.activity_laps.integration_service as activity_laps_integration
import modules.activities.activity_laps.schema as activity_laps_schema
import modules.activities.activity_streams.constants as activity_streams_constants
import modules.activities.activity_streams.integration_service as activity_streams_integration
import modules.analysis.constants as analysis_constants
import modules.analysis.contracts as analysis_contracts
import modules.analysis.course_export as course_export
import modules.analysis.crud as analysis_crud
import modules.analysis.graph as analysis_graph
import modules.analysis.intervals as analysis_intervals
import modules.analysis.maths as analysis_maths
import modules.analysis.methods as analysis_methods
import modules.analysis.route_plan as analysis_route_plan
import modules.analysis.schema as analysis_schema
import modules.analysis.segment_projection as analysis_projection
import modules.analysis.trail as analysis_trail
import modules.analysis.trail_osm as analysis_trail_osm
import modules.analysis.variants as analysis_variants
import modules.segments.integration_service as segments_integration
import modules.users.users.integration_service as users_integration

logger = core_logger.get_logger(__name__)

#: Activity type -> sport group, reversed once from the vocabulary the activities
#: module publishes (the same grouping its own statistics use, so an activity that
#: counts towards a sport's totals is measured against that sport's threshold).
#: Sports the grade-adjusted-pace model speaks for: running, where speed is effort
#: once the gradient is taken out. A bike's ground speed is about drag and drafting,
#: and walking needs no threshold; neither gets a critical speed.
_PACE_FIT_SPORTS = frozenset({"run"})

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
    provided = payload.model_fields_set
    if payload.acute_days is not None:
        current.acute_days = payload.acute_days
    if payload.chronic_days is not None:
        current.chronic_days = payload.chronic_days
    if payload.primary_method is not None:
        current.primary_method = payload.primary_method
    if "fit_from_s" in provided:
        current.fit_from_s = payload.fit_from_s
    if "fit_to_s" in provided:
        current.fit_to_s = payload.fit_to_s
    if current.fit_from_s is not None and current.fit_to_s is not None and current.fit_from_s >= current.fit_to_s:
        raise core_exceptions.InvalidInputError("'fit_from_s' must be shorter than 'fit_to_s'")
    if "resting_hr" in provided:
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
    if payload.speed_threshold_ms is not None:
        for sport, ms in payload.speed_threshold_ms.items():
            if ms is None:
                current.speed_thresholds.pop(sport, None)
            else:
                current.speed_thresholds[sport] = analysis_schema.SpeedThresholdRead(
                    value_ms=ms,
                    source=analysis_constants.ThresholdSource.MANUAL,
                )
    if "hr_threshold_bpm" in provided:
        if payload.hr_threshold_bpm is None:
            current.hr_threshold = None
        else:
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
) -> tuple[float, tuple[float, ...], tuple[datetime, ...]]:
    """Return one activity stream as ``(sample_period_s, values, timestamps)``.

    Power keeps its zeros (a stopped athlete does no work, and that is what the
    stored Normalized Power counts); heart-rate streams drop zeros, which are the
    sensor's "no signal" sentinel. A power stream that never rises above
    :data:`~modules.analysis.constants.POWER_NO_SIGNAL_MAX_W` is reported as
    absent: a pod-less session is a column of zeros, and the intensity chain
    must fall through to pace/heart rate rather than score it at zero.

    Args:
        activity_id: The activity whose stream is read.
        stream_type: Stream type code.
        value_key: Waypoint key to read (``power``, ``hr``, ``vel``, ``ele``).
        db: Database session.
        keep_zeros: Whether zero samples are kept.

    Returns:
        The sample period, the values and their timestamps; ``(0.0, (), ())`` when
        the activity has no such stream.
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
        return 0.0, (), ()
    if (
        stream_type == activity_streams_constants.STREAM_TYPE_POWER
        and max(values) < analysis_constants.POWER_NO_SIGNAL_MAX_W
    ):
        # A power channel that never left the noise floor carries no signal:
        # treat it as absent so the intensity chain scores the activity from
        # pace or heart rate instead of from zeros.
        return 0.0, (), ()
    return analysis_maths.sample_period_s(stamps), tuple(values), tuple(stamps)


def _align_to_power(
    values: tuple[float, ...],
    stamps: tuple[datetime, ...],
    power_stamps: tuple[datetime, ...],
    period_s: float,
) -> tuple[float, ...] | None:
    """Return one value per power sample: the nearest stream sample, within tolerance.

    A stream the power samples fall between (recorded at a slower rate, or with a
    dropped timestamp) still answers for the second it covers; a sample further
    away than :data:`analysis_constants.MAX_SAMPLE_PERIOD_S` is a gap, and reading
    across a gap would invent a speed or an altitude, so it becomes 0.0.

    Args:
        values: The stream's values.
        stamps: The stream's timestamps, aligned to ``values``.
        power_stamps: The timestamps of the power samples to answer for.
        period_s: Seconds between power samples.

    Returns:
        One value per power sample, or ``None`` when the stream carries nothing.
    """
    if not values:
        return None
    tolerance = max(period_s, analysis_constants.MAX_SAMPLE_PERIOD_S)
    aligned: list[float] = []
    cursor = 0
    for stamp in power_stamps:
        while cursor < len(stamps) and stamps[cursor] < stamp:
            cursor += 1
        best = None
        for candidate in (cursor - 1, cursor):
            if 0 <= candidate < len(stamps):
                distance = abs((stamps[candidate] - stamp).total_seconds())
                if distance <= tolerance and (best is None or distance < best[0]):
                    best = (distance, values[candidate])
        aligned.append(best[1] if best else 0.0)
    return tuple(aligned)


def _moving_seconds(activity: activity_schema.ActivityRead) -> float:
    """Return the active time a load scales with, falling back to elapsed time."""
    return float(activity.total_timer_time or activity.total_elapsed_time or 0.0)


def _local_day(activity: activity_schema.ActivityRead) -> date | None:
    """Return the calendar day an activity happened on, in its own timezone.

    "Which day is this load part of?" is a local question, and the activities
    module makes the same conversion for its own date buckets
    (``activity.query.local_start_time_expression``): ``start_time`` is a UTC
    instant, the stored IANA ``timezone`` says where it was run. Activities with
    no timezone (indoor imports) fall back to UTC, and a name the system cannot
    resolve (legacy aliases are canonicalized on write, but a row can predate
    that) does the same rather than losing the load.
    """
    if activity.start_time is None:
        return None
    try:
        zone = ZoneInfo(activity.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return activity.start_time.astimezone(zone).date()


# Per-athlete cache of per-session threshold fits, keyed (user_id, sport,
# measure). Built lazily by _session_threshold_fits (it reads whole streams)
# and persisted, so a restart answers from the table; dropped whenever
# thresholds or history are re-measured, so a recompute pass never scores
# against stale evidence.
_threshold_fits_cache: dict[tuple[int, str, str], list[analysis_contracts.ThresholdFitRecord]] = {}


def _grade_adjusted_speed(activity_id: int, db: Session) -> tuple[tuple[float, ...], float]:
    """Ground speed with the gradient taken out of it, as the speed fit consumes it.

    The same reading and adjustment the threshold pass performs: a hilly run
    feeds the same hyperbolic fit the flat ones do. Empty when the session
    carries no speed stream.
    """
    period_s, speeds, _ = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_SPEED,
        "vel",
        db,
        keep_zeros=True,
    )
    if not speeds or period_s <= 0:
        return (), 0.0
    _, elevations, _ = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_ELEVATION,
        "ele",
        db,
        keep_zeros=True,
    )
    grades = analysis_maths.grade_pct_series(elevations, speeds, period_s) if elevations else (0.0,) * len(speeds)
    return tuple(analysis_maths.grade_adjusted_run_speed(speeds, grades)), period_s


def _session_threshold_fits(
    user_id: int,
    sport: str,
    db: Session,
    *,
    measure: str = "power",
) -> list[analysis_contracts.ThresholdFitRecord]:
    """Return every session's threshold fit for one athlete, sport and measure.

    Every session whose duration-intensity curve can be fitted is stored, with
    the numbers the quality gate needs (regression error, best sustained window)
    and the power provenance: the trend chart plots all of them, the temporal
    envelope keeps only the gated ones (_gated_pairs), and neither rereads a
    stream once the table is warm. Speed fits are made over grade-adjusted
    speed, only for the sports the GAP model speaks for, exactly as the
    threshold pass does. Cached in-process and persisted per athlete, sport and
    measure; dropped whenever thresholds or the whole history are re-measured.

    Args:
        user_id: The athlete.
        sport: Sport group to fit.
        db: Database session.
        measure: ``power`` (watts) or ``speed`` (metres per second).

    Returns:
        The fit records, oldest first.
    """
    key = (user_id, sport, measure)
    cached = _threshold_fits_cache.get(key)
    if cached is not None:
        return cached
    stored = analysis_crud.list_threshold_fits(user_id, sport, measure, db)
    if stored:
        records = [
            analysis_contracts.ThresholdFitRecord(
                activity_id=row.activity_id,
                started_on=row.started_on,
                value=float(row.value),
                w_prime=float(row.w_prime),
                cv_pct=float(row.cv_pct),
                sustained=float(row.sustained),
                power_source=row.power_source,
            )
            for row in stored
        ]
        _threshold_fits_cache[key] = records
        return records
    records: list[analysis_contracts.ThresholdFitRecord] = []
    for activity in activity_integration.list_user_activities(user_id, db):
        if sport_group(activity.activity_type) != sport or activity.start_time is None:
            continue
        if measure == "speed":
            if sport not in _PACE_FIT_SPORTS:
                continue
            signal, period_s = _grade_adjusted_speed(activity.id, db)
            source = None
        else:
            period_s, power_w, _ = _stream_samples(
                activity.id,
                activity_streams_constants.STREAM_TYPE_POWER,
                "power",
                db,
                keep_zeros=True,
            )
            signal = power_w
            source = (activity.import_info or {}).get("power_source")
        if not signal or period_s <= 0:
            continue
        means = analysis_maths.best_means(signal, period_s)
        fit = analysis_maths.fit_cp_wprime(means)
        if fit is None:
            continue
        windows = [
            value
            for duration, value in means.items()
            if analysis_constants.FIT_MIN_S <= duration <= analysis_constants.FIT_MAX_S
        ]
        records.append(
            analysis_contracts.ThresholdFitRecord(
                activity_id=activity.id,
                started_on=activity.start_time.date(),
                value=fit[0],
                w_prime=fit[1],
                cv_pct=fit[2],
                sustained=max(windows, default=0.0),
                power_source=source,
            )
        )
    records.sort(key=lambda item: item.started_on)
    if records:
        analysis_crud.replace_threshold_fits(user_id, sport, measure, records, db)
    _threshold_fits_cache[key] = records
    return records


def _gated_pairs(records: Sequence[analysis_contracts.ThresholdFitRecord]) -> list[tuple[date, float]]:
    """Keep the fits that qualify as threshold evidence.

    A fit counts when its regression is tight and its ceiling is one the session
    itself can vouch for (no overshooting the best sustained window beyond
    ``TEMPORAL_CP_CEIL_FACTOR``); everything else still decorates the chart but
    does not pull the temporal line.
    """
    return [
        (record.started_on, record.value)
        for record in records
        if record.cv_pct <= analysis_constants.TEMPORAL_CP_MAX_CV_PCT
        and record.sustained > 0
        and record.value <= record.sustained * analysis_constants.TEMPORAL_CP_CEIL_FACTOR
    ]


def _threshold_at(
    user_id: int,
    sport: str,
    on_day: date,
    settings: analysis_schema.AnalysisSettingsRead,
    db: Session,
    *,
    measure: str,
) -> float | None:
    """The threshold of one measure to score an activity with: temporal where it can be.

    Falls back to the stored threshold when the athlete pinned a manual one,
    when they turned the temporal rule off, or when the window holds too little
    evidence for an evolution to mean anything. Where no threshold is stored at
    all, dense temporal evidence still answers: an athlete who never refreshed
    can be scored against their own evolving line rather than against nothing.

    Args:
        user_id: The athlete.
        sport: Sport group of the activity.
        on_day: Day of the activity.
        settings: The athlete's stored settings.
        db: Database session.
        measure: ``power`` (watts) or ``speed`` (metres per second).

    Returns:
        Threshold in the measure's units, or ``None`` when the sport has none.
    """
    if measure == "speed":
        stored = settings.speed_thresholds.get(sport)
        stored_value = float(stored.value_ms) if stored is not None else None
        decimals = 3
    else:
        stored = settings.power_thresholds.get(sport)
        stored_value = float(stored.value_w) if stored is not None else None
        decimals = 1
    if stored is not None and (
        stored.source == analysis_constants.ThresholdSource.MANUAL
        or int(dict(settings.method_params).get(analysis_constants.TEMPORAL_CP_PARAM, 1)) != 1
    ):
        return stored_value
    local = analysis_maths.temporal_threshold(
        _gated_pairs(_session_threshold_fits(user_id, sport, db, measure=measure)),
        on_day,
        back_days=analysis_constants.TEMPORAL_CP_BACK_DAYS,
        fwd_days=analysis_constants.TEMPORAL_CP_FWD_DAYS,
        decay_per_day=analysis_constants.TEMPORAL_CP_DECAY_PER_DAY,
        min_fits=analysis_constants.TEMPORAL_CP_MIN_FITS,
    )
    if local is not None:
        return round(local, decimals)
    return stored_value


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
    period_s, power_w, power_stamps = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_POWER,
        "power",
        db,
        keep_zeros=True,
    )
    _, hr_values, _ = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_HR,
        "hr",
        db,
        keep_zeros=False,
    )
    # The per-sample formulas read one aligned timeline rather than three clocks.
    # The clock is the power stream when the device carried one; without it, the
    # speed stream anchors, so pace-side formulas still get a ground timeline.
    _speed_period_s, speeds, speed_stamps = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_SPEED,
        "vel",
        db,
        keep_zeros=True,
    )
    ele_period_s, elevations, elevation_stamps = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_ELEVATION,
        "ele",
        db,
        keep_zeros=True,
    )
    if power_w and period_s > 0:
        anchor_period_s, anchor_stamps = period_s, power_stamps
    elif speeds and _speed_period_s > 0:
        anchor_period_s, anchor_stamps = _speed_period_s, speed_stamps
        period_s = _speed_period_s
    elif elevations and ele_period_s > 0:
        anchor_period_s, anchor_stamps = ele_period_s, elevation_stamps
        period_s = ele_period_s
    else:
        anchor_period_s, anchor_stamps = 0.0, ()
    if anchor_stamps:
        speed_ms = _align_to_power(speeds, speed_stamps, anchor_stamps, anchor_period_s) if speeds else None
        altitude_m = (
            _align_to_power(elevations, elevation_stamps, anchor_stamps, anchor_period_s) if elevations else None
        )
    else:
        speed_ms = altitude_m = None
    grade_pct = None
    if altitude_m is not None and speed_ms is not None:
        grade_pct = analysis_maths.grade_pct_series(altitude_m, speed_ms, anchor_period_s or 1.0)
    threshold = settings.power_thresholds.get(sport) if sport else None
    power_thr_w = (
        _threshold_at(activity.user_id or 0, sport, activity.start_time.date(), settings, db, measure="power")
        if sport and activity.start_time
        else (float(threshold.value_w) if threshold else None)
    )
    speed_threshold = settings.speed_thresholds.get(sport) if sport else None
    speed_thr_ms = (
        _threshold_at(activity.user_id or 0, sport, activity.start_time.date(), settings, db, measure="speed")
        if sport and activity.start_time
        else (float(speed_threshold.value_ms) if speed_threshold else None)
    )
    pace_calibration = settings.pace_calibrations.get(sport) if sport else None
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
        speed_ms=speed_ms,
        grade_pct=grade_pct,
        power_threshold_w=power_thr_w,
        speed_threshold_ms=speed_thr_ms,
        pace_calibration=(
            (
                float(pace_calibration.slope_w_per_ms),
                float(pace_calibration.intercept_w),
                float(pace_calibration.grade_w_per_ms_pct),
            )
            if pace_calibration
            else None
        ),
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
    if activity.user_id is None:
        return 0
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
    written = analysis_crud.replace_activity_loads(
        activity.id,
        activity.user_id,
        _moving_seconds(activity),
        _local_day(activity),
        sport,
        records,
        db,
    )
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


def _effective_fit_window(
    settings: analysis_schema.AnalysisSettingsRead,
    *,
    fit_from_s: int | None = None,
    fit_to_s: int | None = None,
) -> tuple[int, int]:
    """Return the duration range a fit should use: request, then athlete, then default.

    Raises:
        InvalidInputError: If the resulting range is inverted.
    """
    from_s = (
        fit_from_s
        if fit_from_s is not None
        else (settings.fit_from_s if settings.fit_from_s is not None else int(analysis_constants.FIT_MIN_S))
    )
    to_s = (
        fit_to_s
        if fit_to_s is not None
        else (settings.fit_to_s if settings.fit_to_s is not None else int(analysis_constants.FIT_MAX_S))
    )
    if from_s >= to_s:
        raise core_exceptions.InvalidInputError("fit window start must be shorter than its end")
    return from_s, to_s


def refresh_thresholds(user_id: int, db: Session) -> analysis_schema.AnalysisSettingsRead:
    """Derive an athlete's thresholds from their own records, and cache their curves.

    Power thresholds come from fitting the hyperbolic model to each sport's
    duration-intensity curve; running sports get the same fit over their
    grade-adjusted speed peaks (critical speed); the threshold heart rate is the
    best sustained 30-minute average heart rate. Manual values are left alone, and a sport whose
    peaks come from too few activities gets no derived threshold at all: a curve
    through one activity's samples invents a plausible-looking number.

    Args:
        user_id: The athlete.
        db: Database session.

    Returns:
        The settings after the refresh.
    """
    _threshold_fits_cache.clear()
    analysis_crud.clear_threshold_fits(user_id, db)
    activities = activity_integration.list_user_activities(user_id, db)
    settings = get_settings(user_id, db)
    peaks_by_sport: dict[str, dict[int, float]] = {}
    holder_by_peak: dict[tuple[str, int], int] = {}
    activities_by_sport: dict[str, int] = {}
    speed_peaks_by_sport: dict[str, dict[int, float]] = {}
    speed_activities_by_sport: dict[str, int] = {}
    paired_stats_by_sport: dict[str, list] = {}
    flags_by_sport: dict[str, list[analysis_contracts.PeakFlagRecord]] = {}
    best_hr = 0.0

    for activity in activities:
        sport = sport_group(activity.activity_type)
        if sport is None:
            continue
        period_s, power_w, power_stamps = _stream_samples(
            activity.id,
            activity_streams_constants.STREAM_TYPE_POWER,
            "power",
            db,
            keep_zeros=True,
        )
        if power_w:
            activities_by_sport[sport] = activities_by_sport.get(sport, 0) + 1
            means = analysis_maths.best_means(power_w, period_s)
            floor_w = analysis_constants.PEAK_OUTLIER_FLOOR_W.get(
                sport,
                analysis_constants.PEAK_OUTLIER_FLOOR_W_DEFAULT,
            )
            session_np_w = analysis_maths.normalized_power(power_w, period_s)
            violation = (
                analysis_maths.peak_violation(
                    means,
                    session_np_w,
                    ((float("inf"), floor_w),),
                )
                if activity.start_time
                else None
            )
            runaway = None
            if (
                violation is None
                and activity.start_time is not None
                and sport in _PACE_FIT_SPORTS
                and any(d < 30 and v >= analysis_constants.PEAK_RUNAWAY_MIN_W for d, v in means.items())
            ):
                # Sub-thirty-second watts cannot be judged in the abstract (a real
                # sprint peak is physically enormous), so the seconds are judged by
                # context: huge short average, barely any ground speed, and no
                # honest effort around it. A steep climb is slow too, but never
                # isolated - the neighboring samples carry the same effort.
                _, vel_values, vel_stamps = _stream_samples(
                    activity.id,
                    activity_streams_constants.STREAM_TYPE_SPEED,
                    "vel",
                    db,
                    keep_zeros=True,
                )
                aligned_v = (
                    _align_to_power(vel_values, vel_stamps, power_stamps, period_s)
                    if vel_values and power_stamps
                    else None
                )
                if aligned_v is not None and len(aligned_v) == len(power_w):
                    grades: list[float] | None = None
                    _, ele_values, ele_stamps = _stream_samples(
                        activity.id,
                        activity_streams_constants.STREAM_TYPE_ELEVATION,
                        "ele",
                        db,
                        keep_zeros=True,
                    )
                    if ele_values and power_stamps:
                        aligned_e = _align_to_power(ele_values, ele_stamps, power_stamps, period_s)
                        if aligned_e is not None and len(aligned_e) == len(power_w):
                            grades = list(analysis_maths.grade_pct_series(list(aligned_e), list(aligned_v), period_s))
                    runaway = analysis_maths.short_peak_runaway(power_w, list(aligned_v), period_s, grades)
            if violation is not None or runaway is not None:
                # The session's stream carries a peak no athlete holds for that
                # long (a sensor runaway: a kilowatt at walking speed). Skip it
                # whole - the same corrupt stretch bends every duration near it
                # - and record why, instead of silently out-ranking honest data.
                if violation is not None:
                    duration_s, value_w, ratio, cap = violation
                    reason = "implausible peak"
                else:
                    duration_s, value_w, _ = runaway
                    ratio = value_w / session_np_w if session_np_w else 0.0
                    cap = 0.0
                    reason = "isolated spike at walking speed"
                logger.warning(
                    "Held session out of the duration-intensity curve",
                    extra=core_logger.context(
                        user_id=user_id,
                        activity_id=activity.id,
                        reason=reason,
                        duration_s=duration_s,
                        value_w=round(value_w),
                        session_np_w=round(session_np_w),
                        ratio=round(ratio, 2),
                        cap=cap,
                    ),
                )
                flags_by_sport.setdefault(sport, []).append(
                    analysis_contracts.PeakFlagRecord(
                        activity_id=activity.id,
                        started_on=activity.start_time.date(),
                        duration_s=duration_s,
                        value_w=round(value_w, 1),
                        session_np_w=round(session_np_w, 1),
                        ratio=round(ratio, 2),
                        cap=cap,
                    )
                )
            else:
                merged = peaks_by_sport.setdefault(sport, {})
                for duration, value in means.items():
                    if value >= merged.get(duration, 0.0):
                        merged[duration] = value
                        holder_by_peak[(sport, duration)] = activity.id
        hr_period_s, hr_values, _ = _stream_samples(
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
        # The pace-side peaks: ground speed with the gradient taken out of it, so a
        # hilly run feeds the same hyperbolic fit the flat ones do. Only sports the
        # GAP model speaks for (running); a bike's speed is about drag, not effort.
        if sport in _PACE_FIT_SPORTS:
            vel_period_s, speeds, speed_stamps = _stream_samples(
                activity.id,
                activity_streams_constants.STREAM_TYPE_SPEED,
                "vel",
                db,
                keep_zeros=True,
            )
            if speeds:
                _, elevations, _ = _stream_samples(
                    activity.id,
                    activity_streams_constants.STREAM_TYPE_ELEVATION,
                    "ele",
                    db,
                    keep_zeros=True,
                )
                grades = (
                    analysis_maths.grade_pct_series(elevations, speeds, vel_period_s)
                    if elevations
                    else (0.0,) * len(speeds)
                )
                adjusted = analysis_maths.grade_adjusted_run_speed(speeds, grades)
                speed_activities_by_sport[sport] = speed_activities_by_sport.get(sport, 0) + 1
                # Paired watts and adjusted pace on one clock: the raw material for
                # the athlete's own pace-to-watts map, fit further below.
                if power_w and period_s > 0:
                    adjusted_on_power_clock = _align_to_power(adjusted, speed_stamps, power_stamps, period_s)
                    grade_on_power_clock = _align_to_power(grades, speed_stamps, power_stamps, period_s)
                    if adjusted_on_power_clock and grade_on_power_clock:
                        pace_window = (
                            max(int(analysis_methods._pace_window_s(settings.method_params) / period_s), 1) | 1
                        )
                        med_speeds = analysis_maths.moving_median(list(adjusted_on_power_clock), pace_window)
                        med_power = analysis_maths.moving_median(list(power_w), pace_window)
                        med_grades = analysis_maths.moving_median(list(grade_on_power_clock), pace_window)
                        stats = paired_stats_by_sport.setdefault(sport, [0] + [0.0] * 9)
                        grade_cap = analysis_constants.PACE_CAL_GRADE_CAP_PCT
                        for pace_value, watt_value, grade_value in zip(med_speeds, med_power, med_grades, strict=True):
                            if watt_value > 0 and pace_value > 0:
                                # the grade regressor: adjusted pace x clipped grade, so the
                                # correction scales with effort and saturates on steep trails
                                z = pace_value * min(max(grade_value, -grade_cap), grade_cap)
                                stats[0] += 1
                                stats[1] += pace_value
                                stats[2] += watt_value
                                stats[3] += pace_value * pace_value
                                stats[4] += pace_value * watt_value
                                stats[5] += watt_value * watt_value
                                stats[6] += z
                                stats[7] += z * z
                                stats[8] += z * watt_value
                                stats[9] += z * pace_value
                merged_speed = speed_peaks_by_sport.setdefault(sport, {})
                for duration, value in analysis_maths.best_means(adjusted, vel_period_s).items():
                    if value >= merged_speed.get(duration, 0.0):
                        merged_speed[duration] = value

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
    for sport in set(peaks_by_sport) | set(flags_by_sport):
        # Replaced even when empty: a session repaired later must not stay excluded.
        analysis_crud.replace_peak_flags(user_id, sport, flags_by_sport.get(sport, []), db)

    derived = 0
    fit_from_s, fit_to_s = _effective_fit_window(settings)
    for sport, peaks in peaks_by_sport.items():
        existing = settings.power_thresholds.get(sport)
        if existing is not None and existing.source is analysis_constants.ThresholdSource.MANUAL:
            continue
        if activities_by_sport.get(sport, 0) < analysis_constants.FIT_MIN_ACTIVITIES:
            continue
        fit = analysis_maths.fit_cp_wprime(peaks, float(fit_from_s), float(fit_to_s))
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

    for sport, peaks in speed_peaks_by_sport.items():
        existing = settings.speed_thresholds.get(sport)
        if existing is not None and existing.source is analysis_constants.ThresholdSource.MANUAL:
            continue
        if speed_activities_by_sport.get(sport, 0) < analysis_constants.FIT_MIN_ACTIVITIES:
            continue
        fit = analysis_maths.fit_cp_wprime(peaks, float(fit_from_s), float(fit_to_s))
        if fit is None:
            continue
        critical_speed, capacity_m, cv_pct = fit
        settings.speed_thresholds[sport] = analysis_schema.SpeedThresholdRead(
            value_ms=round(critical_speed, 3),
            w_prime_m=round(capacity_m, 1),
            source=analysis_constants.ThresholdSource.AUTO,
            cv_pct=round(cv_pct, 2),
        )
        derived += 1

    for sport, stats in paired_stats_by_sport.items():
        existing_cal = settings.pace_calibrations.get(sport)
        if existing_cal is not None and existing_cal.source is analysis_constants.ThresholdSource.MANUAL:
            continue
        count, sum_x, sum_y, sum_xx, sum_xy, sum_yy, sum_z, sum_zz, sum_zy, sum_zx = stats
        if count < analysis_constants.PACE_CAL_MIN_SECONDS:
            continue
        variance_x = sum_xx - sum_x * sum_x / count
        if variance_x <= 0:
            continue
        slope = (sum_xy - sum_x * sum_y / count) / variance_x
        if slope <= 0:
            continue
        intercept = (sum_y - slope * sum_x) / count
        variance_y = sum_yy - sum_y * sum_y / count
        if variance_y <= 0:
            continue
        # the grade term rides the flat map's residuals: the line the flat sessions
        # fit stays what it was, so zero-grade scoring is untouched by this term
        grade_coef = 0.0
        if sum_zz > 0:
            grade_coef = (sum_zy - slope * sum_zx - intercept * sum_z) / sum_zz
        sse = (
            sum_yy
            - 2.0 * (slope * sum_xy + intercept * sum_y + grade_coef * sum_zy)
            + slope * slope * sum_xx
            + intercept * intercept * count
            + grade_coef * grade_coef * sum_zz
            + 2.0 * slope * intercept * sum_x
            + 2.0 * slope * grade_coef * sum_zx
            + 2.0 * intercept * grade_coef * sum_z
        )
        r2 = 1.0 - sse / variance_y
        if r2 < analysis_constants.PACE_CAL_MIN_R2:
            continue
        settings.pace_calibrations[sport] = analysis_schema.PaceCalibrationRead(
            slope_w_per_ms=round(slope, 2),
            intercept_w=round(intercept, 1),
            grade_w_per_ms_pct=round(grade_coef, 3),
            r2=round(r2, 3),
            source=analysis_constants.ThresholdSource.AUTO,
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
    _threshold_fits_cache.clear()
    analysis_crud.clear_threshold_fits(None, db)
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


def list_loads_summaries(
    user_id: int,
    activity_ids: list[int],
    db: Session,
) -> list[analysis_schema.ActivityLoadsSummaryRead]:
    """Return the stored loads and power provenance of many of the athlete's activities.

    Built for the activities list, which shows one score per row: ownership is
    resolved once for the whole page, then the load rows (already scoped by the
    copied ``user_id``) and the stream tags are read in one query each, so a page
    costs three queries rather than one endpoint call per activity.

    Args:
        user_id: The athlete; only their rows and streams are read.
        activity_ids: Activities to summarize, in display order.
        db: Database session.

    Returns:
        One summary per requested id that has loads or a tagged power stream,
        following the request's order.
    """
    owned = sorted(activity_integration.owned_activity_ids(activity_ids, user_id, db))
    loads_by_id = analysis_crud.list_loads_for_activities(user_id, owned, db)
    sources = activity_streams_integration.get_power_sources(owned, db)
    summaries: list[analysis_schema.ActivityLoadsSummaryRead] = []
    for activity_id in dict.fromkeys(activity_ids):
        loads = loads_by_id.get(activity_id, [])
        if not loads and activity_id not in sources:
            continue
        summaries.append(
            analysis_schema.ActivityLoadsSummaryRead(
                activity_id=activity_id,
                power_recalibrated=sources.get(activity_id) == activity_streams_constants.POWER_SOURCE_STRYD,
                loads=loads,
            )
        )
    return summaries


def get_fitness_series(
    user_id: int,
    db: Session,
    *,
    method: analysis_constants.LoadMethod | None = None,
    acute_days: int | None = None,
    chronic_days: int | None = None,
    sport: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> analysis_schema.FitnessSeriesRead:
    """Return the athlete's daily load with the fatigue/fitness/balance series.

    A stored row says what one activity cost; the answer to "how fit is this
    athlete" sums those into days and relaxes each day toward the next with the
    time constants the athlete configured (overridable per request, so one chart
    can show 5/45 beside the default 7/42). One day's load adds up across sports
    because the body does not separate fatigue by sport; ``sport`` narrows the
    series to one sport's contribution when the athlete wants to see it apart.

    The decay is seeded from the athlete's first measured day, not from the
    requested range's start: a series that began at zero on the first plotted day
    would show a ramp-up that never happened (GoldenCheetah's PMC likewise starts
    at the first activity, not at the window's edge).

    Args:
        user_id: The athlete.
        db: Database session.
        method: Formula to sum, by default the athlete's primary one.
        acute_days: Override for the fatigue time constant, in days.
        chronic_days: Override for the fitness time constant, in days.
        sport: Sport group to restrict the series to, when given.
        from_date: First day to plot; defaults to the standard window before ``to_date``.
        to_date: Last day to plot; defaults to today.

    Returns:
        The series: one point per consecutive day of the requested range.

    Raises:
        InvalidInputError: If the range is inverted, or the sport is not one the
            activities module knows.
    """
    if sport is not None and sport not in activity_constants.ACTIVITY_TYPES_BY_SPORT:
        raise core_exceptions.InvalidInputError(f"Unknown sport '{sport}'")
    if from_date is not None and to_date is not None and from_date > to_date:
        raise core_exceptions.InvalidInputError("'from' must not be after 'to'")

    settings = get_settings(user_id, db)
    chosen = method or settings.primary_method
    tau_acute = acute_days if acute_days is not None else settings.acute_days
    tau_chronic = chronic_days if chronic_days is not None else settings.chronic_days
    end = to_date or date.today()
    start = from_date or end - timedelta(days=analysis_constants.FITNESS_WINDOW_DEFAULT_DAYS)

    totals = analysis_crud.daily_load_totals(user_id, chosen.value, db, sport=sport)
    load_by_day = {day: value for day, value in totals}
    first_measured = totals[0][0] if totals else start
    # A caller asking for the whole archive names a floor far in the past; clamp
    # the plotted start up to the first recorded day so they are not shown the
    # empty runway before it. EWMA is still seeded from that first measured day.
    start = max(start, first_measured)
    if start > end:
        raise core_exceptions.InvalidInputError("'from' must not be after 'to'")
    seed_from = min(start, first_measured)

    days = [seed_from + timedelta(days=offset) for offset in range((end - seed_from).days + 1)]
    loads = [load_by_day.get(day, 0.0) for day in days]
    fatigue = analysis_maths.ewf(loads, float(tau_acute))
    fitness = analysis_maths.ewf(loads, float(tau_chronic))

    plotted_from = (start - seed_from).days
    points = [
        analysis_schema.FitnessPointRead(
            date=day,
            load=round(loads[index], 2),
            acute=round(fatigue[index], 2),
            chronic=round(fitness[index], 2),
            balance=round(fitness[index] - fatigue[index], 2),
        )
        for index, day in enumerate(days)
        if index >= plotted_from
    ]
    return analysis_schema.FitnessSeriesRead(
        method=chosen,
        acute_days=tau_acute,
        chronic_days=tau_chronic,
        sport=sport,
        points=points,
    )


def _months_ago(moment: datetime, months: int) -> datetime:
    """Return ``moment`` shifted back a whole number of calendar months."""
    total = moment.year * 12 + moment.month - 1 - months
    year, month_index = divmod(total, 12)
    day = min(moment.day, calendar.monthrange(year, month_index + 1)[1])
    return moment.replace(year=year, month=month_index + 1, day=day)


def _duration_overlays(
    activities: list[activity_schema.ActivityRead],
    sport: str,
    db: Session,
    *,
    from_s: int,
    to_s: int,
) -> list[analysis_schema.PowerDurationOverlayRead]:
    """Recompute the curve inside each recent window, from the activities' streams.

    The cached peak table holds only all-time bests, so filtering its rows by date
    would answer "which records happened to fall inside the window", not "what the
    athlete can do now": a duration whose record is old would vanish even when a
    strong recent effort exists. Recomputing per activity keeps each window honest;
    the effort is the same block the threshold pass uses, over a slice of history.
    """
    now = datetime.now(UTC)
    since_by_months = {months: _months_ago(now, months) for months in analysis_constants.PD_OVERLAY_MONTHS}
    earliest = min(since_by_months.values())

    peaks_by_months: dict[int, dict[int, float]] = {months: {} for months in analysis_constants.PD_OVERLAY_MONTHS}
    holders: dict[tuple[int, int], int] = {}
    for activity in activities:
        if sport_group(activity.activity_type) != sport:
            continue
        started = activity.start_time
        if started is None or started.tzinfo is None or started < earliest:
            continue
        period_s, power_w, _ = _stream_samples(
            activity.id,
            activity_streams_constants.STREAM_TYPE_POWER,
            "power",
            db,
            keep_zeros=True,
        )
        if not power_w:
            continue
        means = analysis_maths.best_means(power_w, period_s)
        for months, since in since_by_months.items():
            if started < since:
                continue
            merged = peaks_by_months[months]
            for duration, value in means.items():
                if value >= merged.get(duration, 0.0):
                    merged[duration] = value
                    holders[(months, duration)] = activity.id

    overlays: list[analysis_schema.PowerDurationOverlayRead] = []
    for months in analysis_constants.PD_OVERLAY_MONTHS:
        peaks = peaks_by_months[months]
        if not peaks:
            continue
        fit = analysis_maths.fit_cp_wprime(peaks, float(from_s), float(to_s))
        overlays.append(
            analysis_schema.PowerDurationOverlayRead(
                months=months,
                points=[
                    analysis_schema.DurationPeakRead(
                        duration_s=duration,
                        value=round(value, 1),
                        activity_id=holders.get((months, duration)),
                    )
                    for duration, value in sorted(peaks.items())
                ],
                fit=(
                    analysis_schema.PowerDurationFitRead(
                        critical_power_w=round(fit[0], 1),
                        w_prime_j=round(fit[1], 1),
                        cv_pct=round(fit[2], 2),
                        fit_from_s=from_s,
                        fit_to_s=to_s,
                    )
                    if fit is not None
                    else None
                ),
            )
        )
    return overlays


def get_power_duration(
    user_id: int,
    sport: str,
    db: Session,
    *,
    fit_from_s: int | None = None,
    fit_to_s: int | None = None,
) -> analysis_schema.PowerDurationRead:
    """Return an athlete's duration-intensity curve for one sport.

    The points come from the cached peak table (the threshold pass writes them),
    the fit is recomputed on read with the effective window so the athlete can
    preview what a different window would give before refreshing thresholds, and
    the threshold section reports what the loads of this sport are *actually*
    measured against -- the loop the curve feeds: peaks -> CP -> IF -> TSS.

    Activities whose samples built the curve are listed newest first so a client
    can pick one to compute W' balance against.

    Args:
        user_id: The athlete.
        sport: Sport group to read.
        db: Database session.
        fit_from_s: Preview override for the fit window start.
        fit_to_s: Preview override for the fit window end.

    Returns:
        The curve, its fit, the stored threshold, and the contributing sessions.

    Raises:
        InvalidInputError: If the sport is unknown or the window is inverted.
    """
    if sport not in activity_constants.ACTIVITY_TYPES_BY_SPORT:
        raise core_exceptions.InvalidInputError(f"Unknown sport '{sport}'")
    settings = get_settings(user_id, db)
    from_s, to_s = _effective_fit_window(settings, fit_from_s=fit_from_s, fit_to_s=fit_to_s)

    peaks = analysis_crud.list_duration_peaks(user_id, sport, db)
    peaks_map = {peak.duration_s: peak.value for peak in peaks}
    fit = analysis_maths.fit_cp_wprime(peaks_map, float(from_s), float(to_s))

    sessions: list[analysis_schema.PowerDurationSessionRead] = []
    activities = activity_integration.list_user_activities(user_id, db)
    overlays = _duration_overlays(activities, sport, db, from_s=from_s, to_s=to_s)
    for activity in sorted(activities, key=lambda item: item.start_time or datetime.min, reverse=True):
        if sport_group(activity.activity_type) != sport or not activity.normalized_power:
            continue
        if len(sessions) >= analysis_constants.POWER_DURATION_SESSIONS_MAX:
            break
        sessions.append(
            analysis_schema.PowerDurationSessionRead(
                activity_id=activity.id,
                name=activity.name or "",
                start_time=activity.start_time.isoformat() if activity.start_time else None,
            )
        )

    return analysis_schema.PowerDurationRead(
        sport=sport,
        points=[
            analysis_schema.DurationPeakRead(duration_s=peak.duration_s, value=peak.value, activity_id=peak.activity_id)
            for peak in peaks
        ],
        fit=(
            analysis_schema.PowerDurationFitRead(
                critical_power_w=round(fit[0], 1),
                w_prime_j=round(fit[1], 1),
                cv_pct=round(fit[2], 2),
                fit_from_s=from_s,
                fit_to_s=to_s,
            )
            if fit is not None
            else None
        ),
        threshold=settings.power_thresholds.get(sport),
        sessions=sessions,
        overlays=overlays,
    )


def get_power_flags(
    user_id: int,
    sport: str,
    db: Session,
) -> list[analysis_schema.PowerFlagRead]:
    """Return the sessions the duration-intensity curve held out, and why.

    The peak builder refuses sessions whose best average for a duration is
    implausible against the session's own normalized power and an absolute
    floor. The refusals are recorded so the athlete can see which sessions the
    curve does not trust rather than wonder where a peak went.

    Args:
        user_id: The athlete.
        sport: Sport group to read.
        db: Database session.

    Returns:
        The flags, oldest session first.
    """
    return [
        analysis_schema.PowerFlagRead(
            activity_id=flag.activity_id,
            started_on=flag.started_on,
            duration_s=flag.duration_s,
            value_w=flag.value_w,
            session_np_w=flag.session_np_w,
            ratio=flag.ratio,
            cap=flag.cap,
        )
        for flag in analysis_crud.list_peak_flags(user_id, sport, db)
    ]


def get_cp_trend(
    user_id: int,
    sport: str,
    db: Session,
    *,
    window_days: int | None = None,
) -> analysis_schema.CpTrendRead:
    """Return one critical-power point per session, oldest first.

    Each session's own samples give its own duration-intensity curve; fitting
    that curve with the default 5-30 min window answers "what was this athlete's
    critical power *that day*", the evolution the single all-time curve cannot
    show. Sessions too short to hold efforts across four window durations cannot
    define a line and are skipped, exactly as in the threshold pass. The stored
    threshold rides along as the reference line the points move around. An
    athlete with no power stream anywhere answers the same question in speed:
    grade-adjusted critical speed per session, metres per second, for the sports
    the GAP model speaks for - the ``measure`` field says which story was told.

    Args:
        user_id: The athlete.
        sport: Sport group to read.
        db: Database session.
        window_days: Restrict to sessions started within this many days.

    Returns:
        The per-session fits plus the current threshold, for one sport.

    Raises:
        InvalidInputError: If the sport is unknown.
    """
    if sport not in activity_constants.ACTIVITY_TYPES_BY_SPORT:
        raise core_exceptions.InvalidInputError(f"Unknown sport '{sport}'")
    settings = get_settings(user_id, db)
    earliest: datetime | None = None
    if window_days is not None:
        earliest = datetime.now(UTC) - timedelta(days=window_days)

    decimals = 1
    records = _session_threshold_fits(user_id, sport, db, measure="power")
    measure = "power"
    if not records and sport in _PACE_FIT_SPORTS:
        # No watts anywhere: the same story told in the athlete's own speeds.
        # Grade-adjusted so a hilly run answers with the effort, not the terrain,
        # and the points carry metres per second instead of watts.
        measure = "speed"
        decimals = 3
        records = _session_threshold_fits(user_id, sport, db, measure="speed")

    points = [
        analysis_schema.CpTrendPointRead(
            activity_id=record.activity_id,
            date=record.started_on,
            cp_w=round(record.value, decimals),
            w_prime_j=round(record.w_prime, 1),
            cv_pct=round(record.cv_pct, 2),
            power_source=record.power_source,
        )
        for record in records
        if earliest is None or record.started_on >= earliest.date()
    ]

    envelope: list[analysis_schema.CpTrendEnvelopeRead] = []
    if points:
        # Sampled on the points' own days from the same quality-gated fits the
        # scorer uses, so the line on the chart is exactly the threshold the
        # loads were divided by. The chart's x-axis is categorical over sessions,
        # so a day between two sessions has no slot to sit in anyway.
        evidence = _gated_pairs(_session_threshold_fits(user_id, sport, db, measure=measure))
        for day in dict.fromkeys(point.date for point in points):
            value = analysis_maths.temporal_threshold(
                evidence,
                day,
                back_days=analysis_constants.TEMPORAL_CP_BACK_DAYS,
                fwd_days=analysis_constants.TEMPORAL_CP_FWD_DAYS,
                decay_per_day=analysis_constants.TEMPORAL_CP_DECAY_PER_DAY,
                min_fits=analysis_constants.TEMPORAL_CP_MIN_FITS,
            )
            if value is not None:
                envelope.append(
                    analysis_schema.CpTrendEnvelopeRead(
                        date=day,
                        cp_w=round(value, 3 if measure == "speed" else 1),
                    )
                )

    return analysis_schema.CpTrendRead(
        sport=sport,
        window_days=window_days,
        measure=measure,
        threshold=settings.power_thresholds.get(sport),
        speed_threshold=settings.speed_thresholds.get(sport),
        points=points,
        envelope=envelope,
    )


def get_wprime_balance(
    activity_id: int,
    requester_user_id: int,
    db: Session,
) -> analysis_schema.WprimeBalanceRead:
    """Return one activity's W' expenditure and balance, for its owner.

    The balance is the Skiba 2012 integral model over the stored power stream:
    work above CP is expended linearly and repleted exponentially with the
    stream's own time constant. Without a threshold power for the sport there is
    nothing to measure against; without a fitted W' there is no capacity to
    balance -- either way the anaerobic totals (work and time above CP) are
    still reported when CP exists, and the series simply stays empty.

    Args:
        activity_id: The activity to measure.
        requester_user_id: The user asking.
        db: Database session.

    Returns:
        The totals and the thinned balance series.

    Raises:
        NotFoundError: If the activity is not the requester's.
    """
    if not activity_integration.owns_activity(activity_id, requester_user_id, db):
        raise core_exceptions.NotFoundError(f"Activity {activity_id} not found")
    activity = activity_integration.get_activity_for_derivation(activity_id, db)
    sport = sport_group(activity.activity_type) if activity is not None else None
    settings = get_settings(requester_user_id, db)
    threshold = settings.power_thresholds.get(sport) if sport else None

    if activity is None or threshold is None:
        return analysis_schema.WprimeBalanceRead(
            cp_w=None,
            w_prime_j=None,
            tau_s=None,
            work_above_cp_j=0.0,
            time_above_cp_s=0.0,
            points=[],
        )

    period_s, power_w, _ = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_POWER,
        "power",
        db,
        keep_zeros=True,
    )
    cp_w = float(threshold.value_w)
    if not power_w or period_s <= 0:
        return analysis_schema.WprimeBalanceRead(
            cp_w=cp_w,
            w_prime_j=float(threshold.w_prime_j) if threshold.w_prime_j else None,
            tau_s=None,
            work_above_cp_j=0.0,
            time_above_cp_s=0.0,
            points=[],
        )

    work_j, time_s = analysis_maths.work_above_threshold(power_w, period_s, cp_w)
    if not threshold.w_prime_j:
        return analysis_schema.WprimeBalanceRead(
            cp_w=cp_w,
            w_prime_j=None,
            tau_s=None,
            work_above_cp_j=round(work_j, 1),
            time_above_cp_s=round(time_s, 1),
            points=[],
        )

    w_prime_j = float(threshold.w_prime_j)
    tau_s = analysis_maths.skiba_tau_s(cp_w, power_w)
    balance = analysis_maths.wprime_balance(power_w, period_s, cp_w, w_prime_j, tau_s)
    series = [(index * period_s, remaining) for index, remaining in enumerate(balance, start=1)]
    return analysis_schema.WprimeBalanceRead(
        cp_w=cp_w,
        w_prime_j=w_prime_j,
        tau_s=round(tau_s, 1),
        work_above_cp_j=round(work_j, 1),
        time_above_cp_s=round(time_s, 1),
        points=[
            analysis_schema.WprimePointRead(t_s=round(t, 1), remaining_j=round(remaining, 1))
            for t, remaining in analysis_maths.downsample(series, analysis_constants.WPRIME_POINTS_MAX)
        ],
    )


def get_activity_intervals(
    activity_id: int,
    requester_user_id: int,
    db: Session,
) -> analysis_schema.ActivityIntervalsRead:
    """Return how one activity breaks down into efforts: reps, climbs, the steady parts.

    Computed on read from the activity's own samples, against that athlete's threshold
    and the session's own median power, so the same shape parameters separate a race
    pushed for 4 km from five reps without any general-population number entering. An
    activity with no power stream answers ``steady`` with no segments: the split reads
    power, and nothing is guessed from heart rate alone.

    Args:
        activity_id: The activity to split.
        requester_user_id: The athlete asking; only their own activity answers.
        db: Database session.

    Returns:
        The session shape and the contiguous segments covering its timeline.

    Raises:
        NotFoundError: If the activity is not the requester's.
    """
    if not activity_integration.owns_activity(activity_id, requester_user_id, db):
        raise core_exceptions.NotFoundError(f"Activity {activity_id} not found")
    activity = activity_integration.get_activity_for_derivation(activity_id, db)
    if activity is None:
        return analysis_schema.ActivityIntervalsRead(
            shape=analysis_intervals.SessionShape.STEADY,
            threshold_w=None,
            base_power_w=0.0,
            segments=[],
        )
    sport = sport_group(activity.activity_type)
    data = _load_input(activity, sport, db)
    ground_period, _ground_stamps, ground_power, ground_speeds, ground_grades, ground_hr, altitudes_m = (
        _ground_timeline(activity.id, db)
    )
    ground_pace = (
        analysis_methods.pace_series(
            ground_speeds, ground_period, data.speed_threshold_ms, ground_grades, dict(data.parameters)
        )
        if ground_speeds and data.speed_threshold_ms
        else None
    )
    ground_pace_raw = (
        analysis_methods.pace_raw_series(
            ground_speeds, ground_period, data.speed_threshold_ms, ground_grades, dict(data.parameters)
        )
        if ground_speeds and data.speed_threshold_ms
        else None
    )
    analysis = analysis_intervals.analyse_activity(
        power_w=data.power_w or (),
        period_s=data.period_s,
        threshold_w=data.power_threshold_w,
        speeds_ms=data.speed_ms,
        grades_pct=data.grade_pct,
        altitudes_m=altitudes_m,
        parameters=dict(data.parameters),
    )

    def _segment_rap_ms(segment: analysis_intervals.Segment) -> float | None:
        if ground_pace is None or ground_period <= 0:
            return None
        start = int(segment.start_s / ground_period)
        stop = int((segment.start_s + segment.duration_s) / ground_period)
        window = ground_pace[start : max(stop, start + 1)]
        return round(sum(window) / len(window), 2) if window else None

    return analysis_schema.ActivityIntervalsRead(
        shape=analysis.shape,
        threshold_w=analysis.threshold_w,
        base_power_w=analysis.base_power_w,
        rules=analysis_schema.IntervalRulesRead(**analysis.rules) if analysis.rules else None,
        segments=[
            analysis_schema.IntervalSegmentRead(
                kind=segment.kind,
                start_s=segment.start_s,
                duration_s=segment.duration_s,
                distance_m=segment.distance_m,
                avg_power_w=segment.avg_power_w,
                intensity_factor=segment.intensity_factor,
                contrast=segment.contrast,
                avg_grade_pct=segment.avg_grade_pct,
                avg_speed_ms=segment.avg_speed_ms,
                pace_adjusted_ms=_segment_rap_ms(segment),
                elevation_gain_m=segment.elevation_gain_m,
                elevation_loss_m=segment.elevation_loss_m,
                rss=segment.rss,
                rss_per_min=segment.rss_per_min,
            )
            for segment in analysis.segments
        ],
        rss_by_source=[
            analysis_schema.RssSourceTotalsRead(source=source, **bands)
            for source, bands in analysis_methods.rss_bands_by_source(data).items()
        ],
        climbs=[
            analysis_schema.TerrainClimbRead(**vars(climb))
            for climb in analysis_intervals.terrain_climbs(
                altitudes_m=altitudes_m,
                speeds_ms=ground_speeds,
                period_s=ground_period,
                power_w=ground_power,
                hr_bpm=ground_hr,
                threshold_w=analysis.threshold_w,
                speed_threshold_ms=data.speed_threshold_ms,
                hr_threshold_bpm=data.hr_threshold_bpm,
                base_power_w=analysis.base_power_w,
                parameters=dict(data.parameters),
                pace_calibration=data.pace_calibration,
                pace_ms=ground_pace,
                pace_raw_ms=ground_pace_raw,
            )
        ],
    )


def get_activity_lap_dynamics(
    activity_id: int,
    requester_user_id: int,
    db: Session,
) -> list[analysis_schema.LapDynamicsRead]:
    """Return per-lap dynamics: power, cadence-derived stride, elevation split and aRSS.

    The columns come from what the device already recorded per lap; the stride is
    derived (speed over cadence, falling back to distance over cycles), the ascent
    and descent are reported as a pair so a puja-i-baixa lap reads honestly, and
    the aRSS is the daily-load kernel summed over the lap's own window of samples -
    which is why the rows add back up to the activity's rss_total in the PMC.

    Args:
        activity_id: The activity to analyze.
        requester_user_id: The athlete asking; only their own activity answers.
        db: Database session.

    Returns:
        One entry per lap, in recorded order; empty when the activity has no laps.

    Raises:
        NotFoundError: If the activity is not the requester's.
    """
    if not activity_integration.owns_activity(activity_id, requester_user_id, db):
        raise core_exceptions.NotFoundError(f"Activity {activity_id} not found")
    activity = activity_integration.get_activity_for_derivation(activity_id, db)
    if activity is None:
        return []
    sport = sport_group(activity.activity_type)
    settings = get_settings(requester_user_id, db)
    threshold = settings.power_thresholds.get(sport)
    threshold_w = threshold.value_w if threshold else None

    laps = activity_laps_integration.list_laps_for_derivation(activity_id, db)
    dynamics: list[analysis_schema.LapDynamicsRead] = []
    for lap in laps:
        stride_m = _lap_stride_length_m(lap)
        gain = lap.total_ascent if lap.total_ascent is not None else None
        loss = lap.total_descent if lap.total_descent is not None else None
        dynamics.append(
            analysis_schema.LapDynamicsRead(
                lap_id=lap.id,
                avg_power_w=float(lap.avg_power) if lap.avg_power else None,
                stride_length_m=round(stride_m, 2) if stride_m else None,
                elevation_gain_m=float(gain) if gain is not None else None,
                elevation_loss_m=float(loss) if loss is not None else None,
                rss=None,
            )
        )

    speed_threshold = settings.speed_thresholds.get(sport)
    hr_threshold = settings.hr_threshold
    pace_calibration = settings.pace_calibrations.get(sport)
    _apply_lap_rss(
        lap_windows=laps,
        dynamics=dynamics,
        activity_id=activity_id,
        threshold_w=threshold_w,
        speed_threshold_ms=float(speed_threshold.value_ms) if speed_threshold else None,
        hr_threshold_bpm=float(hr_threshold.value_bpm) if hr_threshold else None,
        parameters=dict(settings.method_params),
        db=db,
        pace_calibration=(
            (
                float(pace_calibration.slope_w_per_ms),
                float(pace_calibration.intercept_w),
                float(pace_calibration.grade_w_per_ms_pct),
            )
            if pace_calibration
            else None
        ),
    )
    return dynamics


def _ground_timeline(
    activity_id: int,
    db: Session,
) -> tuple[
    float,
    tuple[datetime, ...],
    tuple[float, ...] | None,
    tuple[float, ...] | None,
    tuple[float, ...] | None,
    tuple[float, ...] | None,
]:
    """Return one activity's samples on a single clock: ``(period, stamps, power, speed, grade, hr)``.

    The anchor is the power stream when the device carried one, otherwise the
    speed stream, otherwise heart rate; every other stream is aligned onto it and
    the grade series derives from the aligned altitude and speed. A stream the
    device never recorded comes back ``None``, so the intensity chain can fall
    through the sources honestly.

    Args:
        activity_id: The activity whose streams are read.
        db: Database session.

    Returns:
        The clock's period and stamps, then the aligned power, speed, grade and
        heart-rate series (any of which may be ``None``).
    """
    power_period, power_values, power_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_POWER,
        "power",
        db,
        keep_zeros=True,
    )
    speed_period, speeds, speed_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_SPEED,
        "vel",
        db,
        keep_zeros=True,
    )
    hr_period, hr_values, hr_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_HR,
        "hr",
        db,
        keep_zeros=False,
    )
    _, elevations, elevation_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_ELEVATION,
        "ele",
        db,
        keep_zeros=True,
    )
    if power_values and power_period > 0:
        anchor_period, anchor_stamps = power_period, power_stamps
        power_aligned: tuple[float, ...] | None = power_values
    elif speeds and speed_period > 0:
        anchor_period, anchor_stamps = speed_period, speed_stamps
        power_aligned = None
    elif hr_values and hr_period > 0:
        anchor_period, anchor_stamps = hr_period, hr_stamps
        power_aligned = None
    else:
        return 0.0, (), None, None, None, None, None
    stamps = _utc_stamps(anchor_stamps)
    speed_aligned = (
        _align_to_power(speeds, _utc_stamps(speed_stamps), stamps, anchor_period)
        if speeds and anchor_stamps is not speed_stamps
        else (speeds if speeds else None)
    )
    hr_aligned = (
        _align_to_power(hr_values, _utc_stamps(hr_stamps), stamps, anchor_period)
        if hr_values and anchor_stamps is not hr_stamps
        else (hr_values if hr_values else None)
    )
    altitude_m = (
        _align_to_power(elevations, _utc_stamps(elevation_stamps), stamps, anchor_period) if elevations else None
    )
    grades: tuple[float, ...] | None = None
    if altitude_m is not None and speed_aligned is not None:
        grades = analysis_maths.grade_pct_series(altitude_m, speed_aligned, anchor_period)
    return anchor_period, stamps, power_aligned, speed_aligned, grades, hr_aligned, altitude_m


def _apply_lap_rss(
    lap_windows: list[activity_laps_schema.ActivityLapsRead],
    dynamics: list[analysis_schema.LapDynamicsRead],
    activity_id: int,
    threshold_w: float | None,
    speed_threshold_ms: float | None,
    hr_threshold_bpm: float | None,
    parameters: Mapping[str, float],
    db: Session,
    pace_calibration: Sequence[float] | None = None,
) -> None:
    """Fill each lap's aRSS from the activity's own samples, through the intensity chain.

    Watts when the device carried them; otherwise grade-adjusted pace over the
    athlete's critical speed; otherwise heart rate over the threshold heart rate.
    Whichever scored a lap is recorded in ``rss_source`` so the table can mark it.
    """
    if not lap_windows:
        return
    period_s, stamps, power_values, speeds, grades, hr_values, _altitude = _ground_timeline(activity_id, db)
    if not stamps or period_s <= 0:
        return
    pace_values = (
        analysis_methods.pace_series(speeds, period_s, speed_threshold_ms, grades, parameters)
        if speeds and speed_threshold_ms
        else None
    )
    pace_raw = (
        analysis_methods.pace_raw_series(speeds, period_s, speed_threshold_ms, grades, parameters)
        if speeds and speed_threshold_ms
        else None
    )
    for lap, entry in zip(lap_windows, dynamics, strict=True):
        window = _lap_sample_window(stamps, lap.start_time, float(lap.total_elapsed_time or 0.0))
        if window is None:
            continue
        start, stop = window
        window_scores = analysis_methods.window_scores_by_source(
            power_values[start:stop] if power_values is not None else None,
            analysis_methods.pace_window(pace_values, pace_raw, period_s, parameters, start, stop),
            hr_values[start:stop] if hr_values is not None else None,
            grades[start:stop] if grades is not None else None,
            period_s,
            threshold_w,
            speed_threshold_ms,
            hr_threshold_bpm,
            parameters,
            pace_calibration,
        )
        source = next((key for key in ("power", "pace", "hr") if key in window_scores), None)
        entry.source_scores = {key: round(value, 1) for key, value in window_scores.items()} or None
        score = window_scores.get(source, 0.0) if source else 0.0
        entry.rss = round(score, 1) if score > 0 and source else None
        entry.rss_source = source if entry.rss is not None else None
        if pace_values is not None:
            window_pace = pace_values[start:stop]
            if window_pace:
                entry.rap_ms = round(sum(window_pace) / len(window_pace), 2)
        elapsed_s = float(lap.total_elapsed_time or 0.0)
        if entry.rss is not None and elapsed_s > 0:
            entry.rss_per_min = round(entry.rss * 60.0 / elapsed_s, 2)


def _utc_stamps(stamps: tuple[datetime, ...]) -> tuple[datetime, ...]:
    """Return the stream's stamps as UTC-aware, whatever clock the device wrote."""
    return tuple(stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC) for stamp in stamps)


def compute_window_stats(
    activity_id: int,
    user_id: int,
    windows: Sequence[tuple[datetime, datetime]],
    db: Session,
) -> list[analysis_contracts.WindowStats]:
    """Return mean power, heart rate and aRSS per minute for time windows of one activity.

    Each window is judged on its own samples - the streams are cut by timestamp
    half-open, ``[start, end)`` on the activity's one ground timeline - and the
    aRSS follows the intensity chain: watts, grade-adjusted pace, heart rate.
    Nothing is invented where a stream is simply absent.

    Args:
        activity_id: The activity whose streams are read.
        user_id: The athlete whose settings evaluate the windows.
        windows: ``(start, end)`` wall-clock pairs in the streams' own clock.
        db: Database session.

    Returns:
        One stats record per window, in the order given.
    """
    if not windows:
        return []
    activity = activity_integration.get_activity_for_derivation(activity_id, db)
    if activity is None:
        return [analysis_contracts.WindowStats() for _ in windows]
    settings = get_settings(user_id, db)
    sport = sport_group(activity.activity_type)
    threshold = settings.power_thresholds.get(sport)
    threshold_w = threshold.value_w if threshold else None
    speed_threshold = settings.speed_thresholds.get(sport)
    speed_threshold_ms = speed_threshold.value_ms if speed_threshold else None
    heart_rate_threshold = settings.hr_threshold
    hr_threshold_bpm = heart_rate_threshold.value_bpm if heart_rate_threshold else None
    parameters = dict(settings.method_params)
    pace_calibration = settings.pace_calibrations.get(sport)
    calibration_pair = (
        (
            float(pace_calibration.slope_w_per_ms),
            float(pace_calibration.intercept_w),
            float(pace_calibration.grade_w_per_ms_pct),
        )
        if pace_calibration
        else None
    )

    period_s, stamps, power_values, speeds, grades, hr_values, _altitude = _ground_timeline(activity_id, db)
    pace_values = (
        analysis_methods.pace_series(speeds, period_s, speed_threshold_ms, grades, parameters)
        if speeds and speed_threshold_ms
        else None
    )
    pace_raw = (
        analysis_methods.pace_raw_series(speeds, period_s, speed_threshold_ms, grades, parameters)
        if speeds and speed_threshold_ms
        else None
    )
    stats: list[analysis_contracts.WindowStats] = []
    for start, end in windows:
        start_a = start if start.tzinfo else start.replace(tzinfo=UTC)
        end_a = end if end.tzinfo else end.replace(tzinfo=UTC)
        elapsed_s = (end_a - start_a).total_seconds()
        avg_power: float | None = None
        avg_hr: float | None = None
        score: float | None = None
        source: str | None = None
        per_min: float | None = None
        rap_ms: float | None = None
        scores: dict[str, float] | None = None
        if stamps and period_s > 0 and elapsed_s > 0:
            cut = bisect_left(stamps, start_a)
            stop = bisect_left(stamps, end_a)
            if stop > cut:
                if power_values is not None and any(power_values[cut:stop]):
                    avg_power = round(arithmetic_mean(power_values[cut:stop]), 1)
                if hr_values is not None and any(hr_values[cut:stop]):
                    avg_hr = round(arithmetic_mean([v for v in hr_values[cut:stop] if v > 0]), 0)
                all_scores = analysis_methods.window_scores_by_source(
                    power_values[cut:stop] if power_values is not None else None,
                    analysis_methods.pace_window(pace_values, pace_raw, period_s, parameters, cut, stop),
                    hr_values[cut:stop] if hr_values is not None else None,
                    grades[cut:stop] if grades is not None else None,
                    period_s,
                    threshold_w,
                    speed_threshold_ms,
                    hr_threshold_bpm,
                    parameters,
                    calibration_pair,
                )
                chain_source = next((key for key in ("power", "pace", "hr") if key in all_scores), None)
                chain_score = all_scores.get(chain_source, 0.0) if chain_source else 0.0
                if chain_score > 0 and chain_source is not None:
                    score = round(chain_score, 1)
                    source = chain_source
                    per_min = round(score * 60.0 / elapsed_s, 2)
                scores = {key: round(value, 1) for key, value in all_scores.items()} or None
                if pace_values is not None and stop > cut:
                    window_pace = pace_values[cut:stop]
                    rap_ms = round(sum(window_pace) / len(window_pace), 2) if window_pace else None
        stats.append(
            analysis_contracts.WindowStats(
                avg_power_w=avg_power,
                avg_hr_bpm=avg_hr,
                rss=score,
                rss_source=source,
                rss_per_min=per_min,
                source_scores=scores,
                rap_ms=rap_ms,
            )
        )
    return stats


def _lap_sample_window(
    power_stamps: tuple[datetime, ...],
    lap_start: datetime | None,
    elapsed_s: float,
) -> tuple[int, int] | None:
    """Return the [start, stop) sample range a lap covers, or ``None``.

    Laps are located by timestamp inside the power stream: their recorded start plus
    elapsed time is the same clock the stream samples carry, so the slice needs no
    resampling, and consecutive laps partition the powered seconds the daily load sums.
    """
    if lap_start is None or elapsed_s <= 0 or not power_stamps:
        return None
    start_stamp = lap_start if lap_start.tzinfo else lap_start.replace(tzinfo=UTC)
    end_stamp = start_stamp + timedelta(seconds=elapsed_s)
    start = bisect_left(power_stamps, start_stamp)
    stop = bisect_left(power_stamps, end_stamp)
    if start >= len(power_stamps) or stop <= start:
        return None
    return start, stop


def _lap_stride_length_m(lap: activity_laps_schema.ActivityLapsRead) -> float | None:
    """Return ground covered per cycle for one lap, or ``None`` when unmeasurable."""
    distance = float(lap.total_distance or 0.0)
    elapsed = float(lap.total_elapsed_time or 0.0)
    if lap.enhanced_avg_speed:
        speed: float | None = float(lap.enhanced_avg_speed)
    elif elapsed > 0 and distance > 0:
        speed = distance / elapsed
    else:
        speed = None
    if speed and lap.avg_cadence:
        return speed / (float(lap.avg_cadence) / 60.0)
    if lap.total_cycles and distance > 0:
        return distance / float(lap.total_cycles)
    return None


def _trail_streams(
    activity_id: int,
    db: Session,
) -> tuple[float, tuple[float, ...], tuple[datetime, ...], list[float], list[float] | None] | None:
    """Read and align the streams the technical-terrain detector needs.

    Args:
        activity_id: The activity to read.
        db: Database session.

    Returns:
        ``(period_s, speeds, stamps, altitude, cadence)`` sharing one clock - the
        altitude carrying across gaps and the cadence converted to steps per
        minute - or ``None`` when the activity cannot answer at all.
    """
    period_s, speeds, stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_SPEED,
        "vel",
        db,
        keep_zeros=True,
    )
    if not speeds or period_s <= 0:
        return None
    _, ele_values, ele_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_ELEVATION,
        "ele",
        db,
        keep_zeros=True,
    )
    _, cad_values, cad_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_CADENCE,
        "cad",
        db,
        keep_zeros=True,
    )
    ele_a = _align_to_power(ele_values, ele_stamps, stamps, period_s) if ele_values else None
    cad_a = _align_to_power(cad_values, cad_stamps, stamps, period_s) if cad_values else None
    if ele_a is None:
        return None
    # alignment marks a gap as 0.0; altitude carries across a gap (jumping to
    # sea level twice would forge two walls)
    ele_fixed: list[float] = []
    last = 0.0
    for value in ele_a:
        if value:
            last = float(value)
        ele_fixed.append(last)
    # Garmin stores cycles/min; stride maths wants steps per minute
    cadence: list[float] | None = None
    if cad_a is not None:
        live = [float(value) for value in cad_a if value > 0]
        if live:
            factor = 2.0 if statistics.median(live) < 110.0 else 1.0
            cadence = [float(value) * factor for value in cad_a]
    return period_s, speeds, stamps, ele_fixed, cadence


def _trail_observation(
    activity_id: int,
    db: Session,
) -> tuple[analysis_trail.TrailAnalysis, tuple[datetime, ...]] | None:
    """Run the technical-terrain detector over one activity's own streams.

    Args:
        activity_id: The activity to measure.
        db: Database session.

    Returns:
        The :class:`modules.analysis.trail.TrailAnalysis` plus the stream's
        timestamps (a section's sample range is in that clock), or ``None`` when
        the activity cannot be measured at all.
    """
    streams = _trail_streams(activity_id, db)
    if streams is None:
        return None
    period_s, speeds, stamps, ele_fixed, cadence = streams
    cum: list[float] = []
    running = 0.0
    for speed in speeds:
        cum.append(running)
        running += float(speed) * period_s
    return analysis_trail.analyse_technical_sections(speeds, cum, ele_fixed, cadence), stamps


def _trail_track(activity_id: int, db: Session) -> list[tuple[datetime, float, float]]:
    """Return the activity's map polyline, clocked, for drawing a section on it.

    Args:
        activity_id: The activity to read.
        db: Database session.

    Returns:
        ``(timestamp, lat, lon)`` per waypoint, empty when there is no map stream.
    """
    map_stream = activity_streams_integration.get_stream_for_derivation(
        activity_id,
        activity_streams_constants.STREAM_TYPE_MAP,
        db,
    )
    track: list[tuple[datetime, float, float]] = []
    for point in (map_stream.stream_waypoints if map_stream is not None else None) or []:
        if not isinstance(point, dict) or point.get("lat") is None or point.get("lon") is None:
            continue
        track.append((datetime.fromisoformat(point["time"]), float(point["lat"]), float(point["lon"])))
    return track


def _empty_trail_sections(activity_id: int) -> analysis_schema.TrailSectionsRead:
    """Return the answer for an activity the detector cannot measure.

    Args:
        activity_id: The activity asked about.

    Returns:
        An empty payload carrying the threshold the model would have used.
    """
    return analysis_schema.TrailSectionsRead(
        activity_id=activity_id,
        threshold=analysis_constants.TRAIL_THRESHOLD,
        technical_ratio=analysis_constants.TRAIL_TECHNICAL_RATIO,
        hard_ratio=analysis_constants.TRAIL_HARD_RATIO,
        very_max_length_m=analysis_constants.TRAIL_VERY_MAX_LENGTH_M,
        v_base_ms=0.0,
        cadence_available=False,
        technical_m=0.0,
        total_m=0.0,
        sections=[],
    )


def _trail_cartography(activity_id: int, db: Session) -> dict[float, analysis_schema.TrailOsmRead]:
    """Look up what the imported cartography says about each measured stretch.

    The match itself is a separate, offline-bounded job (the extract is imported by
    hand, not fetched per request); here the answer is simply joined back onto the
    stretch it belongs to, by its ground position. A stretch with no row, or one
    the map does not describe, answers without the field.

    Args:
        activity_id: The activity being read.
        db: Database session.

    Returns:
        A mapping of stretch start position to the cartographic claim.
    """
    stored = analysis_crud.list_trail_observation(activity_id, analysis_constants.trail_model_stamp(), db)
    if stored is None:
        return {}
    _, rows = stored
    way_ids = [row.osm_way_id for row in rows if row.osm_way_id is not None]
    paths = analysis_crud.get_osm_paths_by_ids(way_ids, db)
    answers: dict[float, analysis_schema.TrailOsmRead] = {}
    for row in rows:
        path = paths.get(row.osm_way_id) if row.osm_way_id is not None else None
        if path is None or row.osm_start_m is None or row.osm_end_m is None or row.osm_covered_m is None:
            continue
        tags = path.tags or {}
        answers[round(row.start_m, 3)] = analysis_schema.TrailOsmRead(
            way_id=path.way_id,
            name=path.name,
            sac_scale=tags.get("sac_scale"),
            mtb_scale=tags.get("mtb:scale"),
            surface=tags.get("surface"),
            smoothness=tags.get("smoothness"),
            tracktype=tags.get("tracktype"),
            trail_visibility=tags.get("trail_visibility"),
            width=tags.get("width"),
            incline=tags.get("incline"),
            start_m=row.osm_start_m,
            end_m=row.osm_end_m,
            covered_m=row.osm_covered_m,
            cache_date=path.cache_date.isoformat(),
        )
    return answers


def get_trail_sections(
    activity_id: int,
    requester_user_id: int,
    db: Session,
) -> analysis_schema.TrailSectionsRead:
    """Return which stretches of one trail run read as technical, for its owner.

    Experimental (the calibration loop is open): spatial windows compare the
    observed pace against what the grade alone explains - the same Minetti GAP
    the pace-side numbers use - and where the pace falls short while stride
    mechanics change (cadence erratic, stride braking on descents) the ground
    itself is what costs. Only run sports answer, measured from the stored
    streams so the answer always follows the data.

    Args:
        activity_id: The activity to read.
        requester_user_id: The athlete asking; only their own activity answers.
        db: Database session.

    Returns:
        The technical sections with their polylines, plus session context.

    Raises:
        NotFoundError: If the activity is not the requester's.
    """
    if not activity_integration.owns_activity(activity_id, requester_user_id, db):
        raise core_exceptions.NotFoundError(f"Activity {activity_id} not found")
    activity = activity_integration.get_activity_for_derivation(activity_id, db)
    if activity is None or sport_group(activity.activity_type) != "run":
        return _empty_trail_sections(activity_id)
    measured = _trail_observation(activity_id, db)
    if measured is None:
        return _empty_trail_sections(activity_id)
    analysis, stamps = measured
    track = _trail_track(activity_id, db)
    cartography = _trail_cartography(activity_id, db)
    marks = {
        round(mark.start_m, 3): mark.verdict
        for mark in analysis_crud.list_trail_marks(activity_id, requester_user_id, db)
    }

    sections: list[analysis_schema.TrailSectionRead] = []
    for section in analysis.sections:
        if not track or section.start_index >= len(stamps) or section.end_index < 1:
            continue
        lo_t = stamps[min(section.start_index, len(stamps) - 1)]
        hi_t = stamps[min(section.end_index - 1, len(stamps) - 1)]
        pts = [(lat, lon) for t, lat, lon in track if lo_t <= t <= hi_t]
        step = max(len(pts) // 150, 1)
        sections.append(
            analysis_schema.TrailSectionRead(
                start_m=section.start_m,
                length_m=section.length_m,
                grade_pct=section.grade_pct,
                avg_speed_ms=section.avg_speed_ms,
                gap_deficit=section.gap_deficit,
                cadence_jitter=section.cadence_jitter,
                brake_score=section.brake_score,
                score=section.score,
                direction=section.direction,
                core_start_m=section.core_start_m,
                core_end_m=section.core_end_m,
                core_score=section.core_score,
                points=[analysis_schema.TrailPointRead(lat=lat, lon=lon) for lat, lon in pts[::step]],
                osm=cartography.get(round(section.start_m, 3)),
                mark=marks.get(round(section.start_m, 3)),
            )
        )
    return analysis_schema.TrailSectionsRead(
        activity_id=activity_id,
        threshold=analysis_constants.TRAIL_THRESHOLD,
        technical_ratio=analysis_constants.TRAIL_TECHNICAL_RATIO,
        hard_ratio=analysis_constants.TRAIL_HARD_RATIO,
        very_max_length_m=analysis_constants.TRAIL_VERY_MAX_LENGTH_M,
        v_base_ms=analysis.v_base_ms,
        cadence_available=analysis.cadence_available,
        technical_m=round(analysis.technical_m, 1),
        total_m=round(analysis.total_m, 1),
        sections=sections,
    )


def compute_trail_sections_for_activity(activity_id: int, db: Session) -> int:
    """Snapshot one activity's technical stretches, with the parameters used.

    The read endpoint always measures the streams again, so what the table holds is
    evidence rather than the answer: the components the model weighed at the time,
    under a stamp of its parameters. Calibration changes the weights, and the only
    way to see afterwards whether a change was an improvement is to have the
    previous measurement of the same ground kept next to it.

    Args:
        activity_id: The activity to measure.
        db: Database session.

    Returns:
        The number of stretches stored (``0`` for a non-run or unmeasurable one).
    """
    activity = activity_integration.get_activity_for_derivation(activity_id, db)
    if activity is None or activity.user_id is None or sport_group(activity.activity_type) != "run":
        return 0
    measured = _trail_observation(activity_id, db)
    if measured is None:
        return 0
    method = analysis_constants.trail_model_stamp()
    analysis, stamps = measured
    records = [
        analysis_contracts.TrailSectionRecord(
            start_m=section.start_m,
            length_m=section.length_m,
            grade_pct=section.grade_pct,
            avg_speed_ms=section.avg_speed_ms,
            gap_deficit=section.gap_deficit,
            cadence_jitter=section.cadence_jitter,
            brake_score=section.brake_score,
            score=section.score,
            direction=section.direction,
            core_start_m=section.core_start_m,
            core_end_m=section.core_end_m,
            core_score=section.core_score,
            v_base_ms=analysis.v_base_ms,
            cadence_available=analysis.cadence_available,
            total_m=round(analysis.total_m, 1),
            start_t=stamps[min(section.start_index, len(stamps) - 1)],
            end_t=stamps[min(section.end_index - 1, len(stamps) - 1)],
        )
        for section in analysis.sections
        if section.start_index < len(stamps) and section.end_index > 0
    ]
    stored = analysis_crud.replace_trail_observation(
        user_id=activity.user_id,
        activity_id=activity_id,
        method=method,
        observation=analysis_contracts.TrailSessionRecord(
            v_base_ms=analysis.v_base_ms,
            cadence_available=analysis.cadence_available,
            total_m=round(analysis.total_m, 1),
            technical_m=round(analysis.technical_m, 1),
        ),
        sections=records,
        db=db,
    )
    logger.info(
        "Trail observation measured",
        extra=core_logger.context(activity_id=activity_id, sections=stored, model=method),
    )
    return stored


def reconcile_trail_sections(db: Session, *, only_missing: bool = True) -> int:
    """Snapshot the technical stretches of every run activity that needs it.

    Args:
        db: Database session.
        only_missing: Skip activities already stored under the current model.

    Returns:
        The number of stretches written.
    """
    method = analysis_constants.trail_model_stamp()
    written = 0
    after_id = 0
    while True:
        activity_ids = activity_integration.list_activity_ids_after(db, after_id=after_id)
        if not activity_ids:
            break
        after_id = max(activity_ids)
        if only_missing:
            measured = analysis_crud.activity_ids_with_trail_sessions(activity_ids, method, db)
            activity_ids = [activity_id for activity_id in activity_ids if activity_id not in measured]
        for activity_id in activity_ids:
            written += compute_trail_sections_for_activity(activity_id, db)
        # the pass outlives any request, and a page of measurements is worthless
        # if the session closes before it lands
        db.commit()
    logger.info(
        "Trail observation pass finished", extra=core_logger.context(only_missing=only_missing, sections=written)
    )
    return written


def compute_way_crossings_for_activity(activity_id: int, db: Session) -> int:
    """Match an activity's whole track against the mapped ways, not just its hard ground.

    The technical observation answers where the ground was remarkable; coverage
    answers where the athlete actually ran. The two together are what makes the
    network a network: the ordinary paths between the hard ones are the ground a
    future route is assembled from.

    Args:
        activity_id: The activity to cover.
        db: Database session.

    Returns:
        The number of along-way crossings stored.
    """
    cache_date = analysis_crud.get_osm_cache_date(db)
    if cache_date is None:
        return 0
    activity = activity_integration.get_activity_for_derivation(activity_id, db)
    if activity is None or activity.user_id is None or sport_group(activity.activity_type) != "run":
        return 0
    track = [(lat, lon) for _stamp, lat, lon in _trail_track(activity_id, db)]
    merged: dict[int, analysis_contracts.WayCrossingRecord] = {}
    for chunk in analysis_trail_osm.chunk_polyline(track):
        match = _match_one_stretch(list(chunk), db)
        if match is None:
            continue
        # one row per (activity, way): an out-and-back over the same path is one
        # crossing of it by that session, with the ground walked across passes summed
        existing = merged.get(match.way_id)
        merged[match.way_id] = (
            analysis_contracts.WayCrossingRecord(
                way_id=match.way_id,
                start_m=min(existing.start_m, match.start_m),
                end_m=max(existing.end_m, match.end_m),
                covered_m=existing.covered_m + match.covered_m,
            )
            if existing is not None
            else analysis_contracts.WayCrossingRecord(
                way_id=match.way_id,
                start_m=match.start_m,
                end_m=match.end_m,
                covered_m=match.covered_m,
            )
        )
    crossings = list(merged.values())
    stored = analysis_crud.replace_way_crossings(activity.user_id, activity_id, cache_date, crossings, db)
    method = analysis_constants.trail_model_stamp()
    analysis_crud.set_trail_session_coverage_date(activity_id, method, cache_date, db)
    logger.info(
        "Way coverage measured",
        extra=core_logger.context(activity_id=activity_id, crossings=stored, cache_date=cache_date.isoformat()),
    )
    return stored


def compute_segment_crossings_for_activity(activity_id: int, db: Session) -> int:
    """Project one activity's stored segment efforts onto track and segment.

    The segment engine answers *that* a pass happened, with stream indices; this
    turns those indices into ground: the span along the segment's geometry, the
    span along the activity's own track (which is what ties the pass to the
    technical observation later), and the mean signed grade from the athlete's
    elevation stream. A pass over a junction that covered only a sliver of the
    segment is dropped: it would fake a traversal.

    Args:
        activity_id: The activity to project.
        db: Database session.

    Returns:
        The number of segment crossings stored.
    """
    activity = activity_integration.get_activity_for_derivation(activity_id, db)
    if activity is None or activity.user_id is None or sport_group(activity.activity_type) != "run":
        return 0
    efforts = segments_integration.list_activity_effort_records(activity_id, db)
    if not efforts:
        return 0
    track = _trail_track(activity_id, db)
    if len(track) < 2:
        return 0
    stamps = [analysis_graph._utc_naive(stamp) for stamp, _lat, _lng in track]
    xy = [analysis_graph.to_xy(lat, lng) for _stamp, lat, lng in track]
    cum: list[float] = [0.0]
    for k in range(1, len(xy)):
        cum.append(cum[-1] + analysis_variants._dist_xy(xy[k - 1], xy[k]))

    lats = [lat for _stamp, lat, _lng in track]
    lngs = [lng for _stamp, _lat, lng in track]
    margin = 0.01
    shapes = {
        shape.id: shape
        for shape in segments_integration.list_segment_shapes_in_bbox(
            min(lats) - margin, min(lngs) - margin, max(lats) + margin, max(lngs) + margin, db
        )
    }

    _, ele_values, ele_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_ELEVATION,
        "ele",
        db,
        keep_zeros=True,
    )
    ele_stamps = [analysis_graph._utc_naive(stamp) for stamp in ele_stamps]
    ele_values = list(ele_values)

    def ele_at(stamp) -> float | None:
        if not ele_stamps:
            return None
        i = bisect_left(ele_stamps, stamp)
        best = i if i < len(ele_stamps) else len(ele_stamps) - 1
        if best > 0 and abs((stamp - ele_stamps[best - 1]).total_seconds()) < abs(
            (stamp - ele_stamps[best]).total_seconds()
        ):
            best -= 1
        if abs((stamp - ele_stamps[best]).total_seconds()) > 30.0:
            return None
        return float(ele_values[best])

    rows: list[dict] = []
    cells: set[tuple[int, int, int]] = set()
    for effort in efforts:
        if effort.coverage is not None and effort.coverage < analysis_constants.TRAIL_SEGMENT_PASS_MIN_COVERAGE:
            continue
        shape = shapes.get(effort.segment_id)
        if shape is None:
            continue
        i0 = max(0, min(effort.stream_index_start, len(track) - 2))
        i1 = max(i0, min(effort.stream_index_end, len(track) - 1))
        seg_xy = analysis_projection.polyline_xy(list(shape.geometry))
        if len(seg_xy) < 2:
            continue
        seg_offsets = analysis_projection.cumulative_offsets(seg_xy)
        along_start, along_end = analysis_projection.effort_along_range(xy, seg_xy, seg_offsets, i0, i1)
        up = ele_at(stamps[i0])
        down = ele_at(stamps[i1])
        span = max(cum[i1] - cum[i0], 1.0)
        grade_pct = None if up is None or down is None else round((down - up) / span * 100.0, 2)
        rows.append(
            {
                "segment_id": effort.segment_id,
                "user_id": activity.user_id,
                "pass_number": effort.pass_number,
                "along_start_m": round(along_start, 1),
                "along_end_m": round(along_end, 1),
                "track_start_m": round(cum[i0], 1),
                "track_end_m": round(cum[i1], 1),
                "direction": effort.direction,
                "grade_pct": grade_pct,
            }
        )
        for cell in analysis_projection.cells_along(seg_xy, seg_offsets, along_start, along_end):
            cells.add((effort.segment_id, *cell))

    written = analysis_crud.replace_activity_segment_crossings(activity_id, rows, db)
    analysis_crud.insert_segment_cells(sorted(cells), db)
    return written


def reconcile_way_crossings(db: Session, *, only_missing: bool = True) -> int:
    """Cover every run activity whose whole track has not been matched to ways yet.

    Args:
        db: Database session.
        only_missing: Skip activities already covered against the current extract.

    Returns:
        The number of crossings written.
    """
    cache_date = analysis_crud.get_osm_cache_date(db)
    if cache_date is None:
        return 0
    method = analysis_constants.trail_model_stamp()
    written = 0
    after_id = 0
    while True:
        activity_ids = activity_integration.list_activity_ids_after(db, after_id=after_id)
        if not activity_ids:
            break
        after_id = max(activity_ids)
        if only_missing:
            activity_ids = sorted(analysis_crud.activity_ids_needing_coverage(activity_ids, method, cache_date, db))
        segment_written = 0
        for activity_id in activity_ids:
            written += compute_way_crossings_for_activity(activity_id, db)
            segment_written += compute_segment_crossings_for_activity(activity_id, db)
        db.commit()
    logger.info(
        "Way coverage pass finished",
        extra=core_logger.context(only_missing=only_missing, crossings=written, segment_crossings=segment_written),
    )
    return written


def _claim_bands(entry: dict, hard_cut: float, technical_cut: float) -> tuple:
    """The claim band a set of descents earns, with its honest spread.

    Same rules for every kind of ground - mapped way or named segment: the
    median over per-session worst descents decides the band, fewer than the
    minimum descents stays thin, and quartiles travel alongside because a
    median without its spread means nothing.

    Args:
        entry: Accumulated ``descents`` (activity -> worst score) and
            ``grades`` (activity -> per-crossing mean grades).
        hard_cut: Score that reads as a hard core.
        technical_cut: Score that reads as firmly technical.

    Returns:
        ``(scores, median, q1, q3, median_grade, claim)``; the numerics are
        ``None`` and the claim ``plain`` when no descent was measured there.
    """
    scores = sorted(entry["descents"].values())
    if not scores:
        return [], None, None, None, None, "plain"
    med = float(statistics.median(scores))
    q1, _, q3 = statistics.quantiles(scores, n=4) if len(scores) >= 2 else (med, med, med)
    grade_med = (
        float(statistics.median([statistics.median(v) for v in entry["grades"].values()])) if entry["grades"] else None
    )
    if len(scores) < analysis_constants.TRAIL_NETWORK_MIN_DESCENTS:
        claim = "thin"
    elif med >= hard_cut:
        claim = "hard"
    elif med >= technical_cut:
        claim = "technical"
    else:
        claim = "mild"
    return scores, med, q1, q3, grade_med, claim


def get_trail_network(
    user_id: int,
    db: Session,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    limit: int = analysis_constants.TRAIL_NETWORK_MAX_WAYS,
) -> analysis_schema.TrailNetworkRead:
    """Aggregate every mapped way the athlete ran over, claimed on descents alone.

    One crossing is not a property of the ground: a tired day or a bad season can
    produce it. Each technical descent contributes exactly one number - the
    harshest index it reached on that way - and a way is claimed by the median
    across its descents, quartiles shown because the spread decides whether the
    median means anything. Ways below the minimum descents are reported thin
    rather than claimed; ascents only corroborate, since a broken path can be
    climbed fast. Ways the full-track coverage found but no technical stretch
    touched are plain: real network, no claim on it.

    Args:
        user_id: The requester.
        db: Database session.
        bbox: Optional ``(south, west, north, east)`` viewport.
        limit: Maximum number of ways handed back.

    Returns:
        The network payload, best claim first.
    """
    method = analysis_constants.trail_model_stamp()
    threshold = analysis_constants.TRAIL_THRESHOLD
    hard_cut = threshold * analysis_constants.TRAIL_HARD_RATIO
    technical_cut = threshold * analysis_constants.TRAIL_TECHNICAL_RATIO
    totals = analysis_crud.list_user_way_crossing_counts(user_id, db)
    per_way: dict[int, dict] = {}
    path_by_id: dict[int, object] = {}
    for path, section in analysis_crud.list_trail_way_evidence(user_id, method, db, bbox=bbox):
        path_by_id[path.way_id] = path
        entry = per_way.setdefault(
            path.way_id,
            {"descents": {}, "ascents": set(), "hard": set(), "grades": {}},
        )
        if section.direction != "descent":
            entry["ascents"].add(section.activity_id)
            continue
        worst = entry["descents"].get(section.activity_id, 0.0)
        entry["descents"][section.activity_id] = max(worst, section.score)
        if section.core_score is not None:
            entry["hard"].add(section.activity_id)
        entry["grades"].setdefault(section.activity_id, []).append(section.grade_pct)

    path_by_id.update(analysis_crud.get_osm_paths_by_ids([w for w in totals if w not in path_by_id], db))

    ways: list[analysis_schema.TrailNetworkWayRead] = []
    for way_id in set(per_way) | set(totals):
        path = path_by_id.get(way_id)
        if path is None:
            continue
        entry = per_way.get(way_id, {"descents": {}, "ascents": set(), "hard": set(), "grades": {}})
        scores, med, q1, q3, grade_med, claim = _claim_bands(entry, hard_cut, technical_cut)
        ways.append(
            analysis_schema.TrailNetworkWayRead(
                way_id=way_id,
                name=path.name,
                claim=claim,
                crossings_total=totals.get(way_id, len(scores)),
                descent_crossings=len(scores),
                ascent_crossings=len(entry["ascents"]),
                hard_crossings=len(entry["hard"]),
                med_score=med,
                q1=q1,
                q3=q3,
                min_score=min(scores) if scores else None,
                max_score=max(scores) if scores else None,
                med_grade_pct=grade_med,
                length_m=path.length_m,
                geometry=path.geometry,
                sac_scale=path.tags.get("sac_scale"),
                mtb_scale=path.tags.get("mtb:scale"),
                surface=path.tags.get("surface"),
                tracktype=path.tags.get("tracktype"),
            )
        )
    rank = {"hard": 0, "technical": 1, "mild": 2, "thin": 3, "plain": 4}
    ways.sort(key=lambda way: (rank[way.claim], -(way.med_score if way.med_score is not None else 0.0)))
    return analysis_schema.TrailNetworkRead(
        method=method,
        threshold=threshold,
        technical_ratio=analysis_constants.TRAIL_TECHNICAL_RATIO,
        hard_ratio=analysis_constants.TRAIL_HARD_RATIO,
        min_descents=analysis_constants.TRAIL_NETWORK_MIN_DESCENTS,
        ways=ways[:limit],
    )


def build_personal_graph(user_id: int, db: Session) -> tuple[int, int]:
    """Rebuild one athlete's ground graph from every stored track, wholesale.

    The graph is derived data: a full rebuild is what keeps traversals and claims
    from being counted twice, and it is only expensive on the database, not on
    the cartography. Nodes and edges replace the previous ones atomically.

    Args:
        user_id: The athlete whose tracks form the graph.
        db: Database session.

    Returns:
        ``(nodes_written, edges_written)``.
    """
    graph_nodes: dict[tuple[int, int], dict] = {}
    graph_edges: dict[tuple[tuple[int, int], tuple[int, int]], dict] = {}
    after_id = 0
    while True:
        activity_ids = activity_integration.list_activity_ids_after(db, after_id=after_id)
        if not activity_ids:
            break
        after_id = max(activity_ids)
        for activity_id in activity_ids:
            activity = activity_integration.get_activity_for_derivation(activity_id, db)
            if activity is None or activity.user_id != user_id or sport_group(activity.activity_type) != "run":
                continue
            node_rows, edge_rows, _grades = analysis_graph.activity_graph_rows(activity_id, user_id, db)
            analysis_graph.merge_activity_into_graph(graph_nodes, graph_edges, node_rows, edge_rows)

    node_rows = [
        {
            "x": c[0],
            "y": c[1],
            "lat": v["lat_sum"] / v["visits"],
            "lng": v["lng_sum"] / v["visits"],
            "ele": v["ele_sum"] / v["ele_visits"] if v["ele_visits"] else 0.0,
            "visits": v["visits"],
            "grade": v["grade"],
        }
        for c, v in graph_nodes.items()
    ]
    edge_rows = [{"ax": a[0], "ay": a[1], "bx": b[0], "by": b[1], **e} for (a, b), e in graph_edges.items()]
    written = analysis_crud.replace_user_graph(user_id, node_rows, edge_rows, db)
    db.commit()
    logger.info(
        "Personal graph rebuilt", extra=core_logger.context(user_id=user_id, nodes=written[0], edges=written[1])
    )
    return written


def _pace_model(user_id: int, db: Session) -> analysis_maths.PaceModel | None:
    """The athlete's own pace rule, fitted over every run the detector measured.

    Each session offers what it covered - ground, climb, descent, technical
    metres - and how long it took moving. Recordings outside the plausible
    speed band are left out.
    """
    technical = analysis_crud.list_trail_session_technical_m(user_id, analysis_constants.trail_model_stamp(), db)
    slowest, fastest = analysis_constants.TRAIL_PACE_SPEED_BAND_MS
    sessions: list[tuple[float, float, float, float, float]] = []
    for activity in activity_integration.list_user_activities(user_id, db):
        moving = float(activity.total_timer_time or 0.0)
        if activity.id not in technical or activity.distance < analysis_constants.TRAIL_PACE_MIN_DISTANCE_M:
            continue
        if moving <= 0 or not slowest <= activity.distance / moving <= fastest:
            continue
        sessions.append(
            (
                float(activity.distance),
                float(activity.elevation_gain or 0),
                float(activity.elevation_loss or 0),
                technical[activity.id],
                moving,
            )
        )
    return analysis_maths.fit_pace_model(sessions)


def _search_variants(
    track: list[tuple],
    marks: dict[float, str],
    requester_user_id: int,
    db: Session,
    pace: analysis_maths.PaceModel | None = None,
) -> list[analysis_schema.TrailVariantRead]:
    """Walk the variant engine over one planned track, activity or designed.

    The ground the variants may use is the athlete's stored personal graph;
    verdicts already placed are carried along, and ``falsa`` ones do not
    resurface. With a pace rule, each variant also says how much time it adds
    or saves.
    """
    cells, cum = analysis_variants.route_cells(track, analysis_constants.TRAIL_GRAPH_CELL_M)
    if not cells:
        return []
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    pad = int(analysis_constants.TRAIL_VARIANT_REJOIN_MAX_M / analysis_constants.TRAIL_GRAPH_CELL_M) + 10
    nodes, edges = analysis_crud.list_user_graph_in_bbox(
        requester_user_id, min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad, db
    )
    if not edges:
        return []
    node_map, adjacency = analysis_graph.graph_from_rows(nodes, edges)
    found = analysis_variants.find_variants(cells, cum, node_map, adjacency, marks)
    segment_claims = {
        row.segment_id: row.claim for row in get_segment_network(requester_user_id, db, show_loops=True).segments
    }
    for variant in found:
        centres = [
            (node_map[tuple(c)]["lat"], node_map[tuple(c)]["lng"]) for c in variant["path"] if tuple(c) in node_map
        ]
        variant["path_latlng"] = analysis_variants.draw_along_ways(centres, requester_user_id, db) if centres else []
        variant.pop("path")
        along = match_segments_for_polyline(
            requester_user_id,
            variant["path_latlng"],
            db,
            min_share=0.7,
            min_covered_m=max(150.0, 0.5 * variant["variant_m"]),
            claims=segment_claims,
        )
        variant["segment"] = along[0].name if along else None
        delta_loss_m = variant.pop("delta_loss_m")
        delta_technical_m = variant.pop("delta_technical_m")
        if pace is not None:
            variant["delta_time_s"] = round(
                pace.seconds(variant["delta_m"], variant["delta_gain_m"], delta_loss_m, delta_technical_m)
            )
    return [analysis_schema.TrailVariantRead(**variant) for variant in found]


def get_activity_variants(
    activity_id: int,
    requester_user_id: int,
    db: Session,
) -> analysis_schema.TrailVariantsRead:
    """Search reconnection variants along one of the caller's recorded routes."""
    track = _trail_track(activity_id, db)
    marks = {
        round(m.split_m, 1): m.verdict
        for m in analysis_crud.list_variant_marks(requester_user_id, db, activity_id=activity_id)
    }
    variants = _search_variants(track, marks, requester_user_id, db) if len(track) >= 10 else []
    return analysis_schema.TrailVariantsRead(
        activity_id=activity_id, cell_m=analysis_constants.TRAIL_GRAPH_CELL_M, variants=variants
    )


def get_route_variants(
    route_id: int,
    requester_user_id: int,
    db: Session,
) -> analysis_schema.TrailVariantsRead:
    """Search reconnection variants along one of the caller's designed routes.

    A plan drawn on the map is searched exactly like a recorded one: the engine
    walks cells and ground distance, and it does not care whether a GPS or a
    finger drew them.
    """
    route = analysis_crud.get_route(route_id, requester_user_id, db)
    if route is None:
        raise core_exceptions.NotFoundError
    track = [(None, float(pt[0]), float(pt[1])) for pt in route.geometry]
    marks = {
        round(m.split_m, 1): m.verdict
        for m in analysis_crud.list_variant_marks(requester_user_id, db, route_id=route_id)
    }
    variants = _search_variants(track, marks, requester_user_id, db) if len(track) >= 10 else []
    return analysis_schema.TrailVariantsRead(
        route_id=route_id, cell_m=analysis_constants.TRAIL_GRAPH_CELL_M, variants=variants
    )


def save_variant_mark(
    activity_id: int,
    requester_user_id: int,
    payload: analysis_schema.VariantMarkCreate,
    db: Session,
) -> None:
    """Place the athlete's verdict on one suggested variant."""
    analysis_crud.save_variant_mark(
        requester_user_id, payload.split_m, payload.verdict, payload.note, db, activity_id=activity_id
    )
    db.commit()
    logger.info(
        "Variant judged",
        extra=core_logger.context(activity_id=activity_id, split_m=payload.split_m, verdict=payload.verdict),
    )


def clear_variant_mark(activity_id: int, requester_user_id: int, split_m: float, db: Session) -> bool:
    """Take back the verdict on one variant."""
    removed = analysis_crud.delete_variant_mark(requester_user_id, split_m, db, activity_id=activity_id)
    db.commit()
    logger.info(
        "Variant judgement taken back",
        extra=core_logger.context(activity_id=activity_id, split_m=split_m, removed=removed),
    )
    return removed


def save_route_variant_mark(
    route_id: int,
    requester_user_id: int,
    payload: analysis_schema.VariantMarkCreate,
    db: Session,
) -> None:
    """Place the athlete's verdict on a variant suggested for a designed route."""
    analysis_crud.save_variant_mark(
        requester_user_id, payload.split_m, payload.verdict, payload.note, db, route_id=route_id
    )
    db.commit()
    logger.info(
        "Route variant judged",
        extra=core_logger.context(route_id=route_id, split_m=payload.split_m, verdict=payload.verdict),
    )


def clear_route_variant_mark(route_id: int, requester_user_id: int, split_m: float, db: Session) -> bool:
    """Take back the verdict on one designed route's variant."""
    removed = analysis_crud.delete_variant_mark(requester_user_id, split_m, db, route_id=route_id)
    db.commit()
    logger.info(
        "Route variant judgement taken back",
        extra=core_logger.context(route_id=route_id, split_m=split_m, removed=removed),
    )
    return removed


def list_routes(requester_user_id: int, db: Session) -> list[analysis_schema.RouteRead]:
    """The caller's saved designed routes."""
    routes = analysis_crud.list_routes(requester_user_id, db)
    return [_to_route_read(r) for r in routes]


def _route_totals(
    geometry: list[list[float]],
    requester_user_id: int,
    db: Session,
) -> tuple[float, float]:
    """Ground distance and elevation gain measured from the polyline itself.

    Distance is the haversine over the vertices; gain is read from the athlete's
    own ground graph, sampling the stored elevation at each vertex's cell. A
    drawn route therefore carries a real ascent instead of a zero, and the gain
    reflects the altitude the athlete actually recorded there.
    """
    pts = [(float(g[0]), float(g[1])) for g in geometry]
    distance = 0.0
    prev = None
    for pt in pts:
        if prev is not None:
            distance += _ground_m(prev, pt)
        prev = pt
    gain = _route_gain(pts, requester_user_id, db)
    return round(distance, 1), round(gain, 1)


def _route_gain(pts: list[tuple[float, float]], user_id: int, db: Session) -> float:
    """Positive elevation along the polyline, from the graph cells under it.

    The altitude under each vertex is its cell's stored mean, smoothed along
    the ground (:func:`graph.ground_profile`); only rises that hold count, and
    ground the athlete never crossed contributes nothing.
    """
    if len(pts) < 2:
        return 0.0
    cells = [analysis_graph.cell_of(*analysis_graph.to_xy(lat, lng)) for lat, lng in pts]
    xs = [cell[0] for cell in cells]
    ys = [cell[1] for cell in cells]
    nodes, _ = analysis_crud.list_user_graph_in_bbox(user_id, min(xs) - 1, max(xs) + 1, min(ys) - 1, max(ys) + 1, db)
    ele_by_cell = {(n.x, n.y): n.ele for n in nodes}
    cum = [0.0]
    for index in range(1, len(pts)):
        cum.append(cum[-1] + _ground_m(pts[index - 1], pts[index]))
    profile = analysis_graph.ground_profile(cum, [ele_by_cell.get(cell) for cell in cells])
    return analysis_graph.profile_gain(profile)


def create_route(
    requester_user_id: int,
    payload: analysis_schema.RouteCreate,
    db: Session,
) -> analysis_schema.RouteRead:
    """Persist a designed route; its totals are measured here, not trusted from the client."""
    distance, gain = _route_totals(payload.geometry, requester_user_id, db)
    source_id = payload.source_activity_id
    if source_id is not None:
        activity = activity_integration.get_activity_for_derivation(source_id, db)
        if activity is None or activity.user_id != requester_user_id:
            source_id = None
    route = analysis_crud.create_route(
        requester_user_id, payload.name, payload.geometry, distance, gain, payload.notes, db, source_id
    )
    db.commit()
    db.refresh(route)
    logger.info(
        "Route saved",
        extra=core_logger.context(route_id=route.id, user_id=requester_user_id, distance_m=route.distance_m),
    )
    return _to_route_read(route)


def match_segments_for_polyline(
    user_id: int,
    geometry: list[list[float]],
    db: Session,
    *,
    min_share: float = 0.4,
    min_covered_m: float = 120.0,
    claims: dict[int, str] | None = None,
) -> list[analysis_schema.RouteSegmentMatchRead]:
    """Which of the athlete's segments a polyline runs along, and how far.

    A run of consecutive points landing within the cartographic tolerance of a
    segment's geometry means the route follows that named corridor; the run's
    along-offsets carry direction. Claims come from the named network (the
    caller may pass them once to avoid re-aggregating per polyline).
    """
    pts = [(float(p0[0]), float(p0[1])) for p0 in geometry]
    if len(pts) < 2:
        return []
    lats = [pt[0] for pt in pts]
    lngs = [pt[1] for pt in pts]
    margin = 0.005
    shapes = segments_integration.list_segment_shapes_in_bbox(
        min(lats) - margin, min(lngs) - margin, max(lats) + margin, max(lngs) + margin, db
    )
    if claims is None:
        claims = {row.segment_id: row.claim for row in get_segment_network(user_id, db, show_loops=True).segments}
    poly_xy = [analysis_graph.to_xy(lat, lng) for lat, lng in pts]
    poly_cum = [0.0]
    for index in range(1, len(poly_xy)):
        poly_cum.append(poly_cum[-1] + analysis_variants._dist_xy(poly_xy[index - 1], poly_xy[index]))
    tol = analysis_constants.TRAIL_VARIANT_WAY_CERT_TOL_M
    matches: list[analysis_schema.RouteSegmentMatchRead] = []
    for shape in shapes:
        seg_xy = analysis_projection.polyline_xy(list(shape.geometry))
        if len(seg_xy) < 2:
            continue
        offsets = analysis_projection.cumulative_offsets(seg_xy)
        run = analysis_projection.longest_snapped_run(poly_xy, seg_xy, offsets, tol)
        if run is None:
            continue
        along_start, along_end, index_start, index_end = run
        covered = abs(along_end - along_start)
        if covered < min_covered_m or covered < min_share * shape.distance_m:
            continue
        matches.append(
            analysis_schema.RouteSegmentMatchRead(
                segment_id=shape.id,
                name=shape.name,
                claim=claims.get(shape.id, "plain"),
                along_start_m=min(along_start, along_end),
                along_end_m=max(along_start, along_end),
                direction="forward" if along_end >= along_start else "reverse",
                covered_m=round(covered, 1),
                length_m=round(shape.distance_m, 1),
                route_start_m=round(poly_cum[index_start], 1),
                route_end_m=round(poly_cum[index_end], 1),
            )
        )
    matches.sort(key=lambda row: row.covered_m, reverse=True)
    return matches


def get_route_segments(
    route_id: int,
    requester_user_id: int,
    db: Session,
) -> list[analysis_schema.RouteSegmentMatchRead]:
    """The named corridors one saved route runs along."""
    route = analysis_crud.get_route(route_id, requester_user_id, db)
    if route is None:
        raise core_exceptions.NotFoundError
    return match_segments_for_polyline(requester_user_id, route.geometry, db)


def connect_route(
    requester_user_id: int,
    payload: analysis_schema.RouteConnectCreate,
    db: Session,
) -> analysis_schema.RouteConnectRead:
    """Shape a drawn route onto the ground the network knows.

    Each clicked anchor lands where it can: on a cell the athlete actually
    crossed, or on a mapped vertex near it. Consecutive anchors are joined by
    the cheapest honest chain of the same network the A→B generator searches -
    the personal graph first, mapped ways where walked ground cannot carry the
    stretch. Every stretch says how it was carried: over walked ground, over
    map ground never crossed (still to confirm), or not carried at all, where
    the straight line stands. Nothing is invented.
    """
    anchors = [(float(point[0]), float(point[1])) for point in payload.points]
    anchor_xy = [analysis_graph.to_xy(lat, lng) for lat, lng in anchors]
    straights = [_ground_m(anchors[k], anchors[k + 1]) for k in range(len(anchors) - 1)]
    boxes = analysis_route_plan.corridor_around(anchors)
    gx0, gx1, gy0, gy1 = boxes["graph"]
    nodes, edges = analysis_crud.list_user_graph_in_bbox(requester_user_id, gx0, gx1, gy0, gy1, db)
    node_map, adjacency = analysis_graph.graph_from_rows(nodes, edges)
    w0, w1, la0, la1 = boxes["ways"]
    ways = [(row.way_id, row.tags, row.geometry) for row in analysis_crud.list_osm_paths_in_box(w0, w1, la0, la1, db)]
    crossed = analysis_crud.list_user_way_crossing_counts(requester_user_id, db)
    analysis_route_plan.add_way_layer(node_map, adjacency, ways, crossed)
    analysis_route_plan.link_layers(node_map, adjacency)
    prefer = analysis_crud.list_segment_preference_cells(requester_user_id, db) or None

    keys = [
        analysis_route_plan.anchor_point(node_map, adjacency, xy, latlng)
        for xy, latlng in zip(anchor_xy, anchors, strict=True)
    ]

    geometry: list[list[float]] = []
    carried: list[str] = []
    spans: list[int] = []

    def push(lat: float, lng: float) -> None:
        if geometry and abs(geometry[-1][0] - lat) < 1e-7 and abs(geometry[-1][1] - lng) < 1e-7:
            return
        geometry.append([lat, lng])

    first = anchors[0] if keys[0] is None else (node_map[keys[0]]["lat"], node_map[keys[0]]["lng"])
    push(first[0], first[1])
    for k in range(1, len(anchors)):
        chain = None
        if keys[k - 1] is not None and keys[k] is not None:
            cutoff = min(
                analysis_constants.TRAIL_ROUTE_CONNECT_MAX_M,
                2.0 * straights[k - 1] + 300.0,
            )
            chain = analysis_route_plan.plan_between(node_map, adjacency, keys[k - 1], keys[k], cutoff, "curt", prefer)
        if chain:
            for step in chain:
                entry = node_map[step["to"]]
                push(entry["lat"], entry["lng"])
            carried.append("mapped" if analysis_route_plan.chain_is_mapped(chain) else "walked")
        else:
            lat, lng = anchors[k]
            push(lat, lng)
            carried.append("straight")
        # the stretch ends where its last pushed point lies: consecutive
        # stretches share that boundary point, so slicing [prev..end] paints
        # each kind without gaps
        spans.append(len(geometry) - 1)
    return analysis_schema.RouteConnectRead(
        geometry=geometry,
        carried=carried,
        spans=spans,
        walked=[kind == "walked" for kind in carried],
    )


def plan_route(
    requester_user_id: int,
    payload: analysis_schema.RouteGenerateCreate,
    db: Session,
) -> analysis_schema.RouteGenerateRead:
    """Plan a route between two drawn points over ground and map.

    The answer is a draft, not a decision: the athlete edits it, saves it or
    throws it away. What it crosses that nobody has walked is reported, never
    hidden; what it uses is either the athlete's own graph or a way the map
    actually draws.
    """
    from_point = (float(payload.from_point[0]), float(payload.from_point[1]))
    to_point = (float(payload.to_point[0]), float(payload.to_point[1]))
    _straight, cutoff, boxes = analysis_route_plan.corridor(from_point, to_point)
    gx0, gx1, gy0, gy1 = boxes["graph"]
    nodes, edges = analysis_crud.list_user_graph_in_bbox(requester_user_id, gx0, gx1, gy0, gy1, db)
    w0, w1, la0, la1 = boxes["ways"]
    ways = [(row.way_id, row.tags, row.geometry) for row in analysis_crud.list_osm_paths_in_box(w0, w1, la0, la1, db)]
    crossed = analysis_crud.list_user_way_crossing_counts(requester_user_id, db)
    node_map, adjacency, start, target = analysis_route_plan.build_layers(
        from_point, to_point, nodes, edges, ways, crossed, cutoff
    )
    steps = analysis_route_plan.plan_between(node_map, adjacency, start, target, cutoff, payload.mode)
    if steps is None:
        raise core_exceptions.InvalidInputError("no ground inside the corridor connects these points")
    geometry, unknown = analysis_route_plan.geometry_and_gaps(steps)
    distance, gain = _route_totals(geometry, requester_user_id, db)
    return analysis_schema.RouteGenerateRead(
        mode=payload.mode,
        geometry=geometry,
        distance_m=distance,
        gain_m=gain,
        unknown_ranges=[analysis_schema.RouteUnknownRangeRead(start_m=s, end_m=e) for s, e in unknown],
        unknown_m=round(sum(e - s for s, e in unknown), 1),
    )


def update_route(
    route_id: int,
    requester_user_id: int,
    payload: analysis_schema.RouteUpdate,
    db: Session,
) -> analysis_schema.RouteRead | None:
    """Edit a saved route: rename it, redraw its polyline, change its notes.

    The totals are measured again from the new geometry, so an edited route
    never carries the old route's distance and gain.
    """
    distance, gain = _route_totals(payload.geometry, requester_user_id, db)
    route = analysis_crud.update_route(
        route_id, requester_user_id, payload.name, payload.geometry, distance, gain, payload.notes, db
    )
    if route is None:
        return None
    db.commit()
    db.refresh(route)
    logger.info(
        "Route edited",
        extra=core_logger.context(route_id=route.id, user_id=requester_user_id, distance_m=route.distance_m),
    )
    return _to_route_read(route)


def _route_annotations(
    route,
    requester_user_id: int,
    pace: analysis_maths.PaceModel | None,
    db: Session,
) -> tuple[
    list[tuple[float, float]],
    list[float],
    list[float | None],
    list[bool],
    list[analysis_schema.RouteGuideEventRead],
    list[analysis_schema.TrailVariantRead],
]:
    """Everything the athlete's own ground can say about a saved route.

    Every event is one that ground backs: named segments the route runs along,
    the recorded track's detected cores (when the route still is that track),
    the hard cells under a drawn polyline, confirmed and un-denied variants,
    mapped water within reach, and honest "to confirm" stretches where the
    route crosses ground never walked before.

    Returns:
        ``(geo, cum, altitudes, technical, events, variants)``: the polyline,
        its cumulative ground, the smoothed altitude and technical flag under
        each vertex, the events in ground order and every variant not judged
        ``falsa``.
    """
    geo = [(float(pt[0]), float(pt[1])) for pt in route.geometry]
    cum = course_export.cumulative(geo)

    # altitudes and known ground come from the personal graph
    route_cells = [analysis_graph.cell_of(*analysis_graph.to_xy(lat, lng)) for lat, lng in geo]
    xs = [cell[0] for cell in route_cells]
    ys = [cell[1] for cell in route_cells]
    nodes, _edges = analysis_crud.list_user_graph_in_bbox(
        requester_user_id, min(xs) - 1, max(xs) + 1, min(ys) - 1, max(ys) + 1, db
    )
    ele_by_cell = {(n.x, n.y): n.ele for n in nodes}
    grades_by_cell = {(n.x, n.y): n.grade for n in nodes}
    known = [cell in ele_by_cell for cell in route_cells]
    altitudes = analysis_graph.ground_profile(cum, [ele_by_cell.get(cell) for cell in route_cells])
    technical = [bool(grades_by_cell.get(cell)) for cell in route_cells]

    events: list[analysis_schema.RouteGuideEventRead] = []

    # named corridors: always announced, claimed by what the athlete's descents say
    for match in match_segments_for_polyline(requester_user_id, route.geometry, db):
        events.append(
            analysis_schema.RouteGuideEventRead(
                kind="segment",
                at_m=match.route_start_m,
                end_m=match.route_end_m,
                label=course_export.clip_text(match.name),
                name=match.name,
                claim=match.claim,
            )
        )

    # recorded cores, only while the route still is that track; a plan that
    # moved on from its source falls back to the claimed cells under the line
    cores = None
    if route.source_activity_id is not None:
        stored = analysis_crud.list_trail_observation(
            route.source_activity_id, analysis_constants.trail_model_stamp(), db
        )
        activity_row = activity_integration.get_activity_for_derivation(route.source_activity_id, db)
        activity_distance = float(activity_row.distance) if activity_row is not None else 0.0
        if stored is not None and activity_distance > 0 and abs(cum[-1] - activity_distance) <= 0.1 * activity_distance:
            verdicts = analysis_crud.list_trail_mark_verdicts(route.source_activity_id, requester_user_id, db)
            _session_row, sections = stored
            cores = []
            for section in sections:
                if section.core_start_m is None or verdicts.get(section.start_m) == "wrong":
                    continue
                overstated = verdicts.get(section.start_m) == "overstated"
                word = "TEC" if overstated else "DUR"
                cores.append(
                    analysis_schema.RouteGuideEventRead(
                        kind="technical" if overstated else "hard",
                        at_m=section.core_start_m,
                        end_m=section.core_end_m,
                        label=f"{word} {section.core_start_m / 1000.0:.1f}k{section.grade_pct:.0f}%",
                        claim="technical" if overstated else "hard",
                    )
                )
    events.extend(cores if cores is not None else _cell_claim_events(route_cells, cum, grades_by_cell))

    # variants: every one not denied is drawn; only the confirmed are announced
    marks = {
        round(m.split_m, 1): m.verdict
        for m in analysis_crud.list_variant_marks(requester_user_id, db, route_id=route.id)
    }
    track = [(None, lat, lng) for lat, lng in geo]
    variants = [
        variant for variant in _search_variants(track, marks, requester_user_id, db, pace) if variant.mark != "falsa"
    ]
    for variant in variants:
        if variant.status != "confirmada" and variant.mark != "real":
            continue
        letter = {"left": "E", "right": "D"}.get(variant.side, "R")
        minutes = "" if variant.delta_time_s is None else f"{round(variant.delta_time_s / 60.0):+d}' "
        events.append(
            analysis_schema.RouteGuideEventRead(
                kind="variant",
                at_m=variant.split_m,
                end_m=variant.rejoin_m,
                label=f"V{letter}{minutes}{variant.delta_m / 1000.0:+.1f}k{variant.delta_gain_m:+d}m",
                name=variant.segment,
                claim={"hard": "hard", "tech": "technical"}.get(variant.grade),
                side=variant.side,
                delta_m=variant.delta_m,
                delta_gain_m=variant.delta_gain_m,
                delta_time_s=variant.delta_time_s,
                confirmed=True,
            )
        )

    # ground never walked: to confirm, never claimed
    k = 0
    while k < len(geo):
        if not known[k]:
            start = k
            while k < len(geo) and not known[k]:
                k += 1
            end_m = cum[min(k, len(geo) - 1)]
            if end_m - cum[start] >= 100.0:
                events.append(
                    analysis_schema.RouteGuideEventRead(
                        kind="unknown", at_m=cum[start], end_m=end_m, label="? a confirmar"
                    )
                )
        else:
            k += 1

    events.extend(_water_events(geo, cum, db))
    events.sort(key=lambda event: event.at_m)
    return geo, cum, altitudes, technical, events, variants


def _cell_claim_events(
    route_cells: list[tuple[int, int]],
    cum: list[float],
    grades_by_cell: dict[tuple[int, int], str],
) -> list[analysis_schema.RouteGuideEventRead]:
    """Warnings carried by the athlete's own hard cells under a drawn line.

    A run of ground the detector called hard on past sessions warns; runs
    under 120 m are passing noise, not a stretch.
    """
    events: list[analysis_schema.RouteGuideEventRead] = []
    k = 0
    while k < len(route_cells):
        if grades_by_cell.get(route_cells[k]) == "hard":
            start_m = cum[k]
            while k < len(route_cells) and grades_by_cell.get(route_cells[k]) == "hard":
                k += 1
            if cum[k - 1] - start_m >= 120.0:
                events.append(
                    analysis_schema.RouteGuideEventRead(
                        kind="hard",
                        at_m=start_m,
                        end_m=cum[k - 1],
                        label=f"DUR {start_m / 1000.0:.1f}k",
                        claim="hard",
                    )
                )
        else:
            k += 1
    return events


def _water_events(
    geo: list[tuple[float, float]],
    cum: list[float],
    db: Session,
) -> list[analysis_schema.RouteGuideEventRead]:
    """Mapped water within reach of the line, placed where the route passes nearest."""
    reach = analysis_constants.TRAIL_GUIDE_WATER_REACH_M
    lats = [pt[0] for pt in geo]
    lngs = [pt[1] for pt in geo]
    margin = 0.002  # ~200 m: wider than the reach at any latitude the frame serves
    waters = analysis_crud.list_osm_waters_in_box(
        min(lats) - margin, max(lats) + margin, min(lngs) - margin, max(lngs) + margin, db
    )
    if not waters:
        return []
    line = [analysis_graph.to_xy(lat, lng) for lat, lng in geo]
    events: list[analysis_schema.RouteGuideEventRead] = []
    for water in waters:
        px, py = analysis_graph.to_xy(water.lat, water.lng)
        best_d, best_m = math.inf, 0.0
        for index in range(len(line) - 1):
            (ax, ay), (bx, by) = line[index], line[index + 1]
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            along = 0.0 if seg2 <= 1e-9 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
            d = math.hypot(px - (ax + along * dx), py - (ay + along * dy))
            if d < best_d:
                best_d, best_m = d, cum[index] + along * (cum[index + 1] - cum[index])
        if best_d > reach:
            continue
        fallback = "FONT" if water.kind == "font" else "DEU?"
        events.append(
            analysis_schema.RouteGuideEventRead(
                kind="water",
                at_m=round(best_m, 1),
                label=course_export.clip_text(water.name or fallback),
                name=water.name,
                off_route_m=round(best_d, 1),
                water=water.kind,
            )
        )
    return events


def _event_course_points(event: analysis_schema.RouteGuideEventRead) -> list[tuple[float, str, int]]:
    """One guide event as the course points a watch shows for it."""
    icons = course_export.CoursePoint
    if event.kind == "segment":
        icon = (
            icons.DANGER
            if event.claim == "hard"
            else icons.INFO
            if event.claim in ("technical", "mild")
            else icons.GENERIC
        )
        return [(event.at_m, event.label, icon)]
    if event.kind == "variant":
        icon = {"left": icons.LEFT, "right": icons.RIGHT}.get(event.side, icons.STRAIGHT)
        return [
            (event.at_m, event.label, icon),
            (event.end_m, f"FI V {event.end_m / 1000.0:.1f}k", icons.STRAIGHT),
        ]
    icon = {"water": icons.WATER, "unknown": icons.GENERIC}.get(event.kind, icons.DANGER)
    return [(event.at_m, event.label, icon)]


def export_route_course(route_id: int, requester_user_id: int, db: Session) -> bytes:
    """Render a saved route as a Garmin COURSE .fit, annotated only with truth.

    The marks are the route guide's events (:func:`_route_annotations`), each
    as the course point a watch can show. The only estimate on it is the time
    a variant adds or saves, by the athlete's own pace rule.
    """
    route = analysis_crud.get_route(route_id, requester_user_id, db)
    if route is None:
        raise core_exceptions.NotFoundError
    geo, _cum, altitudes, _technical, events, _variants = _route_annotations(
        route, requester_user_id, _pace_model(requester_user_id, db), db
    )
    points = [point for event in events for point in _event_course_points(event)]
    return course_export.build_course_bytes(route.name, geo, altitudes, points)


def get_route_guide(route_id: int, requester_user_id: int, db: Session) -> analysis_schema.RouteGuideRead:
    """Build the self-contained guide pack of one saved route.

    The pack carries the line with altitude, ground distance and estimated
    time at every vertex, plus the events and variants a client announces as
    the athlete moves along it. Times come from the athlete's own pace rule
    and are absent when there is none.
    """
    route = analysis_crud.get_route(route_id, requester_user_id, db)
    if route is None:
        raise core_exceptions.NotFoundError
    pace = _pace_model(requester_user_id, db)
    geo, cum, altitudes, technical, events, variants = _route_annotations(route, requester_user_id, pace, db)

    eta: list[float | None] = [None if pace is None else 0.0]
    for index in range(1, len(geo)):
        if pace is None:
            eta.append(None)
            continue
        step = cum[index] - cum[index - 1]
        before, here = altitudes[index - 1], altitudes[index]
        rise = here - before if before is not None and here is not None else 0.0
        eta.append(eta[-1] + pace.seconds(step, max(0.0, rise), max(0.0, -rise), step if technical[index] else 0.0))

    return analysis_schema.RouteGuideRead(
        route_id=route.id,
        name=route.name,
        generated_at=datetime.now(UTC).isoformat(),
        distance_m=round(cum[-1], 1),
        gain_m=round(analysis_graph.profile_gain(altitudes), 1),
        loss_m=round(analysis_graph.profile_gain([None if v is None else -v for v in altitudes]), 1),
        est_time_s=None if pace is None else round(eta[-1]),
        pace=None
        if pace is None
        else analysis_schema.RouteGuidePaceRead(
            per_km_s=round(pace.per_m * 1000.0, 1),
            per_100m_gain_s=round(pace.per_gain_m * 100.0, 1),
            per_100m_loss_s=round(pace.per_loss_m * 100.0, 1),
            per_technical_km_s=round(pace.per_technical_m * 1000.0, 1),
            error=round(pace.error, 3),
            sessions=pace.sessions,
        ),
        track=[
            [
                lat,
                lng,
                None if altitudes[index] is None else round(altitudes[index], 1),
                round(cum[index], 1),
                None if eta[index] is None else round(eta[index]),
            ]
            for index, (lat, lng) in enumerate(geo)
        ],
        events=events,
        variants=variants,
    )


def delete_route(route_id: int, requester_user_id: int, db: Session) -> bool:
    """Delete one saved route."""
    removed = analysis_crud.delete_route(route_id, requester_user_id, db)
    db.commit()
    logger.info(
        "Route deleted", extra=core_logger.context(route_id=route_id, user_id=requester_user_id, removed=removed)
    )
    return removed


def _ground_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat_m = (b[0] - a[0]) * 111195.0
    lng_m = (b[1] - a[1]) * 111320.0 * math.cos(math.radians((a[0] + b[0]) / 2.0))
    return math.hypot(lat_m, lng_m)


def _to_route_read(route) -> analysis_schema.RouteRead:
    return analysis_schema.RouteRead(
        id=route.id,
        name=route.name,
        geometry=route.geometry,
        distance_m=route.distance_m,
        gain_m=route.gain_m,
        notes=route.notes,
        created_at=route.created_at.isoformat() if route.created_at else "",
    )


def get_segment_network(
    user_id: int,
    db: Session,
    *,
    show_loops: bool = False,
    limit: int = 2000,
) -> analysis_schema.SegmentNetworkRead:
    """Aggregate every named segment the athlete ran over, claimed like a way.

    The ways network describes ground the cartography names; this one describes
    ground the athlete named themselves. The claim rules are deliberately the
    same - descents only, minimum traversals, median with its quartiles - so a
    ``hard`` means the same thing in either layer. Short closed circuits (a
    lap of a field) are real training but not travel: they stay out unless
    asked for.

    Args:
        user_id: The athlete whose crossings count.
        db: Database session.
        show_loops: Include short closed circuits.
        limit: Maximum number of segments handed back.

    Returns:
        The named network, best claim first.
    """
    method = analysis_constants.trail_model_stamp()
    threshold = analysis_constants.TRAIL_THRESHOLD
    hard_cut = threshold * analysis_constants.TRAIL_HARD_RATIO
    technical_cut = threshold * analysis_constants.TRAIL_TECHNICAL_RATIO

    shapes = {shape.id: shape for shape in segments_integration.list_segment_shapes(db)}
    crossings = analysis_crud.list_user_segment_crossings(user_id, db)

    totals: dict[int, int] = {}
    per_seg: dict[int, dict] = {}
    for crossing in crossings:
        totals[crossing.segment_id] = totals.get(crossing.segment_id, 0) + 1
        entry = per_seg.setdefault(crossing.segment_id, {"descents": {}, "ascents": set(), "grades": {}})
        if crossing.grade_pct is not None:
            entry["grades"].setdefault(crossing.activity_id, []).append(crossing.grade_pct)
    for crossing, section in analysis_crud.list_segment_descent_evidence(user_id, method, db):
        entry = per_seg.setdefault(crossing.segment_id, {"descents": {}, "ascents": set(), "grades": {}})
        worst = entry["descents"].get(crossing.activity_id, 0.0)
        entry["descents"][crossing.activity_id] = max(worst, section.score)

    segments: list[analysis_schema.SegmentNetworkSegmentRead] = []
    for segment_id, entry in per_seg.items():
        shape = shapes.get(segment_id)
        if shape is None:
            continue
        loop = analysis_projection.is_loop(list(shape.geometry), shape.distance_m)
        if loop and not show_loops:
            continue
        scores, med, q1, q3, grade_med, claim = _claim_bands(entry, hard_cut, technical_cut)
        segments.append(
            analysis_schema.SegmentNetworkSegmentRead(
                segment_id=segment_id,
                name=shape.name,
                claim=claim,
                length_m=round(shape.distance_m, 1),
                gain_m=None if shape.elevation_gain_m is None else round(shape.elevation_gain_m, 1),
                crossings_total=totals.get(segment_id, len(scores)),
                descent_crossings=len(scores),
                med_score=None if med is None else round(med, 3),
                q1=None if q1 is None else round(q1, 3),
                q3=None if q3 is None else round(q3, 3),
                med_grade_pct=grade_med,
                loop=loop,
                geometry=[[float(pt[0]), float(pt[1])] for pt in shape.geometry],
            )
        )
    segments.sort(key=lambda row: (row.descent_crossings, row.med_score or 0.0), reverse=True)
    return analysis_schema.SegmentNetworkRead(
        min_descents=analysis_constants.TRAIL_NETWORK_MIN_DESCENTS,
        threshold=threshold,
        technical_ratio=analysis_constants.TRAIL_TECHNICAL_RATIO,
        hard_ratio=analysis_constants.TRAIL_HARD_RATIO,
        segments=segments[:limit],
    )


def get_best_times(
    user_id: int,
    sport: str,
    db: Session,
    *,
    months: int | None = None,
) -> analysis_schema.BestTimesRead:
    """Return the athlete's best splits over the canonical race distances.

    Each distance's fastest window is found in every activity's own samples, so
    the table answers "the best I have run", never "the best my watch reported
    per lap". Splits whose net grade falls below the bound for their length are
    discarded: a downhill sprint is not a personal best. The short distances also
    demand an engine behind them, judged through the intensity chain: watts over
    threshold, or grade-adjusted pace over critical speed; an activity with only
    heart rate therefore contributes its longer distances alone. Activities with
    neither power nor a critical speed need enough ground covered to appear at
    all, and watts simply read blank where there is no power stream.

    Args:
        user_id: The athlete.
        sport: Sport group to read (the distances are running's).
        db: Database session.
        months: Window in months back from today, or ``None`` for all history.

    Returns:
        One record per distance achieved inside the window, shortest first.

    Raises:
        InvalidInputError: If the sport is unknown or the window is not positive.
    """
    if sport not in activity_constants.ACTIVITY_TYPES_BY_SPORT:
        raise core_exceptions.InvalidInputError(f"Unknown sport '{sport}'")
    if months is not None and months <= 0:
        raise core_exceptions.InvalidInputError(f"Invalid months '{months}'")

    settings = get_settings(user_id, db)
    power_threshold = settings.power_thresholds.get(sport)
    speed_threshold = settings.speed_thresholds.get(sport)
    since = _months_ago(datetime.now(UTC), months) if months else None
    best: dict[int, tuple[float, float | None, int, datetime | None]] = {}
    for activity in activity_integration.list_user_activities(user_id, db):
        if sport_group(activity.activity_type) != sport:
            continue
        started = activity.start_time
        if started is not None and started.tzinfo is not None and since is not None and started < since:
            continue
        if since is not None and (started is None or started.tzinfo is None):
            continue
        period_s, _stamps, power_values, speeds, grades, _hr_values, altitude_m = _ground_timeline(activity.id, db)
        if not speeds or period_s <= 0:
            continue
        # alignment copies the nearest sample across dropouts, which can repeat a GPS
        # speed spike second after second and invent distance; no foot runs that fast,
        # so cap every sample at the split speed ceiling before accumulating
        speeds = tuple(min(value, analysis_constants.BT_MAX_SPEED_MS) for value in speeds)
        count = len(speeds)
        elapsed_s = [index * period_s for index in range(count)]
        distance_cum_m = list(accumulate(speed * period_s for speed in speeds))
        altitude_series_m = altitude_m if altitude_m is not None else (0.0,) * count
        # the gate's engine follows the chain: watts, then grade-adjusted pace. Heart
        # rate alone never evidences a short surge (it lags the effort by minutes),
        # so a heart-rate-only athlete gets only the distances whose gate is the grade.
        intensity: tuple[float, ...] | None = None
        if power_values and power_threshold:
            intensity = tuple(value / float(power_threshold.value_w) for value in power_values[:count])
        elif speed_threshold:
            grade_source = grades if grades is not None else (0.0,) * count
            adjusted = analysis_maths.grade_adjusted_run_speed(speeds, grade_source)
            intensity = tuple(value / float(speed_threshold.value_ms) for value in adjusted[:count])
        splits = analysis_maths.best_split_times(
            elapsed_s=elapsed_s,
            distance_cum_m=distance_cum_m,
            power_w=power_values[:count] if power_values is not None else None,
            altitude_m=altitude_series_m,
            intensity=intensity,
        )
        for distance_m, (time_s, avg_power_w) in splits.items():
            current = best.get(distance_m)
            if current is None or time_s < current[0]:
                best[distance_m] = (time_s, avg_power_w, activity.id, started)

    return analysis_schema.BestTimesRead(
        sport=sport,
        months=months,
        records=[
            analysis_schema.BestTimeRead(
                distance_m=distance_m,
                time_s=round(values[0], 1),
                avg_power_w=round(values[1], 1) if values[1] is not None else None,
                activity_id=values[2],
                start_time=values[3].isoformat() if values[3] is not None else None,
            )
            for distance_m, values in sorted(best.items())
        ],
    )


def match_trail_sections_to_osm(db: Session) -> int:
    """Say which mapped way each measured stretch lies on, when one does.

    Two independent answers only become useful together once they speak about the
    same ground, and that correspondence is geometry: a way is one long polyline,
    so proximity to its middle proves nothing. Each stretch is projected onto the
    candidate ways around it and accepted only when most of it runs along one.

    A stretch the map does not describe keeps a dated record of that too: an
    unmapped gully is a finding, and it is what a newer extract re-checks.

    Args:
        db: Database session.

    Returns:
        The number of stretches matched to a way.
    """
    cache_date = analysis_crud.get_osm_cache_date(db)
    if cache_date is None:
        return 0
    method = analysis_constants.trail_model_stamp()
    matched = 0
    while True:
        rows = analysis_crud.list_sections_needing_osm_match(
            method, cache_date, db, limit=analysis_constants.OSM_MATCH_PAGE
        )
        if not rows:
            break
        by_activity: dict[int, list] = {}
        for row in rows:
            by_activity.setdefault(row.activity_id, []).append(row)
        for activity_id, activity_rows in by_activity.items():
            # the stored stretch range is a UTC-aware instant; the stream's clock is
            # whatever the device wrote, so both are read as UTC
            track = [(stamp, lat, lon) for stamp, lat, lon in _trail_track(activity_id, db)]
            track = [(stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC), lat, lon) for stamp, lat, lon in track]
            for row in activity_rows:
                if row.start_t is None or row.end_t is None:
                    analysis_crud.set_trail_section_osm_match(row, None, cache_date, db)
                    continue
                stretch = [(lat, lon) for stamp, lat, lon in track if row.start_t <= stamp <= row.end_t]
                match = _match_one_stretch(stretch, db)
                analysis_crud.set_trail_section_osm_match(
                    row,
                    analysis_contracts.OsmMatchRecord(
                        way_id=match.way_id,
                        start_m=match.start_m,
                        end_m=match.end_m,
                        covered_m=match.covered_m,
                    )
                    if match
                    else None,
                    cache_date,
                    db,
                )
                if match is not None:
                    matched += 1
        db.commit()
    logger.info(
        "Trail cartography matched", extra=core_logger.context(matched=matched, cache_date=cache_date.isoformat())
    )
    return matched


def _match_one_stretch(
    stretch: list[tuple[float, float]],
    db: Session,
) -> analysis_trail_osm.OsmMatch | None:
    """Match one stretch's own polyline against the ways drawn around it.

    Args:
        stretch: Ordered ``(lat, lng)`` pairs of the stretch.
        db: Database session.

    Returns:
        The way the stretch runs along, or ``None`` when none does.
    """
    if len(stretch) < 2:
        return None
    lats = [point[0] for point in stretch]
    lngs = [point[1] for point in stretch]
    # a margin wide enough that a way running alongside but drawn slightly off the
    # recorded track is still offered as a candidate
    margin = 200.0
    candidates = analysis_crud.list_osm_paths_in_box(
        min_lat=min(lats) - margin / 111195.0,
        max_lat=max(lats) + margin / 111195.0,
        min_lng=min(lngs) - margin / 111320.0,
        max_lng=max(lngs) + margin / 111320.0,
        db=db,
    )
    return analysis_trail_osm.match_stretch(
        stretch,
        [
            analysis_trail_osm.CandidateWay(way_id=path.way_id, points=[tuple(p) for p in path.geometry])
            for path in candidates
        ],
    )


def get_trail_marks(
    activity_id: int,
    requester_user_id: int,
    db: Session,
) -> list[analysis_schema.TrailMarkRead]:
    """Return what the athlete has already said about one activity's stretches.

    Args:
        activity_id: The session judged.
        requester_user_id: The athlete asking; only their own marks answer.
        db: Database session.

    Returns:
        The marks, in ground order.

    Raises:
        NotFoundError: If the activity is not the requester's.
    """
    if not activity_integration.owns_activity(activity_id, requester_user_id, db):
        raise core_exceptions.NotFoundError(f"Activity {activity_id} not found")
    return [
        analysis_schema.TrailMarkRead(
            activity_id=mark.activity_id,
            start_m=mark.start_m,
            verdict=mark.verdict,
            updated_at=mark.updated_at.isoformat() if mark.updated_at else "",
        )
        for mark in analysis_crud.list_trail_marks(activity_id, requester_user_id, db)
    ]


def save_trail_mark(
    activity_id: int,
    requester_user_id: int,
    mark: analysis_schema.TrailMarkCreate,
    db: Session,
) -> analysis_schema.TrailMarkRead:
    """Place the athlete's verdict on one measured stretch.

    The verdict is calibration, not decoration: it is the label a future change to
    the weights or the threshold is judged against, which is why "overstated" and
    "wrong" are kept apart instead of collapsing into "no". A stretch is judged where
    it was measured, so the mark is keyed to its ground position; re-marking replaces.

    Args:
        activity_id: The session the stretch belongs to.
        requester_user_id: The athlete judging; only their own activity answers.
        mark: The stretch position and the verdict.
        db: Database session.

    Returns:
        The stored mark.

    Raises:
        NotFoundError: If the activity is not the requester's.
    """
    if not activity_integration.owns_activity(activity_id, requester_user_id, db):
        raise core_exceptions.NotFoundError(f"Activity {activity_id} not found")
    stored = analysis_crud.replace_trail_mark(
        user_id=requester_user_id,
        activity_id=activity_id,
        start_m=mark.start_m,
        verdict=mark.verdict,
        db=db,
    )
    db.commit()
    logger.info(
        "Trail stretch judged",
        extra=core_logger.context(activity_id=activity_id, start_m=mark.start_m, verdict=mark.verdict),
    )
    return analysis_schema.TrailMarkRead(
        activity_id=stored.activity_id,
        start_m=stored.start_m,
        verdict=stored.verdict,
        updated_at=stored.updated_at.isoformat() if stored.updated_at else "",
    )


def clear_trail_mark(
    activity_id: int,
    requester_user_id: int,
    start_m: float,
    db: Session,
) -> bool:
    """Take back a verdict, when the athlete changes their mind.

    Args:
        activity_id: The session judged.
        requester_user_id: The athlete who judged.
        start_m: Ground position of the stretch.
        db: Database session.

    Returns:
        ``True`` when a mark was removed.

    Raises:
        NotFoundError: If the activity is not the requester's.
    """
    if not activity_integration.owns_activity(activity_id, requester_user_id, db):
        raise core_exceptions.NotFoundError(f"Activity {activity_id} not found")
    removed = analysis_crud.delete_trail_mark(
        user_id=requester_user_id,
        activity_id=activity_id,
        start_m=start_m,
        db=db,
    )
    db.commit()
    logger.info(
        "Trail judgement taken back",
        extra=core_logger.context(activity_id=activity_id, start_m=start_m, removed=removed),
    )
    return removed
