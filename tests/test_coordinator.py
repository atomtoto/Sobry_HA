"""Tests for fetching and reading the Sobry prices."""

from __future__ import annotations

from datetime import datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.sobry.coordinator import error_detail

from .common import (
    API_KEY_URL,
    PARIS,
    PUBLIC_SETTINGS,
    PUBLIC_URL,
    public_payload,
    setup_entry,
    sobry_entry,
)


async def test_public_request(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """The public prices are asked from yesterday to tomorrow included."""
    await setup_entry(hass, sobry_entry())
    _method, url, _data, headers = public_api.mock_calls[0]
    assert dict(url.query) == {
        "start": "2026-01-14",
        "end": "2026-01-17",
        "granularity": "hourly",
        "segment": "C5",
        "turpe": "CU4",
        "profil": "particulier",
        "display": "TTC",
    }
    assert headers is None


async def test_c4_request(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """A C4 contract is always professional and excluding taxes."""
    await setup_entry(
        hass,
        sobry_entry(
            data={**PUBLIC_SETTINGS, "segment": "C4", "profil": "particulier"}
        ),
    )
    query = dict(public_api.mock_calls[0][1].query)
    assert query["profil"] == "pro"
    assert query["display"] == "HT"


def _v2_day(day: str, times: list[str], price: float = 0.1) -> list[dict]:
    """Return entries of the API key endpoint, in Paris local time."""
    return [{"date": day, "time": moment, "price": price} for moment in times]


async def test_api_key_request_and_local_times(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """The V2 local times are read in Paris, the repeated hour included."""
    await hass.config.async_set_time_zone("Europe/Paris")
    # 25 October 2026: the clocks go back, 02:00 comes twice.
    freezer.move_to(datetime(2026, 10, 25, 12, tzinfo=PARIS))
    hours = [f"{hour:02d}:00" for hour in range(24)]
    hours.insert(3, "02:00")
    aioclient_mock.get(
        API_KEY_URL,
        json={"data": _v2_day("2026-10-25", hours) + _v2_day("2026-10-26", ["00:00"])},
    )
    entry = sobry_entry(
        data={"api_key": "secret", "tax_mode": "ht", "granularity": "quarter_hourly"}
    )
    await setup_entry(hass, entry)

    _method, url, _data, headers = aioclient_mock.mock_calls[0]
    assert dict(url.query) == {
        "day": "2026-10-25",
        "days": "1",
        "granularity": "15m",
        "taxMode": "ht",
    }
    assert headers == {"Authorization": "Bearer secret"}

    slots = entry.runtime_data.coordinator.price_slots
    assert len(slots) == 26
    # One hour each, the two 02:00 one hour apart.
    assert all(slot.duration == timedelta(hours=1) for slot in slots)
    assert slots[3].start - slots[2].start == timedelta(hours=1)
    assert hass.states.get("sensor.sobry_current_price").state == "0.1"


@pytest.mark.parametrize(
    "answer",
    [
        {"status": 429},
        {"status": 500, "text": "<html><body>Bad gateway</body></html>"},
        {"text": "not json"},
        {"content": b"\xff\xfe not text"},
        {"json": {"data": []}},
        {"json": {"data": [{"timestamp": "2026-01-15T09:00:00+00:00"}]}},
        {"json": "unexpected"},
    ],
)
async def test_unusable_answers(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
    answer: dict,
) -> None:
    """An error or an answer without prices fails the refresh."""
    aioclient_mock.get(PUBLIC_URL, **answer)
    entry = sobry_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_evening_prices_are_kept_after_midnight(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refresh no longer serving yesterday keeps its prices for planning."""
    aioclient_mock.get(PUBLIC_URL, json=public_payload(days=(-1, 0)))
    entry = sobry_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data.coordinator
    assert len(coordinator.price_slots) == 48

    aioclient_mock.clear_requests()
    aioclient_mock.get(PUBLIC_URL, json=public_payload(days=(0,)))
    await coordinator.async_refresh()
    assert len(coordinator.price_slots) == 48


def test_error_detail() -> None:
    """The message of an error answer is found, whatever its shape."""
    assert error_detail('{"message": "Bad day"}') == "Bad day"
    assert error_detail('{"error": "INVALID_API_KEY"}') == "INVALID_API_KEY"
    assert error_detail("<html>\n  Oops  </html>") == "<html> Oops </html>"
    assert error_detail("") == "unknown error"
    assert len(error_detail("x" * 1000)) == 200
