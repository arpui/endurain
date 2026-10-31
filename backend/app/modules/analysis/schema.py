"""Pydantic schemas for training load, thresholds and analysis settings."""

from collections.abc import Mapping
from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

import modules.analysis.constants as analysis_constants
import modules.analysis.intervals as analysis_intervals


class ActivityLoadRead(BaseModel):
    """Serialized load of one activity, as measured by one formula.

    Attributes:
        method: Formula that produced the load.
        load: Stress score (100 = one hour held at the athlete's threshold).
        intensity_factor: Intensity the load was derived from.
        threshold_used: Threshold value the formula divided by.
        duration_s: Active time the load was computed over.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    method: analysis_constants.LoadMethod
    load: StrictFloat
    intensity_factor: StrictFloat | None
    threshold_used: StrictFloat | None
    duration_s: StrictFloat


class PowerThresholdRead(BaseModel):
    """Serialized power threshold for one sport, with its provenance.

    Attributes:
        value_w: Threshold power in watts.
        w_prime_j: Fitted anaerobic work capacity in joules, an index rather than an
            absolute: it depends on the duration range the fit used.
        source: Derived or entered by the athlete.
        cv_pct: Fit error as a percentage of the mean, derived values only.
    """

    model_config = ConfigDict(extra="forbid")

    value_w: StrictFloat
    w_prime_j: StrictFloat | None
    source: analysis_constants.ThresholdSource
    cv_pct: StrictFloat | None


class HeartRateThresholdRead(BaseModel):
    """Serialized threshold heart rate, with its provenance."""

    model_config = ConfigDict(extra="forbid")

    value_bpm: StrictFloat
    source: analysis_constants.ThresholdSource


class AnalysisSettingsRead(BaseModel):
    """Serialized analysis parameters of one athlete.

    Attributes:
        acute_days: Fatigue time constant.
        chronic_days: Fitness time constant.
        primary_method: Formula used when a single load is asked for.
        fit_from_s: Shortest peak duration the critical-power fit uses, ``None``
            while the module default applies.
        fit_to_s: Longest peak duration the critical-power fit uses, ``None``
            while the module default applies.
        resting_hr: Resting heart rate stated by the athlete.
        power_thresholds: Threshold per sport group, derived or manual.
        hr_threshold: Threshold heart rate, derived or manual.
        method_params: Per-formula parameters.
    """

    model_config = ConfigDict(extra="forbid")

    acute_days: StrictInt
    chronic_days: StrictInt
    primary_method: analysis_constants.LoadMethod
    fit_from_s: StrictInt | None = None
    fit_to_s: StrictInt | None = None
    resting_hr: StrictInt | None = None
    power_thresholds: dict[StrictStr, PowerThresholdRead] = Field(default_factory=dict)
    hr_threshold: HeartRateThresholdRead | None = None
    method_params: dict[StrictStr, StrictFloat] = Field(default_factory=dict)


class AnalysisSettingsUpdate(BaseModel):
    """Body updating an athlete's analysis parameters.

    Every field is optional and only what is sent changes. Sending ``null`` for a
    threshold returns that sport to the derived value instead of leaving the athlete
    with no threshold at all.

    Attributes:
        acute_days: Fatigue time constant in days.
        chronic_days: Fitness time constant in days.
        primary_method: Formula used when a single load is asked for.
        fit_from_s: Shortest peak duration for the critical-power fit, or ``None``
            to fall back to the module default.
        fit_to_s: Longest peak duration for the critical-power fit, or ``None``
            to fall back to the module default.
        resting_hr: Resting heart rate in bpm, or ``None`` to remove it (which
            turns the heart-rate impulse formulas off).
        hr_threshold_bpm: Manual threshold heart rate in bpm, or ``None`` to go back
            to the derived one.
        power_threshold_w: Manual threshold power per sport group, or ``None`` per
            sport to go back to the derived one.
        method_params: Whole replacement of the per-formula parameters.
    """

    model_config = ConfigDict(extra="forbid")

    acute_days: StrictInt | None = Field(default=None, ge=1, le=180)
    chronic_days: StrictInt | None = Field(default=None, ge=1, le=365)
    primary_method: analysis_constants.LoadMethod | None = None
    fit_from_s: StrictInt | None = Field(default=None, ge=10, le=7200)
    fit_to_s: StrictInt | None = Field(default=None, ge=30, le=21600)
    resting_hr: StrictInt | None = Field(default=None, ge=20, le=120)
    hr_threshold_bpm: StrictFloat | None = Field(default=None, ge=60, le=230)
    power_threshold_w: Mapping[StrictStr, StrictFloat | None] | None = None
    method_params: Mapping[StrictStr, StrictFloat] | None = None


class FitnessPointRead(BaseModel):
    """Serialized one-day point of the fitness/fatigue series.

    Attributes:
        date: The calendar day (the athlete's local day the activity started on).
        load: Summed load of that day, in the shared scale.
        acute: Fatigue: the exponentially weighted load with the acute time constant.
        chronic: Fitness: the same with the chronic time constant.
        balance: Form minus fatigue (fitness - fatigue), positive means fresh.
    """

    model_config = ConfigDict(extra="forbid")

    date: date_type
    load: StrictFloat
    acute: StrictFloat
    chronic: StrictFloat
    balance: StrictFloat


class FitnessSeriesRead(BaseModel):
    """Serialized daily-load series with its parameters, so a chart can label itself.

    Attributes:
        method: Which formula's rows were summed.
        acute_days: Fatigue time constant used.
        chronic_days: Fitness time constant used.
        sport: Sport group the series is limited to, ``None`` for all sports.
        points: One point per consecutive day of the requested range.
    """

    model_config = ConfigDict(extra="forbid")

    method: analysis_constants.LoadMethod
    acute_days: StrictInt
    chronic_days: StrictInt
    sport: StrictStr | None
    points: list[FitnessPointRead]


class DurationPeakRead(BaseModel):
    """Serialized one point of the duration-intensity curve.

    Attributes:
        duration_s: Duration the best average was held for, in seconds.
        value: Best sustained average at that duration, in watts.
        activity_id: Activity holding that best average, when recorded.
    """

    model_config = ConfigDict(extra="forbid")

    duration_s: StrictInt
    value: StrictFloat
    activity_id: StrictInt | None


class PowerDurationFitRead(BaseModel):
    """Serialized hyperbolic fit of the curve, as recomputed for the request.

    Attributes:
        critical_power_w: Fitted asymptote of the curve.
        w_prime_j: Fitted anaerobic work capacity, an index tied to the window used.
        cv_pct: Fit error as a percentage of the mean.
        fit_from_s: Shortest duration included in this fit.
        fit_to_s: Longest duration included in this fit.
    """

    model_config = ConfigDict(extra="forbid")

    critical_power_w: StrictFloat
    w_prime_j: StrictFloat
    cv_pct: StrictFloat
    fit_from_s: StrictInt
    fit_to_s: StrictInt


class PowerDurationSessionRead(BaseModel):
    """Serialized one activity whose samples feed the curve.

    Attributes:
        activity_id: The activity.
        name: Its name, for a picker label.
        start_time: When it started, ISO-8601 with offset.
    """

    model_config = ConfigDict(extra="forbid")

    activity_id: StrictInt
    name: StrictStr
    start_time: StrictStr | None


class PowerDurationRead(BaseModel):
    """Serialized duration-intensity curve of one athlete and sport.

    Attributes:
        sport: The sport group the curve belongs to.
        points: Cached best sustained averages, shortest duration first.
        fit: The hyperbolic fit recomputed with the effective window, or ``None``
            when the points cannot define one.
        threshold: The power threshold the loads of this sport are measured
            against, or ``None`` when none is stored yet.
        sessions: Activities with power in this sport, newest first, so the
            client can pick one to compute W' balance against.
    """

    model_config = ConfigDict(extra="forbid")

    sport: StrictStr
    points: list[DurationPeakRead]
    fit: PowerDurationFitRead | None
    threshold: PowerThresholdRead | None
    sessions: list[PowerDurationSessionRead]


class WprimePointRead(BaseModel):
    """Serialized one sample of a W' balance series.

    Attributes:
        t_s: Elapsed seconds since the activity started.
        remaining_j: W' remaining at that moment, in joules.
    """

    model_config = ConfigDict(extra="forbid")

    t_s: StrictFloat
    remaining_j: StrictFloat


class WprimeBalanceRead(BaseModel):
    """Serialized W' expenditure and balance of one activity.

    Carries the anaerobic totals and the downsampled balance curve. An activity
    without power, or whose sport has no (or a capacity-less) threshold, answers
    empty ``points`` with nulls rather than inventing a capacity.

    Attributes:
        cp_w: Critical power the balance was computed against.
        w_prime_j: Total capacity the balance starts from.
        tau_s: Repletion time constant used, from the stream's below-CP average.
        work_above_cp_j: Total work done above CP, in joules.
        time_above_cp_s: Total time spent above CP, in seconds.
        points: Balance series, evenly thinned for transfer.
    """

    model_config = ConfigDict(extra="forbid")

    cp_w: StrictFloat | None
    w_prime_j: StrictFloat | None
    tau_s: StrictFloat | None
    work_above_cp_j: StrictFloat
    time_above_cp_s: StrictFloat
    points: list[WprimePointRead]


class IntervalSegmentRead(BaseModel):
    """One stretch of an activity's timeline, as the interval analysis read it.

    Attributes:
        kind: What the stretch was (interval, climb, race, recovery, easy).
        start_s: Seconds from the start of the sampled data.
        duration_s: Length of the stretch.
        distance_m: Ground covered in it (0 without a speed stream).
        avg_power_w: Mean power over the stretch.
        intensity_factor: Mean power over the athlete's threshold for the sport.
        contrast: Mean power over the session's own median, the relative anchor.
        avg_grade_pct: Mean grade, ``None`` when no track was recorded.
        avg_speed_ms: Mean speed, ``None`` when the activity carries none.
    """

    model_config = ConfigDict(extra="forbid")

    kind: analysis_intervals.SegmentKind
    start_s: StrictFloat
    duration_s: StrictFloat
    distance_m: StrictFloat
    avg_power_w: StrictFloat
    intensity_factor: StrictFloat
    contrast: StrictFloat
    avg_grade_pct: StrictFloat | None
    avg_speed_ms: StrictFloat | None


class IntervalRulesRead(BaseModel):
    """The effective numeric gates the split used for one session.

    Exposed so an athlete can read back why a stretch was called what it was: every
    value is in this athlete's own terms (their threshold, this session's median).

    Attributes:
        open_w: Smoothed power at which an effort begins.
        close_w: Smoothed power at which it ends.
        min_segment_s: Shortest stretch that counts at all.
        short_segment_s: Under this length, the short-effort contrast rule applies.
        short_base: Contrast a short effort needs over the session median.
        strong_base: Contrast needed when the gap before was not a real recovery.
        recovery_w: At or below this, a gap counts as a recovery.
        effort_if: Average intensity a flat effort must hold to be an interval.
        climb_grade_pct: Mean grade above which terrain explains the effort.
        climb_if: Above this intensity even a graded effort is a rep, not a climb.
    """

    model_config = ConfigDict(extra="forbid")

    open_w: StrictFloat
    close_w: StrictFloat
    min_segment_s: StrictFloat
    short_segment_s: StrictFloat
    short_base: StrictFloat
    strong_base: StrictFloat
    recovery_w: StrictFloat
    effort_if: StrictFloat
    climb_grade_pct: StrictFloat
    climb_if: StrictFloat


class ActivityIntervalsRead(BaseModel):
    """How one activity breaks down into efforts, for its owner.

    An activity with no power stream answers ``steady`` with no segments: the split is
    read from power, and nothing is guessed from heart rate alone.

    Attributes:
        shape: How the session as a whole reads (intervals, continuous, terrain...).
        threshold_w: The threshold the intensities were divided by, if the athlete has one.
        base_power_w: The session's own median power, the relative anchor used.
        segments: Contiguous segments covering the timeline, in order.
        rules: The effective numeric gates used, ``None`` when nothing was segmented.
    """

    model_config = ConfigDict(extra="forbid")

    shape: analysis_intervals.SessionShape
    threshold_w: StrictFloat | None
    base_power_w: StrictFloat
    segments: list[IntervalSegmentRead]
    rules: IntervalRulesRead | None = None


class RecomputeQueued(BaseModel):
    """Response of a recompute request.

    Attributes:
        queued: Always ``True``: the pass runs off the request path.
    """

    model_config = ConfigDict(extra="forbid")

    queued: bool
