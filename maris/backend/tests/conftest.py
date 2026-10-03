"""Pytest configuration and test isolation fixtures for MARIS backend test suite."""

import pytest


@pytest.fixture(autouse=True)
def isolate_evaluator_tests_from_sar_fixture(request, monkeypatch):
    """Ensure unrelated evaluator acceptance and robustness tests run in pure baseline
    mode without dynamic SAR raster subscene coordinate overrides.
    """
    if request.module.__name__ in (
        "tests.test_evaluator_acceptance",
        "tests.test_evaluator_robustness",
    ):
        from app.services.real_experiment import evaluator_workflow

        monkeypatch.setattr(
            evaluator_workflow,
            "_get_observation_raster_metrics",
            lambda obs: None,
        )
