"""Tests for the diagnostics."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant

from custom_components.sobry.diagnostics import async_get_config_entry_diagnostics

from .common import API_KEY_URL, WATER_HEATER, public_payload, setup_entry, sobry_entry


async def test_diagnostics(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """The settings, prices and plans, without the API key."""
    aioclient_mock.get(API_KEY_URL, json=public_payload())
    entry = sobry_entry(
        WATER_HEATER,
        data={"api_key": "secret", "tax_mode": "ttc", "granularity": "hourly"},
        options={"api_key": "other"},
    )
    await setup_entry(hass, entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"]["data"]["api_key"] == "**REDACTED**"
    assert diagnostics["entry"]["options"]["api_key"] == "**REDACTED**"
    assert "secret" not in str(diagnostics)
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["prices"]["slot_count"] == 72
    assert diagnostics["prices"]["current_slot"]["price"] == 0.05

    (plan,) = diagnostics["plans"]
    assert plan["config"]["name"] == "Water heater"
    assert plan["active"] is True
    assert "slots" not in plan["attributes"]
    assert plan["attributes"]["windows"]
