"""Tests for the analysis service: thresholds, measurement, and when to abstain.

The service owns the decisions the formulas cannot make: which threshold applies to
which sport, when a derived threshold is not trustworthy enough to use, that a value
the athlete typed is never overwritten, and who may read a stored load. The
formulas themselves are tested in ``test_methods.py``.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import core.exceptions as core_exceptions
import modules.analysis.constants as analysis_constants
import modules.analysis.schema as analysis_schema
import modules.analysis.service as analysis_service

_SVC = "modules.analysis.service"
_T0 = datetime(2026, 10, 15, 16, 0, 0)


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
) -> SimpleNamespace:
    """Return an activity shaped like the activities module's read schema."""
    return SimpleNamespace(
        id=activity_id,
        activity_type=activity_type,
        user_id=user_id,
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

        records = replace.call_args.args[2]
        methods = {record.method for record in records}
        assert analysis_constants.LoadMethod.COGGAN_TSS in methods
        assert written == 3
        assert replace.call_args.args[1] == 3600.0
        db.commit.assert_called_once()

    def test_a_deleted_activity_writes_nothing(self):
        db = _db()
        with (
            patch(f"{_SVC}.activity_integration.get_activity_for_derivation", return_value=None),
            patch(f"{_SVC}.analysis_crud.replace_activity_loads") as replace,
        ):
            assert analysis_service.compute_loads_for_activity(7, db) == 0
        replace.assert_not_called()
        db.commit.assert_not_called()

    def test_without_a_threshold_only_heart_rate_formulas_apply(self):
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
        methods = {record.method for record in replace.call_args.args[2]}
        assert methods == {analysis_constants.LoadMethod.HR_LOAD}

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
        methods = {record.method for record in replace.call_args.args[2]}
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
