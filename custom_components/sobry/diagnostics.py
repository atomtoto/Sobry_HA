"""Diagnostics of the Sobry integration, for support requests."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_API_KEY
from .plan import SobryConfigEntry, local_timezone

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SobryConfigEntry
) -> dict[str, Any]:
    """Return the settings, the price series and what every plan decided."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    data = coordinator.data or {}
    slots = coordinator.price_slots
    tz = local_timezone()
    now = dt_util.utcnow()
    current = coordinator.slot_at(now)

    return {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                repr(coordinator.last_exception)
                if coordinator.last_exception
                else None
            ),
            "update_interval": str(coordinator.update_interval),
        },
        "prices": {
            "published_count": data.get("count"),
            "planning_count": len(data.get("planning_prices") or []),
            "slot_count": len(slots),
            "first_slot": slots[0].as_dict(tz) if slots else None,
            "last_slot": slots[-1].as_dict(tz) if slots else None,
            "current_slot": current.as_dict(tz) if current else None,
            "pricing_metadata": data.get("pricing_metadata"),
            "statistics": data.get("statistics"),
        },
        "plans": [
            {
                "config": async_redact_data(plan.config, TO_REDACT),
                "available": plan.available,
                "active": plan.is_active,
                # The slot list is long and already summed up by the windows.
                "attributes": {
                    key: value
                    for key, value in plan.as_attributes().items()
                    if key != "slots"
                },
            }
            for plan in runtime.plans
        ],
    }
