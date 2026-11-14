"""Config and options flow for Cheapest Hour Scheduler."""

from __future__ import annotations

from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    AUTO_VALUE,
    CONF_ACTION,
    CONF_ACTION_DATA,
    CONF_BLOCK_HOURS,
    CONF_DESCRIPTION,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_DURATION,
    CONF_ENTITY_ID,
    CONF_OFF_VALUE,
    CONF_OFFSET,
    CONF_ON_VALUE,
    CONF_PRICE_SENSOR,
    CONF_READY_BY,
    DEFAULT_BLOCK_HOURS,
    DEFAULT_PRICE_SENSOR,
    DOMAIN,
)
from .scheduler import render_action_data

DEVICE_DOMAINS = [
    "switch",
    "light",
    "fan",
    "input_boolean",
    "number",
    "input_number",
    "select",
    "input_select",
    "button",
    "input_button",
    "script",
    "automation",
    "climate",
    "water_heater",
]

BLOCK_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0.25, max=24, step=0.25, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX
    )
)


def _settings_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_PRICE_SENSOR, default=defaults.get(CONF_PRICE_SENSOR, DEFAULT_PRICE_SENSOR)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_BLOCK_HOURS, default=defaults.get(CONF_BLOCK_HOURS, DEFAULT_BLOCK_HOURS)
            ): BLOCK_SELECTOR,
        }
    )


def _service_options(hass: HomeAssistant) -> list[str]:
    """All available actions (services) as "domain.service", for the dropdown."""
    return sorted(
        f"{domain}.{service}"
        for domain, services in hass.services.async_services().items()
        for service in services
    )


def _device_schema(hass: HomeAssistant) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_DESCRIPTION): selector.TextSelector(),
            vol.Optional(CONF_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=DEVICE_DOMAINS)
            ),
            vol.Optional(CONF_ON_VALUE, default="on"): selector.TextSelector(),
            vol.Optional(CONF_OFF_VALUE): selector.TextSelector(),
            vol.Optional(CONF_ACTION): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_service_options(hass),
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_ACTION_DATA): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Optional(CONF_READY_BY): selector.TimeSelector(),
            vol.Optional(CONF_DURATION): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.25, max=24, step=0.25, unit_of_measurement="h",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(CONF_OFFSET, default=0): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1440, step=15, unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _clean_device(user_input: dict[str, Any], dev_id: str | None = None) -> dict[str, Any]:
    device: dict[str, Any] = {
        CONF_DEVICE_ID: dev_id or uuid.uuid4().hex[:8],
        CONF_ON_VALUE: str(user_input.get(CONF_ON_VALUE) or "on").strip(),
        CONF_OFFSET: float(user_input.get(CONF_OFFSET) or 0),
    }
    for key in (CONF_ENTITY_ID, CONF_OFF_VALUE, CONF_DESCRIPTION, CONF_ACTION):
        if str(user_input.get(key) or "").strip():
            device[key] = str(user_input[key]).strip()
    if str(user_input.get(CONF_ACTION_DATA) or "").strip():
        device[CONF_ACTION_DATA] = str(user_input[CONF_ACTION_DATA]).rstrip()
    if user_input.get(CONF_DURATION):
        device[CONF_DURATION] = float(user_input[CONF_DURATION])
    if user_input.get(CONF_READY_BY):
        device[CONF_READY_BY] = str(user_input[CONF_READY_BY])
    return device


def _validate_device(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    entity_id = str(user_input.get(CONF_ENTITY_ID) or "").strip()
    action = str(user_input.get(CONF_ACTION) or "").strip()

    if not entity_id and not action:
        errors["base"] = "entity_or_action"
        return errors

    if action:
        domain, _, service = action.partition(".")
        if not service or not hass.services.has_service(domain, service):
            errors[CONF_ACTION] = "unknown_action"
        try:
            render_action_data(user_input.get(CONF_ACTION_DATA))
        except HomeAssistantError:
            errors[CONF_ACTION_DATA] = "invalid_data"
    elif entity_id.split(".", 1)[0] in ("number", "input_number"):
        value = str(user_input.get(CONF_ON_VALUE, "")).strip()
        if value.lower() != AUTO_VALUE and "{" not in value:
            try:
                float(value)
            except ValueError:
                errors[CONF_ON_VALUE] = "not_a_number"
    if user_input.get(CONF_OFF_VALUE) and not entity_id:
        errors[CONF_OFF_VALUE] = "off_needs_entity"
    return errors


class CheapestHourConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            name = user_input.pop(CONF_NAME)
            return self.async_create_entry(
                title=name, data=user_input, options={CONF_DEVICES: []}
            )

        schema = vol.Schema(
            {vol.Required(CONF_NAME, default="Cheapest hours"): str}
        ).extend(_settings_schema({}).schema)
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CheapestHourOptionsFlow()


class CheapestHourOptionsFlow(OptionsFlow):
    """Manage settings and device records."""

    def __init__(self) -> None:
        self._devices: list[dict[str, Any]] | None = None
        self._settings: dict[str, Any] | None = None
        self._edit_id: str | None = None

    # helpers ------------------------------------------------------------
    @property
    def _conf(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _get_devices(self) -> list[dict[str, Any]]:
        if self._devices is None:
            self._devices = [dict(d) for d in self._conf.get(CONF_DEVICES, [])]
        return self._devices

    def _save(self) -> ConfigFlowResult:
        conf = self._conf
        data = {
            CONF_PRICE_SENSOR: conf.get(CONF_PRICE_SENSOR, DEFAULT_PRICE_SENSOR),
            CONF_BLOCK_HOURS: conf.get(CONF_BLOCK_HOURS, DEFAULT_BLOCK_HOURS),
            **(self._settings or {}),
            CONF_DEVICES: self._get_devices(),
        }
        return self.async_create_entry(title="", data=data)

    def _device_labels(self) -> list[selector.SelectOptionDict]:
        labels = []
        for d in self._get_devices():
            parts = [d[CONF_DESCRIPTION]] if d.get(CONF_DESCRIPTION) else []
            target = d.get(CONF_ACTION) or d.get(CONF_ENTITY_ID, "")
            parts += [f"({target})" if parts else target]
            if not d.get(CONF_ACTION):
                parts.append(f"→ {d[CONF_ON_VALUE]}")
            if d.get(CONF_DURATION):
                parts.append(f"{d[CONF_DURATION]:g} h")
            if d.get(CONF_READY_BY):
                parts.append(f"klaar om {str(d[CONF_READY_BY])[:5]}")
            if d.get(CONF_OFFSET):
                parts.append(f"+{d[CONF_OFFSET]:g} min")
            labels.append(selector.SelectOptionDict(value=d[CONF_DEVICE_ID], label=" ".join(parts)))
        return labels

    # steps --------------------------------------------------------------
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["settings", "add_device"]
        if self._get_devices():
            menu += ["edit_device", "remove_device"]
        menu.append("finish")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self._save()

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._settings = user_input
            return self._save()
        return self.async_show_form(step_id="settings", data_schema=_settings_schema(self._conf))

    async def async_step_add_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_device(self.hass, user_input)
            if not errors:
                self._get_devices().append(_clean_device(user_input))
                return self._save()
        return self.async_show_form(
            step_id="add_device",
            data_schema=self.add_suggested_values_to_schema(_device_schema(self.hass), user_input or {}),
            errors=errors,
        )

    async def async_step_edit_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._edit_id = user_input[CONF_DEVICE_ID]
            return await self.async_step_edit_device_form()
        return self.async_show_form(
            step_id="edit_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=self._device_labels())
                    )
                }
            ),
        )

    async def async_step_edit_device_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        devices = self._get_devices()
        current = next(d for d in devices if d[CONF_DEVICE_ID] == self._edit_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_device(self.hass, user_input)
            if not errors:
                devices[devices.index(current)] = _clean_device(user_input, self._edit_id)
                return self._save()
        return self.async_show_form(
            step_id="edit_device_form",
            data_schema=self.add_suggested_values_to_schema(_device_schema(self.hass), user_input or current),
            errors=errors,
        )

    async def async_step_remove_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            remove = set(user_input.get(CONF_DEVICES, []))
            self._devices = [d for d in self._get_devices() if d[CONF_DEVICE_ID] not in remove]
            return self._save()
        return self.async_show_form(
            step_id="remove_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICES): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self._device_labels(), multiple=True
                        )
                    )
                }
            ),
        )
