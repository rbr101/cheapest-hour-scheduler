"""Pure planning logic (no Home Assistant imports, so it is unit-testable)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable


@dataclass(frozen=True)
class PriceSlot:
    """A single price interval."""

    start: datetime
    end: datetime
    price: float


@dataclass(frozen=True)
class Block:
    """The cheapest contiguous block."""

    start: datetime
    end: datetime
    average_price: float
    slots: tuple[PriceSlot, ...]

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def parse_prices(*price_lists: Iterable[dict] | None) -> list[PriceSlot]:
    """Turn the prices_today / prices_tomorrow attributes into sorted slots.

    Slot length is inferred from the spacing of the data (15 min for Zonneplan,
    but hourly data works too).
    """
    raw: dict[datetime, float] = {}
    for prices in price_lists:
        for item in prices or []:
            if not isinstance(item, dict):
                continue
            start = _parse_time(item.get("time") or item.get("start"))
            price = item.get("price", item.get("value"))
            if start is None or price is None:
                continue
            try:
                raw[start] = float(price)
            except (TypeError, ValueError):
                continue

    starts = sorted(raw)
    if not starts:
        return []

    diffs = [b - a for a, b in zip(starts, starts[1:]) if b > a]
    step = min(diffs) if diffs else timedelta(hours=1)

    return [PriceSlot(s, s + step, raw[s]) for s in starts]


def find_cheapest_block(
    slots: list[PriceSlot],
    hours: float,
    now: datetime,
    not_before: datetime | None = None,
) -> Block | None:
    """Return the cheapest contiguous block of `hours` that has not ended yet.

    The block may start in the slot that is currently running, never earlier.
    Returns None when there is not enough (future) price data.
    """
    if not slots or hours <= 0:
        return None

    step = slots[0].end - slots[0].start
    needed = max(1, round(timedelta(hours=hours) / step))

    earliest = now if not_before is None else max(now, not_before)
    # Allow the slot that is in progress right now.
    usable = [s for s in slots if s.end > earliest]

    best: Block | None = None
    for i in range(len(usable) - needed + 1):
        window = usable[i : i + needed]
        # Must be contiguous (no gaps in the data)
        if any(b.start != a.end for a, b in zip(window, window[1:])):
            continue
        avg = sum(s.price for s in window) / needed
        # Strictly lower wins -> earliest block wins on ties.
        if best is None or avg < best.average_price - 1e-9:
            best = Block(window[0].start, window[-1].end, avg, tuple(window))
    return best
