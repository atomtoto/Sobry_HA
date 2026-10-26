"""Config flow for Sobry integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_API_KEY,
    CONF_DISPLAY,
    CONF_EXTRA_HOURS,
    CONF_FALLBACK_END,
    CONF_FALLBACK_START,
    CONF_FEATURES,
    CONF_FORCE_PRICE,
    CONF_FORCE_SKIP_NEGATIVE,
    CONF_GRANULARITY,
    CONF_HOURS,
    CONF_HOURS_BY_MONTH,
    CONF_HOURS_ENTITY,
    CONF_MAX_PRICE,
    CONF_MODE,
    CONF_POWER_ENTITY,
    CONF_POWER_HOLD_MAX,
    CONF_POWER_THRESHOLD,
    CONF_PRICE_SOURCE,
    CONF_PROFIL,
    CONF_REQUIRE_COMPLETE_DATA,
    CONF_SEGMENT,
    CONF_TARGET_ENTITY,
    CONF_TAX_MODE,
    CONF_THRESHOLD_PRICE,
    CONF_TURPE,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_GRANULARITY,
    DEFAULT_HOURS,
    DEFAULT_POWER_HOLD_MAX,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_PROFIL,
    DEFAULT_REQUIRE_COMPLETE_DATA,
    DEFAULT_TAX_MODE,
    DEFAULT_THRESHOLD_PRICE,
    DEFAULT_TURPE,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
    FEATURE_ALWAYS_RUN,
    FEATURE_EXTRA_RUNTIME,
    FEATURE_FALLBACK,
    FEATURE_MONTHLY_RUNTIME,
    FEATURE_POWER_HOLD,
    FEATURE_RUNTIME_ENTITY,
    MAX_EXTRA_HOURS,
    MAX_HOURS,
    MAX_POWER_THRESHOLD,
    MAX_PRICE_LIMIT,
    MODE_CHEAPEST_SLOTS,
    MODE_THRESHOLD,
    NAME,
    PLAN_MODES,
    PRICE_SOURCE_API_KEY,
    PRICE_SOURCE_PUBLIC_C4,
    PRICE_SOURCE_PUBLIC_C5,
    PRICE_SOURCES,
    SUBENTRY_APPLIANCE,
)
from .coordinator import async_validate_api_key
from .plan import month_hours

TURPE_C5 = ["CU", "CU4", "MU4", "MUDT", "LU"]
TURPE_C4 = ["CU", "LU"]
PROFILS = ["particulier", "pro"]
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

# The features adjusting the runtime are of no use to the threshold mode,
# which has none.
RUNTIME_FEATURES = [FEATURE_MONTHLY_RUNTIME, FEATURE_RUNTIME_ENTITY]
FEATURES = [
    *RUNTIME_FEATURES,
    FEATURE_EXTRA_RUNTIME,
    FEATURE_POWER_HOLD,
    FEATURE_ALWAYS_RUN,
    FEATURE_FALLBACK,
]

# Settings owned by each feature: unticking it drops them.
FEATURE_SETTINGS = {
    FEATURE_MONTHLY_RUNTIME: [CONF_HOURS_BY_MONTH],
    FEATURE_RUNTIME_ENTITY: [CONF_HOURS_ENTITY],
    FEATURE_EXTRA_RUNTIME: [CONF_EXTRA_HOURS],
    FEATURE_POWER_HOLD: [CONF_POWER_ENTITY, CONF_POWER_THRESHOLD, CONF_POWER_HOLD_MAX],
    FEATURE_ALWAYS_RUN: [CONF_FORCE_PRICE, CONF_FORCE_SKIP_NEGATIVE],
    FEATURE_FALLBACK: [CONF_FALLBACK_START, CONF_FALLBACK_END],
}

# Suggested when a feature is first ticked.
DEFAULT_EXTRA_RUNTIME = 1.0
DEFAULT_FORCE_PRICE = 0.0
DEFAULT_FALLBACK_START = "22:00:00"
DEFAULT_FALLBACK_END = "06:00:00"

MONTHS = range(1, 13)

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

HOURS_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=MAX_HOURS,
        step=0.25,
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="h",
    )
)


def _select(
    options: list[str], translation_key: str, *, list_mode: bool = False
) -> selector.SelectSelector:
    """Return a select whose options are translated."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=translation_key,
            mode=(
                selector.SelectSelectorMode.LIST
                if list_mode
                else selector.SelectSelectorMode.DROPDOWN
            ),
        )
    )


def _required(
    key: str, values: Mapping[str, Any], default: Any = vol.UNDEFINED
) -> vol.Required:
    """Return a required field holding its current value, else ``default``."""
    value = values.get(key)
    return vol.Required(key, default=default if value in (None, "") else value)


def _optional(key: str, values: Mapping[str, Any]) -> vol.Optional:
    """Return an optional field showing its current value."""
    # A suggestion, not a default: a cleared field must not bring it back.
    return vol.Optional(key, description={"suggested_value": values.get(key)})


def _schema_keys(schema: vol.Schema) -> set[str]:
    """Return the fields of a form, those of its sections included."""
    keys: set[str] = set()
    for marker, value in schema.schema.items():
        if isinstance(value, section):
            keys |= _schema_keys(value.schema)
        else:
            keys.add(str(marker))
    return keys


# -- Pricing settings -----------------------------------------------------


def _price_source(settings: Mapping[str, Any]) -> str:
    """Return where the prices of ``settings`` come from."""
    if (settings.get(CONF_API_KEY) or "").strip():
        return PRICE_SOURCE_API_KEY
    if settings.get(CONF_SEGMENT) == "C4":
        return PRICE_SOURCE_PUBLIC_C4
    return PRICE_SOURCE_PUBLIC_C5


def _taxes(settings: Mapping[str, Any], key: str) -> str:
    """Return ``ttc`` or ``ht`` from the display or the tax mode setting."""
    value = str(settings.get(key) or DEFAULT_TAX_MODE).lower()
    return value if value in TAX_MODES else DEFAULT_TAX_MODE


def _public_schema(source: str, settings: Mapping[str, Any]) -> vol.Schema:
    """Return the settings of the public prices for a contract segment."""
    turpes = TURPE_C4 if source == PRICE_SOURCE_PUBLIC_C4 else TURPE_C5
    turpe = str(settings.get(CONF_TURPE) or DEFAULT_TURPE).upper()
    if turpe not in turpes:
        turpe = DEFAULT_TURPE if DEFAULT_TURPE in turpes else turpes[0]

    # Translation keys are lower case, the API takes the TURPE in upper case.
    fields: dict[Any, Any] = {
        vol.Required(CONF_TURPE, default=turpe.lower()): _select(
            [option.lower() for option in turpes], CONF_TURPE
        ),
    }
    if source == PRICE_SOURCE_PUBLIC_C5:
        # A C4 contract is always professional and priced without taxes.
        fields[
            vol.Required(CONF_PROFIL, default=settings.get(CONF_PROFIL) or DEFAULT_PROFIL)
        ] = _select(PROFILS, CONF_PROFIL)
        fields[vol.Required(CONF_DISPLAY, default=_taxes(settings, CONF_DISPLAY))] = (
            _select(TAX_MODES, CONF_TAX_MODE)
        )
    fields[
        vol.Required(
            CONF_GRANULARITY,
            default=settings.get(CONF_GRANULARITY) or DEFAULT_GRANULARITY,
        )
    ] = _select(GRANULARITIES, CONF_GRANULARITY)
    return vol.Schema(fields)


def _public_settings(source: str, user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return the settings to store for the public prices."""
    if source == PRICE_SOURCE_PUBLIC_C4:
        segment, profil, taxes = "C4", "pro", "ht"
    else:
        segment, profil, taxes = "C5", user_input[CONF_PROFIL], user_input[CONF_DISPLAY]
    # The display of the public prices and the tax mode of the API key are the
    # same choice: both are kept in step, so switching keeps it.
    return {
        CONF_SEGMENT: segment,
        CONF_TURPE: str(user_input[CONF_TURPE]).upper(),
        CONF_PROFIL: profil,
        CONF_DISPLAY: taxes.upper(),
        CONF_TAX_MODE: taxes,
        CONF_GRANULARITY: user_input[CONF_GRANULARITY],
    }


def _api_key_schema(settings: Mapping[str, Any]) -> vol.Schema:
    """Return the settings of the prices of a Sobry account."""
    return vol.Schema(
        {
            vol.Required(
                CONF_API_KEY,
                description={"suggested_value": settings.get(CONF_API_KEY) or None},
            ): API_KEY_SELECTOR,
            vol.Required(CONF_TAX_MODE, default=_taxes(settings, CONF_TAX_MODE)): _select(
                TAX_MODES, CONF_TAX_MODE
            ),
            vol.Required(
                CONF_GRANULARITY,
                default=settings.get(CONF_GRANULARITY) or DEFAULT_GRANULARITY,
            ): _select(GRANULARITIES, CONF_GRANULARITY),
        }
    )


def _api_key_settings(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return the settings to store for the prices of a Sobry account."""
    taxes = user_input[CONF_TAX_MODE]
    return {
        CONF_API_KEY: str(user_input.get(CONF_API_KEY) or "").strip(),
        CONF_DISPLAY: taxes.upper(),
        CONF_TAX_MODE: taxes,
        CONF_GRANULARITY: user_input[CONF_GRANULARITY],
    }


class SobryPriceSteps:
    """Ask where the prices come from, then only the settings that apply.

    Shared by the setup and the options, which say where the settings stand
    and what to do with the new ones.
    """

    _source = PRICE_SOURCE_PUBLIC_C5

    def _current_settings(self) -> Mapping[str, Any]:
        """Return the settings in force."""
        return {}

    def _async_save_settings(self, settings: dict[str, Any]):
        """Store the new settings and end the flow."""
        raise NotImplementedError

    async def async_step_source(self, user_input=None):
        """Choose between the public prices and those of a Sobry account."""
        if user_input is not None:
            self._source = user_input[CONF_PRICE_SOURCE]
            if self._source == PRICE_SOURCE_API_KEY:
                return await self.async_step_api_key()
            return await self.async_step_public()

        return self.async_show_form(
            step_id="source",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PRICE_SOURCE,
                        default=_price_source(self._current_settings()),
                    ): _select(PRICE_SOURCES, CONF_PRICE_SOURCE, list_mode=True),
                }
            ),
        )

    async def async_step_public(self, user_input=None):
        """Set the tariff options of the public prices."""
        if user_input is not None:
            return self._async_save_settings(_public_settings(self._source, user_input))

        return self.async_show_form(
            step_id="public",
            data_schema=_public_schema(self._source, self._current_settings()),
        )

    async def async_step_api_key(self, user_input=None):
        """Set the API key, refused here if Sobry does not accept it."""
        errors: dict[str, str] = {}
        settings = dict(self._current_settings())
        if user_input is not None:
            new_settings = _api_key_settings(user_input)
            settings.update(new_settings)
            if not new_settings[CONF_API_KEY]:
                errors[CONF_API_KEY] = "invalid_api_key"
            elif error := await async_validate_api_key(self.hass, settings):
                errors["base" if error == "cannot_connect" else CONF_API_KEY] = error
            else:
                return self._async_save_settings(new_settings)

        return self.async_show_form(
            step_id="api_key", data_schema=_api_key_schema(settings), errors=errors
        )


class SobryConfigFlow(SobryPriceSteps, ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sobry."""

    # 2: the appliances moved from the options to subentries.
    VERSION = 2

    async def async_step_user(self, user_input=None):
        """Start with where the prices come from."""
        await self.async_set_unique_id("sobry")
        self._abort_if_unique_id_configured()
        return await self.async_step_source()

    def _async_save_settings(self, settings: dict[str, Any]):
        """Create the entry."""
        return self.async_create_entry(title=NAME, data=settings)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Start over when Sobry refuses the API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for a new API key, or none to go back to the public prices."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if api_key:
                error = await async_validate_api_key(
                    self.hass, {**entry.data, **entry.options, CONF_API_KEY: api_key}
                )
                if error:
                    errors["base" if error == "cannot_connect" else CONF_API_KEY] = error
            if not errors:
                options = dict(entry.options)
                if CONF_API_KEY in options:
                    # A key set from the options wins over the one given at
                    # setup.
                    options[CONF_API_KEY] = api_key
                changed = self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_API_KEY: api_key}, options=options
                )
                # A loaded entry is reloaded by its update listener when it
                # changes; one that failed to set up has no listener.
                if not changed or entry.state is not ConfigEntryState.LOADED:
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SobryOptionsFlow:
        """Change where the prices come from."""
        return SobryOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Each appliance is a subentry."""
        return {SUBENTRY_APPLIANCE: SobryApplianceFlow}


class SobryOptionsFlow(SobryPriceSteps, OptionsFlow):
    """Change the pricing settings; the appliances are subentries."""

    def _current_settings(self) -> Mapping[str, Any]:
        """Return the setup settings, overridden by the options."""
        return {**self.config_entry.data, **self.config_entry.options}

    async def async_step_init(self, user_input=None):
        """Start with where the prices come from."""
        return await self.async_step_source()

    def _async_save_settings(self, settings: dict[str, Any]):
        """Store the settings in the options."""
        # Saved even when empty: an empty key in the options overrides the one
        # given at setup, which is how a key is removed.
        settings.setdefault(CONF_API_KEY, "")
        return self.async_create_entry(data={**self.config_entry.options, **settings})


# -- Appliances -----------------------------------------------------------


def _available_features(mode: str) -> list[str]:
    """Return the features that apply to a selection mode."""
    if mode == MODE_THRESHOLD:
        return [feature for feature in FEATURES if feature not in RUNTIME_FEATURES]
    return list(FEATURES)


def _enabled_features(plan: Mapping[str, Any]) -> list[str]:
    """Return the features a stored plan uses."""
    enabled = {
        FEATURE_MONTHLY_RUNTIME: bool(month_hours(plan.get(CONF_HOURS_BY_MONTH))),
        FEATURE_RUNTIME_ENTITY: bool(plan.get(CONF_HOURS_ENTITY)),
        FEATURE_EXTRA_RUNTIME: bool(plan.get(CONF_EXTRA_HOURS)),
        FEATURE_POWER_HOLD: bool(plan.get(CONF_POWER_ENTITY)),
        FEATURE_ALWAYS_RUN: plan.get(CONF_FORCE_PRICE) not in (None, ""),
        FEATURE_FALLBACK: bool(plan.get(CONF_FALLBACK_START) or plan.get(CONF_FALLBACK_END)),
    }
    return [feature for feature in FEATURES if enabled[feature]]


def _appliance_schema(values: Mapping[str, Any]) -> vol.Schema:
    """Return the identity of an appliance and how its slots are selected."""
    return vol.Schema(
        {
            _required(CONF_NAME, values): selector.TextSelector(),
            _optional(CONF_TARGET_ENTITY, values): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=CONTROLLABLE_DOMAINS)
            ),
            _required(CONF_MODE, values, MODE_CHEAPEST_SLOTS): _select(
                PLAN_MODES, "plan_mode", list_mode=True
            ),
        }
    )


def _schedule_schema(
    mode: str, values: Mapping[str, Any], features: list[str]
) -> vol.Schema:
    """Return the schedule settings of a mode, and the features to set up."""
    fields: dict[Any, Any] = {}
    if mode == MODE_THRESHOLD:
        fields[_required(CONF_THRESHOLD_PRICE, values, DEFAULT_THRESHOLD_PRICE)] = (
            PRICE_SELECTOR
        )
    else:
        fields[_required(CONF_HOURS, values, DEFAULT_HOURS)] = HOURS_SELECTOR
    fields[_required(CONF_WINDOW_START, values, DEFAULT_WINDOW_START)] = (
        selector.TimeSelector()
    )
    fields[_required(CONF_WINDOW_END, values, DEFAULT_WINDOW_END)] = (
        selector.TimeSelector()
    )
    if mode != MODE_THRESHOLD:
        # The threshold mode selects under its own price: no other cap.
        fields[_optional(CONF_MAX_PRICE, values)] = PRICE_SELECTOR
    fields[
        _required(CONF_REQUIRE_COMPLETE_DATA, values, DEFAULT_REQUIRE_COMPLETE_DATA)
    ] = selector.BooleanSelector()

    available = _available_features(mode)
    fields[
        vol.Optional(
            CONF_FEATURES,
            description={
                "suggested_value": [
                    feature for feature in features if feature in available
                ]
            },
        )
    ] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=available,
            translation_key=CONF_FEATURES,
            mode=selector.SelectSelectorMode.LIST,
            multiple=True,
        )
    )
    return vol.Schema(fields)


def _features_schema(features: list[str], values: Mapping[str, Any]) -> vol.Schema:
    """Return one section per ticked feature, holding its settings."""
    sections: dict[str, dict[Any, Any]] = {}
    if FEATURE_MONTHLY_RUNTIME in features:
        sections[FEATURE_MONTHLY_RUNTIME] = {
            _optional(f"month_{month}", values): HOURS_SELECTOR for month in MONTHS
        }
    if FEATURE_RUNTIME_ENTITY in features:
        sections[FEATURE_RUNTIME_ENTITY] = {
            _required(CONF_HOURS_ENTITY, values): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"])
            ),
        }
    if FEATURE_EXTRA_RUNTIME in features:
        sections[FEATURE_EXTRA_RUNTIME] = {
            _required(CONF_EXTRA_HOURS, values, DEFAULT_EXTRA_RUNTIME): (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=MAX_EXTRA_HOURS,
                        step=0.25,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="h",
                    )
                )
            ),
        }
    if FEATURE_POWER_HOLD in features:
        sections[FEATURE_POWER_HOLD] = {
            _required(CONF_POWER_ENTITY, values): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "input_number", "number"])
            ),
            _required(CONF_POWER_THRESHOLD, values, DEFAULT_POWER_THRESHOLD): (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=MAX_POWER_THRESHOLD,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="W",
                    )
                )
            ),
            _required(CONF_POWER_HOLD_MAX, values, DEFAULT_POWER_HOLD_MAX): HOURS_SELECTOR,
        }
    if FEATURE_ALWAYS_RUN in features:
        sections[FEATURE_ALWAYS_RUN] = {
            _required(CONF_FORCE_PRICE, values, DEFAULT_FORCE_PRICE): PRICE_SELECTOR,
            _required(CONF_FORCE_SKIP_NEGATIVE, values, False): selector.BooleanSelector(),
        }
    if FEATURE_FALLBACK in features:
        sections[FEATURE_FALLBACK] = {
            _required(CONF_FALLBACK_START, values, DEFAULT_FALLBACK_START): (
                selector.TimeSelector()
            ),
            _required(CONF_FALLBACK_END, values, DEFAULT_FALLBACK_END): (
                selector.TimeSelector()
            ),
        }
    return vol.Schema(
        {
            vol.Required(name): section(vol.Schema(fields), {"collapsed": False})
            for name, fields in sections.items()
        }
    )


def _plan_data(values: Mapping[str, Any], features: list[str]) -> dict[str, Any]:
    """Return the settings to store: those of the mode and the ticked features."""
    mode = values.get(CONF_MODE, MODE_CHEAPEST_SLOTS)
    keys = [
        CONF_NAME,
        CONF_MODE,
        CONF_TARGET_ENTITY,
        CONF_WINDOW_START,
        CONF_WINDOW_END,
        CONF_REQUIRE_COMPLETE_DATA,
    ]
    if mode == MODE_THRESHOLD:
        keys.append(CONF_THRESHOLD_PRICE)
    else:
        keys.extend((CONF_HOURS, CONF_MAX_PRICE))
    enabled = [feature for feature in features if feature in _available_features(mode)]
    for feature in enabled:
        keys.extend(FEATURE_SETTINGS[feature])

    data = {key: values[key] for key in keys if values.get(key) not in (None, "")}
    if FEATURE_MONTHLY_RUNTIME in enabled:
        months = {
            str(month): float(values[f"month_{month}"])
            for month in MONTHS
            if values.get(f"month_{month}") not in (None, "")
        }
        if months:
            data[CONF_HOURS_BY_MONTH] = months
        else:
            data.pop(CONF_HOURS_BY_MONTH, None)
    return data


class SobryApplianceFlow(ConfigSubentryFlow):
    """Add or change an appliance, asking only what its choices call for."""

    def __init__(self) -> None:
        """Start from no settings, or those of the appliance reconfigured."""
        self._values: dict[str, Any] = {}
        self._features: list[str] = []

    def _update(self, schema: vol.Schema, user_input: Mapping[str, Any]) -> None:
        """Keep a submitted form: a field left empty drops its previous value."""
        for key in _schema_keys(schema):
            self._values.pop(key, None)
        self._values.update(user_input)

    @property
    def _entry(self) -> ConfigEntry:
        """Return the Sobry entry the appliance belongs to."""
        # Neither _get_entry (2025.4) nor _get_reconfigure_entry (gone since)
        # exists in every supported release.
        return self.hass.config_entries.async_get_known_entry(self.handler[0])

    def _name_taken(self, name: str) -> bool:
        """Return whether another appliance already has that name."""
        own = self.context.get("subentry_id")
        return any(
            subentry.subentry_type == SUBENTRY_APPLIANCE
            and subentry.subentry_id != own
            and subentry.title.casefold() == name.casefold()
            for subentry in self._entry.subentries.values()
        )

    async def async_step_user(self, user_input=None):
        """Add an appliance."""
        return await self.async_step_appliance()

    async def async_step_reconfigure(self, user_input=None):
        """Change an appliance, starting from its settings."""
        self._values = dict(self._get_reconfigure_subentry().data)
        self._features = _enabled_features(self._values)
        return await self.async_step_appliance()

    async def async_step_appliance(self, user_input=None):
        """Name the appliance, and choose what it drives and how."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get(CONF_NAME, "")).strip()
            if not name:
                errors[CONF_NAME] = "name_required"
            elif self._name_taken(name):
                errors[CONF_NAME] = "name_taken"
            else:
                self._update(_appliance_schema({}), {**user_input, CONF_NAME: name})
                return await self.async_step_schedule()

        return self.async_show_form(
            step_id="appliance",
            data_schema=_appliance_schema(
                self._values if user_input is None else user_input
            ),
            errors=errors,
        )

    async def async_step_schedule(self, user_input=None):
        """Set the window and the runtime or threshold, and tick the features."""
        mode = self._values.get(CONF_MODE, MODE_CHEAPEST_SLOTS)
        if user_input is not None:
            available = _available_features(mode)
            self._features = [
                feature
                for feature in user_input.get(CONF_FEATURES) or []
                if feature in available
            ]
            self._update(
                _schedule_schema(mode, {}, []),
                {key: value for key, value in user_input.items() if key != CONF_FEATURES},
            )
            if self._features:
                return await self.async_step_features()
            return self._async_finish()

        return self.async_show_form(
            step_id="schedule",
            data_schema=_schedule_schema(mode, self._values, self._features),
        )

    async def async_step_features(self, user_input=None):
        """Set up the features ticked at the previous step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Each feature is a section holding its own fields.
            values = {
                key: value
                for fields in user_input.values()
                for key, value in fields.items()
            }
            if (
                FEATURE_FALLBACK in self._features
                and values.get(CONF_FALLBACK_START) == values.get(CONF_FALLBACK_END)
            ):
                errors["base"] = "fallback_same_times"
            else:
                self._update(_features_schema(self._features, {}), values)
                return self._async_finish()
        else:
            values = {
                **self._values,
                **{
                    f"month_{month}": hours
                    for month, hours in month_hours(
                        self._values.get(CONF_HOURS_BY_MONTH)
                    ).items()
                },
            }

        return self.async_show_form(
            step_id="features",
            data_schema=_features_schema(self._features, values),
            errors=errors,
        )

    @callback
    def _async_finish(self):
        """Create or update the appliance."""
        data = _plan_data(self._values, self._features)
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._entry,
                self._get_reconfigure_subentry(),
                title=data[CONF_NAME],
                data=data,
            )
        return self.async_create_entry(title=data[CONF_NAME], data=data)
