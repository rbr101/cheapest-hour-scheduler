"""Config flow, options flow and device-record subentry flow."""

from __future__ import annotations

import json
from typing import Any
import uuid

import voluptuous as vol
import yaml

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.service import async_get_all_descriptions

from .const import (
    AUTO_VALUE,
    CONF_ACTION,
    CONF_ACTION_DATA,
    CONF_BLOCK_HOURS,
    CONF_DESCRIPTION,
    CONF_DEVICE_ID,
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
    MINUTES_PLACEHOLDER,
    SECONDS_PLACEHOLDER,
    SUBENTRY_TYPE_RECORD,
)
from .scheduler import record_name, render_action_data

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

# Shown literally in help texts (curly braces are placeholders in translations).
TEXT_PLACEHOLDERS = {
    "seconds_until_start": SECONDS_PLACEHOLDER,
    "minutes_until_start": MINUTES_PLACEHOLDER,
}

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


# --------------------------------------------------------------------- helpers


def _service_options(hass: HomeAssistant) -> list[str]:
    """All available actions (services) as "domain.service", for the dropdown."""
    return sorted(
        f"{domain}.{service}"
        for domain, services in hass.services.async_services().items()
        for service in services
    )


def _example_value(example: Any) -> Any:
    """Like the frontend: parse a string example as JSON-ish YAML, else keep it."""
    if not isinstance(example, str):
        return example
    try:
        return json.loads(example)
    except ValueError:
        pass
    try:
        parsed = yaml.safe_load(example)
    except yaml.YAMLError:
        return example
    return parsed if isinstance(parsed, (dict, list)) else example


async def async_example_action_data(hass: HomeAssistant, action: str) -> str:
    """Example data for an action, like "Fill example data" in the developer tools.

    Every field (also the ones in collapsed sections) that has an example in the
    action's description is filled in. Required fields without an example are
    added as a commented line so it is clear they still need a value.
    """
    domain, _, service = action.partition(".")
    descriptions = await async_get_all_descriptions(hass)
    description = (descriptions.get(domain) or {}).get(service) or {}

    example: dict[str, Any] = {}
    missing: list[str] = []

    def collect(fields: dict[str, Any]) -> None:
        for key, field in (fields or {}).items():
            if not isinstance(field, dict):
                continue
            if "fields" in field:  # a section
                collect(field["fields"])
            elif field.get("example"):  # the frontend skips empty/false examples too
                example[key] = _example_value(field["example"])
            elif field.get("required"):
                missing.append(key)

    collect(description.get("fields") or {})

    lines = [
        f"# Example data for {action}. Remove what you don't need.",
        f"# {SECONDS_PLACEHOLDER} / {MINUTES_PLACEHOLDER} = time until the planned start.",
    ]
    lines += [f"# {key}:" for key in missing]
    if example:
        lines.append(
            yaml.safe_dump(example, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip()
        )
    return "\n".join(lines) if (example or missing) else ""


def _target_schema(hass: HomeAssistant) -> vol.Schema:
    """Step 1 of a record: what to control (entity and/or action)."""
    return vol.Schema(
        {
            vol.Optional(CONF_DESCRIPTION): selector.TextSelector(),
            vol.Optional(CONF_ENTITY_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=DEVICE_DOMAINS)
            ),
            vol.Optional(CONF_ACTION): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_service_options(hass),
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _details_schema(record: dict[str, Any]) -> vol.Schema:
    """Step 2 of a record: values / action data and timing."""
    fields: dict[Any, Any] = {}
    if record.get(CONF_ACTION):
        fields[vol.Optional(CONF_ACTION_DATA)] = selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        )
    else:
        fields[vol.Optional(CONF_ON_VALUE, default="on")] = selector.TextSelector()
    if record.get(CONF_ENTITY_ID):
        fields[vol.Optional(CONF_OFF_VALUE)] = selector.TextSelector()
    fields[vol.Optional(CONF_READY_BY)] = selector.TimeSelector()
    fields[vol.Optional(CONF_DURATION)] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0.25, max=24, step=0.25, unit_of_measurement="h",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    fields[vol.Optional(CONF_OFFSET, default=0)] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=1440, step=15, unit_of_measurement="min",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    return vol.Schema(fields)


def _validate_target(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    entity_id = str(user_input.get(CONF_ENTITY_ID) or "").strip()
    action = str(user_input.get(CONF_ACTION) or "").strip()
    if not entity_id and not action:
        return {"base": "entity_or_action"}
    if action:
        domain, _, service = action.partition(".")
        if not service or not hass.services.has_service(domain, service):
            return {CONF_ACTION: "unknown_action"}
    return {}


def _validate_details(record: dict[str, Any], user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if record.get(CONF_ACTION):
        try:
            render_action_data(user_input.get(CONF_ACTION_DATA))
        except HomeAssistantError:
            errors[CONF_ACTION_DATA] = "invalid_data"
    elif str(record.get(CONF_ENTITY_ID, "")).split(".", 1)[0] in ("number", "input_number"):
        value = str(user_input.get(CONF_ON_VALUE, "")).strip()
        if value.lower() != AUTO_VALUE and "{" not in value:
            try:
                float(value)
            except ValueError:
                errors[CONF_ON_VALUE] = "not_a_number"
    return errors


def _build_record(target: dict[str, Any], details: dict[str, Any], dev_id: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        CONF_DEVICE_ID: dev_id,
        CONF_ON_VALUE: str(details.get(CONF_ON_VALUE) or "on").strip(),
        CONF_OFFSET: float(details.get(CONF_OFFSET) or 0),
    }
    for key in (CONF_DESCRIPTION, CONF_ENTITY_ID, CONF_ACTION):
        if str(target.get(key) or "").strip():
            record[key] = str(target[key]).strip()
    if record.get(CONF_ENTITY_ID) and str(details.get(CONF_OFF_VALUE) or "").strip():
        record[CONF_OFF_VALUE] = str(details[CONF_OFF_VALUE]).strip()
    if record.get(CONF_ACTION) and str(details.get(CONF_ACTION_DATA) or "").strip():
        record[CONF_ACTION_DATA] = str(details[CONF_ACTION_DATA]).rstrip()
    if details.get(CONF_DURATION):
        record[CONF_DURATION] = float(details[CONF_DURATION])
    if details.get(CONF_READY_BY):
        record[CONF_READY_BY] = str(details[CONF_READY_BY])
    return record


# ----------------------------------------------------------------- config flow


class CheapestHourConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup."""

    VERSION = 1
    MINOR_VERSION = 2  # 1.2: device records are subentries

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            name = user_input.pop(CONF_NAME)
            return self.async_create_entry(title=name, data=user_input, options={})

        schema = vol.Schema(
            {vol.Required(CONF_NAME, default="Cheapest hours"): str}
        ).extend(_settings_schema({}).schema)
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CheapestHourOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE_RECORD: RecordSubentryFlow}


class CheapestHourOptionsFlow(OptionsFlow):
    """Global settings. Device records are managed on the integration page."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        conf = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=_settings_schema(conf))


# ------------------------------------------------------- device record subentry


class RecordSubentryFlow(ConfigSubentryFlow):
    """Add (user) or edit (reconfigure, the settings icon) one device record."""

    def __init__(self) -> None:
        self._target: dict[str, Any] = {}
        self._current: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_step_target("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if not self._current:
            self._current = dict(self._get_reconfigure_subentry().data)
        return await self._async_step_target("reconfigure", user_input)

    async def _async_step_target(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_target(self.hass, user_input)
            if not errors:
                self._target = user_input
                return await self.async_step_details()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                _target_schema(self.hass), user_input or self._current
            ),
            errors=errors,
            description_placeholders=TEXT_PLACEHOLDERS,
        )

    async def async_step_details(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        target = self._target
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_details(target, user_input)
            if not errors:
                return self._async_save(user_input)

        suggested = dict(user_input or self._current)
        action = str(target.get(CONF_ACTION) or "").strip()
        if user_input is None and action:
            same_action = action == self._current.get(CONF_ACTION)
            if not (same_action and self._current.get(CONF_ACTION_DATA)):
                # New (or changed) action: start from the action's example data.
                suggested[CONF_ACTION_DATA] = await async_example_action_data(self.hass, action)

        return self.async_show_form(
            step_id="details",
            data_schema=self.add_suggested_values_to_schema(_details_schema(target), suggested),
            errors=errors,
            description_placeholders={
                **TEXT_PLACEHOLDERS,
                "target": record_name(self.hass, target),
            },
        )

    def _async_save(self, details: dict[str, Any]) -> SubentryFlowResult:
        if self.source == "reconfigure":
            subentry = self._get_reconfigure_subentry()
            dev_id = subentry.data.get(CONF_DEVICE_ID) or subentry.unique_id or subentry.subentry_id
            record = _build_record(self._target, details, dev_id)
            return self.async_update_and_abort(
                self._get_entry(), subentry, data=record, title=record_name(self.hass, record)
            )
        dev_id = uuid.uuid4().hex[:8]
        record = _build_record(self._target, details, dev_id)
        return self.async_create_entry(
            title=record_name(self.hass, record), data=record, unique_id=dev_id
        )
