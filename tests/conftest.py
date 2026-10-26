"""Fixtures shared by the Sobry integration tests."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant

from .common import NOW, PUBLIC_URL, public_payload


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture
async def paris(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> FrozenDateTimeFactory:
    """Run Home Assistant in Paris, at a fixed moment."""
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
def public_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Serve the public prices of yesterday, today and tomorrow."""
    aioclient_mock.get(PUBLIC_URL, json=public_payload())
    return aioclient_mock
