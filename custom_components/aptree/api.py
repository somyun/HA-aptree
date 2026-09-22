"""Async client for the private APTREE resident API."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from aiohttp import ClientError, ClientSession

from .const import (
    API_APP_BUILD,
    API_APP_PLATFORM,
    API_APP_VERSION,
    API_BASE_URL,
    API_REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class AptreeApiError(Exception):
    """Base APTREE API error."""


class AptreeAuthenticationError(AptreeApiError):
    """Authentication failed."""


class AptreeConnectionError(AptreeApiError):
    """The APTREE service could not be reached."""


class AptreeResponseError(AptreeApiError):
    """The APTREE service returned an unexpected response."""


class AptreeApiClient:
    """Client for APTREE's resident-facing JSON API."""

    def __init__(self, session: ClientSession, username: str, password: str) -> None:
        """Initialize the client."""
        self._session = session
        self._username = username
        self._password = password
        self._client_id = str(uuid4())
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._access_token_expires_at = 0.0
        self._refresh_token_expires_at = 0.0
        self._token_type = "Bearer"
        self._auth_lock = asyncio.Lock()

    async def async_validate_credentials(self) -> None:
        """Validate credentials without fetching private billing data."""
        await self._async_login()

    async def async_get_bill(self) -> dict[str, Any]:
        """Return the newest bill and available monthly bill details."""
        latest_month = await self._async_request(
            "GET", "/user/bill/latest-month", authenticated=True
        )
        if not isinstance(latest_month, str) or not latest_month.strip():
            raise AptreeResponseError("The latest billing month was not a string")

        result = await self.async_get_bill_detail(latest_month)
        result["monthlyBillDetails"] = await self._async_get_monthly_bill_details(
            result, latest_month
        )
        return result

    async def async_get_bill_detail(self, billing_month: str) -> dict[str, Any]:
        """Return one month of detailed billing data."""
        normalized_month = self._normalize_billing_month(billing_month)
        detail = await self._async_request(
            "GET",
            "/user/bill/detail",
            authenticated=True,
            params={"billingMonth": normalized_month},
        )
        if not isinstance(detail, Mapping):
            raise AptreeResponseError("The bill detail was not an object")

        result = dict(detail)
        result.setdefault("billingMonth", normalized_month)
        return result

    async def _async_get_monthly_bill_details(
        self, latest_detail: Mapping[str, Any], latest_month: str
    ) -> list[dict[str, Any]]:
        """Fetch detailed bills for the rolling history with limited concurrency."""
        normalized_latest = self._normalize_billing_month(latest_month)
        latest_year = int(normalized_latest[:4])
        latest_month_number = int(normalized_latest[4:6])
        months: list[str] = []
        for item in latest_detail.get("yearlyAmountList", []):
            if not isinstance(item, Mapping):
                continue
            raw_month = str(item.get("month", ""))
            try:
                month = self._normalize_billing_month(raw_month)
            except AptreeResponseError:
                # The production API currently labels rolling-history rows as
                # "08월" instead of including a year. These rows are older than
                # latest_month, so infer the year across the year boundary.
                digits = "".join(
                    character for character in raw_month if character.isdigit()
                )
                if len(digits) != 2 or not 1 <= int(digits) <= 12:
                    continue
                history_month_number = int(digits)
                history_year = (
                    latest_year
                    if history_month_number < latest_month_number
                    else latest_year - 1
                )
                month = f"{history_year:04d}{history_month_number:02d}"
            if month not in months:
                months.append(month)
        if normalized_latest not in months:
            months.append(normalized_latest)

        # Keep the most recent 12 billing months even if the summary response
        # includes 12 historical rows in addition to the latest bill.
        months = sorted(months)[-12:]

        semaphore = asyncio.Semaphore(3)

        async def _fetch(month: str) -> dict[str, Any] | None:
            if month == normalized_latest:
                return dict(latest_detail)
            try:
                async with semaphore:
                    return await self.async_get_bill_detail(month)
            except AptreeAuthenticationError:
                raise
            except AptreeApiError as err:
                _LOGGER.warning(
                    "Could not fetch APTREE detail for billing month %s (%s)",
                    month,
                    type(err).__name__,
                )
                return None

        details = await asyncio.gather(*(_fetch(month) for month in months))
        return [detail for detail in details if detail is not None]

    @staticmethod
    def _normalize_billing_month(value: str) -> str:
        """Convert YYYYMM and date-like month values to the API's YYYYMM form."""
        digits = "".join(character for character in value if character.isdigit())
        if len(digits) < 6:
            raise AptreeResponseError("The billing month was invalid")
        month = digits[:6]
        if not 1 <= int(month[4:6]) <= 12:
            raise AptreeResponseError("The billing month was invalid")
        return month

    async def _async_authenticate(self, *, force: bool = False) -> None:
        """Ensure that a usable access token is available."""
        if not force and self._token_is_valid(
            self._access_token, self._access_token_expires_at
        ):
            return

        async with self._auth_lock:
            if not force and self._token_is_valid(
                self._access_token, self._access_token_expires_at
            ):
                return

            if self._token_is_valid(
                self._refresh_token, self._refresh_token_expires_at
            ):
                try:
                    await self._async_refresh_access_token()
                    return
                except AptreeAuthenticationError:
                    self._clear_tokens()

            await self._async_login()

    async def _async_login(self) -> None:
        """Sign in with the configured resident credentials."""
        result = await self._async_request(
            "POST",
            "/auth/signin",
            authenticated=False,
            json_body={"userId": self._username, "password": self._password},
        )
        self._apply_tokens(result)

    async def _async_refresh_access_token(self) -> None:
        """Refresh the access token."""
        if not self._refresh_token:
            raise AptreeAuthenticationError("No refresh token is available")
        result = await self._async_request(
            "POST",
            "/auth/refresh",
            authenticated=False,
            json_body={"refreshToken": self._refresh_token},
        )
        self._apply_tokens(result)

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        allow_retry: bool = True,
    ) -> Any:
        """Perform one API request and unwrap APTREE's response envelope."""
        if authenticated:
            await self._async_authenticate()

        headers = self._base_headers()
        if authenticated and self._access_token:
            headers["Authorization"] = f"{self._token_type} {self._access_token}"

        try:
            async with asyncio.timeout(API_REQUEST_TIMEOUT):
                async with self._session.request(
                    method,
                    f"{API_BASE_URL}{path}",
                    headers=headers,
                    params=params,
                    json=json_body,
                ) as response:
                    status = response.status
                    try:
                        payload = await response.json(content_type=None)
                    except (ValueError, TypeError) as err:
                        raise AptreeResponseError(
                            f"APTREE returned a non-JSON response (HTTP {status})"
                        ) from err
        except (TimeoutError, ClientError) as err:
            raise AptreeConnectionError("Could not connect to APTREE") from err

        if status == 401:
            if authenticated and allow_retry:
                await self._async_authenticate(force=True)
                return await self._async_request(
                    method,
                    path,
                    authenticated=True,
                    params=params,
                    json_body=json_body,
                    allow_retry=False,
                )
            raise AptreeAuthenticationError("APTREE rejected the credentials or token")

        if not isinstance(payload, Mapping):
            raise AptreeResponseError(f"Unexpected response type (HTTP {status})")

        message = payload.get("message")
        if status >= 400 or payload.get("isSuccess") is False:
            if path.startswith("/auth/"):
                raise AptreeAuthenticationError(str(message or "Authentication failed"))
            raise AptreeResponseError(
                f"APTREE request failed (HTTP {status}): {message or 'unknown error'}"
            )

        if "result" not in payload:
            raise AptreeResponseError("APTREE response did not include a result")
        return payload["result"]

    def _apply_tokens(self, result: Any) -> None:
        """Apply a sign-in or refresh response without logging token values."""
        if not isinstance(result, Mapping):
            raise AptreeResponseError("The token response was not an object")

        access_token = result.get("accessToken") or result.get("access_token")
        refresh_token = result.get("refreshToken") or result.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            raise AptreeResponseError(
                "The token response did not include an access token"
            )

        self._access_token = access_token
        if isinstance(refresh_token, str) and refresh_token:
            self._refresh_token = refresh_token
        self._access_token_expires_at = self._normalize_timestamp(
            result.get("accessTokenExpiresAt") or result.get("access_token_expires_at")
        )
        self._refresh_token_expires_at = self._normalize_timestamp(
            result.get("refreshTokenExpiresAt")
            or result.get("refresh_token_expires_at")
        )
        token_type = result.get("tokenType") or result.get("token_type")
        if isinstance(token_type, str) and token_type.strip():
            self._token_type = token_type.strip()

    def _base_headers(self) -> dict[str, str]:
        """Return headers expected by the resident API."""
        return {
            "Accept": "application/json",
            "User-Agent": "HomeAssistant-HA-aptree/0.2.2",
            "X-App-Platform": API_APP_PLATFORM,
            "X-App-Version": API_APP_VERSION,
            "X-App-Build": API_APP_BUILD,
            "X-Client-Id": self._client_id,
        }

    @staticmethod
    def _normalize_timestamp(value: Any) -> float:
        """Normalize second or millisecond epoch timestamps."""
        if not isinstance(value, (int, float)):
            return 0.0
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return timestamp

    @staticmethod
    def _token_is_valid(token: str | None, expires_at: float) -> bool:
        """Return whether a token is present and not close to expiry."""
        return bool(token) and expires_at > time.time() + 60

    def _clear_tokens(self) -> None:
        """Forget runtime tokens."""
        self._access_token = None
        self._refresh_token = None
        self._access_token_expires_at = 0.0
        self._refresh_token_expires_at = 0.0
