"""SQLAlchemy ORM models for training load, thresholds and duration peaks."""

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, Integer, String, UniqueConstraint
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
        resting_hr: Resting heart rate the athlete states, used by heart-rate
            impulse formulas; ``None`` leaves those formulas off.
        thresholds: Threshold document described above; empty when nothing is known.
        method_params: Per-formula knobs (e.g. a TRIMP coefficient), keyed by method.
    """

    __tablename__ = "analysis_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_analysis_settings_user"),
        CheckConstraint(
            f"primary_method IN ('{analysis_constants.LoadMethod.COGGAN_TSS}', "
            f"'{analysis_constants.LoadMethod.SKIBA_SCORE}', "
            f"'{analysis_constants.LoadMethod.HR_LOAD}', "
            f"'{analysis_constants.LoadMethod.TRIMP}')",
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

    Attributes:
        activity_id: The activity this load belongs to (removed with it).
        method: Which formula produced it.
        load: Stress in the shared scale (100 = one hour at threshold).
        intensity_factor: Intensity the load was derived from.
        threshold_used: Threshold value the formula divided by.
        duration_s: Active time the load was computed over, kept so a row can be
            re-derived without re-reading the activity.
        created_at/updated_at: Row timestamps.
    """

    __tablename__ = "analysis_activity_loads"
    __table_args__ = (
        CheckConstraint(
            f"method IN ('{analysis_constants.LoadMethod.COGGAN_TSS}', "
            f"'{analysis_constants.LoadMethod.SKIBA_SCORE}', "
            f"'{analysis_constants.LoadMethod.HR_LOAD}', "
            f"'{analysis_constants.LoadMethod.TRIMP}')",
            name="ck_analysis_activity_loads_method",
        ),
    )

    activity_id: Mapped[int] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        comment="Activity this load belongs to",
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
