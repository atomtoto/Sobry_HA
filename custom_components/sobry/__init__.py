"""The Sobry integration."""

from __future__ import annotations

import logging
from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.typing import ConfigType

from .const import CONF_PLAN_ID, CONF_PLANS, DOMAIN, NAME, SUBENTRY_APPLIANCE
from .coordinator import SobryDataUpdateCoordinator
from .plan import SobryConfigEntry, SobryPlan, SobryRuntimeData, plan_configs
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services, available whether an entry is loaded or not."""
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Move the appliances from the options to subentries."""
    if entry.version > 2:
        # Set up by a newer release: its data cannot be read here.
        return False

    if entry.version == 1:
        device_registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)
        plans = [
            plan
            for plan in entry.options.get(CONF_PLANS) or []
            if isinstance(plan, dict) and plan.get(CONF_PLAN_ID)
        ]
        for plan in plans:
            plan_id = plan[CONF_PLAN_ID]
            # The plan id becomes the subentry id: the unique ids of the
            # entities, built on it, are kept along with their history.
            hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(
                        {key: value for key, value in plan.items() if key != CONF_PLAN_ID}
                    ),
                    subentry_id=plan_id,
                    subentry_type=SUBENTRY_APPLIANCE,
                    title=plan.get(CONF_NAME) or NAME,
                    unique_id=None,
                ),
            )
            base_id = f"{entry.entry_id}_{plan_id}"
            device = device_registry.async_get_device(identifiers={(DOMAIN, base_id)})
            if device is not None:
                # Linked to the subentry first: a device left without any
                # config entry would be removed.
                device_registry.async_update_device(
                    device.id,
                    add_config_entry_id=entry.entry_id,
                    add_config_subentry_id=plan_id,
                )
            # The entities move before the device leaves the entry itself:
            # entities still linked to it would be removed along with it.
            for entity in er.async_entries_for_config_entry(
                entity_registry, entry.entry_id
            ):
                if entity.unique_id.startswith(f"{base_id}_"):
                    entity_registry.async_update_entity(
                        entity.entity_id, config_subentry_id=plan_id
                    )
            if device is not None:
                device_registry.async_update_device(
                    device.id,
                    remove_config_entry_id=entry.entry_id,
                    remove_config_subentry_id=None,
                )

        options = {key: value for key, value in entry.options.items() if key != CONF_PLANS}
        hass.config_entries.async_update_entry(entry, options=options, version=2)
        _LOGGER.debug("Moved %s Sobry appliances to subentries", len(plans))

    return True


async def async_setup_entry(hass: HomeAssistant, entry: SobryConfigEntry) -> bool:
    """Set up Sobry from a config entry."""
    coordinator = SobryDataUpdateCoordinator(hass, entry)
    configs = plan_configs(entry)

    if configs:
        # Appliances must be driven even when the API cannot be reached: the
        # entities are set up anyway, and their fixed fallback schedule takes
        # over until prices come back.
        await coordinator.async_refresh()
    else:
        await coordinator.async_config_entry_first_refresh()

    plans = [SobryPlan(hass, entry, coordinator, config) for config in configs]
    for plan in plans:
        plan.async_recalculate()
        # A deleted or reconfigured appliance leaves no repair behind.
        entry.async_on_unload(plan.async_clear_issue)

    runtime = SobryRuntimeData(coordinator=coordinator, plans=plans)
    entry.runtime_data = runtime

    # Created first: the appliance devices are linked to it.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **coordinator.device_info
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Replan on new prices, and once a minute so slot boundaries are honoured
    # even between two API refreshes.
    entry.async_on_unload(
        coordinator.async_add_listener(runtime.async_handle_coordinator_update)
    )
    entry.async_on_unload(async_track_time_change(hass, runtime.async_tick, second=0))

    # A runtime read from another entity has to be followed as it changes.
    tracked = sorted({plan.hours_entity for plan in plans if plan.hours_entity})
    if tracked:
        entry.async_on_unload(
            async_track_state_change_event(
                hass, tracked, runtime.async_handle_hours_entity_change
            )
        )

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # Entities are set up (and their restored settings applied), the plans can
    # now drive their target entity.
    await runtime.async_start_control()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SobryConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_update_options(hass: HomeAssistant, entry: SobryConfigEntry) -> None:
    """Reload the entry so setting and appliance changes are applied."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: SobryConfigEntry, device: DeviceEntry
) -> bool:
    """Allow removing the device of a plan that no longer exists."""
    known = {(DOMAIN, entry.entry_id)} | {
        (DOMAIN, f"{entry.entry_id}_{config[CONF_PLAN_ID]}")
        for config in plan_configs(entry)
    }
    return not known.intersection(device.identifiers)
