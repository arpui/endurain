"""Pure arithmetic for training load: no database, no domain types, no I/O.

Each function takes sample values plus the seconds between them, so the arithmetic
is testable in isolation and the same code serves a one-hour ride and a five-second
sprint. The formulas follow the published definitions GoldenCheetah implements
(``Coggan.cpp``, ``BikeScore.cpp``, ``PDModel.cpp``); they are re-derived here rather
than copied, and the module's own docstrings state the definition each one uses so a
reviewer can check the arithmetic against the literature.

Sampling convention: power samples keep their zeros (a stopped athlete produces no
work, and that is what devices, Strava and the stored ``normalized_power`` column all
count), while callers drop heart-rate zeros first because they mean "no signal".
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from math import exp
from statistics import median

import numpy as np

import modules.analysis.constants as analysis_constants


def sample_period_s(timestamps: Sequence[datetime]) -> float:
    """Return the median seconds between samples, the period the windows are sized in.

    The median (not the mean) ignores the occasional dropped or duplicated timestamp,
    and a stream with no positive gap reports the default one-second period rather
    than zero, which would divide by zero downstream.

    Args:
        timestamps: Sample times in chronological order.

    Returns:
        Seconds between samples, clamped to :data:`constants.MAX_SAMPLE_PERIOD_S`.
    """
    if len(timestamps) < 2:
        return 1.0
    gaps = np.diff(np.array([stamp.timestamp() for stamp in timestamps], dtype=float))
    positive = gaps[gaps > 0]
    if positive.size == 0:
        return 1.0
    return float(min(np.median(positive), analysis_constants.MAX_SAMPLE_PERIOD_S))


def grade_pct_series(
    elevations_m: Sequence[float],
    speeds_ms: Sequence[float],
    period_s: float,
) -> tuple[float, ...]:
    """Return the grade in percent at each sample, from elevation over ground distance.

    The distance walked or ridden between two samples is the speed held over the
    sample period, so the grade is the elevation gain divided by it. A centered
    median over a handful of samples then damps the ±few-metre noise GPS elevation
    carries, which otherwise reads as a steep hill on flat ground.

    Args:
        elevations_m: Elevation in metres, one value per sample.
        speeds_ms: Moving speed in metres per second, one value per sample.
        period_s: Seconds between samples.

    Returns:
        One grade percentage per sample, clamped to ±100 %.
    """
    count = len(elevations_m)
    if count < 2 or period_s <= 0:
        return tuple(0.0 for _ in range(count))
    raw: list[float] = [0.0]
    for index in range(1, count):
        distance = speeds_ms[index] * period_s if index < len(speeds_ms) else 0.0
        if distance <= 0:
            raw.append(0.0)
            continue
        gain = elevations_m[index] - elevations_m[index - 1]
        raw.append(max(-100.0, min(100.0, 100.0 * gain / distance)))
    half = max(1, int(analysis_constants.GRADE_MEDIAN_WINDOW_S // max(period_s, 1.0)) // 2)
    return tuple(moving_median(raw, 2 * half + 1))


def window_samples(period_s: float, window_s: float) -> int:
    """Return how many samples span ``window_s`` at ``period_s`` seconds per sample."""
    if period_s <= 0:
        return 1
    return max(1, round(window_s / period_s))


def moving_average(values: Sequence[float], samples: int) -> np.ndarray:
    """Return the simple moving average of ``values`` over ``samples`` samples.

    A window of one sample returns the input: an activity shorter than the window
    still gets its own values rather than nothing.
    """
    array = np.asarray(values, dtype=float)
    if samples <= 1 or array.size <= samples:
        return array
    kernel = np.ones(samples) / samples
    return np.convolve(array, kernel, mode="valid")


def moving_median(values: Sequence[float], samples: int) -> list[float]:
    """Return each value replaced by the median of the window centred on it.

    A median, not a mean, because the streams this reads carry single-sample spikes
    (a stray GPS altitude, one dropped power sample) that a mean would smear across
    its neighbours. Windows are clamped at both ends, so the output has the same
    length as the input; ``samples`` below 2 returns the input unchanged.

    Args:
        values: Samples in order.
        samples: Width of the centred window, in samples.

    Returns:
        The filtered values, one per input sample.
    """
    data = list(values)
    if samples <= 1 or not data:
        return data
    half = samples // 2
    return [median(data[max(0, index - half) : min(len(data), index + half + 1)]) for index in range(len(data))]


def normalized_power(
    values: Sequence[float], period_s: float, window_s: float = analysis_constants.NP_WINDOW_S
) -> float:
    """Return Normalized Power: fourth-power mean of a moving average, to the 1/4.

    Args:
        values: Power samples in watts, zeros kept.
        period_s: Seconds between samples.
        window_s: Moving-average window in seconds.

    Returns:
        Normalized Power in watts, or 0 for an empty stream.
    """
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return 0.0
    smoothed = moving_average(array, window_samples(period_s, window_s))
    return float(np.mean(smoothed**4) ** 0.25)


def xpower(values: Sequence[float], period_s: float, mean_time_s: float = analysis_constants.XPOWER_MEAN_S) -> float:
    """Return xPower: an attenuated moving average of power, to the fourth root.

    The average weights each sample by ``period_s / (m + period_s)`` and decays the
    accumulated value by ``m / (m + period_s)`` per sample, where ``m`` is
    ``mean_time_s``. It reacts to a burst sooner than a boxcar window of the same
    length does, and relaxes sooner once the burst ends.
    """
    if not values or period_s <= 0:
        return 0.0
    window = mean_time_s / period_s
    attenuation = window / (window + period_s)
    weight = period_s / (window + period_s)
    accumulated = 0.0
    total = 0.0
    for value in values:
        accumulated = accumulated * attenuation + weight * float(value)
        total += accumulated**4
    return float((total / len(values)) ** 0.25)


def best_means(
    values: Sequence[float],
    period_s: float,
    durations: Sequence[int] = analysis_constants.DURATION_PEAKS_S,
) -> dict[int, float]:
    """Return the best sustained average for each requested duration.

    This is the duration-intensity curve: the highest average power (or pace, or
    heart rate) the athlete held for at least that long. Durations longer than the
    stream are simply absent from the result.
    """
    array = np.asarray(values, dtype=float)
    result: dict[int, float] = {}
    if array.size == 0:
        return result
    cumulative = np.concatenate([[0.0], np.cumsum(array)])
    for duration in durations:
        samples = window_samples(period_s, float(duration))
        if samples < 2 or array.size < samples:
            continue
        windows = cumulative[samples:] - cumulative[:-samples]
        result[int(duration)] = float(np.max(windows) / samples)
    return result


def best_window_mean(values: Sequence[float], period_s: float, window_s: float) -> float:
    """Return the best sustained average over one window, in the units of ``values``."""
    means = best_means(values, period_s, durations=[int(window_s)])
    return float(means.get(int(window_s), 0.0))


def ewf(values: Sequence[float], tau_days: float) -> list[float]:
    """Return the exponentially weighted function of a daily series.

    Each day relaxes toward that day's value: ``state[d] = v·(1-e^(-1/τ)) + state[d-1]·e^(-1/τ)``,
    the recurrence GoldenCheetah's PMC uses for fatigue (τ ≈ 7 days) and fitness
    (τ ≈ 42 days). Starting at zero, a constant daily load converges to that load,
    so the series reads in the same units as the daily sum. Days before the first
    value are not emitted: the caller supplies a dense day-by-day series (missing
    days as ``0.0``) so the decay is continuous.

    Args:
        values: One value per consecutive day.
        tau_days: Time constant in days.

    Returns:
        The smoothed state per day, same length as ``values``.
    """
    if tau_days <= 0:
        return [float(value) for value in values]
    decay = exp(-1.0 / tau_days)
    state = 0.0
    series: list[float] = []
    for value in values:
        state = float(value) * (1.0 - decay) + state * decay
        series.append(state)
    return series


def work_above_threshold(
    values: Sequence[float],
    period_s: float,
    threshold_w: float,
) -> tuple[float, float]:
    """Return the work and time spent above a threshold, ``(joules, seconds)``.

    The direct anaerobic measure behind the ``W'`` bookkeeping: every sample over
    the threshold contributes ``(P - threshold) · period`` of work and ``period``
    of time. It is what the two-parameter model says cannot be sustained, so the
    totals say how much of it an activity actually asked for.
    """
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return 0.0, 0.0
    over = np.maximum(array - threshold_w, 0.0)
    return float(np.sum(over) * period_s), float(np.count_nonzero(over) * period_s)


def skiba_tau_s(cp_w: float, values: Sequence[float]) -> float:
    """Return the W' repletion time constant of the Skiba 2012 model, in seconds.

    ``tau = 546 * e^(-0.01 * DCP) + 316`` where ``DCP`` is the difference between
    the critical power and the mean power of the samples below it -- one value per
    activity, as the paper (and GoldenCheetah's ``WPrime.cpp``) computes it. A
    stream with no samples below CP recovers with ``DCP = CP``.
    """
    array = np.asarray(values, dtype=float)
    below = array[array < cp_w]
    mean_below = float(np.mean(below)) if below.size else 0.0
    return float(546.0 * exp(-0.01 * (cp_w - mean_below)) + 316.0)


def wprime_balance(
    values: Sequence[float],
    period_s: float,
    cp_w: float,
    w_prime_j: float,
    tau_s: float | None = None,
) -> list[float]:
    """Return W' remaining (joules) after each sample, per Skiba 2012's integral form.

    ``W'bal(t) = W' - sum over u<=t of max(0, P(u) - CP) * e^(-(t-u)/tau) * period``.
    Evaluated as a one-step recurrence -- expended work decays by ``e^(-period/tau)``
    per sample and new work is added -- which reproduces the integral exactly at O(n)
    (the optimisation GoldenCheetah makes for the same reason). The result is clamped
    to ``[0, W']``: the model has no negative capacity and no over-supply.

    Args:
        values: Power samples in watts, zeros kept.
        period_s: Seconds between samples.
        cp_w: Critical power in watts.
        w_prime_j: Anaerobic work capacity in joules.
        tau_s: Repletion time constant; computed from the stream when omitted.

    Returns:
        One value per sample: W' remaining in joules.
    """
    array = np.asarray(values, dtype=float)
    if array.size == 0 or cp_w <= 0 or w_prime_j <= 0:
        return []
    tau = tau_s if tau_s and tau_s > 0 else skiba_tau_s(cp_w, array)
    decay = exp(-period_s / tau)
    expended = 0.0
    remaining: list[float] = []
    for value in array:
        over = value - cp_w
        expended = expended * decay + (over if over > 0 else 0.0) * period_s
        remaining.append(min(max(w_prime_j - expended, 0.0), w_prime_j))
    return remaining


def downsample(points: Sequence[tuple[float, float]], max_points: int) -> list[tuple[float, float]]:
    """Return an evenly spaced subset of ``(x, y)`` points, keeping the last.

    A one-second series of a five-hour ride is 18.000 points; charts do not render
    that and JSON should not carry it. Striding with the final point kept preserves
    the endpoint (often the most depleted moment) that a plain stride could drop.
    """
    if max_points <= 0 or len(points) <= max_points:
        return list(points)
    stride = len(points) / max_points
    sampled = [points[int(index * stride)] for index in range(max_points)]
    if sampled[-1] != points[-1]:
        sampled[-1] = points[-1]
    return sampled


def best_split_times(
    elapsed_s: Sequence[float],
    distance_cum_m: Sequence[float],
    power_w: Sequence[float],
    altitude_m: Sequence[float],
    distances_m: Sequence[int] = analysis_constants.BT_DISTANCES_M,
    threshold_w: float | None = None,
) -> dict[int, tuple[float, float]]:
    """Return ``(time_s, avg_power_w)`` for the fastest valid split of each distance.

    A split of distance D is every window the cumulative distance spans D or more;
    the fastest is the one taken between the latest sample that starts it and the
    sample that closes it, and tied times go to the window with the highest power,
    which is the one sitting on the real effort rather than on GPS jitter around it.
    The split counts only when its net grade over D stays above the bound for its
    length: below it the athlete was coasting downhill, and the time says nothing
    about fitness. Below that same length GPS speed itself stops being trustworthy
    (jitter and under-measured bursts), so there the split must also carry a real
    engine: mean power at least ``BT_EFFORT_IF`` times the athlete's threshold.
    Distances no window satisfies are absent from the result.
    """
    cum = np.asarray(distance_cum_m, dtype=float)
    elapsed = np.asarray(elapsed_s, dtype=float)
    altitude = np.asarray(altitude_m, dtype=float)
    power_cum = np.concatenate([[0.0], np.cumsum(np.asarray(power_w, dtype=float))])
    if cum.size != elapsed.size or cum.size != altitude.size or cum.size + 1 != power_cum.size or cum.size < 2:
        return {}

    result: dict[int, tuple[float, float]] = {}
    ends = np.arange(1, cum.size)
    for distance in distances_m:
        if cum[-1] - cum[0] < distance:
            continue
        # the latest start whose cumulative distance still leaves the full distance
        # between it and the end: the tightest window ending there covering D
        begins = np.searchsorted(cum, cum[ends] - distance, side="right") - 1
        valid = begins >= 0
        spans = elapsed[ends] - elapsed[begins]
        grades = (altitude[ends] - altitude[begins]) / distance * 100.0
        min_grade = (
            analysis_constants.BT_MIN_GRADE_PCT
            if distance >= analysis_constants.BT_SHORT_MIN_M
            else analysis_constants.BT_MIN_GRADE_PCT_SHORT
        )
        powers = (power_cum[ends] - power_cum[begins]) / np.maximum(spans, 1e-9)
        valid &= spans > 0.0
        valid &= distance / spans <= analysis_constants.BT_MAX_SPEED_MS
        valid &= grades >= min_grade
        if distance < analysis_constants.BT_SHORT_MIN_M and threshold_w:
            valid &= powers >= analysis_constants.BT_EFFORT_IF * threshold_w
        if not valid.any():
            continue
        candidates = np.flatnonzero(valid)
        # fastest first; among equals, the hardest effort wins
        order = np.lexsort((-powers[candidates], spans[candidates]))
        best = int(candidates[order[0]])
        result[int(distance)] = (float(spans[best]), float(powers[best]))
    return result


def fit_cp_wprime(
    peaks: Mapping[int, float],
    fit_from_s: float = analysis_constants.FIT_MIN_S,
    fit_to_s: float = analysis_constants.FIT_MAX_S,
    min_peaks: int = analysis_constants.FIT_MIN_PEAKS,
) -> tuple[float, float, float] | None:
    """Fit the two-parameter hyperbolic model to a duration-intensity curve.

    Work over a duration is ``W(t) = W' + CP * t``, so a linear regression of work
    against time returns the critical power as its slope and the anaerobic work
    capacity as its intercept. The fit uses only durations inside
    ``[fit_from_s, fit_to_s]``: including sprint durations bends the line above what
    the athlete can hold for an hour.

    Args:
        peaks: Best average power per duration, in watts.
        fit_from_s: Shortest duration included in the fit.
        fit_to_s: Longest duration included in the fit.
        min_peaks: Fewer points than this cannot define a line.

    Returns:
        ``(critical_power_w, w_prime_j, cv_pct)``, or ``None`` when the data or the
        fit is not usable (too few points, a non-positive slope or intercept).
    """
    points = sorted(
        (float(duration), float(power))
        for duration, power in peaks.items()
        if fit_from_s <= duration <= fit_to_s and power > 0
    )
    if len(points) < min_peaks:
        return None
    durations = np.array([point[0] for point in points], dtype=float)
    powers = np.array([point[1] for point in points], dtype=float)
    slope, intercept = np.polyfit(durations, powers * durations, 1)
    critical_power, w_prime = float(slope), float(intercept)
    if critical_power <= 0:
        return None
    # A flat curve leaves the intercept at a float's noise around zero: only a
    # materially negative capacity means the fit is nonsense (power falling faster
    # than the hyperbola allows), and a small negative is read as "no capacity".
    if w_prime < -0.01 * critical_power:
        return None
    w_prime = max(0.0, w_prime)
    predicted = critical_power + w_prime / durations
    cv_pct = 100.0 * float(np.sqrt(np.mean((predicted - powers) ** 2))) / float(np.mean(powers))
    return critical_power, w_prime, cv_pct
