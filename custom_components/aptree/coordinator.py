"""Data coordinator for APTREE."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AptreeApiClient,
    AptreeApiError,
    AptreeAuthenticationError,
)
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class AptreeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate one API poll for all APTREE entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: AptreeApiClient,
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest management bill."""
        try:
            return await self.api.async_get_bill()
        except AptreeAuthenticationError as err:
            raise ConfigEntryAuthFailed("APTREE authentication failed") from err
        except AptreeApiError as err:
            raise UpdateFailed(f"APTREE update failed: {err}") from err
