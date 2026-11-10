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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    power_w: Sequence[float] | None,
    altitude_m: Sequence[float],
    distances_m: Sequence[int] = analysis_constants.BT_DISTANCES_M,
    threshold_w: float | None = None,
    intensity: Sequence[float] | None = None,
) -> dict[int, tuple[float, float | None]]:
    """Return ``(time_s, avg_power_w)`` for the fastest valid split of each distance.

    A split of distance D is every window the cumulative distance spans D or more;
    the fastest is the one taken between the latest sample that starts it and the
    sample that closes it, and tied times go to the window with the highest power,
    which is the one sitting on the real effort rather than on GPS jitter around it.
    The split counts only when its net grade over D stays above the bound for its
    length: below it the athlete was coasting downhill, and the time says nothing
    about fitness. Below that same length GPS speed itself stops being trustworthy
    (jitter and under-measured bursts), so there the split must also carry a real
    engine: mean intensity at least ``BT_EFFORT_IF`` - watts over threshold, or
    grade-adjusted pace over critical speed via ``intensity``, the chain's pace
    reading. With no engine signal at all the short distances are absent: heart
    rate lags too much to evidence a 400 m surge.
    """
    cum = np.asarray(distance_cum_m, dtype=float)
    elapsed = np.asarray(elapsed_s, dtype=float)
    altitude = np.asarray(altitude_m, dtype=float)
    engine = (
        np.asarray(intensity, dtype=float)
        if intensity is not None
        else (
            np.asarray(power_w, dtype=float) / threshold_w
            if threshold_w and threshold_w > 0 and power_w is not None
            else None
        )
    )
    engine_cum = np.concatenate([[0.0], np.cumsum(engine)]) if engine is not None else None
    power_cum = np.concatenate([[0.0], np.cumsum(np.asarray(power_w, dtype=float))]) if power_w is not None else None
    if cum.size != elapsed.size or cum.size != altitude.size or cum.size < 2:
        return {}
    if engine_cum is not None and cum.size + 1 != engine_cum.size:
        return {}

    result: dict[int, tuple[float, float | None]] = {}
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
        if distance < analysis_constants.BT_SHORT_MIN_M and engine_cum is None:
            continue  # no signal can evidence a short surge
        engine_means = (
            (engine_cum[ends] - engine_cum[begins]) / np.maximum(spans, 1e-9) if engine_cum is not None else spans * 0.0
        )
        valid &= spans > 0.0
        valid &= distance / spans <= analysis_constants.BT_MAX_SPEED_MS
        valid &= grades >= min_grade
        if distance < analysis_constants.BT_SHORT_MIN_M:
            valid &= engine_means >= analysis_constants.BT_EFFORT_IF
        if not valid.any():
            continue
        candidates = np.flatnonzero(valid)
        # fastest first; among equals, the hardest effort wins
        order = np.lexsort((-engine_means[candidates], spans[candidates]))
        best = int(candidates[order[0]])
        avg_power = (
            float((power_cum[ends[best]] - power_cum[begins[best]]) / max(spans[best], 1e-9))
            if power_cum is not None
            else None
        )
        result[int(distance)] = (float(spans[best]), avg_power)
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


def grade_adjusted_run_speed(
    speed_ms: Sequence[float],
    grades_pct: Sequence[float],
) -> tuple[float, ...]:
    """Return each sample's equivalent flat-ground running speed.

    Climbing slows the runner while costing more energy, and descending speeds them
    up while costing braking; the GAP factor (GoldenCheetah's fit to Strava's curve)
    converts the actual speed into the flat-ground speed that would cost the same,
    which is what lets a pace-based intensity survive real terrain. Grades outside
    the polynomial's ±40 % validity get the constants the model uses at its edges;
    a grade of exactly zero stays unadjusted.

    Args:
        speed_ms: One speed per sample, in metres per second.
        grades_pct: One grade per sample, in percent, aligned to ``speed_ms``.

    Returns:
        The equivalent flat-ground speed per sample, aligned to the input.
    """
    count = min(len(speed_ms), len(grades_pct))
    speeds = np.asarray(speed_ms[:count], dtype=float)
    slopes = np.asarray(grades_pct[:count], dtype=float)
    factors = np.polyval(
        analysis_constants.GAP_COEFFICIENTS,
        np.clip(slopes, -analysis_constants.GAP_SLOPE_CLAMP_PCT, analysis_constants.GAP_SLOPE_CLAMP_PCT),
    )
    factors = np.where(slopes > analysis_constants.GAP_SLOPE_CLAMP_PCT, analysis_constants.GAP_CLAMP_FACTOR_UP, factors)
    factors = np.where(
        slopes < -analysis_constants.GAP_SLOPE_CLAMP_PCT, analysis_constants.GAP_CLAMP_FACTOR_DOWN, factors
    )
    factors = np.where(slopes == analysis_constants.GAP_FLAT_CORRECTION_PCT, 1.0, factors)
    return tuple(speeds * factors)


def temporal_threshold(
    fits: Sequence[tuple[date, float]],
    on_day: date,
    *,
    back_days: int,
    fwd_days: int,
    decay_per_day: float,
    min_fits: int,
) -> float | None:
    """Return the threshold supported by dated evidence, decayed by age.

    A fit dated ``e`` supports day ``D`` at ``fit * exp(-decay * age)``, where the
    age is zero for evidence at or after ``D`` (it is known at scoring time). The
    threshold is the best such support inside the window: hard efforts lift it at
    once, and without new evidence it relaxes toward the last thing the athlete
    actually held. Fewer than ``min_fits`` fits in the window means the evidence
    is too thin to model an evolution, and the caller should fall back.

    Args:
        fits: ``(day, threshold)`` pairs, any order; already quality-gated.
        on_day: The day to answer for.
        back_days: How far back evidence still counts (before decay is applied to it).
        fwd_days: How far ahead evidence may sit (ahead-of-day evidence counts undecayed).
        decay_per_day: Fractional relaxation per day of age.
        min_fits: Least evidence a usable answer needs.

    Returns:
        The decayed maximum in watts, or ``None`` when the window is too thin.
    """
    lo = on_day - timedelta(days=back_days)
    hi = on_day + timedelta(days=fwd_days)
    best: float | None = None
    seen = 0
    for day, value in fits:
        if day < lo or day > hi:
            continue
        seen += 1
        age = max(0, (on_day - day).days)
        support = float(value) * exp(-decay_per_day * age)
        if best is None or support > best:
            best = support
    return best if seen >= min_fits else None


def peak_violation(
    means: Mapping[int, float],
    session_np_w: float,
    floor_bands: Sequence[tuple[float, float]],
    *,
    min_s: float = analysis_constants.PEAK_OUTLIER_MIN_S,
    tiers: Sequence[tuple[float, float]] = analysis_constants.PEAK_OUTLIER_TIERS,
) -> tuple[int, float, float, float] | None:
    """Return the most implausible peak of one session's duration-intensity curve.

    A peak is implausible when it exceeds BOTH its tier's multiple of the
    session's own normalized power AND the absolute floor of its duration band:
    the ratio alone would flag every surge on a walk day, the floor alone would
    flag an honest max on an easy day. Returns ``(duration_s, value_w, ratio,
    cap)`` for the strongest violation, or ``None`` when the session's curve is
    plausible end to end.

    Args:
        means: Best average power per duration for the session, in watts.
        session_np_w: The session's own normalized power, in watts.
        floor_bands: ``(max_duration_s, floor_w)`` ascending, last band open;
            seconds are judged against higher floors (a 5-s sprint peak is
            physiologically huge) than minutes are.
        min_s: Durations below this are never judged.
        tiers: ``(max_duration_s, ratio_cap)`` steps, ascending.

    Returns:
        The strongest violation, or ``None``.
    """
    if session_np_w <= 0:
        return None
    worst: tuple[int, float, float, float] | None = None
    for duration, value in means.items():
        if duration < min_s:
            continue
        floor_w = next((band_floor for band_max, band_floor in floor_bands if duration <= band_max), floor_bands[-1][1])
        if value < floor_w:
            continue
        cap = next((tier_cap for tier_max, tier_cap in tiers if duration <= tier_max), tiers[-1][1])
        ratio = value / session_np_w
        if ratio > cap and (worst is None or ratio > worst[2]):
            worst = (int(duration), float(value), ratio, cap)
    return worst


def short_peak_runaway(
    power_w: Sequence[float],
    speeds_ms: Sequence[float],
    period_s: float,
    grades_pct: Sequence[float] | None = None,
    *,
    min_w: float = analysis_constants.PEAK_RUNAWAY_MIN_W,
    max_speed_ms: float = analysis_constants.PEAK_RUNAWAY_MAX_V_MS,
    neighbor_share: float = analysis_constants.PEAK_RUNAWAY_NEIGHBOR_SHARE,
    stall_v_ms: float = analysis_constants.PEAK_RUNAWAY_STALL_V_MS,
    grade_exempt_pct: float = analysis_constants.PEAK_RUNAWAY_GRADE_EXEMPT_PCT,
) -> tuple[int, float, float] | None:
    """Detect the sensor-runaway signature in the sub-thirty-second tail.

    Watts alone cannot judge a five-second peak (a real sprint is physically
    enormous); what gives a runaway away is context: a huge short average while
    the athlete barely moves, with honest effort nowhere near it. A steep climb
    legitimately looks like huge watts at slow speed - its grade exempts the
    window - but power always scales with ground speed, so a near-stall (any
    grade) is the sensor, never the athlete. Returns ``(duration_s, value_w,
    mean_speed_ms)`` for the strongest signature, or ``None``.

    Args:
        power_w: The session's power samples.
        speeds_ms: Speed samples aligned one-to-one to ``power_w``.
        period_s: Seconds between samples.
        grades_pct: Grade per sample aligned to ``power_w``, or ``None`` when
            the session has no altitude stream.
        min_w: Only windows averaging above this are candidates.
        max_speed_ms: Below this mean speed the window counts as barely moving.
        neighbor_share: A window is isolated when every neighboring 30 s block
            stays under this share of its average.
        stall_v_ms: Under this mean speed no grade exempts the window.
        grade_exempt_pct: Above this mean |grade| a slow window stands.
    """
    n = len(power_w)
    if n < 120 or len(speeds_ms) != n or period_s <= 0:
        return None
    if grades_pct is not None and len(grades_pct) != n:
        grades_pct = None
    pre_p = [0.0] * (n + 1)
    pre_v = [0.0] * (n + 1)
    for i in range(n):
        pre_p[i + 1] = pre_p[i] + power_w[i]
        pre_v[i + 1] = pre_v[i] + speeds_ms[i]
    bucket = max(int(30.0 / period_s), 1)
    nb = (n + bucket - 1) // bucket
    bucket_mean = [
        (pre_p[min((b + 1) * bucket, n)] - pre_p[b * bucket]) / (min((b + 1) * bucket, n) - b * bucket)
        for b in range(nb)
    ]

    def window_mean(pre: list[float], lo: int, hi: int) -> float:
        return (pre[hi] - pre[lo]) / (hi - lo)

    best: tuple[int, float, float] | None = None
    for dur_s in (5, 10, 15, 20, 25):
        win = max(int(dur_s / period_s), 1)
        if win >= n:
            continue
        for lo in range(n - win + 1):
            mean = window_mean(pre_p, lo, lo + win)
            if mean < min_w:
                continue
            v_mean = window_mean(pre_v, lo, lo + win)
            if v_mean >= max_speed_ms:
                continue
            if grades_pct is not None and v_mean > stall_v_ms:
                g_mean = sum(abs(grades_pct[i]) for i in range(lo, lo + win)) / win
                if g_mean >= grade_exempt_pct:
                    continue
            first = lo // bucket
            last = (lo + win - 1) // bucket
            isolated = all(
                bucket_mean[b] < mean * neighbor_share
                for b in range(max(first - 3, 0), min(last + 4, nb))
                if b < first or b > last
            )
            if isolated and (best is None or mean > best[1]):
                best = (dur_s, mean, v_mean)
    return best


@dataclass(frozen=True)
class PaceModel:
    """An athlete's own rule of thumb for how long ground takes them.

    The seconds a run took, explained by what it covered: so much per metre of
    ground, so much more per metre climbed, descended and spent on technical
    terrain. It is fitted over their own sessions and says how well it fits.

    Attributes:
        per_m: Seconds per metre of ground.
        per_gain_m: Extra seconds per metre climbed.
        per_loss_m: Extra seconds per metre descended.
        per_technical_m: Extra seconds per metre of technical ground.
        error: Median relative miss over the sessions it was fitted on (0-1).
        sessions: Sessions it was fitted on.
    """

    per_m: float
    per_gain_m: float
    per_loss_m: float
    per_technical_m: float
    error: float
    sessions: int

    def seconds(self, distance_m: float, gain_m: float, loss_m: float, technical_m: float) -> float:
        """Time the rule gives a stretch of ground."""
        return (
            self.per_m * distance_m
            + self.per_gain_m * gain_m
            + self.per_loss_m * loss_m
            + self.per_technical_m * technical_m
        )


def fit_pace_model(
    sessions: Sequence[tuple[float, float, float, float, float]],
    min_sessions: int = analysis_constants.TRAIL_PACE_MIN_SESSIONS,
) -> PaceModel | None:
    """Fit the athlete's pace rule over their own sessions.

    Least squares on the relative miss, so a long day in the mountains weighs
    like a short run rather than drowning it. A term that comes out negative
    would let a long enough stretch take no time at all, so it is dropped
    before fitting again.

    Args:
        sessions: ``(distance_m, gain_m, loss_m, technical_m, moving_s)`` per session.
        min_sessions: Fewest sessions a rule may be fitted on.

    Returns:
        The rule, or ``None`` when there are too few sessions to fit one.
    """
    rows = np.asarray([row for row in sessions if row[4] > 0 and row[0] > 0], dtype=float)
    if len(rows) < min_sessions:
        return None
    relative = rows[:, :4] / rows[:, 4:5]
    kept = [0, 1, 2, 3]
    coefficients = np.zeros(4)
    while kept:
        solved, *_ = np.linalg.lstsq(relative[:, kept], np.ones(len(rows)), rcond=None)
        negative = [term for term, value in zip(kept, solved, strict=True) if value < 0 and term != 0]
        if not negative:
            coefficients[kept] = solved
            break
        kept = [term for term in kept if term not in negative]
    if coefficients[0] <= 0:
        return None
    error = float(np.median(np.abs(relative @ coefficients - 1.0)))
    return PaceModel(*(float(value) for value in coefficients), error=error, sessions=len(rows))
