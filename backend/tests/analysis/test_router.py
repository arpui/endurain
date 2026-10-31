"""Tests for the analysis routers: status codes and scope wiring."""

from datetime import date
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.database as core_db
import core.exceptions as core_exceptions
import modules.analysis.activity_router as analysis_activity_router
import modules.analysis.constants as analysis_constants
import modules.analysis.router as analysis_router
import modules.analysis.schema as analysis_schema


def _build_app(mock_db: MagicMock) -> FastAPI:
    """Return an app with the analysis routers and the real error boundary.

    Args:
        mock_db: Session double injected through the dependency override.

    Returns:
        A FastAPI app exercising only the analysis routes.
    """
    import modules.auth.dependencies as auth_deps

    app = FastAPI()
    core_exceptions.register_exception_handlers(app)
    app.include_router(analysis_router.router, prefix="/analysis")
    app.include_router(analysis_activity_router.router, prefix="/activities/{activity_id}")
    app.dependency_overrides[auth_deps.check_scopes] = lambda: None
    app.dependency_overrides[auth_deps.get_sub_from_access_token] = lambda: 2
    app.dependency_overrides[core_db.get_db] = lambda: mock_db
    return app


def _settings_read(**overrides) -> analysis_schema.AnalysisSettingsRead:
    """Return a settings payload."""
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


class TestSettingsRoutes:
    def test_get_returns_the_caller_parameters(self):
        client = TestClient(_build_app(MagicMock()))
        with patch(
            "modules.analysis.router.analysis_service.get_settings", return_value=_settings_read()
        ) as get_settings:
            response = client.get("/analysis/settings")
        assert response.status_code == 200
        assert response.json()["acute_days"] == 7
        get_settings.assert_called_once()

    def test_put_applies_the_body_and_returns_the_result(self):
        client = TestClient(_build_app(MagicMock()))
        updated = _settings_read(
            power_thresholds={
                "run": analysis_schema.PowerThresholdRead(
                    value_w=300.0,
                    w_prime_j=None,
                    source=analysis_constants.ThresholdSource.MANUAL,
                    cv_pct=None,
                )
            }
        )
        with patch("modules.analysis.router.analysis_service.update_settings", return_value=updated) as update_settings:
            response = client.put("/analysis/settings", json={"power_threshold_w": {"run": 300.0}})
        assert response.status_code == 200
        assert response.json()["power_thresholds"]["run"]["source"] == "manual"
        assert update_settings.call_args.args[1].power_threshold_w == {"run": 300.0}

    def test_a_time_constant_out_of_range_is_refused_before_the_service(self):
        client = TestClient(_build_app(MagicMock()))
        with patch("modules.analysis.router.analysis_service.update_settings") as update_settings:
            response = client.put("/analysis/settings", json={"acute_days": 0})
        assert response.status_code == 422
        update_settings.assert_not_called()


class TestQueueRoutes:
    def test_recompute_reports_only_that_it_was_queued(self):
        client = TestClient(_build_app(MagicMock()))
        with patch("modules.analysis.router.analysis_scheduled_jobs.schedule_analysis_recompute") as schedule:
            response = client.post("/analysis/recompute")
        assert response.status_code == 200
        assert response.json() == {"queued": True}
        schedule.assert_called_once()

    def test_threshold_refresh_is_queued_for_the_caller(self):
        client = TestClient(_build_app(MagicMock()))
        with patch("modules.analysis.router.analysis_scheduled_jobs.schedule_analysis_threshold_refresh") as schedule:
            response = client.post("/analysis/thresholds/refresh")
        assert response.status_code == 200
        schedule.assert_called_once_with(2)


class TestActivityLoadsRoute:
    def test_the_owner_reads_every_formula_result(self):
        client = TestClient(_build_app(MagicMock()))
        rows = [
            analysis_schema.ActivityLoadRead(
                method=analysis_constants.LoadMethod.COGGAN_TSS,
                load=100.0,
                intensity_factor=1.0,
                threshold_used=250.0,
                duration_s=3600.0,
            )
        ]
        with patch("modules.analysis.activity_router.analysis_service.list_activity_loads", return_value=rows):
            response = client.get("/activities/5/loads")
        assert response.status_code == 200
        assert response.json()[0]["method"] == "coggan_tss"

    def test_another_users_activity_is_not_found(self):
        client = TestClient(_build_app(MagicMock()))
        with patch(
            "modules.analysis.activity_router.analysis_service.list_activity_loads",
            side_effect=core_exceptions.NotFoundError("Activity 5 not found"),
        ):
            response = client.get("/activities/5/loads")
        assert response.status_code == 404


class TestFitnessRoute:
    def _series(self) -> analysis_schema.FitnessSeriesRead:
        return analysis_schema.FitnessSeriesRead(
            method=analysis_constants.LoadMethod.COGGAN_TSS,
            acute_days=7,
            chronic_days=42,
            sport=None,
            points=[
                analysis_schema.FitnessPointRead(
                    date=date(2026, 10, 1),
                    load=120.0,
                    acute=16.8,
                    chronic=2.84,
                    balance=-13.96,
                )
            ],
        )

    def test_the_series_answers_with_its_parameters(self):
        client = TestClient(_build_app(MagicMock()))
        with patch("modules.analysis.router.analysis_service.get_fitness_series", return_value=self._series()):
            response = client.get("/analysis/fitness")
        assert response.status_code == 200
        body = response.json()
        assert body["method"] == "coggan_tss"
        assert body["points"][0]["date"] == "2026-10-01"

    def test_the_query_parameters_reach_the_service(self):
        client = TestClient(_build_app(MagicMock()))
        with patch(
            "modules.analysis.router.analysis_service.get_fitness_series", return_value=self._series()
        ) as get_series:
            response = client.get(
                "/analysis/fitness",
                params={
                    "method": "hr_load",
                    "acute_days": 5,
                    "chronic_days": 45,
                    "sport": "run",
                    "from": "2026-07-01",
                    "to": "2026-10-01",
                },
            )
        assert response.status_code == 200
        kwargs = get_series.call_args.kwargs
        assert kwargs["method"] is analysis_constants.LoadMethod.HR_LOAD
        assert kwargs["from_date"] == date(2026, 7, 1)
        assert kwargs["to_date"] == date(2026, 10, 1)

    def test_an_unknown_method_is_refused_before_the_service(self):
        client = TestClient(_build_app(MagicMock()))
        with patch("modules.analysis.router.analysis_service.get_fitness_series") as get_series:
            response = client.get("/analysis/fitness", params={"method": "nope"})
        assert response.status_code == 422
        get_series.assert_not_called()

    def test_a_time_constant_out_of_range_is_refused(self):
        client = TestClient(_build_app(MagicMock()))
        response = client.get("/analysis/fitness", params={"acute_days": 0})
        assert response.status_code == 422


class TestPowerDurationRoute:
    def _read(self) -> analysis_schema.PowerDurationRead:
        return analysis_schema.PowerDurationRead(
            sport="run",
            points=[analysis_schema.DurationPeakRead(duration_s=300, value=316.7, activity_id=7)],
            fit=analysis_schema.PowerDurationFitRead(
                critical_power_w=250.0,
                w_prime_j=20000.0,
                cv_pct=2.1,
                fit_from_s=300,
                fit_to_s=1800,
            ),
            threshold=None,
            sessions=[
                analysis_schema.PowerDurationSessionRead(
                    activity_id=1,
                    name="Morning run",
                    start_time="2026-10-15T16:00:00+00:00",
                )
            ],
        )

    def test_the_curve_answers_with_points_fit_and_sessions(self):
        client = TestClient(_build_app(MagicMock()))
        with patch("modules.analysis.router.analysis_service.get_power_duration", return_value=self._read()):
            response = client.get("/analysis/power-duration", params={"sport": "run"})
        assert response.status_code == 200
        body = response.json()
        assert body["points"][0]["duration_s"] == 300
        assert body["fit"]["critical_power_w"] == 250.0
        assert body["sessions"][0]["activity_id"] == 1

    def test_sport_and_preview_window_reach_the_service(self):
        client = TestClient(_build_app(MagicMock()))
        with patch(
            "modules.analysis.router.analysis_service.get_power_duration", return_value=self._read()
        ) as get_duration:
            response = client.get("/analysis/power-duration", params={"sport": "bike", "fit_from": 120, "fit_to": 900})
        assert response.status_code == 200
        assert get_duration.call_args.args[1] == "bike"
        assert get_duration.call_args.kwargs == {"fit_from_s": 120, "fit_to_s": 900}

    def test_sport_is_required(self):
        client = TestClient(_build_app(MagicMock()))
        response = client.get("/analysis/power-duration")
        assert response.status_code == 422

    def test_an_out_of_range_window_is_refused_before_the_service(self):
        client = TestClient(_build_app(MagicMock()))
        response = client.get("/analysis/power-duration", params={"sport": "run", "fit_from": 5})
        assert response.status_code == 422


class TestWprimeRoute:
    def _read(self) -> analysis_schema.WprimeBalanceRead:
        return analysis_schema.WprimeBalanceRead(
            cp_w=250.0,
            w_prime_j=20000.0,
            tau_s=819.5,
            work_above_cp_j=4500.0,
            time_above_cp_s=60.0,
            points=[analysis_schema.WprimePointRead(t_s=1.0, remaining_j=19750.0)],
        )

    def test_the_owner_reads_the_balance(self):
        client = TestClient(_build_app(MagicMock()))
        with patch("modules.analysis.activity_router.analysis_service.get_wprime_balance", return_value=self._read()):
            response = client.get("/activities/5/wprime")
        assert response.status_code == 200
        assert response.json()["cp_w"] == 250.0

    def test_another_users_activity_is_not_found(self):
        client = TestClient(_build_app(MagicMock()))
        with patch(
            "modules.analysis.activity_router.analysis_service.get_wprime_balance",
            side_effect=core_exceptions.NotFoundError("Activity 5 not found"),
        ):
            response = client.get("/activities/5/wprime")
        assert response.status_code == 404


class TestIntervalsRoute:
    def _read(self) -> analysis_schema.ActivityIntervalsRead:
        return analysis_schema.ActivityIntervalsRead(
            shape="intervals",
            threshold_w=350.0,
            base_power_w=280.0,
            rules=analysis_schema.IntervalRulesRead(
                open_w=350.0,
                close_w=315.0,
                min_segment_s=20.0,
                short_segment_s=60.0,
                short_base=1.3,
                strong_base=1.22,
                recovery_w=280.0,
                effort_if=1.05,
                climb_grade_pct=3.5,
                climb_if=1.25,
            ),
            segments=[
                analysis_schema.IntervalSegmentRead(
                    kind="interval",
                    start_s=300.0,
                    duration_s=180.0,
                    distance_m=700.0,
                    avg_power_w=410.0,
                    intensity_factor=1.17,
                    contrast=1.46,
                    avg_grade_pct=0.4,
                    avg_speed_ms=3.9,
                )
            ],
        )

    def test_the_owner_reads_the_breakdown(self):
        client = TestClient(_build_app(MagicMock()))
        with patch(
            "modules.analysis.activity_router.analysis_service.get_activity_intervals", return_value=self._read()
        ):
            response = client.get("/activities/5/intervals")
        assert response.status_code == 200
        body = response.json()
        assert body["shape"] == "intervals"
        assert body["rules"]["open_w"] == 350.0
        assert body["rules"]["effort_if"] == 1.05
        assert body["segments"][0]["kind"] == "interval"
        assert body["segments"][0]["contrast"] == 1.46

    def test_another_users_activity_is_not_found(self):
        client = TestClient(_build_app(MagicMock()))
        with patch(
            "modules.analysis.activity_router.analysis_service.get_activity_intervals",
            side_effect=core_exceptions.NotFoundError("Activity 5 not found"),
        ):
            response = client.get("/activities/5/intervals")
        assert response.status_code == 404


class TestBestTimesRoute:
    def _read(self) -> analysis_schema.BestTimesRead:
        return analysis_schema.BestTimesRead(
            sport="run",
            months=365,
            records=[
                analysis_schema.BestTimeRead(
                    distance_m=5000,
                    time_s=1210.0,
                    avg_power_w=392.0,
                    activity_id=21,
                    start_time="2026-09-20T08:00:00+00:00",
                )
            ],
        )

    def test_the_owner_reads_the_table(self):
        client = TestClient(_build_app(MagicMock()))
        with patch("modules.analysis.router.analysis_service.get_best_times", return_value=self._read()):
            response = client.get("/analysis/best-times?months=365")
        assert response.status_code == 200
        body = response.json()
        assert body["records"][0]["distance_m"] == 5000
        assert body["records"][0]["activity_id"] == 21

    def test_the_window_reaches_the_service(self):
        client = TestClient(_build_app(MagicMock()))
        with patch("modules.analysis.router.analysis_service.get_best_times", return_value=self._read()) as read:
            response = client.get("/analysis/best-times?sport=run&months=90")
        assert response.status_code == 200
        assert read.call_args.kwargs["months"] == 90

    def test_a_non_positive_window_is_rejected(self):
        client = TestClient(_build_app(MagicMock()))
        response = client.get("/analysis/best-times?months=0")
        assert response.status_code == 422
