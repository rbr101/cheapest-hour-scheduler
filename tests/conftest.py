"""Planner tests run anywhere; HA tests need pytest-homeassistant-custom-component."""

import importlib.util

import pytest

HAS_HA = importlib.util.find_spec("pytest_homeassistant_custom_component") is not None

collect_ignore = [] if HAS_HA else ["test_init.py"]

if HAS_HA:

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        yield
