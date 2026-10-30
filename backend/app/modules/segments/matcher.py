"""Geometric matching of an activity's GPS trace against one segment.

Ported from the validated Fase-1 POC (``devscripts/strava-segments/match_activity.py``).
The algorithm:

1. Project both polylines onto a local metric grid (equirectangular around the
   segment's mean point) and compute, for every activity waypoint, the index of
   its nearest segment point and that distance.
2. Keep the waypoints within :data:`MATCH_RADIUS_M` and split them into
   *passes*: a pass ends at a physically implausible jump of the nearest-point
   index, at a sustained reversal of the smoothed index (the trace turned
   around), or at a time gap (the activity left and came back).
3. Accept a pass as a real traverse when it covers at least
   :data:`COVERAGE_MIN` of the segment's index span, classify its direction by
   the majority sign of the smoothed index steps, and bracket it on the pass's own
   extremes: from the first waypoint at the near end of the segment to the first
   later waypoint reaching the far end the athlete actually attained. When the
   trace passes within :data:`ENDPOINT_ANCHOR_M` of the geometry's endpoints, the
   bracket anchors on those endpoints instead.
4. Reject implausibly fast passes (:data:`MAX_SPEED_MPS`).

Pass splitting works on *positions within the candidate list*, never on
activity indices — the POC's subtlest bug, kept straight here.
"""

import itertools
import math
from collections.abc import Mapping, Sequence
from datetime import datetime

import numpy as np

import modules.segments.constants as segments_constants
import modules.segments.contracts as segments_contracts

# Matcher tuning constants, validated against real activities in Fase 1.
MATCH_RADIUS_M = 30.0  # max distance from a segment point to count as coincident
COVERAGE_MIN = 0.90  # min fraction of the segment's index span a pass must extend over
DIRECTION_TOL = 0.90  # min fraction of steps with a growing segment index for "forward"
MAX_SPEED_MPS = 20.0  # a pass faster than this is noise, not an effort
DEC_REVERSAL = 5  # consecutive smoothed-index decreases that mean the trace turned around
SMOOTH_W = 13  # moving-average window for the segment index
NOISE_JUMP = 10  # implausible nearest-point index jump between candidates: a discontinuity
GAP_SEC = 75  # time gap between coincident waypoints that closes a pass
MIN_PASS_POINTS = 3  # fewer candidates than this cannot be a traverse
ENDPOINT_ANCHOR_M = 10.0  # bracket on the geometry's endpoints when the trace passes this close

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


def _split_passes(cand: np.ndarray, js: np.ndarray, epoch: np.ndarray) -> list[list[int]]:
    """Split the coincident waypoints (in temporal order) into separate passes.

    ``cand`` are the activity waypoint indices of the coincident waypoints and
    ``js`` the nearest segment-point index of each, both in temporal order;
    ``epoch`` is the activity's waypoint times in seconds. A pass closes when:

    - the nearest-point index jumps implausibly between consecutive candidates
      (noise corresponding to a different stretch of the segment),
    - the smoothed index retreats :data:`DEC_REVERSAL` consecutive steps — the
      activity turned around and comes back along the trace (the local maximum
      closes the current pass and starts the next one; a pass on its way down
      is not closed again until the index advances),
    - there is a time gap of :data:`GAP_SEC` (the activity left and returned).

    Args:
        cand: Activity waypoint indices of the coincident waypoints.
        js: Nearest segment-point index per coincident waypoint.
        epoch: Waypoint times in epoch seconds, indexed like ``cand``.

    Returns:
        Passes as lists of POSITIONS within ``cand``/``js`` (not activity indices).
    """
    j_sm = _moving_avg(js, SMOOTH_W)
    passes: list[list[int]] = []
    current: list[int] = []
    last_t = -1e18
    prev_raw = -1
    prev_sm = -1e18
    dec_run = 0
    rev_at: int | None = None
    in_decline = False
    for pos, (idx, jv, jsv) in enumerate(zip(cand, js, j_sm, strict=True)):
        j_raw, idx = int(jv), int(idx)
        if not current:
            current = [pos]
            prev_raw, prev_sm = j_raw, float(jsv)
            last_t = float(epoch[idx])
            continue
        dj_raw = j_raw - prev_raw
        dj_sm = float(jsv) - prev_sm
        if abs(dj_raw) > NOISE_JUMP:
            # discontinuity: the current pass ends and this point starts a new one
            if len(current) >= MIN_PASS_POINTS:
                passes.append(current)
            current = [pos]
            dec_run, rev_at, in_decline = 0, None, False
        else:
            if dj_sm < 0:
                dec_run += 1
                if rev_at is None and not in_decline:
                    rev_at = len(current) - 1  # last position before the retreat started
            else:
                dec_run = 0
                rev_at = None
                in_decline = False
            turns = dj_sm < 0 and dec_run >= DEC_REVERSAL and not in_decline
            gap = float(epoch[idx]) - last_t > GAP_SEC
            if turns:
                cut = (rev_at + 1) if rev_at is not None else len(current)
                head = current[:cut]
                tail = current[cut:] or [current[cut - 1]]  # the local maximum starts the next pass
                in_decline = True
            elif gap:
                head, tail = current, [pos]
            else:
                head, tail = None, None
            if turns or gap:
                if head is not None and len(head) >= MIN_PASS_POINTS:
                    passes.append(head)
                current = tail
                dec_run, rev_at = 0, None
            else:
                current.append(pos)
        prev_raw, prev_sm = j_raw, float(jsv)
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


def match_segment_passes(
    segment_id: int,
    geometry: Sequence[Sequence[float]],
    segment_length_m: float,
    waypoints: Sequence[Mapping[str, object]],
) -> list[segments_contracts.SegmentMatch]:
    """Return every matching pass of one activity over one segment.

    A segment whose bounding box cannot reach the activity's within
    :data:`MATCH_RADIUS_M` is rejected outright. Otherwise each waypoint is
    matched against the geometry and the passes are split, scored and bracketed
    as described in the module docstring.

    Args:
        segment_id: Identifier stamped on every returned match.
        geometry: Ordered ``[[lat, lng], ...]`` segment polyline.
        segment_length_m: Segment length used for the plausibility filter.
        waypoints: The activity's map-stream waypoints (``{"lat", "lon", "time"}``).

    Returns:
        The matching passes, in chronological order, numbered per direction.
    """
    if len(geometry) < 2 or len(waypoints) < MIN_PASS_POINTS:
        return []

    seg = np.asarray(geometry, dtype=float)
    seg_lat, seg_lng = seg[:, 0], seg[:, 1]
    act_lat, act_lng, epoch, times = _waypoint_arrays(waypoints)

    # Bounding-box pre-filter (radius-padded) before any distance work.
    pad = MATCH_RADIUS_M / _DEG_TO_M
    if (
        act_lat.max() < seg_lat.min() - pad
        or act_lat.min() > seg_lat.max() + pad
        or act_lng.max() < seg_lng.min() - pad / math.cos(math.radians(float(seg_lat.mean())))
        or act_lng.min() > seg_lng.max() + pad / math.cos(math.radians(float(seg_lat.mean())))
    ):
        return []

    ref_lat, ref_lng = float(seg_lat.mean()), float(seg_lng.mean())
    seg_x, seg_y = _project(seg_lat, seg_lng, ref_lat, ref_lng)
    act_x, act_y = _project(act_lat, act_lng, ref_lat, ref_lng)

    n_seg = len(seg_x)
    n_act = len(act_x)
    jmin = np.empty(n_act, dtype=np.int64)
    dmin = np.empty(n_act, dtype=float)

    # Nearest segment point and distance for every activity waypoint.
    chunk = 512
    for i0 in range(0, n_act, chunk):
        dx = act_x[i0 : i0 + chunk, None] - seg_x[None, :]
        dy = act_y[i0 : i0 + chunk, None] - seg_y[None, :]
        dist2 = dx * dx + dy * dy
        jmin[i0 : i0 + chunk] = np.argmin(dist2, axis=1)
        dmin[i0 : i0 + chunk] = np.sqrt(dist2.min(axis=1))

    cand = np.nonzero(dmin <= MATCH_RADIUS_M)[0]
    if cand.size == 0:
        return []
    js = jmin[cand]
    # Distance of every candidate to the geometry's first and last point: what the
    # endpoint anchoring below reads.
    d_first = np.hypot(act_x[cand] - seg_x[0], act_y[cand] - seg_y[0])
    d_last = np.hypot(act_x[cand] - seg_x[-1], act_y[cand] - seg_y[-1])

    passes = _split_passes(cand, js, epoch)
    matches: list[segments_contracts.SegmentMatch] = []
    pass_counts: dict[segments_constants.SegmentDirection, int] = {}
    for positions in passes:
        pos = np.asarray(positions)
        j_pass = js[pos]
        act_idx = cand[pos]
        j_sm_pass = _moving_avg(j_pass, SMOOTH_W)

        # Span criterion: the pass must extend over at least COVERAGE_MIN of the
        # segment (GPS holes do not cancel a traverse, but partial turns do).
        span_frac = (int(j_pass.max()) - int(j_pass.min())) / max(1, n_seg - 1)
        if span_frac < COVERAGE_MIN:
            continue
        covered = np.unique(j_pass).size / n_seg

        steps = np.diff(j_sm_pass)
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
        near_j = int(j_pass.min()) if near_is_min else int(j_pass.max())
        far_j = int(j_pass.max()) if near_is_min else int(j_pass.min())
        i_start = int(cand[pos[j_pass == near_j]].min())
        exit_sel = pos[(act_idx > i_start) & (j_pass == far_j)]
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
        if dt <= 0:
            continue
        avg_speed = segment_length_m / dt if segment_length_m > 0 else None
        if avg_speed is not None and avg_speed > MAX_SPEED_MPS:
            continue  # impossibly fast: noise, never a real effort

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
    return matches


def polyline_length_m(geometry: Sequence[Sequence[float]]) -> float:
    """Return the length of a polyline in meters.

    Uses the same equirectangular approximation as the matcher's projections:
    the error over any real segment stays far below :data:`MATCH_RADIUS_M`.

    Args:
        geometry: Ordered ``[[lat, lng], ...]`` points.

    Returns:
        The summed segment lengths, ``0.0`` for fewer than two points.
    """
    total = 0.0
    for (lat1, lng1), (lat2, lng2) in itertools.pairwise(geometry):
        ref_lat = math.radians((lat1 + lat2) / 2)
        d_lat = (lat2 - lat1) * _DEG_TO_M
        d_lng = (lng2 - lng1) * _DEG_TO_M * math.cos(ref_lat)
        total += math.hypot(d_lat, d_lng)
    return total


def elevation_gain_m(altitudes: Sequence[float]) -> float:
    """Return the accumulated positive altitude change over a sequence.

    Args:
        altitudes: One altitude per geometry point, in source order.

    Returns:
        The sum of the positive consecutive differences, ``0.0`` for fewer
        than two samples.
    """
    return sum(max(later - earlier, 0.0) for earlier, later in itertools.pairwise(altitudes))
