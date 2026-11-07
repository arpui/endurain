"""The A→B planner: walked ground plus the provisional map, under one search.

Two strata feed a single graph. The personal graph is ground the athlete's
own GPS proved; the cartographic layer is ways the map draws over ground
nobody has crossed - always provisional, never claimed, and every plan that
uses it says where and how far. The two strata join only where they really
meet: a mapped vertex links to the walked network when a crossed cell lies
within a trailhead's step of it.

The mode is the athlete's intention, stated honestly as weights:
- curt: ground distance first; untracked map ground costs more, so equal
  known paths win;
- tecnic: the personal graph's own hard and technical cells are favored, and
  mapped paths (path, steps, sac scale) get their chance;
- tanteig: never-crossed map ground is what the plan is for, so it is
  preferred outright and lightly-walked ground is dodged; but ways crossed
  by many sessions are left alone - heavily walked ground is infrastructure,
  and where it exists it is probably the only ground there is.
"""

import heapq
import math
from collections import defaultdict

import core.exceptions as core_exceptions
import modules.analysis.constants as analysis_constants
import modules.analysis.graph as analysis_graph

_C = analysis_constants

#: highway values that read as underfoot ground rather than street.
_TECH_HIGHWAYS = {"path", "steps", "bridleway"}


def way_is_technical(tags: dict | None) -> bool:
    """Whether a mapped way's cartography promises technical ground."""
    tags = tags or {}
    if tags.get("sac_scale"):
        return True
    return str(tags.get("highway", "")) in _TECH_HIGHWAYS


def _vertex_key(lat: float, lng: float) -> tuple:
    """One node per mapped position: ways sharing an OSM node share a key."""
    return ("o", round(lat, 6), round(lng, 6))


def _ground_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def corridor(from_latlng: tuple[float, float], to_latlng: tuple[float, float]) -> tuple[float, float, dict]:
    """The corridor one A→B request searches.

    Args:
        from_latlng: The drawn start point.
        to_latlng: The drawn end point.

    Returns:
        ``(straight_m, cutoff_m, boxes)``: the straight-line distance, the
        cost ceiling that bounds an honest detour, and the boxes to load -
        ``graph`` in grid cells for the personal graph, ``ways`` in degrees
        for the cartographic layer.

    Raises:
        InvalidInputError: when the pair is too close to plan or too far
            apart for a plan to mean anything.
    """
    a_xy = analysis_graph.to_xy(*from_latlng)
    b_xy = analysis_graph.to_xy(*to_latlng)
    straight = _ground_m(a_xy, b_xy)
    if straight < 50.0:
        raise core_exceptions.InvalidInputError("the two points are the same place")
    if straight > _C.TRAIL_ROUTE_GEN_MAX_STRAIGHT_M:
        raise core_exceptions.InvalidInputError("too far apart for a plan")
    cutoff_m = _C.TRAIL_ROUTE_GEN_CUTOFF_FACTOR * straight + 1500.0
    pad_m = min(max(2.0 * straight, 2000.0), 14000.0)

    cell = _C.TRAIL_GRAPH_CELL_M
    min_x = math.floor((min(a_xy[0], b_xy[0]) - pad_m) / cell)
    max_x = math.ceil((max(a_xy[0], b_xy[0]) + pad_m) / cell)
    min_y = math.floor((min(a_xy[1], b_xy[1]) - pad_m) / cell)
    max_y = math.ceil((max(a_xy[1], b_xy[1]) + pad_m) / cell)
    corner_a = analysis_graph.to_latlng(min_x * cell, min_y * cell)
    corner_b = analysis_graph.to_latlng(max_x * cell, max_y * cell)
    boxes = {
        "graph": (min_x, max_x, min_y, max_y),
        "ways": (
            min(corner_a[0], corner_b[0]),
            max(corner_a[0], corner_b[0]),
            min(corner_a[1], corner_b[1]),
            max(corner_a[1], corner_b[1]),
        ),
    }
    return straight, cutoff_m, boxes


def build_layers(
    from_latlng: tuple[float, float],
    to_latlng: tuple[float, float],
    nodes: list,
    edges: list,
    ways: list[tuple[int, dict | None, list]],
    crossed: dict[int, int],
    cutoff_m: float,
) -> tuple[dict, dict, tuple, tuple]:
    """Assemble the searchable network for one corridor.

    Args:
        from_latlng: The drawn start point.
        to_latlng: The drawn end point.
        nodes: The corridor's personal-graph cell rows.
        edges: The corridor's personal-graph transition rows.
        ways: The corridor's mapped ways as ``(way_id, tags, geometry)``.
        crossed: Sessions per crossed way, for the caller.
        cutoff_m: The corridor's cost ceiling, from :func:`corridor`.

    Returns:
        ``(node_map, adjacency, start_key, target_key)`` with both endpoints
        resolved onto the network.

    Raises:
        InvalidInputError: when an endpoint sits where neither the graph nor
            the map offers anything to start from.
    """
    node_map, adjacency = analysis_graph.graph_from_rows(nodes, edges)
    _add_way_layer(node_map, adjacency, ways, crossed)
    _link_layers(node_map, adjacency)

    a_xy = analysis_graph.to_xy(*from_latlng)
    b_xy = analysis_graph.to_xy(*to_latlng)
    start_key = _anchor(node_map, adjacency, a_xy, from_latlng)
    target_key = _anchor(node_map, adjacency, b_xy, to_latlng)
    if start_key is None or target_key is None:
        raise core_exceptions.InvalidInputError("an endpoint sits on neither walked nor mapped ground")
    return node_map, adjacency, start_key, target_key


def _add_way_layer(
    node_map: dict,
    adjacency: dict,
    ways: list[tuple[int, dict | None, list]],
    crossed: dict[int, int],
) -> None:
    """Add the cartographic stratum: vertices as nodes, stretches as edges.

    A stretch whose middle sits inside a crossed cell is walked ground under
    a different name - the GPS, not the crossing ledger, decides what counts
    as known - so those stretches carry no provisional flag.
    """
    crossed_cells = {key for key in node_map if key[0] != "o"}
    for way_id, tags, raw_geometry in ways:
        pts = [(float(p[0]), float(p[1])) for p in raw_geometry]
        if len(pts) < 2:
            continue
        passes = crossed.get(way_id, 0)
        tech = way_is_technical(tags)
        keys = []
        for lat, lng in pts:
            key = _vertex_key(lat, lng)
            if key not in node_map:
                node_map[key] = {
                    "lat": lat,
                    "lng": lng,
                    "ele": None,
                    "grade": "",
                    "xy": analysis_graph.to_xy(lat, lng),
                }
            keys.append(key)
        for k in range(len(keys) - 1):
            entry = node_map[keys[k]]
            other = node_map[keys[k + 1]]
            d = _ground_m(entry["xy"], other["xy"])
            if d < 0.5:
                continue
            middle = analysis_graph.cell_of(
                (entry["xy"][0] + other["xy"][0]) / 2.0,
                (entry["xy"][1] + other["xy"][1]) / 2.0,
            )
            step = {
                "len": d,
                "kind": "way",
                "way_id": way_id,
                "tech": tech,
                "passes": passes,
                "walked": middle in crossed_cells,
            }
            adjacency.setdefault(keys[k], []).append({**step, "to": keys[k + 1]})
            adjacency.setdefault(keys[k + 1], []).append({**step, "to": keys[k]})


def _link_layers(node_map: dict, adjacency: dict) -> None:
    """Join mapped vertices to crossed cells that meet at a trailhead."""
    link = _C.TRAIL_ROUTE_GEN_LINK_M
    mapped_grid: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for key, entry in node_map.items():
        if key[0] == "o":
            mapped_grid[(math.floor(entry["xy"][0] / link), math.floor(entry["xy"][1] / link))].append(key)
    cap = _C.TRAIL_ROUTE_GEN_LINK_MAX
    for key, entry in node_map.items():
        if key[0] == "o":
            continue
        gx, gy = math.floor(entry["xy"][0] / link), math.floor(entry["xy"][1] / link)
        near: list[tuple[float, tuple]] = []
        for bx in (gx - 1, gx, gx + 1):
            for by in (gy - 1, gy, gy + 1):
                for other in mapped_grid.get((bx, by), []):
                    d = _ground_m(entry["xy"], node_map[other]["xy"])
                    if d <= link:
                        near.append((d, other))
        for d, other in sorted(near)[:cap]:
            adjacency.setdefault(key, []).append({"to": other, "len": d, "kind": "link", "passes": 0})
            adjacency.setdefault(other, []).append({"to": key, "len": d, "kind": "link", "passes": 0})


def _anchor(node_map: dict, adjacency: dict, xy: tuple[float, float], latlng: tuple[float, float]) -> tuple | None:
    """Resolve a drawn endpoint onto the network.

    First the athlete's own ground, then the map's vertices, then a synthetic
    node bridged to whatever honest ground is near.
    """
    graph_best = None
    best_d2 = _C.TRAIL_GRAPH_SNAP_TOL_M**2
    for key, entry in node_map.items():
        if key[0] == "o":
            continue
        d2 = (entry["xy"][0] - xy[0]) ** 2 + (entry["xy"][1] - xy[1]) ** 2
        if d2 <= best_d2:
            graph_best, best_d2 = key, d2
    if graph_best is not None:
        return graph_best
    way_best = None
    best_d2 = _C.TRAIL_ROUTE_GEN_NODE_TOL_M**2
    for key, entry in node_map.items():
        if key[0] != "o":
            continue
        d2 = (entry["xy"][0] - xy[0]) ** 2 + (entry["xy"][1] - xy[1]) ** 2
        if d2 <= best_d2:
            way_best, best_d2 = key, d2
    if way_best is not None:
        return way_best
    key = ("a", round(latlng[0], 6), round(latlng[1], 6))
    node_map[key] = {"lat": latlng[0], "lng": latlng[1], "ele": None, "grade": "", "xy": xy}
    reach = _C.TRAIL_ROUTE_GEN_NODE_TOL_M + _C.TRAIL_ROUTE_GEN_LINK_M
    linked = 0
    for other, entry in node_map.items():
        if other == key:
            continue
        d = _ground_m(xy, entry["xy"])
        if d > reach:
            continue
        adjacency.setdefault(key, []).append({"to": other, "len": d, "kind": "link", "passes": 0})
        adjacency.setdefault(other, []).append({"to": key, "len": d, "kind": "link", "passes": 0})
        linked += 1
    if linked == 0:
        del node_map[key]
        return None
    return key


def plan_between(
    node_map: dict,
    adjacency: dict,
    start: tuple,
    target: tuple,
    cutoff_m: float,
    mode: str,
) -> list[dict] | None:
    """The mode's cheapest chain of steps between two network nodes.

    Returns the steps taken, each carrying its origin and destination nodes,
    or ``None`` when nothing inside the corridor connects the pair.
    """
    weight_of = _weights_for(mode, node_map)
    dist = {start: 0.0}
    prev: dict[tuple, dict] = {}
    heap = [(0.0, 0, start)]
    tie = 0
    while heap:
        d, tie, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf):
            continue
        if u == target:
            steps = []
            cursor = target
            while cursor != start:
                steps.append(prev[cursor])
                cursor = prev[cursor]["from"]
            steps.reverse()
            return steps
        if d > cutoff_m:
            break
        for step in adjacency.get(u, []):
            v = step["to"]
            if v not in node_map:
                continue
            if step.get("step_max", 0.0) > _C.TRAIL_GRAPH_MAX_STEP_M:
                continue
            nd = d + weight_of(u, step)
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = {**step, "from": u, "from_node": node_map[u], "to_node": node_map[v]}
                tie += 1
                heapq.heappush(heap, (nd, tie, v))
    return None


def _weights_for(mode: str, node_map: dict):
    """The mode's cost function over one step into one node."""

    def weight(u: tuple, step: dict) -> float:
        kind = step.get("kind")
        if kind is None:
            # a walked transition costs the ground between the cells, as in
            # walk_between: the per-pass mean length encodes detours, not the
            # distance a plan now covering that edge would actually walk
            base = max(_ground_m(node_map[u]["xy"], node_map[step["to"]]["xy"]), 5.0)
        else:
            base = max(step["len"], 5.0)
        if kind == "link":
            return base
        if kind == "way":
            known = step["passes"] > 0 or step["walked"]
            if mode == "curt":
                return base * (_C.TRAIL_ROUTE_GEN_UNKNOWN_PENALTY if not known else 1.0)
            if mode == "tecnic":
                if step["tech"]:
                    return base * _C.TRAIL_ROUTE_GEN_TECH_PREFER
                return base * (_C.TRAIL_ROUTE_GEN_FLAT_PENALTY if not known else 1.0)
            return base * (_C.TRAIL_ROUTE_GEN_WAY_PREFER if not known else 1.0)
        if mode == "tecnic":
            dest = node_map.get(step["to"], {})
            return base * (_C.TRAIL_ROUTE_GEN_TECH_PREFER if dest.get("grade") in ("hard", "tech") else 1.0)
        if mode == "tanteig":
            heavy = step.get("passes", 0) > _C.TRAIL_ROUTE_GEN_HEAVY_PASSES
            return base * (1.0 if heavy else _C.TRAIL_ROUTE_GEN_LIGHT_PENALTY)
        return base

    return weight


def geometry_and_gaps(steps: list[dict]) -> tuple[list[list[float]], list[tuple[float, float]]]:
    """The plan's polyline and the stretches of it that stay provisional.

    Args:
        steps: The chain returned by :func:`plan_between`.

    Returns:
        ``(geometry, unknown_ranges)``: lat/lng vertices in order, and the
        ``(start_m, end_m)`` intervals walked over mapped ground nobody has
        crossed, measured along the plan itself; gaps under 100 m merge.
    """
    geometry: list[list[float]] = []
    cum = 0.0
    unknown: list[list[float]] = []
    for index, step in enumerate(steps):
        if index == 0:
            geometry.append([step["from_node"]["lat"], step["from_node"]["lng"]])
        geometry.append([step["to_node"]["lat"], step["to_node"]["lng"]])
        seg_m = _ground_m(step["from_node"]["xy"], step["to_node"]["xy"])
        if step.get("kind") == "way" and not (step["passes"] > 0 or step["walked"]):
            unknown.append([cum, cum + seg_m])
        cum += seg_m
    merged: list[list[float]] = []
    for start_m, end_m in unknown:
        if merged and start_m - merged[-1][1] <= 100.0:
            merged[-1][1] = max(merged[-1][1], end_m)
        else:
            merged.append([start_m, end_m])
    return geometry, [(s, e) for s, e in merged if e - s >= 100.0]
