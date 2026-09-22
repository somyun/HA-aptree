"""Redacted diagnostics for APTREE."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import AptreeRuntimeData


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without credentials, tokens, or fee amounts."""
    runtime: AptreeRuntimeData = entry.runtime_data
    data = runtime.coordinator.data
    return {
        "entry": {
            "title": entry.title,
            "username": "**REDACTED**",
        },
        "coordinator": {
            "last_update_success": runtime.coordinator.last_update_success,
            "billing_month": data.get("targetMonth") or data.get("billingMonth"),
            "discount_item_count": len(data.get("discountList", [])),
            "electricity_item_count": len(data.get("electricityList", [])),
            "water_item_count": len(data.get("waterList", [])),
            "other_item_count": len(data.get("etcList", [])),
            "history_item_count": len(data.get("yearlyAmountList", [])),
        },
    }
