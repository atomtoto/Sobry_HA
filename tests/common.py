"""Helpers shared by the Sobry integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant

from custom_components.sobry.const import DOMAIN, SUBENTRY_APPLIANCE

PARIS = ZoneInfo("Europe/Paris")
PUBLIC_URL = "https://api.sobry.co/api/prices/raw"
API_KEY_URL = "https://api.sobry.co/v2/user/daily-prices"

# Thursday 15 January 2026, 10:30 in Paris.
NOW = datetime(2026, 1, 15, 10, 30, tzinfo=PARIS)

PUBLIC_SETTINGS = {
    "segment": "C5",
    "turpe": "CU4",
    "profil": "particulier",
    "display": "TTC",
    "tax_mode": "ttc",
    "granularity": "hourly",
}

WATER_HEATER = {
    "name": "Water heater",
    "mode": "cheapest_slots",
    "target_entity": "input_boolean.water_heater",
    "hours": 8.0,
    "window_start": "00:00:00",
    "window_end": "00:00:00",
    "require_complete_data": True,
}


def day_price(hour: int) -> float:
    """Return the price of an hour: cheapest at 10:00, dearer away from it."""
    return round(0.05 + abs(hour - 10) / 100, 4)


def public_payload(
    day: datetime = NOW, price=day_price, days: tuple[int, ...] = (-1, 0, 1)
) -> dict[str, Any]:
    """Return an answer of the public endpoint, hourly, for the given days."""
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    data = []
    for offset in days:
        midnight = start + timedelta(days=offset)
        for hour in range(24):
            moment = midnight.replace(hour=hour)
            data.append(
                {
                    "timestamp": moment.isoformat(),
                    "price_ttc_eur_kwh": price(hour),
                    "spot_price": 42.0,
                }
            )
    return {
        "data": data,
        "statistics": {"count": len(data)},
        "pricing_metadata": {"segment": "C5"},
    }


def appliance(data: dict[str, Any], subentry_id: str = "plan1") -> ConfigSubentryData:
    """Return an appliance subentry."""
    return ConfigSubentryData(
        data=data,
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_APPLIANCE,
        title=data["name"],
        unique_id=None,
    )


def sobry_entry(*appliances: dict[str, Any], **kwargs: Any) -> MockConfigEntry:
    """Return a Sobry entry on the public prices, with the given appliances."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Sobry Energy",
        unique_id="sobry",
        version=2,
        data=kwargs.pop("data", PUBLIC_SETTINGS),
        subentries_data=[
            appliance(data, f"plan{index}") for index, data in enumerate(appliances, 1)
        ],
        **kwargs,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up an entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
