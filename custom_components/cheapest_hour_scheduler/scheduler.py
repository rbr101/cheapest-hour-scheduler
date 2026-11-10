"""Scheduler: plans the cheapest block and drives the configured entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_PRICES_TODAY,
    ATTR_PRICES_TOMORROW,
    AUTO_VALUE,
    CONF_BLOCK_HOURS,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_DURATION,
    CONF_ENTITY_ID,
    CONF_OFF_VALUE,
    CONF_OFFSET,
    CONF_ON_VALUE,
    CONF_PRICE_SENSOR,
    DEFAULT_BLOCK_HOURS,
    DEFAULT_PRICE_SENSOR,
    DOMAIN,
    SECONDS_PLACEHOLDER,
    SIGNAL_UPDATE,
)
from .planner import Block, PriceSlot, find_cheapest_block, parse_prices

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
REPLAN_INTERVAL = timedelta(minutes=5)

TOGGLE_DOMAINS = {
    "switch",
    "light",
    "fan",
    "input_boolean",
    "automation",
    "siren",
    "humidifier",
    "water_heater",
    "climate",
    "media_player",
    "remote",
    "vacuum",
}
NUMBER_DOMAINS = {"number", "input_number"}
SELECT_DOMAINS = {"select", "input_select"}
ON_WORDS = {"on", "aan", "true", "1"}
OFF_WORDS = {"off", "uit", "false", "0"}


@dataclass
class DevicePlan:
    """When a single device record will be switched."""

    device: dict[str, Any]
    start: datetime
    end: datetime | None
    started: bool = False
    stopped: bool = False
    armed_value: str | None = None  # value sent for "seconds until start" devices
    unsubs: list[Callable[[], None]] = field(default_factory=list)

    @property
    def is_relative(self) -> bool:
        return _is_relative_value(self.device.get(CONF_ON_VALUE))


def _is_relative_value(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text == AUTO_VALUE or SECONDS_PLACEHOLDER in text


class CheapestHourScheduler:
    """Plans the cheapest block and executes device actions."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        conf = {**entry.data, **entry.options}
        self.price_sensor: str = conf.get(CONF_PRICE_SENSOR, DEFAULT_PRICE_SENSOR)
        self.block_hours: float = float(conf.get(CONF_BLOCK_HOURS, DEFAULT_BLOCK_HOURS))
        self.devices: list[dict[str, Any]] = list(conf.get(CONF_DEVICES, []))

        self.enabled = True
        self.block: Block | None = None
        self.slots: list[PriceSlot] = []
        self.device_plans: dict[str, DevicePlan] = {}
        self.last_planned: datetime | None = None
        self.last_error: str | None = None

        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._unsubs: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        self.enabled = stored.get("enabled", True)
        self._restore_block(stored)

        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.price_sensor], self._handle_price_change
            )
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._handle_interval, REPLAN_INTERVAL)
        )
        await self.async_plan()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_device_timers()

    def _restore_block(self, stored: dict[str, Any]) -> None:
        """Restore a block that was active/planned before a restart."""
        try:
            start = dt_util.parse_datetime(stored["block_start"])
            end = dt_util.parse_datetime(stored["block_end"])
            avg = float(stored["block_avg"])
        except (KeyError, TypeError, ValueError):
            return
        if start and end and end > dt_util.utcnow():
            self.block = Block(start, end, avg, ())
            self._restored_flags: dict[str, dict[str, Any]] = stored.get("devices", {})

    async def _async_save(self) -> None:
        data: dict[str, Any] = {"enabled": self.enabled}
        if self.block:
            data.update(
                block_start=self.block.start.isoformat(),
                block_end=self.block.end.isoformat(),
                block_avg=self.block.average_price,
                devices={
                    dev_id: {
                        "started": p.started,
                        "stopped": p.stopped,
                        "armed_value": p.armed_value,
                    }
                    for dev_id, p in self.device_plans.items()
                },
            )
        await self._store.async_save(data)

    # --------------------------------------------------------------- triggers

    @callback
    def _handle_price_change(self, event: Event) -> None:
        self.hass.async_create_task(self.async_plan())

    async def _handle_interval(self, now: datetime) -> None:
        await self.async_plan()

    async def async_set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self._cancel_device_timers()
        await self.async_plan(force=True)

    # --------------------------------------------------------------- planning

    def _read_slots(self) -> list[PriceSlot]:
        state = self.hass.states.get(self.price_sensor)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self.last_error = f"{self.price_sensor} is unavailable"
            return []
        slots = parse_prices(
            state.attributes.get(ATTR_PRICES_TODAY),
            state.attributes.get(ATTR_PRICES_TOMORROW),
        )
        self.last_error = None if slots else f"No prices in {self.price_sensor}"
        return slots

    async def async_plan(self, force: bool = False) -> None:
        """(Re)calculate the block. An active block is never moved."""
        now = dt_util.utcnow()
        self.slots = self._read_slots()
        old_block = self.block

        if old_block and old_block.end <= now:
            # Finished: make sure every device got its "off" before moving on.
            await self._finish_block(old_block)
            new_block = find_cheapest_block(
                self.slots, self.block_hours, now, not_before=old_block.end
            )
        elif old_block and old_block.start <= now:
            new_block = old_block  # running: locked
        else:
            new_block = find_cheapest_block(self.slots, self.block_hours, now)
            if new_block is None and old_block and old_block.end > now:
                new_block = old_block  # keep what we had if data vanished

        changed = (
            new_block is None
            or old_block is None
            or new_block.start != old_block.start
            or new_block.end != old_block.end
        )
        # Fill in slots for a block restored from storage.
        if new_block and not new_block.slots and self.slots:
            match = [s for s in self.slots if new_block.start <= s.start < new_block.end]
            if match:
                new_block = Block(new_block.start, new_block.end, new_block.average_price, tuple(match))

        self.block = new_block
        self.last_planned = now

        if changed or force or not self.device_plans:
            await self._schedule_devices(keep_flags=not changed)
        else:
            await self._catch_up(now)

        await self._async_save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

    # ---------------------------------------------------------------- devices

    def _cancel_device_timers(self) -> None:
        for plan in self.device_plans.values():
            for unsub in plan.unsubs:
                unsub()
            plan.unsubs.clear()

    async def _schedule_devices(self, keep_flags: bool) -> None:
        old_plans = self.device_plans
        self._cancel_device_timers()
        self.device_plans = {}
        restored = getattr(self, "_restored_flags", {})
        self._restored_flags = {}

        if self.block is None:
            return

        for device in self.devices:
            dev_id = device[CONF_DEVICE_ID]
            offset = timedelta(minutes=float(device.get(CONF_OFFSET) or 0))
            start = self.block.start + offset
            duration = device.get(CONF_DURATION)
            end = start + timedelta(hours=float(duration)) if duration else self.block.end
            if _is_relative_value(device.get(CONF_ON_VALUE)) and not device.get(CONF_OFF_VALUE):
                end = None  # device runs its own program, nothing to switch off
            plan = DevicePlan(device, start, end)

            prev = old_plans.get(dev_id)
            if keep_flags and prev and prev.start == start:
                plan.started, plan.stopped, plan.armed_value = prev.started, prev.stopped, prev.armed_value
            elif dev_id in restored:
                flags = restored[dev_id]
                plan.started = flags.get("started", False)
                plan.stopped = flags.get("stopped", False)
                plan.armed_value = flags.get("armed_value")
            self.device_plans[dev_id] = plan

        await self._catch_up(dt_util.utcnow())

    async def _catch_up(self, now: datetime) -> None:
        """Run what is due now and set timers for what is due later."""
        if not self.enabled:
            return
        for plan in self.device_plans.values():
            for unsub in plan.unsubs:
                unsub()
            plan.unsubs.clear()

            if plan.is_relative:
                # e.g. dishwasher "start in X seconds": (re)send whenever the plan
                # is (re)calculated before the start moment.
                # A new start moment creates a fresh plan (armed_value None).
                if not plan.started and plan.start > now and plan.armed_value is None:
                    await self._arm_relative(plan, now)
                elif not plan.started:
                    plan.started = True
            else:
                if not plan.started:
                    if plan.start <= now and (plan.end is None or now < plan.end):
                        await self._run_start(plan)
                    elif plan.start > now:
                        plan.unsubs.append(
                            async_track_point_in_time(
                                self.hass, self._timer_cb(plan, "start"), plan.start
                            )
                        )
            if plan.end is not None and not plan.stopped:
                if plan.end <= now:
                    if plan.started:
                        await self._run_stop(plan)
                else:
                    plan.unsubs.append(
                        async_track_point_in_time(
                            self.hass, self._timer_cb(plan, "stop"), plan.end
                        )
                    )

    def _timer_cb(self, plan: DevicePlan, what: str):
        async def _cb(_now: datetime) -> None:
            if not self.enabled:
                return
            if what == "start" and not plan.started:
                await self._run_start(plan)
            elif what == "stop" and not plan.stopped:
                await self._run_stop(plan)
            await self._async_save()
            async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

        return _cb

    async def _finish_block(self, block: Block) -> None:
        for plan in self.device_plans.values():
            if plan.started and not plan.stopped and plan.end is not None and self.enabled:
                await self._run_stop(plan)

    async def _arm_relative(self, plan: DevicePlan, now: datetime) -> None:
        seconds = int((plan.start - now).total_seconds())
        # Round to whole minutes; most appliances only accept minute steps.
        seconds = max(0, (seconds // 60) * 60)
        raw = str(plan.device.get(CONF_ON_VALUE)).strip()
        value = str(seconds) if raw.lower() == AUTO_VALUE else raw.replace(SECONDS_PLACEHOLDER, str(seconds))
        await self._apply(plan.device[CONF_ENTITY_ID], value)
        plan.armed_value = value

    async def _run_start(self, plan: DevicePlan) -> None:
        await self._apply(plan.device[CONF_ENTITY_ID], plan.device.get(CONF_ON_VALUE, "on"))
        plan.started = True

    async def _run_stop(self, plan: DevicePlan) -> None:
        off_value = plan.device.get(CONF_OFF_VALUE)
        if off_value not in (None, ""):
            await self._apply(plan.device[CONF_ENTITY_ID], off_value)
        plan.stopped = True

    async def _apply(self, entity_id: str, value: Any) -> None:
        """Translate (entity, value) into the right service call."""
        domain = entity_id.split(".", 1)[0]
        text = str(value).strip()
        lower = text.lower()
        target = {"entity_id": entity_id}

        try:
            if domain in NUMBER_DOMAINS:
                await self.hass.services.async_call(
                    domain, "set_value", {**target, "value": float(text)}, blocking=True
                )
            elif domain in SELECT_DOMAINS:
                await self.hass.services.async_call(
                    domain, "select_option", {**target, "option": text}, blocking=True
                )
            elif domain in ("button", "input_button"):
                await self.hass.services.async_call(domain, "press", target, blocking=True)
            elif domain == "script" and lower in ON_WORDS:
                await self.hass.services.async_call("script", "turn_on", target, blocking=True)
            elif lower in ON_WORDS or lower in OFF_WORDS:
                service = "turn_on" if lower in ON_WORDS else "turn_off"
                svc_domain = domain if domain in TOGGLE_DOMAINS else "homeassistant"
                await self.hass.services.async_call(svc_domain, service, target, blocking=True)
            elif domain == "climate":
                await self.hass.services.async_call(
                    domain, "set_hvac_mode", {**target, "hvac_mode": text}, blocking=True
                )
            else:
                _LOGGER.warning("Don't know how to set %s to %s", entity_id, text)
                return
            _LOGGER.info("Set %s to %s", entity_id, text)
        except Exception as err:  # noqa: BLE001 - never break the scheduler
            self.last_error = f"{entity_id}: {err}"
            _LOGGER.error("Failed to set %s to %s: %s", entity_id, text, err)
