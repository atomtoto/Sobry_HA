"""Binary sensor telling whether a Sobry plan wants its device running."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SobryPlanEntity
from .plan import SobryPlan, SobryRuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one binary sensor per configured plan."""
    runtime: SobryRuntimeData = hass.data[DOMAIN][entry.entry_id]
    entities: list[SobryPlanEntity] = []
    for plan in runtime.plans:
        entities.append(SobryPlanBinarySensor(plan))
        if plan.has_fallback:
            entities.append(SobryPlanFallbackBinarySensor(plan))
    async_add_entities(entities)


class SobryPlanBinarySensor(SobryPlanEntity, BinarySensorEntity):
    """On while the plan schedules the appliance to run."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = None
    # Up to two days of slots, over the recorder size limit: kept in the state
    # only, the windows sum them up in the history.
    _unrecorded_attributes = frozenset({"slots"})

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the main entity of the plan device."""
        super().__init__(plan, "active")

    @property
    def available(self) -> bool:
        """Return whether prices are known."""
        return self._plan.available

    @property
    def is_on(self) -> bool | None:
        """Return whether the appliance should be running now."""
        return self._plan.is_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the planned slots and the plan settings."""
        return self._plan.as_attributes()


class SobryPlanFallbackBinarySensor(SobryPlanEntity, BinarySensorEntity):
    """On while the fixed schedule has taken over from the prices."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "plan_fallback"

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the fallback indicator."""
        super().__init__(plan, "fallback")

    @property
    def is_on(self) -> bool:
        """Return whether the plan runs on its fixed schedule."""
        return self._plan.fallback_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return why the fixed schedule took over."""
        attributes = self._plan.as_attributes()
        return {
            key: attributes.get(key)
            for key in ("reason", "data_complete", "fallback_start", "fallback_end")
            if key in attributes
        }
