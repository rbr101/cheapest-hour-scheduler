"""Integration tests (needs pytest-homeassistant-custom-component)."""

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

DOMAIN = "cheapest_hour_scheduler"


def _prices(start, values):
    return [
        {"time": (start + timedelta(minutes=15 * i)).isoformat(), "price": v}
        for i, v in enumerate(values)
    ]


async def test_schedules_devices(hass):
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    # Expensive now, cheap from +2h for 1h, expensive after.
    values = [0.6] * 8 + [0.3] * 4 + [0.6] * 20
    hass.states.async_set(
        "sensor.price", "0.6", {"prices_today": _prices(slot0, values), "prices_tomorrow": []}
    )
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"charger": {}}}
    )
    assert await async_setup_component(
        hass,
        "input_number",
        {"input_number": {"dishwasher": {"min": 0, "max": 86400, "step": 60}}},
    )
    await hass.async_block_till_done()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data={"price_sensor": "sensor.price", "block_hours": 1},
        options={
            "devices": [
                {"id": "a", "entity_id": "input_boolean.charger", "on_value": "on", "off_value": "off"},
                {"id": "b", "entity_id": "input_number.dishwasher", "on_value": "auto"},
            ]
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    start = hass.states.get("sensor.test_block_start")
    assert dt_util.parse_datetime(start.state) == slot0 + timedelta(hours=2)
    assert float(hass.states.get("sensor.test_block_average_price").state) == pytest.approx(0.3)
    assert hass.states.get("binary_sensor.test_cheapest_block_active").state == "off"

    # Dishwasher got seconds until start (~2h, rounded to minutes)
    secs = float(hass.states.get("input_number.dishwasher").state)
    expected = (slot0 + timedelta(hours=2) - dt_util.utcnow()).total_seconds()
    assert abs(secs - expected) <= 120
    assert hass.states.get("input_boolean.charger").state == "off"

    # Jump into the block
    async_fire_time_changed(hass, slot0 + timedelta(hours=2, minutes=1))
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.charger").state == "on"
    assert hass.states.get("binary_sensor.test_cheapest_block_active").state == "on"

    # After the block
    async_fire_time_changed(hass, slot0 + timedelta(hours=3, minutes=1))
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.charger").state == "off"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_config_flow(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    hass.states.async_set("sensor.zonneplan_current_tariff_rounded", "0.4")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Vaatwasser", "price_sensor": "sensor.zonneplan_current_tariff_rounded", "block_hours": 2},
    )
    assert result["type"] == "create_entry"
    entry = result["result"]
    await hass.async_block_till_done()

    # Add a device through the options flow
    hass.states.async_set("number.keuken_dishwasher_start_in_relative", "0")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_device"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"entity_id": "number.keuken_dishwasher_start_in_relative", "on_value": "auto", "start_offset_minutes": 0},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    devices = entry.options["devices"]
    assert devices[0]["entity_id"] == "number.keuken_dishwasher_start_in_relative"
    assert devices[0]["on_value"] == "auto"
    assert entry.options["block_hours"] == 2

    # Invalid number value is rejected
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_device"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"entity_id": "number.keuken_dishwasher_start_in_relative", "on_value": "soon", "start_offset_minutes": 0},
    )
    assert result["errors"] == {"on_value": "not_a_number"}
