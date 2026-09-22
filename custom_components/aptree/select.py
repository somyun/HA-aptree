"""Select platform for browsing APTREE billing months."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AptreeRuntimeData
from .const import DOMAIN
from .coordinator import AptreeDataUpdateCoordinator


def _available_months(data: dict[str, Any]) -> list[str]:
    """Return unique chart months in chronological order."""
    months = {
        str(detail.get("targetMonth") or detail.get("billingMonth"))[:7]
        for detail in data.get("monthlyBillDetails", [])
        if isinstance(detail, dict)
        and (detail.get("targetMonth") or detail.get("billingMonth"))
    }
    return sorted(month for month in months if len(month) == 7)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the billing-month selector."""
    runtime: AptreeRuntimeData = entry.runtime_data
    async_add_entities([AptreeBillingMonthSelect(runtime.coordinator, entry)])


class AptreeBillingMonthSelect(
    CoordinatorEntity[AptreeDataUpdateCoordinator], SelectEntity
):
    """Choose which cached billing month dashboards should display."""

    _attr_has_entity_name = True
    _attr_translation_key = "billing_month"
    _attr_icon = "mdi:calendar-month"

    def __init__(
        self,
        coordinator: AptreeDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the selector."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_billing_month"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="BK Winner",
            model="APTREE Resident",
            name="APTREE",
            configuration_url="https://aptree.co.kr/",
        )
        self._selected: str | None = None

    @property
    def options(self) -> list[str]:
        """Return available billing months."""
        return _available_months(self.coordinator.data)

    @property
    def current_option(self) -> str | None:
        """Return the selected month, defaulting to the latest available month."""
        options = self.options
        if self._selected in options:
            return self._selected
        return options[-1] if options else None

    async def async_select_option(self, option: str) -> None:
        """Select a billing month."""
        if option not in self.options:
            raise ValueError(f"Unknown billing month: {option}")
        self._selected = option
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Keep the selection when it remains available after a refresh."""
        if self._selected not in self.options:
            self._selected = self.options[-1] if self.options else None
        super()._handle_coordinator_update()
