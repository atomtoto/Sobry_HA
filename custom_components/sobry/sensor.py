"""Sensor platform for Sobry integration."""

from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MODE_THRESHOLD
from .coordinator import SobryDataUpdateCoordinator
from .entity import SobryPlanEntity
from .plan import SobryPlan, SobryRuntimeData

# Statistics of the prices of the current day.
_DAY_STATISTICS = {"min_price": min, "max_price": max, "average_price": mean}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Sobry sensor entities."""
    runtime: SobryRuntimeData = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator

    async_add_entities(
        [
            SobryPriceSensor(coordinator, entry, "current_price", "Current price"),
            SobryPriceSensor(coordinator, entry, "next_price", "Next price"),
            SobryPriceSensor(coordinator, entry, "min_price", "Minimum price"),
            SobryPriceSensor(coordinator, entry, "max_price", "Maximum price"),
            SobryPriceSensor(coordinator, entry, "average_price", "Average price"),
        ]
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

    _attr_native_unit_of_measurement = f"{CURRENCY_EURO}/kWh"
    _attr_should_poll = False
    # Hundreds of entries, over the recorder size limit: kept in the state
    # only, not in the history.
    _unrecorded_attributes = frozenset({"all_prices"})

    def __init__(
        self,
        coordinator: SobryDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"Sobry {name}"
        self._attr_has_entity_name = True

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
    def native_value(self) -> float | None:
        """Return the sensor state, for the slot in force right now."""
        now = dt_util.utcnow()
        if self._key == "current_price":
            slot = self.coordinator.slot_at(now)
            value = slot.price if slot else None
        elif self._key == "next_price":
            slot = self.coordinator.slot_after(now)
            value = slot.price if slot else None
        else:
            prices = [slot.price for slot in self.coordinator.day_slots(now)]
            value = _DAY_STATISTICS[self._key](prices) if prices else None
        if value is None:
            return None
        return round(float(value), 6)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes for the primary sensor."""
        if self._key != "current_price":
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

    _attr_native_unit_of_measurement = f"{CURRENCY_EURO}/kWh"
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
