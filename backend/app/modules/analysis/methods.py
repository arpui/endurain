"""The load formulas the analysis module can compute, kept as a registry.

Every formula answers the same question -- how much stress did this activity put on
this athlete -- and every one except :meth:`LoadMethod.TRIMP` answers it on the same
scale: **100 units is one hour held at the athlete's threshold**
(``100 · hours · intensity²``). Holding that scale is what lets a day's load add up
across running, hiking and cycling, and what makes a fitness/fatigue series built on
top of it mean anything.

An athlete's own formula joins :data:`LOAD_FORMULAS` with its calculator and nothing
else in the module changes: it receives the activity's samples and thresholds through
:data:`LoadInput`, its knobs arrive in
:attr:`LoadInput.parameters` from the athlete's settings, and its result is stored in
one :class:`ActivityLoad` row like any other.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from math import exp
from statistics import mean as arithmetic_mean

import modules.analysis.constants as analysis_constants
import modules.analysis.maths as analysis_maths


@dataclass(frozen=True)
class LoadInput:
    """What a formula is given about one activity.

    Attributes:
        moving_seconds: Active time of the activity, the duration the load scales with.
        power_w: Power samples in watts in stream order (stops kept as zero), or
            ``None`` when the activity carries no power.
        period_s: Seconds between power samples.
        stored_np_w: Normalized Power as the ingestion stored it, preferred over
            recomputing it when present.
        average_hr: Average heart rate of the activity, or ``None``.
        max_hr: Highest heart rate seen in the activity, the athlete's value when the
            activity has none.
        speed_ms: Moving speed in metres per second, sample-aligned to
            :attr:`power_w`, or ``None`` when the activity carries no speed stream.
        grade_pct: Grade in percent, sample-aligned to :attr:`power_w`, or ``None``
            when it cannot be derived from the recorded track.
        power_threshold_w: Threshold power for the activity's sport, or ``None``.
        hr_threshold_bpm: Threshold heart rate, or ``None``.
        resting_hr_bpm: Resting heart rate, or ``None`` when unknown.
        parameters: Formula-specific knobs from the athlete's settings.
    """

    moving_seconds: float
    power_w: tuple[float, ...] | None = None
    period_s: float = 1.0
    stored_np_w: float | None = None
    average_hr: float | None = None
    max_hr: float | None = None
    hr_samples: tuple[float, ...] = ()
    speed_ms: tuple[float, ...] | None = None
    grade_pct: tuple[float, ...] | None = None
    power_threshold_w: float | None = None
    speed_threshold_ms: float | None = None
    pace_calibration: tuple[float, float] | None = None
    hr_threshold_bpm: float | None = None
    resting_hr_bpm: float | None = None
    parameters: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadValue:
    """What a formula returns: the load plus what it was measured against.

    Attributes:
        load: Stress in the shared scale (100 = one hour at threshold).
        intensity_factor: Intensity the load was derived from, when the formula has one.
        threshold_used: Threshold value the formula divided by, so a later change of
            threshold never silently reinterprets a stored row.
    """

    load: float
    intensity_factor: float | None = None
    threshold_used: float | None = None


def stress_from_intensity(intensity_factor: float, seconds: float) -> float:
    """Return the shared-scale load for an intensity factor held for ``seconds``.

    ``100 · hours · IF²``: one hour at threshold is 100, two hours at threshold is
    200, one hour at 80 % of threshold is 64.
    """
    return 100.0 * (seconds / 3600.0) * intensity_factor**2


def _normalized_power_w(data: LoadInput) -> float:
    """Return the activity's Normalized Power, preferring what ingestion stored."""
    if data.stored_np_w and data.stored_np_w > 0:
        return float(data.stored_np_w)
    if not data.power_w:
        return 0.0
    return analysis_maths.normalized_power(data.power_w, data.period_s)


def coggan_tss(data: LoadInput) -> LoadValue | None:
    """Training Stress Score: Normalized Power over the threshold power.

    Returns ``None`` when either number is missing, so a power formula never
    guesses a threshold.
    """
    threshold = data.power_threshold_w
    normalized = _normalized_power_w(data)
    if not threshold or threshold <= 0 or not normalized or data.moving_seconds <= 0:
        return None
    intensity = normalized / threshold
    return LoadValue(
        load=stress_from_intensity(intensity, data.moving_seconds),
        intensity_factor=intensity,
        threshold_used=float(threshold),
    )


def skiba_score(data: LoadInput) -> LoadValue | None:
    """Stress score from xPower, which reacts sooner to bursts than Normalized Power.

    Note on running power sampled at one second: the attenuated average this uses
    smooths *more* than the 30-second boxcar, so on that data it scores slightly
    below the Coggan score rather than above it. It is kept because it is the
    standard alternative for variable cycling efforts, not as the anaerobic
    indicator.
    """
    threshold = data.power_threshold_w
    if not threshold or threshold <= 0 or not data.power_w or data.moving_seconds <= 0:
        return None
    burst_weighted = analysis_maths.xpower(data.power_w, data.period_s)
    if not burst_weighted:
        return None
    intensity = burst_weighted / threshold
    return LoadValue(
        load=stress_from_intensity(intensity, data.moving_seconds),
        intensity_factor=intensity,
        threshold_used=float(threshold),
    )


def hr_load(data: LoadInput) -> LoadValue | None:
    """Load from average heart rate over the threshold heart rate.

    The same scale as the power formulas, which is what lets an activity with no
    power still add to the athlete's overall load.
    """
    threshold = data.hr_threshold_bpm
    if not threshold or threshold <= 0 or not data.average_hr or data.moving_seconds <= 0:
        return None
    intensity = data.average_hr / threshold
    return LoadValue(
        load=stress_from_intensity(intensity, data.moving_seconds),
        intensity_factor=intensity,
        threshold_used=float(threshold),
    )


def trimp(data: LoadInput) -> LoadValue | None:
    """Training impulse from heart-rate reserve (Banister).

    A different scale from the stress formulas, so it is reported beside them and
    never summed with them. It needs a resting heart rate; without one it returns
    ``None`` rather than assuming a value the athlete never gave.
    """
    max_hr, resting = data.max_hr, data.resting_hr_bpm
    if not data.average_hr or not max_hr or not resting or max_hr <= resting or data.moving_seconds <= 0:
        return None
    reserve = (data.average_hr - resting) / (max_hr - resting)
    if reserve <= 0:
        return None
    coefficient = float(data.parameters.get("trimp_coefficient", 1.92))
    load = (data.moving_seconds / 60.0) * reserve * 0.64 * exp(coefficient * reserve)
    return LoadValue(load=load, intensity_factor=reserve, threshold_used=float(resting))


# --- Exponential (RSS) stress family -------------------------------------------
#
# The athlete's own formulas, ported from the GoldenCheetah user metrics they fitted
# on running power (``aRSS`` and its four bands). One second at intensity ``x`` is
# worth ``B + A·e^(E·x)`` stress units, and an activity's load is that rate summed
# over its powered seconds, divided by ``RSS_NORM`` so an hour at threshold scores
# ~100 like every other method.
#
# The four bands split each second between exactly one area -- a climb second belongs
# to UP and to no intensity band, which is what keeps the bands from double counting
# a hill that is also hard -- while :attr:`LoadMethod.RSS_TOTAL` measures every
# second instead, so it carries the climb's intensity too and is deliberately not
# the sum of the bands. A day's band loads read as "where the stress came from";
# the general series reads as "how much".
#
# An activity with no power stream, or a sport with no power threshold, gets no RSS
# row at all: the family never estimates stress from something else. GoldenCheetah
# fell back to a heart-rate score for those activities; here they stay visible under
# :attr:`LoadMethod.HR_LOAD` and :attr:`LoadMethod.TRIMP` instead, and a series built
# on an RSS method simply leaves them out.


def _rss_kernel(intensity: float, parameters: Mapping[str, float]) -> float:
    """Return the fitted stress rate of one second at ``intensity`` over threshold."""
    return parameters.get("rss_kernel_b", analysis_constants.RSS_KERNEL_B) + parameters.get(
        "rss_kernel_a", analysis_constants.RSS_KERNEL_A
    ) * exp(parameters.get("rss_kernel_e", analysis_constants.RSS_KERNEL_E) * intensity)


def _rss_sample_band(
    intensity: float,
    speed_ms: float,
    grade_pct: float,
    parameters: Mapping[str, float],
) -> str:
    """Return which RSS band one powered second belongs to.

    The bands are a partition: every powered second lands in exactly one of them,
    which is what keeps a hill from being counted both as a climb and as an
    intensity band. A climb outranks the intensity bands; on flat ground a second
    is high above the high edge, low only when it is both below the low edge and
    slow, and middle otherwise (a fast low-power second is work, not recovery).

    Args:
        intensity: Power over the threshold power for this second.
        speed_ms: Moving speed in metres per second.
        grade_pct: Grade in percent.
        parameters: The athlete's overrides of the band edges.

    Returns:
        One of ``up``, ``high``, ``mid``, ``low``.
    """
    if grade_pct > parameters.get("rss_climb_grade_pct", analysis_constants.RSS_CLIMB_GRADE_PCT):
        return "up"
    if intensity > parameters.get("rss_high_fraction", analysis_constants.RSS_HIGH_FRACTION):
        return "high"
    if intensity <= parameters.get(
        "rss_low_fraction", analysis_constants.RSS_LOW_FRACTION
    ) and speed_ms < parameters.get("rss_slow_speed_ms", analysis_constants.RSS_SLOW_SPEED_MS):
        return "low"
    return "mid"


def _rss_lifted_kernel(
    kernel: float,
    grade_pct: float,
    soft_grade: float,
    soft_floor: float,
    climb_grade: float,
    climb_floor: float,
) -> float:
    """Raise one second's kernel by the grade floors it deserves (soft, then climb)."""
    if grade_pct > soft_grade:
        kernel = max(kernel, soft_floor)
    if grade_pct > climb_grade:
        kernel = max(kernel, climb_floor)
    return kernel


def _plausible_speeds(speeds_ms: Sequence[float]) -> tuple[float, ...]:
    """Cap raw GPS speeds at the plausibility ceiling the best-times gate uses.

    A running sensor invents the occasional 40 km/h second; divided by a critical
    speed of ~4 m/s that reads as a superhuman surge, and an exponential or
    fourth-power kernel turns one such second into a whole activity's stress.
    Everything above ``BT_MAX_SPEED_MS`` counts as that ceiling's worth of effort
    instead: the run happened, its pace credit stays bounded.
    """
    ceiling = analysis_constants.BT_MAX_SPEED_MS
    return tuple(min(value, ceiling) for value in speeds_ms)


def _pace_window_s(parameters: Mapping[str, float]) -> float:
    """Return the athlete's smoothing window for pace, bounded to something sane."""
    raw = parameters.get("pace_smooth_window_s", analysis_constants.PACE_SMOOTH_WINDOW_S)
    return min(max(float(raw), 5.0), 300.0)


def _paced_samples(
    adjusted_ms: Sequence[float],
    threshold_ms: float,
    period_s: float,
    parameters: Mapping[str, float],
) -> tuple[float, ...]:
    """Smooth and cap grade-adjusted pace to what the pace chain can vouch for.

    GPS speed cannot resolve one second of effort, and a convex kernel turns that
    jitter into a bonus an honest effort never earned: the series is averaged
    over the window pace actually speaks at (``pace_smooth_window_s``, default
    ``PACE_SMOOTH_WINDOW_S``) before it is scored. On top, garbage elevation
    feeding the GAP polynomial can inflate a plausible speed into an impossible
    flat-ground equivalent, so the intensity over critical speed - the number
    these methods actually divide - is bounded.
    """
    values = list(adjusted_ms)
    if period_s > 0:
        window = max(round(_pace_window_s(parameters) / period_s), 1)
        if window > 1 and len(values) >= window:
            half = window // 2
            padded = [values[0]] * half + values + [values[-1]] * (window - 1 - half)
            running = 0.0
            smoothed: list[float] = []
            for index, value in enumerate(padded):
                running += value
                if index >= window:
                    running -= padded[index - window]
                if index >= window - 1:
                    smoothed.append(running / window)
            values = smoothed
    cap = analysis_constants.PACE_INTENSITY_CEILING * threshold_ms
    return tuple(min(value, cap) for value in values)


def _pace_signal(
    speeds_ms: Sequence[float],
    period_s: float,
    speed_threshold_ms: float,
    grades_pct: Sequence[float] | None,
    power_threshold_w: float | None,
    calibration: tuple[float, float] | None,
    params: Mapping[str, float],
) -> tuple[list[float], float]:
    """Return the values and divisor the pace source scores.

    Normally the smoothed, capped grade-adjusted pace over the critical speed.
    When the athlete's own records have mapped pace onto watts (the refresh fit
    against paired power/GPS streams), the adjusted pace walks through that map
    and is divided by the critical power instead - the same effort read on the
    watts scale the power source uses, which is what makes the two comparable.
    """
    grades = grades_pct if grades_pct is not None else (0.0,) * len(speeds_ms)
    adjusted = _paced_samples(
        analysis_maths.grade_adjusted_run_speed(_plausible_speeds(speeds_ms), grades),
        float(speed_threshold_ms),
        period_s,
        params,
    )
    if calibration and power_threshold_w and power_threshold_w > 0:
        slope, intercept = calibration
        return [slope * value + intercept for value in adjusted], float(power_threshold_w)
    return list(adjusted), float(speed_threshold_ms)


def _window_stress_scaled(
    values: Sequence[float],
    divisor_signal: float,
    period_s: float,
    grade_pct: Sequence[float] | None,
    parameters: Mapping[str, float],
    *,
    lift_with_grade: bool,
) -> float:
    """Sum the kernel over one window, dividing each raw sample by its threshold.

    A sample at or below zero skipped: it is a stopped sensor second, not a zero
    effort. ``lift_with_grade`` applies the grade floors only where the signal
    itself misses the terrain: watts do, adjusted pace and heart rate do not.
    """
    if not values or divisor_signal <= 0 or period_s <= 0:
        return 0.0
    soft_grade = parameters.get("rss_soft_grade_pct", analysis_constants.RSS_SOFT_GRADE_PCT)
    soft_floor = parameters.get("rss_soft_floor", analysis_constants.RSS_SOFT_FLOOR)
    climb_grade = parameters.get("rss_climb_grade_pct", analysis_constants.RSS_CLIMB_GRADE_PCT)
    climb_floor = parameters.get("rss_climb_floor", analysis_constants.RSS_CLIMB_FLOOR)
    divisor = parameters.get("rss_norm", analysis_constants.RSS_NORM)
    total = 0.0
    for index, raw in enumerate(values):
        if raw <= 0:
            continue
        kernel = _rss_kernel(raw / divisor_signal, parameters)
        if lift_with_grade:
            grade = grade_pct[index] if grade_pct is not None and index < len(grade_pct) else 0.0
            kernel = _rss_lifted_kernel(kernel, grade, soft_grade, soft_floor, climb_grade, climb_floor)
        total += kernel
    return 100.0 * total * period_s / (divisor * 3600.0)


def rss_window_stress(
    power_w: Sequence[float],
    period_s: float,
    threshold_w: float | None,
    grade_pct: Sequence[float] | None = None,
    parameters: Mapping[str, float] | None = None,
) -> float:
    """Return the shared-scale aRSS score of any window of samples.

    The same kernel the daily ``rss_total`` sums, over an arbitrary slice: that is
    what makes the per-lap and per-interval scores add back up to the activity's
    load, which is how the tables can be cross-checked with the PMC.
    """
    if not threshold_w or threshold_w <= 0:
        return 0.0
    return _window_stress_scaled(
        power_w, float(threshold_w), period_s, grade_pct, parameters or {}, lift_with_grade=True
    )


def _power_window_score(
    power_w: Sequence[float],
    period_s: float,
    threshold_w: float,
    grades_pct: Sequence[float] | None,
    params: Mapping[str, float],
) -> float:
    """Score a window from watts alone: the only source needing the grade floors."""
    return _window_stress_scaled(power_w, float(threshold_w), period_s, grades_pct, params, lift_with_grade=True)


def _pace_window_score(
    speeds_ms: Sequence[float],
    period_s: float,
    speed_threshold_ms: float,
    grades_pct: Sequence[float] | None,
    params: Mapping[str, float],
    power_threshold_w: float | None = None,
    calibration: tuple[float, float] | None = None,
) -> float:
    """Score a window from grade-adjusted pace: GAP has already paid for the terrain."""
    values, divisor = _pace_signal(
        speeds_ms, period_s, speed_threshold_ms, grades_pct, power_threshold_w, calibration, params
    )
    return _window_stress_scaled(values, divisor, period_s, None, params, lift_with_grade=False)


def _hr_window_score(
    hr_bpm: Sequence[float],
    period_s: float,
    hr_threshold_bpm: float,
    params: Mapping[str, float],
) -> float:
    """Score a window from heart rate: the body already paid for the hill."""
    return _window_stress_scaled(hr_bpm, float(hr_threshold_bpm), period_s, None, params, lift_with_grade=False)


def window_stress_chain(
    power_w: Sequence[float] | None,
    speeds_ms: Sequence[float] | None,
    hr_bpm: Sequence[float] | None,
    grades_pct: Sequence[float] | None,
    period_s: float,
    threshold_w: float | None,
    speed_threshold_ms: float | None,
    hr_threshold_bpm: float | None,
    parameters: Mapping[str, float] | None = None,
    pace_calibration: tuple[float, float] | None = None,
) -> tuple[float, str | None]:
    """Return a window's shared-scale aRSS from the best signal the device carried.

    The sources follow one order everywhere it is asked: watts over threshold,
    then grade-adjusted pace over critical speed, then heart rate over threshold
    heart rate. Watts get the grade floors because power alone under-reads
    climbing; adjusted pace does not need them because GAP has already turned the
    terrain into equivalent flat speed; heart rate does not need them either
    because the body has already paid for the hill on a climb.

    Returns:
        The score and the source used (``power``, ``pace``, ``hr``), or ``(0.0,
        None)`` when no signal and threshold pair is available.
    """
    scores = window_scores_by_source(
        power_w,
        speeds_ms,
        hr_bpm,
        grades_pct,
        period_s,
        threshold_w,
        speed_threshold_ms,
        hr_threshold_bpm,
        parameters,
        pace_calibration,
    )
    for source in ("power", "pace", "hr"):
        if source in scores:
            return scores[source], source
    return 0.0, None


def window_scores_by_source(
    power_w: Sequence[float] | None,
    speeds_ms: Sequence[float] | None,
    hr_bpm: Sequence[float] | None,
    grades_pct: Sequence[float] | None,
    period_s: float,
    threshold_w: float | None,
    speed_threshold_ms: float | None,
    hr_threshold_bpm: float | None,
    parameters: Mapping[str, float] | None = None,
    pace_calibration: tuple[float, float] | None = None,
) -> dict[str, float]:
    """Score one window with *every* signal and threshold pair it can read.

    The comparison the chain implies but never shows: what the same seconds look
    like from watts, from adjusted pace and from heart rate at once, so an
    athlete can benchmark the estimates against the measured effort beside them.
    Sources the activity or the athlete cannot support stay absent.
    """
    params = parameters or {}
    scores: dict[str, float] = {}
    if period_s <= 0:
        return scores
    if power_w and threshold_w and threshold_w > 0:
        score = _power_window_score(power_w, period_s, float(threshold_w), grades_pct, params)
        if score > 0:
            scores["power"] = score
    if speeds_ms and speed_threshold_ms and speed_threshold_ms > 0:
        score = _pace_window_score(
            speeds_ms, period_s, float(speed_threshold_ms), grades_pct, params, threshold_w, pace_calibration
        )
        if score > 0:
            scores["pace"] = score
    if hr_bpm and hr_threshold_bpm and hr_threshold_bpm > 0:
        score = _hr_window_score(hr_bpm, period_s, float(hr_threshold_bpm), params)
        if score > 0:
            scores["hr"] = score
    return scores


def _rss_load(data: LoadInput, band: str, source: str | None = None) -> LoadValue | None:
    """Sum the exponential kernel over one activity, keeping only ``band``'s seconds.

    The intensity source follows the one chain: the power stream when the activity
    carries one, otherwise grade-adjusted pace over critical speed, otherwise the
    session's average heart rate over the threshold heart rate - the only heart
    rate these formulas have, as in ``hr_load``, so its seconds all read the same
    intensity. Pass ``source`` to force one signal instead, which is how the
    source-by-source benchmark scores the same activity three ways.

    Args:
        data: The activity's samples and thresholds.
        band: ``total``, ``up``, ``high``, ``mid`` or ``low``; ``total`` keeps every
            measured second instead of splitting them.
        source: Force ``power``, ``pace`` or ``hr``; ``None`` follows the chain.

    Returns:
        The load in the shared scale, or ``None`` when the source (or the chain)
        has no signal and threshold pair, or when no second fell in the band.
    """
    if data.moving_seconds <= 0:
        return None
    parameters = data.parameters
    speeds = data.speed_ms
    grades = data.grade_pct
    threshold = data.power_threshold_w
    soft_grade = parameters.get("rss_soft_grade_pct", analysis_constants.RSS_SOFT_GRADE_PCT)
    soft_floor = parameters.get("rss_soft_floor", analysis_constants.RSS_SOFT_FLOOR)
    climb_grade = parameters.get("rss_climb_grade_pct", analysis_constants.RSS_CLIMB_GRADE_PCT)
    climb_floor = parameters.get("rss_climb_floor", analysis_constants.RSS_CLIMB_FLOOR)
    divisor = parameters.get("rss_norm", analysis_constants.RSS_NORM)

    power_ok = bool(data.power_w) and bool(threshold) and threshold > 0
    pace_ok = bool(speeds) and bool(data.speed_threshold_ms) and data.speed_threshold_ms > 0
    hr_ok = bool(data.average_hr) and bool(data.hr_threshold_bpm) and data.hr_threshold_bpm > 0
    if source is None:
        chosen = "power" if power_ok else "pace" if pace_ok else "hr" if hr_ok else None
    elif source in ("power", "pace", "hr") and (
        power_ok if source == "power" else pace_ok if source == "pace" else hr_ok
    ):
        chosen = source
    else:
        return None
    if chosen is None:
        return None

    if chosen == "power":
        samples: Sequence[float] | None = data.power_w
        signal_divisor = float(threshold)
        lift_with_grade = True
    elif chosen == "pace":
        samples, signal_divisor = _pace_signal(
            speeds,
            data.period_s or 1.0,
            float(data.speed_threshold_ms),
            grades,
            float(threshold) if threshold and threshold > 0 else None,
            data.pace_calibration,
            parameters,
        )
        lift_with_grade = False
    else:
        samples = None
        signal_divisor = float(data.hr_threshold_bpm)
        lift_with_grade = False

    total = 0.0
    if samples is None:
        intensity = float(data.average_hr) / signal_divisor
        if band != "total" and _rss_sample_band(intensity, 0.0, 0.0, parameters) != band:
            return None
        total = _rss_kernel(intensity, parameters) * (data.moving_seconds / (data.period_s or 1.0))
        intensity_factor: float | None = intensity
    else:
        for index, raw in enumerate(samples):
            if raw <= 0:
                continue
            intensity = raw / signal_divisor
            grade = grades[index] if grades is not None and index < len(grades) else 0.0
            speed = speeds[index] if speeds is not None and index < len(speeds) else 0.0
            kernel = _rss_kernel(intensity, parameters)
            if band == "total":
                if lift_with_grade:
                    kernel = _rss_lifted_kernel(kernel, grade, soft_grade, soft_floor, climb_grade, climb_floor)
            else:
                if _rss_sample_band(intensity, speed, grade, parameters) != band:
                    continue
                if lift_with_grade:
                    if band == "up" or grade > climb_grade:
                        kernel = max(kernel, climb_floor)
                    elif band in ("low", "mid") and grade > soft_grade:
                        kernel = max(kernel, soft_floor)
            total += kernel
        positive = [value for value in samples if value > 0]
        intensity_factor = (arithmetic_mean(positive) / signal_divisor) if positive else None
    if total <= 0:
        return None
    return LoadValue(
        load=100.0 * total * (data.period_s or 1.0) / (divisor * 3600.0),
        intensity_factor=intensity_factor,
        threshold_used=signal_divisor,
    )


def rss_bands_by_source(data: LoadInput) -> dict[str, dict[str, float]]:
    """Return one activity's band loads scored by *each* signal it can support.

    The benchmark behind the source-comparison views: the same seconds read as
    watts, as adjusted pace and as heart rate, side by side.
    """
    by_source: dict[str, dict[str, float]] = {}
    for source in ("power", "pace", "hr"):
        bands: dict[str, float] = {}
        for band in ("total", "up", "high", "mid", "low"):
            value = _rss_load(data, band, source=source)
            if value is not None:
                bands[band] = value.load
        if bands:
            by_source[source] = bands
    return by_source


def rss_total(data: LoadInput) -> LoadValue | None:
    """Exponential stress over every powered second (the family's general load)."""
    return _rss_load(data, "total")


def rss_up(data: LoadInput) -> LoadValue | None:
    """The share of the stress taken on climbs."""
    return _rss_load(data, "up")


def rss_high(data: LoadInput) -> LoadValue | None:
    """The share taken above the high band edge, off climbs."""
    return _rss_load(data, "high")


def rss_mid(data: LoadInput) -> LoadValue | None:
    """The share taken between the band edges, off climbs."""
    return _rss_load(data, "mid")


def rss_low(data: LoadInput) -> LoadValue | None:
    """The share taken slow and below the low band edge, off climbs."""
    return _rss_load(data, "low")


# --- Daniels points ------------------------------------------------------------
#
# Jack Daniels' pace model, kept in the shape GoldenCheetah's metric gives it:
# one second at intensity ``x`` over threshold is worth ``100/3600 · x⁴``, so
# an hour at threshold scores 100 points. The fourth power is why this is a
# scale of its own: an hour at threshold reads 100 like every other method, but
# away from threshold the curve is far steeper than any of theirs, so Daniels
# numbers are reported beside the shared scale and never added to it, exactly
# like TRIMP's. The intensity source follows the chain only as far as pace -
# watts over critical power, grade-adjusted pace over critical speed - because
# Daniels never scored heart rate, and neither does this.


def daniels_points(data: LoadInput) -> LoadValue | None:
    """Daniels points over one activity: the fourth power of intensity, summed.

    Returns:
        The points, with the mean intensity they were built from and the
        threshold used, or ``None`` when neither watts nor adjusted pace and a
        matching threshold are available.
    """
    if data.moving_seconds <= 0:
        return None
    if data.power_w and data.power_threshold_w and data.power_threshold_w > 0:
        samples: Sequence[float] = data.power_w
        divisor = float(data.power_threshold_w)
    elif data.speed_ms and data.speed_threshold_ms and data.speed_threshold_ms > 0:
        grades = data.grade_pct if data.grade_pct is not None else (0.0,) * len(data.speed_ms)
        samples = _paced_samples(
            analysis_maths.grade_adjusted_run_speed(_plausible_speeds(data.speed_ms), grades),
            float(data.speed_threshold_ms),
            data.period_s or 1.0,
            data.parameters,
        )
        divisor = float(data.speed_threshold_ms)
    else:
        return None
    total = 0.0
    for raw in samples:
        if raw <= 0:
            continue
        total += (raw / divisor) ** 4
    if total <= 0:
        return None
    positive = [value for value in samples if value > 0]
    return LoadValue(
        load=100.0 * total * (data.period_s or 1.0) / 3600.0,
        intensity_factor=(arithmetic_mean(positive) / divisor) if positive else None,
        threshold_used=divisor,
    )


@dataclass(frozen=True)
class LoadFormula:
    """One entry of the registry.

    Attributes:
        method: Stored in ``analysis_activity_loads.method``; part of the row's identity.
        label: Human-readable name, for the settings screen and tooltips.
        calculator: The function computing the load, or ``None`` when the activity
            lacks what the formula needs.
    """

    method: analysis_constants.LoadMethod
    label: str
    calculator: Callable[[LoadInput], LoadValue | None]


#: Every formula the module computes, in the order they are stored and listed.
#: Adding a formula means adding a calculator above and one entry here.
LOAD_FORMULAS: tuple[LoadFormula, ...] = (
    LoadFormula(analysis_constants.LoadMethod.COGGAN_TSS, "Training Stress Score", coggan_tss),
    LoadFormula(analysis_constants.LoadMethod.SKIBA_SCORE, "Stress score (xPower)", skiba_score),
    LoadFormula(analysis_constants.LoadMethod.HR_LOAD, "Heart-rate load", hr_load),
    LoadFormula(analysis_constants.LoadMethod.TRIMP, "Training impulse (TRIMP)", trimp),
    LoadFormula(analysis_constants.LoadMethod.RSS_TOTAL, "Exponential stress (aRSS)", rss_total),
    LoadFormula(analysis_constants.LoadMethod.RSS_UP, "aRSS climbs", rss_up),
    LoadFormula(analysis_constants.LoadMethod.RSS_HIGH, "aRSS high band", rss_high),
    LoadFormula(analysis_constants.LoadMethod.RSS_MID, "aRSS middle band", rss_mid),
    LoadFormula(analysis_constants.LoadMethod.RSS_LOW, "aRSS low band", rss_low),
    LoadFormula(analysis_constants.LoadMethod.DANIELS, "Daniels points", daniels_points),
)


def formula_for(method: analysis_constants.LoadMethod | str) -> LoadFormula | None:
    """Return the registry entry for a method, or ``None`` when it is unknown."""
    for entry in LOAD_FORMULAS:
        if entry.method == method:
            return entry
    return None


def compute_all(data: LoadInput) -> list[tuple[analysis_constants.LoadMethod, LoadValue]]:
    """Run every registered formula and keep the ones this activity supports.

    Returns:
        ``(method, value)`` for each formula the activity's data allowed, in registry
        order; empty when the activity carries nothing any formula can read.
    """
    results: list[tuple[analysis_constants.LoadMethod, LoadValue]] = []
    for entry in LOAD_FORMULAS:
        value = entry.calculator(data)
        if value is not None:
            results.append((entry.method, value))
    return results
