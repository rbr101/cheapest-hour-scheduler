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


def _record_schema(hass: HomeAssistant) -> vol.Schema:
    """One form with all fields of a device record (as before 0.7)."""
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


def _validate_record(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    entity_id = str(user_input.get(CONF_ENTITY_ID) or "").strip()
    action = str(user_input.get(CONF_ACTION) or "").strip()
    if not entity_id and not action:
        return {"base": "entity_or_action"}

    errors: dict[str, str] = {}
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
    if str(user_input.get(CONF_OFF_VALUE) or "").strip() and not entity_id:
        errors[CONF_OFF_VALUE] = "off_needs_entity"
    return errors


def _build_record(user_input: dict[str, Any], dev_id: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        CONF_DEVICE_ID: dev_id,
        CONF_ON_VALUE: str(user_input.get(CONF_ON_VALUE) or "on").strip(),
        CONF_OFFSET: float(user_input.get(CONF_OFFSET) or 0),
    }
    for key in (CONF_DESCRIPTION, CONF_ENTITY_ID, CONF_OFF_VALUE, CONF_ACTION):
        if str(user_input.get(key) or "").strip():
            record[key] = str(user_input[key]).strip()
    if record.get(CONF_ACTION) and str(user_input.get(CONF_ACTION_DATA) or "").strip():
        record[CONF_ACTION_DATA] = str(user_input[CONF_ACTION_DATA]).rstrip()
    if user_input.get(CONF_DURATION):
        record[CONF_DURATION] = float(user_input[CONF_DURATION])
    if user_input.get(CONF_READY_BY):
        record[CONF_READY_BY] = str(user_input[CONF_READY_BY])
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
    """Add (user) or edit (reconfigure, the settings icon) one device record.

    One form with all fields. When an action is chosen and its data is left
    empty, the form comes back once with the action's example data filled in.
    """

    def __init__(self) -> None:
        self._example_offered = False

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_step_record("user", user_input, {})

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        current = dict(self._get_reconfigure_subentry().data)
        return await self._async_step_record("reconfigure", user_input, current)

    async def _async_step_record(
        self, step_id: str, user_input: dict[str, Any] | None, current: dict[str, Any]
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        suggested = dict(user_input or current)
        if user_input is not None:
            errors = _validate_record(self.hass, user_input)
            action = str(user_input.get(CONF_ACTION) or "").strip()
            wants_example = (
                not errors
                and action
                and not str(user_input.get(CONF_ACTION_DATA) or "").strip()
                and not self._example_offered
            )
            if wants_example:
                example = await async_example_action_data(self.hass, action)
                if example:
                    # Show the same form again with the example data to check.
                    self._example_offered = True
                    suggested[CONF_ACTION_DATA] = example
                    return self._show(step_id, suggested, errors)
            if not errors:
                return self._async_save(step_id, user_input)
        return self._show(step_id, suggested, errors)

    def _show(self, step_id: str, suggested: dict[str, Any], errors: dict[str, str]) -> SubentryFlowResult:
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(_record_schema(self.hass), suggested),
            errors=errors,
            description_placeholders=TEXT_PLACEHOLDERS,
        )

    def _async_save(self, step_id: str, user_input: dict[str, Any]) -> SubentryFlowResult:
        if step_id == "reconfigure":
            subentry = self._get_reconfigure_subentry()
            dev_id = subentry.data.get(CONF_DEVICE_ID) or subentry.unique_id or subentry.subentry_id
            record = _build_record(user_input, dev_id)
            return self.async_update_and_abort(
                self._get_entry(), subentry, data=record, title=record_name(self.hass, record)
            )
        dev_id = uuid.uuid4().hex[:8]
        record = _build_record(user_input, dev_id)
        return self.async_create_entry(
            title=record_name(self.hass, record), data=record, unique_id=dev_id
        )
