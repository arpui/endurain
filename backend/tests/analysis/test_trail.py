"""Technical-terrain detection: the anomaly model over spatial windows."""

import numpy as np

import modules.analysis.constants as analysis_constants
import modules.analysis.trail as analysis_trail


def _session(n_s: int, speed: float, grade: float, cad, start_dist: float = 0.0):
    """Constant-speed samples over a constant grade, with cadence per second."""
    speeds = [speed] * n_s
    ele, dist = [], []
    z = 0.0
    d = start_dist
    for _ in range(n_s):
        dist.append(d)
        ele.append(z)
        z += speed * grade  # one second of travel at this grade
        d += speed
    cadence = [float(cad)] * n_s if isinstance(cad, (int, float)) else [float(cad[i % len(cad)]) for i in range(n_s)]
    return speeds, dist, ele, cadence


class TestOrdinaryGround:
    def test_a_steady_flat_run_is_never_technical(self):
        s, d, e, c = _session(600, 3.0, 0.0, 160)
        out = analysis_trail.analyse_technical_sections(s, d, e, c)
        assert out.sections == []
        assert out.v_base_ms > 0

    def test_a_steep_smooth_climb_is_grade_not_terrain(self):
        """Minetti already explains the slow pace on a steep smooth ramp."""
        flat = _session(300, 3.0, 0.0, 160)
        s, d, e, c = _session(400, 1.6, 0.15, 150, start_dist=flat[1][-1] + 3.0)
        out = analysis_trail.analyse_technical_sections([*flat[0], *s], [*flat[1], *d], [*flat[2], *e], [*flat[3], *c])
        assert out.sections == []

    def test_flat_rests_are_not_terrain(self):
        walk = _session(300, 1.0, 0.0, 120)
        out = analysis_trail.analyse_technical_sections(*walk)
        assert out.sections == []

    def test_no_cadence_still_answers_with_the_speed_term(self):
        s, d, e, _c = _session(600, 3.0, 0.0, 160)
        out = analysis_trail.analyse_technical_sections(s, d, e, None)
        assert out.cadence_available is False
        assert out.sections == []


class TestTechnicalGround:
    def test_a_braking_jittery_descent_is_technical(self):
        flat = _session(300, 3.0, 0.0, 160)
        # -15% at 1.3 m/s, cadence swinging (micropasses): stride 0.52 m
        s, d, e, c = _session(400, 1.3, -0.15, [130, 175, 140, 180], start_dist=flat[1][-1] + 1.3)
        out = analysis_trail.analyse_technical_sections([*flat[0], *s], [*flat[1], *d], [*flat[2], *e], [*flat[3], *c])
        assert len(out.sections) >= 1
        section = out.sections[0]
        assert section.direction == "descent"
        assert section.brake_score > 0.5
        assert section.score >= 0.38

    def test_sections_carry_their_sample_range(self):
        flat = _session(300, 3.0, 0.0, 160)
        s, d, e, c = _session(400, 1.3, -0.15, [130, 175, 140, 180], start_dist=flat[1][-1] + 1.3)
        out = analysis_trail.analyse_technical_sections([*flat[0], *s], [*flat[1], *d], [*flat[2], *e], [*flat[3], *c])
        sec = out.sections[0]
        assert 0 <= sec.start_index < sec.end_index <= len(flat[0]) + len(s)

    def test_short_blips_stay_under_the_report(self):
        """One 25 m window of noise is smoothed away below the minimum length."""
        s, d, e, c = _session(600, 3.0, 0.0, 160)
        s2, d2, e2, c2 = _session(10, 0.9, -0.2, [90, 210], start_dist=d[-1] + 3.0)
        s3, d3, e3, c3 = _session(600, 3.0, 0.0, 160, start_dist=d2[-1] + 0.9)
        out = analysis_trail.analyse_technical_sections([*s, *s2, *s3], [*d, *d2, *d3], [*e, *e2, *e3], [*c, *c2, *c3])
        assert all(sec.length_m >= 75 for sec in out.sections)


class TestRobustness:
    def test_tiny_sessions_answer_empty_not_crash(self):
        out = analysis_trail.analyse_technical_sections([1.0] * 10, list(range(10)), [0.0] * 10, [150.0] * 10)
        assert out.sections == []

    def test_nan_cadence_gaps_do_not_break_stride(self):
        s, d, e, c = _session(600, 3.0, 0.0, 160)
        c = [np.nan if i % 3 == 0 else v for i, v in enumerate(c)]
        out = analysis_trail.analyse_technical_sections(s, d, e, c)
        assert out.sections == []


class TestHardCoresInsideAStretch:
    """A long stretch holds a block of hard ground, not a uniform hardness."""

    def test_a_long_stretch_reports_where_inside_it_the_ground_is_hardest(self):
        flat = _session(300, 3.0, 0.0, 160)
        # 600 m of hard descent, then 600 m of merely steep-but-rough descent
        hard = _session(500, 1.25, -0.28, [130, 185, 140, 180], start_dist=flat[1][-1] + 1.25)
        easier = _session(
            500,
            1.7,
            -0.26,
            [155, 168, 158, 165],
            start_dist=hard[1][-1] + 1.7,
        )
        out = analysis_trail.analyse_technical_sections(
            [*flat[0], *hard[0], *easier[0]],
            [*flat[1], *hard[1], *easier[1]],
            [*flat[2], *hard[2], *easier[2]],
            [*flat[3], *hard[3], *easier[3]],
        )
        section = max(out.sections, key=lambda item: item.length_m)
        assert section.core_start_m is not None and section.core_end_m is not None
        assert section.core_score is not None
        assert section.core_score >= analysis_constants.TRAIL_HARD_RATIO * analysis_constants.TRAIL_THRESHOLD
        # the core sits inside the stretch, and is shorter than it
        assert section.core_start_m >= section.start_m
        assert section.core_end_m <= section.start_m + section.length_m
        assert section.core_end_m - section.core_start_m < section.length_m

    def test_a_uniformly_medium_stretch_has_no_hard_core(self):
        flat = _session(300, 3.0, 0.0, 160)
        # technical by the cut, but nowhere near the hard ratio
        s, d, e, c = _session(900, 1.9, -0.12, [150, 172, 155, 168], start_dist=flat[1][-1] + 1.9)
        out = analysis_trail.analyse_technical_sections([*flat[0], *s], [*flat[1], *d], [*flat[2], *e], [*flat[3], *c])
        assert out.sections
        for section in out.sections:
            assert section.core_start_m is None
            assert section.core_score is None
