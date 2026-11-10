"""Master switch to enable/disable scheduling."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SchedulerEntity
from .scheduler import CheapestHourScheduler


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    scheduler: CheapestHourScheduler = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EnabledSwitch(scheduler, "enabled")])


class EnabledSwitch(SchedulerEntity, SwitchEntity):
    _attr_icon = "mdi:calendar-clock"

    @property
    def is_on(self) -> bool:
        return self.scheduler.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.scheduler.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.scheduler.async_set_enabled(False)
