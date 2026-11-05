"""Persistence for analysis settings, per-activity loads and duration peaks.

Only this layer touches the ORM models and executes against the session, and it only
flushes: the service owns the transaction.
"""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    pace: dict[str, analysis_schema.PaceCalibrationRead] = {}
    for sport, entry in (thresholds.get("pace") or {}).items():
        pace[sport] = analysis_schema.PaceCalibrationRead(**entry)
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
        pace_calibrations=pace,
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
        "pace": {sport: entry.model_dump(mode="json") for sport, entry in settings.pace_calibrations.items()},
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


def list_loads_for_activities(
    user_id: int,
    activity_ids: Sequence[int],
    db: Session,
) -> dict[int, list[analysis_schema.ActivityLoadRead]]:
    """Return the stored loads of several of one athlete's activities in one read.

    Args:
        user_id: The loads are scoped to this athlete (the rows copy it at measure time).
        activity_ids: Activities to read.
        db: Database session.

    Returns:
        Activity id to its loads in formula order; activities without rows are absent.
    """
    if not activity_ids:
        return {}
    stmt = (
        select(analysis_models.ActivityLoad)
        .where(
            analysis_models.ActivityLoad.user_id == user_id,
            analysis_models.ActivityLoad.activity_id.in_(activity_ids),
        )
        .order_by(analysis_models.ActivityLoad.activity_id, analysis_models.ActivityLoad.method)
    )
    grouped: dict[int, list[analysis_schema.ActivityLoadRead]] = {}
    for row in db.scalars(stmt).all():
        grouped.setdefault(row.activity_id, []).append(analysis_schema.ActivityLoadRead.model_validate(row))
    return grouped


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


def replace_peak_flags(
    user_id: int,
    sport: str,
    records: Sequence[analysis_contracts.PeakFlagRecord],
    db: Session,
) -> int:
    """Replace one athlete's peak-plausibility flags for one sport.

    Args:
        user_id: The athlete.
        sport: Sport group the flags were raised for.
        records: The refused sessions.
        db: Database session.

    Returns:
        The number of rows written.
    """
    db.execute(
        delete(analysis_models.PeakFlag).where(
            analysis_models.PeakFlag.user_id == user_id,
            analysis_models.PeakFlag.sport == sport,
        )
    )
    for record in records:
        db.add(
            analysis_models.PeakFlag(
                user_id=user_id,
                sport=sport,
                activity_id=record.activity_id,
                started_on=record.started_on,
                duration_s=record.duration_s,
                value_w=record.value_w,
                session_np_w=record.session_np_w,
                ratio=record.ratio,
                cap=record.cap,
            )
        )
    return len(records)


def replace_threshold_fits(
    user_id: int,
    sport: str,
    measure: str,
    records: Sequence[analysis_contracts.ThresholdFitRecord],
    db: Session,
) -> int:
    """Replace one athlete's persisted threshold evidence for one sport and measure.

    Args:
        user_id: The athlete.
        sport: Sport group the fits belong to.
        measure: ``power`` or ``speed``.
        records: The quality-gated fits.
        db: Database session.

    Returns:
        The number of rows written.
    """
    db.execute(
        delete(analysis_models.ThresholdFit).where(
            analysis_models.ThresholdFit.user_id == user_id,
            analysis_models.ThresholdFit.sport == sport,
            analysis_models.ThresholdFit.measure == measure,
        )
    )
    for record in records:
        db.add(
            analysis_models.ThresholdFit(
                user_id=user_id,
                sport=sport,
                measure=measure,
                activity_id=record.activity_id,
                started_on=record.started_on,
                value=record.value,
                w_prime=record.w_prime,
                cv_pct=record.cv_pct,
                sustained=record.sustained,
                power_source=record.power_source,
            )
        )
    return len(records)


def list_threshold_fits(
    user_id: int,
    sport: str,
    measure: str,
    db: Session,
) -> list[analysis_models.ThresholdFit]:
    """Return persisted threshold evidence for one athlete, sport and measure.

    Args:
        user_id: The athlete.
        sport: Sport group stored.
        measure: ``power`` or ``speed``.
        db: Database session.

    Returns:
        The fit rows, oldest day first.
    """
    return list(
        db.scalars(
            select(analysis_models.ThresholdFit)
            .where(
                analysis_models.ThresholdFit.user_id == user_id,
                analysis_models.ThresholdFit.sport == sport,
                analysis_models.ThresholdFit.measure == measure,
            )
            .order_by(analysis_models.ThresholdFit.started_on)
        ).all()
    )


def clear_threshold_fits(
    user_id: int | None,
    db: Session,
) -> int:
    """Drop persisted threshold evidence (``None``: every athlete) before a rebuild.

    Args:
        user_id: The athlete whose evidence expires, or ``None`` for all.
        db: Database session.

    Returns:
        The number of rows removed.
    """
    stmt = delete(analysis_models.ThresholdFit)
    if user_id is not None:
        stmt = stmt.where(analysis_models.ThresholdFit.user_id == user_id)
    result = db.execute(stmt)
    return result.rowcount


def list_peak_flags(
    user_id: int,
    sport: str,
    db: Session,
) -> list[analysis_models.PeakFlag]:
    """Return one athlete's peak-plausibility flags for one sport, oldest session first.

    Args:
        user_id: The athlete.
        sport: Sport group to read.
        db: Database session.

    Returns:
        The flag rows.
    """
    return list(
        db.scalars(
            select(analysis_models.PeakFlag)
            .where(
                analysis_models.PeakFlag.user_id == user_id,
                analysis_models.PeakFlag.sport == sport,
            )
            .order_by(analysis_models.PeakFlag.started_on)
        ).all()
    )


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


def replace_trail_observation(
    user_id: int,
    activity_id: int,
    method: str,
    observation: analysis_contracts.TrailSessionRecord,
    sections: Sequence[analysis_contracts.TrailSectionRecord],
    db: Session,
) -> int:
    """Replace one activity's stored technical observation for one detector model.

    Args:
        user_id: The athlete the observation belongs to.
        activity_id: The session measured.
        method: The detector parameters stamped on the rows.
        observation: The session-level context, always written: measuring and
            finding nothing technical is a result.
        sections: The technical stretches found in it.
        db: Database session.

    Returns:
        The number of stretch rows written.
    """
    db.execute(
        delete(analysis_models.TrailSection).where(
            analysis_models.TrailSection.user_id == user_id,
            analysis_models.TrailSection.activity_id == activity_id,
            analysis_models.TrailSection.method == method,
        )
    )
    db.execute(
        delete(analysis_models.TrailSession).where(
            analysis_models.TrailSession.user_id == user_id,
            analysis_models.TrailSession.activity_id == activity_id,
            analysis_models.TrailSession.method == method,
        )
    )
    db.add(
        analysis_models.TrailSession(
            user_id=user_id,
            activity_id=activity_id,
            method=method,
            v_base_ms=observation.v_base_ms,
            cadence_available=observation.cadence_available,
            total_m=observation.total_m,
            technical_m=observation.technical_m,
        )
    )
    # the stretches point at their session row by a composite key the mappers do
    # not know about, so the parent must be in the database before they are added
    db.flush()
    for section in sections:
        db.add(
            analysis_models.TrailSection(
                user_id=user_id,
                activity_id=activity_id,
                method=method,
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
                v_base_ms=section.v_base_ms,
                cadence_available=section.cadence_available,
                total_m=section.total_m,
                start_t=section.start_t,
                end_t=section.end_t,
            )
        )
    return len(sections)


def list_trail_observation(
    activity_id: int,
    method: str,
    db: Session,
) -> tuple[analysis_models.TrailSession, list[analysis_models.TrailSection]] | None:
    """Return one activity's stored technical observation for one detector model.

    Args:
        activity_id: The session asked about.
        method: The detector parameters stamped on the rows.
        db: Database session.

    Returns:
        The session row and its stretches in ground order, or ``None`` when the
        activity was never measured under those parameters.
    """
    session_row = db.scalars(
        select(analysis_models.TrailSession).where(
            analysis_models.TrailSession.activity_id == activity_id,
            analysis_models.TrailSession.method == method,
        )
    ).first()
    if session_row is None:
        return None
    sections = list(
        db.scalars(
            select(analysis_models.TrailSection)
            .where(
                analysis_models.TrailSection.activity_id == activity_id,
                analysis_models.TrailSection.method == method,
            )
            .order_by(analysis_models.TrailSection.start_m)
        )
    )
    return session_row, sections


def activity_ids_with_trail_sessions(
    activity_ids: Sequence[int],
    method: str,
    db: Session,
) -> set[int]:
    """Return which of these activities already carry a technical observation.

    Args:
        activity_ids: The activities to check.
        method: The detector parameters stamped on the rows.
        db: Database session.

    Returns:
        The ids already measured under those parameters.
    """
    if not activity_ids:
        return set()
    return set(
        db.scalars(
            select(analysis_models.TrailSession.activity_id).where(
                analysis_models.TrailSession.activity_id.in_(activity_ids),
                analysis_models.TrailSession.method == method,
            )
        )
    )


def replace_osm_paths(
    paths: Sequence[analysis_contracts.OsmPathRecord],
    db: Session,
) -> int:
    """Store an imported OpenStreetMap extract, replacing what it re-describes.

    Args:
        paths: The ways the extract carries.
        db: Database session.

    Returns:
        The number of ways written.
    """
    for path in paths:
        db.execute(
            pg_insert(analysis_models.OsmPath)
            .values(
                way_id=path.way_id,
                name=path.name,
                tags=path.tags,
                geometry=[list(point) for point in path.geometry],
                length_m=path.length_m,
                min_lat=path.min_lat,
                min_lng=path.min_lng,
                max_lat=path.max_lat,
                max_lng=path.max_lng,
                cache_date=path.cache_date,
            )
            .on_conflict_do_update(
                index_elements=[analysis_models.OsmPath.way_id],
                set_={
                    "name": path.name,
                    "tags": path.tags,
                    "geometry": [list(point) for point in path.geometry],
                    "length_m": path.length_m,
                    "min_lat": path.min_lat,
                    "min_lng": path.min_lng,
                    "max_lat": path.max_lat,
                    "max_lng": path.max_lng,
                    "cache_date": path.cache_date,
                },
            )
        )
    return len(paths)


def get_osm_cache_date(db: Session) -> date | None:
    """Return the day of the newest stored OpenStreetMap extract.

    Args:
        db: Database session.

    Returns:
        The extract date, or ``None`` when no cartography has been imported.
    """
    return db.execute(select(func.max(analysis_models.OsmPath.cache_date))).scalar()


def list_osm_paths_in_box(
    min_lat: float,
    max_lat: float,
    min_lng: float,
    max_lng: float,
    db: Session,
) -> list[analysis_models.OsmPath]:
    """Return the stored ways whose bounding box touches an area.

    A bounding-box pre-filter only: the geometry question is answered by
    :func:`modules.analysis.trail_osm.match_stretch`, not by this query.

    Args:
        min_lat: South edge of the area.
        max_lat: North edge.
        min_lng: West edge.
        max_lng: East edge.
        db: Database session.

    Returns:
        The candidate ways.
    """
    return list(
        db.scalars(
            select(analysis_models.OsmPath).where(
                analysis_models.OsmPath.min_lat <= max_lat,
                analysis_models.OsmPath.max_lat >= min_lat,
                analysis_models.OsmPath.min_lng <= max_lng,
                analysis_models.OsmPath.max_lng >= min_lng,
            )
        )
    )


def get_osm_paths_by_ids(
    way_ids: Sequence[int],
    db: Session,
) -> dict[int, analysis_models.OsmPath]:
    """Return stored ways by id, for answering a read.

    Args:
        way_ids: The ways asked about.
        db: Database session.

    Returns:
        A mapping of way id to row; ids with no row are simply absent.
    """
    if not way_ids:
        return {}
    return {
        path.way_id: path
        for path in db.scalars(select(analysis_models.OsmPath).where(analysis_models.OsmPath.way_id.in_(way_ids)))
    }


def list_sections_needing_osm_match(
    method: str,
    cache_date: date,
    db: Session,
    *,
    limit: int,
) -> list[analysis_models.TrailSection]:
    """Return measured stretches not yet matched against the current extract.

    A stretch already matched - or honestly found to lie on no mapped way - carries
    the date of the extract it was checked against, so an import of a newer one is
    what reopens the question.

    Args:
        method: The detector parameters stamped on the observations.
        cache_date: Day of the extract in store.
        db: Database session.
        limit: Maximum number of stretches to hand back.

    Returns:
        The stretches, oldest activity first.
    """
    return list(
        db.scalars(
            select(analysis_models.TrailSection)
            .where(
                analysis_models.TrailSection.method == method,
                or_(
                    analysis_models.TrailSection.osm_cache_date.is_(None),
                    analysis_models.TrailSection.osm_cache_date < cache_date,
                ),
            )
            .order_by(analysis_models.TrailSection.activity_id, analysis_models.TrailSection.start_m)
            .limit(limit)
        )
    )


def set_trail_section_osm_match(
    section: analysis_models.TrailSection,
    match: analysis_contracts.OsmMatchRecord | None,
    cache_date: date,
    db: Session,
) -> None:
    """Record what the cartography says about one stretch (including "nothing").

    Args:
        section: The stored stretch.
        match: The way it lies on, or ``None`` when no mapped way explains it.
        cache_date: Day of the extract the answer was measured against.
        db: Database session.
    """
    section.osm_way_id = match.way_id if match else None
    section.osm_start_m = match.start_m if match else None
    section.osm_end_m = match.end_m if match else None
    section.osm_covered_m = match.covered_m if match else None
    section.osm_cache_date = cache_date
    db.add(section)


def replace_trail_mark(
    user_id: int,
    activity_id: int,
    start_m: float,
    verdict: str,
    db: Session,
) -> analysis_models.TrailMark:
    """Place (or replace) the athlete's verdict on one measured stretch.

    Args:
        user_id: The athlete judging.
        activity_id: The session the stretch belongs to.
        start_m: Ground position of the stretch, as the detector reported it.
        verdict: ``agree`` / ``overstated`` / ``wrong``.
        db: Database session.

    Returns:
        The stored mark.
    """
    db.execute(
        delete(analysis_models.TrailMark).where(
            analysis_models.TrailMark.user_id == user_id,
            analysis_models.TrailMark.activity_id == activity_id,
            analysis_models.TrailMark.start_m == start_m,
        )
    )
    mark = analysis_models.TrailMark(
        user_id=user_id,
        activity_id=activity_id,
        start_m=start_m,
        verdict=verdict,
    )
    db.add(mark)
    db.flush()
    return mark


def delete_trail_mark(
    user_id: int,
    activity_id: int,
    start_m: float,
    db: Session,
) -> bool:
    """Take back the verdict on one stretch, when there was one.

    Args:
        user_id: The athlete judging.
        activity_id: The session the stretch belongs to.
        start_m: Ground position of the stretch.
        db: Database session.

    Returns:
        ``True`` when a mark was removed.
    """
    result = db.execute(
        delete(analysis_models.TrailMark).where(
            analysis_models.TrailMark.user_id == user_id,
            analysis_models.TrailMark.activity_id == activity_id,
            analysis_models.TrailMark.start_m == start_m,
        )
    )
    return bool(result.rowcount)


def list_trail_marks(
    activity_id: int,
    user_id: int,
    db: Session,
) -> list[analysis_models.TrailMark]:
    """Return the athlete's verdicts on one activity's stretches.

    Args:
        activity_id: The session judged.
        user_id: The athlete who judged.
        db: Database session.

    Returns:
        The marks, in ground order.
    """
    return list(
        db.scalars(
            select(analysis_models.TrailMark)
            .where(
                analysis_models.TrailMark.activity_id == activity_id,
                analysis_models.TrailMark.user_id == user_id,
            )
            .order_by(analysis_models.TrailMark.start_m)
        )
    )
