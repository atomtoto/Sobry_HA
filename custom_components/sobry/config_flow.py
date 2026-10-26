"""Config flow for Sobry integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import uuid

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_API_KEY,
    CONF_DISPLAY,
    CONF_EXTRA_HOURS,
    CONF_FALLBACK_END,
    CONF_FALLBACK_START,
    CONF_FORCE_PRICE,
    CONF_FORCE_SKIP_NEGATIVE,
    CONF_GRANULARITY,
    CONF_HOURS,
    CONF_HOURS_BY_MONTH,
    CONF_HOURS_ENTITY,
    CONF_MAX_PRICE,
    CONF_MODE,
    CONF_PLAN_ID,
    CONF_POWER_ENTITY,
    CONF_POWER_HOLD_MAX,
    CONF_POWER_THRESHOLD,
    CONF_PLANS,
    CONF_PROFIL,
    CONF_REQUIRE_COMPLETE_DATA,
    CONF_SEGMENT,
    CONF_TARGET_ENTITY,
    CONF_TAX_MODE,
    CONF_THRESHOLD_PRICE,
    CONF_TURPE,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_DISPLAY,
    DEFAULT_EXTRA_HOURS,
    DEFAULT_GRANULARITY,
    DEFAULT_HOURS,
    DEFAULT_POWER_HOLD_MAX,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_PROFIL,
    DEFAULT_REQUIRE_COMPLETE_DATA,
    DEFAULT_SEGMENT,
    DEFAULT_TAX_MODE,
    DEFAULT_THRESHOLD_PRICE,
    DEFAULT_TURPE,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
    MAX_EXTRA_HOURS,
    MAX_HOURS,
    MAX_POWER_THRESHOLD,
    MAX_PRICE_LIMIT,
    MODE_CHEAPEST_SLOTS,
    MODE_THRESHOLD,
    NAME,
    PLAN_MODES,
)

SEGMENTS = ["C5", "C4"]
TURPE_C5 = ["CU", "CU4", "MU4", "MUDT", "LU"]
TURPE_C4 = ["CU", "LU"]
PROFILS = ["particulier", "pro"]
DISPLAYS = ["TTC", "HT"]
GRANULARITIES = ["quarter_hourly", "hourly"]
TAX_MODES = ["ttc", "ht"]

# Domains a plan can drive with homeassistant.turn_on / turn_off.
CONTROLLABLE_DOMAINS = [
    "switch",
    "input_boolean",
    "light",
    "climate",
    "water_heater",
    "humidifier",
    "fan",
]

API_KEY_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)

PRICE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=-MAX_PRICE_LIMIT,
        max=MAX_PRICE_LIMIT,
        step=0.001,
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="€/kWh",
    )
)


def _plan_core_schema() -> vol.Schema:
    """Return the schema for the general plan settings."""
    return vol.Schema(
        {
            vol.Required(CONF_NAME): selector.TextSelector(),
            vol.Required(CONF_MODE, default=MODE_CHEAPEST_SLOTS): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=PLAN_MODES,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="plan_mode",
                )
            ),
            vol.Optional(CONF_TARGET_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=CONTROLLABLE_DOMAINS)
            ),
            vol.Required(CONF_HOURS, default=DEFAULT_HOURS): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=MAX_HOURS,
                    step=0.25,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="h",
                )
            ),
            vol.Optional(CONF_HOURS_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["input_number", "number", "sensor"]
                )
            ),
            vol.Required(CONF_EXTRA_HOURS, default=DEFAULT_EXTRA_HOURS): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=MAX_EXTRA_HOURS,
                    step=0.25,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="h",
                )
            ),
            vol.Required(CONF_WINDOW_START, default=DEFAULT_WINDOW_START): selector.TimeSelector(),
            vol.Required(CONF_WINDOW_END, default=DEFAULT_WINDOW_END): selector.TimeSelector(),
            vol.Required(
                CONF_REQUIRE_COMPLETE_DATA, default=DEFAULT_REQUIRE_COMPLETE_DATA
            ): selector.BooleanSelector(),
        }
    )


def _plan_pricing_schema(mode: str) -> vol.Schema:
    """Return the price settings relevant to the selected mode."""
    fields: dict[Any, Any] = {
        vol.Optional(CONF_MAX_PRICE): PRICE_SELECTOR,
        vol.Optional(CONF_FORCE_PRICE): PRICE_SELECTOR,
    }
    if mode == MODE_THRESHOLD:
        fields[vol.Required(CONF_THRESHOLD_PRICE, default=DEFAULT_THRESHOLD_PRICE)] = (
            PRICE_SELECTOR
        )
    return vol.Schema(fields)


def _plan_force_schema() -> vol.Schema:
    """Return the settings for an enabled force price."""
    return vol.Schema(
        {
            vol.Required(CONF_FORCE_SKIP_NEGATIVE, default=False): selector.BooleanSelector(),
        }
    )


def _plan_power_schema() -> vol.Schema:
    """Return the optional power tracking entity."""
    return vol.Schema(
        {
            vol.Optional(CONF_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor", "input_number", "number"]
                )
            ),
        }
    )


def _plan_power_settings_schema() -> vol.Schema:
    """Return settings for an enabled power tracking entity."""
    return vol.Schema(
        {
            vol.Required(
                CONF_POWER_THRESHOLD, default=DEFAULT_POWER_THRESHOLD
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=MAX_POWER_THRESHOLD,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="W",
                )
            ),
            vol.Required(
                CONF_POWER_HOLD_MAX, default=DEFAULT_POWER_HOLD_MAX
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=MAX_HOURS,
                    step=0.25,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="h",
                )
            ),
        }
    )


def _plan_fallback_schema() -> vol.Schema:
    """Return the optional fixed fallback schedule."""
    return vol.Schema(
        {
            vol.Optional(CONF_FALLBACK_START): selector.TimeSelector(),
            vol.Optional(CONF_FALLBACK_END): selector.TimeSelector(),
        }
    )


def _monthly_schema() -> vol.Schema:
    """Return the schema holding one optional runtime per month."""
    hours = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=MAX_HOURS,
            step=0.25,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="h",
        )
    )
    return vol.Schema(
        {vol.Optional(f"month_{month}"): hours for month in range(1, 13)}
    )


class SobryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sobry."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow state."""
        self._user_input: dict[str, Any] = {}

    async def async_step_user(self, user_input=None):
        """Choose the contract segment."""
        if user_input is not None:
            self._user_input = dict(user_input)
            return await self.async_step_user_details()

        schema = vol.Schema(
            {
                vol.Required(CONF_SEGMENT, default=DEFAULT_SEGMENT): vol.In(SEGMENTS),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_user_details(self, user_input=None):
        """Choose settings available for the selected segment."""
        segment = self._user_input.get(CONF_SEGMENT, DEFAULT_SEGMENT)
        if user_input is not None:
            self._user_input.update(user_input)
            user_input = self._user_input
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if api_key:
                user_input[CONF_API_KEY] = api_key
            else:
                user_input.pop(CONF_API_KEY, None)

            if user_input[CONF_SEGMENT] == "C4":
                if user_input[CONF_TURPE] not in TURPE_C4:
                    user_input[CONF_TURPE] = "CU"
                user_input[CONF_PROFIL] = "pro"
                user_input[CONF_DISPLAY] = "HT"

            await self.async_set_unique_id("sobry")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=NAME, data=user_input)

        fields: dict[Any, Any] = {
            vol.Required(CONF_TURPE, default=DEFAULT_TURPE): vol.In(
                TURPE_C4 if segment == "C4" else TURPE_C5
            ),
            vol.Required(
                CONF_GRANULARITY, default=DEFAULT_GRANULARITY
            ): vol.In(GRANULARITIES),
            vol.Required(CONF_TAX_MODE, default=DEFAULT_TAX_MODE): vol.In(TAX_MODES),
            vol.Optional(CONF_API_KEY): API_KEY_SELECTOR,
        }
        if segment != "C4":
            fields.update(
                {
                    vol.Required(CONF_PROFIL, default=DEFAULT_PROFIL): vol.In(PROFILS),
                    vol.Required(CONF_DISPLAY, default=DEFAULT_DISPLAY): vol.In(
                        DISPLAYS
                    ),
                }
            )
        schema = vol.Schema(
            fields
        )

        return self.async_show_form(step_id="user_details", data_schema=schema)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Start over when Sobry refuses the API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for a new API key, or none to go back to the public prices."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_failed")

        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            options = dict(entry.options)
            if CONF_API_KEY in options:
                # A key set from the options wins over the one given at setup.
                options[CONF_API_KEY] = api_key
            changed = self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_API_KEY: api_key}, options=options
            )
            # A loaded entry is reloaded by its update listener when it
            # changes; one that failed to set up has no listener.
            if not changed or entry.state is not ConfigEntryState.LOADED:
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(entry.entry_id)
                )
            return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_API_KEY): API_KEY_SELECTOR}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SobryOptionsFlow(config_entry)


class SobryOptionsFlow(config_entries.OptionsFlow):
    """Sobry options flow: API settings and device control plans."""

    def __init__(self, config_entry):
        # Kept under a private name: assigning ``self.config_entry`` is
        # deprecated in recent Home Assistant releases.
        self._entry = config_entry
        self._plan_id: str | None = None
        self._plan_input: dict[str, Any] = {}
        self._settings_input: dict[str, Any] = {}

    # -- Helpers ----------------------------------------------------------

    @property
    def _plans(self) -> list[dict[str, Any]]:
        """Return a copy of the configured plans."""
        return [dict(plan) for plan in self._entry.options.get(CONF_PLANS, [])]

    def _save(self, **changes: Any):
        """Persist the options, keeping the untouched keys."""
        return self.async_create_entry(title="", data={**self._entry.options, **changes})

    @staticmethod
    def _clean_plan(
        user_input: dict[str, Any],
        plan_id: str,
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a stored plan from the values submitted in the form."""
        plan = {key: value for key, value in user_input.items() if value not in (None, "")}
        plan[CONF_PLAN_ID] = plan_id
        plan[CONF_NAME] = str(user_input.get(CONF_NAME, "")).strip()
        plan[CONF_REQUIRE_COMPLETE_DATA] = bool(
            user_input.get(CONF_REQUIRE_COMPLETE_DATA, DEFAULT_REQUIRE_COMPLETE_DATA)
        )
        if plan.get(CONF_MODE) != MODE_THRESHOLD:
            plan.pop(CONF_THRESHOLD_PRICE, None)
        if plan.get(CONF_FORCE_PRICE) is None:
            plan.pop(CONF_FORCE_SKIP_NEGATIVE, None)
        if not plan.get(CONF_POWER_ENTITY):
            plan.pop(CONF_POWER_ENTITY, None)
            plan.pop(CONF_POWER_THRESHOLD, None)
            plan.pop(CONF_POWER_HOLD_MAX, None)
        # The per month runtimes live in their own step: editing a plan must
        # not drop them.
        if previous and previous.get(CONF_HOURS_BY_MONTH):
            plan[CONF_HOURS_BY_MONTH] = previous[CONF_HOURS_BY_MONTH]
        return plan

    def _plan_by_id(self) -> dict[str, Any] | None:
        """Return the plan currently being edited."""
        return next(
            (plan for plan in self._plans if plan[CONF_PLAN_ID] == self._plan_id), None
        )

    def _plan_picker(self, step_id: str):
        """Show the form asking which appliance to act on."""
        schema = vol.Schema(
            {
                vol.Required(CONF_PLAN_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._plan_options(),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema)

    def _plan_options(self) -> list[dict[str, str]]:
        """Return the plan list as select options."""
        return [
            {"value": plan[CONF_PLAN_ID], "label": plan.get(CONF_NAME) or plan[CONF_PLAN_ID]}
            for plan in self._plans
        ]

    # -- Steps ------------------------------------------------------------

    async def async_step_init(self, user_input=None):
        """Offer the settings and the plan management."""
        menu_options = ["settings", "add_plan"]
        if self._plans:
            menu_options.extend(["edit_plan", "monthly_hours", "remove_plan"])
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_settings(self, user_input=None):
        """Choose the contract segment before showing its settings."""
        if user_input is not None:
            self._settings_input = dict(user_input)
            return await self.async_step_settings_details()

        data = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SEGMENT,
                        default=data.get(CONF_SEGMENT, DEFAULT_SEGMENT),
                    ): vol.In(SEGMENTS),
                }
            ),
        )

    async def async_step_settings_details(self, user_input=None):
        """Edit settings available for the selected contract segment."""
        data = {**self._entry.data, **self._entry.options}
        segment = self._settings_input.get(
            CONF_SEGMENT, data.get(CONF_SEGMENT, DEFAULT_SEGMENT)
        )
        if user_input is not None:
            self._settings_input.update(user_input)
            user_input = self._settings_input
            # Saved even when empty: an empty key in the options overrides the
            # one given at setup, which is how a key is removed.
            user_input[CONF_API_KEY] = (user_input.get(CONF_API_KEY) or "").strip()
            if segment == "C4":
                if user_input[CONF_TURPE] not in TURPE_C4:
                    user_input[CONF_TURPE] = "CU"
                user_input[CONF_PROFIL] = "pro"
                user_input[CONF_DISPLAY] = "HT"
            return self._save(**user_input)

        turpe_options = TURPE_C4 if segment == "C4" else TURPE_C5

        fields: dict[Any, Any] = {
            vol.Required(
                CONF_TURPE, default=data.get(CONF_TURPE, DEFAULT_TURPE)
            ): vol.In(turpe_options),
            vol.Required(
                CONF_GRANULARITY,
                default=data.get(CONF_GRANULARITY, DEFAULT_GRANULARITY),
            ): vol.In(GRANULARITIES),
            vol.Required(
                CONF_TAX_MODE, default=data.get(CONF_TAX_MODE, DEFAULT_TAX_MODE)
            ): vol.In(TAX_MODES),
            # A suggestion, not a default: a cleared field must not bring
            # the previous key back.
            vol.Optional(
                CONF_API_KEY,
                description={"suggested_value": data.get(CONF_API_KEY) or None},
            ): API_KEY_SELECTOR,
        }
        if segment != "C4":
            fields.update(
                {
                    vol.Required(
                        CONF_PROFIL, default=data.get(CONF_PROFIL, DEFAULT_PROFIL)
                    ): vol.In(PROFILS),
                    vol.Required(
                        CONF_DISPLAY, default=data.get(CONF_DISPLAY, DEFAULT_DISPLAY)
                    ): vol.In(DISPLAYS),
                }
            )
        return self.async_show_form(
            step_id="settings_details", data_schema=vol.Schema(fields)
        )

    async def async_step_add_plan(self, user_input=None):
        """Set the general settings for a new device control plan."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not str(user_input.get(CONF_NAME, "")).strip():
                errors[CONF_NAME] = "name_required"
            else:
                self._plan_id = uuid.uuid4().hex
                self._plan_input = dict(user_input)
                return await self.async_step_plan_pricing()

        return self.async_show_form(
            step_id="add_plan",
            data_schema=self.add_suggested_values_to_schema(
                _plan_core_schema(), user_input or {}
            ),
            errors=errors,
        )

    async def async_step_edit_plan(self, user_input=None):
        """Pick the plan to edit."""
        if user_input is not None:
            self._plan_id = user_input[CONF_PLAN_ID]
            self._plan_input = {}
            return await self.async_step_plan_settings()
        return self._plan_picker("edit_plan")

    async def async_step_plan_settings(self, user_input=None):
        """Set the general settings for the selected plan."""
        plans = self._plans
        current = self._plan_by_id()
        if current is None:
            return await self.async_step_init()

        errors: dict[str, str] = {}
        if user_input is not None:
            if not str(user_input.get(CONF_NAME, "")).strip():
                errors[CONF_NAME] = "name_required"
            else:
                self._plan_input = dict(user_input)
                return await self.async_step_plan_pricing()

        return self.async_show_form(
            step_id="plan_settings",
            data_schema=self.add_suggested_values_to_schema(
                _plan_core_schema(), user_input or current
            ),
            errors=errors,
            description_placeholders={"name": current.get(CONF_NAME, "")},
        )

    async def async_step_plan_pricing(self, user_input=None):
        """Set only the price limits relevant to the selected mode."""
        current = self._plan_by_id() or {}
        mode = self._plan_input.get(
            CONF_MODE, current.get(CONF_MODE, MODE_CHEAPEST_SLOTS)
        )
        if user_input is not None:
            self._plan_input.update(user_input)
            if user_input.get(CONF_FORCE_PRICE) not in (None, ""):
                return await self.async_step_plan_force()
            return await self.async_step_plan_power()

        suggested = {
            key: current[key]
            for key in (CONF_MAX_PRICE, CONF_THRESHOLD_PRICE, CONF_FORCE_PRICE)
            if key in current
        }
        return self.async_show_form(
            step_id="plan_pricing",
            data_schema=self.add_suggested_values_to_schema(
                _plan_pricing_schema(mode), suggested
            ),
        )

    async def async_step_plan_force(self, user_input=None):
        """Configure the optional force-price exception."""
        current = self._plan_by_id() or {}
        if user_input is not None:
            self._plan_input.update(user_input)
            return await self.async_step_plan_power()

        return self.async_show_form(
            step_id="plan_force",
            data_schema=self.add_suggested_values_to_schema(
                _plan_force_schema(), current
            ),
        )

    async def async_step_plan_power(self, user_input=None):
        """Choose whether power consumption should extend the schedule."""
        current = self._plan_by_id() or {}
        if user_input is not None:
            self._plan_input.update(user_input)
            if user_input.get(CONF_POWER_ENTITY):
                return await self.async_step_plan_power_settings()
            return await self.async_step_plan_fallback()

        return self.async_show_form(
            step_id="plan_power",
            data_schema=self.add_suggested_values_to_schema(
                _plan_power_schema(), current
            ),
        )

    async def async_step_plan_power_settings(self, user_input=None):
        """Configure the thresholds for power-based schedule extension."""
        current = self._plan_by_id() or {}
        if user_input is not None:
            self._plan_input.update(user_input)
            return await self.async_step_plan_fallback()

        return self.async_show_form(
            step_id="plan_power_settings",
            data_schema=self.add_suggested_values_to_schema(
                _plan_power_settings_schema(), current
            ),
        )

    async def async_step_plan_fallback(self, user_input=None):
        """Configure the optional fixed fallback schedule and save the plan."""
        current = self._plan_by_id() or {}
        if user_input is not None:
            self._plan_input.update(user_input)
            updated = self._clean_plan(
                self._plan_input,
                self._plan_id or current.get(CONF_PLAN_ID, uuid.uuid4().hex),
                previous=current,
            )
            plans = self._plans
            if current:
                plans = [
                    updated if plan[CONF_PLAN_ID] == current[CONF_PLAN_ID] else plan
                    for plan in plans
                ]
            else:
                plans.append(updated)
            self._plan_input = {}
            return self._save(**{CONF_PLANS: plans})

        return self.async_show_form(
            step_id="plan_fallback",
            data_schema=self.add_suggested_values_to_schema(
                _plan_fallback_schema(), current
            ),
        )

    async def async_step_monthly_hours(self, user_input=None):
        """Pick the appliance whose runtime varies over the year."""
        if user_input is not None:
            self._plan_id = user_input[CONF_PLAN_ID]
            return await self.async_step_monthly_hours_values()
        return self._plan_picker("monthly_hours")

    async def async_step_monthly_hours_values(self, user_input=None):
        """Set a runtime per month, for instance longer in winter."""
        current = self._plan_by_id()
        if current is None:
            return await self.async_step_init()

        if user_input is not None:
            months = {
                str(month): float(user_input[f"month_{month}"])
                for month in range(1, 13)
                if user_input.get(f"month_{month}") not in (None, "")
            }
            updated = {**current}
            if months:
                updated[CONF_HOURS_BY_MONTH] = months
            else:
                updated.pop(CONF_HOURS_BY_MONTH, None)
            plans = [
                updated if plan[CONF_PLAN_ID] == self._plan_id else plan
                for plan in self._plans
            ]
            return self._save(**{CONF_PLANS: plans})

        stored = current.get(CONF_HOURS_BY_MONTH) or {}
        suggested = {
            f"month_{month}": stored[key]
            for month in range(1, 13)
            for key in (str(month), month)
            if key in stored
        }
        return self.async_show_form(
            step_id="monthly_hours_values",
            data_schema=self.add_suggested_values_to_schema(
                _monthly_schema(), suggested
            ),
            description_placeholders={
                "name": current.get(CONF_NAME, ""),
                "hours": str(current.get(CONF_HOURS, DEFAULT_HOURS)),
            },
        )

    async def async_step_remove_plan(self, user_input=None):
        """Delete one or several plans."""
        if user_input is not None:
            removed = set(user_input.get(CONF_PLANS, []))
            plans = [plan for plan in self._plans if plan[CONF_PLAN_ID] not in removed]
            return self._save(**{CONF_PLANS: plans})

        schema = vol.Schema(
            {
                vol.Required(CONF_PLANS, default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=self._plan_options(),
                        mode=selector.SelectSelectorMode.LIST,
                        multiple=True,
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove_plan", data_schema=schema)
