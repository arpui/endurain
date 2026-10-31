"""Tests for the pure arithmetic of analysis: windows, peaks, fits.

These assert the definitions themselves, with hand-checkable numbers: no database,
no activity, no mocks. A regression here silently changes every stored load, so the
expected values are derived in the test from the formula's published definition.
"""

from datetime import datetime, timedelta
from math import exp

import pytest

import modules.analysis.maths as analysis_maths

_T0 = datetime(2026, 10, 15, 16, 0, 0)


def _stamps(count: int, step_s: float = 1.0) -> list[datetime]:
    """Return ``count`` timestamps ``step_s`` seconds apart."""
    return [_T0 + timedelta(seconds=index * step_s) for index in range(count)]


class TestGradeSeries:
    """The grade the per-second formulas read: elevation over ground distance."""

    def test_flat_ground_reads_zero(self):
        grades = analysis_maths.grade_pct_series([100.0] * 50, [3.0] * 50, 1.0)
        assert grades == tuple([0.0] * 50)

    def test_a_steady_climb_reads_its_gradient(self):
        # 3 m/s gaining 0.3 m per second is a 10 % grade.
        elevations = [100.0 + 0.3 * index for index in range(60)]
        grades = analysis_maths.grade_pct_series(elevations, [3.0] * 60, 1.0)
        assert grades[30] == pytest.approx(10.0, abs=0.2)

    def test_a_single_noisy_altitude_sample_does_not_invent_a_hill(self):
        # One metre of GPS noise over a couple of seconds would read as a 30 %
        # grade; the median filter over the window keeps the flat ground flat.
        elevations = [100.0] * 30 + [101.0] + [100.0] * 30
        grades = analysis_maths.grade_pct_series(elevations, [3.0] * 61, 1.0)
        assert max(abs(grade) for grade in grades) < 1.0

    def test_a_stopped_sample_contributes_no_grade(self):
        elevations = [100.0, 100.5, 101.0]
        grades = analysis_maths.grade_pct_series(elevations, [3.0, 0.0, 0.0], 1.0)
        assert grades[1] == 0.0 and grades[2] == 0.0

    def test_a_one_sample_stream_is_flat(self):
        assert analysis_maths.grade_pct_series([100.0], [3.0], 1.0) == (0.0,)


class TestSamplePeriod:
    def test_one_hertz_stream_reports_one_second(self):
        assert analysis_maths.sample_period_s(_stamps(10)) == pytest.approx(1.0)

    def test_median_ignores_one_dropped_sample(self):
        stamps = _stamps(10)
        stamps.pop(5)
        assert analysis_maths.sample_period_s(stamps) == pytest.approx(1.0)

    def test_a_sparse_stream_is_clamped(self):
        stamps = _stamps(10, step_s=600.0)
        assert analysis_maths.sample_period_s(stamps) == pytest.approx(10.0)

    def test_a_single_sample_reports_the_default_period(self):
        assert analysis_maths.sample_period_s([_T0]) == 1.0


class TestWindows:
    @pytest.mark.parametrize(
        ("period_s", "window_s", "expected"),
        [(1.0, 30.0, 30), (0.5, 30.0, 60), (5.0, 30.0, 6), (0.0, 30.0, 1)],
    )
    def test_window_size_follows_the_sample_period(self, period_s: float, window_s: float, expected: int):
        assert analysis_maths.window_samples(period_s, window_s) == expected

    def test_moving_average_slides_over_the_series(self):
        assert list(analysis_maths.moving_average([1.0, 2.0, 3.0, 4.0], 2)) == pytest.approx([1.5, 2.5, 3.5])

    def test_a_window_wider_than_the_series_returns_it_unchanged(self):
        assert list(analysis_maths.moving_average([3.0, 4.0], 30)) == pytest.approx([3.0, 4.0])


class TestNormalizedPower:
    def test_constant_power_normalized_is_that_power(self):
        assert analysis_maths.normalized_power([250.0] * 120, 1.0) == pytest.approx(250.0)

    def test_an_empty_stream_normalizes_to_zero(self):
        assert analysis_maths.normalized_power([], 1.0) == 0.0

    def test_without_smoothing_it_is_the_fourth_power_mean(self):
        values = [100.0, 200.0, 300.0]
        expected = (sum(value**4 for value in values) / len(values)) ** 0.25
        assert analysis_maths.normalized_power(values, 1.0, window_s=1.0) == pytest.approx(expected)

    def test_varied_power_normalizes_above_its_mean(self):
        values = [300.0, 0.0] * 60
        normalized = analysis_maths.normalized_power(values, 1.0, window_s=1.0)
        assert normalized == pytest.approx(300.0 * 0.5**0.25)
        assert normalized > sum(values) / len(values)

    def test_smoothing_pulls_a_single_spike_down(self):
        spiky = [0.0] * 29 + [600.0] + [0.0] * 29
        smoothed = analysis_maths.normalized_power(spiky, 1.0, window_s=30.0)
        unsmoothed = analysis_maths.normalized_power(spiky, 1.0, window_s=1.0)
        assert smoothed < unsmoothed


class TestXPower:
    def test_constant_power_gives_that_power_within_the_start_up_ramp(self):
        """The accumulator starts at zero, so the first ~25 s pull the mean down.

        The deficit shrinks as the stream lengthens, which is why the value is an
        index over an activity's shape rather than an absolute: a long steady
        effort lands on its power, a very short one is measured low.
        """
        short = analysis_maths.xpower([250.0] * 60, 1.0)
        long = analysis_maths.xpower([250.0] * 600, 1.0)
        assert short == pytest.approx(250.0, rel=0.3)
        assert long == pytest.approx(250.0, rel=0.03)
        assert long > short

    def test_an_empty_stream_gives_zero(self):
        assert analysis_maths.xpower([], 1.0) == 0.0

    def test_a_burst_raises_xpower_above_the_mean(self):
        values = [100.0] * 300 + [500.0] * 10 + [100.0] * 300
        assert analysis_maths.xpower(values, 1.0) > sum(values) / len(values)


class TestDurationPeaks:
    def test_the_best_window_is_found_at_each_duration(self):
        peaks = analysis_maths.best_means([1.0, 2.0, 3.0, 4.0, 5.0], 1.0, durations=[2, 3])
        assert peaks == {2: 4.5, 3: 4.0}

    def test_durations_longer_than_the_stream_are_absent(self):
        assert analysis_maths.best_means([1.0, 2.0], 1.0, durations=[60]) == {}

    def test_the_best_average_over_one_window(self):
        assert analysis_maths.best_window_mean([1.0, 5.0, 5.0, 5.0, 1.0], 1.0, 3.0) == pytest.approx(5.0)


class TestCriticalPowerFit:
    def test_recovers_the_hyperbola_that_generated_it(self):
        critical_power, w_prime = 250.0, 20_000.0
        durations = [300, 420, 600, 900, 1200, 1800]
        peaks = {duration: critical_power + w_prime / duration for duration in durations}
        fitted_power, fitted_w_prime, cv_pct = analysis_maths.fit_cp_wprime(peaks)
        assert fitted_power == pytest.approx(critical_power, rel=1e-6)
        assert fitted_w_prime == pytest.approx(w_prime, rel=1e-6)
        assert cv_pct == pytest.approx(0.0, abs=1e-6)

    def test_sprint_durations_stay_out_of_the_fit(self):
        """A curve whose short durations sag must not bend the aerobic slope.

        Fitting only 300-1800 s ignores the sprint peaks that made a 2-20 minute
        fit report 378 W where the athlete's best hour was 338 W.
        """
        critical_power, w_prime = 250.0, 20_000.0
        peaks = {duration: critical_power + w_prime / duration for duration in (300, 420, 600, 900, 1200, 1800)}
        peaks.update({5: 1200.0, 10: 1100.0, 30: 900.0})
        fitted_power, fitted_w_prime, _ = analysis_maths.fit_cp_wprime(peaks)
        assert fitted_power == pytest.approx(critical_power, rel=1e-6)
        assert fitted_w_prime == pytest.approx(w_prime, rel=1e-6)

    def test_too_few_points_refuse_the_fit(self):
        assert analysis_maths.fit_cp_wprime({300: 300.0, 600: 280.0}) is None

    def test_a_curve_outside_the_range_refuses_the_fit(self):
        assert analysis_maths.fit_cp_wprime({60: 400.0, 120: 380.0, 180: 360.0, 240: 350.0}) is None


class TestExponentiallyWeightedFunction:
    """The PMC recurrence GoldenCheetah uses: relax toward each day's value."""

    def test_a_constant_daily_load_converges_to_that_load(self):
        series = analysis_maths.ewf([100.0] * 400, 42.0)
        assert series[-1] == pytest.approx(100.0, rel=1e-3)

    def test_the_first_day_is_the_exact_recurrence_from_zero(self):
        tau = 7.0
        decay = exp(-1.0 / tau)
        series = analysis_maths.ewf([50.0, 0.0], tau)
        assert series[0] == pytest.approx(50.0 * (1.0 - decay))
        assert series[1] == pytest.approx(series[0] * decay)

    def test_a_single_spike_decays_geometrically(self):
        tau = 5.0
        decay = exp(-1.0 / tau)
        series = analysis_maths.ewf([100.0] + [0.0] * 3, tau)
        state = 100.0 * (1.0 - decay)
        for value in series:
            assert value == pytest.approx(state)
            state *= decay

    def test_fatigue_relaxes_faster_than_fitness(self):
        """After one hard day the acute constant drops below the chronic one.

        The spike lifts fatigue above fitness first (a negative balance), and it
        decays sooner: the crossing is the "fresh" the athlete feels on a rest
        day. Same load, same day, only the time constant differs.
        """
        loads = [100.0] + [0.0] * 20
        acute = analysis_maths.ewf(loads, 7.0)
        chronic = analysis_maths.ewf(loads, 42.0)
        assert acute[0] > chronic[0]
        assert acute[-1] < chronic[-1]

    def test_the_series_keeps_the_input_length(self):
        assert len(analysis_maths.ewf([10.0, 20.0, 30.0], 7.0)) == 3


class TestWorkAboveThreshold:
    def test_only_samples_over_the_threshold_count(self):
        work_j, time_s = analysis_maths.work_above_threshold([200.0, 300.0, 400.0], 1.0, 250.0)
        assert time_s == pytest.approx(2.0)
        assert work_j == pytest.approx(50.0 + 150.0)

    def test_at_or_below_the_threshold_nothing_is_spent(self):
        assert analysis_maths.work_above_threshold([250.0, 200.0], 2.0, 250.0) == (0.0, 0.0)


class TestSkibaTau:
    def test_the_time_constant_follows_the_published_constants(self):
        values = [150.0, 150.0, 300.0]  # below-CP mean 150, CP 250 -> DCP 100
        tau = analysis_maths.skiba_tau_s(250.0, values)
        assert tau == pytest.approx(546.0 * exp(-0.01 * 100.0) + 316.0)

    def test_a_stream_always_above_cp_recovers_slowly(self):
        assert analysis_maths.skiba_tau_s(250.0, [300.0, 320.0]) > 316.0


class TestWprimeBalance:
    def test_a_constant_effort_above_cp_empties_the_capacity(self):
        # CP 250, 50 W over -> 50 J/s; W' 1000 J empties in ~20 s (minus early repletion).
        balance = analysis_maths.wprime_balance([300.0] * 60, 1.0, 250.0, 1000.0, tau_s=1e6)
        assert balance[0] == pytest.approx(950.0)
        assert balance[-1] == pytest.approx(0.0)

    def test_below_cp_work_never_spends_and_never_overfills(self):
        balance = analysis_maths.wprime_balance([200.0] * 30, 1.0, 250.0, 1000.0, tau_s=300.0)
        assert balance == pytest.approx([1000.0] * 30)

    def test_spent_work_repletes_exponentially(self):
        """One second 100 W over CP, then silence: the debt decays by e^(-dt/tau)."""
        tau = 100.0
        balance = analysis_maths.wprime_balance([350.0] + [0.0] * 3, 1.0, 250.0, 1000.0, tau_s=tau)
        debt = 100.0
        for value in balance:
            assert value == pytest.approx(1000.0 - debt)
            debt *= exp(-1.0 / tau)

    def test_a_missing_capacity_yields_no_series(self):
        assert analysis_maths.wprime_balance([300.0] * 3, 1.0, 250.0, 0.0) == []


class TestDownsample:
    def test_short_series_pass_through(self):
        points = [(float(i), float(i)) for i in range(5)]
        assert analysis_maths.downsample(points, 10) == points

    def test_the_endpoint_survives_the_stride(self):
        points = [(float(i), float(i * 2)) for i in range(100)]
        sampled = analysis_maths.downsample(points, 10)
        assert len(sampled) == 10
        assert sampled[-1] == points[-1]
        assert sampled[0] == points[0]


class TestBestSplitTimes:
    """Best splits over canonical race distances, with the downhill guard."""

    @staticmethod
    def _run(meters_per_second: float, seconds: int, watts: float = 250.0, grade_pct: float = 0.0):
        """Flat run at constant speed; ``grade_pct`` tilts the whole route."""
        elapsed = [index * 1.0 for index in range(seconds)]
        distance = [meters_per_second * index for index in range(seconds)]
        power = [watts] * seconds
        # cumulative ascent whose slope matches the requested net grade
        altitude = [grade_pct / 100.0 * meters_per_second * index for index in range(seconds)]
        return elapsed, distance, power, altitude

    def test_a_flat_run_yields_its_pace_over_every_covered_distance(self):
        elapsed, distance, power, altitude = self._run(4.0, 1500, 250.0)  # 6 km at 4 m/s
        splits = analysis_maths.best_split_times(
            elapsed_s=elapsed,
            distance_cum_m=distance,
            power_w=power,
            altitude_m=altitude,
            distances_m=(1000, 2000, 5000),
        )
        assert set(splits) == {1000, 2000, 5000}
        assert splits[1000][0] == pytest.approx(250.0, rel=0.02)
        assert splits[5000][0] == pytest.approx(1250.0, rel=0.02)
        assert splits[1000][1] == pytest.approx(250.0, rel=0.05)

    def test_a_faster_middle_split_wins(self):
        seconds = 1000
        elapsed = [index * 1.0 for index in range(seconds)]
        distance, power = [], []
        running = 0.0
        for index in range(seconds):
            speed, watts = (5.0, 350.0) if 300 <= index < 500 else (3.0, 220.0)
            running += speed
            distance.append(running)
            power.append(watts)
        altitude = [0.0] * seconds
        splits = analysis_maths.best_split_times(
            elapsed_s=elapsed,
            distance_cum_m=distance,
            power_w=power,
            altitude_m=altitude,
            distances_m=(1000,),
        )
        assert splits[1000][0] == pytest.approx(200.0, rel=0.02)  # the 5 m/s stretch
        assert splits[1000][1] > 340.0

    def test_a_downhill_split_is_rejected_for_long_distances(self):
        elapsed, distance, power, altitude = self._run(5.0, 700, 300.0, grade_pct=-2.5)
        splits = analysis_maths.best_split_times(
            elapsed_s=elapsed,
            distance_cum_m=distance,
            power_w=power,
            altitude_m=altitude,
            distances_m=(1000, 3000),
        )
        assert splits == {}  # -2,5 % net fails both the -1 % and the -2 % bound

    def test_the_grade_bounds_follow_the_distance_length(self):
        elapsed, distance, power, altitude = self._run(5.0, 700, 300.0, grade_pct=-1.5)
        splits = analysis_maths.best_split_times(
            elapsed_s=elapsed,
            distance_cum_m=distance,
            power_w=power,
            altitude_m=altitude,
            distances_m=(400, 1000),
        )
        assert 400 in splits  # short: bound is -2 %, -1,5 % passes
        assert 1000 not in splits  # long: bound is -1 %, -1,5 % fails

    def test_distances_the_activity_never_covers_are_absent(self):
        elapsed, distance, power, altitude = self._run(4.0, 100, 250.0)
        assert (
            analysis_maths.best_split_times(
                elapsed_s=elapsed,
                distance_cum_m=distance,
                power_w=power,
                altitude_m=altitude,
                distances_m=(1000, 5000),
            )
            == {}
        )


class TestBestSplitSpeedCeiling:
    def test_gps_jitter_faster_than_world_record_pace_is_rejected(self):
        seconds = 200
        elapsed = [index * 1.0 for index in range(seconds)]
        distance = [15.0 * index for index in range(seconds)]  # 15 m/s: GPS jump, not a runner
        power = [220.0] * seconds
        altitude = [0.0] * seconds
        splits = analysis_maths.best_split_times(
            elapsed_s=elapsed,
            distance_cum_m=distance,
            power_w=power,
            altitude_m=altitude,
            distances_m=(100, 400),
        )
        assert splits == {}

    def test_a_honest_short_split_still_passes(self):
        # 100 m in 13 s is 7,7 m/s: under the ceiling, so it counts
        seconds = 20
        elapsed = [index * 1.0 for index in range(seconds)]
        distance = [7.7 * index for index in range(seconds)]
        power = [500.0] * seconds
        altitude = [0.0] * seconds
        splits = analysis_maths.best_split_times(
            elapsed_s=elapsed,
            distance_cum_m=distance,
            power_w=power,
            altitude_m=altitude,
            distances_m=(100,),
        )
        assert splits[100][0] == pytest.approx(13.0, abs=1.0)
