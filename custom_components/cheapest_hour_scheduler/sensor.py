"""Sensors for the planned cheapest block."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_DEVICE_ID, CONF_ENTITY_ID, CONF_ON_VALUE, CONF_READY_BY, DOMAIN
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
        + [DeviceStartSensor(scheduler, device) for device in scheduler.devices]
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
        return {
            "block_hours": s.block_hours,
            "price_sensor": s.price_sensor,
            "enabled": s.enabled,
            "last_planned": s.last_planned.isoformat() if s.last_planned else None,
            "last_error": s.last_error,
            "price_source": s.price_source,
            "prices_known_until": (
                dt_util.as_local(s.prices_known_until).isoformat() if s.prices_known_until else None
            ),
            "planned_actions": [_plan_attrs(p) for p in s.device_plans.values()],
            "waiting": {
                d[CONF_ENTITY_ID]: s.waiting[d[CONF_DEVICE_ID]]
                for d in s.devices
                if d[CONF_DEVICE_ID] in s.waiting
            },
        }


def _plan_attrs(p) -> dict[str, Any]:
    local = dt_util.as_local
    return {
        "entity_id": p.device[CONF_ENTITY_ID],
        "value": p.armed_value or p.device.get(CONF_ON_VALUE),
        "start": local(p.start).isoformat(),
        "end": local(p.end).isoformat() if p.end else None,
        "finished_at": local(p.run_end).isoformat(),
        "ready_by": local(p.deadline).isoformat() if p.deadline else None,
        "average_price": p.price,
        "started": p.started,
        "stopped": p.stopped,
    }


class DeviceStartSensor(SchedulerEntity, SensorEntity):
    """Planned start of one device record."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-play-outline"

    def __init__(self, scheduler: CheapestHourScheduler, device: dict[str, Any]) -> None:
        super().__init__(scheduler, "device_start", f"device_{device[CONF_DEVICE_ID]}")
        self._dev_id = device[CONF_DEVICE_ID]
        self._entity_id = device[CONF_ENTITY_ID]
        self._ready_by = device.get(CONF_READY_BY)
        self._attr_translation_placeholders = {"entity": self._entity_id.split(".", 1)[1]}

    @property
    def native_value(self) -> datetime | None:
        plan = self.scheduler.device_plans.get(self._dev_id)
        return plan.start if plan else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self.scheduler.device_plans.get(self._dev_id)
        attrs: dict[str, Any] = {"entity_id": self._entity_id, "ready_by_time": self._ready_by}
        if plan:
            attrs.update(_plan_attrs(plan))
        if self._dev_id in self.scheduler.waiting:
            attrs["waiting"] = self.scheduler.waiting[self._dev_id]
        return attrs


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
