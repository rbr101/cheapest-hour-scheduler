"""Binary sensor: is the cheapest block running right now."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import SchedulerEntity
from .scheduler import CheapestHourScheduler


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    scheduler: CheapestHourScheduler = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BlockActiveSensor(scheduler, "block_active")])


class BlockActiveSensor(SchedulerEntity, BinarySensorEntity):
    _attr_icon = "mdi:lightning-bolt-circle"
    _unsubs: list

    async def async_added_to_hass(self) -> None:
        self._unsubs = []
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel)
        self._schedule()

    @callback
    def _cancel(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    @callback
    def _schedule(self) -> None:
        self._cancel()
        b = self.scheduler.block
        now = dt_util.utcnow()
        if b:
            for moment in (b.start, b.end):
                if moment > now:
                    self._unsubs.append(
                        async_track_point_in_time(self.hass, self._tick, moment)
                    )

    @callback
    def _tick(self, _now) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_update(self) -> None:
        self._schedule()
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        b = self.scheduler.block
        return bool(b and b.contains(dt_util.utcnow()))
