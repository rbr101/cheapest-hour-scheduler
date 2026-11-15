"""Integration tests (needs pytest-homeassistant-custom-component)."""

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

DOMAIN = "cheapest_hour_scheduler"


def _prices(start, values):
    return [
        {"time": (start + timedelta(minutes=15 * i)).isoformat(), "price": v}
        for i, v in enumerate(values)
    ]


def _mock_entry(*, options=None, **kwargs):
    """Config entry with the given device records as subentries (format 1.2)."""
    options = dict(options or {})
    devices = options.pop("devices", [])
    return MockConfigEntry(
        minor_version=2,
        options=options,
        subentries_data=[
            ConfigSubentryData(
                data=d, subentry_type="record", title=d.get("name") or d["id"], unique_id=d["id"]
            )
            for d in devices
        ],
        **kwargs,
    )


async def _subentry_flow(hass, entry, user_input, subentry_id=None):
    """Run the add (or, with subentry_id, edit) form of a device record."""
    context = {"source": "reconfigure", "subentry_id": subentry_id} if subentry_id else {"source": "user"}
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "record"), context=context)
    assert result["type"] == "form"
    return await hass.config_entries.subentries.async_configure(result["flow_id"], user_input)


async def test_schedules_devices(hass, freezer):
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

    entry = _mock_entry(
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
    freezer.move_to(slot0 + timedelta(hours=2, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.charger").state == "on"
    assert hass.states.get("binary_sensor.test_cheapest_block_active").state == "on"

    # After the block
    freezer.move_to(slot0 + timedelta(hours=3, minutes=1))
    async_fire_time_changed(hass)
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

    # Options only hold the global settings now
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form" and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"price_sensor": "sensor.zonneplan_current_tariff_rounded", "block_hours": 3}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["block_hours"] == 3

    # Add a device record from the integration page ("Add device" -> subentry)
    hass.states.async_set("number.keuken_dishwasher_start_in_relative", "0")
    result = await _subentry_flow(
        hass, entry,
        {"name": "Vaatwasser", "entity_id": "number.keuken_dishwasher_start_in_relative",
         "on_value": "auto", "start_offset_minutes": 0},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    (subentry,) = entry.subentries.values()
    assert subentry.title == "Vaatwasser"
    assert subentry.data["entity_id"] == "number.keuken_dishwasher_start_in_relative"
    assert subentry.data["on_value"] == "auto"
    assert hass.states.get("sensor.vaatwasser_start") is not None

    # Invalid number value is rejected
    result = await _subentry_flow(
        hass, entry,
        {"entity_id": "number.keuken_dishwasher_start_in_relative", "on_value": "soon", "start_offset_minutes": 0},
    )
    assert result["errors"] == {"on_value": "not_a_number"}

    # Edit it with the settings icon (reconfigure): change the run time
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "record"), context={"source": "reconfigure", "subentry_id": subentry.subentry_id}
    )
    # All fields on one form, filled with the current values
    schema = result["data_schema"].schema
    for field in ("name", "entity_id", "on_value", "off_value", "action", "action_data",
                  "ready_by", "duration_hours", "start_offset_minutes"):
        assert field in schema
    assert next(k for k in schema if k == "on_value").description["suggested_value"] == "auto"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Vaatwasser", "entity_id": "number.keuken_dishwasher_start_in_relative",
         "on_value": "auto", "duration_hours": 2, "ready_by": "07:00:00", "start_offset_minutes": 0},
    )
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    subentry = entry.subentries[subentry.subentry_id]
    assert subentry.data["duration_hours"] == 2
    assert subentry.data["ready_by"] == "07:00:00"
    assert subentry.data["id"] == subentry.unique_id  # record id is kept


async def test_ready_by_gets_own_window(hass):
    tz = dt_util.get_default_time_zone()
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    # 48 h of data: cheapest is at +20h, but the device must be ready at +6h,
    # within that the cheapest hour is +3h..+4h.
    values = [0.6] * 192
    for i in range(12, 16):
        values[i] = 0.4
    for i in range(80, 84):
        values[i] = 0.1
    hass.states.async_set(
        "sensor.price", "0.6", {"prices_today": _prices(slot0, values), "prices_tomorrow": []}
    )
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"boiler": {}, "battery": {}}}
    )
    await hass.async_block_till_done()
    ready = dt_util.as_local(slot0 + timedelta(hours=6)).strftime("%H:%M:00")

    entry = _mock_entry(
        domain=DOMAIN,
        title="Test",
        data={"price_sensor": "sensor.price", "block_hours": 1},
        options={
            "devices": [
                {"id": "a", "entity_id": "input_boolean.boiler", "on_value": "on",
                 "off_value": "off", "duration_hours": 1, "ready_by": ready},
                {"id": "b", "entity_id": "input_boolean.battery", "on_value": "on", "off_value": "off"},
            ]
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    boiler = hass.states.get("sensor.boiler_start")
    assert dt_util.parse_datetime(boiler.state) == slot0 + timedelta(hours=3)
    battery = hass.states.get("sensor.battery_start")
    assert dt_util.parse_datetime(battery.state) == slot0 + timedelta(hours=20)
    assert tz is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_uses_stored_prices_when_sensor_unavailable(hass, hass_storage, freezer):
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    values = [0.6] * 8 + [0.2] * 4 + [0.6] * 20
    hass.states.async_set(
        "sensor.price", "0.6", {"prices_today": _prices(slot0, values), "prices_tomorrow": []}
    )
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"charger": {}}})
    entry = _mock_entry(
        domain=DOMAIN,
        title="Test",
        data={"price_sensor": "sensor.price", "block_hours": 1},
        options={"devices": [{"id": "a", "entity_id": "input_boolean.charger", "on_value": "on", "off_value": "off"}]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    expected = slot0 + timedelta(hours=2)
    assert dt_util.parse_datetime(hass.states.get("sensor.test_block_start").state) == expected

    # Prices were persisted
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]
    assert len(stored["prices"]) == 32

    # Sensor goes away and HA (the integration) restarts: plan is rebuilt from storage
    hass.states.async_set("sensor.price", "unavailable", {})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.test_block_start")
    assert dt_util.parse_datetime(state.state) == expected
    assert state.attributes["price_source"] == "cache"
    assert float(hass.states.get("sensor.test_block_average_price").state) == pytest.approx(0.2)

    # And the device still switches
    freezer.move_to(expected + timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.charger").state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_websocket_plan(hass, hass_ws_client):
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    values = [0.6] * 8 + [0.2] * 4 + [0.6] * 20
    hass.states.async_set(
        "sensor.price", "0.6", {"prices_today": _prices(slot0, values), "prices_tomorrow": []}
    )
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"charger": {}}})
    entry = _mock_entry(
        domain=DOMAIN,
        title="Test",
        data={"price_sensor": "sensor.price", "block_hours": 1},
        options={"devices": [{"id": "a", "entity_id": "input_boolean.charger", "on_value": "on", "off_value": "off"}]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/plan", "entity_id": "sensor.test_block_start"})
    msg = await client.receive_json()
    assert msg["success"], msg
    data = msg["result"]["entries"][0]
    assert data["title"] == "Test"
    assert len(data["prices"]) == 32
    assert data["devices"][0]["entity_id"] == "input_boolean.charger"
    assert data["devices"][0]["record"]["on_value"] == "on"
    assert data["devices"][0]["device_id"]
    assert data["devices"][0]["last_action"] is None
    plan = data["devices"][0]["plan"]
    assert dt_util.parse_datetime(plan["start"]) == slot0 + timedelta(hours=2)

    await client.send_json({"id": 2, "type": f"{DOMAIN}/plan", "entity_id": "sensor.price"})
    msg = await client.receive_json()
    assert not msg["success"]


async def test_record_description_device_and_switch(hass, freezer):
    from homeassistant.helpers import device_registry as dr

    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    values = [0.6] * 8 + [0.2] * 4 + [0.6] * 20
    hass.states.async_set(
        "sensor.price", "0.6", {"prices_today": _prices(slot0, values), "prices_tomorrow": []}
    )
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"charger": {}}})
    entry = _mock_entry(
        domain=DOMAIN,
        title="Test",
        data={"price_sensor": "sensor.price", "block_hours": 1},
        options={"devices": [{"id": "a", "name": "Thuisaccu", "entity_id": "input_boolean.charger",
                              "on_value": "on", "off_value": "off"}]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Own device under the scheduler device, named after the description
    registry = dr.async_get(hass)
    record_dev = registry.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_a")})
    parent = registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert record_dev.name == "Thuisaccu"
    assert record_dev.via_device_id == parent.id

    start = hass.states.get("sensor.thuisaccu_start")
    assert start.attributes["name"] == "Thuisaccu"
    assert hass.states.get("sensor.thuisaccu_end") is not None
    assert hass.states.get("switch.thuisaccu_enabled").state == "on"
    assert hass.states.get("binary_sensor.thuisaccu_active").state == "off"

    # Running...
    freezer.move_to(slot0 + timedelta(hours=2, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.charger").state == "on"
    assert hass.states.get("binary_sensor.thuisaccu_active").state == "on"

    # ...switch the record off: device goes off and no plan remains
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.thuisaccu_enabled"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.charger").state == "off"
    assert hass.states.get("sensor.thuisaccu_start").state == "unknown"
    assert hass.states.get("sensor.thuisaccu_start").attributes["waiting"] == "disabled"

    # On again during the block: starts again, a few minutes from now
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.thuisaccu_enabled"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.charger").state == "off"
    freezer.move_to(slot0 + timedelta(hours=2, minutes=8))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.charger").state == "on"

    # Removing the record removes its device
    (subentry_id,) = entry.subentries
    hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()
    assert registry.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_a")}) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_record_test_buttons(hass):
    from homeassistant.exceptions import HomeAssistantError

    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    values = [0.6] * 8 + [0.2] * 4 + [0.6] * 20
    hass.states.async_set(
        "sensor.price", "0.6", {"prices_today": _prices(slot0, values), "prices_tomorrow": []}
    )
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"charger": {}}})
    assert await async_setup_component(
        hass, "input_number", {"input_number": {"dishwasher": {"min": 0, "max": 86400, "step": 60}}}
    )
    entry = _mock_entry(
        domain=DOMAIN,
        title="Test",
        data={"price_sensor": "sensor.price", "block_hours": 1},
        options={"devices": [
            {"id": "a", "name": "Thuisaccu", "entity_id": "input_boolean.charger", "on_value": "on", "off_value": "off"},
            {"id": "b", "name": "Vaatwasser", "entity_id": "input_number.dishwasher", "on_value": "auto"},
            {"id": "c", "name": "Kapot", "entity_id": "switch.does_not_exist", "on_value": "on"},
        ]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sched = hass.data[DOMAIN][entry.entry_id]

    # The recalculate button works
    await hass.services.async_call("button", "press", {"entity_id": "button.test_recalculate"}, blocking=True)
    assert sched.device_plans["a"].start == slot0 + timedelta(hours=2)

    # Start/stop buttons for a switch record; the plan is not touched
    assert hass.states.get("button.thuisaccu_test_start") is not None
    await hass.services.async_call("button", "press", {"entity_id": "button.thuisaccu_test_start"}, blocking=True)
    assert hass.states.get("input_boolean.charger").state == "on"
    assert sched.device_plans["a"].started is False
    await hass.services.async_call("button", "press", {"entity_id": "button.thuisaccu_test_stop"}, blocking=True)
    assert hass.states.get("input_boolean.charger").state == "off"

    # 'auto' record: no stop button, test sends seconds until the planned start
    assert hass.states.get("button.vaatwasser_test_stop") is None
    hass.states.async_set("input_number.dishwasher", "0")
    await hass.services.async_call("button", "press", {"entity_id": "button.vaatwasser_test_start"}, blocking=True)
    secs = float(hass.states.get("input_number.dishwasher").state)
    expected = (slot0 + timedelta(hours=2) - dt_util.utcnow()).total_seconds()
    assert abs(secs - expected) <= 120

    # A failing device shows an error to the user
    with pytest.raises(HomeAssistantError, match="does not exist"):
        await hass.services.async_call("button", "press", {"entity_id": "button.kapot_test_start"}, blocking=True)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_record_with_action(hass, freezer):
    from homeassistant.core import ServiceCall

    calls: list[ServiceCall] = []

    async def _record(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("home_connect", "start_selected_program", _record)
    hass.services.async_register("script", "boiler_boost", _record)

    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    values = [0.6] * 8 + [0.2] * 4 + [0.6] * 20
    hass.states.async_set(
        "sensor.price", "0.6", {"prices_today": _prices(slot0, values), "prices_tomorrow": []}
    )
    entry = _mock_entry(
        domain=DOMAIN,
        title="Test",
        data={"price_sensor": "sensor.price", "block_hours": 1},
        options={"devices": [
            {"id": "dw", "name": "Vaatwasser", "on_value": "on",
             "action": "home_connect.start_selected_program",
             "action_data": "device_id: 9c11835a9a1add7e34dbdcf6956e70b8\n"
                            "b_s_h_common_option_start_in_relative: {seconds_until_start}"},
            {"id": "bb", "name": "Boiler boost", "on_value": "on", "action": "script.boiler_boost"},
        ]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Relative action: called right away with the seconds until the block start
    assert len(calls) == 1
    call = calls[0]
    assert (call.domain, call.service) == ("home_connect", "start_selected_program")
    assert call.data["device_id"] == "9c11835a9a1add7e34dbdcf6956e70b8"
    expected = (slot0 + timedelta(hours=2) - dt_util.utcnow()).total_seconds()
    assert abs(call.data["b_s_h_common_option_start_in_relative"] - expected) <= 120
    assert call.data["b_s_h_common_option_start_in_relative"] % 60 == 0

    # Plain action without data: called at the block start, without data
    freezer.move_to(slot0 + timedelta(hours=2, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 2
    assert (calls[1].domain, calls[1].service) == ("script", "boiler_boost")
    assert dict(calls[1].data) == {}

    # Test button runs the action; no stop button without an entity
    assert hass.states.get("button.vaatwasser_test_stop") is None
    await hass.services.async_call("button", "press", {"entity_id": "button.vaatwasser_test_start"}, blocking=True)
    assert len(calls) == 3
    assert calls[2].data["b_s_h_common_option_start_in_relative"] == 60  # no future plan -> 1 min
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_record_flow_action_and_example_data(hass):
    from homeassistant.helpers import service as service_helper

    hass.services.async_register("home_connect", "start_selected_program", lambda call: None)
    service_helper.async_set_service_schema(hass, "home_connect", "start_selected_program", {
        "fields": {
            "device_id": {"required": True, "selector": {"device": {}}},
            "affects_to": {"example": "selected_program", "selector": {"text": {}}},
            "dish_washer_options": {
                "collapsed": True,
                "fields": {
                    "b_s_h_common_option_start_in_relative": {"example": 3600, "selector": {"number": {}}},
                    "dishcare_dishwasher_option_extra_dry": {"example": False, "selector": {"boolean": {}}},
                },
            },
        }
    })
    entry = _mock_entry(domain=DOMAIN, title="Test", data={"price_sensor": "sensor.price", "block_hours": 1})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Validation
    result = await _subentry_flow(hass, entry, {"name": "Leeg", "start_offset_minutes": 0})
    assert result["errors"] == {"base": "entity_or_action"}
    result = await _subentry_flow(hass, entry, {"action": "home_connect.nope", "start_offset_minutes": 0})
    assert result["errors"] == {"action": "unknown_action"}
    result = await _subentry_flow(
        hass, entry, {"action": "home_connect.start_selected_program", "off_value": "off", "start_offset_minutes": 0}
    )
    assert result["errors"] == {"off_value": "off_needs_entity"}

    # Action with empty data: the same form comes back once with the example data
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "record"), context={"source": "user"})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Vaatwasser", "action": "home_connect.start_selected_program",
         "ready_by": "07:00:00", "duration_hours": 2, "start_offset_minutes": 0},
    )
    assert result["type"] == "form" and result["step_id"] == "user" and not result["errors"]
    schema = result["data_schema"].schema
    example = next(k for k in schema if k == "action_data").description["suggested_value"]
    assert "affects_to: selected_program" in example
    assert "b_s_h_common_option_start_in_relative: 3600" in example  # from a collapsed section
    assert "extra_dry" not in example  # false examples are skipped, like the frontend
    assert "# device_id:" in example  # required without example: hint
    # what was filled in is kept
    assert next(k for k in schema if k == "ready_by").description["suggested_value"] == "07:00:00"

    # Invalid YAML is rejected, valid data is saved
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Vaatwasser", "action": "home_connect.start_selected_program",
         "action_data": "- a\n- b", "start_offset_minutes": 0},
    )
    assert result["errors"] == {"action_data": "invalid_data"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Vaatwasser", "action": "home_connect.start_selected_program",
         "action_data": "device_id: abc\nb_s_h_common_option_start_in_relative: {seconds_until_start}",
         "ready_by": "07:00:00", "duration_hours": 2, "start_offset_minutes": 0},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    (subentry,) = entry.subentries.values()
    assert subentry.data["action"] == "home_connect.start_selected_program"
    assert subentry.data["ready_by"] == "07:00:00"
    assert "entity_id" not in subentry.data
    assert "{seconds_until_start}" in subentry.data["action_data"]

    # Editing shows the own data (no example over it)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "record"), context={"source": "reconfigure", "subentry_id": subentry.subentry_id}
    )
    key = next(k for k in result["data_schema"].schema if k == "action_data")
    assert key.description["suggested_value"].startswith("device_id: abc")

    # Data left empty on purpose: second submit saves without data
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "record"), context={"source": "user"})
    user_input = {"name": "Leeg", "action": "home_connect.start_selected_program", "start_offset_minutes": 0}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], user_input)
    assert result["type"] == "form"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], user_input)
    assert result["type"] == "create_entry"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_migrates_records_from_options(hass, freezer):
    """Records stored in options (before 0.7) become subentries, ids are kept."""
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    hass.states.async_set(
        "sensor.price", "0.6",
        {"prices_today": _prices(slot0, [0.6] * 8 + [0.2] * 4 + [0.6] * 20), "prices_tomorrow": []},
    )
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"charger": {}}})
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test", version=1, minor_version=1,
        data={"price_sensor": "sensor.price", "block_hours": 1},
        options={"block_hours": 1, "devices": [
            {"id": "a1b2c3d4", "name": "Thuisaccu", "entity_id": "input_boolean.charger",
             "on_value": "on", "off_value": "off"},
        ]},
    )
    entry.add_to_hass(hass)

    # As left behind by 0.6: device and entity exist, without a subentry
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    old_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{entry.entry_id}_a1b2c3d4")}, name="Thuisaccu"
    )
    er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, f"{entry.entry_id}_device_a1b2c3d4_enabled",
        config_entry=entry, device_id=old_device.id, suggested_object_id="thuisaccu_enabled",
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2
    assert "devices" not in entry.options
    (subentry,) = entry.subentries.values()
    assert subentry.subentry_type == "record"
    assert subentry.unique_id == "a1b2c3d4"
    assert subentry.title == "Thuisaccu"

    reg = er.async_get(hass).async_get("switch.thuisaccu_enabled")
    assert reg.unique_id == f"{entry.entry_id}_device_a1b2c3d4_enabled"
    assert reg.config_subentry_id == subentry.subentry_id

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_a1b2c3d4")})
    assert device.id == old_device.id  # same device, history kept
    assert device.config_entries_subentries[entry.entry_id] == {subentry.subentry_id}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_edit_ready_by_inside_cheapest_hours(hass, freezer):
    """Changing a record so its window has already begun must still start it."""
    from homeassistant.core import ServiceCall

    calls: list[ServiceCall] = []

    async def _record(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("home_connect", "start_selected_program", _record)

    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    # cheap from +1h to +3h
    values = [0.6] * 4 + [0.2] * 8 + [0.6] * 180
    hass.states.async_set(
        "sensor.price", "0.6", {"prices_today": _prices(slot0, values), "prices_tomorrow": []}
    )
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"boiler": {}, "pump": {}}})

    def at(hours):
        return dt_util.as_local(slot0 + timedelta(hours=hours)).strftime("%H:%M:00")

    boiler = {"id": "bo", "name": "Boiler", "entity_id": "input_boolean.boiler", "on_value": "on",
              "off_value": "off", "duration_hours": 1, "ready_by": at(20)}
    washer = {"id": "dw", "name": "Vaatwasser", "on_value": "on", "duration_hours": 1, "ready_by": at(20),
              "action": "home_connect.start_selected_program",
              "action_data": "device_id: abc\nb_s_h_common_option_start_in_relative: {seconds_until_start}"}
    entry = _mock_entry(
        domain=DOMAIN, title="Test", data={"price_sensor": "sensor.price", "block_hours": 1},
        options={"devices": [boiler, washer]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Planned at the start of the cheap hours; dishwasher got its delay
    assert dt_util.parse_datetime(hass.states.get("sensor.boiler_start").state) == slot0 + timedelta(hours=1)
    assert len(calls) == 1 and calls[0].data["b_s_h_common_option_start_in_relative"] > 0

    # 20 minutes into the cheap hours both are running
    freezer.move_to(slot0 + timedelta(hours=1, minutes=20))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.boiler").state == "on"

    # Now edit both: longer run time and another ready-by. The new windows fall in
    # the cheap hours and begin in the slot that is already running.
    by_id = {s.unique_id: s for s in entry.subentries.values()}
    hass.config_entries.async_update_subentry(
        entry, by_id["bo"], data={**boiler, "duration_hours": 1.5, "ready_by": at(3)}
    )
    await hass.async_block_till_done()
    hass.config_entries.async_update_subentry(
        entry, by_id["dw"], data={**washer, "duration_hours": 1.5, "ready_by": at(3)}
    )
    await hass.async_block_till_done()

    # The boiler was on already: it stays on and gets the new run time
    start = dt_util.parse_datetime(hass.states.get("sensor.boiler_start").state)
    end = dt_util.parse_datetime(hass.states.get("sensor.boiler_end").state)
    assert start == slot0 + timedelta(hours=1, minutes=25)  # 5 minutes from now, never in the past
    assert end == slot0 + timedelta(hours=2, minutes=55)  # new run time is used
    assert hass.states.get("input_boolean.boiler").state == "on"

    # The dishwasher is not planned in the past but a few minutes from now,
    # and the action is sent again with that delay
    soon = slot0 + timedelta(hours=1, minutes=25)
    assert dt_util.parse_datetime(hass.states.get("sensor.vaatwasser_start").state) == soon
    assert dt_util.parse_datetime(hass.states.get("sensor.vaatwasser_end").state) == soon + timedelta(hours=1.5)
    assert len(calls) == 2
    assert calls[1].data["b_s_h_common_option_start_in_relative"] == 300

    # A device added now also starts a few minutes from now, not in the past
    result = await _subentry_flow(
        hass, entry,
        {"name": "Pomp", "entity_id": "input_boolean.pump", "on_value": "on", "off_value": "off",
         "duration_hours": 1, "ready_by": at(3), "start_offset_minutes": 0},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert dt_util.parse_datetime(hass.states.get("sensor.pomp_start").state) == soon
    assert hass.states.get("input_boolean.pump").state == "off"
    freezer.move_to(soon + timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.pump").state == "on"
    assert dt_util.parse_datetime(hass.states.get("sensor.pomp_start").state) == soon  # not moved again

    # Replanning doesn't send it again; boiler goes off at the new end
    freezer.move_to(slot0 + timedelta(hours=2, minutes=57))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    # ...and the next run (tomorrow's ready-by) is not squeezed into today's cheap hours
    assert all(c.data["b_s_h_common_option_start_in_relative"] > 0 for c in calls[2:])
    assert dt_util.parse_datetime(hass.states.get("sensor.vaatwasser_start").state) >= slot0 + timedelta(hours=3)
    assert hass.states.get("input_boolean.boiler").state == "off"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_replans_window_that_began_without_a_start(hass, hass_storage, freezer):
    """A plan left in the past by an older version (never sent) is planned again from now."""
    from homeassistant.core import ServiceCall

    calls: list[ServiceCall] = []

    async def _record(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("home_connect", "start_selected_program", _record)
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"boiler": {}}})

    now = dt_util.utcnow().replace(second=0, microsecond=0)
    slot0 = now - timedelta(minutes=now.minute % 15)
    freezer.move_to(slot0 + timedelta(minutes=10))
    hass.states.async_set(
        "sensor.price", "0.2", {"prices_today": _prices(slot0, [0.2] * 20 + [0.6] * 100), "prices_tomorrow": []}
    )
    ready = dt_util.as_local(slot0 + timedelta(hours=5)).strftime("%H:%M:00")
    washer = {"id": "dw", "name": "Vaatwasser", "on_value": "on", "duration_hours": 4, "ready_by": ready,
              "action": "home_connect.start_selected_program",
              "action_data": "device_id: abc\nb_s_h_common_option_start_in_relative: {seconds_until_start}"}
    boiler = {"id": "bo", "name": "Boiler", "entity_id": "input_boolean.boiler", "on_value": "on",
              "off_value": "off", "duration_hours": 4, "ready_by": ready}
    entry = _mock_entry(
        domain=DOMAIN, title="Test", data={"price_sensor": "sensor.price", "block_hours": 1},
        options={"devices": [washer, boiler]},
    )
    entry.add_to_hass(hass)

    def old_plan(started):
        return {
            "start": slot0.isoformat(), "end": None, "run_end": (slot0 + timedelta(hours=4)).isoformat(),
            "price": 0.2, "deadline": (slot0 + timedelta(hours=5)).isoformat(),
            "started": started, "stopped": False, "armed_value": None,
        }

    boiler_plan = {**old_plan(True), "end": (slot0 + timedelta(hours=4)).isoformat()}
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1, "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {"enabled": True, "plans": {"dw": old_plan(True), "bo": boiler_plan}},
    }
    hass.states.async_set("input_boolean.boiler", "on")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Dishwasher: marked started but nothing was ever sent -> planned again from now
    soon = slot0 + timedelta(minutes=15)
    assert dt_util.parse_datetime(hass.states.get("sensor.vaatwasser_start").state) == soon
    assert len(calls) == 1 and calls[0].data["b_s_h_common_option_start_in_relative"] == 300
    sched = hass.data[DOMAIN][entry.entry_id]
    last = sched.last_actions["dw"]
    assert last["ok"] and last["what"] == "start" and "start_in_relative=300" in last["sent"]

    # Boiler: really on -> its running window is kept
    assert dt_util.parse_datetime(hass.states.get("sensor.boiler_start").state) == slot0

    # Saving the boiler in the edit form (other run time) replans it, even though the
    # old plan has no fingerprint yet; it stays on and gets the new end
    (sub,) = [s for s in entry.subentries.values() if s.unique_id == "bo"]
    result = await _subentry_flow(
        hass, entry,
        {"name": "Boiler", "entity_id": "input_boolean.boiler", "on_value": "on", "off_value": "off",
         "duration_hours": 2, "ready_by": ready, "start_offset_minutes": 0},
        subentry_id=sub.subentry_id,
    )
    assert result["type"] == "abort"
    await hass.async_block_till_done()
    end = dt_util.parse_datetime(hass.states.get("sensor.boiler_end").state)
    start = dt_util.parse_datetime(hass.states.get("sensor.boiler_start").state)
    assert end - start == timedelta(hours=2)
    assert hass.states.get("input_boolean.boiler").state == "on"

    # Recalculate keeps what is planned from now on (no jump back into the past)
    await hass.services.async_call("button", "press", {"entity_id": "button.test_recalculate"}, blocking=True)
    assert dt_util.parse_datetime(hass.states.get("sensor.vaatwasser_start").state) == soon
    assert len(calls) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_action_data_pasted_from_developer_tools():
    """The whole action YAML (action: / data:) may be pasted; only the data is used."""
    from custom_components.cheapest_hour_scheduler.scheduler import render_action_data

    pasted = (
        "action: home_connect.start_selected_program\n"
        "data:\n"
        "  device_id: 9c11835a9a1add7e34dbdcf6956e70b8\n"
        "  b_s_h_common_option_start_in_relative: {seconds_until_start}\n"
    )
    assert render_action_data(pasted, 300) == {
        "device_id": "9c11835a9a1add7e34dbdcf6956e70b8",
        "b_s_h_common_option_start_in_relative": 300,
    }
    assert render_action_data("action: light.turn_on\ntarget:\n  entity_id: light.x\ndata:\n  brightness: 5") == {
        "entity_id": "light.x", "brightness": 5,
    }
    # only the "data:" line left above the fields
    assert render_action_data("data:\n  device_id: abc\n  b_s_h_common_option_start_in_relative: 121") == {
        "device_id": "abc", "b_s_h_common_option_start_in_relative": 121,
    }
    # real fields next to a field called "data" are left alone
    assert render_action_data("message: hi\ndata:\n  tag: x") == {"message": "hi", "data": {"tag": "x"}}
    # a real field that happens to be called "data" is left alone
    assert render_action_data("data: hello") == {"data": "hello"}
