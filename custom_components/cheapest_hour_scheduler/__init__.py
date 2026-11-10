"""Cheapest Hour Scheduler integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .scheduler import CheapestHourScheduler


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    scheduler = CheapestHourScheduler(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = scheduler

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await scheduler.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    scheduler: CheapestHourScheduler = hass.data[DOMAIN][entry.entry_id]
    await scheduler.async_stop()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
