"""Persistence for analysis settings, per-activity loads and duration peaks.

Only this layer touches the ORM models and executes against the session, and it only
flushes: the service owns the transaction.
"""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import modules.analysis.contracts as analysis_contracts
import modules.analysis.models as analysis_models
import modules.analysis.schema as analysis_schema


def _to_settings_read(row: analysis_models.AnalysisSettings) -> analysis_schema.AnalysisSettingsRead:
    """Shape a settings row into its read schema, reading the threshold document.

    Args:
        row: The stored settings row.

    Returns:
        The settings with their thresholds, empty when no threshold is stored.
    """
    thresholds = row.thresholds or {}
    power: dict[str, analysis_schema.PowerThresholdRead] = {}
    for sport, entry in (thresholds.get("power") or {}).items():
        power[sport] = analysis_schema.PowerThresholdRead(**entry)
    speed: dict[str, analysis_schema.SpeedThresholdRead] = {}
    for sport, entry in (thresholds.get("speed") or {}).items():
        speed[sport] = analysis_schema.SpeedThresholdRead(**entry)
    heart_rate = thresholds.get("hr")
    return analysis_schema.AnalysisSettingsRead(
        acute_days=row.acute_days,
        chronic_days=row.chronic_days,
        primary_method=row.primary_method,
        fit_from_s=row.fit_from_s,
        fit_to_s=row.fit_to_s,
        resting_hr=row.resting_hr,
        power_thresholds=power,
        speed_thresholds=speed,
        hr_threshold=analysis_schema.HeartRateThresholdRead(**heart_rate) if heart_rate else None,
        method_params={key: float(value) for key, value in (row.method_params or {}).items()},
    )


def get_settings(user_id: int, db: Session) -> analysis_schema.AnalysisSettingsRead | None:
    """Return one athlete's stored analysis parameters, or ``None`` when unstored.

    Args:
        user_id: The athlete.
        db: Database session.

    Returns:
        The stored settings, or None when the athlete never changed the defaults.
    """
    row = db.scalar(select(analysis_models.AnalysisSettings).where(analysis_models.AnalysisSettings.user_id == user_id))
    return None if row is None else _to_settings_read(row)


def store_settings(
    user_id: int,
    settings: analysis_schema.AnalysisSettingsRead,
    db: Session,
) -> analysis_schema.AnalysisSettingsRead:
    """Insert or update the athlete's single settings row, from its read shape.

    Stages only: the caller owns the transaction. Keeping the write in terms of the
    read schema means the service merges values without holding an ORM row.

    Args:
        user_id: The athlete.
        settings: The full parameter set to store.
        db: Database session.

    Returns:
        The stored settings.
    """
    row = db.scalar(select(analysis_models.AnalysisSettings).where(analysis_models.AnalysisSettings.user_id == user_id))
    if row is None:
        row = analysis_models.AnalysisSettings(user_id=user_id)
        db.add(row)
    row.acute_days = settings.acute_days
    row.chronic_days = settings.chronic_days
    row.primary_method = settings.primary_method.value
    row.fit_from_s = settings.fit_from_s
    row.fit_to_s = settings.fit_to_s
    row.resting_hr = settings.resting_hr
    row.thresholds = _threshold_document(settings)
    row.method_params = dict(settings.method_params)
    db.flush()
    return _to_settings_read(row)


def _threshold_document(settings: analysis_schema.AnalysisSettingsRead) -> dict:
    """Return the threshold JSON document for a settings read."""
    document: dict = {
        "power": {sport: entry.model_dump(mode="json") for sport, entry in settings.power_thresholds.items()},
        "speed": {sport: entry.model_dump(mode="json") for sport, entry in settings.speed_thresholds.items()},
    }
    if settings.hr_threshold is not None:
        document["hr"] = settings.hr_threshold.model_dump(mode="json")
    return document


def replace_activity_loads(
    activity_id: int,
    user_id: int,
    duration_s: float,
    load_date: date | None,
    sport: str | None,
    records: Sequence[analysis_contracts.LoadRecord],
    db: Session,
) -> int:
    """Replace every load row stored for one activity.

    Delete-then-insert makes computing idempotent: re-running it for the same
    activity converges to the same rows instead of duplicating a formula.

    Args:
        activity_id: The activity whose loads are replaced.
        user_id: The activity's owner, copied onto every row for aggregation.
        duration_s: Active time the formulas computed over, kept on every row so a
            stored load can be re-derived without re-reading the activity.
        load_date: Calendar day the activity started in its own timezone.
        sport: Sport group of the activity type, when categorized.
        records: The computed loads, one per formula that applied.
        db: Database session.

    Returns:
        The number of rows written.
    """
    db.execute(delete(analysis_models.ActivityLoad).where(analysis_models.ActivityLoad.activity_id == activity_id))
    for record in records:
        db.add(
            analysis_models.ActivityLoad(
                activity_id=activity_id,
                user_id=user_id,
                method=record.method.value,
                load=record.load,
                intensity_factor=record.intensity_factor,
                threshold_used=record.threshold_used,
                duration_s=duration_s,
                load_date=load_date,
                sport=sport,
            )
        )
    db.flush()
    return len(records)


def list_activity_loads(
    activity_id: int,
    db: Session,
) -> list[analysis_schema.ActivityLoadRead]:
    """Return every stored load of one activity, in formula order.

    Args:
        activity_id: The activity to read.
        db: Database session.

    Returns:
        The activity's loads, empty when none were computed.
    """
    stmt = (
        select(analysis_models.ActivityLoad)
        .where(analysis_models.ActivityLoad.activity_id == activity_id)
        .order_by(analysis_models.ActivityLoad.method)
    )
    return [analysis_schema.ActivityLoadRead.model_validate(row) for row in db.scalars(stmt).all()]


def activity_ids_with_loads(activity_ids: Sequence[int], db: Session) -> set[int]:
    """Return which of the given activities already have a load row.

    Args:
        activity_ids: Activities to check.
        db: Database session.

    Returns:
        The ids that already carry at least one load.
    """
    if not activity_ids:
        return set()
    stmt = (
        select(analysis_models.ActivityLoad.activity_id)
        .where(analysis_models.ActivityLoad.activity_id.in_(activity_ids))
        .distinct()
    )
    return {int(activity_id) for activity_id in db.scalars(stmt).all()}


def daily_load_totals(
    user_id: int,
    method: str,
    db: Session,
    *,
    sport: str | None = None,
) -> list[tuple[date, float]]:
    """Return one athlete's summed load per calendar day, oldest first.

    Sums every activity's row for one formula into the day the activity started
    (the row's own ``load_date``: the activity's local calendar day, copied at
    measure time). Days with no activity are absent; the caller fills them to keep
    the decay continuous. Rows without a date (an activity that carried no start
    time) cannot land on a day and are left out.

    Args:
        user_id: The athlete.
        method: Which formula's rows to sum.
        db: Database session.
        sport: When given, only that sport group contributes.

    Returns:
        ``(day, summed load)`` pairs, ascending.
    """
    stmt = (
        select(analysis_models.ActivityLoad.load_date, func.sum(analysis_models.ActivityLoad.load))
        .where(
            analysis_models.ActivityLoad.user_id == user_id,
            analysis_models.ActivityLoad.method == method,
            analysis_models.ActivityLoad.load_date.isnot(None),
        )
        .group_by(analysis_models.ActivityLoad.load_date)
        .order_by(analysis_models.ActivityLoad.load_date)
    )
    if sport is not None:
        stmt = stmt.where(analysis_models.ActivityLoad.sport == sport)
    return [(row[0], float(row[1])) for row in db.execute(stmt).all()]


def replace_duration_peaks(
    user_id: int,
    sport: str,
    records: Sequence[analysis_contracts.PeakRecord],
    db: Session,
) -> int:
    """Replace one athlete's duration-intensity curve for one sport.

    Args:
        user_id: The athlete.
        sport: Sport group the curve belongs to.
        records: The curve points.
        db: Database session.

    Returns:
        The number of rows written.
    """
    db.execute(
        delete(analysis_models.DurationPeak).where(
            analysis_models.DurationPeak.user_id == user_id,
            analysis_models.DurationPeak.sport == sport,
        )
    )
    for record in records:
        db.add(
            analysis_models.DurationPeak(
                user_id=user_id,
                sport=sport,
                duration_s=record.duration_s,
                value=record.value,
                activity_id=record.activity_id,
            )
        )
    db.flush()
    return len(records)


def list_duration_peaks(
    user_id: int,
    sport: str,
    db: Session,
) -> list[analysis_contracts.PeakRecord]:
    """Return one athlete's stored duration-intensity curve for a sport.

    Args:
        user_id: The athlete.
        sport: Sport group the curve belongs to.
        db: Database session.

    Returns:
        The stored curve points, shortest duration first, empty when the curve
        was never computed.
    """
    stmt = (
        select(
            analysis_models.DurationPeak.duration_s,
            analysis_models.DurationPeak.value,
            analysis_models.DurationPeak.activity_id,
        )
        .where(
            analysis_models.DurationPeak.user_id == user_id,
            analysis_models.DurationPeak.sport == sport,
        )
        .order_by(analysis_models.DurationPeak.duration_s)
    )
    return [
        analysis_contracts.PeakRecord(duration_s=int(duration), value=float(value), activity_id=activity_id)
        for duration, value, activity_id in db.execute(stmt).all()
    ]
