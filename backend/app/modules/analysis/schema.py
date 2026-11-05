"""Pydantic schemas for training load, thresholds and analysis settings."""

from collections.abc import Mapping
from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

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


class ActivityLoadsSummaryRead(BaseModel):
    """One activity's loads plus its power provenance, for list rendering.

    Attributes:
        activity_id: The activity measured.
        power_recalibrated: Whether its power stream is Stryd-era, so the shown
            watts are recalibrated to the athlete's current scale.
        loads: Every formula's load, in formula order; empty when unscored.
    """

    model_config = ConfigDict(extra="forbid")

    activity_id: StrictInt
    power_recalibrated: StrictBool
    loads: list[ActivityLoadRead]


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


class SpeedThresholdRead(BaseModel):
    """Serialized threshold running speed (critical speed).

    Attributes:
        value_ms: Critical speed in metres per second: the fastest pace that can be
            held indefinitely on the flat.
        w_prime_m: Distance capacity above the critical speed, in metres: how far
            faster than critical a short burst carries (the W' of the speed model).
        source: Whether the value was derived from the athlete's records or stated
            by them.
        cv_pct: Fit error as a percentage of the mean, only for derived values.
    """

    model_config = ConfigDict(extra="forbid")

    value_ms: StrictFloat
    w_prime_m: StrictFloat | None = None
    source: analysis_constants.ThresholdSource
    cv_pct: StrictFloat | None = None


class PaceCalibrationRead(BaseModel):
    """Linear map from grade-adjusted pace to one athlete's own watts.

    The pace side of the intensity chain normally reads pace against critical
    speed; when the athlete's own records pair power and GPS on the same runs,
    a least-squares line between adjusted pace and watts lets the pace source
    score on the watts scale instead - which is what makes a pace estimate
    comparable with the powered sessions beside it in the benchmark.

    Attributes:
        slope_w_per_ms: Watts gained per metre/second of adjusted pace.
        intercept_w: Watts the line assigns to standing still; an artefact of the
            fit, only the line over the athlete's real pace range is meaningful.
        grade_w_per_ms_pct: Watts added per (metre/second x grade percent) of the
            sample's own grade, clipped to the grade band the GAP model is trusted
            in. It carries the effort the steady-state cost model misses on moderate
            climbs and the coasting it over-reads on descents; on flat ground it
            changes nothing. ``0.0`` means no term (maps stored before it existed
            keep scoring exactly as before).
        r2: Squared correlation over the paired samples; low values mean the
            athlete's pace explains little of their power.
        source: Whether the map was derived from records or stated by the athlete.
    """

    model_config = ConfigDict(extra="forbid")

    slope_w_per_ms: StrictFloat
    intercept_w: StrictFloat
    grade_w_per_ms_pct: StrictFloat = 0.0
    r2: StrictFloat | None = None
    source: analysis_constants.ThresholdSource


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
        speed_thresholds: Threshold running speed per sport group, derived or manual:
            the pace-side twin of the power thresholds, used wherever intensity is
            read from ground speed instead of watts.
        hr_threshold: Threshold heart rate, derived or manual.
        pace_calibrations: Per-sport pace-to-watts maps fitted over the athlete's
            own paired power/GPS records; ``None``-absent when they have none.
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
    speed_thresholds: dict[StrictStr, SpeedThresholdRead] = Field(default_factory=dict)
    hr_threshold: HeartRateThresholdRead | None = None
    pace_calibrations: dict[StrictStr, PaceCalibrationRead] = Field(default_factory=dict)
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
        speed_threshold_ms: Manual threshold running speed per sport group, or
            ``None`` per sport to fall back to the derived one.
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
    speed_threshold_ms: Mapping[StrictStr, StrictFloat | None] | None = None
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


class PowerDurationOverlayRead(BaseModel):
    """One rolling-window curve: the athlete's recent months against the record.

    Attributes:
        months: The window's length, months back from today.
        points: Best sustained averages inside the window, shortest first.
        fit: The hyperbolic fit of this window's curve, when it has enough points;
            its own CP is what shows how the threshold has moved.
    """

    model_config = ConfigDict(extra="forbid")

    months: StrictInt
    points: list[DurationPeakRead]
    fit: PowerDurationFitRead | None


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
        overlays: Curves recomputed from rolling recent windows, so the athlete
            can watch where their form is going, not only where it peaked.
    """

    model_config = ConfigDict(extra="forbid")

    sport: StrictStr
    points: list[DurationPeakRead]
    fit: PowerDurationFitRead | None
    threshold: PowerThresholdRead | None
    sessions: list[PowerDurationSessionRead]
    overlays: list[PowerDurationOverlayRead] = []


class CpTrendPointRead(BaseModel):
    """Serialized one session's fitted critical power.

    Attributes:
        activity_id: The session the fit came from.
        date: When the session started (server date, for chart positioning).
        cp_w: Fitted critical power from this session alone, in watts.
        w_prime_j: Fitted anaerobic work capacity in joules, an index tied to
            the duration window the fit used.
        cv_pct: Fit error as a percentage of the mean.
        power_source: Where the session's watts came from (``stryd``,
            ``garmin_native``, ...), or ``None`` when untagged.
    """

    model_config = ConfigDict(extra="forbid")

    activity_id: StrictInt
    date: date_type
    cp_w: StrictFloat
    w_prime_j: StrictFloat
    cv_pct: StrictFloat
    power_source: StrictStr | None


class TrailPointRead(BaseModel):
    """Serialized one point of a technical section's polyline.

    Attributes:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
    """

    model_config = ConfigDict(extra="forbid")

    lat: StrictFloat
    lon: StrictFloat


class TrailOsmRead(BaseModel):
    """Serialized what OpenStreetMap claims about the ground a stretch lies on.

    Reported exactly as the map states it, never blended with the measured index:
    the two answers disagreeing is information, and collapsing them would throw
    the disagreement away. Absent attributes are absent from the map, not zero.

    Attributes:
        way_id: The OpenStreetMap way id.
        name: Way name, when mapped.
        sac_scale: Hiking difficulty (T0-T6), when mapped.
        mtb_scale: IMBA-style difficulty (0-5), when mapped.
        surface: What the ground is made of, when mapped.
        smoothness: How rough the ride reads, when mapped.
        tracktype: How drivable the track is, when mapped.
        trail_visibility: Whether the path can be found, when mapped.
        width: Way width, when mapped.
        incline: How the way itself is signed to climb, when mapped.
        start_m: Offset along the way where the stretch starts.
        end_m: Offset along the way where it ends.
        covered_m: Ground of the stretch the way accounts for.
        cache_date: Day of the extract this was read from.
    """

    model_config = ConfigDict(extra="forbid")

    way_id: StrictInt
    name: StrictStr | None = None
    sac_scale: StrictStr | None = None
    mtb_scale: StrictStr | None = None
    surface: StrictStr | None = None
    smoothness: StrictStr | None = None
    tracktype: StrictStr | None = None
    trail_visibility: StrictStr | None = None
    width: StrictStr | None = None
    incline: StrictStr | None = None
    start_m: StrictFloat
    end_m: StrictFloat
    covered_m: StrictFloat
    cache_date: str


class TrailSectionRead(BaseModel):
    """Serialized one technical stretch of a trail run.

    Attributes:
        start_m: Ground position where it starts, from the start of the data.
        length_m: Ground covered by the stretch.
        grade_pct: Mean grade, in percent.
        avg_speed_ms: Mean ground speed through it.
        gap_deficit: Shortfall against the pace the grade explains (0-1).
        cadence_jitter: Cadence instability over it (0-1).
        brake_score: Downhill braking share over it (0-1).
        score: Peak smoothed technicality index inside it.
        direction: ``descent`` / ``ascent`` / ``flat`` by mean grade.
        points: The section's own polyline, for map overlays.
        osm: What the cartography says about this ground, when it says anything.
    """

    model_config = ConfigDict(extra="forbid")

    start_m: StrictFloat
    length_m: StrictFloat
    grade_pct: StrictFloat
    avg_speed_ms: StrictFloat
    gap_deficit: StrictFloat
    cadence_jitter: StrictFloat
    brake_score: StrictFloat
    score: StrictFloat
    direction: StrictStr
    points: list[TrailPointRead]
    osm: TrailOsmRead | None = None


class TrailSectionsRead(BaseModel):
    """Serialized technical-terrain pass over one activity (experimental).

    Attributes:
        activity_id: The activity analysed.
        v_base_ms: The session's own flat-ground reference pace.
        threshold: The index cut the detector used, so a view can grade sections
            against the model's own number instead of inventing one.
        cadence_available: Whether the stride/cadence terms could be used.
        technical_m: Total metres flagged technical.
        total_m: Total ground covered.
        sections: The technical stretches, in ground order.
    """

    model_config = ConfigDict(extra="forbid")

    activity_id: StrictInt
    threshold: StrictFloat
    v_base_ms: StrictFloat
    cadence_available: StrictBool
    technical_m: StrictFloat
    total_m: StrictFloat
    sections: list[TrailSectionRead]


class PowerFlagRead(BaseModel):
    """Serialized one session held out of the duration-intensity curve.

    Attributes:
        activity_id: The refused session.
        started_on: Day of the session.
        duration_s: Duration of the strongest implausible average, in seconds.
        value_w: The refused best average, in watts.
        session_np_w: The session's own normalized power, in watts.
        ratio: ``value_w / session_np_w``.
        cap: The tier ratio the session exceeded.
    """

    model_config = ConfigDict(extra="forbid")

    activity_id: StrictInt
    started_on: date_type
    duration_s: StrictInt
    value_w: StrictFloat
    session_np_w: StrictFloat
    ratio: StrictFloat
    cap: StrictFloat


class CpTrendEnvelopeRead(BaseModel):
    """One sample of the evolving threshold line: decayed best evidence per day.

    Attributes:
        date: Day the threshold is answered for (samples are evenly spaced).
        cp_w: Threshold in watts supported by the decayed per-session fits.
    """

    model_config = ConfigDict(extra="forbid")

    date: date_type
    cp_w: StrictFloat


class CpTrendRead(BaseModel):
    """Serialized critical-power evolution of one athlete and sport.

    Attributes:
        sport: The sport group the fits belong to.
        window_days: Days of history covered, or ``None`` for all of it.
        measure: What the points and envelope are in: ``power`` (watts) or
            ``speed`` (metres per second, the no-power answer for running).
        threshold: The power threshold the loads of this sport are currently
            measured against, as the reference line, or ``None`` when unset.
        speed_threshold: The speed threshold, in the same role for a speed
            trend, or ``None`` when unset.
        points: Per-session CP fits, oldest first.
        envelope: The temporal threshold line - what scoring actually uses per
            day - sampled on the points' own days.
    """

    model_config = ConfigDict(extra="forbid")

    sport: StrictStr
    window_days: StrictInt | None
    measure: StrictStr
    threshold: PowerThresholdRead | None
    speed_threshold: SpeedThresholdRead | None = None
    points: list[CpTrendPointRead]
    envelope: list[CpTrendEnvelopeRead] = []


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
        elevation_gain_m: Ground risen through, ``None`` without an altitude stream.
        elevation_loss_m: Ground fallen through, ``None`` without an altitude stream.
        rss: Shared-scale aRSS score of the stretch (same kernel as the daily
            rss_total), ``None`` when the sport has no threshold.
        rss_per_min: That score per minute of the stretch, the mean stress rate;
            ``None`` when there is no score.
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
    pace_adjusted_ms: StrictFloat | None = None
    elevation_gain_m: StrictFloat | None = None
    elevation_loss_m: StrictFloat | None = None
    rss: StrictFloat | None = None
    rss_per_min: StrictFloat | None = None


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
        tempo_open_w: Smoothed power at which a sub-threshold block starts counting
            as tempo (the interval's own closing gate).
        tempo_min_s: Shortest sustained stretch the tempo gate keeps.
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
    tempo_open_w: StrictFloat
    tempo_min_s: StrictFloat
    climb_grade_pct: StrictFloat
    climb_if: StrictFloat


class TerrainClimbRead(BaseModel):
    """One climb the route itself shows, read from the terrain not the power signal.

    Attributes:
        start_m: Ground position where the climb starts, from the start of the data.
        end_m: Ground position where it ends.
        length_m: Ground climbed over.
        start_s: Seconds from the start of the sampled data.
        duration_s: Time spent on the climb.
        elevation_gain_m: Ground risen through.
        elevation_loss_m: Ground let go inside the climb (dips that did not break it).
        avg_grade_pct: Net grade over the whole climb.
        avg_power_w: Mean power, ``None`` without a power stream.
        intensity_factor: Mean power over the threshold, ``None`` without either.
        contrast: Mean power over the session's median, ``None`` without either.
        rss: Shared-scale aRSS score, from the intensity chain (watts, adjusted
            pace, heart rate); ``None`` when nothing scored.
        rss_source: Which signal scored it (``power``, ``pace``, ``hr``).
        rss_per_min: That score per minute of climbing, the mean stress rate.
        source_scores: The climb scored by every available signal, for the
            side-by-side benchmark.
    """

    model_config = ConfigDict(extra="forbid")

    start_m: StrictFloat
    end_m: StrictFloat
    length_m: StrictFloat
    start_s: StrictFloat
    duration_s: StrictFloat
    elevation_gain_m: StrictFloat
    elevation_loss_m: StrictFloat
    avg_grade_pct: StrictFloat
    avg_power_w: StrictFloat | None
    intensity_factor: StrictFloat | None
    contrast: StrictFloat | None
    rss: StrictFloat | None
    rss_source: StrictStr | None = None
    rss_per_min: StrictFloat | None
    rap_ms: StrictFloat | None = None
    source_scores: dict[StrictStr, StrictFloat] | None = None


class RssSourceTotalsRead(BaseModel):
    """One signal's band scores over a whole activity (the source benchmark).

    Attributes:
        source: The signal the activity was scored with (``power``, ``pace``,
            ``hr``).
        total: The general exponential stress over every measured second.
        up: The share taken on climbs.
        high: The share taken above the high band edge, off climbs.
        mid: The share taken between the band edges, off climbs.
        low: The share taken slow and below the low band edge, off climbs.
    """

    model_config = ConfigDict(extra="forbid")

    source: StrictStr
    total: StrictFloat | None = None
    up: StrictFloat | None = None
    high: StrictFloat | None = None
    mid: StrictFloat | None = None
    low: StrictFloat | None = None


class ActivityIntervalsRead(BaseModel):
    """How one activity breaks down into efforts, for its owner.

    An activity with no power stream answers ``steady`` with no segments: the split
    is read from power, and nothing is guessed from heart rate alone. The terrain
    climbs still answer in that case - they come from altitude and speed - only
    their power and stress columns stay empty.

    Attributes:
        shape: How the session as a whole reads (intervals, continuous, terrain...).
        threshold_w: The threshold the intensities were divided by, if the athlete has one.
        base_power_w: The session's own median power, the relative anchor used.
        segments: Contiguous segments covering the timeline, in order.
        rules: The effective numeric gates used, ``None`` when nothing was segmented.
        climbs: Climb stretches the route shows, in order; independent of the segments
            and possibly overlapping them, so never summed with them.
    """

    model_config = ConfigDict(extra="forbid")

    shape: analysis_intervals.SessionShape
    threshold_w: StrictFloat | None
    base_power_w: StrictFloat
    segments: list[IntervalSegmentRead]
    rules: IntervalRulesRead | None = None
    climbs: list[TerrainClimbRead] = []
    rss_by_source: list[RssSourceTotalsRead] = []


class BestTimeRead(BaseModel):
    """Serialized best time the athlete has run over one race distance.

    Attributes:
        distance_m: Distance the split covered, in metres.
        time_s: Time it took, in seconds.
        avg_power_w: Mean power across the split, in watts.
        activity_id: Activity in which the split was run.
        start_time: Start of that activity, so the record can link to its day.
    """

    model_config = ConfigDict(extra="forbid")

    distance_m: StrictInt
    time_s: StrictFloat
    avg_power_w: StrictFloat | None
    activity_id: StrictInt
    start_time: StrictStr | None


class BestTimesRead(BaseModel):
    """Serialized table of one athlete's best splits over a set of distances.

    Attributes:
        sport: The sport group read.
        months: The window applied, months back from today; ``None`` for all history.
        records: Best split per distance achieved, shortest first; a distance with no
            valid split (too long, or only ever run downhill) simply does not appear.
    """

    model_config = ConfigDict(extra="forbid")

    sport: StrictStr
    months: StrictInt | None
    records: list[BestTimeRead]


class LapDynamicsRead(BaseModel):
    """Serialized per-lap dynamics, computed from the lap's own recorded numbers.

    Cadence and power are what the device reported; stride length is derived
    (speed over cadence, falling back to distance over cycles), the ascent and the
    descent are reported as a pair so a rolling lap reads honestly, and the aRSS is
    the daily-load kernel summed over the lap's window - the rows add back up to the
    activity's total.

    Attributes:
        lap_id: The lap these dynamics belong to.
        avg_power_w: Mean power over the lap, when the device recorded one.
        stride_length_m: Ground covered per cycle, when cadence or cycles exist.
        elevation_gain_m: Ascent the device recorded for the lap.
        elevation_loss_m: Descent the device recorded for the lap.
        rss: Shared-scale aRSS score of the lap's window, from the intensity chain
            (watts, grade-adjusted pace, heart rate); ``None`` when nothing scored.
        rss_source: Which signal scored it (``power``, ``pace``, ``hr``).
        source_scores: The lap scored by every available signal, for the
            side-by-side benchmark.
        rss_per_min: That score per minute of the lap, the mean stress rate;
            ``None`` when there is no score.
        rap_ms: Grade-equivalent pace of the lap (the GAP/RAP the pace chain
            scores), ``None`` when no critical speed exists for the sport.
    """

    model_config = ConfigDict(extra="forbid")

    lap_id: StrictInt
    avg_power_w: StrictFloat | None
    stride_length_m: StrictFloat | None
    elevation_gain_m: StrictFloat | None = None
    elevation_loss_m: StrictFloat | None = None
    rss: StrictFloat | None = None
    rss_source: StrictStr | None = None
    rss_per_min: StrictFloat | None = None
    rap_ms: StrictFloat | None = None
    source_scores: dict[StrictStr, StrictFloat] | None = None


class RecomputeQueued(BaseModel):
    """Response of a recompute request.

    Attributes:
        queued: Always ``True``: the pass runs off the request path.
    """

    model_config = ConfigDict(extra="forbid")

    queued: bool
