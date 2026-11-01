"""Splitting one activity into the efforts the athlete actually did.

The question this answers is not "what was the average of the session" but "what did
the session consist of": a 10 km race pushed for 4 km and then run easy is not the
same training as five 1 km reps, and a trail run whose hard moments are the climbs is
not an interval session either. Every threshold is relative to the athlete's own
numbers, never to a general population figure:

* the sport's **threshold** (their derived or stated critical power) says what an
  effort means physiologically, and
* the session's **median power** says what was hard *that day*, which is what tells a
  surge inside a steady run apart from a steady run.

The shape parameters around them (shortest rep, what counts as a recovery, how steep
before a push is terrain rather than a rep) are per athlete too, arriving through
``analysis_settings.method_params`` under the ``iv_`` prefix, so an athlete whose
intervals are 30-second hills and one whose are 8-minute threshold reps are both read
by their own settings.

The output is a timeline: every measured second belongs to exactly one segment,
labelled ``interval``, ``climb``, ``race``, ``recovery`` or ``easy``, so a table can
explain the session end to end instead of listing only the hard parts.
"""

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import chain
from statistics import fmean, median

import modules.analysis.constants as analysis_constants
import modules.analysis.maths as analysis_maths
import modules.analysis.methods as analysis_methods


class SegmentKind(StrEnum):
    """What one stretch of an activity was.

    Attributes:
        INTERVAL: A sustained effort above the athlete's threshold, kept separate from
            the next one by a real recovery or by clearly standing above the session.
        CLIMB: A sustained effort explained by the gradient rather than taken as a rep.
        RACE: One effort holding most of the session: a race or a long tempo block.
        RECOVERY: An easy stretch between two efforts, deep enough to be a rest.
        EASY: Anything else: warm-up, cooldown, the flat part between pushes.
    """

    INTERVAL = "interval"
    CLIMB = "climb"
    RACE = "race"
    RECOVERY = "recovery"
    EASY = "easy"


class SessionShape(StrEnum):
    """How the session as a whole reads.

    Attributes:
        INTERVALS: Several efforts separated by recoveries, of similar length.
        FARTLEK: Several efforts of unlike length.
        CONTINUOUS: One sustained effort with no real recovery in it.
        TERRAIN: The hard moments are climbs, not chosen reps.
        STEADY: Nothing rose to an effort: an easy or endurance day.
    """

    INTERVALS = "intervals"
    FARTLEK = "fartlek"
    CONTINUOUS = "continuous"
    TERRAIN = "terrain"
    STEADY = "steady"


@dataclass(frozen=True)
class IntervalParams:
    """The athlete's own segmentation knobs, all relative to their numbers.

    Attributes:
        enter_cp: Multiple of the threshold that opens an effort.
        enter_base: Multiple of the session median that opens an effort.
        exit_cp: Multiple of the threshold that closes it.
        exit_base: Multiple of the session median that closes it.
        min_segment_s: Shortest run kept; shorter ones are strides, not reps.
        short_segment_s: Below this length an effort also needs ``short_base`` contrast.
        short_base: Contrast required for a short effort.
        strong_base: Contrast that makes an effort count without a recovery before it.
        recovery_cp: At or below this fraction of the threshold a gap is a real recovery.
        effort_if: Average intensity a flat run must hold to count as an interval.
        merge_gap_s: A gap no longer than this joins two runs into one effort.
        merge_share: Or a gap no longer than this share of the effort before it.
        climb_grade_pct: Mean grade above which an effort may be terrain rather than a rep.
        climb_if: Intensity below which that grade explanation is accepted.
        steady_share: Share of the session an effort must hold to be the session's push.
        steady_if: Below this intensity a session-long effort reads as a race, not a rep.
    """

    enter_cp: float = analysis_constants.IV_ENTER_CP
    enter_base: float = analysis_constants.IV_ENTER_BASE
    exit_cp: float = analysis_constants.IV_EXIT_CP
    exit_base: float = analysis_constants.IV_EXIT_BASE
    min_segment_s: float = analysis_constants.IV_MIN_SEGMENT_S
    short_segment_s: float = analysis_constants.IV_SHORT_SEGMENT_S
    short_base: float = analysis_constants.IV_SHORT_BASE
    strong_base: float = analysis_constants.IV_STRONG_BASE
    recovery_cp: float = analysis_constants.IV_RECOVERY_CP
    effort_if: float = analysis_constants.IV_EFFORT_IF
    merge_gap_s: float = analysis_constants.IV_MERGE_GAP_S
    merge_share: float = analysis_constants.IV_MERGE_SHARE
    climb_grade_pct: float = analysis_constants.IV_CLIMB_GRADE_PCT
    climb_if: float = analysis_constants.IV_CLIMB_IF
    steady_share: float = analysis_constants.IV_STEADY_SHARE
    steady_if: float = analysis_constants.IV_STEADY_IF


def params_from(parameters: Mapping[str, float]) -> IntervalParams:
    """Return the athlete's segmentation parameters, defaults where unset."""
    defaults = IntervalParams()
    return IntervalParams(
        enter_cp=parameters.get("iv_enter_cp", defaults.enter_cp),
        enter_base=parameters.get("iv_enter_base", defaults.enter_base),
        exit_cp=parameters.get("iv_exit_cp", defaults.exit_cp),
        exit_base=parameters.get("iv_exit_base", defaults.exit_base),
        min_segment_s=parameters.get("iv_min_segment_s", defaults.min_segment_s),
        short_segment_s=parameters.get("iv_short_segment_s", defaults.short_segment_s),
        short_base=parameters.get("iv_short_base", defaults.short_base),
        strong_base=parameters.get("iv_strong_base", defaults.strong_base),
        recovery_cp=parameters.get("iv_recovery_cp", defaults.recovery_cp),
        effort_if=parameters.get("iv_effort_if", defaults.effort_if),
        merge_gap_s=parameters.get("iv_merge_gap_s", defaults.merge_gap_s),
        merge_share=parameters.get("iv_merge_share", defaults.merge_share),
        climb_grade_pct=parameters.get("iv_climb_grade_pct", defaults.climb_grade_pct),
        climb_if=parameters.get("iv_climb_if", defaults.climb_if),
        steady_share=parameters.get("iv_steady_share", defaults.steady_share),
        steady_if=parameters.get("iv_steady_if", defaults.steady_if),
    )


@dataclass(frozen=True)
class Segment:
    """One stretch of the activity timeline.

    Attributes:
        kind: What the stretch was.
        start_s: Seconds from the start of the sampled data.
        duration_s: Length of the stretch.
        distance_m: Ground covered in it (0 when the activity carries no speed).
        avg_power_w: Mean power over the stretch.
        intensity_factor: Mean power over the sport's threshold (0 without one).
        contrast: Mean power over the session's own median.
        avg_grade_pct: Mean grade, ``None`` when no track was recorded.
        avg_speed_ms: Mean speed, ``None`` when the activity carries none.
        elevation_gain_m: Ground risen through in the stretch, ``None`` without altitude.
        elevation_loss_m: Ground fallen through in the stretch, ``None`` without altitude.
        rss: The shared-scale aRSS score of the stretch (same kernel as the daily
            rss_total), ``None`` when the sport has no threshold.
        rss_per_min: That score per minute of the stretch - aRSS is a time
            integral, so this is its mean stress rate and stretches of different
            length compare directly; ``None`` when there is no score.
    """

    kind: SegmentKind
    start_s: float
    duration_s: float
    distance_m: float
    avg_power_w: float
    intensity_factor: float
    contrast: float
    avg_grade_pct: float | None
    avg_speed_ms: float | None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    rss: float | None = None
    rss_per_min: float | None = None


@dataclass(frozen=True)
class IntervalAnalysis:
    """The segmentation of one activity.

    Attributes:
        shape: How the session as a whole reads.
        base_power_w: The session's own median power, the relative anchor used.
        threshold_w: The threshold the intensities were divided by.
        segments: Every stretch of the timeline, in order, contiguous and complete.
        rules: The effective numeric gates used for this session (watts and factors),
            so the athlete can see why each stretch was called what it was; ``None``
            when nothing was segmented.
    """

    shape: SessionShape
    base_power_w: float
    threshold_w: float | None
    segments: tuple[Segment, ...]
    rules: Mapping[str, float] | None = None


def _window_samples(period_s: float, window_s: float) -> int:
    """Return the odd number of samples a smoothing window spans at ``period_s``."""
    return max(3, 2 * (int(window_s / period_s) // 2) + 1)


def _smooth_altitude(values: list[float], samples: int) -> list[float]:
    """Median-smooth an altitude series without the clamped window dragging its ends.

    A centred median at the series' ends reads only one side of the slope, so a
    climb that runs straight into the first or last sample would lose its foot and
    its summit to the filter. Extending both ends along their own slope first lets
    the window see the same terrain on every sample.
    """
    if samples <= 1 or len(values) < 2:
        return analysis_maths.moving_median(values, samples)
    half = samples // 2
    lead = min(half, len(values) - 1)
    head_slope = (values[lead] - values[0]) / lead
    tail_slope = (values[-1] - values[-1 - lead]) / lead
    pad_head = [values[0] - head_slope * (half - k) for k in range(half)]
    pad_tail = [values[-1] + tail_slope * (k + 1) for k in range(half)]
    smoothed = analysis_maths.moving_median(pad_head + values + pad_tail, samples)
    return smoothed[half : half + len(values)]


def _runs_above(
    signal: Sequence[float],
    period_s: float,
    open_at: float,
    close_at: float,
    min_length_s: float,
) -> list[tuple[int, int]]:
    """Return ``[start, end)`` sample ranges the signal holds above ``open_at``.

    A range closes when the signal falls to ``close_at``, which sits below ``open_at``:
    that hysteresis is what keeps one effort from being cut in two by a single wobble.
    """
    found: list[tuple[int, int]] = []
    begin: int | None = None
    limit = min_length_s / max(period_s, 1.0)
    for index, value in enumerate(chain(signal, [float("-inf")])):
        if begin is None:
            if value >= open_at:
                begin = index
        elif value <= close_at:
            if index - begin >= limit:
                found.append((begin, index))
            begin = None
    return found


def _keep_efforts(
    runs: list[tuple[int, int]],
    signal: Sequence[float],
    period_s: float,
    params: IntervalParams,
    base: float,
    anchor_w: float,
) -> list[tuple[int, int]]:
    """Decide which raw runs are separate efforts, merged halves, or passing surges.

    Three cases, every one measured against this athlete's own numbers:

    * a short gap *inside* one effort (a wobble, a corner, a brief dip) joins the runs
      back together, so one rep is never reported as two;
    * a run whose gap never became a recovery and that barely stands above the
      session's own level is a *passing surge*: a 10 km race that eases to a cruise and
      pops at the finish was one effort, not two;
    * anything else keeps its own identity.
    """
    kept: list[tuple[int, int]] = []
    for run in runs:
        contrast = fmean(signal[run[0] : run[1]]) / base if base > 0 else 0.0
        if kept:
            gap_samples = run[0] - kept[-1][1]
            gap_s = gap_samples * period_s
            previous_s = (kept[-1][1] - kept[-1][0]) * period_s
            gap_level = fmean(signal[kept[-1][1] : run[0]]) if gap_samples > 0 else 0.0
            if gap_s <= min(params.merge_gap_s, params.merge_share * previous_s):
                kept[-1] = (kept[-1][0], run[1])
                continue
            rests = gap_level <= params.recovery_cp * anchor_w
            if not rests and contrast < params.strong_base:
                continue
        elif contrast < params.strong_base:
            # The first run has no gap to judge it by, so it stands on contrast alone:
            # a session whose opening merely nudges above its own median is a steady
            # day, not a rep.
            continue
        kept.append(run)
    return kept


def _classify_effort(
    run: tuple[int, int],
    signal: Sequence[float],
    grades_pct: Sequence[float] | None,
    params: IntervalParams,
    anchor_w: float,
    period_s: float,
    measured_s: float,
) -> SegmentKind:
    """Return what one effort was: the session's own push, a climb, a rep, or nothing.

    On flat ground the average has to actually sit above this athlete's threshold
    (``effort_if``): a run that only grazed the opening gate and then cruised below
    the threshold is tempo, not a rep. Where the grade explains the power, the climb
    rule decides instead.
    """
    power = fmean(signal[run[0] : run[1]])
    intensity = power / anchor_w if anchor_w else 0.0
    if (run[1] - run[0]) * period_s >= params.steady_share * measured_s and intensity < params.steady_if:
        return SegmentKind.RACE
    if grades_pct is not None:
        grade = fmean(grades_pct[run[0] : run[1]])
        if grade > params.climb_grade_pct:
            if intensity < params.climb_if:
                return SegmentKind.CLIMB
            return SegmentKind.INTERVAL
    if intensity < params.effort_if:
        return SegmentKind.EASY
    return SegmentKind.INTERVAL


def analyse_activity(
    power_w: Sequence[float],
    period_s: float,
    threshold_w: float | None,
    speeds_ms: Sequence[float] | None = None,
    grades_pct: Sequence[float] | None = None,
    altitudes_m: Sequence[float] | None = None,
    parameters: Mapping[str, float] | None = None,
) -> IntervalAnalysis:
    """Split one activity's samples into the efforts it contained.

    Args:
        power_w: Power samples in watts, stops kept as zero.
        period_s: Seconds between samples.
        threshold_w: The sport's threshold power, or ``None`` when the athlete has
            none, in which case only the session-relative anchor applies.
        speeds_ms: Speed samples aligned to the power ones, for distance and pace.
        grades_pct: Grade samples aligned to the power ones, for climbs.
        altitudes_m: Altitude samples aligned to the power ones, for per-segment
            elevation gain and loss.
        parameters: The athlete's ``iv_`` overrides.

    Returns:
        An :class:`IntervalAnalysis`: contiguous segments covering the timeline and a
        shape for the session. Nothing is invented when an input is missing: without
        speed the distances read 0, without a grade no climb is claimed, without an
        altitude the elevations read ``None``.
    """
    params = params_from(parameters or {})
    raw_parameters = parameters or {}
    empty = IntervalAnalysis(shape=SessionShape.STEADY, base_power_w=0.0, threshold_w=threshold_w, segments=())
    if not power_w or period_s <= 0:
        return empty

    signal = analysis_maths.moving_median(power_w, _window_samples(period_s, analysis_constants.IV_SMOOTH_WINDOW_S))
    measured = [value for value in signal if value > 0]
    if not measured:
        return empty
    base = median(measured)
    anchor_w = threshold_w or base

    if threshold_w:
        open_at = max(params.enter_cp * threshold_w, params.enter_base * base)
        close_at = max(params.exit_cp * threshold_w, params.exit_base * base)
    else:
        open_at = params.enter_base * base
        close_at = params.exit_base * base

    runs = []
    for run in _runs_above(signal, period_s, open_at, close_at, params.min_segment_s):
        length_s = (run[1] - run[0]) * period_s
        if length_s < params.short_segment_s and fmean(signal[run[0] : run[1]]) < params.short_base * base:
            continue
        runs.append(run)
    runs = _keep_efforts(runs, signal, period_s, params, base, anchor_w)

    measured_s = len(signal) * period_s
    efforts = [_classify_effort(run, signal, grades_pct, params, anchor_w, period_s, measured_s) for run in runs]
    altitudes_smooth = (
        analysis_maths.moving_median(altitudes_m, _window_samples(period_s, analysis_constants.IV_ELEV_MEDIAN_S))
        if altitudes_m is not None
        else None
    )
    segments = _timeline(
        runs,
        efforts,
        signal,
        speeds_ms,
        grades_pct,
        period_s,
        base,
        anchor_w,
        threshold_w,
        power_w,
        altitudes_smooth,
        raw_parameters,
    )
    real_efforts = [kind for kind in efforts if kind is not SegmentKind.EASY]
    return IntervalAnalysis(
        shape=_shape(real_efforts, segments, base, anchor_w),
        base_power_w=round(base, 1),
        threshold_w=threshold_w,
        segments=tuple(segments),
        rules={
            "open_w": round(open_at, 1),
            "close_w": round(close_at, 1),
            "min_segment_s": params.min_segment_s,
            "short_segment_s": params.short_segment_s,
            "short_base": params.short_base,
            "strong_base": params.strong_base,
            "recovery_w": round(params.recovery_cp * anchor_w, 1),
            "effort_if": params.effort_if,
            "climb_grade_pct": params.climb_grade_pct,
            "climb_if": params.climb_if,
        },
    )


def _timeline(
    runs: Sequence[tuple[int, int]],
    efforts: Sequence[SegmentKind],
    signal: Sequence[float],
    speeds_ms: Sequence[float] | None,
    grades_pct: Sequence[float] | None,
    period_s: float,
    base: float,
    anchor_w: float,
    threshold_w: float | None,
    power_w: Sequence[float],
    altitudes_m: Sequence[float] | None,
    parameters: Mapping[str, float],
) -> list[Segment]:
    """Return contiguous segments covering the timeline: each effort and the gaps."""
    out: list[Segment] = []
    cursor = 0
    for run, kind in zip(runs, efforts, strict=True):
        if run[0] > cursor:
            out.append(
                _gap_segment(
                    (cursor, run[0]),
                    signal,
                    speeds_ms,
                    grades_pct,
                    period_s,
                    base,
                    anchor_w,
                    threshold_w,
                    power_w,
                    altitudes_m,
                    parameters,
                )
            )
        out.append(
            _segment(
                kind, run, signal, speeds_ms, grades_pct, period_s, base, threshold_w, power_w, altitudes_m, parameters
            )
        )
        cursor = run[1]
    if cursor < len(signal):
        out.append(
            _gap_segment(
                (cursor, len(signal)),
                signal,
                speeds_ms,
                grades_pct,
                period_s,
                base,
                anchor_w,
                threshold_w,
                power_w,
                altitudes_m,
                parameters,
            )
        )
    return out


def _segment(
    kind: SegmentKind,
    run: tuple[int, int],
    signal: Sequence[float],
    speeds_ms: Sequence[float] | None,
    grades_pct: Sequence[float] | None,
    period_s: float,
    base: float,
    threshold_w: float | None,
    power_w: Sequence[float],
    altitudes_m: Sequence[float] | None,
    parameters: Mapping[str, float],
) -> Segment:
    """Describe one stretch of samples as a segment."""
    span = run[1] - run[0]
    power = fmean(signal[run[0] : run[1]]) if span else 0.0
    speed = fmean(speeds_ms[run[0] : run[1]]) if speeds_ms is not None and span else 0.0
    gain_m: float | None = None
    loss_m: float | None = None
    if altitudes_m is not None and span:
        deltas = [altitudes_m[i] - altitudes_m[i - 1] for i in range(run[0] + 1, run[1])]
        gain_m = round(sum(d for d in deltas if d > 0), 1)
        loss_m = round(sum(-d for d in deltas if d < 0), 1)
    rss = (
        round(
            analysis_methods.rss_window_stress(
                power_w[run[0] : run[1]],
                period_s,
                threshold_w,
                grades_pct[run[0] : run[1]] if grades_pct is not None else None,
                parameters,
            ),
            1,
        )
        if threshold_w
        else None
    )
    distance_m = speed * span * period_s
    duration_s = span * period_s
    rss_per_min = round(rss * 60.0 / duration_s, 2) if rss and duration_s > 0 else None
    return Segment(
        kind=kind,
        start_s=round(run[0] * period_s, 1),
        duration_s=round(duration_s, 1),
        distance_m=round(distance_m, 1),
        avg_power_w=round(power, 1),
        intensity_factor=round(power / threshold_w, 2) if threshold_w else 0.0,
        contrast=round(power / base, 2) if base else 0.0,
        avg_grade_pct=round(fmean(grades_pct[run[0] : run[1]]), 1) if grades_pct is not None and span else None,
        avg_speed_ms=round(speed, 2) if speeds_ms is not None else None,
        elevation_gain_m=gain_m,
        elevation_loss_m=loss_m,
        rss=rss,
        rss_per_min=rss_per_min,
    )


def _gap_segment(
    run: tuple[int, int],
    signal: Sequence[float],
    speeds_ms: Sequence[float] | None,
    grades_pct: Sequence[float] | None,
    period_s: float,
    base: float,
    anchor_w: float,
    threshold_w: float | None,
    power_w: Sequence[float],
    altitudes_m: Sequence[float] | None,
    parameters: Mapping[str, float],
) -> Segment:
    """Describe the stretch between two efforts: a recovery when it is deep enough."""
    power = fmean(signal[run[0] : run[1]]) if run[1] > run[0] else 0.0
    kind = SegmentKind.RECOVERY if power <= analysis_constants.IV_RECOVERY_CP * anchor_w else SegmentKind.EASY
    return _segment(
        kind, run, signal, speeds_ms, grades_pct, period_s, base, threshold_w, power_w, altitudes_m, parameters
    )


def _shape(
    efforts: Sequence[SegmentKind],
    segments: Sequence[Segment],
    base_w: float,
    anchor_w: float,
) -> SessionShape:
    """Summarise the session from the efforts found in it."""
    if not efforts:
        # Nothing rose above the session's own level. A day spent near the threshold is
        # still one sustained effort (a race or a tempo run); a day well below it was
        # easy, and saying so is the honest answer.
        return (
            SessionShape.CONTINUOUS
            if anchor_w and base_w >= analysis_constants.IV_CONTINUOUS_BASE * anchor_w
            else SessionShape.STEADY
        )
    if all(kind is SegmentKind.CLIMB for kind in efforts):
        return SessionShape.TERRAIN
    reps = [kind for kind in efforts if kind is not SegmentKind.RACE]
    if not reps:
        return SessionShape.CONTINUOUS
    if len(reps) < analysis_constants.IV_MIN_REPS:
        return SessionShape.CONTINUOUS
    durations = [
        segment.duration_s for segment in segments if segment.kind in (SegmentKind.INTERVAL, SegmentKind.CLIMB)
    ]
    if len(durations) >= 2:
        mean = fmean(durations)
        if mean > 0:
            spread = fmean([(value - mean) ** 2 for value in durations]) ** 0.5 / mean
            if spread <= analysis_constants.IV_REGULAR_CV:
                return SessionShape.INTERVALS
    return SessionShape.FARTLEK


@dataclass(frozen=True)
class TerrainClimb:
    """One climb the route itself shows, whatever the athlete did on it.

    Unlike the timeline's segments these are read from the terrain, not from the
    power signal: a long steady ascent that never crossed the effort threshold is
    still a climb, and several climbs joined by short dips are one climb. They are
    an independent view, so they may overlap the timeline freely and are never
    summed with it.

    Attributes:
        start_m: Ground position where the climb starts, from the start of the data.
        end_m: Ground position where it ends.
        length_m: Ground climbed over.
        start_s: Seconds from the start of the sampled data.
        duration_s: Time spent on the climb.
        elevation_gain_m: Ground risen through.
        elevation_loss_m: Ground let go inside the climb (dips that did not break it).
        avg_grade_pct: Net grade over the whole climb.
        avg_power_w: Mean power over the climb, ``None`` without a power stream.
        intensity_factor: Mean power over the sport's threshold, ``None`` without either.
        contrast: Mean power over the session's median, ``None`` without either.
        rss: Shared-scale aRSS score of the climb, from the best signal available.
        rss_source: Which signal scored it (``power``, ``pace``, ``hr``), ``None`` when
            nothing could.
        rss_per_min: That score per minute of climbing, the mean stress rate.
    """

    start_m: float
    end_m: float
    length_m: float
    start_s: float
    duration_s: float
    elevation_gain_m: float
    elevation_loss_m: float
    avg_grade_pct: float
    avg_power_w: float | None
    intensity_factor: float | None
    contrast: float | None
    rss: float | None
    rss_source: str | None
    rss_per_min: float | None


def terrain_climbs(
    altitudes_m: Sequence[float] | None,
    speeds_ms: Sequence[float] | None,
    period_s: float,
    power_w: Sequence[float] | None = None,
    hr_bpm: Sequence[float] | None = None,
    threshold_w: float | None = None,
    speed_threshold_ms: float | None = None,
    hr_threshold_bpm: float | None = None,
    base_power_w: float = 0.0,
    parameters: Mapping[str, float] | None = None,
) -> list[TerrainClimb]:
    """Return the climbs the smoothed altitude profile shows along the route.

    A stretch opens as a climb when the net grade over ``iv_terrain_window_m`` reaches
    ``iv_terrain_open_pct`` and stays one while it holds above ``iv_terrain_close_pct``
    - the gap between the two is what keeps a steady ascent in one piece. A short dip
    between two climbs (ground and depth bounded by ``iv_terrain_merge_*``) is a false
    flat inside the climb, not its end. What survives must be long enough, have gained
    enough, and have climbed more than it let go across the whole stretch - that last
    filter is what keeps rolling ground off the list.

    Nothing is invented: without an altitude or speed stream there are no climbs to
    find, and without power or a threshold the climb is still listed, only its power
    and stress columns read ``None``.
    """
    params = parameters or {}
    if altitudes_m is None or speeds_ms is None or period_s <= 0:
        return []
    window_m = params.get("iv_terrain_window_m", analysis_constants.IV_TERRAIN_WINDOW_M)
    open_pct = params.get("iv_terrain_open_pct", analysis_constants.IV_TERRAIN_OPEN_PCT)
    close_pct = params.get("iv_terrain_close_pct", analysis_constants.IV_TERRAIN_CLOSE_PCT)
    min_length_m = params.get("iv_terrain_min_length_m", analysis_constants.IV_TERRAIN_MIN_LENGTH_M)
    min_gain_m = params.get("iv_terrain_min_gain_m", analysis_constants.IV_TERRAIN_MIN_GAIN_M)
    min_net_pct = params.get("iv_terrain_min_net_pct", analysis_constants.IV_TERRAIN_MIN_NET_PCT)
    merge_gap_m = params.get("iv_terrain_merge_gap_m", analysis_constants.IV_TERRAIN_MERGE_GAP_M)
    merge_dip_m = params.get("iv_terrain_merge_dip_m", analysis_constants.IV_TERRAIN_MERGE_DIP_M)
    merge_dip_frac = params.get("iv_terrain_merge_dip_frac", analysis_constants.IV_TERRAIN_MERGE_DIP_FRAC)

    count = min(len(altitudes_m), len(speeds_ms))
    if count < 2 or window_m <= 0:
        return []
    altitudes = _smooth_altitude(
        [float(altitudes_m[i]) for i in range(count)], _window_samples(period_s, analysis_constants.IV_ELEV_MEDIAN_S)
    )
    dist: list[float] = [0.0] * count
    for i in range(1, count):
        dist[i] = dist[i - 1] + max(float(speeds_ms[i]), 0.0) * period_s
    if dist[count - 1] < min_length_m:
        return []

    grades: list[float] = []
    for i in range(count):
        # At the end of the data the window simply runs short: a climb that ends at
        # the last sample is still climbing there, so the truncated window judges it.
        j = min(bisect_left(dist, dist[i] + window_m), count - 1)
        span = dist[j] - dist[i]
        grades.append((altitudes[j] - altitudes[i]) * 100.0 / span if span > 0 else 0.0)

    runs: list[tuple[int, int]] = []
    i = 0
    while i < count:
        if grades[i] >= open_pct:
            start = i
            while i < count and grades[i] >= close_pct:
                i += 1
            runs.append((start, max(i - 1, start)))
        else:
            i += 1

    merged: list[list[int]] = []
    for run in runs:
        if merged:
            gap = dist[run[0]] - dist[merged[-1][1]]
            dip = max(0.0, altitudes[merged[-1][1]] - altitudes[run[0]])
            gained = altitudes[merged[-1][1]] - altitudes[merged[-1][0]]
            if gap <= merge_gap_m and dip <= max(merge_dip_m, merge_dip_frac * gained):
                merged[-1][1] = run[1]
                continue
        merged.append([run[0], run[1]])

    sample_grades = analysis_maths.grade_pct_series(altitudes, [float(s) for s in speeds_ms[:count]], period_s)
    climbs: list[TerrainClimb] = []
    for start, end in merged:
        length_m = dist[end] - dist[start]
        if length_m < min_length_m:
            continue
        gain_m = sum(max(0.0, altitudes[k + 1] - altitudes[k]) for k in range(start, end))
        loss_m = sum(max(0.0, altitudes[k] - altitudes[k + 1]) for k in range(start, end))
        if gain_m < min_gain_m:
            continue
        avg_grade = (altitudes[end] - altitudes[start]) * 100.0 / length_m
        if avg_grade < min_net_pct:
            continue
        window = slice(start, end + 1)
        avg_power: float | None = None
        intensity: float | None = None
        contrast: float | None = None
        score: float | None = None
        source: str | None = None
        per_min: float | None = None
        if power_w is not None and len(power_w) >= end + 1:
            avg_power = round(fmean(power_w[window]), 1)
            if threshold_w:
                intensity = round(avg_power / threshold_w, 2)
            if base_power_w and avg_power is not None:
                contrast = round(avg_power / base_power_w, 2)
        chain_score, chain_source = analysis_methods.window_stress_chain(
            list(power_w[window]) if power_w is not None and len(power_w) >= end + 1 else None,
            list(speeds_ms[window]) if speeds_ms is not None else None,
            list(hr_bpm[window]) if hr_bpm is not None and len(hr_bpm) >= end + 1 else None,
            sample_grades[window],
            period_s,
            threshold_w,
            speed_threshold_ms,
            hr_threshold_bpm,
            params,
        )
        if chain_score > 0 and chain_source is not None:
            score = round(chain_score, 1)
            source = chain_source
            per_min = round(score * 60.0 / ((end - start + 1) * period_s), 2)
        climbs.append(
            TerrainClimb(
                start_m=round(dist[start], 1),
                end_m=round(dist[end], 1),
                length_m=round(length_m, 1),
                start_s=round(start * period_s, 1),
                duration_s=round((end - start + 1) * period_s, 1),
                elevation_gain_m=round(gain_m, 1),
                elevation_loss_m=round(loss_m, 1),
                avg_grade_pct=round(avg_grade, 2),
                avg_power_w=avg_power,
                intensity_factor=intensity,
                contrast=contrast,
                rss=score,
                rss_source=source,
                rss_per_min=per_min,
            )
        )
    return climbs
