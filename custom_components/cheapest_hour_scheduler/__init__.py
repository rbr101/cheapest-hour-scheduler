"""Cheapest Hour Scheduler integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import CONF_DEVICE_ID, CONF_DEVICES, DOMAIN, PLATFORMS, SUBENTRY_TYPE_RECORD
from .entity import record_device_identifier
from .scheduler import CheapestHourScheduler, record_name

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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config entries.

    1.1 -> 1.2: device records move from options["devices"] to config subentries,
    so each record gets its own row (with a settings icon) on the integration page.
    The record id is kept, so entities, devices and stored plans stay the same.
    """
    if entry.version > 1:
        return False  # downgrade from a future version
    if entry.version == 1 and entry.minor_version < 2:
        options = dict(entry.options)
        existing = {sub.unique_id for sub in entry.subentries.values()}
        for device in options.pop(CONF_DEVICES, []) or []:
            if device.get(CONF_DEVICE_ID) in existing:
                continue
            hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(dict(device)),
                    subentry_type=SUBENTRY_TYPE_RECORD,
                    title=record_name(hass, device),
                    unique_id=device.get(CONF_DEVICE_ID),
                ),
            )
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
        _LOGGER.info("Moved device records of %s to subentries", entry.title)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    scheduler = CheapestHourScheduler(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = scheduler
    _remove_stale_record_devices(hass, entry, scheduler)
    _link_record_devices_to_subentries(hass, entry, scheduler)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await scheduler.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _remove_stale_record_devices(
    hass: HomeAssistant, entry: ConfigEntry, scheduler: CheapestHourScheduler
) -> None:
    """Remove devices of records that no longer exist."""
    registry = dr.async_get(hass)
    keep = {(DOMAIN, entry.entry_id)} | {
        record_device_identifier(entry.entry_id, d[CONF_DEVICE_ID]) for d in scheduler.devices
    }
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & keep:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


def _link_record_devices_to_subentries(
    hass: HomeAssistant, entry: ConfigEntry, scheduler: CheapestHourScheduler
) -> None:
    """Attach record devices created before 0.7 to their subentry."""
    registry = dr.async_get(hass)
    for dev_id, subentry_id in scheduler.subentry_of.items():
        device = registry.async_get_device(
            identifiers={record_device_identifier(entry.entry_id, dev_id)}
        )
        if device is None:
            continue
        subentries = device.config_entries_subentries.get(entry.entry_id, set())
        if subentry_id not in subentries:
            registry.async_update_device(
                device.id, add_config_entry_id=entry.entry_id, add_config_subentry_id=subentry_id
            )
        if None in subentries:
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id, remove_config_subentry_id=None
            )


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
