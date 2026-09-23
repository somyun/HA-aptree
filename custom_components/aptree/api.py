"""Async client for the APTREE resident website."""

from __future__ import annotations

import asyncio
import copy
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import API_BASE_URL, API_REQUEST_TIMEOUT, DEFAULT_COMMUNITY_ID
from .html_parser import parse_analysis_page, parse_monthly_bill


class AptreeApiError(Exception):
    """Base APTREE API error."""


class AptreeAuthenticationError(AptreeApiError):
    """Authentication failed."""


class AptreeConnectionError(AptreeApiError):
    """The APTREE service could not be reached."""


class AptreeResponseError(AptreeApiError):
    """The APTREE service returned an unexpected response."""


class AptreeApiClient:
    """Client for the logged-in APTREE resident website."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        community_id: str = DEFAULT_COMMUNITY_ID,
        run_sync: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._community_id = self._normalize_community_id(community_id)
        self._run_sync = run_sync
        self._authenticated = False
        self._auth_lock = asyncio.Lock()

    @property
    def _site_url(self) -> str:
        return f"{API_BASE_URL}/home/user/{self._community_id}"

    async def async_validate_credentials(self) -> None:
        """Validate credentials against the resident website."""
        await self._async_login(force=True)

    async def async_get_bill(
        self, cached_details: list[dict[str, Any]] | None = None
    ) -> tuple[dict[str, Any], bool]:
        """Return billing data and fetch at most one missing month."""
        await self._async_login()
        analysis_html = await self._async_request_text("GET", "/cac_confirm.php")
        try:
            analysis = await self._async_parse(parse_analysis_page, analysis_html)
        except ValueError as err:
            raise AptreeResponseError("Could not parse the APTREE analysis page") from err

        latest_month = analysis["billingMonth"]
        months = [item["month"] for item in analysis["yearlyAmountList"]][-12:]
        if latest_month not in months:
            months.append(latest_month)
        months = sorted(set(months))[-12:]

        cached_by_month = {
            self._normalize_billing_month(str(detail.get("billingMonth"))): copy.deepcopy(detail)
            for detail in cached_details or []
            if isinstance(detail, Mapping) and detail.get("billingMonth")
        }
        missing_months = [month for month in months if month not in cached_by_month]

        # The newest bill is useful immediately. Remaining historical months are
        # then filled from oldest to newest, one coordinator refresh at a time.
        month_to_fetch = None
        if latest_month in missing_months:
            month_to_fetch = latest_month
        elif missing_months:
            month_to_fetch = missing_months[0]

        if month_to_fetch is not None:
            html = await self._async_request_text(
                "POST",
                "/lib/cac.load_content.php",
                data={"date": month_to_fetch},
            )
            try:
                detail = await self._async_parse(
                    parse_monthly_bill, html, month_to_fetch
                )
            except ValueError as err:
                raise AptreeResponseError(
                    f"Could not parse the APTREE bill for {month_to_fetch}"
                ) from err
            cached_by_month[detail["billingMonth"]] = detail

        details = [cached_by_month[month] for month in sorted(cached_by_month)]
        if not details:
            raise AptreeResponseError("APTREE did not return any monthly bill details")

        latest = next(
            (detail for detail in details if detail["billingMonth"] == latest_month),
            details[-1],
        )

        # Total history comes from the independently fetched monthly pages;
        # this prevents one latest value being repeated for every month.
        visible_details = [
            detail for detail in details if detail["billingMonth"] in set(months)
        ]
        analysis["yearlyAmountList"] = [
            {
                "month": detail["billingMonth"],
                "amount": detail["totalAmount"]["amount"],
            }
            for detail in visible_details
        ]
        latest.update(analysis)
        latest["monthlyBillDetails"] = details

        # The website publishes same-area comparisons for the newest month
        # only. Attach the exact values there without fabricating old averages.
        for detail in details:
            if detail["billingMonth"] != latest_month:
                continue
            for key in ("electricityComparison", "waterComparison"):
                detail.setdefault(key, {}).update(analysis.get(key, {}))

        remaining = any(month not in cached_by_month for month in months)
        return latest, remaining

    async def async_get_bill_detail(self, billing_month: str) -> dict[str, Any]:
        """Return one month of detailed web billing data."""
        month = self._normalize_billing_month(billing_month)
        await self._async_login()
        html = await self._async_request_text(
            "POST", "/lib/cac.load_content.php", data={"date": month}
        )
        try:
            return await self._async_parse(parse_monthly_bill, html, month)
        except ValueError as err:
            raise AptreeResponseError(f"Could not parse the APTREE bill for {month}") from err

    async def _async_login(self, *, force: bool = False) -> None:
        if self._authenticated and not force:
            return
        async with self._auth_lock:
            if self._authenticated and not force:
                return
            response = await self._async_request_text(
                "POST",
                "/member/login.php",
                authenticated=False,
                data={
                    "login_id": self._username,
                    "login_pw": self._password,
                    "login_id_save": "",
                    "login_auto": "",
                    "width": "1920",
                    "height": "1080",
                },
            )
            if response.strip().lower() != "ok":
                raise AptreeAuthenticationError("APTREE rejected the credentials")
            self._authenticated = True

    async def _async_request_text(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        data: Mapping[str, str] | None = None,
        retry: bool = True,
    ) -> str:
        if authenticated:
            await self._async_login()
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "User-Agent": "HomeAssistant-HA-aptree/0.5.0",
            "Referer": f"{self._site_url}/cac.php",
        }
        try:
            async with asyncio.timeout(API_REQUEST_TIMEOUT):
                async with self._session.request(
                    method,
                    f"{self._site_url}{path}",
                    headers=headers,
                    data=data,
                ) as response:
                    status = response.status
                    text = await response.text(errors="replace")
                    final_url = str(response.url)
        except (TimeoutError, ClientError) as err:
            raise AptreeConnectionError("Could not connect to APTREE") from err

        if status in (401, 403) or ("/member/login" in final_url and authenticated):
            self._authenticated = False
            if authenticated and retry:
                await self._async_login(force=True)
                return await self._async_request_text(method, path, data=data, retry=False)
            raise AptreeAuthenticationError("APTREE login session expired")
        if status >= 400:
            raise AptreeResponseError(f"APTREE website returned HTTP {status}")
        return text

    async def _async_parse(self, parser: Callable[..., Any], *args: Any) -> Any:
        """Run CPU-bound HTML parsing outside Home Assistant's event loop."""
        if self._run_sync is not None:
            return await self._run_sync(parser, *args)
        return parser(*args)

    @staticmethod
    def _normalize_community_id(value: str) -> str:
        match = re.fullmatch(r"\d+", str(value).strip())
        if not match:
            raise AptreeResponseError("The APTREE community number was invalid")
        return match.group(0)

    @staticmethod
    def _normalize_billing_month(value: str) -> str:
        digits = "".join(character for character in str(value) if character.isdigit())
        if len(digits) < 6 or not 1 <= int(digits[4:6]) <= 12:
            raise AptreeResponseError("The billing month was invalid")
        return digits[:6]
