"""Services exposed by the Sobry integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EXTRA_HOURS,
    CONF_FORCE_PRICE,
    CONF_FORCE_SKIP_NEGATIVE,
    CONF_HOURS,
    CONF_MAX_PRICE,
    CONF_MODE,
    CONF_REQUIRE_COMPLETE_DATA,
    CONF_THRESHOLD_PRICE,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_EXTRA_HOURS,
    DEFAULT_HOURS,
    DOMAIN,
    MODE_CHEAPEST_SLOTS,
    PLAN_MODES,
    SERVICE_GET_CHEAPEST_SLOTS,
)
from .plan import SobryRuntimeData, local_timezone, parse_time
from .planner import build_plan

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

GET_CHEAPEST_SLOTS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): selector.ConfigEntrySelector(
            {"integration": DOMAIN}
        ),
        vol.Optional(CONF_MODE, default=MODE_CHEAPEST_SLOTS): vol.In(PLAN_MODES),
        vol.Optional(CONF_HOURS, default=DEFAULT_HOURS): vol.Coerce(float),
        vol.Optional(CONF_EXTRA_HOURS, default=DEFAULT_EXTRA_HOURS): vol.Coerce(float),
        vol.Optional(CONF_WINDOW_START): cv.time,
        vol.Optional(CONF_WINDOW_END): cv.time,
        vol.Optional(CONF_MAX_PRICE): vol.Coerce(float),
        vol.Optional(CONF_THRESHOLD_PRICE): vol.Coerce(float),
        vol.Optional(CONF_FORCE_PRICE): vol.Coerce(float),
        vol.Optional(CONF_FORCE_SKIP_NEGATIVE, default=False): cv.boolean,
        vol.Optional(CONF_REQUIRE_COMPLETE_DATA, default=False): cv.boolean,
    }
)


def _runtime(hass: HomeAssistant, entry_id: str | None) -> SobryRuntimeData:
    """Return the runtime of the requested (or only) Sobry config entry."""
    entries: dict[str, SobryRuntimeData] = hass.data.get(DOMAIN, {})
    if entry_id is not None:
        runtime = entries.get(entry_id)
        if runtime is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
            )
        return runtime
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
        )
    return next(iter(entries.values()))


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Sobry services, once for the whole integration."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_CHEAPEST_SLOTS):
        return

    async def async_get_cheapest_slots(call: ServiceCall) -> ServiceResponse:
        """Return the cheapest slots for the requested plan definition."""
        runtime = _runtime(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        slots = runtime.coordinator.price_slots
        if not slots:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_prices",
            )

        window_start = call.data.get(CONF_WINDOW_START) or parse_time(None, "00:00:00")
        window_end = call.data.get(CONF_WINDOW_END) or parse_time(None, "00:00:00")
        tz = local_timezone()
        now = dt_util.utcnow()

        result = build_plan(
            slots,
            now,
            tz,
            mode=call.data[CONF_MODE],
            hours=call.data[CONF_HOURS],
            window_start=window_start,
            window_end=window_end,
            max_price=call.data.get(CONF_MAX_PRICE),
            threshold_price=call.data.get(CONF_THRESHOLD_PRICE),
            extra_hours=call.data[CONF_EXTRA_HOURS],
            require_complete_data=call.data[CONF_REQUIRE_COMPLETE_DATA],
            force_price=call.data.get(CONF_FORCE_PRICE),
            force_skip_negative=call.data[CONF_FORCE_SKIP_NEGATIVE],
        )

        next_window = result.next_window(now)
        response: dict[str, Any] = {
            "period_start": result.period_start.astimezone(tz).isoformat(),
            "period_end": result.period_end.astimezone(tz).isoformat(),
            "data_complete": result.data_complete,
            "active": result.is_active(now),
            "planned_hours": round(result.scheduled_hours, 3),
            "average_price": (
                round(result.average_price, 6) if result.average_price is not None else None
            ),
            "next_start": (
                next_window.start.astimezone(tz).isoformat() if next_window else None
            ),
            "slots": [slot.as_dict(tz) for slot in result.selected],
            "windows": [window.as_dict(tz) for window in result.windows],
            "forced_slots": [slot.as_dict(tz) for slot in result.forced],
        }
        if result.reason:
            response["reason"] = result.reason
        return response

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_CHEAPEST_SLOTS,
        async_get_cheapest_slots,
        schema=GET_CHEAPEST_SLOTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the Sobry services when the last entry is unloaded."""
    hass.services.async_remove(DOMAIN, SERVICE_GET_CHEAPEST_SLOTS)
