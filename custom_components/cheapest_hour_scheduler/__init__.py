"""Cheapest Hour Scheduler integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import CONF_DEVICE_ID, DOMAIN, PLATFORMS
from .entity import record_device_identifier
from .scheduler import CheapestHourScheduler

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_FILE = Path(__file__).parent / "frontend" / "cheapest-hour-card.js"
CARD_URL = f"/{DOMAIN}/cheapest-hour-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the websocket API and the Lovelace card (once)."""
    websocket.async_register(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and load it in every dashboard, no manual resource needed."""
    if getattr(hass, "http", None) is None:  # e.g. in some test setups
        return
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig

        manifest = await hass.async_add_executor_job(
            (Path(__file__).parent / "manifest.json").read_text
        )
        version = json.loads(manifest).get("version", "0")
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(CARD_FILE), False)]
        )
        add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    except Exception as err:  # noqa: BLE001 - the card is optional
        _LOGGER.warning("Could not register the Lovelace card: %s", err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    scheduler = CheapestHourScheduler(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = scheduler
    _remove_stale_record_devices(hass, entry, scheduler)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await scheduler.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _remove_stale_record_devices(
    hass: HomeAssistant, entry: ConfigEntry, scheduler: CheapestHourScheduler
) -> None:
    """Remove devices of records that were deleted in the options."""
    registry = dr.async_get(hass)
    keep = {(DOMAIN, entry.entry_id)} | {
        record_device_identifier(entry.entry_id, d[CONF_DEVICE_ID]) for d in scheduler.devices
    }
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & keep:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


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
