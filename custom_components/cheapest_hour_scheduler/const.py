"""Constants for Cheapest Hour Scheduler."""

DOMAIN = "cheapest_hour_scheduler"

CONF_PRICE_SENSOR = "price_sensor"
CONF_BLOCK_HOURS = "block_hours"
CONF_DEVICES = "devices"

# Device record keys
CONF_DEVICE_ID = "id"
CONF_ENTITY_ID = "entity_id"
CONF_ON_VALUE = "on_value"
CONF_OFF_VALUE = "off_value"
CONF_DURATION = "duration_hours"
CONF_OFFSET = "start_offset_minutes"

DEFAULT_PRICE_SENSOR = "sensor.zonneplan_current_tariff_rounded"
DEFAULT_BLOCK_HOURS = 3.0

ATTR_PRICES_TODAY = "prices_today"
ATTR_PRICES_TOMORROW = "prices_tomorrow"

# Placeholder in on_value: replaced by seconds from "now" until the device's start.
# Useful for e.g. number.keuken_dishwasher_start_in_relative
SECONDS_PLACEHOLDER = "{seconds_until_start}"
AUTO_VALUE = "auto"

PLATFORMS = ["sensor", "binary_sensor", "switch", "button"]

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"
