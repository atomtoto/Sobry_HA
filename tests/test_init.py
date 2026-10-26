"""Tests for setting up, unloading and migrating the Sobry entry."""

from __future__ import annotations

from datetime import timedelta

from aiohttp import ClientError
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.sobry.const import DOMAIN

from .common import (
    API_KEY_URL,
    PUBLIC_SETTINGS,
    PUBLIC_URL,
    WATER_HEATER,
    public_payload,
    setup_entry,
    sobry_entry,
)


async def test_setup_and_unload(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """The entry loads, and unloads leaving the service in place."""
    entry = sobry_entry(WATER_HEATER)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, "get_cheapest_slots")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.services.has_service(DOMAIN, "get_cheapest_slots")
    assert hass.states.get("sensor.sobry_current_price").state == STATE_UNAVAILABLE


async def test_devices(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """Prices sit on a Sobry device, each appliance on its own linked to it."""
    entry = sobry_entry(WATER_HEATER)
    await setup_entry(hass, entry)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    main = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert main is not None
    assert main.name == "Sobry"
    assert main.entry_type is dr.DeviceEntryType.SERVICE

    heater = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_plan1")}
    )
    assert heater is not None
    assert heater.name == "Water heater"
    assert heater.via_device_id == main.id
    assert heater.config_entries_subentries == {entry.entry_id: {"plan1"}}

    price = entity_registry.async_get("sensor.sobry_current_price")
    assert price.device_id == main.id
    assert price.config_subentry_id is None
    active = entity_registry.async_get("binary_sensor.water_heater")
    assert active.device_id == heater.id
    assert active.config_subentry_id == "plan1"


async def test_not_ready_without_appliance(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without an appliance to drive, an unreachable API retries the setup."""
    aioclient_mock.get(PUBLIC_URL, exc=ClientError())
    entry = sobry_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_loaded_with_appliance_when_api_is_down(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """Appliances are set up anyway, so their fallback can drive them."""
    aioclient_mock.get(PUBLIC_URL, status=503, text="<html>Service Unavailable</html>")
    entry = sobry_entry(
        {**WATER_HEATER, "fallback_start": "10:00:00", "fallback_end": "12:00:00"}
    )
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get("binary_sensor.water_heater").state == "on"
    fallback = hass.states.get("binary_sensor.water_heater_fixed_schedule_fallback")
    assert fallback.state == "on"
    assert fallback.attributes["reason"] == "no_prices"
    assert hass.states.get("sensor.sobry_current_price").state == STATE_UNAVAILABLE


async def test_reauth_on_refused_key(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused key asks for a new one, even when the answer is not JSON."""
    aioclient_mock.get(API_KEY_URL, status=401, text="Unauthorized")
    entry = sobry_entry(data={**PUBLIC_SETTINGS, "api_key": "revoked"})
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_reauth_keeps_appliances_running(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """With appliances, the entry stays loaded while a new key is asked."""
    aioclient_mock.get(API_KEY_URL, status=401, json={"error": "INVALID_API_KEY"})
    entry = sobry_entry(WATER_HEATER, data={**PUBLIC_SETTINGS, "api_key": "revoked"})
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_prices_survive_a_failed_refresh(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """The day-ahead prices stay shown while the API is briefly down."""
    aioclient_mock.get(PUBLIC_URL, json=public_payload())
    entry = sobry_entry()
    await setup_entry(hass, entry)
    assert hass.states.get("sensor.sobry_current_price").state == "0.05"

    aioclient_mock.clear_requests()
    aioclient_mock.get(PUBLIC_URL, exc=TimeoutError())
    paris.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert not entry.runtime_data.coordinator.last_update_success
    assert hass.states.get("sensor.sobry_current_price").state == "0.05"


async def test_migrate_appliances_from_options(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """Appliances of 0.8 move to subentries, keeping entity ids and devices."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sobry Energy",
        unique_id="sobry",
        version=1,
        data=PUBLIC_SETTINGS,
        options={
            "granularity": "hourly",
            "plans": [{**WATER_HEATER, "plan_id": "abc123"}],
        },
    )
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_abc123")},
        name="Water heater",
    )
    entity_registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_abc123_active",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="my_water_heater",
    )

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.options == {"granularity": "hourly"}
    subentry = entry.subentries["abc123"]
    assert subentry.title == "Water heater"
    assert "plan_id" not in subentry.data

    active = entity_registry.async_get("binary_sensor.my_water_heater")
    assert active.config_subentry_id == "abc123"
    assert hass.states.get("binary_sensor.my_water_heater").state == "on"
    device = device_registry.async_get(device.id)
    assert device.config_entries_subentries == {entry.entry_id: {"abc123"}}


async def test_entry_from_a_newer_release(hass: HomeAssistant) -> None:
    """An entry set up by a newer release is not touched."""
    entry = MockConfigEntry(domain=DOMAIN, version=3, data=PUBLIC_SETTINGS)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_appliance_device_cannot_be_removed_while_configured(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """Only the device of an appliance that no longer exists can be removed."""
    entry = sobry_entry(WATER_HEATER)
    await setup_entry(hass, entry)
    device_registry = dr.async_get(hass)

    from custom_components.sobry import async_remove_config_entry_device

    for identifier in (entry.entry_id, f"{entry.entry_id}_plan1"):
        device = device_registry.async_get_device(identifiers={(DOMAIN, identifier)})
        assert not await async_remove_config_entry_device(hass, entry, device)

    stale = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_gone")},
    )
    assert await async_remove_config_entry_device(hass, entry, stale)
