"""Data coordinator for APTREE."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AptreeApiClient,
    AptreeApiError,
    AptreeAuthenticationError,
)
from .archive import BillArchive
from .const import (
    API_EXTEND_TIMEOUT,
    API_UPDATE_TIMEOUT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PUBLIC_MONTHS,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class AptreeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate one API poll for all APTREE entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: AptreeApiClient,
        cache_scope: str,
        archive: BillArchive,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_UPDATE_INTERVAL,
            always_update=False,
        )
        self.api = api
        self.archive = archive
        self._cache_scope = cache_scope
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )
        self._stored_data: dict[str, Any] | None = None
        self._storage_loaded = False
        self._backfill_pending = True

    async def _async_load_storage(self) -> None:
        """Load the persistent archive once per integration runtime."""
        if self._storage_loaded:
            return
        saved = await self._store.async_load()
        if (
            isinstance(saved, Mapping)
            and saved.get("cache_scope") == self._cache_scope
            and isinstance(saved.get("data"), Mapping)
        ):
            self._stored_data = copy.deepcopy(dict(saved["data"]))
        self._storage_loaded = True

    async def async_initialize_from_storage(self) -> None:
        """Initialize entities without waiting for any APTREE network request."""
        await self._async_load_storage()
        self.async_set_updated_data(
            self._public_data(self._stored_data) if self._stored_data else {}
        )

    @property
    def backfill_pending(self) -> bool:
        """Return whether another historical month should be fetched."""
        return self._backfill_pending

    @property
    def archive_data(self) -> dict[str, Any]:
        """Return the complete stored bill archive."""
        return self._stored_data or {}

    async def _async_extend(
        self, details: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], bool]:
        """Extend the archive without ever failing the regular update."""
        try:
            async with asyncio.timeout(API_EXTEND_TIMEOUT):
                return await self.api.async_extend_history(details)
        except (TimeoutError, AptreeApiError) as err:
            _LOGGER.info("APTREE history extension stopped (%s)", type(err).__name__)
            return details, False

    async def _async_update_archive(self, data: dict[str, Any]) -> None:
        """Fetch newly issued original bills in the background."""
        months = [
            str(detail.get("targetMonth"))[:7]
            for detail in data.get("monthlyBillDetails", [])
            if isinstance(detail, Mapping) and detail.get("targetMonth")
        ]
        await self.archive.async_update(months, self.async_update_listeners)

    def _schedule_archive_update(self, data: dict[str, Any]) -> None:
        self.config_entry.async_create_background_task(
            self.hass, self._async_update_archive(data), "APTREE bill PDF archive"
        )

    @staticmethod
    def _public_data(data: dict[str, Any]) -> dict[str, Any]:
        """Limit entity attributes to the latest 13 months.

        The complete archive remains in Home Assistant's integration storage,
        avoiding repeated large recorder attributes as history grows.
        """
        public = copy.deepcopy(data)
        details = public.get("monthlyBillDetails")
        if isinstance(details, list):
            public["monthlyBillDetails"] = details[-PUBLIC_MONTHS:]
        history = public.get("yearlyAmountList")
        if isinstance(history, list):
            public["yearlyAmountList"] = history[-12:]
        return public

    async def _async_update_data(self) -> dict[str, Any]:
        """Check the newest month and append only missing bill details."""
        await self._async_load_storage()
        cached_details = None
        if self._stored_data is not None:
            value = self._stored_data.get("monthlyBillDetails")
            if isinstance(value, list):
                cached_details = value
        try:
            async with asyncio.timeout(API_UPDATE_TIMEOUT):
                data, self._backfill_pending = await self.api.async_get_bill(
                    cached_details
                )
                if not self._backfill_pending:
                    (
                        data["monthlyBillDetails"],
                        self._backfill_pending,
                    ) = await self._async_extend(data["monthlyBillDetails"])
        except TimeoutError as err:
            raise UpdateFailed("APTREE update exceeded the time limit") from err
        except AptreeAuthenticationError as err:
            raise ConfigEntryAuthFailed("APTREE authentication failed") from err
        except AptreeApiError as err:
            if self._stored_data is not None:
                _LOGGER.warning(
                    "APTREE update failed; continuing with the persisted billing archive (%s)",
                    type(err).__name__,
                )
                return self._public_data(self._stored_data)
            raise UpdateFailed(f"APTREE update failed: {err}") from err

        if data != self._stored_data:
            self._stored_data = copy.deepcopy(data)
            await self._store.async_save(
                {
                    "cache_scope": self._cache_scope,
                    "saved_at": datetime.now(UTC).isoformat(),
                    "data": self._stored_data,
                }
            )
        self._schedule_archive_update(data)
        return self._public_data(data)
