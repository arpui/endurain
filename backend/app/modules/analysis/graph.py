"""The personal ground graph: the athlete's crossed cells and transitions.

The graph is rebuilt from the stored tracks - map and elevation streams - on the
application's own schedule. Ground truth lives in the GPS: a cell exists because
samples landed in it, an edge because a track moved between two cells. The
cartography only labels: a traversal counts as cartographically confirmed only
when it runs ALONG a way the athlete's coverage already matched (within the
tolerance and ±50° parallel), never toward one. The variant search and the route
designer walk on these edges, so the honesty of what is ground decides both.
"""

import math
from bisect import bisect_left
from datetime import UTC

import numpy as np
from sqlalchemy.orm import Session

import modules.activities.activity_streams.constants as activity_streams_constants
import modules.analysis.constants as analysis_constants
import modules.analysis.crud as analysis_crud
import modules.analysis.service as analysis_service

CELL_M = analysis_constants.TRAIL_GRAPH_CELL_M
MAX_JUMP_M = analysis_constants.TRAIL_GRAPH_MAX_JUMP_M
REF_LAT = analysis_constants.TRAIL_GRAPH_REF_LAT
REF_LNG = analysis_constants.TRAIL_GRAPH_REF_LNG
GRADE_RANK = {"": 0, "tech": 1, "hard": 2}


def to_xy(lat: float, lng: float) -> tuple[float, float]:
    """Local metric frame (east, north) in metres; fixed reference for all users."""
    return (
        (lng - REF_LNG) * 111320.0 * math.cos(math.radians(REF_LAT)),
        (lat - REF_LAT) * 111195.0,
    )


def to_latlng(x: float, y: float) -> tuple[float, float]:
    """Inverse of :func:`to_xy`."""
    return (
        REF_LAT + y / 111195.0,
        REF_LNG + x / (111320.0 * math.cos(math.radians(REF_LAT))),
    )


def cell_of(x: float, y: float) -> tuple[int, int]:
    """Grid cell containing a metric position."""
    return round(x / CELL_M), round(y / CELL_M)


def _utc_naive(stamp):
    """One clock for both streams: map stamps are naive, elevation may not be."""
    if stamp.tzinfo is None:
        return stamp
    return stamp.astimezone(UTC).replace(tzinfo=None)


def way_geometries_for_activity(activity_id: int, user_id: int, db: Session) -> list[list[tuple[float, float]]]:
    """The stored ways an activity was matched to, as XY polylines with bboxes."""
    way_ids = analysis_crud.list_crossing_way_ids(activity_id, user_id, db)
    out = []
    for way in analysis_crud.get_osm_paths_by_ids(way_ids, db).values():
        poly = [to_xy(lat, lng) for lat, lng in way.geometry]
        if len(poly) >= 2:
            out.append(poly)
    return out


def _on_path_flags(
    points_xy: list[tuple[float, float]], way_polys: list[list[tuple[float, float]]]
) -> list[tuple[bool, tuple[float, float] | None]]:
    """For each point: whether it sits ≤25 m from a matched way, and that way's direction."""
    pts = np.asarray(points_xy, dtype=float)
    near = np.zeros(len(pts), dtype=bool)
    best_d2 = np.full(len(pts), np.inf)
    vecs: list[tuple[float, float] | None] = [None] * len(pts)
    tol2 = analysis_constants.TRAIL_VARIANT_WAY_CERT_TOL_M**2
    for poly in way_polys:
        a = np.asarray(poly[:-1])
        d = np.asarray(poly[1:]) - a
        seg2 = (d * d).sum(axis=1)
        keep = seg2 > 1.0
        a, d, seg2 = a[keep], d[keep], seg2[keep]
        if len(a) == 0:
            continue
        for start in range(0, len(pts), 256):
            chunk = pts[start : start + 256]
            rel = chunk[:, None, :] - a[None, :, :]
            tt = np.clip((rel * d[None]).sum(axis=2) / seg2[None], 0.0, 1.0)
            closest = a[None] + tt[:, :, None] * d[None]
            dist2 = ((chunk[:, None, :] - closest) ** 2).sum(axis=2)
            hit = dist2 <= tol2
            row_best = dist2.argmin(axis=1)
            idx = np.arange(start, start + len(chunk))
            near[idx[hit.any(axis=1)]] = True
            improve = hit.any(axis=1) & (dist2.min(axis=1) < best_d2[idx])
            for i in idx[improve]:
                v = d[row_best[i - start]]
                vecs[i] = (float(v[0]), float(v[1]))
                best_d2[i] = dist2[i - start].min()
    return [(bool(near[i]), vecs[i]) for i in range(len(pts))]


def _along(prev_ev, cur_ev, dx: float, dy: float) -> bool:
    """Both endpoints on a matched way and the step parallel to it (±50°)."""
    near_p, vec_p = prev_ev
    near_c, vec_c = cur_ev
    if not (near_p and near_c):
        return False
    norm = math.hypot(dx, dy)
    if norm <= 1.0:
        return False
    ux, uy = dx / norm, dy / norm
    for vec in (vec_p, vec_c):
        if vec is None:
            continue
        vn = math.hypot(*vec)
        if vn <= 1e-6:
            continue
        if abs(ux * vec[0] + uy * vec[1]) / vn >= math.cos(math.radians(50.0)):
            return True
    return False


def activity_graph_rows(
    activity_id: int, user_id: int, db: Session
) -> tuple[list[dict], list[dict], dict[tuple[int, int], str]]:
    """Cells, directed edges and cell grades one activity contributes.

    Returns:
        ``(nodes, edges, cell_grades)`` in grid coordinates; grades come from the
        stored technical observation projected by ground distance, with denied
        (``wrong``) stretches marking nothing.
    """
    track = analysis_service._trail_track(activity_id, db)
    if len(track) < 2:
        return [], [], {}
    _, ele_values, ele_stamps = analysis_service._stream_samples(
        activity_id,
        activity_streams_constants.STREAM_TYPE_ELEVATION,
        "ele",
        db,
        keep_zeros=True,
    )
    stamps = [_utc_naive(s) for s in ele_stamps]
    values = list(ele_values)

    def ele_at(stamp) -> float | None:
        if not stamps:
            return None
        i = bisect_left(stamps, stamp)
        best = i if i < len(stamps) else len(stamps) - 1
        if best > 0 and abs((stamp - stamps[best - 1]).total_seconds()) < abs((stamp - stamps[best]).total_seconds()):
            best -= 1
        if abs((stamp - stamps[best]).total_seconds()) > 30.0:
            return None
        return float(values[best])

    points = [(_utc_naive(s), lat, lng) for s, lat, lng in track]
    xy = [to_xy(lat, lng) for _s, lat, lng in points]
    evidence = _on_path_flags(xy, way_geometries_for_activity(activity_id, user_id, db))

    cells: dict[tuple[int, int], list[float]] = {}  # [lat_sum, lng_sum, ele_sum, n]
    edges: dict[tuple[tuple[int, int], tuple[int, int]], list[float]] = {}  # len, gain, loss, passes, step_max, on_path
    occ: dict[tuple[int, int], list[list[float]]] = {}  # cell -> [[enter_m, exit_m], ...]
    prev_cell = None
    prev_xy = None
    prev_ele: float | None = None
    prev_index = -1
    cum = 0.0
    for index, (stamp, lat, lng) in enumerate(points):
        x, y = xy[index]
        step = 0.0
        if prev_xy is not None:
            step = math.hypot(x - prev_xy[0], y - prev_xy[1])
            if step > MAX_JUMP_M:
                prev_xy = (x, y)
                prev_cell = None
                prev_index = -1
                continue
            cum += step
        ele = ele_at(stamp)
        cell = cell_of(x, y)
        node = cells.get(cell)
        if node is None:
            cells[cell] = [lat, lng, ele or 0.0, 1]
        else:
            node[0] += lat
            node[1] += lng
            node[2] += ele or 0.0
            node[3] += 1
        ranges = occ.setdefault(cell, [])
        if ranges and cum - ranges[-1][1] <= 1.0:
            ranges[-1][1] = cum
        else:
            ranges.append([cum, cum])
        if prev_cell is not None and cell != prev_cell:
            length = math.hypot(x - prev_xy[0], y - prev_xy[1])
            gain = max(0.0, ele - prev_ele) if ele is not None and prev_ele is not None else 0.0
            loss = max(0.0, prev_ele - ele) if ele is not None and prev_ele is not None else 0.0
            on_path = 1 if _along(evidence[prev_index], evidence[index], x - prev_xy[0], y - prev_xy[1]) else 0
            edge = edges.get((prev_cell, cell))
            if edge is None:
                edges[(prev_cell, cell)] = [max(length, 1.0), gain, loss, 1, step, on_path]
            else:
                edge[0] += max(length, 1.0)
                edge[1] += gain
                edge[2] += loss
                edge[3] += 1
                edge[4] = max(edge[4], step)
                edge[5] += on_path
        prev_cell, prev_xy, prev_ele, prev_index = cell, (x, y), ele, index

    # cell grades: the stored observation projected by ground distance
    cell_grades: dict[tuple[int, int], str] = {}
    stored = analysis_crud.list_trail_observation(activity_id, analysis_constants.trail_model_stamp(), db)
    if stored is not None:
        _session_row, sections = stored
        verdicts = analysis_crud.list_trail_mark_verdicts(activity_id, user_id, db)
        for section in sections:
            if verdicts.get(section.start_m) == "wrong":
                continue
            grade = (
                "hard" if section.core_score is not None and verdicts.get(section.start_m) != "overstated" else "tech"
            )
            lo_m, hi_m = section.start_m, section.start_m + section.length_m
            for cell, ranges in occ.items():
                if (
                    any(r[0] <= hi_m and r[1] >= lo_m for r in ranges)
                    and GRADE_RANK[grade] > GRADE_RANK[cell_grades.get(cell, "")]
                ):
                    cell_grades[cell] = grade

    node_rows = [
        {
            "x": c[0],
            "y": c[1],
            "lat": v[0] / v[3],
            "lng": v[1] / v[3],
            "ele": v[2] / v[3],
            "visits": int(v[3]),
            "grade": cell_grades.get(c, ""),
        }
        for c, v in cells.items()
    ]
    edge_rows = [
        {
            "ax": a[0],
            "ay": a[1],
            "bx": b[0],
            "by": b[1],
            "length": e[0],
            "gain": e[1],
            "loss": e[2],
            "passes": int(e[3]),
            "step_max": e[4],
            "on_path": int(e[5]),
        }
        for (a, b), e in edges.items()
    ]
    return node_rows, edge_rows, cell_grades


def merge_activity_into_graph(
    graph_nodes: dict[tuple[int, int], dict],
    graph_edges: dict[tuple[tuple[int, int], tuple[int, int]], dict],
    node_rows: list[dict],
    edge_rows: list[dict],
) -> None:
    """Accumulate one activity's contribution into the running graph aggregate."""
    for row in node_rows:
        key = (row["x"], row["y"])
        acc = graph_nodes.get(key)
        if acc is None:
            graph_nodes[key] = {
                "lat_sum": row["lat"] * row["visits"],
                "lng_sum": row["lng"] * row["visits"],
                "ele_sum": row["ele"] * row["visits"],
                "visits": row["visits"],
                "grade": row["grade"],
            }
        else:
            acc["lat_sum"] += row["lat"] * row["visits"]
            acc["lng_sum"] += row["lng"] * row["visits"]
            acc["ele_sum"] += row["ele"] * row["visits"]
            acc["visits"] += row["visits"]
            if GRADE_RANK[row["grade"]] > GRADE_RANK[acc["grade"]]:
                acc["grade"] = row["grade"]
    for row in edge_rows:
        key = ((row["ax"], row["ay"]), (row["bx"], row["by"]))
        acc = graph_edges.get(key)
        if acc is None:
            graph_edges[key] = dict(row)
        else:
            acc["length"] += row["length"]
            acc["gain"] += row["gain"]
            acc["loss"] += row["loss"]
            acc["passes"] += row["passes"]
            acc["step_max"] = max(acc["step_max"], row["step_max"])
            acc["on_path"] += row["on_path"]


def graph_from_rows(nodes, edges) -> tuple[dict, dict]:
    """Rebuild adjacency dictionaries from stored graph rows (crud hands them over).

    An edge can leave the bounding box its origin was loaded by: its destination
    cell then gets its position from the grid itself, which is exact by
    construction, and carries no claim of its own.
    """
    node_map = {(n.x, n.y): {"lat": n.lat, "lng": n.lng, "ele": n.ele, "grade": n.grade} for n in nodes}
    adjacency: dict[tuple[int, int], list[dict]] = {}
    for e in edges:
        adjacency.setdefault((e.ax, e.ay), []).append(
            {
                "to": (e.bx, e.by),
                "len": e.length / e.passes if e.passes else 0.0,
                "gain": e.gain / e.passes if e.passes else 0.0,
                "loss": e.loss / e.passes if e.passes else 0.0,
                "passes": e.passes,
                "step_max": e.step_max,
                "on_path": e.on_path,
            }
        )
    for e in edges:
        for cell in ((e.ax, e.ay), (e.bx, e.by)):
            if cell not in node_map:
                lat, lng = to_latlng(cell[0] * CELL_M, cell[1] * CELL_M)
                node_map[cell] = {"lat": lat, "lng": lng, "ele": 0.0, "grade": ""}
    return node_map, adjacency
