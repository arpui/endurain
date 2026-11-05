"""SQLAlchemy ORM models for training load, thresholds and duration peaks."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import modules.analysis.constants as analysis_constants
from core.database import Base, TimestampMixin


class AnalysisSettings(Base, TimestampMixin):
    """One athlete's analysis parameters: time constants, methods and thresholds.

    Thresholds live in one JSON document rather than a table because they are read as
    a whole by every load computation and written as a whole by the threshold pass or
    by the athlete; a row per sport would need the same read anyway and would let a
    partial write leave a sport with no threshold. The shape is
    ``{"power": {sport: {"value_w": …, "w_prime_j": …, "source": "auto|manual",
    "cv_pct": …}}, "hr": {"value_bpm": …, "source": "auto|manual"}}``.

    Attributes:
        id: Surrogate primary key.
        user_id: The athlete these settings belong to (one row per user).
        acute_days: Fatigue time constant, in days.
        chronic_days: Fitness time constant, in days.
        primary_method: Load formula used when a caller asks for one load.
        fit_from_s: Shortest peak duration the critical-power fit uses; ``None``
            leaves the module default.
        fit_to_s: Longest peak duration the critical-power fit uses; ``None``
            leaves the module default.
        resting_hr: Resting heart rate the athlete states, used by heart-rate
            impulse formulas; ``None`` leaves those formulas off.
        thresholds: Threshold document described above; empty when nothing is known.
        method_params: Per-formula knobs (e.g. a TRIMP coefficient), keyed by method.
    """

    __tablename__ = "analysis_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_analysis_settings_user"),
        CheckConstraint(
            f"primary_method IN ({analysis_constants.method_sql_list()})",
            name="ck_analysis_settings_primary_method",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Athlete these parameters belong to",
    )
    acute_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=analysis_constants.ACUTE_DAYS_DEFAULT,
        server_default=str(analysis_constants.ACUTE_DAYS_DEFAULT),
        comment="Fatigue time constant in days",
    )
    chronic_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=analysis_constants.CHRONIC_DAYS_DEFAULT,
        server_default=str(analysis_constants.CHRONIC_DAYS_DEFAULT),
        comment="Fitness time constant in days",
    )
    primary_method: Mapped[str] = mapped_column(
        String(length=32),
        nullable=False,
        default=analysis_constants.PRIMARY_METHOD_DEFAULT,
        server_default=analysis_constants.PRIMARY_METHOD_DEFAULT,
        comment="Load formula used when one load is asked for",
    )
    fit_from_s: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Shortest peak duration used by the critical-power fit, when not default",
    )
    fit_to_s: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Longest peak duration used by the critical-power fit, when not default",
    )
    resting_hr: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Resting heart rate in bpm stated by the athlete, when known",
    )
    thresholds: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Power and heart-rate thresholds, with their provenance",
    )
    method_params: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Per-formula parameters keyed by method name",
    )


class ActivityLoad(Base):
    """One activity's load as measured by one formula.

    One row per (activity, formula) so the formulas coexist: the athlete can see the
    standard score beside a burst-sensitive one and compare them, and changing the
    threshold used is a recompute rather than a reinterpretation.

    The athlete, the local calendar day and the sport group are copied onto the row
    from the parent activity at measure time. They duplicate data the activities
    module owns, and that is the point: the daily fitness series groups by them in
    one indexed read of this table, without the analysis persistence reaching into
    another module's models or every chart request re-reading the activity list. An
    edit to what they derive from (start time, timezone, sport) re-measures the
    activity, the same rule that keeps ``threshold_used`` honest.

    Attributes:
        activity_id: The activity this load belongs to (removed with it).
        user_id: The activity's owner, copied for per-athlete aggregation.
        method: Which formula produced it.
        load: Stress in the shared scale (100 = one hour at threshold).
        intensity_factor: Intensity the load was derived from.
        threshold_used: Threshold value the formula divided by.
        duration_s: Active time the load was computed over, kept so a row can be
            re-derived without re-reading the activity.
        load_date: Calendar day the activity started in its own timezone (the day
            the load lands on in the fitness series), ``None`` when the activity
            carries no start time.
        sport: Sport group of the activity's type, ``None`` when uncategorized.
    """

    __tablename__ = "analysis_activity_loads"
    __table_args__ = (
        CheckConstraint(
            f"method IN ({analysis_constants.method_sql_list()})",
            name="ck_analysis_activity_loads_method",
        ),
        Index("ix_analysis_activity_loads_user_method_date", "user_id", "method", "load_date"),
    )

    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        comment="Activity this load belongs to",
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Activity owner, copied so daily series aggregate in one read",
    )
    method: Mapped[str] = mapped_column(
        String(length=32),
        primary_key=True,
        comment="Formula that produced this load",
    )
    load: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Stress score: 100 is one hour held at the athlete's threshold",
    )
    intensity_factor: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Intensity the load was derived from",
    )
    threshold_used: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Threshold value the formula divided by",
    )
    duration_s: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Active time the load was computed over, in seconds",
    )
    load_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Calendar day the activity started, in its own timezone",
    )
    sport: Mapped[str | None] = mapped_column(
        String(length=32),
        nullable=True,
        comment="Sport group of the activity type, when categorized",
    )


class DurationPeak(Base, TimestampMixin):
    """An athlete's best sustained average for one duration of one sport.

    The points of the duration-intensity curve, cached per athlete and sport so the
    curve and the derived thresholds do not reread every stream on request. The
    activity holding each point is recorded so a plotted peak can be traced back to
    the session that produced it.

    Attributes:
        user_id: The athlete the peaks belong to.
        sport: Sport group the curve is drawn for (the activities module's grouping).
        duration_s: Duration the average was held for.
        value: Best sustained average, in watts for power sports.
        activity_id: Activity holding the best average.
    """

    __tablename__ = "analysis_duration_peaks"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Athlete this curve point belongs to",
    )
    sport: Mapped[str] = mapped_column(
        String(length=32),
        primary_key=True,
        comment="Sport group the curve is drawn for",
    )
    duration_s: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        comment="Duration the average was held for, in seconds",
    )
    value: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Best sustained average at that duration",
    )
    activity_id: Mapped[int | None] = mapped_column(
        ForeignKey("activities.id", ondelete="SET NULL"),
        nullable=True,
        comment="Activity holding this best average",
    )


class ThresholdFit(Base, TimestampMixin):
    """One session's quality-gated threshold fit, persisted as temporal evidence.

    Rebuilding the per-session fits means reading every power (or speed) stream
    the athlete owns - a minute of work that used to repeat on the first request
    after each restart. The fits depend only on the streams, so they are kept
    here: built once per pass and dropped whenever the streams are re-measured
    (threshold refresh, full recompute), so the temporal line and the chart
    answer from a plain query the rest of the time.

    Attributes:
        user_id: The athlete the evidence belongs to.
        sport: Sport group the fit was made for.
        measure: ``power`` (watts) or ``speed`` (metres per second).
        activity_id: The session the fit came from.
        started_on: Day the fit supports.
        value: Fitted threshold, in the measure's units.
    """

    __tablename__ = "analysis_threshold_fits"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Athlete the evidence belongs to",
    )
    sport: Mapped[str] = mapped_column(
        String(length=32),
        primary_key=True,
        comment="Sport group the fit was made for",
    )
    measure: Mapped[str] = mapped_column(
        String(length=8),
        primary_key=True,
        comment="power (watts) or speed (metres per second)",
    )
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Session the fit came from",
    )
    started_on: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Day the fit supports",
    )
    value: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Fitted threshold in the measure's units",
    )
    w_prime: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="Fitted anaerobic capacity (joules or metres): an index tied to the fit window",
    )
    cv_pct: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="Regression error of the fit, percent of the mean",
    )
    sustained: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="Best sustained window average inside the fit range, the ceiling the fit must not overshoot",
    )
    power_source: Mapped[str | None] = mapped_column(
        String(length=32),
        nullable=True,
        comment="Where the session's watts came from, when tagged",
    )


class PeakFlag(Base, TimestampMixin):
    """One session the peak builder refused, with the numbers behind the refusal.

    The duration-intensity cache is a maximum over every session's samples, so one
    corrupt stream (a sensor runaway: a thousand watts at walking speed) would
    otherwise define the athlete's record forever. When a session's peak is
    implausible the builder skips it and records one of these rows: the session is
    never deleted or edited - its data stands as imported - but the curve and the
    derived thresholds stop trusting it, and the athlete can see which sessions
    were held out and why.

    Attributes:
        user_id: The athlete the flag belongs to.
        sport: Sport group the refused peak was fitted for.
        activity_id: The session held out of the curve.
        started_on: Day of the session, kept so the notice survives the activity.
        duration_s: Duration of the strongest violation, in seconds.
        value_w: The refused best average, in watts.
        session_np_w: The session's own normalized power, in watts.
        ratio: ``value_w / session_np_w``.
        cap: The tier ratio the session exceeded.
    """

    __tablename__ = "analysis_peak_flags"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Athlete this flag belongs to",
    )
    sport: Mapped[str] = mapped_column(
        String(length=32),
        primary_key=True,
        comment="Sport group the refused peak was fitted for",
    )
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Session held out of the curve",
    )
    started_on: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Day of the session, kept so the notice survives the activity",
    )
    duration_s: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Duration of the strongest violation, in seconds",
    )
    value_w: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="The refused best average, in watts",
    )
    session_np_w: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="The session's own normalized power, in watts",
    )
    ratio: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Refused value over the session's normalized power",
    )
    cap: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="The tier ratio the session exceeded",
    )


class TrailSession(Base, TimestampMixin):
    """One run activity as the technical-terrain detector measured it, once per model.

    A session row exists whether or not the detector found anything technical in it:
    "measured, nothing technical" is a result, and without it every backfill pass
    would walk the same unremarkable runs again. It also carries the session-level
    context the stretches are ratios of - the athlete's own reference pace for that
    day - so a stored stretch can be read without reopening the streams.

    Attributes:
        user_id: The athlete the observation belongs to.
        activity_id: The session measured.
        method: The detector parameters that produced the row, stamped.
        v_base_ms: The session's own flat-ground reference pace.
        cadence_available: Whether the stride terms could be used at all.
        total_m: Ground covered by the session.
        technical_m: Ground flagged technical over it.
    """

    __tablename__ = "analysis_trail_sessions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Athlete the observation belongs to",
    )
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Session measured",
    )
    method: Mapped[str] = mapped_column(
        String(length=64),
        primary_key=True,
        comment="Detector parameters that produced the row, stamped",
    )
    v_base_ms: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="The session's own flat-ground reference pace"
    )
    cadence_available: Mapped[bool] = mapped_column(
        nullable=False, default=True, comment="Whether the stride terms could be used at all"
    )
    total_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, comment="Ground covered by the session")
    technical_m: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="Ground flagged technical over it"
    )
    coverage_cache_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Extract the session's full track was matched against",
    )


class OsmPath(Base, TimestampMixin):
    """One OpenStreetMap way the trail detector can be read against.

    What the map says about the ground (``sac_scale``, ``mtb:scale``, ``surface``,
    ``trail_visibility``) and what the athlete's own pace and stride say about it
    are two independent answers, and the interesting cases are where they
    disagree: an unmapped gully, or a path tagged innocently that has eroded into
    steps. Keeping the cartography in a table - imported by a maintenance script,
    never fetched per request - lets both answers sit side by side and lets the
    extract's date be recorded with them.

    Attributes:
        way_id: The OpenStreetMap way id.
        name: Way name, when mapped.
        tags: The cartographic attributes the way carries.
        geometry: Ordered ``[[lat, lng], ...]`` polyline as the way is drawn.
        length_m: Length along the stored geometry.
        min_lat: South edge of the geometry bounding box.
        min_lng: West edge of the geometry bounding box.
        max_lat: North edge of the geometry bounding box.
        max_lng: East edge of the geometry bounding box.
        cache_date: Day the extract was taken.
    """

    __tablename__ = "analysis_osm_paths"

    way_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="OpenStreetMap way id",
    )
    name: Mapped[str | None] = mapped_column(String(length=256), nullable=True, comment="Way name, when mapped")
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="Cartographic attributes the way carries")
    geometry: Mapped[list] = mapped_column(
        JSONB, nullable=False, comment="Ordered [[lat, lng], ...] polyline as the way is drawn"
    )
    length_m: Mapped[float] = mapped_column(Float, nullable=False, comment="Length along the stored geometry")
    min_lat: Mapped[float] = mapped_column(Float, nullable=False, comment="South edge of the geometry bounding box")
    min_lng: Mapped[float] = mapped_column(Float, nullable=False, comment="West edge of the geometry bounding box")
    max_lat: Mapped[float] = mapped_column(Float, nullable=False, comment="North edge of the geometry bounding box")
    max_lng: Mapped[float] = mapped_column(Float, nullable=False, comment="East edge of the geometry bounding box")
    cache_date: Mapped[date] = mapped_column(Date, nullable=False, comment="Day the extract was taken")


class TrailMark(Base, TimestampMixin):
    """What the athlete says about one stretch the detector claimed.

    The index is a hypothesis about ground; the athlete's memory of the route is
    the only ground truth there is. Keeping the verdict next to the stretch is what
    lets a calibration be judged against labels: which stretches were really that
    hard, which were technical but not as claimed, and which were never technical -
    the tired-leg case, the marked path with knee-high grass.

    Attributes:
        user_id: The athlete judging.
        activity_id: The session the judged stretch belongs to.
        start_m: Ground position of the stretch, as the detector reported it.
        verdict: ``agree`` / ``overstated`` / ``wrong``.
    """

    __tablename__ = "analysis_trail_marks"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Athlete judging the stretch",
    )
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Session the judged stretch belongs to",
    )
    start_m: Mapped[float] = mapped_column(
        Float,
        primary_key=True,
        comment="Ground position of the judged stretch, as the detector reported it",
    )
    verdict: Mapped[str] = mapped_column(String(length=16), nullable=False, comment="agree / overstated / wrong")

    __table_args__ = (
        CheckConstraint(
            f"verdict IN ({analysis_constants.trail_verdict_sql_list()})",
            name="ck_analysis_trail_marks_verdict",
        ),
        Index("ix_analysis_trail_marks_activity", "activity_id"),
        Index("ix_analysis_trail_marks_user_verdict", "user_id", "verdict"),
    )


class TrailSection(Base, TimestampMixin):
    """One technical stretch the trail detector read in a session.

    The detector answers "was the *ground* costing here, or only the gradient?" and
    its weights are still being calibrated against what the athlete remembers of
    specific routes. So each row keeps the three components it weighed (pace short
    of what the grade explains, cadence instability, braking in descent) next to the
    index they produced, the session's own reference pace, and the parameters that
    produced them. Recalibrating then measures the same ground again: rows written
    by an older model stay readable and comparable instead of being silently
    rewritten, which is what makes a weight change reviewable after the fact.

    Attributes:
        user_id: The athlete the observation belongs to.
        activity_id: The session the stretch comes from.
        method: The detector parameters that produced the row, stamped.
        start_m: Ground position where the stretch starts, metres from the start.
        grade_pct: Mean grade over it, percent.
        length_m: Ground covered by the stretch.
        avg_speed_ms: Mean ground speed through it.
        gap_deficit: Shortfall against the pace the grade explains (0-1).
        cadence_jitter: Cadence instability over it (0-1).
        brake_score: Downhill braking share over it (0-1).
        score: Peak smoothed technicality index inside it.
        direction: ``descent`` / ``ascent`` / ``flat`` by mean grade.
        v_base_ms: The session's own flat-ground reference pace.
        cadence_available: Whether the stride terms could be used at all.
        total_m: Ground covered by the session.
        start_t: Timestamp of the stretch's first sample (map polyline).
        end_t: Timestamp of its last sample.
    """

    __tablename__ = "analysis_trail_sections"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Athlete the observation belongs to",
    )
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Session the stretch comes from",
    )
    method: Mapped[str] = mapped_column(
        String(length=64),
        primary_key=True,
        comment="Detector parameters that produced the row, stamped",
    )
    start_m: Mapped[float] = mapped_column(
        Float,
        primary_key=True,
        comment="Ground position where the stretch starts, metres from the start",
    )
    length_m: Mapped[float] = mapped_column(Float, nullable=False, comment="Ground covered by the stretch")
    grade_pct: Mapped[float] = mapped_column(Float, nullable=False, comment="Mean grade over it, percent")
    avg_speed_ms: Mapped[float] = mapped_column(Float, nullable=False, comment="Mean ground speed through it")
    gap_deficit: Mapped[float] = mapped_column(
        Float, nullable=False, comment="Shortfall against the pace the grade explains (0-1)"
    )
    cadence_jitter: Mapped[float] = mapped_column(Float, nullable=False, comment="Cadence instability over it (0-1)")
    brake_score: Mapped[float] = mapped_column(Float, nullable=False, comment="Downhill braking share over it (0-1)")
    score: Mapped[float] = mapped_column(Float, nullable=False, comment="Peak smoothed technicality index inside it")
    direction: Mapped[str] = mapped_column(
        String(length=8),
        nullable=False,
        comment="descent / ascent / flat by mean grade",
    )
    v_base_ms: Mapped[float] = mapped_column(
        Float, nullable=False, comment="The session's own flat-ground reference pace"
    )
    cadence_available: Mapped[bool] = mapped_column(
        nullable=False, default=True, comment="Whether the stride terms could be used at all"
    )
    total_m: Mapped[float] = mapped_column(Float, nullable=False, comment="Ground covered by the session")
    start_t: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp of the stretch's first sample, to draw its polyline",
    )
    end_t: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Timestamp of the stretch's last sample"
    )
    osm_way_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_osm_paths.way_id", ondelete="SET NULL"),
        nullable=True,
        comment="Way the stretch was matched to",
    )
    osm_start_m: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Offset along the way where the stretch starts"
    )
    osm_end_m: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Offset along the way where the stretch ends"
    )
    osm_covered_m: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Ground of the stretch the way accounts for"
    )
    core_start_m: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Ground position where the hard block inside starts"
    )
    core_end_m: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Ground position where that block ends"
    )
    core_score: Mapped[float | None] = mapped_column(Float, nullable=True, comment="Peak index inside the hard block")
    osm_cache_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Extract the stretch was matched against (also records an honest miss)",
    )

    __table_args__ = (
        CheckConstraint(
            f"direction IN ({analysis_constants.trail_direction_sql_list()})",
            name="ck_analysis_trail_sections_direction",
        ),
        ForeignKeyConstraint(
            ["user_id", "activity_id", "method"],
            [
                "analysis_trail_sessions.user_id",
                "analysis_trail_sessions.activity_id",
                "analysis_trail_sessions.method",
            ],
            ondelete="CASCADE",
        ),
        Index("ix_analysis_trail_sections_user_method", "user_id", "method"),
        Index("ix_analysis_trail_sections_osm_way", "osm_way_id"),
    )


class WayCrossing(Base, TimestampMixin):
    """One pass of one activity over one mapped way.

    Technical stretches show where the ground was remarkable; crossings show
    where the athlete actually ran. The full set is what makes the network a
    network: the ordinary paths between the hard ones are the ground a future
    route is assembled from, and they are missing until each activity's whole
    track is matched, not just its technical parts.

    Attributes:
        user_id: The athlete who crossed it.
        activity_id: The session of the crossing.
        way_id: The OSM way the track ran along.
        start_m: Offset along the way (drawn order) where the crossing starts.
        end_m: Offset along the way where it ends.
        covered_m: Ground of the crossing the way accounts for.
        cache_date: Day of the extract this crossing was checked against.
    """

    __tablename__ = "analysis_way_crossings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, comment="Athlete who crossed it"
    )
    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True, comment="Session of the crossing"
    )
    way_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_osm_paths.way_id", ondelete="CASCADE"), primary_key=True, comment="OSM way crossed"
    )
    start_m: Mapped[float] = mapped_column(
        Float, primary_key=True, comment="Smallest along-way offset that session reached on the way"
    )
    end_m: Mapped[float] = mapped_column(Float, nullable=False, comment="Offset along the way where it ends")
    covered_m: Mapped[float] = mapped_column(
        Float, nullable=False, comment="Ground of the crossing the way accounts for"
    )
    cache_date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="Extract the crossing was checked against (an honest miss has no row)"
    )

    __table_args__ = (
        Index("ix_analysis_way_crossings_activity", "activity_id"),
        Index("ix_analysis_way_crossings_user_way", "user_id", "way_id"),
    )
