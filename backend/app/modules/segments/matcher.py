"""Geometric matching of an activity's GPS trace against one segment.

Ported from the validated Fase-1 POC (``devscripts/strava-segments/match_activity.py``),
reworked to *continuous arc-length* matching: every decision the matcher makes is
expressed in metres and seconds along the polyline, never in vertex counts, so the
result is identical whether the stored geometry carries one vertex every 2 m or
every 20 m.

The algorithm:

1. Project both polylines onto a local metric grid (equirectangular around the
   segment's mean point) and compute, for every activity waypoint, its nearest
   *point on the polyline* (clamped perpendicular projection onto the nearest
   edge): the along-track abscissa ``s`` in metres and the distance to it.
2. Keep the waypoints within :data:`MATCH_RADIUS_M` and split them into
   *passes*: a pass ends at an implausible jump of ``s`` between consecutive
   candidates (:data:`NOISE_JUMP_M`: the trace teleported along the segment),
   at a sustained retreat of the smoothed abscissa (:data:`DEC_REVERSAL_M`: the
   trace turned around), or at a time gap (the activity left and came back).
3. Accept a pass as a real traverse when it extends over at least
   :data:`COVERAGE_MIN` of the segment's arc length, classify its direction by
   the majority sign of the smoothed abscissa steps, and bracket it on the pass's
   own extremes: from the first waypoint at the near end of the segment to the
   first later waypoint reaching the far end the athlete actually attained. When
   the trace passes within :data:`ENDPOINT_ANCHOR_M` of the geometry's endpoints,
   the bracket anchors on those endpoints instead.
4. Reject implausibly fast passes (:data:`MAX_SPEED_MPS`).

Pass splitting works on *positions within the candidate list*, never on
activity indices — the POC's subtlest bug, kept straight here.

:func:`audit_segment_match` runs the same core and reports *why* a segment
matched or did not (outcome, candidate count, best span and coverage), which is
what the activity debug view reads.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import datetime

import numpy as np

import modules.segments.constants as segments_constants
import modules.segments.contracts as segments_contracts

# Matcher tuning constants. All length thresholds are metres along the segment,
# so geometry vertex spacing cannot change the outcome (validated on the
# Capdella/Pontet pair: a 1.9 m-step and a 6.9 m-step polyline of the same road
# must return the same efforts).
MATCH_RADIUS_M = 30.0  # max distance from the polyline to count as coincident
COVERAGE_MIN = 0.90  # min fraction of the segment's arc length a pass must extend over
DIRECTION_TOL = 0.90  # min fraction of steps with a growing abscissa for "forward"
MAX_SPEED_MPS = 20.0  # a pass faster than this is noise, not an effort
DEC_REVERSAL_M = 40.0  # smoothed retreat along the segment that means the trace turned around
SMOOTH_W = 13  # moving-average window (candidates, i.e. activity sample points)
NOISE_JUMP_M = 100.0  # implausible along-segment abscissa jump between candidates: a discontinuity
GAP_SEC = 75  # time gap between coincident waypoints that closes a pass
MIN_PASS_POINTS = 3  # fewer candidates than this cannot be a traverse
ENDPOINT_ANCHOR_M = 10.0  # bracket on the geometry's endpoints when the trace passes this close
BRACKET_TOL_M = 2.0  # within this along-track distance of a pass extreme counts as "at" it

_PROJ_CHUNK = 192  # waypoints per projection batch (bounds peak memory on long geometries)
_DEG_TO_M = 111_320.0  # metres per degree of latitude (equirectangular approximation)


def _project(lat: np.ndarray, lng: np.ndarray, ref_lat: float, ref_lng: float) -> tuple[np.ndarray, np.ndarray]:
    """Return local metric coordinates (equirectangular around the reference).

    Args:
        lat: Latitudes in degrees.
        lng: Longitudes in degrees.
        ref_lat: Reference latitude in degrees.
        ref_lng: Reference longitude in degrees.

    Returns:
        ``(x, y)`` metres east/north of the reference point.
    """
    x = (lng - ref_lng) * _DEG_TO_M * math.cos(math.radians(ref_lat))
    y = (lat - ref_lat) * _DEG_TO_M
    return x, y


def _moving_avg(values: np.ndarray, window: int) -> np.ndarray:
    """Return the centred moving average, same length as ``values``.

    Args:
        values: The series to smooth.
        window: Odd window length.

    Returns:
        The smoothed series.
    """
    pad = window // 2
    padded = np.pad(values.astype(float), pad, mode="edge")
    return np.convolve(padded, np.ones(window) / window, mode="valid")


def _arc_positions(
    act_x: np.ndarray,
    act_y: np.ndarray,
    seg_x: np.ndarray,
    seg_y: np.ndarray,
    cum_m: np.ndarray,
    edge_len_m: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Project every activity waypoint onto the segment polyline.

    Each waypoint maps to the nearest point of the polyline (clamped
    perpendicular projection over every edge), which yields its abscissa along
    the polyline and its distance to it — both in metres.

    Args:
        act_x: Waypoint eastings in the local metric grid.
        act_y: Waypoint northings in the local metric grid.
        seg_x: Segment vertex eastings in the same grid.
        seg_y: Segment vertex northings in the same grid.
        cum_m: Cumulative arc length at each segment vertex (metres, starts at 0).
        edge_len_m: Length of each polyline edge (metres, ``len(cum_m) - 1``).

    Returns:
        ``(s_m, dist_m)``: along-track abscissa and distance, per waypoint.
    """
    n_act = len(act_x)
    s = np.empty(n_act, dtype=float)
    dist = np.empty(n_act, dtype=float)

    base_x, base_y = seg_x[:-1], seg_y[:-1]
    edge_x, edge_y = seg_x[1:] - base_x, seg_y[1:] - base_y
    edge_len2 = np.maximum(edge_x * edge_x + edge_y * edge_y, 1e-9)

    for i0 in range(0, n_act, _PROJ_CHUNK):
        px = act_x[i0 : i0 + _PROJ_CHUNK, None] - base_x[None, :]
        py = act_y[i0 : i0 + _PROJ_CHUNK, None] - base_y[None, :]
        t = np.clip((px * edge_x[None, :] + py * edge_y[None, :]) / edge_len2[None, :], 0.0, 1.0)
        qx = px - t * edge_x[None, :]
        qy = py - t * edge_y[None, :]
        dist2 = qx * qx + qy * qy
        k = np.argmin(dist2, axis=1)
        t_best = t[np.arange(len(k)), k]
        s[i0 : i0 + _PROJ_CHUNK] = cum_m[k] + t_best * edge_len_m[k]
        dist[i0 : i0 + _PROJ_CHUNK] = np.sqrt(dist2[np.arange(len(k)), k])
    return s, dist


def _split_passes(cand: np.ndarray, s: np.ndarray, epoch: np.ndarray) -> list[list[int]]:
    """Split the coincident waypoints (in temporal order) into separate passes.

    ``cand`` are the activity waypoint indices of the coincident waypoints and
    ``s`` their along-track abscissa in metres, both in temporal order; ``epoch``
    is the activity's waypoint times in seconds. A pass closes when:

    - the abscissa jumps implausibly between consecutive candidates
      (:data:`NOISE_JUMP_M`: a discontinuity, a different stretch of the segment),
    - the smoothed abscissa retreats :data:`DEC_REVERSAL_M` metres from where it
      was when the decline started — the activity turned around and comes back
      along the trace (the local maximum closes the current pass and starts the
      next one; a pass on its way down is not closed again until the abscissa
      advances),
    - there is a time gap of :data:`GAP_SEC` (the activity left and returned).

    Args:
        cand: Activity waypoint indices of the coincident waypoints.
        s: Along-track abscissa in metres per coincident waypoint.
        epoch: Waypoint times in epoch seconds, indexed like ``cand``.

    Returns:
        Passes as lists of POSITIONS within ``cand``/``s`` (not activity indices).
    """
    s_sm = _moving_avg(s, SMOOTH_W)
    passes: list[list[int]] = []
    current: list[int] = []
    last_t = -1e18
    prev_raw = 0.0
    prev_sm = 0.0
    rev_at: int | None = None
    rev_peak = 0.0
    in_decline = False
    for pos, (idx, sv, ssv) in enumerate(zip(cand, s, s_sm, strict=True)):
        idx = int(idx)
        if not current:
            current = [pos]
            prev_raw, prev_sm = float(sv), float(ssv)
            last_t = float(epoch[idx])
            continue
        d_raw = float(sv) - prev_raw
        d_sm = float(ssv) - prev_sm
        if abs(d_raw) > NOISE_JUMP_M:
            # discontinuity: the current pass ends and this point starts a new one
            if len(current) >= MIN_PASS_POINTS:
                passes.append(current)
            current = [pos]
            rev_at, in_decline = None, False
        else:
            reversal = False
            if d_sm < 0:
                if rev_at is None and not in_decline:
                    rev_at = len(current) - 1  # last position before the retreat started
                    rev_peak = prev_sm
                reversal = rev_at is not None and not in_decline and rev_peak - float(ssv) >= DEC_REVERSAL_M
            else:
                rev_at = None
                in_decline = False
            gap = float(epoch[idx]) - last_t > GAP_SEC
            if reversal:
                cut = rev_at + 1
                head = current[:cut]
                tail = current[cut:] or [current[cut - 1]]  # the local maximum starts the next pass
                in_decline = True
            elif gap:
                head, tail = current, [pos]
            else:
                head, tail = None, None
            if reversal or gap:
                if head is not None and len(head) >= MIN_PASS_POINTS:
                    passes.append(head)
                current = tail
                rev_at = None
            else:
                current.append(pos)
        prev_raw, prev_sm = float(sv), float(ssv)
        last_t = float(epoch[idx])
    if len(current) >= MIN_PASS_POINTS:
        passes.append(current)
    return passes


def _waypoint_arrays(
    waypoints: Sequence[Mapping[str, object]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[datetime]]:
    """Extract lat/lng arrays and epoch seconds from stream waypoints.

    Args:
        waypoints: Map-stream waypoints (``{"lat", "lon", "time"}`` dicts).

    Returns:
        ``(lat, lng, epoch_seconds, times)`` with all four aligned.
    """
    lat = np.array([float(wp["lat"]) for wp in waypoints])  # type: ignore[arg-type]
    lng = np.array([float(wp["lon"]) for wp in waypoints])  # type: ignore[arg-type]
    times = [_parse_time(wp["time"]) for wp in waypoints]
    epoch = np.array([t.timestamp() for t in times])
    return lat, lng, epoch, times


def _parse_time(value: object) -> datetime:
    """Parse a stream waypoint time (ISO string, or already a datetime).

    Args:
        value: The raw waypoint time.

    Returns:
        The parsed naive datetime, as stored by the stream.
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _touched_vertex_frac(cand_x: np.ndarray, cand_y: np.ndarray, seg_x: np.ndarray, seg_y: np.ndarray) -> float:
    """Return the fraction of segment vertices lying near the pass's waypoints.

    A vertex counts as touched when some waypoint of the pass is within
    :data:`MATCH_RADIUS_M` of it. This measures how much of the drawn segment
    the athlete actually ran over, and it does not care how many vertices the
    drawing used.

    Args:
        cand_x: Pass waypoint eastings in the local metric grid.
        cand_y: Pass waypoint northings in the local metric grid.
        seg_x: Segment vertex eastings in the same grid.
        seg_y: Segment vertex northings in the same grid.

    Returns:
        Touched vertices over total vertices, between 0 and 1.
    """
    touched = np.zeros(len(seg_x), dtype=bool)
    r2 = MATCH_RADIUS_M * MATCH_RADIUS_M
    for i0 in range(0, len(cand_x), _PROJ_CHUNK):
        dx = cand_x[i0 : i0 + _PROJ_CHUNK, None] - seg_x[None, :]
        dy = cand_y[i0 : i0 + _PROJ_CHUNK, None] - seg_y[None, :]
        touched |= (dx * dx + dy * dy <= r2).any(axis=0)
    return float(touched.mean())


def _match_core(
    segment_id: int,
    geometry: Sequence[Sequence[float]],
    segment_length_m: float,
    waypoints: Sequence[Mapping[str, object]],
) -> tuple[list[segments_contracts.SegmentMatch], segments_contracts.SegmentMatchAudit]:
    """Match one activity against one segment, with the audit trail of the decision.

    Args:
        segment_id: Identifier stamped on every returned match.
        geometry: Ordered ``[[lat, lng], ...]`` segment polyline.
        segment_length_m: Segment length used for the plausibility filter.
        waypoints: The activity's map-stream waypoints (``{"lat", "lon", "time"}``).

    Returns:
        ``(matches, audit)``: the matching passes (chronological, numbered per
        direction) and the verdict with the statistics behind it.
    """

    def audit(
        outcome: segments_constants.MatchOutcome,
        *,
        candidates: int = 0,
        accepted: int = 0,
        rejected: int = 0,
        best_span: float = 0.0,
        best_coverage: float = 0.0,
    ) -> segments_contracts.SegmentMatchAudit:
        return segments_contracts.SegmentMatchAudit(
            segment_id=segment_id,
            outcome=outcome,
            candidate_count=candidates,
            accepted_pass_count=accepted,
            rejected_pass_count=rejected,
            best_span_frac=round(best_span, 3),
            best_coverage=round(best_coverage, 3),
        )

    if len(geometry) < 2:
        return [], audit(segments_constants.MatchOutcome.NO_GEOMETRY)
    if len(waypoints) < MIN_PASS_POINTS:
        return [], audit(segments_constants.MatchOutcome.NO_WAYPOINTS)

    seg = np.asarray(geometry, dtype=float)
    seg_lat, seg_lng = seg[:, 0], seg[:, 1]
    act_lat, act_lng, epoch, times = _waypoint_arrays(waypoints)

    # Bounding-box pre-filter (radius-padded) before any distance work.
    pad = MATCH_RADIUS_M / _DEG_TO_M
    cos_ref = math.cos(math.radians(float(seg_lat.mean())))
    if (
        act_lat.max() < seg_lat.min() - pad
        or act_lat.min() > seg_lat.max() + pad
        or act_lng.max() < seg_lng.min() - pad / cos_ref
        or act_lng.min() > seg_lng.max() + pad / cos_ref
    ):
        return [], audit(segments_constants.MatchOutcome.BBOX_FAR)

    ref_lat, ref_lng = float(seg_lat.mean()), float(seg_lng.mean())
    seg_x, seg_y = _project(seg_lat, seg_lng, ref_lat, ref_lng)
    act_x, act_y = _project(act_lat, act_lng, ref_lat, ref_lng)

    edge_lens = np.hypot(np.diff(seg_x), np.diff(seg_y))
    cum = np.concatenate(([0.0], np.cumsum(edge_lens)))
    arc_len_m = float(cum[-1])

    # Along-track abscissa and polyline distance for every activity waypoint.
    s_all, d_all = _arc_positions(act_x, act_y, seg_x, seg_y, cum, edge_lens)

    cand = np.nonzero(d_all <= MATCH_RADIUS_M)[0]
    if cand.size == 0:
        return [], audit(segments_constants.MatchOutcome.NO_CANDIDATES)
    s = s_all[cand]
    # Distance of every candidate to the geometry's first and last point: what the
    # endpoint anchoring below reads.
    d_first = np.hypot(act_x[cand] - seg_x[0], act_y[cand] - seg_y[0])
    d_last = np.hypot(act_x[cand] - seg_x[-1], act_y[cand] - seg_y[-1])

    passes = _split_passes(cand, s, epoch)
    cand_x, cand_y = act_x[cand], act_y[cand]
    matches: list[segments_contracts.SegmentMatch] = []
    pass_counts: dict[segments_constants.SegmentDirection, int] = {}
    best_span = 0.0
    best_coverage = 0.0
    rejected = 0
    for positions in passes:
        pos = np.asarray(positions)
        s_pass = s[pos]
        act_idx = cand[pos]
        s_sm_pass = _moving_avg(s_pass, SMOOTH_W)

        s_near, s_far = float(s_pass.min()), float(s_pass.max())
        span_frac = (s_far - s_near) / max(arc_len_m, 1e-9)
        covered = _touched_vertex_frac(cand_x[pos], cand_y[pos], seg_x, seg_y)
        best_span = max(best_span, span_frac)
        best_coverage = max(best_coverage, covered)

        # Span criterion: the pass must extend over at least COVERAGE_MIN of the
        # segment (GPS holes do not cancel a traverse, but partial turns do).
        if span_frac < COVERAGE_MIN:
            rejected += 1
            continue

        steps = np.diff(s_sm_pass)
        forward_frac = float((steps > 0).mean()) if steps.size else 0.0
        direction = (
            segments_constants.SegmentDirection.FORWARD
            if forward_frac >= DIRECTION_TOL
            else segments_constants.SegmentDirection.REVERSE
        )

        # Bracket inside the pass: from the first waypoint at the end of the
        # segment the athlete entered from, to the first later waypoint reaching
        # the farthest point along the segment they attained. The pass's own
        # extremes, not fixed fractions of the stored geometry: a traverse that
        # stops short of the polyline's last metres still gets the time it really
        # took, which is how the elapsed reads against the sources that also
        # report it. Measuring from the wrong edge would understate the elapsed
        # time and trip the plausibility filter below.
        near_is_min = direction is segments_constants.SegmentDirection.FORWARD
        near_s, far_s = (s_near, s_far) if near_is_min else (s_far, s_near)
        near_sel = np.abs(s_pass - near_s) <= BRACKET_TOL_M
        far_sel = np.abs(s_pass - far_s) <= BRACKET_TOL_M
        i_start = int(act_idx[near_sel].min())
        exit_sel = pos[far_sel & (act_idx > i_start)]
        i_end = int(cand[exit_sel].min()) if exit_sel.size else int(cand[pos[-1]])

        # Anchor the bracket on the geometry's endpoints when the trace clearly
        # runs over them: the corridor edge (:data:`MATCH_RADIUS_M`) is 30 m wide,
        # so a waypoint taken as the entry can sit tens of metres short of the
        # start, which inflates the elapsed by ~10 s on a slow traverse and stops
        # it agreeing with the elapsed other sources report for the same geometry.
        # A pass that never comes that close to an endpoint (canopy, a geometry
        # that overshoots the path) keeps the extremes above.
        entry_near, exit_near = (d_first, d_last) if near_is_min else (d_last, d_first)
        anchored_entry = pos[entry_near[pos] <= ENDPOINT_ANCHOR_M]
        if anchored_entry.size:
            anchored_start = int(cand[anchored_entry.min()])
            anchored_exit = pos[(exit_near[pos] <= ENDPOINT_ANCHOR_M) & (act_idx > anchored_start)]
            if anchored_exit.size:
                i_start, i_end = anchored_start, int(cand[anchored_exit.min()])

        t_start, t_end = times[i_start], times[i_end]
        dt = (t_end - t_start).total_seconds()
        avg_speed = segment_length_m / dt if dt > 0 and segment_length_m > 0 else None
        if dt <= 0 or (avg_speed is not None and avg_speed > MAX_SPEED_MPS):
            rejected += 1
            continue  # zero/negative time or impossibly fast: noise, never a real effort

        pass_counts[direction] = pass_counts.get(direction, 0) + 1
        matches.append(
            segments_contracts.SegmentMatch(
                segment_id=segment_id,
                direction=direction,
                pass_number=pass_counts[direction],
                elapsed_seconds=int(dt),
                avg_speed_mps=round(avg_speed, 3) if avg_speed is not None else None,
                coverage=round(covered, 3),
                span=round(span_frac, 3),
                time_start=t_start,
                time_end=t_end,
                stream_index_start=i_start,
                stream_index_end=i_end,
            )
        )

    if matches:
        outcome = segments_constants.MatchOutcome.MATCHED
    elif passes and best_span >= COVERAGE_MIN:
        outcome = segments_constants.MatchOutcome.NO_PLAUSIBLE
    elif passes:
        outcome = segments_constants.MatchOutcome.SPAN_SHORT
    else:
        outcome = segments_constants.MatchOutcome.NO_PASSES
    return matches, audit(
        outcome,
        candidates=int(cand.size),
        accepted=len(matches),
        rejected=rejected,
        best_span=best_span,
        best_coverage=best_coverage,
    )


def match_segment_passes(
    segment_id: int,
    geometry: Sequence[Sequence[float]],
    segment_length_m: float,
    waypoints: Sequence[Mapping[str, object]],
) -> list[segments_contracts.SegmentMatch]:
    """Return every matching pass of one activity over one segment.

    A segment whose bounding box cannot reach the activity's within
    :data:`MATCH_RADIUS_M` is rejected outright. Otherwise each waypoint is
    projected onto the geometry and the passes are split, scored and bracketed
    as described in the module docstring.

    Args:
        segment_id: Identifier stamped on every returned match.
        geometry: Ordered ``[[lat, lng], ...]`` segment polyline.
        segment_length_m: Segment length used for the plausibility filter.
        waypoints: The activity's map-stream waypoints (``{"lat", "lon", "time"}``).

    Returns:
        The matching passes, in chronological order, numbered per direction.
    """
    matches, _ = _match_core(segment_id, geometry, segment_length_m, waypoints)
    return matches


def audit_segment_match(
    segment_id: int,
    geometry: Sequence[Sequence[float]],
    segment_length_m: float,
    waypoints: Sequence[Mapping[str, object]],
) -> segments_contracts.SegmentMatchAudit:
    """Return the matcher's verdict for one activity against one segment.

    Same computation as :func:`match_segment_passes`; the audit reports the
    outcome (matched or why it was rejected), the candidate count and the best
    span and coverage observed over the pass fragments.

    Args:
        segment_id: Segment identifier to report.
        geometry: Ordered ``[[lat, lng], ...]`` segment polyline.
        segment_length_m: Segment length used for the plausibility filter.
        waypoints: The activity's map-stream waypoints (``{"lat", "lon", "time"}``).

    Returns:
        The verdict with the statistics behind it.
    """
    _, audit_result = _match_core(segment_id, geometry, segment_length_m, waypoints)
    return audit_result


def polyline_length_m(geometry: Sequence[Sequence[float]]) -> float:
    """Return the length of a polyline in meters.

    Args:
        geometry: Ordered ``[[lat, lng], ...]`` polyline.

    Returns:
        The along-polyline length, 0 for fewer than two points.
    """
    if len(geometry) < 2:
        return 0.0
    pts = np.asarray(geometry, dtype=float)
    x, y = _project(pts[:, 0], pts[:, 1], float(pts[:, 0].mean()), float(pts[:, 1].mean()))
    return float(np.hypot(np.diff(x), np.diff(y)).sum())


def elevation_gain_m(altitudes: Sequence[float]) -> float:
    """Return the accumulated climb of an altitude series.

    Args:
        altitudes: The altitudes, in order, possibly empty.

    Returns:
        The sum of positive successive differences, in meters.
    """
    if not altitudes:
        return 0.0
    a = np.asarray(altitudes, dtype=float)
    steps = np.diff(a)
    return float(steps[steps > 0].sum())


def elevation_loss_m(altitudes: Sequence[float]) -> float:
    """Return the accumulated descent of an altitude series.

    Args:
        altitudes: The altitudes, in order, possibly empty.

    Returns:
        The sum of negative successive differences, in meters.
    """
    if not altitudes:
        return 0.0
    a = np.asarray(altitudes, dtype=float)
    steps = np.diff(a)
    return float(-steps[steps < 0].sum())
