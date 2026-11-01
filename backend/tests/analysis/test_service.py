"""Tests for the analysis service: thresholds, measurement, and when to abstain.

The service owns the decisions the formulas cannot make: which threshold applies to
which sport, when a derived threshold is not trustworthy enough to use, that a value
the athlete typed is never overwritten, and who may read a stored load. The
formulas themselves are tested in ``test_methods.py``.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import core.exceptions as core_exceptions
import modules.activities.activity_streams.constants as activity_streams_constants
import modules.analysis.constants as analysis_constants
import modules.analysis.contracts as analysis_contracts
import modules.analysis.intervals as analysis_intervals
import modules.analysis.methods as analysis_methods
import modules.analysis.schema as analysis_schema
import modules.analysis.service as analysis_service

_SVC = "modules.analysis.service"
_T0 = datetime(2026, 10, 15, 16, 0, 0)
_T0_UTC = _T0.replace(tzinfo=UTC)


def _db() -> MagicMock:
    """Return a session double (the service must be the one committing)."""
    return MagicMock()


def _activity(
    activity_id: int = 1,
    *,
    activity_type: int = 1,
    user_id: int | None = 2,
    seconds: float = 3600.0,
    average_hr: int | None = 140,
    max_hr: int | None = 180,
    normalized_power: int | None = 250,
    start_time: datetime | None = _T0_UTC,
    timezone: str | None = "Europe/Madrid",
    name: str | None = "Morning run",
) -> SimpleNamespace:
    """Return an activity shaped like the activities module's read schema."""
    return SimpleNamespace(
        id=activity_id,
        activity_type=activity_type,
        user_id=user_id,
        start_time=start_time,
        timezone=timezone,
        name=name,
        total_timer_time=seconds,
        total_elapsed_time=seconds,
        average_hr=average_hr,
        max_hr=max_hr,
        average_power=normalized_power,
        normalized_power=normalized_power,
    )


def _stream(values: list[float], key: str = "power", step_s: float = 1.0) -> SimpleNamespace:
    """Return a stream shaped like the activity-streams read schema."""
    waypoints = [
        {"time": (_T0 + timedelta(seconds=index * step_s)).isoformat(), key: value}
        for index, value in enumerate(values)
    ]
    return SimpleNamespace(stream_waypoints=waypoints)


def _settings(**overrides) -> analysis_schema.AnalysisSettingsRead:
    """Return a settings read, defaults unless overridden."""
    body = {
        "acute_days": 7,
        "chronic_days": 42,
        "primary_method": analysis_constants.LoadMethod.COGGAN_TSS,
        "resting_hr": None,
        "power_thresholds": {},
        "hr_threshold": None,
        "method_params": {},
    }
    body.update(overrides)
    return analysis_schema.AnalysisSettingsRead(**body)


def _capture_settings(calls: list[analysis_schema.AnalysisSettingsRead]):
    """Return a ``store_settings`` double that records what it was given and returns it."""

    def store(
        _user_id: int, settings: analysis_schema.AnalysisSettingsRead, _db
    ) -> analysis_schema.AnalysisSettingsRead:
        calls.append(settings)
        return settings

    return store


class TestSettings:
    def test_no_row_yields_the_module_defaults(self):
        with patch(f"{_SVC}.analysis_crud.get_settings", return_value=None):
            settings = analysis_service.get_settings(2, _db())
        assert settings.acute_days == analysis_constants.ACUTE_DAYS_DEFAULT
        assert settings.chronic_days == analysis_constants.CHRONIC_DAYS_DEFAULT
        assert settings.primary_method is analysis_constants.LoadMethod.COGGAN_TSS
        assert settings.power_thresholds == {}

    def test_a_typed_power_threshold_is_marked_manual(self):
        db = _db()
        stored: list[analysis_schema.AnalysisSettingsRead] = []

        def capture(_user_id, settings, _db):
            stored.append(settings)
            return settings

        payload = analysis_schema.AnalysisSettingsUpdate(power_threshold_w={"run": 300.0})
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=capture),
        ):
            analysis_service.update_settings(2, payload, db)

        entry = stored[0].power_thresholds["run"]
        assert entry.value_w == 300.0
        assert entry.source is analysis_constants.ThresholdSource.MANUAL
        db.commit.assert_called_once()

    def test_clearing_a_threshold_falls_back_to_derived(self):
        """Sending null removes the entry, so the next refresh re-derives it."""
        db = _db()
        current = _settings(
            power_thresholds={
                "run": analysis_schema.PowerThresholdRead(
                    value_w=300.0,
                    w_prime_j=None,
                    source=analysis_constants.ThresholdSource.MANUAL,
                    cv_pct=None,
                )
            }
        )
        stored: list[analysis_schema.AnalysisSettingsRead] = []
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=current),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.update_settings(
                2, analysis_schema.AnalysisSettingsUpdate(power_threshold_w={"run": None}), db
            )
        assert stored[0].power_thresholds == {}

    def test_time_constants_are_athletable_parameters(self):
        db = _db()
        stored: list[analysis_schema.AnalysisSettingsRead] = []
        payload = analysis_schema.AnalysisSettingsUpdate(acute_days=10, chronic_days=60)
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.update_settings(2, payload, db)
        assert (stored[0].acute_days, stored[0].chronic_days) == (10, 60)


class TestComputeLoads:
    def test_a_measured_activity_writes_one_row_per_formula(self):
        db = _db()
        settings = _settings(
            power_thresholds={
                "run": analysis_schema.PowerThresholdRead(
                    value_w=250.0,
                    w_prime_j=20000.0,
                    source=analysis_constants.ThresholdSource.AUTO,
                    cv_pct=3.0,
                )
            }
        )
        with (
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=settings),
            patch(
                f"{_SVC}.activity_streams_integration.get_stream_for_derivation", return_value=_stream([250.0] * 3600)
            ),
            patch(f"{_SVC}.analysis_crud.replace_activity_loads", return_value=3) as replace,
        ):
            written = analysis_service.compute_loads_for_activity(1, db)

        records = replace.call_args.args[5]
        methods = {record.method for record in records}
        assert analysis_constants.LoadMethod.COGGAN_TSS in methods
        assert written == 3
        assert replace.call_args.args[1] == 2
        assert replace.call_args.args[2] == 3600.0
        assert replace.call_args.args[3] == _T0_UTC.astimezone(ZoneInfo("Europe/Madrid")).date()
        assert replace.call_args.args[4] == "run"
        db.commit.assert_called_once()

    def test_a_climbing_run_measures_the_exponential_bands_apart(self):
        """Speed and elevation reach the formulas, so climbs score as climbs."""
        db = _db()
        settings = _settings(
            power_thresholds={
                "run": analysis_schema.PowerThresholdRead(
                    value_w=250.0,
                    w_prime_j=20000.0,
                    source=analysis_constants.ThresholdSource.AUTO,
                    cv_pct=3.0,
                )
            }
        )
        watts = [100.0] * 300 + [280.0] * 300 + [280.0] * 300  # easy, hard flat, hard climbing
        speeds = [1.5] * 300 + [3.6] * 300 + [2.4] * 300
        elevations = [100.0] * 600 + [100.0 + index * 0.3 for index in range(300)]
        # The last section gains 0.3 m per second at 2.4 m/s: a 12 % grade, above
        # the climb cutoff, so those seconds are measured as climbs.

        def stream_for(_activity_id: int, stream_type: int, _db):
            if stream_type == 2:
                return _stream(watts)
            if stream_type == 5:
                return _stream(speeds, key="vel")
            if stream_type == 4:
                return _stream(elevations, key="ele")
            return None

        with (
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity(seconds=900.0)),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=settings),
            patch(f"{_SVC}.activity_streams_integration.get_stream_for_derivation", side_effect=stream_for),
            patch(f"{_SVC}.analysis_crud.replace_activity_loads", return_value=9) as replace,
        ):
            analysis_service.compute_loads_for_activity(1, db)

        records = {record.method: record.load for record in replace.call_args.args[5]}
        assert records[analysis_constants.LoadMethod.RSS_UP] > 0
        assert records[analysis_constants.LoadMethod.RSS_HIGH] > 0
        assert records[analysis_constants.LoadMethod.RSS_LOW] > 0
        assert analysis_constants.LoadMethod.RSS_MID not in records  # every second is easy, high or climbing
        total = records[analysis_constants.LoadMethod.RSS_TOTAL]
        bands = sum(
            load for method, load in records.items() if method.value.startswith("rss_") and method.value != "rss_total"
        )
        assert bands == pytest.approx(total, rel=1e-6)

    def test_a_deleted_activity_writes_nothing(self):
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=None),
            patch(f"{_SVC}.analysis_crud.replace_activity_loads") as replace,
        ):
            assert analysis_service.compute_loads_for_activity(7, db) == 0
        replace.assert_not_called()
        db.commit.assert_not_called()

    def test_without_a_power_threshold_the_heart_rate_chain_still_feeds_the_loads(self):
        db = _db()
        settings = _settings(
            hr_threshold=analysis_schema.HeartRateThresholdRead(
                value_bpm=150.0,
                source=analysis_constants.ThresholdSource.AUTO,
            )
        )
        with (
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=settings),
            patch(f"{_SVC}.activity_streams_integration.get_stream_for_derivation", return_value=None),
            patch(f"{_SVC}.analysis_crud.replace_activity_loads") as replace,
        ):
            analysis_service.compute_loads_for_activity(1, db)
        methods = {record.method for record in replace.call_args.args[5]}
        assert methods == {
            analysis_constants.LoadMethod.HR_LOAD,
            analysis_constants.LoadMethod.RSS_TOTAL,
            analysis_constants.LoadMethod.RSS_MID,
        }

    def test_an_uncategorized_sport_gets_no_power_threshold(self):
        """An activity type outside the sport vocabulary cannot be given a sport's threshold."""
        db = _db()
        settings = _settings(
            power_thresholds={
                "run": analysis_schema.PowerThresholdRead(
                    value_w=250.0,
                    w_prime_j=None,
                    source=analysis_constants.ThresholdSource.AUTO,
                    cv_pct=None,
                )
            }
        )
        with (
            patch(
                f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity(activity_type=999)
            ),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=settings),
            patch(
                f"{_SVC}.activity_streams_integration.get_stream_for_derivation", return_value=_stream([250.0] * 600)
            ),
            patch(f"{_SVC}.analysis_crud.replace_activity_loads") as replace,
        ):
            analysis_service.compute_loads_for_activity(1, db)
        methods = {record.method for record in replace.call_args.args[5]}
        assert analysis_constants.LoadMethod.COGGAN_TSS not in methods


class TestThresholdRefresh:
    def _refresh(self, activities: list[SimpleNamespace], streams: dict[tuple[int, int], SimpleNamespace]):
        """Run the refresh pass, returning the settings it stored."""
        db = _db()
        stored: list[analysis_schema.AnalysisSettingsRead] = []

        def stream_for(activity_id: int, stream_type: int, _db):
            return streams.get((activity_id, stream_type))

        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=activities),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.activity_streams_integration.get_stream_for_derivation", side_effect=stream_for),
            patch(f"{_SVC}.analysis_crud.replace_duration_peaks") as peaks,
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.refresh_thresholds(2, db)
        return (stored[0] if stored else None), peaks, db

    def test_a_curve_over_enough_activities_yields_critical_power(self):
        activities = [_activity(activity_id=index) for index in (1, 2, 3, 4)]
        streams = {
            (index, 2): _stream([300.0] * 1800 + [200.0] * 1800) for index in (1, 2, 3, 4)
        }  # long enough for the 300-1800 s fit range at 1 Hz
        settings, peaks_written, db = self._refresh(activities, streams)
        assert settings is not None
        assert "run" in settings.power_thresholds
        assert settings.power_thresholds["run"].source is analysis_constants.ThresholdSource.AUTO
        assert peaks_written.called
        db.commit.assert_called_once()

    def test_too_few_contributing_activities_derive_nothing(self):
        """One activity cannot define a threshold: it fits a curve through itself.

        A single 288-minute walk invented a 202 W threshold and scored itself 529,
        which is what this guard exists to stop.
        """
        activities = [_activity(activity_id=1, activity_type=11)]
        streams = {(1, 2): _stream([300.0] * 1800 + [200.0] * 1800)}
        settings, _, _db = self._refresh(activities, streams)
        assert settings is not None
        assert settings.power_thresholds == {}

    def test_a_manual_threshold_survives_the_refresh(self):
        activities = [_activity(activity_id=index) for index in (1, 2, 3, 4)]
        streams = {(index, 2): _stream([300.0] * 1800 + [200.0] * 1800) for index in (1, 2, 3, 4)}
        manual = _settings(
            power_thresholds={
                "run": analysis_schema.PowerThresholdRead(
                    value_w=280.0,
                    w_prime_j=None,
                    source=analysis_constants.ThresholdSource.MANUAL,
                    cv_pct=None,
                )
            }
        )
        db = _db()
        stored: list[analysis_schema.AnalysisSettingsRead] = []
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=activities),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=manual),
            patch(
                f"{_SVC}.activity_streams_integration.get_stream_for_derivation",
                side_effect=lambda a, t, _d: streams.get((a, t)),
            ),
            patch(f"{_SVC}.analysis_crud.replace_duration_peaks"),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.refresh_thresholds(2, db)
        assert stored[0].power_thresholds["run"].value_w == 280.0
        assert stored[0].power_thresholds["run"].source is analysis_constants.ThresholdSource.MANUAL

    def test_the_threshold_heart_rate_is_the_best_sustained_half_hour(self):
        activities = [_activity(activity_id=index) for index in (1, 2)]
        streams = {
            (1, 1): _stream([120.0] * 600 + [158.0] * 1800 + [120.0] * 600, key="hr"),
            (2, 1): _stream([130.0] * 3000, key="hr"),
        }
        db = _db()
        stored: list[analysis_schema.AnalysisSettingsRead] = []
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=activities),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(
                f"{_SVC}.activity_streams_integration.get_stream_for_derivation",
                side_effect=lambda a, t, _d: streams.get((a, t)),
            ),
            patch(f"{_SVC}.analysis_crud.replace_duration_peaks"),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.refresh_thresholds(2, db)
        assert stored[0].hr_threshold is not None
        assert stored[0].hr_threshold.value_bpm == pytest.approx(158.0)


class TestWalks:
    def test_the_reconcile_pass_skips_activities_already_measured(self):
        db = _db()
        pages = [[1, 2, 3], [4], []]
        with (
            patch(f"{_SVC}.activity_integration.list_activity_ids_after", side_effect=pages),
            patch(f"{_SVC}.analysis_crud.activity_ids_with_loads", return_value={1, 2}),
            patch(
                f"{_SVC}.analysis_crud.get_settings",
                return_value=_settings(power_thresholds={"run": _power_threshold()}),
            ),
            patch(
                f"{_SVC}.activity_integration.get_activity_for_derivation",
                side_effect=lambda activity_id, _db: _activity(activity_id),
            ),
            patch(f"{_SVC}.activity_streams_integration.get_stream_for_derivation", return_value=None),
            patch(f"{_SVC}.analysis_crud.replace_activity_loads", return_value=1) as replace,
        ):
            analysis_service.reconcile_missing_loads(db)
        assert {call.args[0] for call in replace.call_args_list} == {3, 4}

    def test_an_athlete_without_thresholds_gets_them_derived_first(self):
        """Otherwise the first pass writes nothing and history is never measured."""
        db = _db()
        calls: list[str] = []
        with (
            patch(f"{_SVC}.activity_integration.list_activity_ids_after", side_effect=[[1], []]),
            patch(f"{_SVC}.analysis_crud.activity_ids_with_loads", return_value=set()),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(
                f"{_SVC}.activity_integration.get_activity_for_derivation",
                side_effect=lambda activity_id, _db: _activity(activity_id),
            ),
            patch(
                f"{_SVC}.activity_integration.list_user_activities",
                side_effect=lambda _u, _db: calls.append("refresh") or [],
            ),
            patch(f"{_SVC}.activity_streams_integration.get_stream_for_derivation", return_value=None),
            patch(f"{_SVC}.analysis_crud.replace_duration_peaks"),
            patch(f"{_SVC}.analysis_crud.store_settings"),
            patch(f"{_SVC}.analysis_crud.replace_activity_loads", return_value=0),
        ):
            analysis_service.reconcile_missing_loads(db)
        assert calls == ["refresh"]


class TestReads:
    def test_only_the_owner_reads_an_activity_loads(self):
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=False),
            pytest.raises(core_exceptions.NotFoundError),
        ):
            analysis_service.list_activity_loads(5, 2, _db())

    def test_the_owner_gets_every_formula_result(self):
        rows = [
            analysis_schema.ActivityLoadRead(
                method=analysis_constants.LoadMethod.COGGAN_TSS,
                load=100.0,
                intensity_factor=1.0,
                threshold_used=250.0,
                duration_s=3600.0,
            )
        ]
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.analysis_crud.list_activity_loads", return_value=rows),
        ):
            assert analysis_service.list_activity_loads(5, 2, _db()) == rows


def _power_threshold() -> analysis_schema.PowerThresholdRead:
    """Return a stored-style power threshold."""
    return analysis_schema.PowerThresholdRead(
        value_w=250.0,
        w_prime_j=20000.0,
        source=analysis_constants.ThresholdSource.AUTO,
        cv_pct=3.0,
    )


class TestLocalDay:
    """The day a load lands on is the athlete's local day, not the UTC one."""

    def test_a_late_utc_run_rolls_into_the_next_local_day(self):
        activity = _activity(start_time=datetime(2026, 10, 15, 22, 0, tzinfo=UTC), timezone="Asia/Tokyo")
        assert analysis_service._local_day(activity) == date(2026, 10, 16)

    def test_no_timezone_falls_back_to_utc(self):
        activity = _activity(start_time=datetime(2026, 10, 15, 22, 0, tzinfo=UTC), timezone=None)
        assert analysis_service._local_day(activity) == date(2026, 10, 15)

    def test_an_unresolvable_timezone_still_keeps_the_load(self):
        activity = _activity(start_time=_T0_UTC, timezone="Not/AZone")
        assert analysis_service._local_day(activity) == _T0_UTC.date()

    def test_an_activity_without_start_time_has_no_day(self):
        assert analysis_service._local_day(_activity(start_time=None)) is None


class TestFitnessSeries:
    """Reading the stored rows as a fatigue/fitness series."""

    def test_days_without_loads_still_decay_between_the_plotted_points(self):
        totals = [(date(2026, 10, 1), 100.0), (date(2026, 10, 5), 50.0)]
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.daily_load_totals", return_value=totals),
        ):
            series = analysis_service.get_fitness_series(
                2,
                _db(),
                from_date=date(2026, 10, 1),
                to_date=date(2026, 10, 6),
            )
        points = {point.date: point for point in series.points}
        assert set(points) == {date(2026, 10, day) for day in range(1, 7)}
        assert points[date(2026, 10, 2)].load == 0.0
        assert points[date(2026, 10, 2)].acute < 100.0  # decayed, not reset
        assert points[date(2026, 10, 2)].acute > points[date(2026, 10, 4)].acute

    def test_the_series_is_seeded_before_the_plotted_window(self):
        """A window starting mid-history must not ramp up from zero on its first day."""
        totals = [(date(2026, 9, 1) + timedelta(days=offset), 100.0) for offset in range(40)]
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.daily_load_totals", return_value=totals),
        ):
            series = analysis_service.get_fitness_series(
                2,
                _db(),
                chronic_days=10,
                from_date=date(2026, 10, 10),
                to_date=date(2026, 10, 12),
            )
        first = series.points[0]
        assert first.date == date(2026, 10, 10)
        assert first.chronic == pytest.approx(100.0, rel=0.05)

    def test_balance_is_fitness_minus_fatigue(self):
        totals = [(date(2026, 10, 1), 300.0)]
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.daily_load_totals", return_value=totals),
        ):
            series = analysis_service.get_fitness_series(
                2,
                _db(),
                acute_days=5,
                chronic_days=42,
                from_date=date(2026, 10, 1),
                to_date=date(2026, 10, 3),
            )
        for point in series.points:
            assert point.balance == pytest.approx(point.chronic - point.acute, abs=0.02)

    def test_missing_parameters_come_from_the_settings_row(self):
        settings = _settings(
            acute_days=5,
            chronic_days=60,
            primary_method=analysis_constants.LoadMethod.HR_LOAD,
        )
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=settings),
            patch(f"{_SVC}.analysis_crud.daily_load_totals", return_value=[]) as totals,
        ):
            series = analysis_service.get_fitness_series(
                2, _db(), from_date=date(2026, 10, 1), to_date=date(2026, 10, 2)
            )
        assert series.method is analysis_constants.LoadMethod.HR_LOAD
        assert totals.call_args.args[1] == "hr_load"
        assert (series.acute_days, series.chronic_days) == (5, 60)

    def test_a_requested_method_overrides_the_stored_primary(self):
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.daily_load_totals", return_value=[]) as totals,
        ):
            series = analysis_service.get_fitness_series(
                2,
                _db(),
                method=analysis_constants.LoadMethod.SKIBA_SCORE,
                from_date=date(2026, 10, 1),
                to_date=date(2026, 10, 2),
            )
        assert totals.call_args.args[1] == "skiba_score"
        assert series.method is analysis_constants.LoadMethod.SKIBA_SCORE

    def test_a_sport_narrows_the_series_to_that_sport(self):
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.daily_load_totals", return_value=[]) as totals,
        ):
            analysis_service.get_fitness_series(
                2,
                _db(),
                sport="bike",
                from_date=date(2026, 10, 1),
                to_date=date(2026, 10, 2),
            )
        assert totals.call_args.kwargs["sport"] == "bike"

    def test_an_unknown_sport_is_refused(self):
        with pytest.raises(core_exceptions.InvalidInputError):
            analysis_service.get_fitness_series(2, _db(), sport="kitesurfing")

    def test_an_inverted_range_is_refused(self):
        with pytest.raises(core_exceptions.InvalidInputError):
            analysis_service.get_fitness_series(2, _db(), from_date=date(2026, 10, 10), to_date=date(2026, 10, 1))

    def test_no_loads_at_all_answers_a_flat_window(self):
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.daily_load_totals", return_value=[]),
        ):
            series = analysis_service.get_fitness_series(
                2,
                _db(),
                from_date=date(2026, 10, 1),
                to_date=date(2026, 10, 10),
            )
        assert len(series.points) == 10
        assert all(point.load == 0.0 and point.chronic == 0.0 for point in series.points)


class TestSettingsClearing:
    """An explicit null clears a value the athlete no longer wants."""

    def test_a_null_hr_threshold_falls_back_to_derived(self):
        db = _db()
        current = _settings(
            hr_threshold=analysis_schema.HeartRateThresholdRead(
                value_bpm=160.0,
                source=analysis_constants.ThresholdSource.MANUAL,
            )
        )
        stored: list[analysis_schema.AnalysisSettingsRead] = []
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=current),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.update_settings(2, analysis_schema.AnalysisSettingsUpdate(hr_threshold_bpm=None), db)
        assert stored[0].hr_threshold is None

    def test_an_omitted_hr_threshold_stays_put(self):
        db = _db()
        current = _settings(
            hr_threshold=analysis_schema.HeartRateThresholdRead(
                value_bpm=160.0,
                source=analysis_constants.ThresholdSource.MANUAL,
            )
        )
        stored: list[analysis_schema.AnalysisSettingsRead] = []
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=current),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.update_settings(2, analysis_schema.AnalysisSettingsUpdate(acute_days=10), db)
        assert stored[0].hr_threshold is not None
        assert stored[0].hr_threshold.value_bpm == 160.0

    def test_a_null_resting_hr_turns_the_impulse_formulas_off(self):
        db = _db()
        current = _settings(resting_hr=50)
        stored: list[analysis_schema.AnalysisSettingsRead] = []
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=current),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.update_settings(2, analysis_schema.AnalysisSettingsUpdate(resting_hr=None), db)
        assert stored[0].resting_hr is None


class TestPowerDuration:
    def _duration(self, peaks, activities, settings, **overrides):
        db = _db()
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=settings),
            patch(f"{_SVC}.analysis_crud.list_duration_peaks", return_value=peaks),
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=activities),
            patch(f"{_SVC}._stream_samples", return_value=(1.0, (), ())),
        ):
            series = analysis_service.get_power_duration(2, "run", db, **overrides)
        return series, db

    def _peaks(self):
        critical_power, w_prime = 250.0, 20_000.0
        return [
            analysis_contracts.PeakRecord(duration_s=duration, value=critical_power + w_prime / duration, activity_id=7)
            for duration in (300, 420, 600, 900, 1200, 1800)
        ]

    def test_curve_and_fit_and_threshold_travel_together(self):
        settings = _settings(
            power_thresholds={
                "run": analysis_schema.PowerThresholdRead(
                    value_w=250.0,
                    w_prime_j=20000.0,
                    source=analysis_constants.ThresholdSource.AUTO,
                    cv_pct=2.0,
                )
            }
        )
        series, _db = self._duration(self._peaks(), [_activity()], settings)
        assert [point.duration_s for point in series.points] == [300, 420, 600, 900, 1200, 1800]
        assert series.fit is not None
        assert series.fit.critical_power_w == pytest.approx(250.0, rel=1e-3)
        assert series.threshold is not None
        assert series.threshold.value_w == 250.0
        assert series.sessions[0].activity_id == 1

    def test_recent_windows_recompute_their_curves_from_streams(self):
        """A window's curve answers what the athlete can do there and then.

        Filtering the all-time table instead would drop the durations whose record
        is old; recomputation keeps them, at the recent value.
        """
        now = datetime.now(UTC)
        recent = _activity(start_time=now - timedelta(days=10))
        older = _activity(9, start_time=now - timedelta(days=100))
        activities = [recent, older]

        def _streams(activity_id, stream_type, _key, _db, *, keep_zeros):
            if stream_type != activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, (), ()
            watts = 320.0 if activity_id == recent.id else 420.0
            return 1.0, tuple([watts] * 1200), ()

        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=_settings()),
            patch(f"{_SVC}.analysis_crud.list_duration_peaks", return_value=[]),
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=activities),
            patch(f"{_SVC}._stream_samples", side_effect=_streams),
        ):
            series = analysis_service.get_power_duration(2, "run", _db())

        by_months = {overlay.months: overlay for overlay in series.overlays}
        assert set(by_months) == {3, 6}
        assert all(point.value == 320.0 for point in by_months[3].points)
        assert by_months[3].points[0].activity_id == recent.id
        assert all(point.value == 420.0 for point in by_months[6].points)
        assert by_months[6].points[0].activity_id == older.id
        # the all-time curve stays the cached one, untouched by the overlays
        assert series.points == []

    def test_a_recent_window_without_activities_just_omits_itself(self):
        old = _activity(start_time=datetime.now(UTC) - timedelta(days=400))
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=_settings()),
            patch(f"{_SVC}.analysis_crud.list_duration_peaks", return_value=[]),
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=[old]),
            patch(f"{_SVC}._stream_samples", return_value=(1.0, tuple([250.0] * 600), ())),
        ):
            series = analysis_service.get_power_duration(2, "run", _db())
        assert series.overlays == []

    def test_preview_window_overrides_without_touching_settings(self):
        """A request-scoped fit window answers for the preview; the stored threshold stays."""
        series, _db = self._duration(self._peaks(), [_activity()], _settings(), fit_from_s=300, fit_to_s=1200)
        assert series.fit is not None
        assert (series.fit.fit_from_s, series.fit.fit_to_s) == (300, 1200)
        assert series.threshold is None  # nothing stored; the preview does not store either

    def test_stored_window_is_the_default_fit_window(self):
        settings = _settings(fit_from_s=600, fit_to_s=1800)
        series, _db = self._duration(self._peaks(), [], settings)
        assert series.fit is not None
        assert (series.fit.fit_from_s, series.fit.fit_to_s) == (600, 1800)

    def test_only_power_activities_of_the_sport_are_offered_as_sessions(self):
        activities = [
            _activity(activity_id=1),
            _activity(activity_id=2, activity_type=11),  # walk sport, no peak here
            _activity(activity_id=3, normalized_power=None),  # no power
        ]
        series, _db = self._duration(self._peaks(), activities, _settings())
        assert [session.activity_id for session in series.sessions] == [1]

    def test_sessions_are_newest_first(self):
        activities = [
            _activity(activity_id=1, start_time=datetime(2026, 10, 1, tzinfo=UTC)),
            _activity(activity_id=2, start_time=datetime(2026, 10, 20, tzinfo=UTC)),
        ]
        series, _db = self._duration(self._peaks(), activities, _settings())
        assert [session.activity_id for session in series.sessions] == [2, 1]

    def test_an_unknown_sport_is_refused(self):
        with pytest.raises(core_exceptions.InvalidInputError):
            analysis_service.get_power_duration(2, "kitesurfing", _db())

    def test_an_inverted_preview_window_is_refused(self):
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            pytest.raises(core_exceptions.InvalidInputError),
        ):
            analysis_service.get_power_duration(2, "run", _db(), fit_from_s=1800, fit_to_s=300)


class TestWprimeBalance:
    _SETTINGS = _settings(
        power_thresholds={
            "run": analysis_schema.PowerThresholdRead(
                value_w=250.0,
                w_prime_j=1000.0,
                source=analysis_constants.ThresholdSource.AUTO,
                cv_pct=2.0,
            )
        }
    )

    def test_only_the_owner_reads_the_balance(self):
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=False),
            pytest.raises(core_exceptions.NotFoundError),
        ):
            analysis_service.get_wprime_balance(5, 2, _db())

    def test_a_powerful_activity_reports_totals_and_a_depleting_series(self):
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=self._SETTINGS),
            patch(f"{_SVC}.activity_streams_integration.get_stream_for_derivation", return_value=_stream([300.0] * 30)),
        ):
            balance = analysis_service.get_wprime_balance(1, 2, db)
        assert balance.cp_w == 250.0
        assert balance.w_prime_j == 1000.0
        assert balance.work_above_cp_j == pytest.approx(1500.0)
        assert balance.time_above_cp_s == pytest.approx(30.0)
        assert balance.points[0].remaining_j < 1000.0
        assert balance.points[-1].remaining_j == pytest.approx(0.0)
        assert balance.tau_s is not None

    def test_without_a_threshold_nothing_is_measured(self):
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=_settings()),
        ):
            balance = analysis_service.get_wprime_balance(1, 2, db)
        assert balance.cp_w is None
        assert balance.points == []

    def test_cp_without_capacity_still_reports_the_anaerobic_totals(self):
        """A typed-in FTP gives no W', but time and work above it are still facts."""
        settings = _settings(
            power_thresholds={
                "run": analysis_schema.PowerThresholdRead(
                    value_w=250.0,
                    w_prime_j=None,
                    source=analysis_constants.ThresholdSource.MANUAL,
                    cv_pct=None,
                )
            }
        )
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=settings),
            patch(f"{_SVC}.activity_streams_integration.get_stream_for_derivation", return_value=_stream([300.0] * 20)),
        ):
            balance = analysis_service.get_wprime_balance(1, 2, db)
        assert balance.w_prime_j is None
        assert balance.points == []
        assert balance.work_above_cp_j == pytest.approx(1000.0)
        assert balance.time_above_cp_s == pytest.approx(20.0)

    def test_the_series_is_thinned_to_the_transfer_cap(self):
        settings = self._SETTINGS
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=settings),
            patch(
                f"{_SVC}.activity_streams_integration.get_stream_for_derivation",
                return_value=_stream([200.0] * 6000),
            ),
        ):
            balance = analysis_service.get_wprime_balance(1, 2, db)
        assert len(balance.points) <= analysis_constants.WPRIME_POINTS_MAX


class TestFitWindowParameters:
    """The critical-power fit range is the athlete's parameter, not a constant."""

    def test_update_stores_the_window(self):
        db = _db()
        stored: list[analysis_schema.AnalysisSettingsRead] = []
        payload = analysis_schema.AnalysisSettingsUpdate(fit_from_s=120, fit_to_s=900)
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.update_settings(2, payload, db)
        assert (stored[0].fit_from_s, stored[0].fit_to_s) == (120, 900)

    def test_an_inverted_window_is_refused(self):
        db = _db()
        payload = analysis_schema.AnalysisSettingsUpdate(fit_from_s=1800, fit_to_s=600)
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=None),
            patch(f"{_SVC}.analysis_crud.store_settings"),
            pytest.raises(core_exceptions.InvalidInputError),
        ):
            analysis_service.update_settings(2, payload, db)

    def test_the_stored_window_feeds_the_threshold_pass(self):
        """Derivation and preview use one window, so what the chart shows is what gets stored."""
        activities = [_activity(activity_id=index) for index in (1, 2, 3, 4)]
        streams = {(index, 2): _stream([300.0] * 1800 + [200.0] * 1800) for index in (1, 2, 3, 4)}
        settings = _settings(fit_from_s=600, fit_to_s=1800)
        db = _db()
        stored: list[analysis_schema.AnalysisSettingsRead] = []
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=activities),
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=settings),
            patch(
                f"{_SVC}.activity_streams_integration.get_stream_for_derivation",
                side_effect=lambda activity_id, stream_type, _db: streams.get((activity_id, stream_type)),
            ),
            patch(f"{_SVC}.analysis_crud.replace_duration_peaks"),
            patch(f"{_SVC}.analysis_crud.store_settings", side_effect=_capture_settings(stored)),
        ):
            analysis_service.refresh_thresholds(2, db)
        assert stored[0].power_thresholds["run"].source is analysis_constants.ThresholdSource.AUTO


class TestActivityIntervals:
    def test_the_breakdown_reads_from_the_activity_samples(self):
        db = _db()
        data = analysis_methods.LoadInput(
            moving_seconds=900.0,
            power_w=tuple([250.0] * 300 + [430.0] * 120 + [120.0] * 120 + [250.0] * 300 + [250.0] * 60),
            period_s=1.0,
            speed_ms=tuple([4.0] * 900),
            grade_pct=tuple([0.0] * 900),
            power_threshold_w=350.0,
        )
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}._load_input", return_value=data),
            patch(f"{_SVC}._stream_samples", return_value=(1.0, (), ())),
        ):
            read = analysis_service.get_activity_intervals(1, 2, db)
        assert read.shape is analysis_intervals.SessionShape.STEADY or read.segments
        assert read.threshold_w == 350.0
        kinds = [segment.kind for segment in read.segments]
        assert analysis_intervals.SegmentKind.INTERVAL in kinds
        assert sum(segment.duration_s for segment in read.segments) == pytest.approx(900.0)

    def test_an_activity_without_power_answers_steady_and_empty(self):
        db = _db()
        data = analysis_methods.LoadInput(moving_seconds=1800.0, average_hr=130.0)
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}._load_input", return_value=data),
            patch(f"{_SVC}._stream_samples", return_value=(1.0, (), ())),
        ):
            read = analysis_service.get_activity_intervals(1, 2, db)
        assert read.shape is analysis_intervals.SessionShape.STEADY
        assert read.segments == []

    def test_a_deleted_activity_answers_steady(self):
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=None),
        ):
            read = analysis_service.get_activity_intervals(9, 2, db)
        assert read.shape is analysis_intervals.SessionShape.STEADY
        assert read.threshold_w is None

    def test_reading_another_athlete_activity_is_not_found(self):
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=False),
            pytest.raises(core_exceptions.NotFoundError),
        ):
            analysis_service.get_activity_intervals(1, 7, db)


class TestTerrainClimbsService:
    """The intervals route also answers with the climbs the route itself shows."""

    @staticmethod
    def _streams(alt_slope_after_s: int, with_power: bool):
        """One hilly run: flat, then an 8 % ramp, then flat; 4 m/s throughout."""

        def _fake(activity_id, stream_type, _key, _db, *, keep_zeros):
            stamps = tuple(_T0_UTC + timedelta(seconds=i) for i in range(1500))
            speeds = tuple([4.0] * 1500)
            alt = 0.0
            eles = []
            for i in range(1500):
                if alt_slope_after_s <= i < alt_slope_after_s + 313:
                    alt += 0.32  # 8 % at 4 m/s
                eles.append(alt)
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                if not with_power:
                    return 1.0, (), ()
                return 1.0, tuple([300.0] * 600 + [400.0] * 313 + [300.0] * 587), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, speeds, stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                return 1.0, tuple(eles), stamps
            return 1.0, (), ()

        return _fake

    def _read(self, streams, *, with_power=True):
        db = _db()
        data = analysis_methods.LoadInput(
            moving_seconds=1500.0,
            power_w=tuple([300.0] * 1500) if with_power else None,
            period_s=1.0,
            speed_ms=tuple([4.0] * 1500),
            power_threshold_w=350.0,
        )
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}._load_input", return_value=data),
            patch(f"{_SVC}._stream_samples", side_effect=streams),
        ):
            return analysis_service.get_activity_intervals(1, 2, db)

    def test_the_route_answers_with_the_climb_it_shows(self):
        read = self._read(self._streams(600, with_power=True))
        assert len(read.climbs) == 1
        climb = read.climbs[0]
        assert 900.0 <= climb.length_m <= 1450.0  # the grade window rounds the foot and top
        assert climb.avg_power_w is not None and climb.rss is not None
        assert climb.rss_per_min is not None

    def test_an_activity_without_power_still_gets_its_climbs(self):
        read = self._read(self._streams(600, with_power=False), with_power=False)
        assert len(read.climbs) == 1  # terrain needs only altitude and speed
        climb = read.climbs[0]
        assert climb.avg_power_w is None
        assert climb.rss is None and climb.rss_per_min is None

    def test_a_powerless_route_with_a_critical_speed_scores_its_climbs_by_pace(self):
        settings_with_cs = _settings(
            speed_thresholds={
                "run": analysis_schema.SpeedThresholdRead(value_ms=4.0, source=analysis_constants.ThresholdSource.AUTO)
            }
        )
        db = _db()
        data = analysis_methods.LoadInput(
            moving_seconds=1500.0,
            period_s=1.0,
            speed_ms=tuple([4.0] * 1500),
            speed_threshold_ms=4.0,
            power_threshold_w=None,
        )
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}._load_input", return_value=data),
            patch(f"{_SVC}._stream_samples", side_effect=self._streams(600, with_power=False)),
        ):
            read = analysis_service.get_activity_intervals(1, 2, db)
        assert len(read.climbs) == 1
        climb = read.climbs[0]
        assert climb.rss is not None
        assert climb.rss_source == "pace"
        assert climb.rss_per_min is not None
        assert climb.avg_power_w is None

    def test_a_flat_route_answers_no_climbs(self):
        read = self._read(self._streams(10**9, with_power=True))
        assert read.climbs == []


class TestBestTimes:
    def _streams(self, seconds: int, watts: float, speed: float, grade_pct: float):
        """Return a ``_stream_samples`` stand-in for one constant-effort run."""

        def _fake(activity_id, stream_type, _key, _db, *, keep_zeros):
            stamps = tuple(_T0_UTC + timedelta(seconds=i) for i in range(seconds))
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, tuple([watts] * seconds), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, tuple([speed] * seconds), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                alt = [grade_pct / 100.0 * speed * i for i in range(seconds)]
                return 1.0, tuple(alt), stamps
            return 1.0, (), ()

        return _fake

    def _read(self, activities, streams):
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=activities),
            patch(f"{_SVC}.get_settings", return_value=_settings()),
            patch(f"{_SVC}._stream_samples", side_effect=streams),
        ):
            return analysis_service.get_best_times(2, "run", db)

    def test_the_fastest_split_of_each_distance_wins(self):
        slow = _activity(1)
        fast = _activity(2, start_time=_T0_UTC + timedelta(days=1))

        def streams(activity_id, stream_type, _key, _db, *, keep_zeros):
            seconds = 1000
            stamps = tuple(_T0_UTC + timedelta(seconds=i) for i in range(seconds))
            speed = 4.0 if activity_id == slow.id else 5.0
            watts = 300.0 if activity_id == slow.id else 400.0
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, tuple([watts] * seconds), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, tuple([speed] * seconds), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                return 1.0, tuple([0.0] * seconds), stamps
            return 1.0, (), ()

        read = self._read([slow, fast], streams)
        by_distance = {record.distance_m: record for record in read.records}
        assert by_distance[1000].activity_id == fast.id
        assert by_distance[1000].time_s == pytest.approx(200.0, rel=0.02)
        assert by_distance[1000].avg_power_w == pytest.approx(400.0, rel=0.02)

    def test_the_window_drops_older_activities(self):
        now = datetime.now(UTC)
        recent = _activity(1, start_time=now - timedelta(days=10))
        older = _activity(2, start_time=now - timedelta(days=100))
        streams = self._streams(400, 300.0, 5.0, 0.0)
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=[recent, older]),
            patch(f"{_SVC}.get_settings", return_value=_settings()),
            patch(f"{_SVC}._stream_samples", side_effect=streams),
        ):
            read = analysis_service.get_best_times(2, "run", db, months=3)
        assert {record.activity_id for record in read.records} == {recent.id}

    def test_downhill_only_activities_contribute_nothing(self):
        activity = _activity(1, start_time=datetime.now(UTC))
        streams = self._streams(1500, 300.0, 5.0, -2.5)
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=[activity]),
            patch(f"{_SVC}.get_settings", return_value=_settings()),
            patch(f"{_SVC}._stream_samples", side_effect=streams),
        ):
            read = analysis_service.get_best_times(2, "run", db)
        assert read.records == []

    def test_activities_without_power_never_appear(self):
        activity = _activity(1, start_time=datetime.now(UTC))

        def streams(activity_id, stream_type, key, db, *, keep_zeros):
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, (), ()
            return 1.0, tuple([4.0] * 1500), tuple()

        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=[activity]),
            patch(f"{_SVC}.get_settings", return_value=_settings()),
            patch(f"{_SVC}._stream_samples", side_effect=streams),
        ):
            read = analysis_service.get_best_times(2, "run", db)
        assert read.records == []

    def test_a_repeated_gps_spike_does_not_become_a_split(self):
        """Alignment copies the last speed across a dropout; six copies fake a 100 m sprint.

        Raw, the copied spike covers 100 m in ~13 s and even clears the average-speed
        ceiling (the window trims its own edges). The speed median erases copies
        shorter than half its window, and the honest distance stands: 3 m/s for 100 m.
        """
        seconds = 400
        speeds = [3.0] * seconds
        for index in range(200, 206):  # a dropout repeating one 16,7 m/s sample
            speeds[index] = 16.66

        def streams(activity_id, stream_type, _key, _db, *, keep_zeros):
            stamps = tuple(_T0_UTC + timedelta(seconds=i) for i in range(seconds))
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, tuple([210.0] * seconds), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, tuple(speeds), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                return 1.0, tuple([0.0] * seconds), stamps
            return 1.0, (), ()

        activity = _activity(1, start_time=datetime.now(UTC))
        threshold = analysis_schema.PowerThresholdRead(
            value_w=350.0,
            w_prime_j=20_000.0,
            source=analysis_constants.ThresholdSource.AUTO,
            cv_pct=2.0,
        )
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=[activity]),
            patch(f"{_SVC}.get_settings", return_value=_settings(power_thresholds={"run": threshold})),
            patch(f"{_SVC}._stream_samples", side_effect=streams),
        ):
            read = analysis_service.get_best_times(2, "run", db)
        distances = {record.distance_m for record in read.records}
        # the copies feed a short window the athlete's engine never produced (210 W
        # under a 1,05x threshold floor), while the honest kilometre pace survives
        assert 100 not in distances
        assert 1000 in distances

    def test_short_splits_need_a_real_engine_behind_the_speed(self):
        """Fast coasting (gravity or jitter) is not a best; from 800 m up, pace suffices."""
        seconds = 500
        speeds = [5.5] * seconds  # 5,5 m/s: 100 m in 18 s
        watts = [180.0] * seconds  # while barely working

        def streams(activity_id, stream_type, _key, _db, *, keep_zeros):
            stamps = tuple(_T0_UTC + timedelta(seconds=i) for i in range(seconds))
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, tuple(watts), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, tuple(speeds), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                return 1.0, tuple([0.0] * seconds), stamps
            return 1.0, (), ()

        activity = _activity(1, start_time=datetime.now(UTC))
        threshold = analysis_schema.PowerThresholdRead(
            value_w=350.0,
            w_prime_j=20_000.0,
            source=analysis_constants.ThresholdSource.AUTO,
            cv_pct=2.0,
        )
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=[activity]),
            patch(f"{_SVC}.get_settings", return_value=_settings(power_thresholds={"run": threshold})),
            patch(f"{_SVC}._stream_samples", side_effect=streams),
        ):
            with_threshold = analysis_service.get_best_times(2, "run", db)
        distances = {record.distance_m for record in with_threshold.records}
        assert 100 not in distances  # 180 W carries no 100 m personal best
        assert 1000 in distances  # the same speed over 1 km is an honest tempo split

    def test_an_unknown_sport_is_rejected(self):
        db = _db()
        with pytest.raises(core_exceptions.InvalidInputError):
            analysis_service.get_best_times(2, "kiteboarding", db)


class TestLapDynamics:
    def _lap(
        self, lap_id, *, distance, elapsed, speed=None, cadence=None, cycles=None, power=None, ascent=None, descent=None
    ):
        return SimpleNamespace(
            id=lap_id,
            total_distance=distance,
            total_elapsed_time=elapsed,
            enhanced_avg_speed=speed,
            avg_cadence=cadence,
            total_cycles=cycles,
            avg_power=power,
            total_ascent=ascent,
            total_descent=descent,
            start_time=_T0_UTC,
        )

    def _read(self, laps, *, streams=None):
        db = _db()
        stamps = tuple(_T0_UTC + timedelta(seconds=i) for i in range(600))

        def default_streams(activity_id, stream_type, _key, _db, *, keep_zeros):
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, tuple([400.0] * 600), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, tuple([4.0] * 600), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                return 1.0, tuple([0.0] * 600), stamps
            return 1.0, (), ()

        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(
                f"{_SVC}.get_settings",
                return_value=_settings(
                    power_thresholds={
                        "run": analysis_schema.PowerThresholdRead(
                            value_w=350.0,
                            w_prime_j=20000.0,
                            source=analysis_constants.ThresholdSource.AUTO,
                            cv_pct=2.0,
                        )
                    }
                ),
            ),
            patch(f"{_SVC}.activity_laps_integration.list_laps_for_derivation", return_value=laps),
            patch(f"{_SVC}._stream_samples", side_effect=streams or default_streams),
        ):
            return analysis_service.get_activity_lap_dynamics(1, 2, db)

    def test_stride_prefers_speed_over_cadence_and_falls_back_to_cycles(self):
        by_cadence = self._lap(10, distance=1000.0, elapsed=250.0, speed=4.0, cadence=80)
        by_cycles = self._lap(11, distance=1000.0, elapsed=250.0, cycles=350)
        rows = self._read([by_cadence, by_cycles])
        assert rows[0].stride_length_m == pytest.approx(3.0)  # 4 m/s over 80 cycles/min
        assert rows[1].stride_length_m == pytest.approx(2.86, abs=0.01)

    def test_ascent_and_descent_travel_as_a_pair(self):
        rolling = self._lap(12, distance=1000.0, elapsed=300.0, speed=3.3, cadence=80, ascent=10, descent=8)
        rows = self._read([rolling])
        assert rows[0].elevation_gain_m == 10.0
        assert rows[0].elevation_loss_m == 8.0  # not netted to a misleading 2

    def test_lap_rss_entries_add_back_up_to_the_window_total(self):
        first = self._lap(20, distance=1000.0, elapsed=300.0, speed=3.3, cadence=80, power=400.0)
        second = self._lap(21, distance=1000.0, elapsed=300.0, speed=3.3, cadence=80, power=400.0)
        rows = self._read([first, second])
        whole = analysis_methods.rss_window_stress([400.0] * 600, 1.0, 350.0)
        assert rows[0].rss is not None and rows[1].rss is not None
        assert (rows[0].rss + rows[1].rss) == pytest.approx(whole, abs=0.3)
        # each lap ran exactly 300 s, so its rate is its score over five minutes
        assert rows[0].rss_per_min == pytest.approx(rows[0].rss / 5.0, abs=0.1)
        assert rows[1].rss_per_min == pytest.approx(rows[1].rss / 5.0, abs=0.1)

    def test_no_threshold_means_no_rss_column(self):
        lap = self._lap(30, distance=1000.0, elapsed=300.0, speed=3.3, cadence=80, power=400.0)
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}.get_settings", return_value=_settings()),
            patch(f"{_SVC}.activity_laps_integration.list_laps_for_derivation", return_value=[lap]),
            patch(f"{_SVC}._stream_samples", return_value=(1.0, (), ())),
        ):
            rows = analysis_service.get_activity_lap_dynamics(1, 2, db)
        assert rows[0].rss is None
        assert rows[0].stride_length_m is not None  # the rest of the view stays

    def test_a_lap_on_a_powerless_run_scores_through_the_pace_source(self):
        """No watts but speed, altitude and a critical speed: the lap still gets an aRSS."""
        lap = self._lap(35, distance=1200.0, elapsed=300.0, speed=4.0, cadence=80)

        def _hilly(activity_id, stream_type, _key, _db, *, keep_zeros):
            stamps = tuple(_T0_UTC + timedelta(seconds=i) for i in range(600))
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, (), ()
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, tuple([4.0] * 600), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                return 1.0, tuple([i * 0.4 for i in range(600)]), stamps  # 10 % climb
            return 1.0, (), ()

        db = _db()
        settings = _settings(
            power_thresholds={},
            speed_thresholds={
                "run": analysis_schema.SpeedThresholdRead(value_ms=4.0, source=analysis_constants.ThresholdSource.AUTO)
            },
        )
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=True),
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity()),
            patch(f"{_SVC}.get_settings", return_value=settings),
            patch(f"{_SVC}.activity_laps_integration.list_laps_for_derivation", return_value=[lap]),
            patch(f"{_SVC}._stream_samples", side_effect=_hilly),
        ):
            rows = analysis_service.get_activity_lap_dynamics(1, 2, db)
        assert rows[0].rss is not None
        assert rows[0].rss_source == "pace"
        assert rows[0].rss_per_min is not None

    def test_streams_without_a_utc_offset_still_locate_the_laps(self):
        """Waypoint times arrive naive while lap start times are aware: one shared UTC clock.

        The live streams of a device-written activity carry no offset, and a naive
        stamp compared against the database's aware lap start raised a TypeError;
        the window must still find its samples.
        """
        lap = self._lap(40, distance=1000.0, elapsed=300.0, speed=3.3, cadence=80, power=400.0)

        def naive_streams(activity_id, stream_type, _key, _db, *, keep_zeros):
            stamps = tuple((_T0_UTC + timedelta(seconds=i)).replace(tzinfo=None) for i in range(600))
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, tuple([400.0] * 600), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, tuple([4.0] * 600), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                return 1.0, tuple([0.0] * 600), stamps
            return 1.0, (), ()

        rows = self._read([lap], streams=naive_streams)
        assert rows[0].rss is not None

    def test_another_athletes_activity_is_not_found(self):
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.owns_activity", return_value=False),
            pytest.raises(core_exceptions.NotFoundError),
        ):
            analysis_service.get_activity_lap_dynamics(1, 7, db)


class TestWindowStreamStats:
    """The published surface other modules use to aggregate a stream window."""

    @staticmethod
    def _streams(with_power: bool = True):
        def _fake(activity_id, stream_type, _key, _db, *, keep_zeros):
            stamps = tuple(_T0_UTC + timedelta(seconds=i) for i in range(600))
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return (1.0, tuple([400.0] * 600), stamps) if with_power else (1.0, (), ())
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, tuple([4.0] * 600), stamps
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                return 1.0, tuple([10.0] * 600), stamps  # flat: the plain kernel applies
            if stream_type == activity_streams_constants.STREAM_TYPE_HR:
                return 1.0, tuple([150.0] * 600), stamps
            return 1.0, (), ()

        return _fake

    def _stats(self, windows, *, with_power=True, threshold=True):
        db = _db()
        thresholds = {"run": _power_threshold()} if threshold else {}
        with (
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=_activity(activity_type=2)),
            patch(f"{_SVC}.get_settings", return_value=_settings(power_thresholds=thresholds)),
            patch(f"{_SVC}._stream_samples", side_effect=self._streams(with_power=with_power)),
        ):
            return analysis_service.compute_window_stats(1, 2, windows, db)

    def test_a_powered_window_answers_power_heart_rate_and_the_a_rate(self):
        windows = [(_T0_UTC, _T0_UTC + timedelta(seconds=300))]
        (stat,) = self._stats(windows)
        assert stat.avg_power_w == 400.0
        assert stat.avg_hr_bpm == 150.0
        expected = analysis_methods.rss_window_stress([400.0] * 300, 1.0, 250.0)
        assert stat.rss_per_min == pytest.approx(expected * 60.0 / 300.0, abs=0.1)

    def test_the_rate_is_window_size_free(self):
        short = self._stats([(_T0_UTC, _T0_UTC + timedelta(seconds=100))])
        long = self._stats([(_T0_UTC, _T0_UTC + timedelta(seconds=500))])
        assert short[0].rss_per_min == pytest.approx(long[0].rss_per_min, abs=0.1)

    def test_without_power_the_window_still_answers_heart_rate(self):
        (stat,) = self._stats([(_T0_UTC, _T0_UTC + timedelta(seconds=300))], with_power=False)
        assert stat.avg_power_w is None
        assert stat.rss_per_min is None
        assert stat.avg_hr_bpm == 150.0

    def test_without_a_threshold_the_rate_stays_blank(self):
        (stat,) = self._stats([(_T0_UTC, _T0_UTC + timedelta(seconds=300))], threshold=False)
        assert stat.avg_power_w == 400.0
        assert stat.rss_per_min is None

    def test_windows_given_on_the_naive_device_clock_still_cut(self):
        (stat,) = self._stats([(_T0, _T0 + timedelta(seconds=300))])
        assert stat.avg_power_w == 400.0


class TestCriticalSpeedDerivation:
    """The pace-side twin of the CP fit: critical speed from grade-adjusted peaks."""

    @staticmethod
    def _run_activities(count: int = 4) -> list:
        return [SimpleNamespace(id=100 + i, activity_type=2) for i in range(count)]

    @staticmethod
    def _streams(bursts: list[tuple[int, float]]):
        """``_stream_samples`` stand-in: activity k bursts (secs, m/s) over a flat 3 m/s base."""

        def _fake(activity_id, stream_type, _key, _db, *, keep_zeros):
            index = activity_id - 100
            burst_s, burst_ms = bursts[index]
            total_s = 2400
            speeds = [burst_ms] * burst_s + [3.0] * (total_s - burst_s)
            if stream_type == activity_streams_constants.STREAM_TYPE_SPEED:
                return 1.0, tuple(speeds), ()
            if stream_type == activity_streams_constants.STREAM_TYPE_ELEVATION:
                return 1.0, tuple([50.0] * total_s), ()
            if stream_type == activity_streams_constants.STREAM_TYPE_POWER:
                return 1.0, (), ()
            if stream_type == activity_streams_constants.STREAM_TYPE_HR:
                return 1.0, tuple([140.0] * total_s), ()
            return 1.0, (), ()

        return _fake

    def test_runs_without_power_still_get_a_critical_speed(self):
        db = _db()
        bursts = [(300, 6.0), (600, 5.0), (1200, 4.4), (1500, 4.1)]
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=self._run_activities()),
            patch(f"{_SVC}._stream_samples", side_effect=self._streams(bursts)),
            patch(f"{_SVC}.get_settings", return_value=_settings()),
            patch(f"{_SVC}.analysis_crud.store_settings") as mock_store,
        ):
            analysis_service.refresh_thresholds(2, db)
        stored = mock_store.call_args.args[1]
        assert "run" in stored.speed_thresholds
        threshold = stored.speed_thresholds["run"]
        assert threshold.source is analysis_constants.ThresholdSource.AUTO
        assert 3.5 <= threshold.value_ms <= 4.5
        assert threshold.w_prime_m is not None and threshold.w_prime_m > 0

    def test_a_manual_critical_speed_is_left_alone(self):
        db = _db()
        settings = _settings()
        settings.speed_thresholds["run"] = analysis_schema.SpeedThresholdRead(
            value_ms=4.2, source=analysis_constants.ThresholdSource.MANUAL
        )
        bursts = [(300, 6.0), (600, 5.0), (1200, 4.4), (1500, 4.1)]
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=self._run_activities()),
            patch(f"{_SVC}._stream_samples", side_effect=self._streams(bursts)),
            patch(f"{_SVC}.get_settings", return_value=settings),
            patch(f"{_SVC}.analysis_crud.store_settings") as mock_store,
        ):
            analysis_service.refresh_thresholds(2, db)
        stored = mock_store.call_args.args[1]
        assert stored.speed_thresholds["run"].value_ms == 4.2
        assert stored.speed_thresholds["run"].source is analysis_constants.ThresholdSource.MANUAL

    def test_bikes_get_no_critical_speed_their_speed_is_drag_not_effort(self):
        db = _db()
        activities = [SimpleNamespace(id=100 + i, activity_type=4) for i in range(4)]
        bursts = [(300, 12.0), (600, 10.0), (1200, 9.0), (1500, 8.0)]
        with (
            patch(f"{_SVC}.activity_integration.list_user_activities", return_value=activities),
            patch(f"{_SVC}._stream_samples", side_effect=self._streams(bursts)),
            patch(f"{_SVC}.get_settings", return_value=_settings()),
            patch(f"{_SVC}.analysis_crud.store_settings") as mock_store,
        ):
            analysis_service.refresh_thresholds(2, db)
        stored = mock_store.call_args.args[1]
        assert "bike" not in stored.speed_thresholds

    def test_a_stated_critical_speed_survives_and_clears(self):
        db = _db()
        payload = analysis_schema.AnalysisSettingsUpdate(speed_threshold_ms={"run": 4.35})
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=_settings()),
            patch(f"{_SVC}.analysis_crud.store_settings") as mock_store,
        ):
            analysis_service.update_settings(2, payload, db)
        stored = mock_store.call_args.args[1]
        assert stored.speed_thresholds["run"].value_ms == 4.35
        assert stored.speed_thresholds["run"].source is analysis_constants.ThresholdSource.MANUAL
        payload_clear = analysis_schema.AnalysisSettingsUpdate(speed_threshold_ms={"run": None})
        with (
            patch(f"{_SVC}.analysis_crud.get_settings", return_value=stored),
            patch(f"{_SVC}.analysis_crud.store_settings") as mock_store_clear,
        ):
            analysis_service.update_settings(2, payload_clear, db)
        assert "run" not in mock_store_clear.call_args.args[1].speed_thresholds
