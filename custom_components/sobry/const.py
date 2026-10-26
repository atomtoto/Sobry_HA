"""Constants for the Sobry integration."""

DOMAIN = "sobry"
NAME = "Sobry Energy"

CONF_SEGMENT = "segment"
CONF_TURPE = "turpe"
CONF_PROFIL = "profil"
CONF_DISPLAY = "display"
CONF_GRANULARITY = "granularity"
CONF_API_KEY = "api_key"
CONF_TAX_MODE = "tax_mode"

DEFAULT_SEGMENT = "C5"
DEFAULT_TURPE = "CU4"
DEFAULT_PROFIL = "particulier"
DEFAULT_DISPLAY = "TTC"
DEFAULT_GRANULARITY = "quarter_hourly"
DEFAULT_TAX_MODE = "ttc"

# Where the prices come from, asked first so only the settings that apply are
# shown. Derived from the settings, never stored.
CONF_PRICE_SOURCE = "price_source"
PRICE_SOURCE_PUBLIC_C5 = "public_c5"
PRICE_SOURCE_PUBLIC_C4 = "public_c4"
PRICE_SOURCE_API_KEY = "api_key"
PRICE_SOURCES = [PRICE_SOURCE_PUBLIC_C5, PRICE_SOURCE_PUBLIC_C4, PRICE_SOURCE_API_KEY]

PLATFORMS = ["binary_sensor", "number", "sensor", "switch"]

API_BASE_URL = "https://api.sobry.co"

# --- Device control plans -------------------------------------------------
# A plan decides when an appliance should run, based on the price series.
# Each plan is a config subentry of this type; its id is the plan id.

SUBENTRY_APPLIANCE = "appliance"

# Where the plans lived before they became subentries (config entry v1).
CONF_PLANS = "plans"
CONF_PLAN_ID = "plan_id"
CONF_MODE = "mode"
CONF_HOURS = "hours"
CONF_HOURS_ENTITY = "hours_entity"
CONF_HOURS_BY_MONTH = "hours_by_month"
CONF_WINDOW_START = "window_start"
CONF_WINDOW_END = "window_end"
CONF_TARGET_ENTITY = "target_entity"
CONF_MAX_PRICE = "max_price"
CONF_EXTRA_HOURS = "extra_hours"
CONF_POWER_ENTITY = "power_entity"
CONF_POWER_THRESHOLD = "power_threshold"
CONF_POWER_HOLD_MAX = "power_hold_max"
CONF_FALLBACK_START = "fallback_start"
CONF_FALLBACK_END = "fallback_end"
CONF_THRESHOLD_PRICE = "threshold_price"
CONF_FORCE_PRICE = "force_price"
CONF_FORCE_SKIP_NEGATIVE = "force_skip_negative"
CONF_REQUIRE_COMPLETE_DATA = "require_complete_data"

# Optional features of a plan, ticked in the flow so that only their settings
# are asked. Derived from the plan settings, never stored.
CONF_FEATURES = "features"
FEATURE_MONTHLY_RUNTIME = "monthly_runtime"
FEATURE_RUNTIME_ENTITY = "runtime_entity"
FEATURE_EXTRA_RUNTIME = "extra_runtime"
FEATURE_POWER_HOLD = "power_hold"
FEATURE_ALWAYS_RUN = "always_run"
FEATURE_FALLBACK = "fallback_schedule"

# Where the runtime of a plan comes from, exposed as an attribute.
HOURS_SOURCE_MANUAL = "manual"
HOURS_SOURCE_MONTH = "month"
HOURS_SOURCE_ENTITY = "entity"
HOURS_SOURCE_DEFAULT = "default"

MODE_CHEAPEST_SLOTS = "cheapest_slots"
MODE_CHEAPEST_BLOCK = "cheapest_block"
MODE_THRESHOLD = "threshold"
PLAN_MODES = [MODE_CHEAPEST_SLOTS, MODE_CHEAPEST_BLOCK, MODE_THRESHOLD]

DEFAULT_HOURS = 8.0
DEFAULT_EXTRA_HOURS = 0.0
DEFAULT_POWER_THRESHOLD = 100.0
DEFAULT_POWER_HOLD_MAX = 2.0
DEFAULT_WINDOW_START = "00:00:00"
DEFAULT_WINDOW_END = "00:00:00"
DEFAULT_THRESHOLD_PRICE = 0.15
DEFAULT_REQUIRE_COMPLETE_DATA = True

MAX_HOURS = 24.0
MAX_EXTRA_HOURS = 12.0
MAX_POWER_THRESHOLD = 20000.0
MAX_PRICE_LIMIT = 5.0

SERVICE_GET_CHEAPEST_SLOTS = "get_cheapest_slots"
