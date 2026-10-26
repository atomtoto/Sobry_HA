"""Tests for the appliances: the plan, its entities and the control."""

from __future__ import annotations

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.sobry.const import DOMAIN

from .common import (
    NOW,
    PARIS,
    PUBLIC_URL,
    WATER_HEATER,
    public_payload,
    setup_entry,
    sobry_entry,
)

RELAY = {**WATER_HEATER, "target_entity": "switch.relay"}


async def _move_to(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, moment: datetime
) -> None:
    """Move to ``moment`` and let the minute tick run."""
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.fixture
async def input_boolean(hass: HomeAssistant) -> None:
    """Provide the input_boolean driven by the water heater."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"water_heater": {"initial": False}}}
    )


async def test_plan_drives_the_appliance(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    public_api: AiohttpClientMocker,
    input_boolean: None,
) -> None:
    """The 8 cheapest hours run from 06:00 to 14:00, then the heater stops."""
    await setup_entry(hass, sobry_entry(WATER_HEATER))

    state = hass.states.get("binary_sensor.water_heater")
    assert state.state == STATE_ON
    # Today's 8 hours, and tomorrow's, published already.
    assert state.attributes["planned_hours"] == 16
    assert state.attributes["windows"][0]["start"] == "2026-01-15T06:00:00+01:00"
    assert state.attributes["windows"][0]["end"] == "2026-01-15T14:00:00+01:00"
    assert hass.states.get("input_boolean.water_heater").state == STATE_ON
    assert hass.states.get("sensor.water_heater_next_end").state == (
        "2026-01-15T13:00:00+00:00"
    )
    assert hass.states.get("sensor.water_heater_planned_average_price").state == (
        "0.07"
    )

    await _move_to(hass, paris, NOW.replace(hour=14, minute=0))
    assert hass.states.get("binary_sensor.water_heater").state == STATE_OFF
    assert hass.states.get("input_boolean.water_heater").state == STATE_OFF
    # Tomorrow is published: its run is advertised already.
    assert hass.states.get("sensor.water_heater_next_start").state == (
        "2026-01-16T05:00:00+00:00"
    )


async def test_automatic_control_off_leaves_the_appliance(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    public_api: AiohttpClientMocker,
    input_boolean: None,
) -> None:
    """With the automatic control off, the plan changes nothing."""
    await setup_entry(hass, sobry_entry(WATER_HEATER))
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.water_heater_automatic_control"},
        blocking=True,
    )

    await _move_to(hass, paris, NOW.replace(hour=14, minute=0))
    assert hass.states.get("binary_sensor.water_heater").state == STATE_OFF
    assert hass.states.get("input_boolean.water_heater").state == STATE_ON

    # Back on, the plan applies again right away.
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.water_heater_automatic_control"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.water_heater").state == STATE_OFF


async def test_force_on(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    public_api: AiohttpClientMocker,
    input_boolean: None,
) -> None:
    """The force switch runs the appliance until it is turned off."""
    paris.move_to(NOW.replace(hour=16))
    await setup_entry(hass, sobry_entry(WATER_HEATER))
    assert hass.states.get("input_boolean.water_heater").state == STATE_OFF

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.water_heater_force_on"}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.water_heater")
    assert state.state == STATE_ON
    assert state.attributes["boost_active"] is True
    assert hass.states.get("input_boolean.water_heater").state == STATE_ON

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.water_heater_force_on"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.water_heater").state == STATE_OFF


async def test_runtime_number(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    public_api: AiohttpClientMocker,
    input_boolean: None,
) -> None:
    """The runtime is tuned from its number, and the plan follows."""
    await setup_entry(hass, sobry_entry(WATER_HEATER))
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.water_heater_runtime", "value": 2},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.water_heater")
    assert state.attributes["requested_hours"] == 2
    # 09:00 and 10:00 (equal at 0.06, the earliest wins), 10:30 is in it.
    assert state.attributes["windows"][0]["start"] == "2026-01-15T09:00:00+01:00"
    assert state.state == STATE_ON


async def test_restored_settings(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """A runtime tuned and a force left on survive a restart."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State("switch.water_heater_force_on", STATE_ON), {}),
            (
                State("number.water_heater_runtime", "3"),
                {"value": 3.0, "configured": 8.0},
            ),
        ],
    )
    paris.move_to(NOW.replace(hour=16))
    await setup_entry(hass, sobry_entry({**WATER_HEATER, "target_entity": None}))

    assert hass.states.get("number.water_heater_runtime").state == "3.0"
    state = hass.states.get("binary_sensor.water_heater")
    assert state.state == STATE_ON
    assert state.attributes["boost_active"] is True


async def test_restored_runtime_loses_to_a_new_setting(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """A runtime tuned from the UI is dropped once the appliance is edited."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.water_heater_runtime", "3"),
                {"value": 3.0, "configured": 6.0},
            )
        ],
    )
    await setup_entry(hass, sobry_entry(WATER_HEATER))
    assert hass.states.get("number.water_heater_runtime").state == "8.0"


async def test_unavailable_target_is_retried(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    public_api: AiohttpClientMocker,
) -> None:
    """A target unavailable when it should start is started once it is back."""
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    hass.states.async_set("switch.relay", STATE_UNAVAILABLE)
    await setup_entry(hass, sobry_entry(RELAY))
    assert hass.states.get("binary_sensor.water_heater").state == STATE_ON
    assert turn_on == []

    hass.states.async_set("switch.relay", STATE_OFF)
    await _move_to(hass, paris, NOW.replace(minute=31))
    assert [call.data["entity_id"] for call in turn_on] == ["switch.relay"]

    # Done once: the next minutes do not call it again.
    await _move_to(hass, paris, NOW.replace(minute=32))
    assert len(turn_on) == 1


async def test_failed_call_is_retried(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    public_api: AiohttpClientMocker,
) -> None:
    """A call that fails is made again on the next tick."""
    hass.states.async_set("switch.relay", STATE_OFF)
    # No homeassistant.turn_on yet: the call fails.
    await setup_entry(hass, sobry_entry(RELAY))

    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    await _move_to(hass, paris, NOW.replace(minute=31))
    assert len(turn_on) == 1


async def test_missing_target_raises_a_repair(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    public_api: AiohttpClientMocker,
) -> None:
    """A target that no longer exists is reported, until it comes back."""
    turn_on = async_mock_service(hass, "homeassistant", "turn_on")
    entry = sobry_entry(RELAY)
    await setup_entry(hass, entry)

    issue_registry = ir.async_get(hass)
    issue = issue_registry.async_get_issue(DOMAIN, "target_missing_plan1")
    assert issue is not None
    assert issue.translation_placeholders == {
        "name": "Water heater",
        "entity_id": "switch.relay",
    }
    assert turn_on == []

    hass.states.async_set("switch.relay", STATE_OFF)
    await _move_to(hass, paris, NOW.replace(minute=31))
    assert issue_registry.async_get_issue(DOMAIN, "target_missing_plan1") is None
    assert len(turn_on) == 1

    # Gone again, then the entry unloads: the repair goes with it.
    hass.states.async_remove("switch.relay")
    await _move_to(hass, paris, NOW.replace(hour=14))
    assert issue_registry.async_get_issue(DOMAIN, "target_missing_plan1") is not None
    await hass.config_entries.async_unload(entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, "target_missing_plan1") is None


async def test_power_hold_finishes_the_cycle(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    public_api: AiohttpClientMocker,
    input_boolean: None,
) -> None:
    """After the last window the heater runs on while it still draws power."""
    hass.states.async_set("sensor.heater_power", "1.8", {"unit_of_measurement": "kW"})
    await setup_entry(
        hass,
        sobry_entry(
            {
                **WATER_HEATER,
                "power_entity": "sensor.heater_power",
                "power_threshold": 100,
                "power_hold_max": 1,
            }
        ),
    )

    await _move_to(hass, paris, NOW.replace(hour=14, minute=0))
    state = hass.states.get("binary_sensor.water_heater")
    assert state.state == STATE_ON
    assert state.attributes["power_hold_active"] is True
    assert state.attributes["power_now"] == 1800
    assert state.attributes["power_hold_until"] == "2026-01-15T15:00:00+01:00"
    assert hass.states.get("input_boolean.water_heater").state == STATE_ON

    # A single low reading does not stop it, two minutes of them do.
    hass.states.async_set("sensor.heater_power", "0.05", {"unit_of_measurement": "kW"})
    await _move_to(hass, paris, NOW.replace(hour=14, minute=1))
    assert hass.states.get("binary_sensor.water_heater").state == STATE_ON
    await _move_to(hass, paris, NOW.replace(hour=14, minute=3))
    assert hass.states.get("binary_sensor.water_heater").state == STATE_OFF
    assert hass.states.get("input_boolean.water_heater").state == STATE_OFF


async def test_runtime_from_an_entity(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """A runtime read from an entity is followed as it changes."""
    hass.states.async_set("input_number.heater_hours", "2")
    await setup_entry(
        hass,
        sobry_entry(
            {**WATER_HEATER, "target_entity": None, "hours_entity": "input_number.heater_hours"}
        ),
    )
    assert hass.states.get("number.water_heater_runtime") is None
    runtime = hass.states.get("sensor.water_heater_runtime")
    assert runtime.state == "2.0"
    assert runtime.attributes["source"] == "entity"

    hass.states.async_set("input_number.heater_hours", "30")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.water_heater_runtime").state == "24.0"

    # Unusable: the runtime of the appliance takes over.
    hass.states.async_set("input_number.heater_hours", "unknown")
    await hass.async_block_till_done()
    runtime = hass.states.get("sensor.water_heater_runtime")
    assert runtime.state == "8.0"
    assert runtime.attributes["source"] == "default"


async def test_monthly_runtime(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """A month set to 0 keeps the appliance off, without any fallback."""
    await setup_entry(
        hass,
        sobry_entry(
            {
                **WATER_HEATER,
                "target_entity": None,
                "hours_by_month": {"1": 0},
                "fallback_start": "10:00:00",
                "fallback_end": "12:00:00",
            }
        ),
    )
    state = hass.states.get("binary_sensor.water_heater")
    assert state.state == STATE_OFF
    assert state.attributes["hours_source"] == "month"
    assert state.attributes["fallback_active"] is False
    assert hass.states.get("sensor.water_heater_runtime").state == "0.0"


async def test_threshold_mode(
    hass: HomeAssistant, paris: FrozenDateTimeFactory, public_api: AiohttpClientMocker
) -> None:
    """Every slot at or under the threshold runs, tunable from its number."""
    await setup_entry(
        hass,
        sobry_entry(
            {
                "name": "Pool",
                "mode": "threshold",
                "threshold_price": 0.06,
                "window_start": "00:00:00",
                "window_end": "00:00:00",
            }
        ),
    )
    state = hass.states.get("binary_sensor.pool")
    assert state.state == STATE_ON
    # 09:00 to 12:00, today and tomorrow.
    assert state.attributes["planned_hours"] == 6
    assert hass.states.get("number.pool_price_threshold").state == "0.06"
    assert hass.states.get("number.pool_runtime") is None

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.pool_price_threshold", "value": 0.04},
        blocking=True,
    )
    state = hass.states.get("binary_sensor.pool")
    assert state.state == STATE_OFF
    assert state.attributes["reason"] == "no_slot_selected"


async def test_waiting_for_tomorrow(
    hass: HomeAssistant,
    paris: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An overnight window waits until tomorrow is published."""
    aioclient_mock.get(PUBLIC_URL, json=public_payload(days=(-1, 0)))
    await setup_entry(
        hass,
        sobry_entry(
            {
                **WATER_HEATER,
                "target_entity": None,
                "window_start": "22:00:00",
                "window_end": "06:00:00",
                "hours": 4,
            }
        ),
    )
    state = hass.states.get("binary_sensor.water_heater")
    assert state.state == STATE_OFF
    assert state.attributes["data_complete"] is False
    assert state.attributes["reason"] == "waiting_for_prices"
    assert state.attributes["period_start"] == (
        datetime(2026, 1, 15, 22, tzinfo=PARIS).isoformat()
    )
    assert hass.states.get("sensor.water_heater_next_start").state == "unknown"
