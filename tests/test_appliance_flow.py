"""Tests for adding and reconfiguring an appliance."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .common import WATER_HEATER, sobry_entry


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Keep the entry from being set up when an appliance changes."""
    with patch(
        "custom_components.sobry.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


async def _add(hass: HomeAssistant, entry_id: str) -> dict:
    """Start adding an appliance."""
    return await hass.config_entries.subentries.async_init(
        (entry_id, "appliance"), context={"source": SOURCE_USER}
    )


async def _reconfigure(hass: HomeAssistant, entry_id: str, subentry_id: str) -> dict:
    """Start reconfiguring an appliance."""
    return await hass.config_entries.subentries.async_init(
        (entry_id, "appliance"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )


async def _configure(hass: HomeAssistant, result: dict, user_input: dict) -> dict:
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input
    )


async def test_add_without_options(hass: HomeAssistant) -> None:
    """Without an option ticked, the appliance is created after the schedule."""
    entry = sobry_entry()
    entry.add_to_hass(hass)

    result = await _add(hass, entry.entry_id)
    assert result["step_id"] == "appliance"
    result = await _configure(
        hass,
        result,
        {
            "name": "  Dishwasher ",
            "target_entity": "switch.dishwasher",
            "mode": "cheapest_block",
        },
    )
    assert result["step_id"] == "schedule"
    result = await _configure(
        hass,
        result,
        {
            "hours": 2,
            "window_start": "22:00:00",
            "window_end": "07:00:00",
            "max_price": 0.2,
            "require_complete_data": True,
            "features": [],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    assert subentry.title == "Dishwasher"
    assert dict(subentry.data) == {
        "name": "Dishwasher",
        "mode": "cheapest_block",
        "target_entity": "switch.dishwasher",
        "hours": 2,
        "window_start": "22:00:00",
        "window_end": "07:00:00",
        "max_price": 0.2,
        "require_complete_data": True,
    }


async def test_threshold_mode_asks_no_runtime(hass: HomeAssistant) -> None:
    """The threshold mode has neither a runtime nor a maximum price."""
    entry = sobry_entry()
    entry.add_to_hass(hass)

    result = await _add(hass, entry.entry_id)
    result = await _configure(hass, result, {"name": "Pool", "mode": "threshold"})
    schema = result["data_schema"].schema
    assert "threshold_price" in schema
    assert "hours" not in schema
    assert "max_price" not in schema
    features = next(key for key in schema if key == "features")
    options = schema[features].config["options"]
    assert "monthly_runtime" not in options
    assert "runtime_entity" not in options


@pytest.mark.parametrize(
    ("user_input", "error"),
    [({"name": "   "}, "name_required"), ({"name": "water HEATER"}, "name_taken")],
)
async def test_name_errors(hass: HomeAssistant, user_input: dict, error: str) -> None:
    """A name is required, and unique among the appliances."""
    entry = sobry_entry(WATER_HEATER)
    entry.add_to_hass(hass)

    result = await _add(hass, entry.entry_id)
    result = await _configure(hass, result, {**user_input, "mode": "cheapest_slots"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": error}


async def test_add_with_options(hass: HomeAssistant) -> None:
    """The ticked options get their own section, and only those."""
    entry = sobry_entry()
    entry.add_to_hass(hass)

    result = await _add(hass, entry.entry_id)
    result = await _configure(hass, result, {"name": "Boiler", "mode": "cheapest_slots"})
    result = await _configure(
        hass,
        result,
        {
            "hours": 6,
            "window_start": "00:00:00",
            "window_end": "00:00:00",
            "require_complete_data": False,
            "features": ["monthly_runtime", "always_run", "fallback_schedule"],
        },
    )
    assert result["step_id"] == "features"
    assert set(result["data_schema"].schema) == {
        "monthly_runtime",
        "always_run",
        "fallback_schedule",
    }

    # The same start and end would never run.
    sections = {
        "monthly_runtime": {"month_1": 10, "month_7": 0},
        "always_run": {"force_price": 0, "force_skip_negative": True},
        "fallback_schedule": {
            "fallback_start": "22:00:00",
            "fallback_end": "22:00:00",
        },
    }
    result = await _configure(hass, result, sections)
    assert result["errors"] == {"base": "fallback_same_times"}

    sections["fallback_schedule"]["fallback_end"] = "06:00:00"
    result = await _configure(hass, result, sections)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = dict(next(iter(entry.subentries.values())).data)
    assert data["hours_by_month"] == {"1": 10.0, "7": 0.0}
    assert data["force_price"] == 0
    assert data["force_skip_negative"] is True
    assert data["fallback_start"] == "22:00:00"
    assert data["fallback_end"] == "06:00:00"
    assert "month_1" not in data


async def test_reconfigure_unticks_an_option(hass: HomeAssistant) -> None:
    """Reconfiguring starts from the settings, unticking clears them."""
    entry = sobry_entry(
        {
            **WATER_HEATER,
            "extra_hours": 1.5,
            "fallback_start": "22:00:00",
            "fallback_end": "06:00:00",
        }
    )
    entry.add_to_hass(hass)

    result = await _reconfigure(hass, entry.entry_id, "plan1")
    assert result["step_id"] == "appliance"
    result = await _configure(
        hass,
        result,
        {
            "name": "Water heater",
            "target_entity": "input_boolean.water_heater",
            "mode": "cheapest_slots",
        },
    )
    schema = result["data_schema"].schema
    features = next(key for key in schema if key == "features")
    assert features.description["suggested_value"] == [
        "extra_runtime",
        "fallback_schedule",
    ]

    result = await _configure(
        hass,
        result,
        {
            "hours": 7,
            "window_start": "00:00:00",
            "window_end": "00:00:00",
            "require_complete_data": True,
            "features": ["extra_runtime"],
        },
    )
    assert result["step_id"] == "features"
    result = await _configure(hass, result, {"extra_runtime": {"extra_hours": 2}})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    data = dict(entry.subentries["plan1"].data)
    assert data["hours"] == 7
    assert data["extra_hours"] == 2
    assert "fallback_start" not in data
    assert "fallback_end" not in data


async def test_reconfigure_keeps_its_own_name(hass: HomeAssistant) -> None:
    """An appliance keeping its name is not taken for a duplicate."""
    entry = sobry_entry(WATER_HEATER)
    entry.add_to_hass(hass)

    result = await _reconfigure(hass, entry.entry_id, "plan1")
    result = await _configure(
        hass, result, {"name": "Water heater", "mode": "cheapest_slots"}
    )
    assert result["step_id"] == "schedule"
