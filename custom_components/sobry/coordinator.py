"""Data coordinator for Sobry integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
import json
import logging
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientTimeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
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
    NAME,
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

REQUEST_TIMEOUT = ClientTimeout(total=20)
UPDATE_INTERVAL = timedelta(minutes=15)


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
            url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
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


def error_detail(body: str) -> str:
    """Return the message of an error answer, JSON or not."""
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("message", "detail", "error", "code"):
            if payload.get(key):
                return str(payload[key])
    # An HTML error page from a proxy says nothing useful past its start.
    return " ".join(body.split())[:200] or "unknown error"


class SobryDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage Sobry API data updates."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._slots: list[PriceSlot] = []
        self._slots_source: list[Any] | None = None
        self._entries: dict[datetime, dict[str, Any]] = {}
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )

    @property
    def settings(self) -> dict[str, Any]:
        """Return the merged config entry data and options."""
        return {**self.config_entry.data, **self.config_entry.options}

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device holding the price sensors.

        Named "Sobry" so the entities keep the names and entity ids they had
        before they were grouped on it, such as ``sensor.sobry_current_price``.
        """
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name="Sobry",
            manufacturer=NAME,
            model="Electricity prices",
            entry_type=DeviceEntryType.SERVICE,
        )

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
            async with session.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                status = response.status
                # Read as text: an error answer is not always JSON, and a
                # refused key must be recognised whatever its body. Bytes
                # that are not text end up as an unreadable answer below.
                body = await response.text(errors="replace")
        except (ClientError, TimeoutError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err) or type(err).__name__},
            ) from err

        if status != 200:
            if status == 401 and api_key:
                # Revoked or expired: ask for a new key.
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="invalid_api_key"
                )
            if status == 429:
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="rate_limited"
                )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={
                    "status": str(status),
                    "detail": error_detail(body),
                },
            )

        try:
            payload = json.loads(body)
        except ValueError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="invalid_response"
            ) from err

        if isinstance(payload, dict):
            prices = payload.get("data", [])
            statistics = payload.get("statistics") or {}
            pricing_metadata = payload.get("pricing_metadata") or {}
        elif isinstance(payload, list):
            prices = payload
            statistics = {}
            pricing_metadata = {}
        else:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="invalid_response"
            )

        if not isinstance(prices, list) or not prices:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="no_data")

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
        if not any(extract_price(item) is not None for item in sorted_prices):
            # No usable timestamp, or not a single price.
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="no_data")

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
