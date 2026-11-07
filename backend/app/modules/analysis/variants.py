"""Variant search over the personal graph, judged by the athlete afterwards.

A variant is a reconnection: ground the athlete has actually crossed (every edge
comes from their tracks) that leaves a planned route, never touches it again,
and joins back within sane bounds. The search is the honest geometry; the
athlete's verdicts (VariantMark) decide what is real, and ``falsa`` buries a
suggestion for good. Drawing follows mapped ways only where the athlete's own
crossings certify them in that stretch - the cartography helps, the points rule.
"""

import heapq
import math
from bisect import bisect_left, bisect_right
from itertools import pairwise

from sqlalchemy.orm import Session

import modules.analysis.constants as analysis_constants
import modules.analysis.crud as analysis_crud
import modules.analysis.graph as analysis_graph

REF_LAT = analysis_constants.TRAIL_GRAPH_REF_LAT
REF_LNG = analysis_constants.TRAIL_GRAPH_REF_LNG


def _dist_xy(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def route_cells(track: list[tuple], cell_m: float) -> tuple[list[tuple[int, int]], list[float]]:
    """The planned track as cell sequence plus cumulative ground distance.

    Args:
        track: ``(stamp, lat, lng)`` waypoints.
        cell_m: Grid size used by the graph.

    Returns:
        ``(cells, cum_m)`` with consecutive duplicates collapsed.
    """
    cells: list[tuple[int, int]] = []
    cum: list[float] = []
    prev_xy = None
    total = 0.0
    for _stamp, lat, lng in track:
        xy = analysis_graph.to_xy(lat, lng)
        if prev_xy is not None:
            step = _dist_xy(prev_xy, xy)
            if step > analysis_constants.TRAIL_GRAPH_MAX_JUMP_M:
                prev_xy = xy
                continue
            total += step
        cell = analysis_graph.cell_of(xy[0], xy[1])
        if not cells or cells[-1] != cell:
            cells.append(cell)
            cum.append(total)
        prev_xy = xy
    return cells, cum


def _rdp(points: list[tuple[float, float]], eps_m: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker on metric points: the corridor a cell chain walks."""
    if len(points) < 3:
        return points
    dmax, index = 0.0, 0
    ax, ay = points[0]
    bx, by = points[-1]
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    for i in range(1, len(points) - 1):
        px, py = points[i]
        if seg2 <= 1e-9:
            d = math.hypot(px - ax, py - ay)
        else:
            tt = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
            d = math.hypot(px - (ax + tt * dx), py - (ay + tt * dy))
        if d > dmax:
            dmax, index = d, i
    if dmax > eps_m:
        return _rdp(points[: index + 1], eps_m)[:-1] + _rdp(points[index:], eps_m)
    return [points[0], points[-1]]


def _xy(node_map: dict, cell) -> tuple[float, float]:
    """Metric position of a stored node, computed once and carried along."""
    entry = node_map[cell]
    xy = entry.get("xy")
    if xy is None:
        xy = analysis_graph.to_xy(entry["lat"], entry["lng"])
        entry["xy"] = xy
    return xy


def _best_variant(adjacency: dict, node_map: dict, start, blocked: set, targets: dict, cutoff_m: float):
    """Shortest directed path from start to any target, never touching blocked cells.

    Dijkstra pops in order of total length, so the first target popped at a
    usable distance is the shortest possible reconnection: the walk ends there
    instead of bloating outward to the cutoff.
    """
    dist = {start: 0.0}
    prev = {}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf):
            continue
        if u in targets and d >= analysis_constants.TRAIL_VARIANT_MIN_LEN_M:
            path = [u]
            while path[-1] != start:
                path.append(prev[path[-1]])
            path.reverse()
            return path, d
        if d > cutoff_m:
            break
        uxy = _xy(node_map, u)
        for step in adjacency.get(u, []):
            v = step["to"]
            if v in blocked or step["step_max"] > analysis_constants.TRAIL_GRAPH_MAX_STEP_M:
                continue
            centre_len = max(_dist_xy(uxy, _xy(node_map, v)), 5.0)
            nd = d + centre_len
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    return None


def walk_between(
    adjacency: dict,
    node_map: dict,
    start,
    target,
    cutoff_m: float,
    prefer: set[tuple[int, int]] | None = None,
) -> list | None:
    """Shortest walked chain of cells between two cells of the personal graph.

    Same ground rules as the variant search: only the athlete's own directed
    transitions, and none born of a long GPS interpolation. When no chain fits
    within the cutoff there is no walked answer. Cells covered by the athlete's
    own named segments are favored - ground they chose and repeated is the
    better answer when more than one chain exists - without excluding the rest.
    """
    if start == target:
        return [start]
    dist = {start: 0.0}
    prev = {}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf):
            continue
        if u == target:
            path = [u]
            while path[-1] != start:
                path.append(prev[path[-1]])
            path.reverse()
            return path
        if d > cutoff_m:
            break
        uxy = _xy(node_map, u)
        for step in adjacency.get(u, []):
            v = step["to"]
            if v not in node_map or step["step_max"] > analysis_constants.TRAIL_GRAPH_MAX_STEP_M:
                continue
            weight = max(_dist_xy(uxy, _xy(node_map, v)), 5.0)
            if prefer is not None and u in prefer and v in prefer:
                weight *= 0.5
            nd = d + weight
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    return None


def nearest_cell(node_map: dict, xy: tuple[float, float], tol_m: float) -> tuple | None:
    """The graph cell nearest to a metric position, within tolerance."""
    best = None
    best_d2 = tol_m * tol_m
    for cell in node_map:
        cxy = _xy(node_map, cell)
        dx = cxy[0] - xy[0]
        dy = cxy[1] - xy[1]
        d2 = dx * dx + dy * dy
        if d2 <= best_d2:
            best, best_d2 = cell, d2
    return best


def _tangent(node_map: dict, a, b) -> tuple[float, float]:
    axy = _xy(node_map, a)
    bxy = _xy(node_map, b)
    norm = max(_dist_xy(axy, bxy), 1e-6)
    return (bxy[0] - axy[0]) / norm, (bxy[1] - axy[1]) / norm


def find_variants(
    cells: list[tuple[int, int]],
    cum: list[float],
    node_map: dict,
    adjacency: dict,
    marks: dict[float, str],
) -> list[dict]:
    """Suggest reconnection variants along a planned cell route.

    Args:
        cells: The route as grid cells, in order.
        cum: Cumulative ground distance per route cell.
        node_map: Stored nodes keyed by cell.
        adjacency: Stored directed edges keyed by origin cell.
        marks: existing verdicts keyed by junction ground position (metres).

    Returns:
        One dict per surviving candidate, with status and drawn-ready cell path.
    """
    route_set = set(cells)
    out: list[dict] = []
    last_alert = -math.inf
    for i in range(1, len(cells) - 3):
        u = cells[i]
        if cum[i] - last_alert < analysis_constants.TRAIL_VARIANT_ALERT_SPACING_M:
            continue
        successors = [
            step["to"]
            for step in adjacency.get(u, [])
            if step["to"] != cells[i + 1]
            and step["to"] not in route_set
            and step["step_max"] <= analysis_constants.TRAIL_GRAPH_MAX_STEP_M
        ]
        if not successors:
            continue
        lo = bisect_left(cum, cum[i] + analysis_constants.TRAIL_VARIANT_REJOIN_MIN_M, i + 3)
        hi = bisect_right(cum, cum[i] + analysis_constants.TRAIL_VARIANT_REJOIN_MAX_M, i + 3)
        targets = {cells[j]: j for j in range(lo, hi)}
        if not targets:
            continue
        blocked = route_set - set(targets)
        # no variant can be worth chasing beyond twice the farthest possible
        # rejoin: cap the walk there so junctions near a route's end cost little
        cutoff = min(
            analysis_constants.TRAIL_VARIANT_REJOIN_MAX_M * analysis_constants.TRAIL_VARIANT_MAX_DETOUR_RATIO,
            2.0 * (cum[hi - 1] - cum[i]),
        )
        for nbr in successors:
            if nbr in blocked:
                continue
            found = _best_variant(adjacency, node_map, nbr, blocked, targets, cutoff)
            if found is None:
                continue
            sub_path, _sub_len = found
            path = [u, *sub_path]
            exit_step = next(s for s in adjacency.get(u, []) if s["to"] == nbr)
            j = targets[path[-1]]
            main_len = cum[j] - cum[i]
            chain = [_xy(node_map, c) for c in path]
            corridor = _rdp(chain, 20.0)
            full_len = sum(_dist_xy(a, b) for a, b in pairwise(corridor))
            if full_len < analysis_constants.TRAIL_VARIANT_MIN_LEN_M:
                continue
            if full_len > main_len * analysis_constants.TRAIL_VARIANT_MAX_DETOUR_RATIO:
                continue
            straight = _dist_xy(chain[0], chain[-1])
            if straight < main_len * analysis_constants.TRAIL_VARIANT_MIN_SPAN_RATIO:
                continue
            if full_len < main_len * analysis_constants.TRAIL_VARIANT_MIN_LEN_RATIO:
                continue
            steps = [exit_step] + [
                next(s for s in adjacency.get(path[k], []) if s["to"] == path[k + 1]) for k in range(len(path) - 1)
            ]
            gain = sum(s["gain"] for s in steps)
            main_gain = sum(
                next((s["gain"] for s in adjacency.get(cells[k], []) if s["to"] == cells[k + 1]), 0.0)
                for k in range(i, j)
            )
            confident = sum(s["len"] for s in steps if s["passes"] >= 2 or s["on_path"] >= 1)
            status = (
                "confirmada"
                if confident >= analysis_constants.TRAIL_VARIANT_CONFIRM_SHARE * sum(s["len"] for s in steps)
                else "provisional"
            )
            um = _tangent(node_map, u, cells[min(i + 3, len(cells) - 1)])
            ua = _tangent(node_map, u, path[min(2, len(path) - 1)])
            z = um[0] * ua[1] - um[1] * ua[0]
            side = "left" if z >= 0.05 else ("right" if z <= -0.05 else "straight")
            rank = {"": 0, "tech": 1, "hard": 2}
            grade = max((node_map.get(c, {}).get("grade", "") for c in path), key=lambda g: rank.get(g, 0), default="")
            key = round(cum[i], 1)
            if marks.get(key) == "falsa":
                break
            out.append(
                {
                    "split_m": cum[i],
                    "rejoin_m": cum[j],
                    "side": side,
                    "variant_m": round(full_len),
                    "main_m": round(main_len),
                    "delta_m": round(full_len - main_len),
                    "delta_gain_m": round(gain - main_gain),
                    "status": status,
                    "grade": grade,
                    "path": [[c[0], c[1]] for c in path],
                    "mark": marks.get(key),
                }
            )
            last_alert = cum[i]
            break  # one alert per junction
    return out


def _project(pt, pts) -> int:
    """Index of the geometry vertex nearest to a latlng point."""
    return min(range(len(pts)), key=lambda k: _dist_pt_geo(pt, pts[k]))


def draw_along_ways(centres_latlng: list[tuple[float, float]], user_id: int, db: Session) -> list[list[float]]:
    """Shape a cell-centre chain by the mapped ways the athlete certified.

    A way only shapes the drawing where a crossing says the athlete ran that way
    in that stretch; everything else keeps the GPS-derived centres.
    """
    near_ids = [
        [w.way_id for w in analysis_crud.list_osm_paths_in_box(la - 0.0004, la + 0.0004, ln - 0.0005, ln + 0.0005, db)]
        for la, ln in centres_latlng
    ]
    way_ids = sorted({wid for ids in near_ids for wid in ids})
    geoms = analysis_crud.get_osm_paths_by_ids(way_ids, db)
    tol = analysis_constants.TRAIL_VARIANT_WAY_CERT_TOL_M

    stable: list[tuple[float, int] | None] = []
    for (la, ln), candidates in zip(centres_latlng, near_ids, strict=False):
        best: tuple[float, int] | None = None
        for way_id in candidates:
            for pt in geoms[way_id].geometry:
                d = _dist_pt_geo((la, ln), pt)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, way_id)
                    break
        stable.append(best)

    out: list[list[float]] = []
    i = 0
    n = len(centres_latlng)
    while i < n:
        a = stable[i]
        if a is None:
            out.append([centres_latlng[i][0], centres_latlng[i][1]])
            i += 1
            continue
        way_id = a[1]
        j = i
        while j + 1 < n and stable[j + 1] is not None and stable[j + 1][1] == way_id:
            j += 1
        geo = geoms[way_id].geometry
        s0 = _project(centres_latlng[i], geo)
        s1 = _project(centres_latlng[j], geo)
        run = geo[s0 : s1 + 1] if s0 <= s1 else geo[s1 : s0 + 1][::-1]
        offsets = [0.0]
        for k in range(len(geo) - 1):
            offsets.append(offsets[-1] + _dist_pt_geo(geo[k], geo[k + 1]))
        lo_m = offsets[min(s0, s1)] - 30.0
        hi_m = offsets[max(s0, s1)] + 30.0
        certified = analysis_crud.has_crossing_in_range(way_id, user_id, lo_m, hi_m, db)
        chain_len = sum(
            _dist_xy(
                analysis_graph.to_xy(*centres_latlng[k]),
                analysis_graph.to_xy(*centres_latlng[k + 1]),
            )
            for k in range(i, j)
        )
        run_len = sum(_dist_pt_geo(run[k], run[k + 1]) for k in range(len(run) - 1)) if len(run) > 1 else 0.0
        if certified and run_len <= 3.0 * max(chain_len, 15.0) + 60.0:
            for pt in run:
                if not out or abs(pt[0] - out[-1][0]) > 1e-7 or abs(pt[1] - out[-1][1]) > 1e-7:
                    out.append([float(pt[0]), float(pt[1])])
        else:
            for k in range(i, j + 1):
                out.append([centres_latlng[k][0], centres_latlng[k][1]])
        i = j + 1
    return out


def _dist_pt_geo(a, b) -> float:
    ax, ay = analysis_graph.to_xy(a[0], a[1])
    bx, by = analysis_graph.to_xy(b[0], b[1])
    return math.hypot(ax - bx, ay - by)
