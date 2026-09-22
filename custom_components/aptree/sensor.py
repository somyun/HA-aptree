"""Sensor platform for APTREE management fees."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AptreeRuntimeData
from .const import ATTR_BILLING_MONTH, ATTR_HISTORY, DOMAIN
from .coordinator import AptreeDataUpdateCoordinator

CURRENCY_KRW = "KRW"


def _nested(data: Mapping[str, Any], *path: str) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _amount(data: Mapping[str, Any], key: str) -> Any:
    return _whole_krw(_nested(data, key, "amount"))


def _whole_krw(value: Any) -> int | None:
    """Normalize Korean won amounts to whole numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = str(value).replace(",", "")
        return int(Decimal(normalized).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


@dataclass(frozen=True, kw_only=True)
class AptreeSensorEntityDescription(SensorEntityDescription):
    """Describe one scalar APTREE sensor."""

    value_fn: Callable[[Mapping[str, Any]], Any]


SENSOR_DESCRIPTIONS: tuple[AptreeSensorEntityDescription, ...] = (
    AptreeSensorEntityDescription(
        key="total_amount",
        translation_key="total_amount",
        icon="mdi:cash-multiple",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _amount(data, "totalAmount"),
    ),
    AptreeSensorEntityDescription(
        key="due_date",
        translation_key="due_date",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda data: _parse_date(data.get("dueDate")),
    ),
    AptreeSensorEntityDescription(
        key="unpaid_amount",
        translation_key="unpaid_amount",
        icon="mdi:cash-alert",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(data.get("unpaidAmount")),
    ),
    AptreeSensorEntityDescription(
        key="overdue_amount",
        translation_key="overdue_amount",
        icon="mdi:calendar-alert",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(data.get("overdueAmount")),
    ),
    AptreeSensorEntityDescription(
        key="same_area_average",
        translation_key="same_area_average",
        icon="mdi:home-group",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(data.get("sameAreaAverage")),
    ),
    AptreeSensorEntityDescription(
        key="same_area_minimum",
        translation_key="same_area_minimum",
        icon="mdi:arrow-down-bold-circle-outline",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(data.get("sameAreaMin")),
    ),
    AptreeSensorEntityDescription(
        key="same_area_maximum",
        translation_key="same_area_maximum",
        icon="mdi:arrow-up-bold-circle-outline",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(data.get("sameAreaMax")),
    ),
    AptreeSensorEntityDescription(
        key="electricity_usage",
        translation_key="electricity_usage",
        icon="mdi:lightning-bolt",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda data: _nested(data, "electricityComparison", "usage"),
    ),
    AptreeSensorEntityDescription(
        key="electricity_amount",
        translation_key="electricity_amount",
        icon="mdi:cash",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(
            _nested(data, "electricityComparison", "amount")
        ),
    ),
    AptreeSensorEntityDescription(
        key="water_usage",
        translation_key="water_usage",
        icon="mdi:water",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        value_fn=lambda data: _nested(data, "waterComparison", "usage"),
    ),
    AptreeSensorEntityDescription(
        key="water_amount",
        translation_key="water_amount",
        icon="mdi:cash",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(_nested(data, "waterComparison", "amount")),
    ),
    AptreeSensorEntityDescription(
        key="trash_amount",
        translation_key="trash_amount",
        icon="mdi:trash-can-outline",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(_nested(data, "trashComparison", "amount")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up APTREE sensors from one config entry."""
    runtime: AptreeRuntimeData = entry.runtime_data
    entities: list[SensorEntity] = [
        AptreeSensor(runtime.coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.extend(
        (
            AptreeBillDetailsSensor(runtime.coordinator, entry),
            AptreeYearlyHistorySensor(runtime.coordinator, entry),
        )
    )
    async_add_entities(entities)


class AptreeBaseSensor(CoordinatorEntity[AptreeDataUpdateCoordinator], SensorEntity):
    """Base class for APTREE sensors."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AptreeDataUpdateCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        """Initialize common entity metadata."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="BK Winner",
            model="APTREE Resident",
            name="APTREE",
            configuration_url="https://aptree.co.kr/",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the billing month on every entity."""
        month = self.coordinator.data.get("targetMonth") or self.coordinator.data.get(
            "billingMonth"
        )
        return {ATTR_BILLING_MONTH: month} if month else {}


class AptreeSensor(AptreeBaseSensor):
    """A scalar value from the latest bill."""

    entity_description: AptreeSensorEntityDescription

    def __init__(
        self,
        coordinator: AptreeDataUpdateCoordinator,
        entry: ConfigEntry,
        description: AptreeSensorEntityDescription,
    ) -> None:
        """Initialize a scalar sensor."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator.data)


class AptreeBillDetailsSensor(AptreeBaseSensor):
    """Expose itemized bill data for dashboards and templates."""

    _attr_translation_key = "bill_details"
    _attr_icon = "mdi:receipt-text"

    def __init__(
        self, coordinator: AptreeDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the bill details sensor."""
        super().__init__(coordinator, entry, "bill_details")

    @property
    def native_value(self) -> str | None:
        """Use the bill month as the compact entity state."""
        return self.coordinator.data.get("targetMonth") or self.coordinator.data.get(
            "billingMonth"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return itemized categories without authentication data."""
        data = self.coordinator.data
        attributes = super().extra_state_attributes
        attributes.update(
            {
                "due_date": data.get("dueDate"),
                "auto_transfer": data.get("autoTransfer"),
                "auto_transfer_info": data.get("autoTransferInfo"),
                "discount_items": data.get("discountList", []),
                "electricity_items": data.get("electricityList", []),
                "water_items": data.get("waterList", []),
                "other_items": data.get("etcList", []),
            }
        )
        return attributes


class AptreeYearlyHistorySensor(AptreeBaseSensor):
    """Expose the API's rolling 12-month history."""

    _attr_translation_key = "yearly_history"
    _attr_icon = "mdi:chart-line"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_KRW
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: AptreeDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialize the history sensor."""
        super().__init__(coordinator, entry, "yearly_history")

    @property
    def native_value(self) -> int | float | None:
        """Return the newest total amount."""
        return _amount(self.coordinator.data, "totalAmount")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the 12-month series."""
        attributes = super().extra_state_attributes
        attributes[ATTR_HISTORY] = self.coordinator.data.get("yearlyAmountList", [])
        return attributes
