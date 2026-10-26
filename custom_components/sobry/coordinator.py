"""Data coordinator for Sobry integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_BASE_URL,
    CONF_API_KEY,
    CONF_DISPLAY,
    CONF_GRANULARITY,
    CONF_PROFIL,
    CONF_SEGMENT,
    CONF_TAX_MODE,
    CONF_TURPE,
    DEFAULT_DISPLAY,
    DEFAULT_GRANULARITY,
    DEFAULT_PROFIL,
    DEFAULT_SEGMENT,
    DEFAULT_TAX_MODE,
    DEFAULT_TURPE,
    DOMAIN,
)
from .planner import (
    PriceSlot,
    build_slots,
    entry_start,
    extract_price,
    merge_price_entries,
)

_LOGGER = logging.getLogger(__name__)

# The V2 API returns a separate ``date`` and ``time`` in the Europe/Paris
# timezone rather than the ``timestamp`` field used by the legacy endpoint.
_SOBRY_TIME_ZONE = ZoneInfo("Europe/Paris")


def _entry_dt(item: Any, fold: int = 0) -> datetime | None:
    """Return the UTC start of a raw API entry, whatever the payload shape."""
    if not isinstance(item, dict):
        return None
    if item.get("timestamp"):
        return entry_start(item)

    # /v2/user/daily-prices returns date (YYYY-MM-DD) and time (HH:mm), with
    # the time explicitly documented as Europe/Paris.
    raw_date = item.get("date")
    raw_time = item.get("time")
    if not raw_date or not raw_time:
        return None
    try:
        parsed = datetime.fromisoformat(f"{raw_date}T{raw_time}")
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=_SOBRY_TIME_ZONE, fold=fold).astimezone(dt_util.UTC)


def build_request(
    settings: Mapping[str, Any], today: date
) -> tuple[str, dict[str, Any], dict[str, str] | None]:
    """Return the URL, query and headers asking for the prices around ``today``."""
    segment = settings.get(CONF_SEGMENT, DEFAULT_SEGMENT)
    granularity = settings.get(CONF_GRANULARITY, DEFAULT_GRANULARITY)
    display = settings.get(CONF_DISPLAY, DEFAULT_DISPLAY)
    tax_mode = (
        settings.get(CONF_TAX_MODE, DEFAULT_TAX_MODE) or DEFAULT_TAX_MODE
    ).lower()
    api_key = (settings.get(CONF_API_KEY) or "").strip()

    if api_key:
        if segment == "C4":
            tax_mode = "ht"
        elif tax_mode not in {"ht", "ttc"}:
            tax_mode = "ht" if str(display).upper() == "HT" else "ttc"

        # Today and tomorrow: tomorrow is a forecast until it is published.
        params: dict[str, Any] = {
            "day": today.isoformat(),
            "days": 1,
            "granularity": "15m" if granularity == "quarter_hourly" else "1h",
            "taxMode": tax_mode,
        }
        headers = {"Authorization": "Bearer " + api_key}
        return f"{API_BASE_URL}/v2/user/daily-prices", params, headers

    # ``end`` is excluded: yesterday to tomorrow. Tomorrow comes without
    # prices until it is published; a range starting on an unpublished day is
    # refused, a range ending on one is not.
    params = {
        "start": (today - timedelta(days=1)).isoformat(),
        "end": (today + timedelta(days=2)).isoformat(),
        "granularity": granularity,
        "segment": segment,
        "turpe": settings.get(CONF_TURPE, DEFAULT_TURPE),
        "profil": settings.get(CONF_PROFIL, DEFAULT_PROFIL),
        "display": display,
    }

    if segment == "C4":
        params["profil"] = "pro"
        params["display"] = "HT"

    return f"{API_BASE_URL}/api/prices/raw", params, None


async def async_validate_api_key(
    hass: HomeAssistant, settings: Mapping[str, Any]
) -> str | None:
    """Try the API key of ``settings``, returning an error key if it fails."""
    today = dt_util.now(_SOBRY_TIME_ZONE).date()
    url, params, headers = build_request(settings, today)
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            url, params=params, headers=headers, timeout=20
        ) as response:
            if response.status in (401, 403):
                return "invalid_api_key"
            if response.status != 200:
                _LOGGER.debug(
                    "Sobry API answered %s while checking the API key", response.status
                )
                return "cannot_connect"
    except (ClientError, TimeoutError) as err:
        _LOGGER.debug("Could not reach Sobry to check the API key: %s", err)
        return "cannot_connect"
    return None


class SobryDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage Sobry API data updates."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._slots: list[PriceSlot] = []
        self._slots_source: list[Any] | None = None
        self._entries: dict[datetime, dict[str, Any]] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=15),
        )

    @property
    def settings(self) -> dict[str, Any]:
        """Return the merged config entry data and options."""
        return {**self.entry.data, **self.entry.options}

    @property
    def slot_duration(self) -> timedelta:
        """Return the expected length of a price slot."""
        granularity = self.settings.get(CONF_GRANULARITY, DEFAULT_GRANULARITY)
        return timedelta(hours=1) if granularity == "hourly" else timedelta(minutes=15)

    @property
    def price_slots(self) -> list[PriceSlot]:
        """Return the price series as planning slots, rebuilt on new data.

        The series runs from the start of yesterday, kept across refreshes, to
        the last published price.
        """
        prices = (self.data or {}).get("planning_prices") or []
        if self._slots_source is not prices:
            self._slots = build_slots(prices, self.slot_duration)
            self._entries = {
                start: item
                for item in prices
                if (start := entry_start(item)) is not None
            }
            self._slots_source = prices
        return self._slots

    def slot_at(self, moment: datetime) -> PriceSlot | None:
        """Return the priced slot in force at ``moment``."""
        return next((slot for slot in self.price_slots if slot.contains(moment)), None)

    def slot_after(self, moment: datetime) -> PriceSlot | None:
        """Return the first priced slot starting after ``moment``."""
        return next((slot for slot in self.price_slots if slot.start > moment), None)

    def day_slots(self, moment: datetime) -> list[PriceSlot]:
        """Return the priced slots of the local day of ``moment``."""
        day = dt_util.as_local(moment).date()
        return [
            slot for slot in self.price_slots if dt_util.as_local(slot.start).date() == day
        ]

    def price_entry(self, slot: PriceSlot | None) -> dict[str, Any]:
        """Return the raw API entry a slot was built from."""
        if slot is None:
            return {}
        return self._entries.get(slot.start, {})

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Sobry API."""
        data = self.settings
        api_key = (data.get(CONF_API_KEY) or "").strip()
        # Prices are published per day in France, whatever the zone of Home
        # Assistant or of the host.
        today = dt_util.now(_SOBRY_TIME_ZONE).date()
        url, params, headers = build_request(data, today)

        session = async_get_clientsession(self.hass)

        try:
            async with session.get(url, params=params, headers=headers, timeout=20) as response:
                if response.status != 200:
                    error_code = ""
                    error_message = ""
                    body = ""
                    try:
                        error_payload = await response.json()
                        if isinstance(error_payload, dict):
                            error_code = str(error_payload.get("error", "") or error_payload.get("code", ""))
                            error_message = str(error_payload.get("message", "") or error_payload.get("detail", ""))
                    except ValueError:
                        body = await response.text()

                    if response.status == 401 and api_key:
                        # Revoked or expired: ask for a new key.
                        if (
                            error_code == "INVALID_API_KEY"
                            or "INVALID_API_KEY" in error_message
                            or "INVALID_API_KEY" in body
                        ):
                            raise ConfigEntryAuthFailed(
                                "Sobry API authentication failed: invalid API key"
                            )
                        raise ConfigEntryAuthFailed(
                            "Sobry API authentication failed: unauthorized API key"
                        )

                    if response.status == 429:
                        raise UpdateFailed("Sobry API rate limit reached (HTTP 429), retrying on next refresh")

                    detail = error_message or error_code or body or "unknown error"
                    raise UpdateFailed(f"Sobry API error {response.status}: {detail}")

                payload = await response.json()
        except (ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Could not fetch Sobry data: {err}") from err

        if isinstance(payload, dict):
            prices = payload.get("data", [])
            statistics = payload.get("statistics", {})
            pricing_metadata = payload.get("pricing_metadata", {})
        elif isinstance(payload, list):
            prices = payload
            statistics = {}
            pricing_metadata = {}
        else:
            raise UpdateFailed("Sobry API returned an unsupported payload format")

        if not prices:
            raise UpdateFailed("Sobry API returned no pricing data")

        normalized_prices = []
        local_times: set[tuple[Any, Any]] = set()
        for item in prices:
            if not isinstance(item, dict):
                continue
            fold = 0
            if not item.get("timestamp"):
                # Local times only: the second occurrence of a time is the
                # hour repeated when the clocks go back.
                local_time = (item.get("date"), item.get("time"))
                fold = int(local_time in local_times)
                local_times.add(local_time)
            item_dt = _entry_dt(item, fold)
            if item_dt is None:
                continue
            # Retain the API fields and provide a consistent timestamp for
            # sorting and the current-price sensor attributes.
            normalized_prices.append({**item, "timestamp": item_dt.isoformat()})

        sorted_prices = sorted(normalized_prices, key=entry_start)
        if not sorted_prices:
            raise UpdateFailed("Sobry API returned only invalid timestamps")

        if not any(extract_price(item) is not None for item in sorted_prices):
            raise UpdateFailed("Sobry API returned no usable price values")

        # The prices of the evening before stay available for planning after
        # midnight, whatever the API still serves.
        planning_prices = merge_price_entries(
            (self.data or {}).get("planning_prices") or [],
            sorted_prices,
            dt_util.start_of_local_day(dt_util.now().date() - timedelta(days=1)),
        )
        # Exposed as before: the priced entries from today on, tomorrow
        # included once published, without the placeholders of the hours
        # that are not.
        start_of_today = datetime.combine(today, time(0), tzinfo=_SOBRY_TIME_ZONE)
        published = [
            item
            for item in sorted_prices
            if entry_start(item) >= start_of_today and extract_price(item) is not None
        ]

        return {
            "raw_payload": payload,
            "prices": published,
            "planning_prices": planning_prices,
            "statistics": statistics,
            "count": len(published),
            "pricing_metadata": pricing_metadata,
        }
