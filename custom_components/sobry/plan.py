"""Runtime object turning the Sobry price series into device control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_NAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfPower,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EXTRA_HOURS,
    CONF_FALLBACK_END,
    CONF_FALLBACK_START,
    CONF_HOURS,
    CONF_HOURS_BY_MONTH,
    CONF_HOURS_ENTITY,
    CONF_MAX_PRICE,
    CONF_MODE,
    CONF_POWER_ENTITY,
    CONF_POWER_HOLD_MAX,
    CONF_POWER_THRESHOLD,
    CONF_PLAN_ID,
    CONF_PLANS,
    CONF_REQUIRE_COMPLETE_DATA,
    CONF_TARGET_ENTITY,
    CONF_THRESHOLD_PRICE,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_EXTRA_HOURS,
    DEFAULT_HOURS,
    DEFAULT_POWER_HOLD_MAX,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_REQUIRE_COMPLETE_DATA,
    DEFAULT_THRESHOLD_PRICE,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
    HOURS_SOURCE_DEFAULT,
    HOURS_SOURCE_ENTITY,
    HOURS_SOURCE_MANUAL,
    HOURS_SOURCE_MONTH,
    MAX_HOURS,
    MODE_CHEAPEST_SLOTS,
    MODE_THRESHOLD,
    NAME,
)
from .coordinator import SobryDataUpdateCoordinator
from .planner import PlanResult, PlanWindow, build_plan, period_bounds

_LOGGER = logging.getLogger(__name__)

# A power reading has to stay below the threshold for that long before the
# appliance is released: a single dip must not cut a heating cycle short.
POWER_RELEASE_DELAY = timedelta(minutes=2)


def local_timezone():
    """Return the Home Assistant time zone, across supported HA versions."""
    getter = getattr(dt_util, "get_default_time_zone", None)
    if getter is not None:
        return getter()
    return dt_util.DEFAULT_TIME_ZONE


def parse_time(value: Any, default: str) -> time:
    """Parse a ``HH:MM(:SS)`` option, falling back to ``default``."""
    parsed = optional_time(value)
    if parsed is None:
        parsed = dt_util.parse_time(default)
    return parsed or time(0, 0)


def optional_time(value: Any) -> time | None:
    """Parse a ``HH:MM(:SS)`` option that may be left empty."""
    if value in (None, ""):
        return None
    return dt_util.parse_time(str(value))


def month_hours(value: Any) -> dict[int, float]:
    """Parse the per month runtime table stored in the options."""
    if not isinstance(value, dict):
        return {}
    parsed: dict[int, float] = {}
    for month, hours in value.items():
        try:
            key = int(month)
        except (TypeError, ValueError):
            continue
        amount = _as_float(hours)
        if amount is None or not 1 <= key <= 12:
            continue
        parsed[key] = amount
    return parsed


def _as_float(value: Any, default: float | None = None) -> float | None:
    """Return ``value`` as a float, or ``default`` when it is not usable."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class SobryPlan:
    """Decide when a single appliance should run, and drive it."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: SobryDataUpdateCoordinator,
        config: dict[str, Any],
    ) -> None:
        """Initialise the plan from its stored configuration."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.config = config
        self.plan_id: str = config[CONF_PLAN_ID]
        self.name: str = config.get(CONF_NAME) or NAME
        self.mode: str = config.get(CONF_MODE, MODE_CHEAPEST_SLOTS)
        self.target_entity: str | None = config.get(CONF_TARGET_ENTITY) or None
        self.window_start = parse_time(config.get(CONF_WINDOW_START), DEFAULT_WINDOW_START)
        self.window_end = parse_time(config.get(CONF_WINDOW_END), DEFAULT_WINDOW_END)
        self.max_price = _as_float(config.get(CONF_MAX_PRICE))
        # Runtime sources, in order of precedence: an entity, the month of the
        # period, then the runtime configured on the plan.
        self.hours_entity: str | None = config.get(CONF_HOURS_ENTITY) or None
        self.hours_by_month = month_hours(config.get(CONF_HOURS_BY_MONTH))
        self.extra_hours = (
            _as_float(config.get(CONF_EXTRA_HOURS), DEFAULT_EXTRA_HOURS) or 0.0
        )
        # Hold the appliance on after its window while it still draws power.
        self.power_entity: str | None = config.get(CONF_POWER_ENTITY) or None
        self.power_threshold = (
            _as_float(config.get(CONF_POWER_THRESHOLD), DEFAULT_POWER_THRESHOLD) or 0.0
        )
        self.power_hold_max = (
            _as_float(config.get(CONF_POWER_HOLD_MAX), DEFAULT_POWER_HOLD_MAX) or 0.0
        )
        # Fixed schedule used whenever the plan cannot decide on its own.
        self.fallback_start = optional_time(config.get(CONF_FALLBACK_START))
        self.fallback_end = optional_time(config.get(CONF_FALLBACK_END))
        self.require_complete_data = bool(
            config.get(CONF_REQUIRE_COMPLETE_DATA, DEFAULT_REQUIRE_COMPLETE_DATA)
        )

        # Configured values are kept apart: they are the reference the
        # tunable entities compare their restored value against.
        self.configured_hours = _as_float(config.get(CONF_HOURS), DEFAULT_HOURS) or 0.0
        self.configured_threshold_price = _as_float(
            config.get(CONF_THRESHOLD_PRICE), DEFAULT_THRESHOLD_PRICE
        )
        self._hours = self.configured_hours
        self._threshold_price = self.configured_threshold_price
        self._control_enabled = True
        self._control_started = False
        self._applied_state: bool | None = None
        self._last_control_error: str | None = None
        self._active: bool | None = None
        self._hours_source = HOURS_SOURCE_MANUAL
        self._last_hours_error: str | None = None
        self._fallback_active = False
        self._fallback_window: tuple[datetime, datetime] | None = None
        self._holding = False
        self._hold_deadline: datetime | None = None
        self._power_below_since: datetime | None = None
        self._power_now: float | None = None
        self._last_power_error: str | None = None
        self._listeners: list[Callable[[], None]] = []

        self.result: PlanResult | None = None

    # -- Identity ---------------------------------------------------------

    @property
    def unique_id(self) -> str:
        """Return the base unique id of the plan entities."""
        return f"{self.entry.entry_id}_{self.plan_id}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device grouping every entity of the plan."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer=NAME,
            model="Price plan",
        )

    # -- Tunable settings -------------------------------------------------

    @property
    def hours(self) -> float:
        """Return the daily runtime the plan schedules."""
        return self._hours

    @property
    def uses_derived_hours(self) -> bool:
        """Return whether the runtime comes from a source, not from the UI."""
        return bool(self.hours_entity or self.hours_by_month)

    @property
    def hours_source(self) -> str:
        """Return where the current runtime comes from."""
        return self._hours_source

    @property
    def threshold_price(self) -> float | None:
        """Return the price under which the appliance may run."""
        return self._threshold_price

    @property
    def control_enabled(self) -> bool:
        """Return whether the plan is allowed to drive its target entity."""
        return self._control_enabled

    @property
    def is_active(self) -> bool | None:
        """Return whether the appliance should be running right now."""
        return self._active

    @property
    def has_fallback(self) -> bool:
        """Return whether a fixed fallback schedule is configured."""
        return (
            self.fallback_start is not None
            and self.fallback_end is not None
            and self.fallback_start != self.fallback_end
        )

    @property
    def has_power_hold(self) -> bool:
        """Return whether the appliance may be held on by its consumption."""
        return bool(self.power_entity) and self.power_hold_max > 0

    @property
    def power_hold_active(self) -> bool:
        """Return whether the appliance is held on by its consumption."""
        return self._holding

    @property
    def fallback_active(self) -> bool:
        """Return whether the fixed schedule is currently driving the plan."""
        return self._fallback_active

    @property
    def available(self) -> bool:
        """Return whether the plan knows what the appliance should do.

        A plan running on its fixed fallback schedule stays available: it is
        precisely the case where the prices are missing.
        """
        return self.result is not None or self._fallback_window is not None

    async def async_set_hours(self, hours: float) -> None:
        """Change the scheduled runtime."""
        self._hours = float(hours)
        await self.async_update()

    async def async_set_threshold_price(self, price: float) -> None:
        """Change the price threshold."""
        self._threshold_price = float(price)
        await self.async_update()

    async def async_set_control_enabled(self, enabled: bool) -> None:
        """Enable or disable the automatic control of the target entity."""
        self._control_enabled = bool(enabled)
        if enabled:
            # Re-apply immediately so the appliance matches the plan again.
            self._applied_state = None
        await self.async_update()

    @callback
    def async_restore_settings(
        self,
        *,
        hours: float | None = None,
        threshold_price: float | None = None,
        control_enabled: bool | None = None,
    ) -> None:
        """Apply values restored by the entities, before control starts."""
        if hours is not None:
            self._hours = float(hours)
        if threshold_price is not None:
            self._threshold_price = float(threshold_price)
        if control_enabled is not None:
            self._control_enabled = bool(control_enabled)
        self.async_recalculate()

    # -- Listeners --------------------------------------------------------

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity callback, and return the removal callable."""
        self._listeners.append(update_callback)

        @callback
        def _remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove

    @callback
    def async_notify_listeners(self) -> None:
        """Push the current state to every entity of the plan."""
        for update_callback in list(self._listeners):
            update_callback()

    # -- Planning ---------------------------------------------------------

    @callback
    def async_recalculate(self, now: datetime | None = None) -> None:
        """Recompute the schedule from the latest prices."""
        moment = now or dt_util.utcnow()
        timezone = local_timezone()
        slots = self.coordinator.price_slots

        if self.uses_derived_hours:
            self._hours, self._hours_source = self._resolve_hours(moment, timezone)

        self.result = (
            build_plan(
                slots,
                moment,
                timezone,
                mode=self.mode,
                hours=self._hours,
                window_start=self.window_start,
                window_end=self.window_end,
                max_price=self.max_price,
                threshold_price=self._threshold_price,
                extra_hours=self.extra_hours,
                require_complete_data=self.require_complete_data,
            )
            if slots
            else None
        )

        self._fallback_window = None
        self._fallback_active = False

        if self.has_fallback and self._is_blind(moment):
            assert self.fallback_start is not None and self.fallback_end is not None
            window = period_bounds(
                moment, self.fallback_start, self.fallback_end, timezone
            )
            self._fallback_window = window
            self._fallback_active = window[0] <= moment < window[1]
            self._active = self._fallback_active
        elif self.result is None:
            self._active = None
        else:
            self._active = self.result.is_active(moment)

        if self.has_power_hold and self._active is not None:
            self._active = self._apply_power_hold(moment, self._active)

    def _apply_power_hold(self, moment: datetime, planned: bool) -> bool:
        """Extend the run past the planned window while power stays high.

        A water heater that still draws power has not finished heating: the
        plan keeps it on until its consumption drops, and never longer than
        the configured maximum.
        """
        power = self._power_reading()

        if planned:
            # Running on the plan: arm the hold for the end of the window.
            self._holding = False
            self._power_below_since = None
            self._hold_deadline = self._planned_end(moment) + timedelta(
                hours=self.power_hold_max
            )
            return True

        if self._hold_deadline is None:
            # The appliance was not running, there is nothing to extend.
            return False

        if moment >= self._hold_deadline:
            self._release_hold()
            return False

        if power is not None and power > self.power_threshold:
            self._power_below_since = None
            self._holding = True
            return True

        # Below the threshold, or unreadable: release once it has lasted.
        if self._power_below_since is None:
            self._power_below_since = moment
        if moment - self._power_below_since >= POWER_RELEASE_DELAY:
            self._release_hold()
            return False
        self._holding = True
        return True

    def _planned_end(self, moment: datetime) -> datetime:
        """Return when the appliance is scheduled to stop running."""
        if self._fallback_window is not None:
            return self._fallback_window[1]
        if self.result is not None:
            window = self.result.current_window(moment)
            if window is not None:
                return window.end
        return moment

    def _release_hold(self) -> None:
        """Stop holding the appliance on."""
        self._holding = False
        self._hold_deadline = None
        self._power_below_since = None

    def _power_reading(self) -> float | None:
        """Return the consumption of the power entity, in watts."""
        if not self.power_entity:
            return None
        state = self.hass.states.get(self.power_entity)
        if state is None:
            return self._unusable_power(f"{self.power_entity} does not exist")
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return self._unusable_power(f"{self.power_entity} is {state.state}")
        power = _as_float(state.state)
        if power is None:
            return self._unusable_power(
                f"{self.power_entity} is not a number: {state.state}"
            )
        if state.attributes.get("unit_of_measurement") == UnitOfPower.KILO_WATT:
            power *= 1000
        self._last_power_error = None
        self._power_now = power
        return power

    def _unusable_power(self, message: str) -> None:
        """Warn once about an unusable power entity, then stop holding."""
        if message != self._last_power_error:
            _LOGGER.warning(
                "Sobry plan %s cannot read the consumption of its appliance: %s",
                self.name,
                message,
            )
            self._last_power_error = message
        self._power_now = None
        return None

    def _resolve_hours(self, moment: datetime, zone: tzinfo) -> tuple[float, str]:
        """Return the runtime to schedule, and where it comes from."""
        if self.hours_entity:
            hours = self._entity_hours(self.hours_entity)
            if hours is not None:
                return hours, HOURS_SOURCE_ENTITY

        if self.hours_by_month:
            # The month of the window the plan is about, not of the current
            # instant: an overnight window belongs to the day it starts.
            period_start, _period_end = period_bounds(
                moment, self.window_start, self.window_end, zone
            )
            month = period_start.astimezone(zone).month
            if month in self.hours_by_month:
                return self.hours_by_month[month], HOURS_SOURCE_MONTH

        return self.configured_hours, HOURS_SOURCE_DEFAULT

    def _entity_hours(self, entity_id: str) -> float | None:
        """Return the runtime published by another entity, if it is usable."""
        state = self.hass.states.get(entity_id)
        if state is None:
            self._warn_unusable_hours(f"{entity_id} does not exist")
            return None
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._warn_unusable_hours(f"{entity_id} is {state.state}")
            return None
        hours = _as_float(state.state)
        if hours is None:
            self._warn_unusable_hours(f"{entity_id} is not a number: {state.state}")
            return None
        self._last_hours_error = None
        return min(max(hours, 0.0), MAX_HOURS)

    def _warn_unusable_hours(self, message: str) -> None:
        """Warn once about an unusable runtime source, before falling back."""
        if message == self._last_hours_error:
            return
        _LOGGER.warning(
            "Sobry plan %s cannot read its runtime: %s, falling back",
            self.name,
            message,
        )
        self._last_hours_error = message

    def _is_blind(self, moment: datetime) -> bool:
        """Return True when the plan has no usable schedule for right now.

        That is what the fixed fallback schedule is there for: an unreachable
        API, prices that stop before the current moment, or a window the plan
        could not fill.
        """
        if self.mode != MODE_THRESHOLD and self._hours <= 0:
            # A zero runtime is an explicit "do not run", not a failure.
            return False
        if self.result is None:
            return True
        if self.result.is_active(moment):
            return False
        if not self.result.windows:
            return True
        # Prices that do not cover the current moment cannot be trusted.
        return not any(slot.contains(moment) for slot in self.coordinator.price_slots)

    async def async_update(self, now: datetime | None = None) -> None:
        """Recompute the schedule, refresh the entities and drive the target."""
        self.async_recalculate(now)
        # Publish the new schedule before driving the appliance: a slow or
        # unresponsive device must not hold the entity states back.
        self.async_notify_listeners()
        await self.async_apply_control()

    async def async_start_control(self) -> None:
        """Start driving the target entity once the entities are set up."""
        self._control_started = True
        await self.async_update()

    async def async_apply_control(self) -> None:
        """Turn the target entity on or off when the plan says so."""
        if not self._control_started or not self.control_enabled:
            return
        if not self.target_entity or self._active is None:
            return
        if self._active is self._applied_state:
            return

        service = SERVICE_TURN_ON if self._active else SERVICE_TURN_OFF
        try:
            await self.hass.services.async_call(
                "homeassistant",
                service,
                {ATTR_ENTITY_ID: self.target_entity},
                blocking=True,
            )
        except (HomeAssistantError, ValueError) as err:
            message = str(err)
            if message != self._last_control_error:
                _LOGGER.warning(
                    "Sobry plan %s could not call %s on %s: %s",
                    self.name,
                    service,
                    self.target_entity,
                    err,
                )
                self._last_control_error = message
            # Leave the applied state unknown so the call is retried.
            self._applied_state = None
            return

        self._last_control_error = None
        self._applied_state = self._active
        _LOGGER.debug(
            "Sobry plan %s called %s on %s", self.name, service, self.target_entity
        )

    # -- Presentation -----------------------------------------------------

    @property
    def next_start(self) -> datetime | None:
        """Return the start of the next planned run."""
        now = dt_util.utcnow()
        if self._fallback_window is not None:
            start = self._fallback_window[0]
            return start if start > now else None
        if self.result is None:
            return None
        window = self.result.next_window(now)
        return window.start if window else None

    @property
    def next_end(self) -> datetime | None:
        """Return the end of the running window, else of the next one."""
        now = dt_util.utcnow()
        if self._holding:
            # The appliance stops when its consumption drops, at the latest
            # on the deadline.
            return self._hold_deadline
        if self._fallback_window is not None:
            return self._fallback_window[1]
        if self.result is None:
            return None
        current = self.result.current_window(now)
        if current is not None:
            return current.end
        window = self.result.next_window(now)
        return window.end if window else None

    @property
    def average_price(self) -> float | None:
        """Return the average price of the planned slots."""
        if self._fallback_window is not None:
            # The fixed schedule runs whatever the price is.
            return None
        return self.result.average_price if self.result else None

    def as_attributes(self) -> dict[str, Any]:
        """Return the plan details exposed on the binary sensor."""
        tz = local_timezone()
        next_start = self.next_start
        next_end = self.next_end
        attributes: dict[str, Any] = {
            "mode": self.mode,
            "control_enabled": self._control_enabled,
            "target_entity": self.target_entity,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "requested_hours": round(self._hours, 3),
            "hours_source": self._hours_source,
            "extra_hours": round(self.extra_hours, 3),
            "fallback_active": self._fallback_active,
            "next_start": next_start.astimezone(tz).isoformat() if next_start else None,
            "next_end": next_end.astimezone(tz).isoformat() if next_end else None,
        }
        if self.hours_entity:
            attributes["hours_entity"] = self.hours_entity
        if self.has_power_hold:
            attributes["power_entity"] = self.power_entity
            attributes["power_threshold"] = self.power_threshold
            attributes["power_hold_active"] = self._holding
            attributes["power_now"] = self._power_now
            attributes["power_hold_until"] = (
                self._hold_deadline.astimezone(tz).isoformat()
                if self._holding and self._hold_deadline
                else None
            )
        if self.has_fallback and self.fallback_start and self.fallback_end:
            attributes["fallback_start"] = self.fallback_start.isoformat()
            attributes["fallback_end"] = self.fallback_end.isoformat()
        if self.mode == MODE_THRESHOLD:
            attributes["threshold_price"] = self._threshold_price
        else:
            attributes["max_price"] = self.max_price

        if self._fallback_window is not None:
            # The fixed schedule is in charge: report it as the planned window.
            fallback = PlanWindow(*self._fallback_window, 0.0)
            attributes["planned_hours"] = round(fallback.hours, 3)
            attributes["planned_average_price"] = None
            attributes["slots"] = []
            attributes["windows"] = [
                {
                    "start": fallback.start.astimezone(tz).isoformat(),
                    "end": fallback.end.astimezone(tz).isoformat(),
                    "hours": round(fallback.hours, 3),
                }
            ]
        elif self.result is not None:
            attributes["planned_hours"] = round(self.result.scheduled_hours, 3)
            attributes["planned_average_price"] = (
                round(self.result.average_price, 6)
                if self.result.average_price is not None
                else None
            )
            attributes["slots"] = [slot.as_dict(tz) for slot in self.result.selected]
            attributes["windows"] = [
                window.as_dict(tz) for window in self.result.windows
            ]

        if self.result is None:
            attributes["reason"] = "no_prices"
            return attributes

        attributes["period_start"] = self.result.period_start.astimezone(tz).isoformat()
        attributes["period_end"] = self.result.period_end.astimezone(tz).isoformat()
        attributes["data_complete"] = self.result.data_complete
        if self.result.reason:
            attributes["reason"] = self.result.reason
        return attributes


def plan_configs(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the plan configurations stored in the config entry options."""
    plans = entry.options.get(CONF_PLANS) or []
    return [dict(plan) for plan in plans if isinstance(plan, dict) and plan.get(CONF_PLAN_ID)]


@dataclass
class SobryRuntimeData:
    """Everything a config entry keeps alive while it is loaded."""

    coordinator: SobryDataUpdateCoordinator
    plans: list[SobryPlan]

    def plan(self, plan_id: str) -> SobryPlan | None:
        """Return a plan by id."""
        return next((plan for plan in self.plans if plan.plan_id == plan_id), None)

    @callback
    def async_handle_coordinator_update(self) -> None:
        """Replan as soon as new prices are available."""
        for plan in self.plans:
            plan.async_recalculate()
            plan.async_notify_listeners()
        if self.plans:
            self.coordinator.hass.async_create_task(self.async_apply_control())

    async def async_apply_control(self) -> None:
        """Drive every target entity towards its planned state."""
        for plan in self.plans:
            await plan.async_apply_control()

    async def async_handle_hours_entity_change(self, event: Event) -> None:
        """Replan the plans reading their runtime from the changed entity."""
        entity_id = event.data["entity_id"]
        for plan in self.plans:
            if plan.hours_entity == entity_id:
                await plan.async_update()

    async def async_tick(self, now: datetime) -> None:
        """Re-evaluate every plan, called once a minute."""
        for plan in self.plans:
            await plan.async_update(now)

    async def async_start_control(self) -> None:
        """Let the plans drive their target entity."""
        for plan in self.plans:
            await plan.async_start_control()
