"""Projecting stored segment efforts onto the athlete's own track and ground.

The segment engine knows an effort happened; these helpers say *where* - the
offsets along the segment's geometry and along the activity's track that the
pass covered, plus the mean signed grade from the athlete's own elevation
stream. No cartography decides anything here: segment geometry gives the
along-axis, the track gives the ground position, the elevation stream gives
the honesty of calling the pass a descent. Pure functions; the queries live
in the crud.
"""

import math
from itertools import pairwise

import modules.analysis.constants as analysis_constants
import modules.analysis.graph as analysis_graph


def polyline_xy(geometry: list) -> list[tuple[float, float]]:
    """Segment geometry (lat/lng pairs) in the shared metric frame."""
    return [analysis_graph.to_xy(float(pt[0]), float(pt[1])) for pt in geometry]


def cumulative_offsets(points_xy: list[tuple[float, float]]) -> list[float]:
    """Along-line offsets of a polyline's vertices, metres."""
    offsets = [0.0]
    for a, b in pairwise(points_xy):
        offsets.append(offsets[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return offsets


def project_with_distance(
    xy: tuple[float, float], line_xy: list[tuple[float, float]], offsets: list[float]
) -> tuple[float, float]:
    """Along-line offset and distance of the polyline point nearest to a position."""
    best = 0.0
    best_d2 = float("inf")
    for k in range(len(line_xy) - 1):
        ax, ay = line_xy[k]
        dx, dy = line_xy[k + 1][0] - ax, line_xy[k + 1][1] - ay
        seg2 = dx * dx + dy * dy
        if seg2 <= 1e-9:
            continue
        tt = max(0.0, min(1.0, ((xy[0] - ax) * dx + (xy[1] - ay) * dy) / seg2))
        px, py = ax + tt * dx, ay + tt * dy
        d2 = (xy[0] - px) ** 2 + (xy[1] - py) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = offsets[k] + tt * math.sqrt(seg2)
    return best, math.sqrt(best_d2)


def project_along(xy: tuple[float, float], line_xy: list[tuple[float, float]], offsets: list[float]) -> float:
    """Along-line offset of the point on a polyline nearest to a position."""
    return project_with_distance(xy, line_xy, offsets)[0]


def effort_along_range(
    track_xy: list[tuple[float, float]],
    seg_xy: list[tuple[float, float]],
    seg_offsets: list[float],
    index_start: int,
    index_end: int,
) -> tuple[float, float]:
    """The along-segment span a stream-index range of the track swept.

    Sampling the traversed points keeps the cost sane on dense tracks; the span
    is the min/max of the projections, which is the honest answer whether the
    pass ran forward or reverse along the segment.
    """
    count = max(index_end - index_start + 1, 1)
    stride = max(1, count // 400)
    along = [
        project_along(track_xy[k], seg_xy, seg_offsets)
        for k in range(index_start, min(index_end, len(track_xy) - 1) + 1, stride)
    ]
    if not along:
        along = [project_along(track_xy[min(index_start, len(track_xy) - 1)], seg_xy, seg_offsets)]
    return min(along), max(along)


def cells_along(
    seg_xy: list[tuple[float, float]],
    seg_offsets: list[float],
    along_start: float,
    along_end: float,
) -> set[tuple[int, int]]:
    """Grid cells a stretch of the segment's own geometry covers.

    The walk follows the stored geometry, not any single GPS pass: the segment
    is definitionally repeated ground, and the cells it names are what the
    fitted drawer prefers.
    """
    cell_m = analysis_constants.TRAIL_GRAPH_CELL_M
    cells: set[tuple[int, int]] = set()
    step = max(cell_m / 2.0, 5.0)
    distance = max(along_end - along_start, 0.0)
    samples = max(1, int(distance / step))
    for k in range(samples + 1):
        along = along_start + distance * k / samples
        vertex = next((i for i, off in enumerate(seg_offsets) if off >= along), len(seg_xy) - 2)
        lo_off = seg_offsets[vertex]
        hi_off = seg_offsets[vertex + 1] if vertex + 1 < len(seg_xy) else lo_off
        span = max(hi_off - lo_off, 1e-9)
        tt = max(0.0, min(1.0, (along - lo_off) / span))
        px = seg_xy[vertex][0] + tt * (seg_xy[min(vertex + 1, len(seg_xy) - 1)][0] - seg_xy[vertex][0])
        py = seg_xy[vertex][1] + tt * (seg_xy[min(vertex + 1, len(seg_xy) - 1)][1] - seg_xy[vertex][1])
        cells.add(analysis_graph.cell_of(px, py))
    return cells


def longest_snapped_run(
    poly_xy: list[tuple[float, float]],
    line_xy: list[tuple[float, float]],
    offsets: list[float],
    tol_m: float,
) -> tuple[float, float] | None:
    """The segment span a polyline runs along: its longest consecutive stretch.

    The run is the longest sequence of vertices all landing within tolerance of
    the segment's geometry. Returns the along-offsets of the run's ends, in the
    order the polyline reaches them: an increasing pair went forward, a
    decreasing pair reverse.
    """
    projected = [project_with_distance(xy, line_xy, offsets) for xy in poly_xy]
    best: tuple[float, float, int, int] | None = None
    best_len = 0.0
    start_along = None
    start_index = None
    last_along = None
    last_index = None
    for k, (along, dist) in enumerate(projected):
        if dist <= tol_m:
            if start_along is None:
                start_along, start_index = along, k
            last_along, last_index = along, k
        elif start_along is not None:
            span = abs(last_along - start_along)
            if span > best_len:
                best, best_len = (start_along, last_along, start_index, last_index), span
            start_along = None
    if start_along is not None:
        span = abs(last_along - start_along)
        if span > best_len:
            best, best_len = (start_along, last_along, start_index, last_index), span
    return best


def is_loop(geometry: list, distance_m: float) -> bool:
    """Whether a segment is a short closed circuit rather than a piece of network.

    A 300 m lap of a field is real ground and real training, but drawing it as
    network would claim routes run through it: closedness plus shortness is
    what says circuit, and it is judged on the segment's own geometry.
    """
    if distance_m > analysis_constants.TRAIL_SEGMENT_LOOP_MAX_LEN_M or len(geometry) < 2:
        return False
    start = analysis_graph.to_xy(float(geometry[0][0]), float(geometry[0][1]))
    end = analysis_graph.to_xy(float(geometry[-1][0]), float(geometry[-1][1]))
    close = math.hypot(end[0] - start[0], end[1] - start[1])
    tol = max(
        analysis_constants.TRAIL_SEGMENT_LOOP_CLOSE_MIN_M,
        distance_m * analysis_constants.TRAIL_SEGMENT_LOOP_CLOSE_RATIO,
    )
    return close <= tol
