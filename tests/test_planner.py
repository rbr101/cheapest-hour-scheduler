"""Tests for the pure planning logic (no Home Assistant needed)."""

from datetime import datetime, timedelta
import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "planner", ROOT / "custom_components/cheapest_hour_scheduler/planner.py"
)
planner = importlib.util.module_from_spec(spec)
sys.modules["planner"] = planner
spec.loader.exec_module(planner)

ATTRS = yaml.safe_load((Path(__file__).parent / "zonneplan_attributes.yaml").read_text())
SLOTS = planner.parse_prices(ATTRS["prices_today"], ATTRS["prices_tomorrow"])


def t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_parse():
    assert len(SLOTS) == 192
    assert SLOTS[0].end - SLOTS[0].start == timedelta(minutes=15)


def test_three_hours_from_evening():
    block = planner.find_cheapest_block(SLOTS, 3, t("2026-11-10T17:21:00+01:00"))
    assert block.start == t("2026-11-11T02:15:00+01:00")
    assert block.end == t("2026-11-11T05:15:00+01:00")
    assert abs(block.average_price - 0.37) < 0.0005


def test_running_slot_allowed_but_not_past():
    block = planner.find_cheapest_block(SLOTS, 1, t("2026-11-11T03:50:00+01:00"))
    assert block.start >= t("2026-11-11T03:45:00+01:00")


def test_not_enough_data():
    assert planner.find_cheapest_block(SLOTS, 3, t("2026-11-11T22:00:00+01:00")) is None


def test_not_before():
    block = planner.find_cheapest_block(
        SLOTS, 2, t("2026-11-10T08:00:00+01:00"), not_before=t("2026-11-11T00:00:00+01:00")
    )
    assert block.start >= t("2026-11-11T00:00:00+01:00")
