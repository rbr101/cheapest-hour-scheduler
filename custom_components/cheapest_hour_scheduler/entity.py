"""Shared entity base."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import CONF_DEVICE_ID, CONF_ENTITY_ID, DOMAIN, SIGNAL_UPDATE
from .scheduler import CheapestHourScheduler


class SchedulerEntity(Entity):
    """Base class: one device per config entry, updates via dispatcher."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, scheduler: CheapestHourScheduler, key: str, unique_suffix: str | None = None
    ) -> None:
        self.scheduler = scheduler
        entry = scheduler.entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix or key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="rbr101",
            model="Cheapest Hour Scheduler",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(self.scheduler.entry.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


def record_device_identifier(entry_id: str, dev_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_{dev_id}")


class RecordEntity(SchedulerEntity):
    """Entity of one device record, on its own device under the scheduler."""

    def __init__(
        self, scheduler: CheapestHourScheduler, device: dict, key: str, unique_suffix: str
    ) -> None:
        super().__init__(scheduler, key, unique_suffix)
        entry = scheduler.entry
        self._dev_id = device[CONF_DEVICE_ID]
        self._record = device
        self._attr_device_info = DeviceInfo(
            identifiers={record_device_identifier(entry.entry_id, self._dev_id)},
            name=scheduler.name_of(device),
            manufacturer="rbr101",
            model=f"Cheapest Hour Scheduler record ({device.get(CONF_ENTITY_ID) or device.get('action', '')})",
            entry_type=DeviceEntryType.SERVICE,
            via_device=(DOMAIN, entry.entry_id),
        )
