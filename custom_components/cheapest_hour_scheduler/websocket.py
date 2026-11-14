"""Websocket API used by the Lovelace card."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import CONF_DEVICE_ID, CONF_ENTITY_ID, CONF_READY_BY, DOMAIN

WS_TYPE = f"{DOMAIN}/plan"


def _iso(value) -> str | None:
    return dt_util.as_local(value).isoformat() if value else None


def serialize(hass: HomeAssistant, scheduler) -> dict[str, Any]:
    """Everything the card needs for one config entry."""
    now = dt_util.utcnow()
    devices = []
    for device in scheduler.devices:
        dev_id = device[CONF_DEVICE_ID]
        plan = scheduler.device_plans.get(dev_id)
        item: dict[str, Any] = {
            "id": dev_id,
            "entity_id": device.get(CONF_ENTITY_ID) or device.get("action"),
            "name": scheduler.name_of(device),
            "enabled": scheduler.is_record_enabled(dev_id),
            "ready_by": device.get(CONF_READY_BY),
            "waiting": scheduler.waiting.get(dev_id),
            "plan": None,
        }
        if plan:
            item["plan"] = {
                "start": _iso(plan.start),
                "end": _iso(plan.run_end),
                "switch_off": _iso(plan.end),
                "deadline": _iso(plan.deadline),
                "price": plan.price,
                "started": plan.started,
                "stopped": plan.stopped,
                "running": plan.locked(now),
                "value": plan.armed_value or device.get("on_value"),
            }
        devices.append(item)

    block = scheduler.block
    return {
        "entry_id": scheduler.entry.entry_id,
        "title": scheduler.entry.title,
        "enabled": scheduler.enabled,
        "now": _iso(now),
        "price_source": scheduler.price_source,
        "price_sensor": scheduler.price_sensor,
        "unit": _price_unit(hass, scheduler.price_sensor),
        "block": (
            {"start": _iso(block.start), "end": _iso(block.end), "price": block.average_price}
            if block
            else None
        ),
        "prices": [
            {"start": _iso(s.start), "end": _iso(s.end), "price": s.price}
            for s in scheduler.slots
        ],
        "devices": devices,
        "error": scheduler.last_error,
    }


def _price_unit(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    return (state and state.attributes.get("unit_of_measurement")) or "€/kWh"


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_plan)


@websocket_api.websocket_command(
    {vol.Required("type"): WS_TYPE, vol.Optional("entity_id"): str}
)
@callback
def ws_plan(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Return plan + prices for the entry owning `entity_id`, or for all entries."""
    schedulers = hass.data.get(DOMAIN, {})
    entry_id = None
    if msg.get("entity_id"):
        reg_entry = er.async_get(hass).async_get(msg["entity_id"])
        if reg_entry is None or reg_entry.config_entry_id not in schedulers:
            connection.send_error(msg["id"], "not_found", f"{msg['entity_id']} is not a Cheapest Hour Scheduler entity")
            return
        entry_id = reg_entry.config_entry_id

    result = [
        serialize(hass, sched)
        for eid, sched in schedulers.items()
        if entry_id is None or eid == entry_id
    ]
    connection.send_result(msg["id"], {"entries": result})
