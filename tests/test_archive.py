"""Tests for bill PDF parsing and history extension, using synthetic data."""

from __future__ import annotations

import importlib.util
import sys
import zlib
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase

from test_api import FakeResponse, FakeSession, api_module, token_response

INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "aptree"
spec = importlib.util.spec_from_file_location(
    "aptree_bill_pdf", INTEGRATION_DIR / "bill_pdf.py"
)
bill_pdf = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bill_pdf
spec.loader.exec_module(bill_pdf)


def _page(cells: list[tuple[float, float, str]]) -> bytes:
    body = "BT\n" + "".join(
        f"10 0 0 10 {x} {y} Tm\n{text}\n" for x, y, text in cells
    )
    return b"stream\r\n" + zlib.compress((body + "ET").encode()) + b"\r\nendstream\n"


def _bill(month: str = "(3)Tj") -> bytes:
    front = [
        (31.0, 549.9, "(2025)Tj"),
        (84.0, 549.9, month),
        (159.3, 438.5, "[(1,234)-4000(567)]TJ"),
        (76.0, 106.3, "[(10,100)-3000(9,900)]TJ"),
    ]
    back = [
        (314.9, 509.4, "(100,000)Tj"),
        (542.4, 530.1, "(200,000)Tj"),
        (542.4, 515.9, "(50,000)Tj"),
        (432.7, 500.0, "(10,000)Tj"),
        (455.5, 500.0, "(\\(90,000\\))Tj".replace("\\(90,000\\)", "\\(90,000\\)")),
        (181.9, 320.3, "(8)Tj"),
        (188.1, 320.3, "(\\(310\\))Tj"),
        (258.6, 320.3, "(\\(40,000\\))Tj"),
        (185.5, 143.7, "(\\(.011\\))Tj"),
        (267.4, 143.7, "(\\(900\\))Tj"),
    ]
    return b"%PDF-1.5\n" + _page(front) + _page(back)


class BillPdfTests(TestCase):
    """Read only values the fixed layout really contains."""

    def test_parse_bill(self) -> None:
        self.assertEqual(
            bill_pdf.parse_bill(_bill()),
            {
                "month": "2025-03",
                "total": 100000,
                "sameAreaAverage": 90000,
                "sameAreaMax": 200000,
                "sameAreaMin": 50000,
                "electricity": {"averageUsage": 310, "averageAmount": 40000},
                "heating": {"averageUsage": 0.011, "averageAmount": 900},
                "readings": {"electricity": {"current": 10100, "previous": 9900}},
                "climate_fee": 1234,
                "fuel_adjustment": 567,
            },
        )

    def test_unknown_layout_is_rejected(self) -> None:
        for data in (b"<html></html>", b"%PDF-1.5\n", _bill("(13)Tj")):
            with self.assertRaises(ValueError):
                bill_pdf.parse_bill(data)


def _detail(month: str, total: int, average: int | None = None) -> dict:
    detail = {
        "billingMonth": month,
        "targetMonth": f"{month[:4]}-{month[4:]}-01",
        "totalAmount": {"amount": total},
        "etcList": [{"title": "일반 관리비", "amount": total}],
    }
    if average is not None:
        detail["electricityComparison"] = {"averageUsage": average}
    return detail


class ExtendHistoryTests(IsolatedAsyncioTestCase):
    """Never mislabel another month's bill."""

    async def _extend(self, response: dict, worker) -> tuple[list[dict], bool]:
        session = FakeSession(
            [
                token_response(),
                FakeResponse(
                    200, {"isSuccess": True, "message": "ok", "result": response}
                ),
            ]
        )
        client = api_module.AptreeApiClient(
            session, "user", "password", run_sync=worker
        )
        return await client.async_extend_history(
            [_detail("202606", 300), _detail("202607", 400, 10)]
        )

    async def test_adds_older_month_and_matching_average(self) -> None:
        async def worker(_function, *args):
            return _detail(args[-1], 200)

        details, pending = await self._extend(_detail("202606", 300, 77), worker)
        self.assertEqual(
            [d["billingMonth"] for d in details], ["202605", "202606", "202607"]
        )
        self.assertEqual(details[1]["electricityComparison"]["averageUsage"], 77)
        self.assertTrue(pending)

    async def test_rejects_other_months_average_and_repeated_page(self) -> None:
        async def worker(_function, *args):
            return {**_detail("202606", 300), "billingMonth": args[-1]}

        details, pending = await self._extend(_detail("202607", 400, 10), worker)
        self.assertEqual([d["billingMonth"] for d in details], ["202606", "202607"])
        self.assertNotIn("electricityComparison", details[0])
        self.assertFalse(pending)


class CustomerCodeTests(TestCase):
    """The bill code is read from the billing page's own link."""

    def test_find_customer_code(self) -> None:
        source = (INTEGRATION_DIR / "web_worker.py").read_text(encoding="utf-8")
        start = source.index("CUSTOMER_PATTERN")
        end = source.index("def _request(")
        namespace: dict = {}
        exec("import re\n" + source[start:end], namespace)  # noqa: S102
        page = (
            "//window.open('https://example.com/HP_Bill.aspx?pCustCode=11111111');\n"
            "window.open('https://pdf2.vnrisa.com/HP_Bill_Api.aspx?pParam=Goji"
            "&pYyyymm='+x+'&pCustCode=91234010102021&pRecgubun=0');"
        )
        self.assertEqual(namespace["find_customer_code"](page), "91234010102021")
        with self.assertRaises(ValueError):
            namespace["find_customer_code"]("<html></html>")
