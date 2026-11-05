"""Matching a measured stretch to the way drawn under it."""

import modules.analysis.trail_osm as analysis_trail_osm

# A 1 deg lat step is ~111 km; these offsets are a few hundred metres apart.
_BASE_LAT = 42.47
_BASE_LNG = 0.99


def _along(offsets_m: list[float]) -> list[tuple[float, float]]:
    """Return points north of the base point, at the given metre offsets."""
    return [(_BASE_LAT + offset / 111195.0, _BASE_LNG) for offset in offsets_m]


def _east(offsets_m: list[float]) -> list[tuple[float, float]]:
    """Return points east of the base point, at the given metre offsets."""
    cos_lat = 0.7375  # cos(42.47 deg)
    return [(_BASE_LAT, _BASE_LNG + offset / (111320.0 * cos_lat)) for offset in offsets_m]


class TestFindsTheWayUnderTheStretch:
    def test_a_stretch_walked_along_a_way_matches_it(self):
        stretch = _along([0.0, 60.0, 120.0, 200.0])
        way = analysis_trail_osm.CandidateWay(way_id=1, points=_along([0.0, 500.0, 1500.0]))
        match = analysis_trail_osm.match_stretch(stretch, [way])
        assert match is not None
        assert match.way_id == 1
        assert match.start_m == 0.0
        assert match.end_m > 190.0
        assert match.mean_offset_m < 1.0
        assert match.reverse is False

    def test_the_same_stretch_walked_backwards_reads_as_reverse(self):
        stretch = list(reversed(_along([0.0, 60.0, 120.0, 200.0])))
        way = analysis_trail_osm.CandidateWay(way_id=1, points=_along([0.0, 500.0, 1500.0]))
        match = analysis_trail_osm.match_stretch(stretch, [way])
        assert match is not None
        assert match.reverse is True
        assert match.start_m > match.end_m


class TestRefusesTheWrongMatch:
    def test_a_way_that_merely_crosses_the_stretch_is_not_a_match(self):
        stretch = _along([0.0, 60.0, 120.0, 200.0])
        crossing = analysis_trail_osm.CandidateWay(way_id=7, points=_east([-400.0, 0.0, 400.0]))
        assert analysis_trail_osm.match_stretch(stretch, [crossing]) is None

    def test_a_longer_way_that_passes_nearby_is_not_enough(self):
        """The middle of a long track can sit next to a stretch without being it."""
        stretch = _along([0.0, 30.0, 60.0])
        far = analysis_trail_osm.CandidateWay(
            way_id=9,
            points=_east([-2000.0, -50.0, 0.0, 50.0, 2000.0]),
        )
        assert analysis_trail_osm.match_stretch(stretch, [far]) is None

    def test_the_nearest_of_two_parallel_paths_wins(self):
        stretch = _east([0.0, 80.0, 160.0, 240.0])
        near = analysis_trail_osm.CandidateWay(way_id=11, points=_east([0.0, 300.0]))
        far = analysis_trail_osm.CandidateWay(
            way_id=12,
            points=[(point[0] + 40.0 / 111195.0, point[1]) for point in _east([0.0, 300.0])],
        )
        match = analysis_trail_osm.match_stretch(stretch, [far, near])
        assert match is not None
        assert match.way_id == 11

    def test_unmapped_ground_answers_none(self):
        stretch = _along([0.0, 60.0, 120.0])
        assert analysis_trail_osm.match_stretch(stretch, []) is None
        assert analysis_trail_osm.match_stretch(stretch, [analysis_trail_osm.CandidateWay(way_id=1, points=[])]) is None

    def test_a_single_point_stretch_cannot_be_matched(self):
        way = analysis_trail_osm.CandidateWay(way_id=1, points=_along([0.0, 500.0]))
        assert analysis_trail_osm.match_stretch([(_BASE_LAT, _BASE_LNG)], [way]) is None
