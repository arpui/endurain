"""Matching a measured stretch to the OpenStreetMap way drawn under it.

The detector says what the ground cost; the map says what the ground is claimed to
be. They only become useful together once a stretch knows *which way it lies on*,
and that is a geometry question with two traps this module exists to avoid:

* a way is one long polyline, so a match cannot be decided from a midpoint - a
  1,5 km forest track and the gully that crosses it have the same middle;
* paths run parallel and switchback, so proximity alone steals matches.

Both are answered the same way the segment matcher does it: every point of the
stretch is projected onto the polyline, and a way is accepted only if most of the
stretch lies within tolerance of it *and* its projections spread along the way
rather than clustering where it happens to cross.

Nothing here judges technicality. The cartographic claim stays exactly what
OpenStreetMap says it is; this only establishes the correspondence.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

import modules.analysis.constants as analysis_constants

_EARTH_RADIUS_M = 6371000.0


@dataclass(frozen=True)
class CandidateWay:
    """One way the stretch can be matched against.

    Attributes:
        way_id: OpenStreetMap way id.
        points: Ordered ``(lat, lng)`` pairs as the way is drawn.
    """

    way_id: int
    points: Sequence[tuple[float, float]]


@dataclass(frozen=True)
class OsmMatch:
    """Where a measured stretch sits along a way.

    Attributes:
        way_id: The matched way.
        start_m: Offset along the way of the stretch's first point, in the way's
            own drawn order.
        end_m: Offset along the way of its last point.
        covered_m: Ground of the stretch the way accounts for.
        mean_offset_m: Mean distance from the stretch's points to the way.
        reverse: ``True`` when the stretch runs against the way's drawn direction.
    """

    way_id: int
    start_m: float
    end_m: float
    covered_m: float
    mean_offset_m: float
    reverse: bool


def _to_xy(points: Sequence[tuple[float, float]], ref_lat: float, ref_lng: float) -> np.ndarray:
    """Project lat/lng onto a local metric plane (metres, good over a few km).

    Args:
        points: ``(lat, lng)`` pairs.
        ref_lat: Latitude the plane is tangent at.
        ref_lng: Longitude the plane is tangent at.

    Returns:
        An ``(n, 2)`` array of metres.
    """
    lat = np.radians(np.asarray([point[0] for point in points], dtype=float))
    lng = np.radians(np.asarray([point[1] for point in points], dtype=float))
    x = (lng - np.radians(ref_lng)) * np.cos(np.radians(ref_lat)) * _EARTH_RADIUS_M
    y = (lat - np.radians(ref_lat)) * _EARTH_RADIUS_M
    return np.column_stack((x, y))


def _arc_offsets(xy: np.ndarray) -> np.ndarray:
    """Return the along-polyline distance of every vertex, in metres.

    Args:
        xy: Metric vertices of the polyline.

    Returns:
        One cumulative distance per vertex, starting at 0.
    """
    if len(xy) < 2:
        return np.zeros(len(xy))
    steps = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    return np.concatenate(([0.0], np.cumsum(steps)))


def _project(point: np.ndarray, line: np.ndarray, offsets: np.ndarray) -> tuple[float, float]:
    """Project one point onto a polyline.

    Args:
        point: Metric coordinates of the point.
        line: Metric vertices of the polyline.
        offsets: Along-polyline distance of each vertex.

    Returns:
        ``(offset_m, distance_m)``: where on the polyline the nearest point falls
        and how far the point is from it.
    """
    if len(line) == 1:
        return float(offsets[0]), float(np.linalg.norm(point - line[0]))

    seg_start = line[:-1]
    seg_vec = np.diff(line, axis=0)
    seg_len_sq = np.einsum("ij,ij->i", seg_vec, seg_vec)
    valid = seg_len_sq > 1e-9
    if not valid.any():
        return float(offsets[0]), float(np.linalg.norm(point - line[0]))

    to_point = point - seg_start
    t = np.where(valid, (to_point * seg_vec).sum(axis=1) / np.where(valid, seg_len_sq, 1.0), 0.0)
    t = np.clip(t, 0.0, 1.0)
    closest = seg_start + t[:, None] * seg_vec
    distances = np.linalg.norm(point - closest, axis=1)
    index = int(np.argmin(distances))
    seg_length = float(np.sqrt(seg_len_sq[index])) if seg_len_sq[index] > 1e-9 else 0.0
    return float(offsets[index] + t[index] * seg_length), float(distances[index])


def match_stretch(
    stretch: Sequence[tuple[float, float]],
    ways: Sequence[CandidateWay],
    *,
    tolerance_m: float = analysis_constants.OSM_MATCH_TOLERANCE_M,
    min_coverage: float = analysis_constants.OSM_MATCH_MIN_COVERAGE,
) -> OsmMatch | None:
    """Find which way a stretch of ground lies on.

    Args:
        stretch: Ordered ``(lat, lng)`` pairs of the measured stretch.
        ways: Candidate ways nearby.
        tolerance_m: How far a point may sit from the way and still count as on it.
        min_coverage: Share of the stretch that must sit within tolerance.

    Returns:
        The best :class:`OsmMatch`, or ``None`` when no way explains the stretch
        (it crosses open ground, or the mapping does not reach it).
    """
    if len(stretch) < 2 or not ways:
        return None

    ref = stretch[0]
    stretch_xy = _to_xy(stretch, ref[0], ref[1])
    stretch_points = [tuple(point) for point in stretch_xy]
    span = float(np.linalg.norm(stretch_xy[-1] - stretch_xy[0]))
    path_length = float(np.linalg.norm(np.diff(stretch_xy, axis=0), axis=1).sum())
    reference_length = max(path_length, span)

    best: OsmMatch | None = None
    best_coverage = 0.0
    for way in ways:
        if len(way.points) < 2:
            continue
        line = _to_xy(way.points, ref[0], ref[1])
        offsets = _arc_offsets(line)
        way_offsets: list[float] = []
        inside = 0
        total_offset = 0.0
        for point in stretch_points:
            offset_m, distance_m = _project(np.asarray(point), line, offsets)
            way_offsets.append(offset_m)
            if distance_m <= tolerance_m:
                inside += 1
                total_offset += distance_m
        coverage = inside / len(stretch_points)
        if coverage < min_coverage or inside == 0:
            continue

        covered = [offset for offset, point in zip(way_offsets, stretch_points, strict=False)]
        low, high = min(covered), max(covered)
        # a way that is merely crossed projects the whole stretch onto a short run
        # of itself; a way that is followed spreads along it
        if high - low < min_coverage * reference_length:
            continue

        match = OsmMatch(
            way_id=way.way_id,
            start_m=round(covered[0], 1),
            end_m=round(covered[-1], 1),
            covered_m=round(reference_length, 1),
            mean_offset_m=round(total_offset / inside, 1),
            reverse=covered[-1] < covered[0],
        )
        if coverage > best_coverage or (
            coverage == best_coverage and best and match.mean_offset_m < best.mean_offset_m
        ):
            best = match
            best_coverage = coverage

    return best
