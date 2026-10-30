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
