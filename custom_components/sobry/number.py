"""Numbers tuning a Sobry plan without going through the options flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import DOMAIN, MAX_HOURS, MAX_PRICE_LIMIT, MODE_THRESHOLD
from .entity import SobryPlanEntity
from .plan import SobryPlan, SobryRuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the tunable settings of every plan."""
    runtime: SobryRuntimeData = hass.data[DOMAIN][entry.entry_id]
    for plan in runtime.plans:
        entity: SobryPlanEntity
        if plan.mode == MODE_THRESHOLD:
            entity = SobryPlanThresholdNumber(plan)
        elif not plan.uses_derived_hours:
            # A runtime driven by a month table or an entity is reported by a
            # sensor instead, so there is a single source of truth.
            entity = SobryPlanHoursNumber(plan)
        else:
            continue
        async_add_entities([entity], config_subentry_id=plan.plan_id)


@dataclass
class SobryTunableExtraData(ExtraStoredData):
    """Value set from the UI, together with the option it was tuned from."""

    value: float | None
    configured: float | None

    def as_dict(self) -> dict[str, Any]:
        """Return the stored representation."""
        return {"value": self.value, "configured": self.configured}


class SobryPlanTunableNumber(SobryPlanEntity, RestoreEntity, NumberEntity):
    """A plan setting that can be tuned from a dashboard and survives restarts."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, plan: SobryPlan, key: str, configured: float | None) -> None:
        """Remember the option the entity was created from."""
        super().__init__(plan, key)
        self._configured = configured

    @property
    def extra_restore_data(self) -> SobryTunableExtraData:
        """Return the value to restore on the next start."""
        return SobryTunableExtraData(self.native_value, self._configured)

    async def async_added_to_hass(self) -> None:
        """Restore the last value, unless the option itself changed."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_extra_data()
        if last_data is None:
            return
        stored = last_data.as_dict()
        value = stored.get("value")
        # An edit of the plan options wins over a value tuned from the UI.
        if value is None or stored.get("configured") != self._configured:
            return
        self._async_restore_value(float(value))

    def _async_restore_value(self, value: float) -> None:
        """Push the restored value back into the plan."""
        raise NotImplementedError


class SobryPlanHoursNumber(SobryPlanTunableNumber):
    """Daily runtime the plan has to schedule."""

    _attr_translation_key = "plan_hours"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_HOURS
    _attr_native_step = 0.25
    _attr_native_unit_of_measurement = UnitOfTime.HOURS

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the runtime number."""
        super().__init__(plan, "hours", plan.configured_hours)

    def _async_restore_value(self, value: float) -> None:
        """Restore the runtime last set by the user."""
        self._plan.async_restore_settings(hours=value)

    @property
    def native_value(self) -> float:
        """Return the scheduled runtime."""
        return self._plan.hours

    async def async_set_native_value(self, value: float) -> None:
        """Change the scheduled runtime and replan."""
        await self._plan.async_set_hours(value)


class SobryPlanThresholdNumber(SobryPlanTunableNumber):
    """Price under which the appliance is allowed to run."""

    _attr_translation_key = "plan_threshold_price"
    _attr_native_min_value = -MAX_PRICE_LIMIT
    _attr_native_max_value = MAX_PRICE_LIMIT
    _attr_native_step = 0.001
    _attr_native_unit_of_measurement = f"{CURRENCY_EURO}/kWh"

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the threshold number."""
        super().__init__(plan, "threshold_price", plan.configured_threshold_price)

    def _async_restore_value(self, value: float) -> None:
        """Restore the threshold last set by the user."""
        self._plan.async_restore_settings(threshold_price=value)

    @property
    def native_value(self) -> float | None:
        """Return the price threshold."""
        return self._plan.threshold_price

    async def async_set_native_value(self, value: float) -> None:
        """Change the price threshold and replan."""
        await self._plan.async_set_threshold_price(value)
