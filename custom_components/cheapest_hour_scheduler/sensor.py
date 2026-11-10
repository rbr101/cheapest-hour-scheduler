"""Sensors for the planned cheapest block."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_ENTITY_ID, CONF_ON_VALUE, DOMAIN
from .entity import SchedulerEntity
from .scheduler import CheapestHourScheduler


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    scheduler: CheapestHourScheduler = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BlockStartSensor(scheduler, "block_start"),
            BlockEndSensor(scheduler, "block_end"),
            BlockPriceSensor(scheduler, "block_average_price"),
        ]
    )


class BlockStartSensor(SchedulerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-start"

    @property
    def native_value(self) -> datetime | None:
        b = self.scheduler.block
        return b.start if b else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.scheduler
        local = dt_util.as_local
        return {
            "block_hours": s.block_hours,
            "price_sensor": s.price_sensor,
            "enabled": s.enabled,
            "last_planned": s.last_planned.isoformat() if s.last_planned else None,
            "last_error": s.last_error,
            "planned_actions": [
                {
                    "entity_id": p.device[CONF_ENTITY_ID],
                    "value": p.armed_value or p.device.get(CONF_ON_VALUE),
                    "start": local(p.start).isoformat(),
                    "end": local(p.end).isoformat() if p.end else None,
                    "started": p.started,
                    "stopped": p.stopped,
                }
                for p in s.device_plans.values()
            ],
        }


class BlockEndSensor(SchedulerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-end"

    @property
    def native_value(self) -> datetime | None:
        b = self.scheduler.block
        return b.end if b else None


class BlockPriceSensor(SchedulerEntity, SensorEntity):
    _attr_icon = "mdi:cash-clock"
    _attr_suggested_display_precision = 3

    @property
    def native_unit_of_measurement(self) -> str | None:
        state = self.hass.states.get(self.scheduler.price_sensor) if self.hass else None
        return (state and state.attributes.get("unit_of_measurement")) or "€/kWh"

    @property
    def native_value(self) -> float | None:
        b = self.scheduler.block
        return round(b.average_price, 4) if b else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        b = self.scheduler.block
        if not b or not b.slots:
            return {}
        return {
            "slots": [
                {"start": dt_util.as_local(sl.start).isoformat(), "price": sl.price}
                for sl in b.slots
            ],
            "min_price": min(sl.price for sl in b.slots),
            "max_price": max(sl.price for sl in b.slots),
        }
