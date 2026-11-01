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
from bisect import bisect_left
from collections.abc import Mapping
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
import modules.analysis.crud as analysis_crud
import modules.analysis.intervals as analysis_intervals
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
    sensor's "no signal" sentinel.

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
    # Speed and elevation answer only for the seconds the power stream covers, so
    # the per-sample formulas read one aligned timeline rather than three clocks.
    _, speeds, speed_stamps = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_SPEED,
        "vel",
        db,
        keep_zeros=True,
    )
    _, elevations, elevation_stamps = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_ELEVATION,
        "ele",
        db,
        keep_zeros=True,
    )
    speed_ms = _align_to_power(speeds, speed_stamps, power_stamps, period_s) if power_w else None
    altitude_m = _align_to_power(elevations, elevation_stamps, power_stamps, period_s) if power_w else None
    grade_pct = None
    if altitude_m is not None and speed_ms is not None:
        grade_pct = analysis_maths.grade_pct_series(altitude_m, speed_ms, period_s or 1.0)
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
        speed_ms=speed_ms,
        grade_pct=grade_pct,
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
        period_s, power_w, _ = _stream_samples(
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
    if start > end:
        raise core_exceptions.InvalidInputError("'from' must not be after 'to'")

    totals = analysis_crud.daily_load_totals(user_id, chosen.value, db, sport=sport)
    load_by_day = {day: value for day, value in totals}
    first_measured = totals[0][0] if totals else start
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
    power_period, power_values, power_stamps = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_POWER,
        "power",
        db,
        keep_zeros=True,
    )
    speed_period, speeds, speed_stamps = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_SPEED,
        "vel",
        db,
        keep_zeros=True,
    )
    _, elevations, elevation_stamps = _stream_samples(
        activity.id,
        activity_streams_constants.STREAM_TYPE_ELEVATION,
        "ele",
        db,
        keep_zeros=True,
    )
    # One clock for the terrain read: the power samples when the device carried a
    # power meter, otherwise the speed samples. Altitude aligns onto whichever wins.
    if power_values and power_period > 0:
        ground_period, ground_stamps = power_period, power_stamps
        ground_speeds = _align_to_power(speeds, speed_stamps, ground_stamps, ground_period) if speeds else None
        ground_power: tuple[float, ...] | None = power_values
    elif speeds and speed_period > 0:
        ground_period, ground_stamps = speed_period, speed_stamps
        ground_speeds = speeds
        ground_power = None
    else:
        ground_period, ground_stamps, ground_speeds, ground_power = 0.0, (), None, None
    altitudes_m = (
        _align_to_power(elevations, elevation_stamps, ground_stamps, ground_period)
        if elevations and ground_stamps
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
                elevation_gain_m=segment.elevation_gain_m,
                elevation_loss_m=segment.elevation_loss_m,
                rss=segment.rss,
                rss_per_min=segment.rss_per_min,
            )
            for segment in analysis.segments
        ],
        climbs=[
            analysis_schema.TerrainClimbRead(**vars(climb))
            for climb in analysis_intervals.terrain_climbs(
                altitudes_m=altitudes_m,
                speeds_ms=ground_speeds,
                period_s=ground_period,
                power_w=ground_power,
                threshold_w=analysis.threshold_w,
                base_power_w=analysis.base_power_w,
                parameters=dict(data.parameters),
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

    _apply_lap_rss(
        lap_windows=laps,
        dynamics=dynamics,
        activity_id=activity_id,
        db=db,
        threshold_w=threshold_w,
        parameters=settings.method_params,
    )
    return dynamics


def _apply_lap_rss(
    lap_windows: list[activity_laps_schema.ActivityLapsRead],
    dynamics: list[analysis_schema.LapDynamicsRead],
    activity_id: int,
    threshold_w: float | None,
    parameters: Mapping[str, float],
    db: Session,
) -> None:
    """Fill each lap's aRSS from the activity's own power and grade samples."""
    if not threshold_w or not lap_windows:
        return
    period_s, power_values, power_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_POWER,
        "power",
        db,
        keep_zeros=True,
    )
    if not power_values or period_s <= 0:
        return

    # Device-written waypoint times carry no offset while the database hands back
    # aware lap starts: put both clocks in UTC before any comparison.
    def _utc(stamps: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC) for stamp in stamps)

    power_stamps = _utc(power_stamps)
    _, speeds, speed_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_SPEED,
        "vel",
        db,
        keep_zeros=True,
    )
    _, elevations, elevation_stamps = _stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_ELEVATION,
        "ele",
        db,
        keep_zeros=True,
    )
    speed_ms = _align_to_power(speeds, _utc(speed_stamps), power_stamps, period_s)
    altitude_m = _align_to_power(elevations, _utc(elevation_stamps), power_stamps, period_s)
    grade_pct = (
        analysis_maths.grade_pct_series(altitude_m, speed_ms, period_s)
        if altitude_m is not None and speed_ms is not None
        else None
    )
    parameters = dict(parameters)
    for lap, entry in zip(lap_windows, dynamics, strict=True):
        window = _lap_sample_window(power_stamps, lap.start_time, float(lap.total_elapsed_time or 0.0))
        if window is None:
            continue
        start, stop = window
        score = analysis_methods.rss_window_stress(
            power_values[start:stop],
            period_s,
            threshold_w,
            grade_pct[start:stop] if grade_pct is not None else None,
            parameters,
        )
        entry.rss = round(score, 1) if score > 0 else None
        elapsed_s = float(lap.total_elapsed_time or 0.0)
        if entry.rss is not None and elapsed_s > 0:
            entry.rss_per_min = round(entry.rss * 60.0 / elapsed_s, 1)


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
    discarded: a downhill sprint is not a personal best. Activities without a
    power stream cannot appear (the table shows watts alongside every time);
    without elevation a split is read as flat, which the short-distance bound
    already tolerates.

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

    threshold = get_settings(user_id, db).power_thresholds.get(sport)
    since = _months_ago(datetime.now(UTC), months) if months else None
    best: dict[int, tuple[float, float, int, datetime | None]] = {}
    for activity in activity_integration.list_user_activities(user_id, db):
        if sport_group(activity.activity_type) != sport:
            continue
        started = activity.start_time
        if started is not None and started.tzinfo is not None and since is not None and started < since:
            continue
        if since is not None and (started is None or started.tzinfo is None):
            continue
        period_s, power_w, power_stamps = _stream_samples(
            activity.id,
            activity_streams_constants.STREAM_TYPE_POWER,
            "power",
            db,
            keep_zeros=True,
        )
        if not power_w or period_s <= 0:
            continue
        _, speeds, speed_stamps = _stream_samples(
            activity.id,
            activity_streams_constants.STREAM_TYPE_SPEED,
            "vel",
            db,
            keep_zeros=True,
        )
        _, elevations, elevation_stamps = _stream_samples(
            activity.id,
            activity_streams_constants.STREAM_TYPE_ELEVATION,
            "ele",
            db,
            keep_zeros=True,
        )
        speed_ms = _align_to_power(speeds, speed_stamps, power_stamps, period_s)
        if not speed_ms:
            continue
        altitude_m = _align_to_power(elevations, elevation_stamps, power_stamps, period_s)
        # alignment copies the nearest sample across dropouts, which can repeat a GPS
        # speed spike second after second and invent distance; no foot runs that fast,
        # so cap every sample at the split speed ceiling before accumulating
        speed_ms = tuple(min(value, analysis_constants.BT_MAX_SPEED_MS) for value in speed_ms)

        count = len(power_w)
        elapsed_s = [index * period_s for index in range(count)]
        distance_cum_m = list(accumulate(speed * period_s for speed in speed_ms))
        altitude_series_m = altitude_m if altitude_m is not None else (0.0,) * count
        splits = analysis_maths.best_split_times(
            elapsed_s=elapsed_s,
            distance_cum_m=distance_cum_m,
            power_w=power_w,
            altitude_m=altitude_series_m,
            threshold_w=threshold.value_w if threshold else None,
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
                avg_power_w=round(values[1], 1),
                activity_id=values[2],
                start_time=values[3].isoformat() if values[3] is not None else None,
            )
            for distance_m, values in sorted(best.items())
        ],
    )
