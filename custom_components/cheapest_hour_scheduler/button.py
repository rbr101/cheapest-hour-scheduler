"""Button to force a recalculation."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    async_add_entities([ReplanButton(scheduler, "replan")])


class ReplanButton(SchedulerEntity, ButtonEntity):
    _attr_icon = "mdi:refresh"

    async def async_press(self) -> None:
        await self.scheduler.async_plan(force=True)
