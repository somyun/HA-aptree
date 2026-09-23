"""APTREE management fee integration."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STARTED,
    Platform,
)
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import AptreeApiClient
from .const import (
    BACKFILL_MAX_STEPS,
    BACKFILL_START_DELAY,
    BACKFILL_STEP_DELAY,
    CONF_COMMUNITY_ID,
    DEFAULT_COMMUNITY_ID,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .coordinator import AptreeDataUpdateCoordinator

PLATFORMS = [Platform.SELECT, Platform.SENSOR]


@dataclass
class AptreeRuntimeData:
    """Runtime objects for one APTREE account."""

    api: AptreeApiClient
    coordinator: AptreeDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up APTREE from a config entry."""
    community_id = entry.data.get(CONF_COMMUNITY_ID, DEFAULT_COMMUNITY_ID)
    cache_scope = hashlib.sha256(
        f"json-v1\0{community_id}\0{entry.data[CONF_USERNAME].casefold()}".encode()
    ).hexdigest()
    api = AptreeApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = AptreeDataUpdateCoordinator(hass, entry, api, cache_scope)
    # Restore persisted data immediately. The potentially slow first backfill
    # must never block Home Assistant startup.
    await coordinator.async_initialize_from_storage()

    entry.runtime_data = AptreeRuntimeData(api=api, coordinator=coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _schedule_backfill(_event: Event | None = None) -> None:
        entry.async_create_background_task(
            hass,
            _async_backfill(coordinator),
            "APTREE paced billing history backfill",
        )

    # The listener itself is cheap and setup returns immediately. Network work
    # begins only after Home Assistant has declared startup complete.
    if hass.state is CoreState.running:
        _schedule_backfill()
    else:
        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _schedule_backfill)
        )
    return True


async def _async_backfill(coordinator: AptreeDataUpdateCoordinator) -> None:
    """Fill history gradually without taking part in Home Assistant startup."""
    await asyncio.sleep(BACKFILL_START_DELAY)
    for step in range(BACKFILL_MAX_STEPS):
        await coordinator.async_request_refresh()
        if not coordinator.last_update_success or not coordinator.backfill_pending:
            return
        if step < BACKFILL_MAX_STEPS - 1:
            await asyncio.sleep(BACKFILL_STEP_DELAY)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an APTREE config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the persisted private billing archive with the config entry."""
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry.entry_id}"
    )
    await store.async_remove()


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after credentials are changed in the options flow."""
    await hass.config_entries.async_reload(entry.entry_id)
