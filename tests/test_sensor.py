"""Tests for the price sensors."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.sobry.const import DOMAIN

from .common import NOW, day_price, setup_entry, sobry_entry


async def test_price_sensors(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """The prices of the slot in force and of the day."""
    await setup_entry(hass, sobry_entry())

    current = hass.states.get("sensor.sobry_current_price")
    assert current.state == "0.05"
    assert current.attributes["friendly_name"] == "Sobry Current price"
    assert current.attributes[ATTR_UNIT_OF_MEASUREMENT] == "€/kWh"
    assert current.attributes[ATTR_STATE_CLASS] is SensorStateClass.MEASUREMENT
    assert current.attributes["current_timestamp"] == "2026-01-15T09:00:00+00:00"
    assert current.attributes["next_price"] == 0.06
    assert current.attributes["spot_price"] == 42.0
    # Today and tomorrow, not yesterday.
    assert len(current.attributes["all_prices"]) == 48

    assert hass.states.get("sensor.sobry_next_price").state == "0.06"
    assert hass.states.get("sensor.sobry_minimum_price").state == "0.05"
    assert hass.states.get("sensor.sobry_maximum_price").state == str(day_price(23))
    average = sum(day_price(hour) for hour in range(24)) / 24
    assert float(hass.states.get("sensor.sobry_average_price").state) == round(
        average, 6
    )
    assert ATTR_STATE_CLASS not in hass.states.get("sensor.sobry_next_price").attributes


async def test_price_follows_the_slots(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """The current price changes on the slot boundary, between two refreshes."""
    await setup_entry(hass, sobry_entry(pref_disable_polling=True))
    calls = public_api.call_count

    paris.move_to(NOW.replace(hour=11, minute=0))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.sobry_current_price").state == "0.06"
    assert hass.states.get("sensor.sobry_next_price").state == "0.07"
    assert public_api.call_count == calls


async def test_names_are_translated(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """In French the names are translated, existing entity ids are kept."""
    hass.config.language = "fr"
    entry = sobry_entry()
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_current_price",
        config_entry=entry,
        suggested_object_id="sobry_current_price",
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.sobry_current_price")
    assert state.attributes["friendly_name"] == "Sobry Prix actuel"
    # A new entity takes its id from the French name, as appliances do.
    state = hass.states.get("sensor.sobry_prix_minimum")
    assert state.attributes["friendly_name"] == "Sobry Prix minimum"
