"""Tests for the pricing setup, the options and the reauthentication."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

from aiohttp import ClientError
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.sobry.const import DOMAIN

from .common import API_KEY_URL, PUBLIC_SETTINGS, sobry_entry


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Keep the flows from setting the entry up."""
    with patch(
        "custom_components.sobry.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


async def _start(hass: HomeAssistant, source: str) -> dict:
    """Start the setup and choose where the prices come from."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "source"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"price_source": source}
    )


async def test_public_c5_prices(hass: HomeAssistant) -> None:
    """A C5 contract asks the TURPE, the customer type and the taxes."""
    result = await _start(hass, "public_c5")
    assert result["step_id"] == "public"
    assert set(result["data_schema"].schema) == {
        "turpe",
        "profil",
        "display",
        "granularity",
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"turpe": "mu4", "profil": "pro", "display": "ht", "granularity": "hourly"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sobry Energy"
    assert result["data"] == {
        "segment": "C5",
        "turpe": "MU4",
        "profil": "pro",
        "display": "HT",
        "tax_mode": "ht",
        "granularity": "hourly",
    }


async def test_public_c4_prices(hass: HomeAssistant) -> None:
    """A C4 contract is professional and excluding taxes, only CU and LU."""
    result = await _start(hass, "public_c4")
    assert set(result["data_schema"].schema) == {"turpe", "granularity"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"turpe": "lu", "granularity": "quarter_hourly"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "segment": "C4",
        "turpe": "LU",
        "profil": "pro",
        "display": "HT",
        "tax_mode": "ht",
        "granularity": "quarter_hourly",
    }


async def test_api_key_prices(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The key is checked with Sobry before the entry is created."""
    aioclient_mock.get(API_KEY_URL, json={"data": []})
    result = await _start(hass, "api_key")
    assert result["step_id"] == "api_key"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"api_key": "  secret  ", "tax_mode": "ttc", "granularity": "hourly"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "api_key": "secret",
        "display": "TTC",
        "tax_mode": "ttc",
        "granularity": "hourly",
    }
    assert aioclient_mock.mock_calls[0][3] == {"Authorization": "Bearer secret"}


@pytest.mark.parametrize(
    ("answer", "errors"),
    [
        ({"status": 401}, {"api_key": "invalid_api_key"}),
        ({"status": 403}, {"api_key": "invalid_api_key"}),
        ({"status": 500}, {"base": "cannot_connect"}),
        ({"exc": ClientError()}, {"base": "cannot_connect"}),
        ({"exc": TimeoutError()}, {"base": "cannot_connect"}),
    ],
)
async def test_api_key_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    answer: dict,
    errors: dict,
) -> None:
    """A key Sobry refuses, or cannot check, is not saved."""
    aioclient_mock.get(API_KEY_URL, **answer)
    result = await _start(hass, "api_key")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"api_key": "secret", "tax_mode": "ttc", "granularity": "hourly"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    # The form keeps the key typed, and accepts it once Sobry does.
    aioclient_mock.clear_requests()
    aioclient_mock.get(API_KEY_URL, json={"data": []})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"api_key": "secret", "tax_mode": "ttc", "granularity": "hourly"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_blank_api_key(hass: HomeAssistant) -> None:
    """A blank key is refused without asking Sobry."""
    result = await _start(hass, "api_key")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"api_key": "   ", "tax_mode": "ttc", "granularity": "hourly"},
    )
    assert result["errors"] == {"api_key": "invalid_api_key"}


async def test_single_instance(hass: HomeAssistant) -> None:
    """Sobry is set up once."""
    sobry_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] in ("single_instance_allowed", "already_configured")


async def test_options_switch_to_api_key_and_back(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The options change the price source, keeping the tax choice in step."""
    aioclient_mock.get(API_KEY_URL, json={"data": []})
    entry = sobry_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "source"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"price_source": "api_key"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"api_key": "secret", "tax_mode": "ht", "granularity": "quarter_hourly"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "api_key": "secret",
        "display": "HT",
        "tax_mode": "ht",
        "granularity": "quarter_hourly",
    }

    # Back to the public prices: the key in the options is emptied, which
    # wins over one given at setup.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    default = next(key for key in schema if key == "price_source").default()
    assert default == "api_key"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"price_source": "public_c5"}
    )
    assert result["step_id"] == "public"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"turpe": "cu4", "profil": "particulier", "display": "ht", "granularity": "hourly"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["api_key"] == ""
    assert entry.options["tax_mode"] == "ht"
    assert entry.options["segment"] == "C5"


async def test_reauth_with_a_new_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused key is replaced from the reauthentication."""
    aioclient_mock.get(API_KEY_URL, json={"data": []})
    entry = sobry_entry(data={**PUBLIC_SETTINGS, "api_key": "old"})
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["api_key"] == "new"


async def test_reauth_refused_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A new key Sobry refuses keeps the form open."""
    aioclient_mock.get(API_KEY_URL, status=401)
    entry = sobry_entry(data={**PUBLIC_SETTINGS, "api_key": "old"})
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"api_key": "invalid_api_key"}
    assert entry.data["api_key"] == "old"


async def test_reauth_back_to_public_prices(hass: HomeAssistant) -> None:
    """An empty key goes back to the public prices, the options included."""
    entry = sobry_entry(
        data={**PUBLIC_SETTINGS, "api_key": "old"}, options={"api_key": "older"}
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["reason"] == "reauth_successful"
    assert entry.data["api_key"] == ""
    assert entry.options["api_key"] == ""
