"""Scheduler: plans the cheapest block(s) and drives the configured entities.

Two kinds of device records:
* without "ready by": the device follows the shared cheapest block of the entry
  (start = block start + offset, run time = duration or until block end);
* with "ready by" (a time of day): the device gets its own cheapest window of
  `duration` hours (or the block length) that ends at or before the next time
  the clock shows that time.
"""

from __future__ import annotations

import asyncio
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
    SECONDS_PLACEHOLDER,
    SIGNAL_UPDATE,
)
from .planner import (
    Block,
    PriceSlot,
    covers,
    find_cheapest_block,
    next_occurrence,
    parse_prices,
    parse_time_of_day,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
REPLAN_INTERVAL = timedelta(minutes=5)
PRICE_CACHE_KEEP = timedelta(days=1)
# The shared block runs at most once a day: the next one starts at least this
# long after the previous one started (gives some room to move within the day).
SHARED_BLOCK_MIN_GAP = timedelta(hours=20)  # keep prices of the past day, plus all future ones

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
    end: datetime | None  # None: nothing to switch back (appliance runs itself)
    run_end: datetime  # when the device is expected to be finished
    price: float | None = None
    deadline: datetime | None = None
    started: bool = False
    stopped: bool = False
    armed_value: str | None = None  # value sent for "seconds until start" devices
    unsubs: list[Callable[[], None]] = field(default_factory=list, compare=False)

    @property
    def dev_id(self) -> str:
        return self.device[CONF_DEVICE_ID]

    @property
    def is_relative(self) -> bool:
        return _is_relative_value(self.device.get(CONF_ON_VALUE))

    def finished(self, now: datetime) -> bool:
        return self.start <= now and self.run_end <= now

    def locked(self, now: datetime) -> bool:
        """Started and still running: never move it."""
        return self.start <= now < self.run_end

    def same_slot(self, other: DevicePlan | None) -> bool:
        return other is not None and other.start == self.start and other.run_end == self.run_end

    def cancel(self) -> None:
        for unsub in self.unsubs:
            unsub()
        self.unsubs.clear()

    def to_store(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat() if self.end else None,
            "run_end": self.run_end.isoformat(),
            "price": self.price,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "started": self.started,
            "stopped": self.stopped,
            "armed_value": self.armed_value,
        }

    @classmethod
    def from_store(cls, device: dict[str, Any], data: dict[str, Any]) -> DevicePlan | None:
        try:
            parse = dt_util.parse_datetime
            return cls(
                device=device,
                start=parse(data["start"]),
                end=parse(data["end"]) if data.get("end") else None,
                run_end=parse(data["run_end"]),
                price=data.get("price"),
                deadline=parse(data["deadline"]) if data.get("deadline") else None,
                started=bool(data.get("started")),
                stopped=bool(data.get("stopped")),
                armed_value=data.get("armed_value"),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _is_relative_value(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text == AUTO_VALUE or SECONDS_PLACEHOLDER in text


def record_name(hass: HomeAssistant, device: dict[str, Any]) -> str:
    """Description of a record, falling back to the entity's friendly name."""
    if str(device.get(CONF_DESCRIPTION) or "").strip():
        return str(device[CONF_DESCRIPTION]).strip()
    state = hass.states.get(device[CONF_ENTITY_ID])
    if state and state.attributes.get("friendly_name"):
        return state.attributes["friendly_name"]
    return device[CONF_ENTITY_ID].split(".", 1)[-1].replace("_", " ").capitalize()


def _hours(device: dict[str, Any], default: float) -> float:
    try:
        return float(device.get(CONF_DURATION) or default)
    except (TypeError, ValueError):
        return default


class CheapestHourScheduler:
    """Plans the cheapest block(s) and executes device actions."""

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
        self.waiting: dict[str, str] = {}  # dev_id -> reason it has no plan
        # Last known prices (merged from every sensor update and persisted), used
        # when the price sensor is unavailable or temporarily has less data.
        self._price_cache: dict[datetime, PriceSlot] = {}
        self.price_source: str = "none"  # "sensor", "cache" or "none"
        self._last_block_start: datetime | None = None  # last finished shared block
        self.record_enabled: dict[str, bool] = {}  # dev_id -> on/off (default on)
        self.last_planned: datetime | None = None
        self.last_error: str | None = None

        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._unsubs: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ setup

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        self.enabled = stored.get("enabled", True)
        self._restore(stored)

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

    def _restore(self, stored: dict[str, Any]) -> None:
        """Restore the shared block and the device plans after a restart."""
        block = stored.get("block") or {}
        try:
            start = dt_util.parse_datetime(block["start"])
            end = dt_util.parse_datetime(block["end"])
            if start and end:
                self.block = Block(start, end, float(block["avg"]), ())
        except (KeyError, TypeError, ValueError):
            pass

        self.record_enabled = {
            k: bool(v) for k, v in (stored.get("record_enabled") or {}).items()
        }
        if stored.get("last_block_start"):
            self._last_block_start = dt_util.parse_datetime(stored["last_block_start"])

        for item in stored.get("prices") or []:
            try:
                start = dt_util.parse_datetime(item[0])
                end = dt_util.parse_datetime(item[1])
                if start and end:
                    self._price_cache[start] = PriceSlot(start, end, float(item[2]))
            except (IndexError, TypeError, ValueError):
                continue

        by_id = {d[CONF_DEVICE_ID]: d for d in self.devices}
        for dev_id, data in (stored.get("plans") or {}).items():
            device = by_id.get(dev_id)
            if device is None:
                continue
            plan = DevicePlan.from_store(device, data)
            if plan is not None:
                self.device_plans[dev_id] = plan

    async def _async_save(self) -> None:
        data: dict[str, Any] = {
            "enabled": self.enabled,
            "plans": {dev_id: p.to_store() for dev_id, p in self.device_plans.items()},
            "prices": [
                [slot.start.isoformat(), slot.end.isoformat(), slot.price]
                for slot in sorted(self._price_cache.values(), key=lambda sl: sl.start)
            ],
        }
        data["record_enabled"] = self.record_enabled
        if self._last_block_start:
            data["last_block_start"] = self._last_block_start.isoformat()
        if self.block:
            data["block"] = {
                "start": self.block.start.isoformat(),
                "end": self.block.end.isoformat(),
                "avg": self.block.average_price,
            }
        await self._store.async_save(data)

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

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
        await self.async_plan()

    def is_record_enabled(self, dev_id: str) -> bool:
        return self.record_enabled.get(dev_id, True)

    async def async_set_record_enabled(self, dev_id: str, enabled: bool) -> None:
        """Turn one record on/off. Turning it off while it runs switches it back."""
        self.record_enabled[dev_id] = enabled
        await self.async_plan()

    def name_of(self, device: dict[str, Any]) -> str:
        return record_name(self.hass, device)

    # --------------------------------------------------------------- planning

    def _read_slots(self) -> list[PriceSlot]:
        """Prices from the sensor, merged with (and falling back to) the cache."""
        now = dt_util.utcnow()
        state = self.hass.states.get(self.price_sensor)
        fresh: list[PriceSlot] = []
        if state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            fresh = parse_prices(
                state.attributes.get(ATTR_PRICES_TODAY),
                state.attributes.get(ATTR_PRICES_TOMORROW),
            )

        # New data wins; data the sensor no longer shows (e.g. yesterday after
        # midnight, or everything while it is unavailable) is kept from the cache.
        for slot in fresh:
            self._price_cache[slot.start] = slot
        cutoff = now - PRICE_CACHE_KEEP
        self._price_cache = {k: v for k, v in self._price_cache.items() if v.end > cutoff}
        slots = sorted(self._price_cache.values(), key=lambda sl: sl.start)

        if fresh:
            self.price_source = "sensor"
            self.last_error = None
        elif slots and slots[-1].end > now:
            self.price_source = "cache"
            self.last_error = (
                f"{self.price_sensor} has no prices; using stored prices up to "
                f"{dt_util.as_local(slots[-1].end):%a %H:%M}"
            )
            _LOGGER.debug(self.last_error)
        else:
            self.price_source = "none"
            self.last_error = f"No prices from {self.price_sensor} and none stored"
        return slots

    @property
    def prices_known_until(self) -> datetime | None:
        if not self._price_cache:
            return None
        return max(slot.end for slot in self._price_cache.values())

    def _plan_shared_block(self, now: datetime) -> None:
        """The entry-wide block, used by records without a ready-by time."""
        old = self.block
        if old and old.start <= now < old.end:
            new = old  # running: locked
        elif old and old.end <= now:
            new = find_cheapest_block(
                self.slots,
                self.block_hours,
                now,
                not_before=max(old.end, old.start + SHARED_BLOCK_MIN_GAP),
            )
            if new is None:
                self.block = None
                self._last_block_start = old.start
                return
        else:
            not_before = None
            if self._last_block_start is not None:
                not_before = self._last_block_start + SHARED_BLOCK_MIN_GAP
            new = find_cheapest_block(self.slots, self.block_hours, now, not_before=not_before)
            if new is None and old and old.end > now:
                new = old  # keep what we had if data vanished
        if old and old.end <= now:
            self._last_block_start = old.start
        # Fill in slots for a block restored from storage.
        if new and not new.slots and self.slots:
            match = tuple(s for s in self.slots if new.start <= s.start < new.end)
            if match:
                new = Block(new.start, new.end, new.average_price, match)
        self.block = new

    def _window_price(self, start: datetime, end: datetime) -> float | None:
        prices = [s.price for s in self.slots if start <= s.start < end]
        return round(sum(prices) / len(prices), 4) if prices else None

    def _new_plan(self, device: dict[str, Any], prev: DevicePlan | None, now: datetime) -> DevicePlan | None:
        """Compute the next plan for one record (prev is not running)."""
        dev_id = device[CONF_DEVICE_ID]
        relative_only = _is_relative_value(device.get(CONF_ON_VALUE)) and not device.get(CONF_OFF_VALUE)
        ready_by = parse_time_of_day(device.get(CONF_READY_BY))

        if ready_by is None:
            if self.block is None or self.block.end <= now:
                self.waiting[dev_id] = "waiting for the next block (needs prices)"
                return None
            start = self.block.start + timedelta(minutes=float(device.get(CONF_OFFSET) or 0))
            run_end = start + timedelta(hours=float(device[CONF_DURATION])) if device.get(CONF_DURATION) else self.block.end
            if prev and prev.finished(now) and prev.same_slot(DevicePlan(device, start, None, run_end)):
                return prev  # this block was already done for this device
            return DevicePlan(
                device, start, None if relative_only else run_end, run_end,
                price=self._window_price(start, run_end),
            )

        # ---- own window that must be finished by `ready_by`
        hours = _hours(device, self.block_hours)
        tz = dt_util.get_default_time_zone()
        base = now
        if prev and prev.finished(now) and prev.deadline:
            base = max(now, prev.deadline)  # done for that deadline -> next one
        deadline = next_occurrence(base, ready_by, tz)
        latest_start = deadline - timedelta(hours=hours)

        window = None
        if covers(self.slots, deadline):
            window = find_cheapest_block(self.slots, hours, now, deadline=deadline)
        elif latest_start <= now + REPLAN_INTERVAL:
            # Prices don't reach the deadline, but waiting longer would be too late:
            # take the cheapest window we know about, or simply start now.
            window = find_cheapest_block(self.slots, hours, now, deadline=deadline)
            if window is None:
                start = now
                return DevicePlan(
                    device, start, None if relative_only else start + timedelta(hours=hours),
                    start + timedelta(hours=hours), deadline=deadline,
                )
        else:
            self.waiting[dev_id] = (
                f"waiting for prices up to {dt_util.as_local(deadline):%a %H:%M}"
            )
            return None

        if window is None:
            # Not enough time left before the deadline: start right away.
            start = now
            run_end = now + timedelta(hours=hours)
            self.waiting[dev_id] = "not enough time before ready-by, started immediately"
            return DevicePlan(device, start, None if relative_only else run_end, run_end, deadline=deadline)

        return DevicePlan(
            device, window.start, None if relative_only else window.end, window.end,
            price=round(window.average_price, 4), deadline=deadline,
        )

    async def async_plan(self) -> None:
        """(Re)calculate everything. A running device plan is never moved."""
        async with self._lock:
            now = dt_util.utcnow()
            self.slots = self._read_slots()
            self._plan_shared_block(now)
            self.waiting = {}
            self.last_planned = now

            new_plans: dict[str, DevicePlan] = {}
            for device in self.devices:
                dev_id = device[CONF_DEVICE_ID]
                prev = self.device_plans.get(dev_id)

                if not self.is_record_enabled(dev_id):
                    if prev:
                        if prev.started and not prev.stopped and prev.end and self.enabled:
                            await self._run_stop(prev)  # switched off while running
                        prev.cancel()
                    self.waiting[dev_id] = "disabled"
                    continue
                if prev and prev.locked(now):
                    new_plans[dev_id] = prev
                    continue
                # A finished run that still has to be switched off.
                if prev and prev.started and not prev.stopped and prev.end and prev.end <= now and self.enabled:
                    await self._run_stop(prev)

                plan = self._new_plan(device, prev, now)
                if plan is None:
                    if prev:
                        prev.cancel()
                    continue
                if prev is not None and plan is not prev and plan.same_slot(prev):
                    # Same moment as before: keep what was already executed.
                    plan.started, plan.stopped, plan.armed_value = prev.started, prev.stopped, prev.armed_value
                if prev is not None and plan is not prev:
                    prev.cancel()
                new_plans[dev_id] = plan

            for dev_id, old in self.device_plans.items():
                if dev_id not in new_plans:
                    old.cancel()
            self.device_plans = new_plans

            await self._catch_up(now)
            await self._async_save()
        self._notify()

    # ---------------------------------------------------------------- devices

    def _cancel_device_timers(self) -> None:
        for plan in self.device_plans.values():
            plan.cancel()

    async def _catch_up(self, now: datetime) -> None:
        """Run what is due now and set timers for what is due later."""
        if not self.enabled:
            self._cancel_device_timers()
            return
        for plan in self.device_plans.values():
            plan.cancel()

            if plan.is_relative:
                # e.g. dishwasher "start in X seconds": sent once per planned start
                # moment; a moved start creates a fresh plan (armed_value None).
                if not plan.started and plan.start > now and plan.armed_value is None:
                    await self._arm_relative(plan, now)
                elif not plan.started and plan.start <= now:
                    plan.started = True
            elif not plan.started:
                if plan.start <= now < plan.run_end:
                    await self._run_start(plan)
                elif plan.start > now:
                    plan.unsubs.append(
                        async_track_point_in_time(self.hass, self._timer_cb(plan, "start"), plan.start)
                    )
            if plan.end is not None and not plan.stopped:
                if plan.end <= now:
                    if plan.started:
                        await self._run_stop(plan)
                else:
                    plan.unsubs.append(
                        async_track_point_in_time(self.hass, self._timer_cb(plan, "stop"), plan.end)
                    )

    def _timer_cb(self, plan: DevicePlan, what: str):
        async def _cb(_now: datetime) -> None:
            if not self.enabled or self.device_plans.get(plan.dev_id) is not plan:
                return
            if what == "start" and not plan.started:
                await self._run_start(plan)
            elif what == "stop" and not plan.stopped:
                await self._run_stop(plan)
            await self._async_save()
            self._notify()

        return _cb

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
