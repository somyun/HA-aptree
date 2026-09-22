"""APTREE management fee integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AptreeApiClient
from .coordinator import AptreeDataUpdateCoordinator

PLATFORMS = [Platform.SENSOR]


@dataclass
class AptreeRuntimeData:
    """Runtime objects for one APTREE account."""

    api: AptreeApiClient
    coordinator: AptreeDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up APTREE from a config entry."""
    api = AptreeApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = AptreeDataUpdateCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = AptreeRuntimeData(api=api, coordinator=coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an APTREE config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after credentials are changed in the options flow."""
    await hass.config_entries.async_reload(entry.entry_id)
