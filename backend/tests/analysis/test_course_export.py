"""The Garmin course writer: geometry in, parseable course out."""

import pytest
from fit_tool.fit_file import FitFile
from fit_tool.profile.profile_type import CoursePoint

from modules.analysis import course_export


class TestBuildCourseBytes:
    def test_a_course_round_trips_with_its_points(self):
        geo = [(42.3, 1.5), (42.3001, 1.5005), (42.3002, 1.501)]
        data = course_export.build_course_bytes(
            "prova",
            geo,
            [100.0, 101.0, 102.0],
            [(70.0, "DUR 0.1k", CoursePoint.DANGER), (150.0, "VE+0.1k", CoursePoint.LEFT)],
        )
        parsed = FitFile.from_bytes(data)
        counts: dict[str, int] = {}
        for record in parsed.records:
            if record.is_definition or record.message is None:
                continue
            counts[record.message.name] = counts.get(record.message.name, 0) + 1
        assert counts["record"] == 3
        assert counts["course_point"] == 2
        assert counts["course"] == 1

    def test_unknown_altitudes_are_simply_omitted(self):
        geo = [(42.3, 1.5), (42.3001, 1.5005)]
        data = course_export.build_course_bytes("sense alts", geo, [None, None], [])
        parsed = FitFile.from_bytes(data)
        records = [
            r.message
            for r in parsed.records
            if not r.is_definition and r.message is not None and r.message.name == "record"
        ]
        assert records and all(m.altitude is None for m in records)

    def test_a_line_needs_two_points(self):
        with pytest.raises(ValueError):
            course_export.build_course_bytes("res", [(42.3, 1.5)], [100.0], [])


class TestNamesAndDistances:
    def test_names_are_clipped_to_what_the_watch_shows(self):
        assert len(course_export.clip_text("Pista de l'Hospitalet de la Riba").encode()) <= 15
        assert course_export.clip_text("curta") == "curta"

    def test_cumulative_ground_distance(self):
        geo = [(42.3, 1.5), (42.3001, 1.5)]
        cum = course_export.cumulative(geo)
        assert cum[0] == 0.0
        assert 10.0 < cum[1] < 12.0
