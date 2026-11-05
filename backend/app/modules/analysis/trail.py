"""Technical-terrain detection over a trail run (experimental).

The question this answers: which stretches of a route were *technical*, not
just steep? The model follows the biomechanical-anomaly idea the athlete
proposed: on smooth ground, grade alone explains the pace (Minetti's cost
polynomial over grade, the same GAP the pace-side numbers use); where the
observed pace falls short of that expectation while the stride mechanics
change - cadence becoming erratic, stride collapsing while braking on
descents - the terrain, not the gradient, is what costs.

Everything is measured over *spatial* windows (25 m by default): time windows
would over-weight the slow parts the method is trying to detect. Inputs are
sample arrays already aligned to one clock, plus cumulative ground distance,
so the arithmetic stays pure and testable.

Calibration status: the weights and the 0.38 threshold come from a first pass
against known routes and stay deliberately visible as parameters; validation
against the athlete's memory of specific sections is the open loop.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

import modules.analysis.constants as analysis_constants


@dataclass(frozen=True)
class TechnicalSection:
    """One stretch of ground the detector read as technical.

    Attributes:
        start_m: Ground position where it starts, from the start of the data.
        length_m: Ground covered by the stretch.
        grade_pct: Mean grade over it, in percent.
        avg_speed_ms: Mean ground speed through it.
        gap_deficit: How far below the grade-explained pace the section ran (0-1).
        cadence_jitter: Cadence instability over it (0-1).
        brake_score: Downhill braking share over it (0-1).
        score: Highest smoothed technicality index inside it.
        direction: ``descent`` / ``ascent`` / ``flat`` by mean grade.
        start_index: First sample index of the section (map polylines use it).
        end_index: Last sample index (exclusive).
    """

    start_m: float
    length_m: float
    grade_pct: float
    avg_speed_ms: float
    gap_deficit: float
    cadence_jitter: float
    brake_score: float
    score: float
    direction: str
    start_index: int
    end_index: int


@dataclass(frozen=True)
class TrailAnalysis:
    """What the technical pass found in one session.

    Attributes:
        sections: The technical stretches, in ground order.
        v_base_ms: The session's own flat-ground reference pace.
        cadence_available: Whether stride/cadence terms could be used at all.
        technical_m: Total metres flagged technical.
        total_m: Total ground covered.
    """

    sections: list[TechnicalSection]
    v_base_ms: float
    cadence_available: bool
    technical_m: float
    total_m: float


def _minetti_alpha(grade: np.ndarray) -> np.ndarray:
    """Minetti cost over grade relative to flat, clamped to the fitted domain."""
    g = np.clip(grade, -0.45, 0.45)
    cost = 155.4 * g**5 - 30.4 * g**4 - 43.3 * g**3 + 46.3 * g**2 + 19.5 * g + 3.6
    return np.maximum(cost / 3.6, 0.4)


def analyse_technical_sections(
    speeds_ms: Sequence[float],
    cum_dist_m: Sequence[float],
    altitude_m: Sequence[float],
    cadence_spm: Sequence[float] | None,
    *,
    window_m: float = analysis_constants.TRAIL_WINDOW_M,
    threshold: float = analysis_constants.TRAIL_THRESHOLD,
    min_length_m: float = analysis_constants.TRAIL_MIN_LENGTH_M,
    w_speed: float = analysis_constants.TRAIL_W_SPEED,
    w_cadence: float = analysis_constants.TRAIL_W_CADENCE,
    w_brake: float = analysis_constants.TRAIL_W_BRAKE,
) -> TrailAnalysis:
    """Split one session's ground into technical and ordinary stretches.

    Args:
        speeds_ms: Ground speed per sample, aligned to the other arrays.
        cum_dist_m: Cumulative ground distance per sample (same clock).
        altitude_m: Altitude per sample (the DEM-repaired stream where it exists).
        cadence_spm: Steps per minute per sample, or ``None`` when unmeasured.
        window_m: Spatial window length.
        threshold: Smoothed-index cut that marks a window technical.
        min_length_m: Shortest reported stretch.
        w_speed: Weight of the pace-anomaly term.
        w_cadence: Weight of the cadence-instability term.
        w_brake: Weight of the downhill-braking term.

    Returns:
        The :class:`TrailAnalysis` for the session.
    """
    speed = np.asarray(speeds_ms, dtype=float)
    cum = np.asarray(cum_dist_m, dtype=float)
    ele = np.asarray(altitude_m, dtype=float)
    cad = np.asarray(cadence_spm, dtype=float) if cadence_spm is not None else None
    n = len(speed)
    total = float(cum[-1]) if n else 0.0
    if n < 120 or total < window_m * 4:
        return TrailAnalysis([], 0.0, cad is not None, 0.0, total)

    moving = speed > 0.4
    bins = np.arange(0.0, total + window_m, window_m)
    nb = len(bins) - 1
    b_idx = np.clip(np.digitize(cum, bins) - 1, 0, nb - 1)

    grade = np.zeros(nb)
    v_mean = np.zeros(nb)
    cad_mean = np.zeros(nb)
    cad_std = np.zeros(nb)
    stride = np.zeros(nb)
    valid = np.zeros(nb, dtype=bool)
    first_sample = np.full(nb, -1, dtype=int)
    last_sample = np.full(nb, -1, dtype=int)

    for b in range(nb):
        mask = (b_idx == b) & moving
        if int(mask.sum()) < analysis_constants.TRAIL_MIN_SAMPLES:
            continue
        idxs = np.nonzero(mask)[0]
        valid[b] = True
        first_sample[b] = int(idxs[0])
        last_sample[b] = int(idxs[-1])
        dz = float(ele[idxs[-1]] - ele[idxs[0]])
        ds = max(float(cum[idxs[-1]] - cum[idxs[0]]), window_m * 0.5)
        grade[b] = float(np.clip(dz / ds, -0.45, 0.45))
        v_mean[b] = float(np.mean(speed[idxs]))
        if cad is not None:
            c = cad[idxs]
            c = c[~np.isnan(c) & (c > 0)]
            if c.size > 2:
                cad_mean[b] = float(np.mean(c))
                cad_std[b] = float(np.std(c))
        if cad_mean[b] > 0:
            stride[b] = v_mean[b] * 60.0 / cad_mean[b]

    if int(valid.sum()) < 8:
        return TrailAnalysis([], 0.0, cad is not None, 0.0, total)

    # Median-of-3 grade smoothing: one barometric or GPS jump must not forge a wall.
    gr = grade.copy()
    for b in range(1, nb - 1):
        vals = [grade[j] for j in (b - 1, b, b + 1) if valid[j]]
        if vals:
            gr[b] = float(np.median(vals))

    # Pauses, not terrain: near-flat and barely moving is a rest.
    resting = valid & (np.abs(gr) < 0.06) & (v_mean < analysis_constants.TRAIL_REST_V_MS)
    valid &= ~resting
    if int(valid.sum()) < 8:
        return TrailAnalysis([], 0.0, cad is not None, 0.0, total)

    v_gap = v_mean * _minetti_alpha(gr)
    runnable = valid & (np.abs(gr) < 0.06)
    if int(runnable.sum()) >= 6:
        v_base = float(np.percentile(v_gap[runnable], 75))
    else:
        v_base = float(np.percentile(v_gap[valid], 75))
    v_base = max(v_base, 2.0)

    delta_v = np.clip((v_base - v_gap) / v_base, 0.0, 1.0)
    cad_cv = np.zeros(nb)
    if cad is not None:
        cad_cv = np.clip(
            cad_std / np.maximum(cad_mean, 120.0) * analysis_constants.TRAIL_CAD_JITTER_SCALE,
            0.0,
            1.0,
        )
    brake = np.zeros(nb)
    downhill = valid & (gr < analysis_constants.TRAIL_BRAKE_GRADE) & (stride > 0)
    brake[downhill] = np.clip(
        (analysis_constants.TRAIL_BRAKE_STRIDE_M - stride[downhill]) / analysis_constants.TRAIL_BRAKE_SPAN_M,
        0.0,
        1.0,
    )

    tech = w_speed * delta_v + w_cadence * cad_cv + w_brake * brake
    tech[~valid] = 0.0
    smooth = np.convolve(tech, np.array([0.25, 0.5, 0.25]), mode="same")
    is_tech = smooth >= threshold

    sections: list[TechnicalSection] = []
    b = 0
    while b < nb:
        if is_tech[b]:
            start = b
            while b < nb and is_tech[b]:
                b += 1
            length = (b - start) * window_m
            if length >= min_length_m:
                mg = float(np.mean(gr[start:b]))
                sections.append(
                    TechnicalSection(
                        start_m=bins[start],
                        length_m=length,
                        grade_pct=round(mg * 100.0, 1),
                        avg_speed_ms=round(float(np.mean(v_mean[start:b])), 2),
                        gap_deficit=round(float(np.mean(delta_v[start:b])), 2),
                        cadence_jitter=round(float(np.mean(cad_cv[start:b])), 2),
                        brake_score=round(float(np.mean(brake[start:b])), 2),
                        score=round(float(np.max(smooth[start:b])), 2),
                        direction="descent" if mg < -0.05 else ("ascent" if mg > 0.05 else "flat"),
                        start_index=int(first_sample[start]),
                        end_index=int(last_sample[b - 1]) + 1,
                    )
                )
        else:
            b += 1

    return TrailAnalysis(
        sections,
        round(v_base, 2),
        cad is not None,
        float(is_tech.sum()) * window_m,
        total,
    )
