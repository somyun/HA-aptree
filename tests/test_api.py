"""Tests for the APTREE website client and HTML parsers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase

# Load submodules without importing the integration package, which requires a
# complete Home Assistant test environment.
package_stub = ModuleType("custom_components.aptree")
package_stub.__path__ = [str(Path(__file__).parents[1] / "custom_components" / "aptree")]
sys.modules.setdefault("custom_components.aptree", package_stub)

from custom_components.aptree.api import AptreeApiClient, AptreeAuthenticationError
from custom_components.aptree.html_parser import parse_analysis_page, parse_monthly_bill


def monthly_html(total: int = 351170, electricity: int = 125920, usage: int = 613) -> str:
    return f"""
    <div class="con"><div class="tit_area">납기내 금액</div><div class="detail_area"><ul>
      <li><span class="text">당월부과금</span><span class="fee">{total:,}</span></li>
      <li><span class="text">미납관리비</span><span class="fee">0</span></li>
      <li><span class="text">미납연체료</span><span class="fee">0</span></li>
    </ul></div></div>
    <div class="con"><div class="tit_area">할인합계 금액</div><div class="detail_area"><ul>
      <li><span class="text">수도감면</span><span class="fee">0</span></li>
    </ul></div></div>
    <div class="con">
      <div class="tit_area">전기료 소계</div><div class="detail_area"><ul>
        <li><span class="text">세대 {usage} k</span><span class="fee">{electricity:,}</span></li>
      </ul></div>
      <div class="tit_area">수도료 소계</div><div class="detail_area"><ul>
        <li><span class="text">세대 9 t</span><span class="fee">7,110</span></li>
      </ul></div>
      <div class="tit_area">관리비 소계</div><div class="detail_area"><ul>
        <li><span class="text">생활폐기물수수</span><span class="fee">450</span></li>
        <li><span class="text">일반 관리비</span><span class="fee">45,540</span></li>
      </ul></div>
    </div>"""


def analysis_html() -> str:
    rows = "".join(
        f"<tr><td>{year}/{month:02d}</td><td>{300 + month}</td><td>{month}</td><td>0</td><td>0</td></tr>"
        for year, month in [(2025, m) for m in range(9, 13)] + [(2026, m) for m in range(1, 9)]
    )
    return f"""
    <table><thead><tr><th>년월</th><th>전기</th><th>수도</th><th>온수</th><th>난방</th></tr></thead><tbody>{rows}</tbody></table>
    <table><thead><tr><th>항목</th><th>우리호실 사용량</th><th>동일면적 평균 사용량</th><th>차액</th></tr></thead><tbody>
      <tr><td>전기</td><td>613</td><td>614</td><td>1</td></tr>
      <tr><td>수도</td><td>9</td><td>19</td><td>10</td></tr>
      <tr><td>관리비</td><td>351,170</td><td>344,370</td><td>6,800</td></tr>
    </tbody></table>"""


class ParserTests(TestCase):
    def test_monthly_bill_groups_items_and_usage(self) -> None:
        result = parse_monthly_bill(monthly_html(), "202608")
        self.assertEqual("2026-08-01", result["targetMonth"])
        self.assertEqual(351170, result["totalAmount"]["amount"])
        self.assertEqual(0, result["unpaidAmount"])
        self.assertEqual(0, result["overdueAmount"])
        self.assertEqual(613, result["electricityComparison"]["usage"])
        self.assertEqual("음식물쓰레기 수수료", result["etcList"][0]["title"])

    def test_analysis_page_has_12_months_and_peer_values(self) -> None:
        result = parse_analysis_page(analysis_html())
        self.assertEqual("202608", result["billingMonth"])
        self.assertEqual(12, len(result["yearlyAmountList"]))
        self.assertEqual(614, result["electricityComparison"]["averageUsage"])
        self.assertEqual(344370, result["sameAreaAverage"])


class FakeResponse:
    def __init__(self, text: str, url: str, status: int = 200) -> None:
        self.text = text
        self.url = url
        self.status = status


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def request(self, method: str, url: str, headers: dict, data: dict | None):
        self.requests.append(
            {"method": method, "url": url, "headers": headers, "data": data}
        )
        response = self.responses.pop(0)
        return response.status, response.text, response.url


class ClientTests(IsolatedAsyncioTestCase):
    async def test_login_uses_web_form(self) -> None:
        session = FakeSession([FakeResponse("ok", "https://aptree.co.kr/home/user/6745/member/login.php")])
        await AptreeApiClient(session, "resident", "secret").async_validate_credentials()
        request = session.requests[0]
        self.assertTrue(request["url"].endswith("/home/user/6745/member/login.php"))
        self.assertEqual("resident", request["data"]["login_id"])

    async def test_invalid_login_is_rejected(self) -> None:
        session = FakeSession([FakeResponse("fail", "https://aptree.co.kr/home/user/6745/member/login.php")])
        with self.assertRaises(AptreeAuthenticationError):
            await AptreeApiClient(session, "bad", "bad").async_validate_credentials()

    async def test_bill_backfill_fetches_one_distinct_month_per_call(self) -> None:
        responses = [
            FakeResponse("ok", "https://aptree.co.kr/home/user/6745/member/login.php"),
        ]
        for i in range(12):
            responses.extend(
                [
                    FakeResponse(
                        analysis_html(),
                        "https://aptree.co.kr/home/user/6745/cac_confirm.php",
                    ),
                    FakeResponse(
                        monthly_html(300000 + i),
                        "https://aptree.co.kr/home/user/6745/lib/cac.load_content.php",
                    ),
                ]
            )

        session = FakeSession(responses)
        client = AptreeApiClient(session, "resident", "secret")
        result = None
        for i in range(12):
            cached = result["monthlyBillDetails"] if result else None
            result, pending = await client.async_get_bill(cached)
            self.assertEqual(i < 11, pending)

        requests = [
            request
            for request in session.requests
            if request["url"].endswith("cac.load_content.php")
        ]
        self.assertEqual(12, len(requests))
        self.assertEqual(12, len({request["data"]["date"] for request in requests}))
        self.assertEqual(12, len(result["monthlyBillDetails"]))

    async def test_cached_months_are_not_downloaded_again(self) -> None:
        responses = [
            FakeResponse("ok", "https://aptree.co.kr/home/user/6745/member/login.php"),
        ]
        for i in range(12):
            responses.extend(
                [
                    FakeResponse(
                        analysis_html(),
                        "https://aptree.co.kr/home/user/6745/cac_confirm.php",
                    ),
                    FakeResponse(
                        monthly_html(300000 + i),
                        "https://aptree.co.kr/home/user/6745/lib/cac.load_content.php",
                    ),
                ]
            )
        responses.append(
            FakeResponse(
                analysis_html(),
                "https://aptree.co.kr/home/user/6745/cac_confirm.php",
            )
        )

        session = FakeSession(responses)
        client = AptreeApiClient(session, "resident", "secret")
        result = None
        for _ in range(12):
            cached = result["monthlyBillDetails"] if result else None
            result, _ = await client.async_get_bill(cached)

        second, pending = await client.async_get_bill(result["monthlyBillDetails"])
        detail_requests = [
            request
            for request in session.requests
            if request["url"].endswith("cac.load_content.php")
        ]
        self.assertEqual(12, len(detail_requests))
        self.assertFalse(pending)
        self.assertEqual(12, len(second["monthlyBillDetails"]))

    async def test_network_and_parsing_use_injected_sync_runner(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    "ok",
                    "https://aptree.co.kr/home/user/6745/member/login.php",
                ),
                FakeResponse(
                    analysis_html(),
                    "https://aptree.co.kr/home/user/6745/cac_confirm.php",
                ),
                FakeResponse(
                    monthly_html(),
                    "https://aptree.co.kr/home/user/6745/lib/cac.load_content.php",
                ),
            ]
        )
        parser_calls = []

        async def run_sync(parser, *args):
            parser_calls.append(parser.__name__)
            return parser(*args)

        client = AptreeApiClient(
            session,
            "resident",
            "secret",
            run_sync=run_sync,
        )
        await client.async_get_bill()
        self.assertEqual(
            ["request", "request", "parse_analysis_page", "request", "parse_monthly_bill"],
            parser_calls,
        )
