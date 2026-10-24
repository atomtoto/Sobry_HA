"""Switches letting the user take over a Sobry plan."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .entity import SobryPlanEntity
from .plan import SobryPlan, SobryRuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switches of every plan."""
    runtime: SobryRuntimeData = hass.data[DOMAIN][entry.entry_id]
    entities: list[SobryPlanEntity] = []
    for plan in runtime.plans:
        # Forcing works without an entity to control: the binary sensor follows.
        entities.append(SobryPlanBoostSwitch(plan))
        if plan.target_entity:
            entities.append(SobryPlanControlSwitch(plan))
    async_add_entities(entities)


class SobryPlanControlSwitch(SobryPlanEntity, SwitchEntity, RestoreEntity):
    """Turn the automatic control of the target entity on or off."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "plan_control"

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the control switch."""
        super().__init__(plan, "control")

    async def async_added_to_hass(self) -> None:
        """Restore whether the user had disabled the automatic control."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == STATE_OFF:
            self._plan.async_restore_settings(control_enabled=False)

    @property
    def is_on(self) -> bool:
        """Return whether the plan may drive its target entity."""
        return self._plan.control_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Resume the automatic control and apply the plan right away."""
        await self._plan.async_set_control_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop driving the target entity, leaving it as it is."""
        await self._plan.async_set_control_enabled(False)


class SobryPlanBoostSwitch(SobryPlanEntity, SwitchEntity, RestoreEntity):
    """Run the appliance now, whatever the plan says, until switched off."""

    _attr_translation_key = "plan_boost"

    def __init__(self, plan: SobryPlan) -> None:
        """Initialise the boost switch."""
        super().__init__(plan, "boost")

    async def async_added_to_hass(self) -> None:
        """Restore whether the appliance was being forced on."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == STATE_ON:
            self._plan.async_restore_settings(boost=True)

    @property
    def is_on(self) -> bool:
        """Return whether the appliance is forced on."""
        return self._plan.boost

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Force the appliance on right away."""
        await self._plan.async_set_boost(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hand the appliance back to the plan right away."""
        await self._plan.async_set_boost(False)
