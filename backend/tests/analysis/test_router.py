"""Tests for the analysis routers: status codes and scope wiring."""

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
