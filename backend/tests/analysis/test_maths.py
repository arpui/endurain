"""Tests for the pure arithmetic of analysis: windows, peaks, fits.

These assert the definitions themselves, with hand-checkable numbers: no database,
no activity, no mocks. A regression here silently changes every stored load, so the
expected values are derived in the test from the formula's published definition.
"""

from datetime import datetime, timedelta

import pytest

import modules.analysis.maths as analysis_maths

_T0 = datetime(2026, 10, 15, 16, 0, 0)


def _stamps(count: int, step_s: float = 1.0) -> list[datetime]:
    """Return ``count`` timestamps ``step_s`` seconds apart."""
    return [_T0 + timedelta(seconds=index * step_s) for index in range(count)]


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
