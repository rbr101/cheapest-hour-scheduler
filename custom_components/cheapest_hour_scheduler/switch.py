"""Master switch to enable/disable scheduling."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .const import CONF_DEVICE_ID
from .entity import RecordEntity, SchedulerEntity
from .scheduler import CheapestHourScheduler


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    scheduler: CheapestHourScheduler = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [EnabledSwitch(scheduler, "enabled")]
        + [RecordEnabledSwitch(scheduler, device) for device in scheduler.devices]
    )


class EnabledSwitch(SchedulerEntity, SwitchEntity):
    _attr_icon = "mdi:calendar-clock"

    @property
    def is_on(self) -> bool:
        return self.scheduler.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.scheduler.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.scheduler.async_set_enabled(False)


class RecordEnabledSwitch(RecordEntity, SwitchEntity):
    """Turn a single device record on or off."""

    _attr_icon = "mdi:calendar-check"

    def __init__(self, scheduler: CheapestHourScheduler, device: dict[str, Any]) -> None:
        super().__init__(scheduler, device, "record_enabled", f"device_{device[CONF_DEVICE_ID]}_enabled")

    @property
    def is_on(self) -> bool:
        return self.scheduler.is_record_enabled(self._dev_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.scheduler.async_set_record_enabled(self._dev_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.scheduler.async_set_record_enabled(self._dev_id, False)
