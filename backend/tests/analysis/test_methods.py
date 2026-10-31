"""Tests for the load-formula registry: what each formula computes, and when it abstains.

The scale is the contract: 100 units is one hour held at the athlete's threshold, so
a formula that applied to a two-hour effort at threshold must return 200. A formula
that lacks an input it needs returns ``None`` rather than guessing, which is what
keeps a power score from appearing on an activity with no power.
"""

from math import exp

import pytest

import modules.analysis.constants as analysis_constants
import modules.analysis.methods as analysis_methods


def _hours(seconds: float) -> float:
    """Return seconds as hours, for reading the expected numbers."""
    return seconds / 3600.0


class TestSharedScale:
    def test_one_hour_at_threshold_is_one_hundred(self):
        assert analysis_methods.stress_from_intensity(1.0, 3600.0) == pytest.approx(100.0)

    def test_two_hours_at_threshold_is_two_hundred(self):
        assert analysis_methods.stress_from_intensity(1.0, 7200.0) == pytest.approx(200.0)

    def test_one_hour_at_eighty_percent_is_sixty_four(self):
        assert analysis_methods.stress_from_intensity(0.8, 3600.0) == pytest.approx(64.0)


class TestCogganScore:
    def test_a_steady_hour_at_threshold_scores_one_hundred(self):
        value = analysis_methods.coggan_tss(
            analysis_methods.LoadInput(
                moving_seconds=3600.0,
                stored_np_w=250.0,
                power_threshold_w=250.0,
            )
        )
        assert value is not None
        assert value.load == pytest.approx(100.0)
        assert value.intensity_factor == pytest.approx(1.0)
        assert value.threshold_used == 250.0

    def test_the_stored_value_is_preferred_over_recomputing(self):
        """Ingestion's Normalized Power is what devices and Strava report; use it."""
        value = analysis_methods.coggan_tss(
            analysis_methods.LoadInput(
                moving_seconds=3600.0,
                power_w=tuple([100.0] * 3600),
                period_s=1.0,
                stored_np_w=250.0,
                power_threshold_w=250.0,
            )
        )
        assert value is not None
        assert value.load == pytest.approx(100.0)

    def test_power_samples_are_used_when_nothing_is_stored(self):
        value = analysis_methods.coggan_tss(
            analysis_methods.LoadInput(
                moving_seconds=1800.0,
                power_w=tuple([300.0] * 1800),
                period_s=1.0,
                power_threshold_w=300.0,
            )
        )
        assert value is not None
        assert value.load == pytest.approx(50.0)

    def test_no_threshold_abstains(self):
        assert (
            analysis_methods.coggan_tss(
                analysis_methods.LoadInput(moving_seconds=3600.0, stored_np_w=250.0, power_threshold_w=None)
            )
            is None
        )

    def test_no_power_abstains(self):
        assert (
            analysis_methods.coggan_tss(analysis_methods.LoadInput(moving_seconds=3600.0, power_threshold_w=250.0))
            is None
        )


class TestHeartRateLoad:
    def test_one_hour_at_threshold_heart_rate_scores_one_hundred(self):
        value = analysis_methods.hr_load(
            analysis_methods.LoadInput(moving_seconds=3600.0, average_hr=150.0, hr_threshold_bpm=150.0)
        )
        assert value is not None
        assert value.load == pytest.approx(100.0)
        assert value.threshold_used == 150.0

    def test_the_same_scale_as_the_power_formula_across_sports(self):
        """A hard hour of hiking and a hard hour of cycling add up as one day.

        Both formulas return 100 for one hour at their own threshold, which is the
        property the summed daily load depends on.
        """
        cycling = analysis_methods.coggan_tss(
            analysis_methods.LoadInput(moving_seconds=3600.0, stored_np_w=250.0, power_threshold_w=250.0)
        )
        hiking = analysis_methods.hr_load(
            analysis_methods.LoadInput(moving_seconds=3600.0, average_hr=130.0, hr_threshold_bpm=130.0)
        )
        assert cycling is not None and hiking is not None
        assert cycling.load == pytest.approx(hiking.load)

    def test_no_threshold_abstains(self):
        assert analysis_methods.hr_load(analysis_methods.LoadInput(moving_seconds=3600.0, average_hr=140.0)) is None


class TestTrimp:
    def test_heart_rate_reserve_scales_the_impulse(self):
        value = analysis_methods.trimp(
            analysis_methods.LoadInput(moving_seconds=3600.0, average_hr=130.0, max_hr=180.0, resting_hr_bpm=50.0)
        )
        assert value is not None
        reserve = (130.0 - 50.0) / (180.0 - 50.0)
        assert value.intensity_factor == pytest.approx(reserve)
        assert value.load > 0

    def test_a_missing_resting_heart_rate_abstains(self):
        """No resting rate is guessed: the athlete either states it or gets no TRIMP."""
        assert (
            analysis_methods.trimp(analysis_methods.LoadInput(moving_seconds=3600.0, average_hr=130.0, max_hr=180.0))
            is None
        )

    def test_a_harder_hour_impulses_more(self):
        easy = analysis_methods.trimp(
            analysis_methods.LoadInput(moving_seconds=3600.0, average_hr=120.0, max_hr=180.0, resting_hr_bpm=50.0)
        )
        hard = analysis_methods.trimp(
            analysis_methods.LoadInput(moving_seconds=3600.0, average_hr=150.0, max_hr=180.0, resting_hr_bpm=50.0)
        )
        assert easy is not None and hard is not None
        assert hard.load > easy.load


class TestSkibaScore:
    def test_it_measures_the_same_activity_on_the_same_scale(self):
        value = analysis_methods.skiba_score(
            analysis_methods.LoadInput(
                moving_seconds=3600.0,
                power_w=tuple([250.0] * 3600),
                period_s=1.0,
                power_threshold_w=250.0,
            )
        )
        assert value is not None
        assert value.load == pytest.approx(100.0, rel=0.05)

    def test_it_needs_power_samples(self):
        assert (
            analysis_methods.skiba_score(
                analysis_methods.LoadInput(moving_seconds=3600.0, stored_np_w=250.0, power_threshold_w=250.0)
            )
            is None
        )


class TestExponentialFamily:
    """The athlete's fitted kernel and its four bands.

    The kernel rate of a second at intensity ``x`` is
    ``B + A·e^(E·x)`` with the constants the athlete fitted, and the activity's
    score is that rate summed over the powered seconds divided by the normalizer,
    which puts the family on the shared scale.
    """

    @staticmethod
    def _kernel(intensity: float) -> float:
        """Return the documented kernel rate for one second at ``intensity``."""
        return analysis_constants.RSS_KERNEL_B + analysis_constants.RSS_KERNEL_A * exp(
            analysis_constants.RSS_KERNEL_E * intensity
        )

    @staticmethod
    def _score(seconds: int, rate: float) -> float:
        """Return the shared-scale score of ``seconds`` held at kernel ``rate``."""
        return 100.0 * rate * seconds / (analysis_constants.RSS_NORM * 3600.0)

    def test_an_hour_at_threshold_scores_a_hundred_on_the_shared_scale(self):
        data = analysis_methods.LoadInput(
            moving_seconds=3600.0,
            power_w=tuple([250.0] * 3600),
            period_s=1.0,
            power_threshold_w=250.0,
        )
        value = analysis_methods.rss_total(data)
        assert value is not None
        # The fitted kernel is ~1.7 at threshold and the normalizer is 1.6, so the
        # hour lands a hair above 100 rather than exactly on it.
        assert value.load == pytest.approx(106.1, abs=0.5)
        assert value.intensity_factor == pytest.approx(1.0)
        assert value.threshold_used == 250.0

    def test_a_second_below_the_low_edge_and_slow_is_low_band_only(self):
        # 100 W at a 250 W threshold is IF 0.4, walked at 1.5 m/s.
        data = analysis_methods.LoadInput(
            moving_seconds=100.0,
            power_w=tuple([100.0] * 100),
            period_s=1.0,
            speed_ms=tuple([1.5] * 100),
            power_threshold_w=250.0,
        )
        low = analysis_methods.rss_low(data)
        mid = analysis_methods.rss_mid(data)
        assert low is not None and mid is None
        assert low.load == pytest.approx(self._score(100, self._kernel(0.4)))

    def test_a_fast_low_power_second_is_middle_work_not_recovery(self):
        data = analysis_methods.LoadInput(
            moving_seconds=100.0,
            power_w=tuple([100.0] * 100),
            period_s=1.0,
            speed_ms=tuple([6.0] * 100),
            power_threshold_w=250.0,
        )
        mid = analysis_methods.rss_mid(data)
        assert mid is not None
        assert mid.load == pytest.approx(self._score(100, self._kernel(0.4)))
        assert analysis_methods.rss_low(data) is None

    def test_a_steep_second_is_a_climb_and_never_also_an_intensity_band(self):
        # 260 W (IF 1.04, above the high edge) on a 12 % grade belongs to the climbs.
        data = analysis_methods.LoadInput(
            moving_seconds=100.0,
            power_w=tuple([260.0] * 100),
            period_s=1.0,
            speed_ms=tuple([3.0] * 100),
            grade_pct=tuple([12.0] * 100),
            power_threshold_w=250.0,
        )
        up = analysis_methods.rss_up(data)
        assert up is not None
        assert up.load == pytest.approx(self._score(100, self._kernel(1.04)))
        assert analysis_methods.rss_high(data) is None
        assert analysis_methods.rss_mid(data) is None

    def test_the_climb_floor_lifts_an_easy_second_on_a_steep_grade(self):
        # 100 W (kernel ~0.56) at 12 % grades as at least 0.8: the gradient costs
        # more than its watts.
        data = analysis_methods.LoadInput(
            moving_seconds=100.0,
            power_w=tuple([100.0] * 100),
            period_s=1.0,
            speed_ms=tuple([2.0] * 100),
            grade_pct=tuple([12.0] * 100),
            power_threshold_w=250.0,
        )
        up = analysis_methods.rss_up(data)
        assert up is not None
        assert up.load == pytest.approx(self._score(100, analysis_constants.RSS_CLIMB_FLOOR))

    def test_the_bands_partition_the_general_load(self):
        """Every powered second lands in exactly one band, so the bands add to the total."""
        watts, speeds, grades = [], [], []
        for _ in range(300):  # slow easy
            watts.append(100.0)
            speeds.append(1.5)
            grades.append(0.0)
        for _ in range(300):  # steady middle work
            watts.append(220.0)
            speeds.append(3.2)
            grades.append(0.0)
        for _ in range(200):  # above threshold, flat
            watts.append(280.0)
            speeds.append(3.6)
            grades.append(0.0)
        for _ in range(200):  # hard, but climbing
            watts.append(280.0)
            speeds.append(2.4)
            grades.append(12.0)
        for _ in range(200):  # stops contribute to nothing
            watts.append(0.0)
            speeds.append(0.0)
            grades.append(0.0)
        data = analysis_methods.LoadInput(
            moving_seconds=1200.0,
            power_w=tuple(watts),
            period_s=1.0,
            speed_ms=tuple(speeds),
            grade_pct=tuple(grades),
            power_threshold_w=250.0,
        )
        total = analysis_methods.rss_total(data)
        assert total is not None
        parts = [getattr(analysis_methods, f"rss_{band}")(data) for band in ("up", "high", "mid", "low")]
        assert all(part is not None for part in parts)
        assert sum(part.load for part in parts) == pytest.approx(total.load)

    def test_the_general_score_counts_a_moderate_grade_above_its_watts(self):
        # 6 % grade at IF 0.4: the total applies the soft floor, and the low band it
        # belongs to applies the same one, so both read 0.65 per second.
        data = analysis_methods.LoadInput(
            moving_seconds=100.0,
            power_w=tuple([100.0] * 100),
            period_s=1.0,
            speed_ms=tuple([1.5] * 100),
            grade_pct=tuple([6.0] * 100),
            power_threshold_w=250.0,
        )
        total = analysis_methods.rss_total(data)
        assert total is not None
        assert total.load == pytest.approx(self._score(100, analysis_constants.RSS_SOFT_FLOOR))

    def test_the_athlete_can_retune_the_kernel(self):
        data = analysis_methods.LoadInput(
            moving_seconds=3600.0,
            power_w=tuple([250.0] * 3600),
            period_s=1.0,
            power_threshold_w=250.0,
            parameters={"rss_kernel_a": 0.0, "rss_kernel_b": 1.0, "rss_norm": 1.0},
        )
        value = analysis_methods.rss_total(data)
        assert value is not None
        assert value.load == pytest.approx(100.0)

    def test_no_power_never_invents_an_exponential_score(self):
        data = analysis_methods.LoadInput(
            moving_seconds=3600.0,
            average_hr=150.0,
            power_threshold_w=250.0,
        )
        assert analysis_methods.rss_total(data) is None

    def test_a_sport_without_a_power_threshold_measures_no_band(self):
        data = analysis_methods.LoadInput(
            moving_seconds=3600.0,
            power_w=tuple([250.0] * 3600),
            period_s=1.0,
        )
        assert analysis_methods.rss_total(data) is None


class TestRegistry:
    def test_every_registered_method_is_stored_as_its_own_row(self):
        registered = {entry.method for entry in analysis_methods.LOAD_FORMULAS}
        assert registered == set(analysis_constants.LoadMethod)

    def test_labels_exist_for_the_settings_screen(self):
        assert all(entry.label for entry in analysis_methods.LOAD_FORMULAS)

    def test_an_activity_measures_every_formula_it_supports(self):
        # Mixed efforts (easy, steady, above threshold, climbing, stopped) so each
        # band of the exponential family has at least one second to measure.
        watts = tuple([100.0] * 300 + [220.0] * 300 + [280.0] * 200 + [280.0] * 100 + [0.0] * 100)
        speeds = tuple([1.5] * 300 + [3.2] * 300 + [3.6] * 200 + [2.4] * 100 + [0.0] * 100)
        grades = tuple([0.0] * 800 + [12.0] * 100 + [0.0] * 100)
        data = analysis_methods.LoadInput(
            moving_seconds=1000.0,
            power_w=watts,
            period_s=1.0,
            speed_ms=speeds,
            grade_pct=grades,
            average_hr=140.0,
            max_hr=180.0,
            power_threshold_w=250.0,
            hr_threshold_bpm=150.0,
            resting_hr_bpm=50.0,
        )
        measured = dict(analysis_methods.compute_all(data))
        assert set(measured) == set(analysis_constants.LoadMethod)

    def test_a_no_power_activity_keeps_the_heart_rate_formulas(self):
        data = analysis_methods.LoadInput(
            moving_seconds=3600.0,
            average_hr=140.0,
            max_hr=180.0,
            hr_threshold_bpm=150.0,
            resting_hr_bpm=50.0,
        )
        measured = {method for method, _ in analysis_methods.compute_all(data)}
        assert measured == {analysis_constants.LoadMethod.HR_LOAD, analysis_constants.LoadMethod.TRIMP}

    def test_a_formula_is_added_by_joining_the_registry(self):
        """The extension point: one entry, and its load is stored like any other.

        An athlete's own formula receives the same :class:`LoadInput` (samples,
        thresholds, and its knobs in ``parameters``) and returns the same
        :class:`LoadValue`; nothing else in the module has to know it exists.
        """

        def double_threshold_load(data: analysis_methods.LoadInput) -> analysis_methods.LoadValue | None:
            if not data.power_threshold_w or data.moving_seconds <= 0:
                return None
            intensity = 2.0
            return analysis_methods.LoadValue(
                load=analysis_methods.stress_from_intensity(intensity, data.moving_seconds),
                intensity_factor=intensity,
                threshold_used=data.power_threshold_w * 2.0,
            )

        entry = analysis_methods.LoadFormula(
            method=analysis_constants.LoadMethod.HR_LOAD,
            label="Temporary double-threshold probe",
            calculator=double_threshold_load,
        )
        value = entry.calculator(analysis_methods.LoadInput(moving_seconds=1800.0, power_threshold_w=200.0))
        assert value is not None
        assert value.load == pytest.approx(200.0)
        assert value.threshold_used == 400.0
