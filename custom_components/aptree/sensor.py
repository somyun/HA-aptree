"""Sensor platform for APTREE management fees."""

from __future__ import annotations

import re
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
from .const import ATTR_BILLING_MONTH, ATTR_HISTORY, ATTR_MONTHLY_DETAILS, DOMAIN
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


def _number(value: Any) -> int | float | None:
    """Return an API value as a finite-looking number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite():
        return None
    return int(number) if number == number.to_integral_value() else float(number)


def _month_start(value: Any) -> str | None:
    """Normalize a month value for chart timestamps."""
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) < 6 or not 1 <= int(digits[4:6]) <= 12:
        return None
    return f"{digits[:4]}-{digits[4:6]}-01"


def _category_title(list_key: str, value: Any) -> str | None:
    """Normalize variable APTREE fee labels into stable chart series names."""
    if not isinstance(value, str) or not (title := value.strip()):
        return None
    if title == "소계":
        return None
    if list_key == "electricityList" and title.startswith("세대 ("):
        return "세대 전기요금"
    if list_key == "electricityList" and title.startswith("전기차"):
        return "전기차 충전요금"
    if list_key == "waterList" and title.startswith("세대 ("):
        return "세대 수도요금"
    if list_key == "waterList" and title.startswith("급탕"):
        return "급탕 요금"
    if title == "생활폐기물수수":
        return "음식물쓰레기 수수료"
    return title


_FOOD_WASTE_TITLES = ("음식물쓰레기 수수료", "생활폐기물수수")


def _list_item_amount(
    detail: Mapping[str, Any], list_key: str, predicate: Callable[[str], bool]
) -> int | None:
    """Return the first matching fee line amount from a bill list."""
    items = detail.get(list_key)
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, Mapping):
            continue
        title = item.get("title")
        if isinstance(title, str) and predicate(title.strip()):
            return _whole_krw(item.get("amount"))
    return None


def _food_waste(detail: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Return the billed food waste fee and its comparable average.

    The app API's trashComparison reports 0 for complexes that bill food waste
    as a flat line item, so the bill line is preferred when it exists.
    """
    trash = detail.get("trashComparison")
    if not isinstance(trash, Mapping):
        trash = {}
    compared = _whole_krw(trash.get("amount"))
    average = _whole_krw(trash.get("averageAmount"))
    billed = _list_item_amount(detail, "etcList", lambda t: t in _FOOD_WASTE_TITLES)
    if billed is None:
        return compared, average
    if compared != billed:
        average = None
    return billed, average


def _water_amount(detail: Mapping[str, Any]) -> int | None:
    """Return the water charge on the app's basis (household + sewer).

    The app API includes sewer charges in waterComparison.amount, while the
    website parser reports only the household line. Add sewer for the latter
    so the monthly series stays comparable with the same-area average.
    """
    water = detail.get("waterComparison")
    amount = _whole_krw(water.get("amount")) if isinstance(water, Mapping) else None
    household = _list_item_amount(detail, "waterList", lambda t: t.startswith("세대"))
    sewer = _list_item_amount(detail, "waterList", lambda t: t.startswith("하수도"))
    if amount is not None and household is not None and sewer and amount == household:
        return amount + sewer
    return amount


def monthly_records(
    data: Mapping[str, Any], pdfs: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return every archived month, completed with values from its PDF bill."""
    records = _monthly_details(data)
    for record in records:
        month = record["month"][:7]
        record["has_pdf"] = month in pdfs
        pdf = pdfs.get(month) or {}
        if not pdf or pdf.get("total") != record["total"]:
            continue
        for key, source in (
            ("same_area_average", "sameAreaAverage"),
            ("same_area_min", "sameAreaMin"),
            ("same_area_max", "sameAreaMax"),
        ):
            if not record.get(key):
                record[key] = pdf.get(source)
        for kind in ("electricity", "water", "heating"):
            comparison = pdf.get(kind) or {}
            for key, source in (
                (f"{kind}_average_usage", "averageUsage"),
                (f"{kind}_average_amount", "averageAmount"),
            ):
                if not record.get(key):
                    record[key] = comparison.get(source)
        for key in ("readings", "climate_fee", "fuel_adjustment"):
            if key in pdf:
                record[key] = pdf[key]
    return records


def _titled_usage(
    detail: Mapping[str, Any], list_key: str, prefix: str
) -> int | float | None:
    """Read the usage printed inside a line title such as ``급탕 2 t``."""
    for item in detail.get(list_key) or []:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title", "")).replace(" ", "")
        match = re.fullmatch(rf"{prefix}\(?([\d.,]*)(?:t|M)\)?", title, re.I)
        if match:
            return _number(match.group(1)) if match.group(1) else 0
    return None


def _monthly_details(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build compact chart-ready records from monthly detailed bills."""
    records: list[dict[str, Any]] = []
    for detail in data.get("monthlyBillDetails", []):
        if not isinstance(detail, Mapping):
            continue
        month = _month_start(detail.get("targetMonth") or detail.get("billingMonth"))
        if month is None:
            continue

        categories: dict[str, int] = {}
        for list_key in ("discountList", "electricityList", "waterList", "etcList"):
            for item in detail.get(list_key, []):
                if not isinstance(item, Mapping):
                    continue
                title = _category_title(list_key, item.get("title"))
                amount = _whole_krw(item.get("amount"))
                if title is None or amount is None:
                    continue
                categories[title] = categories.get(title, 0) + amount

        electricity = detail.get("electricityComparison")
        if not isinstance(electricity, Mapping):
            electricity = {}
        water = detail.get("waterComparison")
        if not isinstance(water, Mapping):
            water = {}
        food_waste_amount, food_waste_average_amount = _food_waste(detail)

        records.append(
            {
                "month": month,
                "total": _amount(detail, "totalAmount"),
                "categories": categories,
                "same_area_average": _whole_krw(detail.get("sameAreaAverage")),
                "same_area_min": _whole_krw(detail.get("sameAreaMin")),
                "same_area_max": _whole_krw(detail.get("sameAreaMax")),
                "due_date": detail.get("dueDate"),
                "hot_water_usage": _titled_usage(detail, "waterList", "급탕"),
                "heating_usage": _titled_usage(detail, "etcList", "난방")
                # Bills omit the metered heating line in months without use.
                or (0 if detail.get("etcList") else None),
                "heating_amount": _list_item_amount(
                    detail, "etcList", lambda t: re.match(r"난방[\s\d.(]*M", t) is not None
                )
                or (0 if detail.get("etcList") else None),
                "electricity_usage": _number(electricity.get("usage")),
                "electricity_amount": _whole_krw(electricity.get("amount")),
                "electricity_average_usage": _number(electricity.get("averageUsage")),
                "electricity_average_amount": _whole_krw(
                    electricity.get("averageAmount")
                ),
                "water_usage": _number(water.get("usage")),
                "water_amount": _water_amount(detail),
                "water_average_usage": _number(water.get("averageUsage")),
                "water_average_amount": _whole_krw(water.get("averageAmount")),
                "food_waste_amount": food_waste_amount,
                "food_waste_average_amount": food_waste_average_amount,
                "discount_items": detail.get("discountList", []),
                "electricity_items": detail.get("electricityList", []),
                "water_items": detail.get("waterList", []),
                "other_items": detail.get("etcList", []),
            }
        )
    return sorted(records, key=lambda record: record["month"])


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
        key="electricity_average_usage",
        translation_key="electricity_average_usage",
        icon="mdi:home-group",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda data: _nested(data, "electricityComparison", "averageUsage"),
    ),
    AptreeSensorEntityDescription(
        key="electricity_average_amount",
        translation_key="electricity_average_amount",
        icon="mdi:home-group",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(
            _nested(data, "electricityComparison", "averageAmount")
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
        value_fn=_water_amount,
    ),
    AptreeSensorEntityDescription(
        key="water_average_usage",
        translation_key="water_average_usage",
        icon="mdi:home-group",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        value_fn=lambda data: _nested(data, "waterComparison", "averageUsage"),
    ),
    AptreeSensorEntityDescription(
        key="water_average_amount",
        translation_key="water_average_amount",
        icon="mdi:home-group",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _whole_krw(
            _nested(data, "waterComparison", "averageAmount")
        ),
    ),
    AptreeSensorEntityDescription(
        key="trash_amount",
        translation_key="trash_amount",
        icon="mdi:trash-can-outline",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_KRW,
        suggested_display_precision=0,
        value_fn=lambda data: _food_waste(data)[0],
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
    _unrecorded_attributes = frozenset({ATTR_HISTORY, ATTR_MONTHLY_DETAILS})
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
        attributes[ATTR_MONTHLY_DETAILS] = _monthly_details(self.coordinator.data)
        return attributes
