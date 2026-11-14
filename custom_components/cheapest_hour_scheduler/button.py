"""Button to force a recalculation."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .const import CONF_DEVICE_ID, CONF_ENTITY_ID, CONF_OFF_VALUE
from .entity import RecordEntity, SchedulerEntity
from .scheduler import CheapestHourScheduler


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    scheduler: CheapestHourScheduler = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ReplanButton(scheduler, "replan")])
    for device in scheduler.devices:
        entities: list[ButtonEntity] = [RecordTestButton(scheduler, device, "start")]
        if str(device.get(CONF_OFF_VALUE) or "").strip() and device.get(CONF_ENTITY_ID):
            entities.append(RecordTestButton(scheduler, device, "stop"))
        async_add_entities(
            entities, config_subentry_id=scheduler.subentry_of.get(device[CONF_DEVICE_ID])
        )


class ReplanButton(SchedulerEntity, ButtonEntity):
    _attr_icon = "mdi:refresh"

    async def async_press(self) -> None:
        await self.scheduler.async_plan(force=True)


class RecordTestButton(RecordEntity, ButtonEntity):
    """Run the start (or stop) action of a record right now, to test the device."""

    def __init__(self, scheduler: CheapestHourScheduler, device: dict, action: str) -> None:
        super().__init__(
            scheduler, device, f"test_{action}", f"device_{device[CONF_DEVICE_ID]}_test_{action}"
        )
        self._action = action
        self._attr_icon = "mdi:play-circle" if action == "start" else "mdi:stop-circle"

    @property
    def extra_state_attributes(self) -> dict:
        return {"value": self.scheduler.test_value(self._dev_id, self._action)}

    async def async_press(self) -> None:
        await self.scheduler.async_test_record(self._dev_id, self._action)
