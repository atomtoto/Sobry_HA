"""Sensor platform for Sobry integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import MODE_THRESHOLD
from .coordinator import SobryDataUpdateCoordinator
from .entity import SobryPlanEntity
from .plan import SobryConfigEntry, SobryPlan
from .planner import PriceSlot

PRICE_UNIT = f"{CURRENCY_EURO}/kWh"


def _slot_price(slot: PriceSlot | None) -> float | None:
    """Return the price of a slot, if there is one."""
    return slot.price if slot else None


def _day_statistic(
    statistic: Callable[[list[float]], float],
) -> Callable[[SobryDataUpdateCoordinator, datetime], float | None]:
    """Return a statistic of the prices of the current day."""

    def _value(coordinator: SobryDataUpdateCoordinator, now: datetime) -> float | None:
        prices = [slot.price for slot in coordinator.day_slots(now)]
        return statistic(prices) if prices else None

    return _value


@dataclass(frozen=True, kw_only=True)
class SobryPriceSensorDescription(SensorEntityDescription):
    """A price computed from the series, at a given moment."""

    value_fn: Callable[[SobryDataUpdateCoordinator, datetime], float | None]


PRICE_SENSORS: tuple[SobryPriceSensorDescription, ...] = (
    SobryPriceSensorDescription(
        key="current_price",
        translation_key="current_price",
        # Long-term statistics keep the price history past the recorder.
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator, now: _slot_price(coordinator.slot_at(now)),
    ),
    SobryPriceSensorDescription(
        key="next_price",
        translation_key="next_price",
        value_fn=lambda coordinator, now: _slot_price(coordinator.slot_after(now)),
    ),
    SobryPriceSensorDescription(
        key="min_price", translation_key="min_price", value_fn=_day_statistic(min)
    ),
    SobryPriceSensorDescription(
        key="max_price", translation_key="max_price", value_fn=_day_statistic(max)
    ),
    SobryPriceSensorDescription(
        key="average_price",
        translation_key="average_price",
        value_fn=_day_statistic(mean),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SobryConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Sobry sensor entities."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator

    async_add_entities(
        SobryPriceSensor(coordinator, description) for description in PRICE_SENSORS
    )

    for plan in runtime.plans:
        entities: list[Entity] = [
            SobryPlanNextStartSensor(plan),
            SobryPlanNextEndSensor(plan),
            SobryPlanAveragePriceSensor(plan),
        ]
        if plan.mode != MODE_THRESHOLD and plan.uses_derived_hours:
            entities.append(SobryPlanRuntimeSensor(plan))
        async_add_entities(entities, config_subentry_id=plan.plan_id)


class SobryPriceSensor(CoordinatorEntity[SobryDataUpdateCoordinator], SensorEntity):
    """Representation of a Sobry price sensor."""

    entity_description: SobryPriceSensorDescription
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PRICE_UNIT
    _attr_suggested_display_precision = 4
    # Hundreds of entries, over the recorder size limit: kept in the state
    # only, not in the history.
    _unrecorded_attributes = frozenset({"all_prices"})

    def __init__(
        self,
        coordinator: SobryDataUpdateCoordinator,
        description: SobryPriceSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        self._attr_device_info = coordinator.device_info

    async def async_added_to_hass(self) -> None:
        """Follow the slot boundaries, not only the API refreshes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._async_slot_changed, minute=[0, 15, 30, 45], second=0
            )
        )

    @callback
    def _async_slot_changed(self, _now: datetime) -> None:
        """Publish the price of the slot that just started."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the price is known.

        Day-ahead prices stay right when a refresh fails: the sensor keeps
        showing them rather than going unavailable on a brief API outage.
        """
        return super().available or self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the sensor state, for the slot in force right now."""
        value = self.entity_description.value_fn(self.coordinator, dt_util.utcnow())
        if value is None:
            return None
        return round(float(value), 6)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes for the primary sensor."""
        if self.entity_description.key != "current_price":
            return None

        now = dt_util.utcnow()
        data = self.coordinator.data or {}
        current = self.coordinator.price_entry(self.coordinator.slot_at(now))
        upcoming = self.coordinator.slot_after(now)

        return {
            "current_timestamp": current.get("timestamp"),
            "current_date": current.get("date"),
            "current_time": current.get("time"),
            "price": current.get("price"),
            "spot_price": current.get("spot_price"),
            "spot_price_eur_kwh": current.get("spot_price_eur_kwh"),
            "price_ht_eur_kwh": current.get("price_ht_eur_kwh"),
            "price_ttc_eur_kwh": current.get("price_ttc_eur_kwh"),
            "next_price": round(upcoming.price, 6) if upcoming else None,
            "pricing_metadata": data.get("pricing_metadata", {}),
            "statistics": data.get("statistics", {}),
            "count": data.get("count"),
            "all_prices": data.get("prices", []),
        }


class SobryPlanSensor(SobryPlanEntity, SensorEntity):
    """Base sensor exposing a value computed by a plan."""

    @property
    def available(self) -> bool:
        """Return whether prices are known."""
        return self._plan.available


class SobryPlanNextStartSensor(SobryPlanSensor):
    """Next moment the appliance is scheduled to start."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "plan_next_start"

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the next start sensor."""
        super().__init__(plan, "next_start")

    @property
    def native_value(self) -> datetime | None:
        """Return the start of the next planned run."""
        return self._plan.next_start


class SobryPlanNextEndSensor(SobryPlanSensor):
    """End of the running window, or of the next planned one."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "plan_next_end"

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the next end sensor."""
        super().__init__(plan, "next_end")

    @property
    def native_value(self) -> datetime | None:
        """Return when the appliance is scheduled to stop."""
        return self._plan.next_end


class SobryPlanAveragePriceSensor(SobryPlanSensor):
    """Average price of the slots the plan selected."""

    _attr_native_unit_of_measurement = PRICE_UNIT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "plan_average_price"
    _attr_suggested_display_precision = 4

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the planned price sensor."""
        super().__init__(plan, "average_price")

    @property
    def native_value(self) -> float | None:
        """Return the average price of the planned slots."""
        value = self._plan.average_price
        return None if value is None else round(value, 6)


class SobryPlanRuntimeSensor(SobryPlanSensor):
    """Runtime the plan schedules, when it is not set by hand."""

    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_translation_key = "plan_runtime"

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the runtime sensor."""
        super().__init__(plan, "runtime")

    @property
    def native_value(self) -> float:
        """Return the runtime currently scheduled."""
        return self._plan.hours

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return where the runtime comes from."""
        attributes: dict[str, Any] = {"source": self._plan.hours_source}
        if self._plan.hours_entity:
            attributes["hours_entity"] = self._plan.hours_entity
        return attributes
