"""Tests for the sobry.get_cheapest_slots service."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.sobry.const import DOMAIN

from .common import setup_entry, sobry_entry


async def _call(hass: HomeAssistant, **data) -> dict:
    return await hass.services.async_call(
        DOMAIN, "get_cheapest_slots", data, blocking=True, return_response=True
    )


async def test_cheapest_slots(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """The cheapest hours of a window, with a fixed extension."""
    entry = sobry_entry()
    await setup_entry(hass, entry)

    response = await _call(
        hass,
        config_entry_id=entry.entry_id,
        hours=2,
        extra_hours=1,
        window_start="08:00:00",
        window_end="12:00:00",
    )
    assert response["period_start"] == "2026-01-15T08:00:00+01:00"
    assert response["period_end"] == "2026-01-15T12:00:00+01:00"
    assert response["data_complete"] is True
    assert response["active"] is True
    assert [slot["start"] for slot in response["slots"]][:3] == [
        "2026-01-15T09:00:00+01:00",
        "2026-01-15T10:00:00+01:00",
        "2026-01-15T11:00:00+01:00",
    ]
    assert response["windows"][0] == {
        "start": "2026-01-15T09:00:00+01:00",
        "end": "2026-01-15T12:00:00+01:00",
        "hours": 3.0,
        "price": 0.056667,
    }
    assert response["forced_slots"] == []
    assert "reason" not in response


async def test_force_price(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """Slots under the force price come on top, whatever the window."""
    await setup_entry(hass, sobry_entry())
    response = await _call(
        hass, hours=1, window_start="20:00:00", window_end="22:00:00", force_price=0.05
    )
    assert [slot["start"] for slot in response["forced_slots"]] == [
        "2026-01-15T10:00:00+01:00",
        "2026-01-16T10:00:00+01:00",
    ]


async def test_without_a_loaded_entry(hass: HomeAssistant) -> None:
    """The service is there, but needs a loaded entry."""
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass)
    assert err.value.translation_key == "entry_not_loaded"

    with pytest.raises(ServiceValidationError):
        await _call(hass, config_entry_id="unknown")


async def test_invalid_values(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """Values out of range are refused."""
    await setup_entry(hass, sobry_entry())
    with pytest.raises(vol.Invalid):
        await _call(hass, hours=30)
    with pytest.raises(vol.Invalid):
        await _call(hass, mode="random")
