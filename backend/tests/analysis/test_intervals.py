"""Tests for splitting one activity into the efforts it contained.

The cases are the shapes an athlete actually trains: reps with real recoveries, a race
pushed for part of its distance and then cruised, a steady tempo day, hill pushes on
mountain terrain, and an easy day. What the segmentation must never do is decide from a
general population's numbers: every verdict here follows from the athlete's own
threshold and the session's own median, which is why the same wattage reads as a rep on
one day and as a cruise on another.
"""

import pytest

import modules.analysis.intervals as analysis_intervals
import modules.analysis.maths as analysis_maths
import modules.analysis.methods as analysis_methods

CP = 350.0


def _reps(count: int, work_s: int, work_w: float, rest_s: int, rest_w: float) -> list[tuple[int, float]]:
    """Return ``count`` ``(work, rest)`` block pairs."""
    return [(work_s, work_w), (rest_s, rest_w)] * count


def _session(*blocks: tuple[int, float]) -> tuple[list[float], list[float], list[float]]:
    """Build ``(power, speed, grade)`` samples from ``(seconds, watts)`` blocks.

    Speed and grade follow the power loosely (a run at about 3.5-4.5 m/s) so distance
    and pace come out plausible; grade is zero unless a block asks for it.
    """
    power: list[float] = []
    speed: list[float] = []
    grade: list[float] = []
    for seconds, watts in blocks:
        power.extend([watts] * seconds)
        speed.extend([3.5 + watts / 400.0] * seconds)
        grade.extend([0.0] * seconds)
    return power, speed, grade


def _analyse(*blocks: tuple[int, float], **kwargs) -> analysis_intervals.IntervalAnalysis:
    """Segment a synthetic session given as ``(seconds, watts)`` blocks."""
    power, speed, grade = _session(*blocks)
    return analysis_intervals.analyse_activity(
        power_w=power,
        period_s=1.0,
        threshold_w=kwargs.pop("threshold_w", CP),
        speeds_ms=speed,
        grades_pct=kwargs.pop("grades_pct", grade),
        **kwargs,
    )


def _efforts(analysis: analysis_intervals.IntervalAnalysis) -> list[analysis_intervals.Segment]:
    """Return the effort segments (the blocks the athlete worked in)."""
    return [
        s
        for s in analysis.segments
        if s.kind
        in (
            analysis_intervals.SegmentKind.INTERVAL,
            analysis_intervals.SegmentKind.CLIMB,
            analysis_intervals.SegmentKind.RACE,
        )
    ]


class TestRepetitionSessions:
    def test_reps_with_recoveries_are_read_as_intervals(self):
        analysis = _analyse(
            (300, 250.0),  # warm-up
            *_reps(5, 120, 430.0, 120, 120.0),
            (300, 240.0),  # cooldown
        )
        efforts = _efforts(analysis)
        assert len(efforts) == 5
        assert analysis.shape is analysis_intervals.SessionShape.INTERVALS
        assert all(effort.intensity_factor > 1.0 for effort in efforts)
        assert all(effort.contrast > 1.2 for effort in efforts)

    def test_the_recoveries_between_them_are_named(self):
        analysis = _analyse(*_reps(4, 120, 430.0, 120, 110.0))
        kinds = [segment.kind for segment in analysis.segments]
        recovery = analysis_intervals.SegmentKind.RECOVERY
        # Three rests between four reps, plus the closing one after the last.
        assert kinds.count(recovery) == 4

    def test_segments_cover_the_whole_timeline_without_gaps(self):
        analysis = _analyse((300, 250.0), (120, 430.0), (120, 120.0), (120, 430.0), (300, 240.0))
        total = sum(segment.duration_s for segment in analysis.segments)
        covered = sum(segment.duration_s for _, segment in enumerate(analysis.segments))
        assert total == covered
        assert analysis.segments[0].start_s == 0.0
        previous_end = 0.0
        for segment in analysis.segments:
            assert segment.start_s == previous_end
            previous_end = segment.start_s + segment.duration_s

    def test_reps_of_very_unlike_lengths_read_as_fartlek(self):
        analysis = _analyse(
            (200, 250.0),
            (30, 520.0),
            (120, 120.0),
            (420, 400.0),
            (180, 130.0),
            (60, 500.0),
            (120, 120.0),
        )
        assert len(_efforts(analysis)) == 3
        assert analysis.shape is analysis_intervals.SessionShape.FARTLEK


class TestContinuousEfforts:
    def test_a_race_pushed_then_cruised_keeps_one_interval_of_the_pushed_part(self):
        """The athlete's own reading: 4 km hard inside a 10 km race, the rest easy."""
        analysis = _analyse((1000, 390.0), (2100, 300.0))
        efforts = _efforts(analysis)
        assert len(efforts) == 1
        assert efforts[0].duration_s < 1200  # the hard stretch, not the whole race
        assert efforts[0].intensity_factor > 1.0
        assert efforts[0].distance_m > 3500

    def test_a_steady_race_reads_as_one_continuous_effort_with_no_reps(self):
        analysis = _analyse((3000, 330.0))
        assert not _efforts(analysis)
        assert analysis.shape is analysis_intervals.SessionShape.CONTINUOUS

    def test_a_hard_block_covering_the_session_is_the_race_itself(self):
        analysis = _analyse((600, 250.0), (1800, 335.0), (300, 250.0))
        efforts = _efforts(analysis)
        assert all(effort.kind is not analysis_intervals.SegmentKind.INTERVAL for effort in efforts)


class TestEasyDays:
    def test_an_easy_run_produces_nothing_but_easy(self):
        analysis = _analyse((2400, 240.0))
        assert not _efforts(analysis)
        assert analysis.shape is analysis_intervals.SessionShape.STEADY

    def test_a_short_stride_inside_an_easy_run_is_reported_on_its_own(self):
        """A 25 s surge is a surge, not a rep set: seen, but not read as intervals."""
        analysis = _analyse((600, 230.0), (25, 520.0), (600, 235.0))
        efforts = _efforts(analysis)
        assert len(efforts) == 1
        assert efforts[0].duration_s < 40
        assert analysis.shape is not analysis_intervals.SessionShape.INTERVALS

    def test_without_power_there_is_nothing_to_segment(self):
        analysis = analysis_intervals.analyse_activity(power_w=[], period_s=1.0, threshold_w=CP)
        assert analysis.shape is analysis_intervals.SessionShape.STEADY
        assert analysis.segments == ()


class TestClimbs:
    def _hilly(self, pushes: list[tuple[int, float]], base_seconds: int, grade_during: float):
        """Steady running with grade-driven pushes: power rises because the road rises."""
        power, speed, grade = [], [], []
        for seconds, watts in pushes:
            power.extend([240.0] * base_seconds)
            speed.extend([4.0] * base_seconds)
            grade.extend([0.0] * base_seconds)
            power.extend([watts] * seconds)
            speed.extend([3.0] * seconds)
            grade.extend([grade_during] * seconds)
        return power, speed, grade

    def test_grade_explained_efforts_are_terrain_not_intervals(self):
        power, speed, grade = self._hilly([(60, 370.0), (45, 360.0), (90, 380.0)], 400, 8.0)
        analysis = analysis_intervals.analyse_activity(
            power_w=power, period_s=1.0, threshold_w=CP, speeds_ms=speed, grades_pct=grade
        )
        efforts = _efforts(analysis)
        assert efforts
        assert all(effort.kind is analysis_intervals.SegmentKind.CLIMB for effort in efforts)
        assert analysis.shape is analysis_intervals.SessionShape.TERRAIN

    def test_a_hill_rep_pushed_far_above_threshold_is_still_a_rep(self):
        # Same gradient, but the effort is far harder than the grade alone explains.
        power, speed, grade = self._hilly([(30, 560.0), (30, 570.0), (30, 580.0)], 120, 7.0)
        analysis = analysis_intervals.analyse_activity(
            power_w=power, period_s=1.0, threshold_w=CP, speeds_ms=speed, grades_pct=grade
        )
        efforts = _efforts(analysis)
        assert len(efforts) == 3
        assert all(effort.kind is analysis_intervals.SegmentKind.INTERVAL for effort in efforts)

    def test_without_a_grade_no_climb_is_claimed(self):
        power, speed, _grade = self._hilly([(60, 370.0), (60, 375.0)], 400, 8.0)
        analysis = analysis_intervals.analyse_activity(power_w=power, period_s=1.0, threshold_w=CP, speeds_ms=speed)
        assert all(effort.kind is analysis_intervals.SegmentKind.INTERVAL for effort in _efforts(analysis))
        assert all(segment.avg_grade_pct is None for segment in analysis.segments)


class TestThresholdsComeFromTheAthlete:
    def test_without_a_stored_threshold_the_session_median_alone_decides(self):
        blocks = ((200, 200.0), (120, 340.0), (120, 90.0), (120, 340.0), (120, 90.0), (200, 195.0))
        with_threshold = _analyse(*blocks, threshold_w=300.0)
        without_threshold = _analyse(*blocks, threshold_w=None)
        assert len(_efforts(with_threshold)) == 2
        assert len(_efforts(without_threshold)) == 2
        assert all(segment.intensity_factor == 0.0 for segment in without_threshold.segments)

    def test_a_different_threshold_reads_the_same_session_differently(self):
        """Hard is relative to the athlete: 380 W tops a 300 W threshold, not a 400 W one."""
        blocks = [(600, 240.0), (150, 380.0), (150, 130.0), (150, 380.0), (300, 235.0)]
        below = _analyse(*blocks, threshold_w=400.0)
        above = _analyse(*blocks, threshold_w=300.0)
        assert not _efforts(below)
        assert len(_efforts(above)) == 2

    def test_the_session_median_scales_what_reads_as_an_effort(self):
        """The same 380 W surge is a rep in an easy day and a wobble in a hard one."""
        easy_day = [(600, 240.0), (150, 380.0), (300, 250.0)]
        hard_day = [(600, 350.0), (150, 380.0), (300, 355.0)]
        assert _efforts(_analyse(*easy_day))
        assert not _efforts(_analyse(*hard_day))

    def test_the_athlete_can_move_the_knobs(self):
        blocks = ((300, 250.0), (120, 400.0), (200, 240.0))
        assert _efforts(_analyse(*blocks))
        assert not _efforts(_analyse(*blocks, parameters={"iv_enter_base": 1.8, "iv_enter_cp": 1.5}))

    def test_a_stricter_minimum_length_drops_the_short_reps(self):
        blocks = ((300, 250.0), (40, 560.0), (120, 130.0), (40, 560.0), (300, 245.0))
        assert len(_efforts(_analyse(*blocks))) == 2
        assert not _efforts(_analyse(*blocks, parameters={"iv_min_segment_s": 60}))


class TestFlatEffortQuality:
    """A flat stretch earns the interval label by holding above threshold, not by
    touching it: grazing the opening gate and then cruising below it is tempo."""

    def test_a_flat_run_below_the_average_gate_is_not_an_interval(self):
        # 200 s at 360 W (1,03x CP) after a real rest: tempo out, not a rep.
        analysis = _analyse((300, 260.0), (120, 430.0), (300, 150.0), (200, 360.0), (300, 255.0))
        assert len(_efforts(analysis)) == 1
        easy = [s for s in analysis.segments if s.kind is analysis_intervals.SegmentKind.EASY]
        assert any(segment.duration_s >= 180 for segment in easy)

    def test_the_same_length_held_above_the_gate_still_counts(self):
        analysis = _analyse((300, 260.0), (120, 430.0), (300, 150.0), (200, 380.0), (300, 255.0))
        assert len(_efforts(analysis)) == 2

    def test_the_gate_is_the_athlete_s_own_knob(self):
        blocks = ((300, 260.0), (200, 360.0), (300, 255.0))
        assert not _efforts(_analyse(*blocks))
        assert len(_efforts(_analyse(*blocks, parameters={"iv_effort_if": 1.0}))) == 1

    def test_a_graded_push_below_the_flat_gate_is_explained_by_terrain(self):
        power = [250.0] * 300 + [360.0] * 150 + [250.0] * 300
        speed = [4.0] * 300 + [3.0] * 150 + [4.0] * 300
        grade = [0.0] * 300 + [7.0] * 150 + [0.0] * 300
        analysis = analysis_intervals.analyse_activity(
            power_w=power, period_s=1.0, threshold_w=CP, speeds_ms=speed, grades_pct=grade
        )
        efforts = _efforts(analysis)
        assert len(efforts) == 1
        assert efforts[0].kind is analysis_intervals.SegmentKind.CLIMB

    def test_the_answer_names_the_gates_it_used(self):
        analysis = _analyse((300, 250.0), (120, 430.0), (120, 120.0), (300, 245.0))
        assert analysis.rules is not None
        assert analysis.rules["open_w"] == 350.0  # 1,00xCP beats 1,15x median here
        assert analysis.rules["close_w"] == 315.0
        assert analysis.rules["recovery_w"] == 280.0
        assert analysis.rules["effort_if"] == 1.05


class TestSegmentElevationAndStress:
    """Per-segment elevation split and aRSS, so the tables cross-check the load."""

    def test_a_hill_split_reports_ascent_and_descent_as_a_pair(self):
        power = [250.0] * 300 + [420.0] * 200 + [250.0] * 300
        speed = [4.0] * 800
        altitude = [0.0] * 300 + [i * 0.05 for i in range(200)] + [10.0] * 300
        grades = analysis_maths.grade_pct_series(altitude, speed, 1.0)
        analysis = analysis_intervals.analyse_activity(
            power_w=power,
            period_s=1.0,
            threshold_w=CP,
            speeds_ms=speed,
            grades_pct=grades,
            altitudes_m=altitude,
        )
        efforts = [s for s in analysis.segments if s.kind is analysis_intervals.SegmentKind.INTERVAL]
        assert efforts
        climb = efforts[0]
        assert climb.elevation_gain_m is not None and climb.elevation_gain_m >= 8.0
        assert climb.elevation_loss_m is not None and climb.elevation_loss_m <= 1.0

    def test_segment_rss_entries_add_up_to_the_whole_window(self):
        power = [250.0] * 300 + [430.0] * 150 + [120.0] * 150 + [430.0] * 150 + [250.0] * 300
        speed = [4.0] * 1050
        grades = [0.0] * 1050
        analysis = analysis_intervals.analyse_activity(
            power_w=power, period_s=1.0, threshold_w=CP, speeds_ms=speed, grades_pct=grades
        )
        total = analysis_methods.rss_window_stress(power, 1.0, CP)
        summed = sum(segment.rss or 0.0 for segment in analysis.segments)
        assert summed == pytest.approx(total, abs=0.5)

    def test_without_a_threshold_the_rss_column_stays_blank(self):
        power = [300.0] * 300
        analysis = analysis_intervals.analyse_activity(power_w=power, period_s=1.0, threshold_w=None)
        assert all(segment.rss is None for segment in analysis.segments)
