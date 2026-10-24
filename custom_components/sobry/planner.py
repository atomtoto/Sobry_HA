"""Pure planning helpers used to drive devices from Sobry prices.

This module deliberately avoids any Home Assistant import so the scheduling
logic can be reasoned about (and tested) on its own.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Any

DEFAULT_SLOT_DURATION = timedelta(minutes=15)

MODE_CHEAPEST_SLOTS = "cheapest_slots"
MODE_CHEAPEST_BLOCK = "cheapest_block"
MODE_THRESHOLD = "threshold"

_PRICE_KEYS = (
    "price",  # V2: EUR/kWh, tax mode selected by the request.
    "price_ttc_eur_kwh",
    "price_ht_eur_kwh",
    "spot_price_eur_kwh",
)
_TOLERANCE = timedelta(seconds=1)


def extract_price(item: Any) -> float | None:
    """Return the EUR/kWh price of an API entry, whatever the payload shape."""
    if not isinstance(item, dict):
        return None
    for key in _PRICE_KEYS:
        try:
            return float(item[key])
        except (KeyError, TypeError, ValueError):
            continue
    try:
        return float(item["spot_price"]) / 1000.0
    except (KeyError, TypeError, ValueError):
        return None


def entry_start(item: Any) -> datetime | None:
    """Return the UTC start of an API entry carrying a ``timestamp``."""
    if not isinstance(item, dict):
        return None
    raw = item.get("timestamp")
    if not raw:
        return None
    try:
        start = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return start.astimezone(timezone.utc)


def merge_price_entries(
    previous: list[Any], latest: list[Any], since: datetime
) -> list[dict[str, Any]]:
    """Return ``latest`` completed by the entries of ``previous`` it lacks.

    A refresh after midnight no longer returns the evening before: keeping
    those prices lets a window that started yesterday still be planned.
    Entries starting before ``since`` are dropped, ``latest`` wins on a tie.
    """
    merged: dict[datetime, dict[str, Any]] = {}
    for item in [*previous, *latest]:
        start = entry_start(item)
        if start is None or start < since:
            continue
        merged[start] = item
    return [merged[start] for start in sorted(merged)]


@dataclass(frozen=True)
class PriceSlot:
    """A single priced time slot."""

    start: datetime
    end: datetime
    price: float

    @property
    def duration(self) -> timedelta:
        """Return the slot length."""
        return self.end - self.start

    @property
    def hours(self) -> float:
        """Return the slot length in hours."""
        return self.duration.total_seconds() / 3600

    def contains(self, moment: datetime) -> bool:
        """Return True when ``moment`` falls inside the slot."""
        return self.start <= moment < self.end

    def as_dict(self, tz: tzinfo | None = None) -> dict[str, Any]:
        """Return a JSON friendly representation of the slot."""
        start = self.start.astimezone(tz) if tz else self.start
        end = self.end.astimezone(tz) if tz else self.end
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "price": round(self.price, 6),
        }


@dataclass
class PlanWindow:
    """A contiguous run of selected slots."""

    start: datetime
    end: datetime
    price: float

    @property
    def hours(self) -> float:
        """Return the window length in hours, daylight saving time included."""
        return (self.end.timestamp() - self.start.timestamp()) / 3600

    def contains(self, moment: datetime) -> bool:
        """Return True when ``moment`` falls inside the window."""
        return self.start <= moment < self.end

    def as_dict(self, tz: tzinfo | None = None) -> dict[str, Any]:
        """Return a JSON friendly representation of the window."""
        start = self.start.astimezone(tz) if tz else self.start
        end = self.end.astimezone(tz) if tz else self.end
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "hours": round(self.hours, 3),
            "price": round(self.price, 6),
        }


@dataclass
class PlanResult:
    """Outcome of a planning run."""

    period_start: datetime
    period_end: datetime
    selected: list[PriceSlot] = field(default_factory=list)
    windows: list[PlanWindow] = field(default_factory=list)
    data_complete: bool = False
    candidate_count: int = 0
    reason: str | None = None
    # ``selected`` and ``windows`` hold every run. The slots run only because
    # they are priced under the force price, and the windows the plan decided
    # on its own, are also kept apart.
    forced: list[PriceSlot] = field(default_factory=list)
    planned_windows: list[PlanWindow] = field(default_factory=list)
    # Where the last run of the current period ends, before the next period is
    # merged in. Only that run may be held on by the appliance's consumption.
    last_end: datetime | None = None

    @property
    def selected_hours(self) -> float:
        """Return the total duration of the selected slots."""
        return sum(slot.hours for slot in self.selected)

    @property
    def scheduled_hours(self) -> float:
        """Return the total runtime, extensions beyond the prices included."""
        return sum(window.hours for window in self.windows)

    @property
    def average_price(self) -> float | None:
        """Return the duration weighted average price of the selection."""
        hours = self.selected_hours
        if not hours:
            return None
        return sum(slot.price * slot.hours for slot in self.selected) / hours

    def is_active(self, moment: datetime) -> bool:
        """Return True when the device should be running at ``moment``."""
        return any(window.contains(moment) for window in self.windows)

    def current_window(self, moment: datetime) -> PlanWindow | None:
        """Return the running window containing ``moment``, if any."""
        return next((window for window in self.windows if window.contains(moment)), None)

    def planned_window(self, moment: datetime) -> PlanWindow | None:
        """Return the window the plan decided on containing ``moment``, if any."""
        return next(
            (window for window in self.planned_windows if window.contains(moment)), None
        )

    def is_forced(self, moment: datetime) -> bool:
        """Return True when ``moment`` falls in a slot run for its price alone."""
        return any(slot.contains(moment) for slot in self.forced)

    def closes_period(self, window: PlanWindow) -> bool:
        """Return True when ``window`` holds the last run of the current period."""
        return self.last_end is not None and window.start < self.last_end <= window.end

    def next_window(self, moment: datetime) -> PlanWindow | None:
        """Return the first window starting after ``moment``."""
        return next((window for window in self.windows if window.start > moment), None)

    def next_transition(self, moment: datetime) -> datetime | None:
        """Return the next time the planned state changes."""
        current = self.current_window(moment)
        if current is not None:
            return current.end
        upcoming = self.next_window(moment)
        return upcoming.start if upcoming else None


def slot_step(slots: list[PriceSlot], default: timedelta = DEFAULT_SLOT_DURATION) -> timedelta:
    """Return the nominal slot length of a price series."""
    durations = Counter(slot.duration for slot in slots if slot.duration > timedelta(0))
    if not durations:
        return default
    return durations.most_common(1)[0][0]


def build_slots(
    prices: list[Any],
    default_duration: timedelta = DEFAULT_SLOT_DURATION,
) -> list[PriceSlot]:
    """Turn raw API entries into sorted, non overlapping price slots.

    Entries without a price (not published yet) and forecasts (``estimated``)
    are left out: a plan only relies on published prices.
    """
    parsed: list[tuple[datetime, float]] = []
    for item in prices:
        price = extract_price(item)
        if price is None or item.get("estimated"):
            continue
        start = entry_start(item)
        if start is None:
            continue
        parsed.append((start, price))

    parsed.sort(key=lambda entry: entry[0])
    # Drop duplicated timestamps, keeping the last value seen for a given start.
    deduplicated: list[tuple[datetime, float]] = []
    for start, price in parsed:
        if deduplicated and deduplicated[-1][0] == start:
            deduplicated[-1] = (start, price)
            continue
        deduplicated.append((start, price))

    deltas = Counter(
        deduplicated[index + 1][0] - deduplicated[index][0]
        for index in range(len(deduplicated) - 1)
    )
    step = default_duration
    for delta, _count in deltas.most_common():
        if delta > timedelta(0):
            step = delta
            break

    slots: list[PriceSlot] = []
    for index, (start, price) in enumerate(deduplicated):
        end = start + step
        if index + 1 < len(deduplicated):
            # A shorter gap means overlapping data, a longer one a hole in the
            # series: in both cases the slot stops at the nominal step.
            end = min(end, deduplicated[index + 1][0])
        if end <= start:
            continue
        slots.append(PriceSlot(start, end, price))
    return slots


def period_bounds(
    moment: datetime,
    window_start: time,
    window_end: time,
    tz: tzinfo,
) -> tuple[datetime, datetime]:
    """Return the current (or next) occurrence of the configured daily window.

    ``window_start == window_end`` means a full day starting at that time.
    """
    local = moment.astimezone(tz)
    today = local.date()

    def _at(day_offset: int, moment_of_day: time) -> datetime:
        return datetime.combine(today + timedelta(days=day_offset), moment_of_day, tzinfo=tz)

    if window_start == window_end:
        start = _at(0, window_start)
        if local < start:
            start = _at(-1, window_start)
        return start, start + timedelta(days=1)

    if window_start < window_end:
        if local >= _at(0, window_end):
            return _at(1, window_start), _at(1, window_end)
        return _at(0, window_start), _at(0, window_end)

    # The window crosses midnight.
    if local < _at(0, window_end):
        return _at(-1, window_start), _at(0, window_end)
    return _at(0, window_start), _at(1, window_end)


def merge_windows(selected: list[PriceSlot]) -> list[PlanWindow]:
    """Group contiguous slots into windows."""
    windows: list[PlanWindow] = []
    run: list[PriceSlot] = []

    def _flush() -> None:
        if not run:
            return
        hours = sum(slot.hours for slot in run)
        price = sum(slot.price * slot.hours for slot in run) / hours if hours else run[0].price
        windows.append(PlanWindow(run[0].start, run[-1].end, price))

    for slot in sorted(selected, key=lambda item: item.start):
        if run and slot.start == run[-1].end:
            run.append(slot)
            continue
        _flush()
        run = [slot]
    _flush()
    return windows


def merge_overlapping(windows: list[PlanWindow]) -> list[PlanWindow]:
    """Merge windows that overlap or touch, keeping a weighted price."""
    merged: list[PlanWindow] = []
    for window in sorted(windows, key=lambda item: item.start):
        if merged and window.start <= merged[-1].end:
            previous = merged[-1]
            hours = previous.hours + window.hours
            price = (
                (previous.price * previous.hours + window.price * window.hours) / hours
                if hours
                else previous.price
            )
            merged[-1] = PlanWindow(previous.start, max(previous.end, window.end), price)
            continue
        merged.append(window)
    return merged


def extend_last_window(
    result: PlanResult, slots: list[PriceSlot], extra_hours: float
) -> None:
    """Keep the device running ``extra_hours`` after the last planned slot.

    The extension is a duration, not a selection: it still applies when the
    price series stops before the end of the extension.
    """
    if extra_hours <= 0 or not result.windows:
        return

    extension_end = result.windows[-1].end + timedelta(hours=extra_hours)
    selected = set(result.selected)
    extra = [
        slot
        for slot in slots
        if result.windows[-1].end <= slot.start < extension_end and slot not in selected
    ]
    if extra:
        result.selected = sorted(selected.union(extra), key=lambda slot: slot.start)
        result.windows = merge_windows(result.selected)

    last = result.windows[-1]
    if last.end < extension_end:
        result.windows[-1] = PlanWindow(last.start, extension_end, last.price)


def add_forced_slots(
    result: PlanResult,
    slots: list[PriceSlot],
    force_price: float,
    since: datetime,
    tz: tzinfo,
    skip_negative_days: bool = False,
) -> None:
    """Run the device during every slot priced at or under ``force_price``.

    The forced slots come on top of the plan, whatever its window, runtime or
    maximum price. They are not extended: a run the price imposes is a bonus,
    not the run the device needs.

    With ``skip_negative_days``, a day (read in ``tz``) holding a negative
    price forces nothing: only the slots the plan selected run that day.
    """
    negative_days = (
        {slot.start.astimezone(tz).date() for slot in slots if slot.price < 0}
        if skip_negative_days
        else set()
    )
    selected = set(result.selected)
    forced = [
        slot
        for slot in slots
        if slot.end > since
        and slot.price <= force_price
        and slot not in selected
        and slot.start.astimezone(tz).date() not in negative_days
    ]
    if not forced:
        return
    result.forced = forced
    result.selected = sorted(selected.union(forced), key=lambda slot: slot.start)
    result.windows = merge_overlapping(result.windows + merge_windows(forced))


def _cheapest_slots(candidates: list[PriceSlot], hours: float) -> list[PriceSlot]:
    """Return the cheapest slots adding up to ``hours``, in any order."""
    selected: list[PriceSlot] = []
    total = 0.0
    for slot in sorted(candidates, key=lambda item: (item.price, item.start)):
        if total >= hours - 1e-9:
            break
        selected.append(slot)
        total += slot.hours
    return selected


def _cheapest_block(candidates: list[PriceSlot], hours: float) -> list[PriceSlot]:
    """Return the cheapest contiguous run of slots covering ``hours``."""
    runs: list[list[PriceSlot]] = []
    for slot in sorted(candidates, key=lambda item: item.start):
        if runs and runs[-1][-1].end == slot.start:
            runs[-1].append(slot)
            continue
        runs.append([slot])

    best: list[PriceSlot] | None = None
    best_price: float | None = None
    fallback: list[PriceSlot] | None = None
    fallback_price: float | None = None

    for run in runs:
        start = 0
        total = 0.0
        cost = 0.0
        for end in range(len(run)):
            total += run[end].hours
            cost += run[end].price * run[end].hours
            while start < end and total - run[start].hours >= hours - 1e-9:
                total -= run[start].hours
                cost -= run[start].price * run[start].hours
                start += 1
            if total < hours - 1e-9:
                continue
            average = cost / total
            if best_price is None or average < best_price - 1e-12:
                best_price = average
                best = run[start : end + 1]

        if best is None:
            # No run is long enough: keep the cheapest of the longest ones so
            # the device still runs during the best available period.
            run_hours = sum(slot.hours for slot in run)
            run_price = sum(slot.price * slot.hours for slot in run) / run_hours
            if (
                fallback is None
                or run_hours > sum(slot.hours for slot in fallback) + 1e-9
                or (
                    abs(run_hours - sum(slot.hours for slot in fallback)) <= 1e-9
                    and fallback_price is not None
                    and run_price < fallback_price
                )
            ):
                fallback = run
                fallback_price = run_price

    if best is not None:
        return best
    return fallback or []


def build_plan(
    slots: list[PriceSlot],
    moment: datetime,
    tz: tzinfo,
    *,
    mode: str = MODE_CHEAPEST_SLOTS,
    hours: float = 8.0,
    window_start: time = time(0, 0),
    window_end: time = time(0, 0),
    max_price: float | None = None,
    threshold_price: float | None = None,
    extra_hours: float = 0.0,
    require_complete_data: bool = True,
    force_price: float | None = None,
    force_skip_negative: bool = False,
) -> PlanResult:
    """Plan when a device should run, for the current window occurrence.

    When the price series already covers the next occurrence of the window in
    full, that occurrence is planned as well so the entities can advertise the
    next start beyond the end of the current period.

    Every known slot priced at or under ``force_price`` is added on top, from
    the start of the current period or from now, whichever comes first, except
    on the days holding a negative price when ``force_skip_negative`` is set.
    """
    period_start, period_end = period_bounds(moment, window_start, window_end, tz)
    result = _plan_period(
        slots,
        period_start,
        period_end,
        mode=mode,
        hours=hours,
        max_price=max_price,
        threshold_price=threshold_price,
        extra_hours=extra_hours,
        require_complete_data=require_complete_data,
    )
    result.last_end = max((window.end for window in result.windows), default=None)

    next_start, next_end = period_bounds(period_end + _TOLERANCE, window_start, window_end, tz)
    if next_start >= period_end:
        upcoming = _plan_period(
            slots,
            next_start,
            next_end,
            mode=mode,
            hours=hours,
            max_price=max_price,
            threshold_price=threshold_price,
            extra_hours=extra_hours,
            require_complete_data=True,
        )
        if upcoming.data_complete:
            result.selected = sorted(
                set(result.selected).union(upcoming.selected),
                key=lambda slot: slot.start,
            )
            result.windows = merge_overlapping(result.windows + upcoming.windows)

    result.planned_windows = list(result.windows)
    if force_price is not None:
        add_forced_slots(
            result,
            slots,
            force_price,
            min(period_start, moment),
            tz,
            force_skip_negative,
        )
    return result


def _plan_period(
    slots: list[PriceSlot],
    period_start: datetime,
    period_end: datetime,
    *,
    mode: str,
    hours: float,
    max_price: float | None,
    threshold_price: float | None,
    extra_hours: float,
    require_complete_data: bool,
) -> PlanResult:
    """Plan a single window occurrence."""
    candidates = [slot for slot in slots if period_start <= slot.start < period_end]
    covered = sum((slot.duration for slot in candidates), timedelta(0))
    # A period losing an hour to daylight saving time is complete with one
    # hour less. One gaining an hour is accepted without it: a source giving
    # local times only cannot tell the repeated hour apart.
    elapsed = period_end.astimezone(timezone.utc) - period_start.astimezone(timezone.utc)
    wall_clock = period_end.replace(tzinfo=None) - period_start.replace(tzinfo=None)
    data_complete = covered >= min(elapsed, wall_clock) - _TOLERANCE

    result = PlanResult(
        period_start=period_start,
        period_end=period_end,
        data_complete=data_complete,
        candidate_count=len(candidates),
    )

    if not candidates:
        result.reason = "no_prices"
        return result

    if require_complete_data and not data_complete:
        result.reason = "waiting_for_prices"
        return result

    if mode == MODE_THRESHOLD:
        limit = threshold_price if threshold_price is not None else max_price
        if limit is None:
            result.reason = "no_threshold"
            return result
        selected = [slot for slot in candidates if slot.price <= limit]
    else:
        eligible = [
            slot for slot in candidates if max_price is None or slot.price <= max_price
        ]
        if not eligible:
            result.reason = "above_max_price"
            return result
        if hours <= 0:
            result.reason = "no_runtime"
            return result
        if mode == MODE_CHEAPEST_BLOCK:
            selected = _cheapest_block(eligible, hours)
        else:
            selected = _cheapest_slots(eligible, hours)

    result.selected = sorted(selected, key=lambda slot: slot.start)
    result.windows = merge_windows(result.selected)
    extend_last_window(result, slots, extra_hours)
    if not result.selected and result.reason is None:
        result.reason = "no_slot_selected"
    return result
