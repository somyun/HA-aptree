"""Tests for chart-ready monthly records built by the sensor platform."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest import TestCase

ROOT = Path(__file__).parents[1]
INTEGRATION_DIR = ROOT / "custom_components" / "aptree"


class _StubMeta(type):
    """Stand-in for Home Assistant classes, enums and generics."""

    def __getattr__(cls, name: str) -> str:
        if name.startswith("__"):
            raise AttributeError(name)
        return name

    def __getitem__(cls, _item: object) -> type:
        return cls


@dataclass(frozen=True, kw_only=True)
class _EntityDescription:
    """Stand-in for SensorEntityDescription with the fields the platform uses."""

    key: str
    translation_key: str | None = None
    device_class: object = None
    icon: str | None = None
    native_unit_of_measurement: object = None
    suggested_display_precision: int | None = None


def _stub_module(name: str, *attributes: str) -> ModuleType:
    module = ModuleType(name)
    for attribute in attributes:
        setattr(module, attribute, _StubMeta(attribute, (), {}))
    return module


def _load_sensor_module() -> ModuleType:
    stubs = {
        "homeassistant": (),
        "homeassistant.components": (),
        "homeassistant.components.sensor": (
            "SensorDeviceClass",
            "SensorEntity",
            "SensorEntityDescription",
            "SensorStateClass",
        ),
        "homeassistant.config_entries": ("ConfigEntry",),
        "homeassistant.const": ("UnitOfEnergy", "UnitOfVolume", "EntityCategory"),
        "homeassistant.core": ("HomeAssistant",),
        "homeassistant.helpers": (),
        "homeassistant.helpers.device_registry": ("DeviceInfo",),
        "homeassistant.helpers.entity_platform": ("AddEntitiesCallback",),
        "homeassistant.helpers.update_coordinator": ("CoordinatorEntity",),
        "custom_components.aptree.coordinator": ("AptreeDataUpdateCoordinator",),
    }
    for name, attributes in stubs.items():
        sys.modules.setdefault(name, _stub_module(name, *attributes))
    sys.modules["homeassistant.components.sensor"].SensorEntityDescription = (
        _EntityDescription
    )

    custom_components = ModuleType("custom_components")
    custom_components.__path__ = [str(ROOT / "custom_components")]
    sys.modules.setdefault("custom_components", custom_components)
    package = sys.modules.setdefault(
        "custom_components.aptree", ModuleType("custom_components.aptree")
    )
    package.__path__ = [str(INTEGRATION_DIR)]
    package.AptreeRuntimeData = _StubMeta("AptreeRuntimeData", (), {})

    spec = importlib.util.spec_from_file_location(
        "custom_components.aptree.sensor", INTEGRATION_DIR / "sensor.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sensor = _load_sensor_module()


def _web_detail() -> dict:
    """A month parsed from the website: water amount is the household line only."""
    return {
        "billingMonth": "202606",
        "totalAmount": {"amount": 300000},
        "waterList": [
            {"title": "세대 9 t", "amount": 7000},
            {"title": "하수도 금액", "amount": 4000},
            {"title": "공동 수도료", "amount": -1500},
        ],
        "etcList": [{"title": "음식물쓰레기 수수료", "amount": 200}],
        "waterComparison": {"usage": 9, "amount": 7000},
        "trashComparison": {"amount": 200},
    }


def _api_detail() -> dict:
    """A month from the app API: water includes sewer and trash reports zero."""
    return {
        "billingMonth": "202608",
        "totalAmount": {"amount": 350000},
        "waterList": [
            {"title": "세대 (9t)", "amount": 7000},
            {"title": "하수도금액", "amount": 4000},
            {"title": "소계", "amount": 20000},
        ],
        "etcList": [{"title": "생활폐기물수수", "amount": 400}],
        "waterComparison": {
            "usage": 9,
            "amount": 11000,
            "averageUsage": 19,
            "averageAmount": 24000,
        },
        "trashComparison": {"amount": 0, "averageAmount": 0},
    }


class SensorMappingTests(TestCase):
    """Keep website and app API months on the same basis."""

    def test_water_amount_includes_sewer_for_both_sources(self) -> None:
        self.assertEqual(sensor._water_amount(_web_detail()), 11000)
        self.assertEqual(sensor._water_amount(_api_detail()), 11000)

    def test_water_amount_without_bill_lines_uses_comparison(self) -> None:
        self.assertEqual(
            sensor._water_amount({"waterComparison": {"amount": 5000}}), 5000
        )

    def test_food_waste_prefers_bill_line_over_zero_comparison(self) -> None:
        self.assertEqual(sensor._food_waste(_api_detail()), (400, None))
        self.assertEqual(sensor._food_waste(_web_detail()), (200, None))

    def test_food_waste_keeps_matching_average(self) -> None:
        detail = _api_detail()
        detail["trashComparison"] = {"amount": 400, "averageAmount": 700}
        self.assertEqual(sensor._food_waste(detail), (400, 700))

    def test_food_waste_falls_back_to_comparison(self) -> None:
        detail = {"trashComparison": {"amount": 1200, "averageAmount": 900}}
        self.assertEqual(sensor._food_waste(detail), (1200, 900))

    def test_monthly_details_use_normalized_values(self) -> None:
        records = sensor._monthly_details(
            {"monthlyBillDetails": [_api_detail(), _web_detail()]}
        )
        self.assertEqual([r["month"] for r in records], ["2026-06-01", "2026-08-01"])
        self.assertEqual([r["water_amount"] for r in records], [11000, 11000])
        self.assertEqual([r["food_waste_amount"] for r in records], [200, 400])
        self.assertEqual(records[1]["water_average_amount"], 24000)
        self.assertIsNone(records[1]["food_waste_average_amount"])
