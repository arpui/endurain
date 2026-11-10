"""The A→B planner: two strata, one search, three intentions."""

import math
from types import SimpleNamespace

import pytest

import core.exceptions as core_exceptions
import modules.analysis.constants as analysis_constants
import modules.analysis.graph as analysis_graph
import modules.analysis.route_plan as route_plan

REF_LAT = analysis_constants.TRAIL_GRAPH_REF_LAT
REF_LNG = analysis_constants.TRAIL_GRAPH_REF_LNG
CELL = analysis_constants.TRAIL_GRAPH_CELL_M


def _latlng(x_cells: float, y_cells: float) -> tuple[float, float]:
    return (REF_LAT + y_cells * CELL / 111195.0, REF_LNG + x_cells * CELL / (111320.0 * 0.74))


def _graph_chain(n: int, *, passes: int = 3) -> tuple[list, list]:
    """A walked line of n cells along y=0."""
    nodes = []
    edges = []
    for k in range(n):
        lat, lng = _latlng(k, 0)
        nodes.append(SimpleNamespace(x=k, y=0, lat=lat, lng=lng, ele=100.0, grade=""))
        if k:
            edges.append(
                SimpleNamespace(
                    ax=k - 1, ay=0, bx=k, by=0, length=CELL, gain=0.0, loss=0.0, passes=passes, step_max=5.0, on_path=0
                )
            )
    return nodes, edges


def _way(way_id: int, pts: list[tuple[float, float]], *, highway: str = "path") -> tuple[int, dict, list]:
    return (way_id, {"highway": highway}, [list(p) for p in pts])


class TestCorridor:
    def test_the_same_twice_is_not_a_journey(self):
        with pytest.raises(core_exceptions.InvalidInputError):
            route_plan.corridor(_latlng(0, 0), _latlng(0, 0))

    def test_too_far_apart_refuses_to_plan(self):
        far_x = int(analysis_constants.TRAIL_ROUTE_GEN_MAX_STRAIGHT_M / CELL) + 10
        with pytest.raises(core_exceptions.InvalidInputError):
            route_plan.corridor(_latlng(0, 0), _latlng(far_x, 0))

    def test_the_boxes_cover_the_corridor(self):
        _straight, cutoff, boxes = route_plan.corridor(_latlng(0, 0), _latlng(40, 0))
        gx0, gx1, gy0, gy1 = boxes["graph"]
        assert gx0 < 0 and gx1 > 40 and gy0 < 0 and gy1 > 0
        assert cutoff > 3.0 * 40 * CELL


class TestModes:
    def test_curt_stays_on_walked_ground_when_it_is_not_longer(self):
        nodes, edges = _graph_chain(21)
        a, b = _latlng(0, 0), _latlng(20, 0)
        way = _way(7, [_latlng(0, 2), _latlng(10, 4), _latlng(20, 2)])  # a longer untracked promise
        node_map, adjacency, start, target = route_plan.build_layers(a, b, nodes, edges, [way], {}, 3000.0)
        steps = route_plan.plan_between(node_map, adjacency, start, target, 3000.0, "curt")
        assert steps is not None
        assert all(step.get("kind") != "way" for step in steps)
        geometry, unknown = route_plan.geometry_and_gaps(steps)
        assert unknown == []
        assert len(geometry) == 21

    def test_tanteig_prefers_mapped_ground_nobody_has_crossed(self):
        nodes, edges = _graph_chain(21)
        a, b = _latlng(0, 0), _latlng(20, 0)
        way = _way(7, [_latlng(0, 2), _latlng(10, 2), _latlng(20, 2)])  # parallel, near, never walked
        node_map, adjacency, start, target = route_plan.build_layers(a, b, nodes, edges, [way], {}, 3000.0)
        steps = route_plan.plan_between(node_map, adjacency, start, target, 3000.0, "tanteig")
        assert steps is not None
        assert any(step.get("kind") == "way" for step in steps)
        _geometry, unknown = route_plan.geometry_and_gaps(steps)
        assert unknown, "the provisional stretch must be reported"

    def test_heavily_walked_ground_is_infrastructure_even_for_tanteig(self):
        # a short walked detour that has been crossed a lot beats a long virgin
        # promise; the same detour crossed only a little does not
        nodes, edges = _graph_chain(11)
        for e in edges:
            e.passes = 40
        # an arch: its virgin promise (cost ~60 m of links + 0.55x the length)
        # beats dodged light ground and loses to infrastructure
        way = _way(9, [_latlng(0, 2), _latlng(4, 6), _latlng(6, 6), _latlng(10, 2)])
        a, b = _latlng(0, 0), _latlng(10, 0)
        node_map, adjacency, start, target = route_plan.build_layers(a, b, nodes, edges, [way], {}, 3000.0)
        steps = route_plan.plan_between(node_map, adjacency, start, target, 3000.0, "tanteig")
        assert steps is not None
        assert all(step.get("kind") != "way" for step in steps)
        for e in edges:
            e.passes = 5
        node_map, adjacency, start, target = route_plan.build_layers(a, b, nodes, edges, [way], {}, 3000.0)
        steps = route_plan.plan_between(node_map, adjacency, start, target, 3000.0, "tanteig")
        assert any(step.get("kind") == "way" for step in steps)

    def test_tecnic_walks_into_the_hard_cells(self):
        nodes, edges = _graph_chain(21)
        for k in range(6, 15):
            nodes[k].grade = "hard"
        a, b = _latlng(0, 0), _latlng(20, 0)
        way = _way(7, [_latlng(0, 2), _latlng(10, 2), _latlng(20, 2)], highway="footway")
        node_map, adjacency, start, target = route_plan.build_layers(a, b, nodes, edges, [way], {}, 3000.0)
        steps = route_plan.plan_between(node_map, adjacency, start, target, 3000.0, "tecnic")
        assert all(step.get("kind") != "way" for step in steps)


class TestGroundRules:
    def test_a_way_under_the_athletes_own_footsteps_is_not_provisional(self):
        nodes, edges = _graph_chain(11)
        way = _way(3, [_latlng(0, 0), _latlng(5, 0), _latlng(10, 0)])  # the way repeats walked cells
        a, b = _latlng(0, 0), _latlng(10, 0)
        node_map, adjacency, start, target = route_plan.build_layers(a, b, nodes, edges, [way], {}, 3000.0)
        steps = route_plan.plan_between(node_map, adjacency, start, target, 3000.0, "tanteig")
        _geometry, unknown = route_plan.geometry_and_gaps(steps)
        assert unknown == []

    def test_an_endpoint_nowhere_honest_refuses(self):
        nodes, edges = _graph_chain(5)
        a, b = _latlng(0, 0), _latlng(1000, 900)
        with pytest.raises(core_exceptions.InvalidInputError):
            route_plan.build_layers(a, b, nodes, edges, [], {}, 3000.0)

    def test_crossed_ways_are_known_ground(self):
        nodes, edges = _graph_chain(11)
        way = _way(4, [_latlng(0, 2), _latlng(10, 2), _latlng(11, 0)])
        a, b = _latlng(0, 0), _latlng(11, 0)
        node_map, adjacency, start, target = route_plan.build_layers(a, b, nodes, edges, [way], {4: 2}, 3000.0)
        steps = route_plan.plan_between(node_map, adjacency, start, target, 3000.0, "tanteig")
        _geometry, unknown = route_plan.geometry_and_gaps(steps)
        assert unknown == []

    def test_technical_tags(self):
        assert route_plan.way_is_technical({"highway": "path"})
        assert route_plan.way_is_technical({"highway": "footway", "sac_scale": "T3"})
        assert not route_plan.way_is_technical({"highway": "footway"})
        assert not route_plan.way_is_technical(None)


class TestGroundProfile:
    def test_the_climb_survives_and_the_cell_disagreement_does_not(self):
        cum = [15.0 * k for k in range(60)]
        steady = [1000.0 + k for k in range(60)]
        noisy = [value + (8.0 if k % 2 else -8.0) for k, value in enumerate(steady)]
        gain = analysis_graph.profile_gain(analysis_graph.ground_profile(cum, noisy))
        assert 50.0 <= gain <= 62.0  # 59 m of real climb; the raw rises sum to ~500

    def test_unknown_ground_claims_no_altitude_and_no_gain(self):
        cum = [0.0, 15.0, 30.0, 45.0]
        profile = analysis_graph.ground_profile(cum, [100.0, None, None, 300.0])
        assert profile[1] is None and profile[2] is None
        assert analysis_graph.profile_gain(profile) == 0.0


class TestActivityGraphRows:
    @staticmethod
    def _rows(monkeypatch, altitudes):
        from datetime import datetime, timedelta

        start = datetime(2026, 1, 1, 8, 0, 0)
        # 3 m per second eastwards: five samples inside every 15 m cell
        step_lng = 3.0 / (111320.0 * math.cos(math.radians(analysis_constants.TRAIL_GRAPH_REF_LAT)))
        track = [
            (
                start + timedelta(seconds=k),
                analysis_constants.TRAIL_GRAPH_REF_LAT,
                analysis_constants.TRAIL_GRAPH_REF_LNG + (k - 2) * step_lng,
            )
            for k in range(len(altitudes))
        ]
        samples = [(stamp, alt) for (stamp, _lat, _lng), alt in zip(track, altitudes, strict=True) if alt is not None]
        monkeypatch.setattr(analysis_graph.analysis_service, "_trail_track", lambda *_a: track)
        monkeypatch.setattr(
            analysis_graph.analysis_service,
            "_stream_samples",
            lambda *_a, **_k: (None, tuple(alt for _s, alt in samples), tuple(stamp for stamp, _a in samples)),
        )
        monkeypatch.setattr(analysis_graph, "way_geometries_for_activity", lambda *_a: [])
        monkeypatch.setattr(analysis_graph.analysis_crud, "list_trail_observation", lambda *_a: None)
        return analysis_graph.activity_graph_rows(1, 1, None)

    def test_a_traversal_climbs_what_the_whole_cell_climbed(self, monkeypatch):
        # one metre up per sample, five samples per cell: each crossing is 5 m of climb
        _nodes, edges, _grades = self._rows(monkeypatch, [100.0 + k for k in range(30)])
        assert len(edges) == 5
        assert all(edge["gain"] == pytest.approx(5.0) and edge["loss"] == 0.0 for edge in edges)

    def test_a_track_without_altitude_does_not_lower_the_cell(self, monkeypatch):
        graph_nodes: dict = {}
        with_altitude, _edges, _grades = self._rows(monkeypatch, [500.0] * 30)
        analysis_graph.merge_activity_into_graph(graph_nodes, {}, with_altitude, [])
        # the same ground again, its altimeter silent for the first minute
        silent, _edges, _grades = self._rows(monkeypatch, [None] * 60 + [500.0] * 5)
        assert min(silent, key=lambda row: row["x"])["ele_visits"] == 0
        analysis_graph.merge_activity_into_graph(graph_nodes, {}, silent, [])
        first = graph_nodes[(0, 0)]
        assert first["visits"] == 10 and first["ele_visits"] == 5
        assert first["ele_sum"] / first["ele_visits"] == pytest.approx(500.0)
