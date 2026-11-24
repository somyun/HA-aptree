"""Async client for the private APTREE resident API."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiohttp import ClientError, ClientSession

from .const import (
    API_APP_BUILD,
    API_APP_PLATFORM,
    API_APP_VERSION,
    API_BASE_URL,
    API_REQUEST_TIMEOUT,
    API_WEB_WORKER_TIMEOUT,
    ARCHIVE_MAX_MONTHS,
    DEFAULT_COMMUNITY_ID,
)

_LOGGER = logging.getLogger(__name__)


def _fetch_web_bill_sync(
    username: str,
    password: str,
    community_id: str,
    month: str,
    customer_code: bool = False,
) -> dict[str, Any]:
    """Fetch one web bill, or the bill code, in a killable child process."""
    worker = Path(__file__).with_name("web_worker.py")
    payload = json.dumps(
        {
            "username": username,
            "password": password,
            "community": community_id,
            "month": month,
            "customer_code": customer_code,
        },
        ensure_ascii=False,
    )
    try:
        result = subprocess.run(
            [sys.executable, str(worker)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=API_WEB_WORKER_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise AptreeConnectionError(
            f"Historical detail worker failed ({type(err).__name__})"
        ) from err
    if result.returncode != 0:
        error_type = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr.strip()
            else "WorkerError"
        )
        raise AptreeResponseError(f"Historical detail worker failed ({error_type})")
    try:
        detail = json.loads(result.stdout)
    except (TypeError, ValueError) as err:
        raise AptreeResponseError(
            "Historical detail worker returned invalid JSON"
        ) from err
    if not isinstance(detail, dict):
        raise AptreeResponseError(
            "Historical detail worker returned an invalid object"
        )
    return detail


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

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        community_id: str = DEFAULT_COMMUNITY_ID,
        run_sync: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._username = username
        self._password = password
        self._community_id = community_id
        self._run_sync = run_sync
        self._client_id = str(uuid4())
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._access_token_expires_at = 0.0
        self._refresh_token_expires_at = 0.0
        self._token_type = "Bearer"
        self._auth_lock = asyncio.Lock()
        self._archive_exhausted = False
        self._average_attempted: set[str] = set()

    async def async_validate_credentials(self) -> None:
        """Validate credentials without fetching private billing data."""
        await self._async_login()

    async def async_get_bill(
        self, cached_details: list[dict[str, Any]] | None = None
    ) -> tuple[dict[str, Any], bool]:
        """Return current data and append at most one missing historical month."""
        latest_month_raw = await self._async_request(
            "GET", "/user/bill/latest-month", authenticated=True
        )
        if not isinstance(latest_month_raw, str) or not latest_month_raw.strip():
            raise AptreeResponseError("The latest billing month was not a string")
        latest_month = self._normalize_billing_month(latest_month_raw)
        latest = await self.async_get_bill_detail(latest_month)
        months = self._history_months(latest, latest_month)

        cached_by_month = {
            self._normalize_billing_month(
                str(detail.get("billingMonth"))
            ): copy.deepcopy(detail)
            for detail in cached_details or []
            if isinstance(detail, Mapping) and detail.get("billingMonth")
        }
        cached_by_month[latest_month] = copy.deepcopy(latest)
        missing_months = [month for month in months if month not in cached_by_month]
        historical_failed = False
        if missing_months:
            month = missing_months[0]
            try:
                if self._run_sync is None:
                    raise AptreeResponseError("Historical detail worker is unavailable")
                cached_by_month[month] = await self._run_sync(
                    _fetch_web_bill_sync,
                    self._username,
                    self._password,
                    self._community_id,
                    month,
                )
            except AptreeApiError as err:
                historical_failed = True
                _LOGGER.warning(
                    "Could not fetch APTREE historical month %s (%s)",
                    month,
                    type(err).__name__,
                )

        latest["monthlyBillDetails"] = [
            cached_by_month[month] for month in sorted(cached_by_month)
        ]
        remaining = not historical_failed and any(
            month not in cached_by_month for month in months
        )
        return latest, remaining

    async def async_extend_history(
        self, details: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], bool]:
        """Add one older month and complete one month's averages.

        Best effort: every failure only ends the attempt until the next start.
        """
        cached = {
            self._normalize_billing_month(str(detail["billingMonth"])): copy.deepcopy(
                detail
            )
            for detail in details
            if detail.get("billingMonth")
        }
        if not cached:
            return details, False
        pending = await self._async_fill_averages(cached, max(cached))
        if not self._archive_exhausted:
            oldest = min(cached)
            if len(cached) >= ARCHIVE_MAX_MONTHS or self._run_sync is None:
                self._archive_exhausted = True
            else:
                month = self._previous_month(oldest)
                try:
                    detail = await self._run_sync(
                        _fetch_web_bill_sync,
                        self._username,
                        self._password,
                        self._community_id,
                        month,
                    )
                except AptreeApiError as err:
                    self._archive_exhausted = True
                    _LOGGER.info("APTREE history before %s unavailable (%s)", oldest, err)
                else:
                    if self._is_end_of_history(detail, cached[oldest]):
                        self._archive_exhausted = True
                    else:
                        cached[month] = detail
        return (
            [cached[month] for month in sorted(cached)],
            pending or not self._archive_exhausted,
        )

    async def async_get_customer_code(self) -> str | None:
        """Return the household's bill code, which original bills require."""
        if self._run_sync is None:
            return None
        try:
            result = await self._run_sync(
                _fetch_web_bill_sync,
                self._username,
                self._password,
                self._community_id,
                "",
                True,
            )
        except AptreeApiError as err:
            _LOGGER.info("APTREE bill code unavailable (%s)", type(err).__name__)
            return None
        code = str(result.get("customerCode", ""))
        return code if code.isascii() and code.isdigit() else None

    @staticmethod
    def _previous_month(month: str) -> str:
        index = int(month[:4]) * 12 + int(month[4:6]) - 2
        return f"{index // 12:04d}{index % 12 + 1:02d}"

    @staticmethod
    def _is_end_of_history(
        detail: Mapping[str, Any], newer: Mapping[str, Any]
    ) -> bool:
        """Detect empty months and pages that repeat a bill already archived."""
        total = detail.get("totalAmount")
        amount = total.get("amount") if isinstance(total, Mapping) else None
        return not amount or all(
            detail.get(key) == newer.get(key) for key in ("totalAmount", "etcList")
        )

    async def _async_fill_averages(
        self, cached: dict[str, dict[str, Any]], latest_month: str
    ) -> bool:
        """Ask the app API once for one month that lacks same-area averages.

        Only a response for the very same bill is accepted, so an API that
        ignores the requested month can never mislabel another month's data.
        """
        candidates = [
            month
            for month in sorted(cached, reverse=True)
            if month != latest_month
            and month not in self._average_attempted
            and not self._has_average(cached[month])
        ]
        if not candidates:
            return False
        month = candidates[0]
        self._average_attempted.add(month)
        try:
            detail = await self.async_get_bill_detail(month)
        except AptreeApiError as err:
            _LOGGER.info(
                "APTREE app API has no detail for %s (%s)", month, type(err).__name__
            )
            return len(candidates) > 1
        if self._has_average(detail) and self._same_bill(
            detail, cached[month], month
        ):
            cached[month] = detail
        else:
            _LOGGER.info("APTREE app API has no usable averages for %s", month)
        return len(candidates) > 1

    @staticmethod
    def _has_average(detail: Mapping[str, Any]) -> bool:
        comparison = detail.get("electricityComparison")
        return isinstance(comparison, Mapping) and bool(comparison.get("averageUsage"))

    @classmethod
    def _same_bill(
        cls, detail: Mapping[str, Any], cached: Mapping[str, Any], month: str
    ) -> bool:
        target = detail.get("targetMonth") or detail.get("billingMonth")
        try:
            if cls._normalize_billing_month(str(target)) != month:
                return False
        except AptreeResponseError:
            return False
        amounts = [
            value.get("amount") if isinstance(value, Mapping) else None
            for value in (detail.get("totalAmount"), cached.get("totalAmount"))
        ]
        try:
            return float(amounts[0]) == float(amounts[1])
        except (TypeError, ValueError):
            return False

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

    @classmethod
    def _history_months(
        cls, latest_detail: Mapping[str, Any], latest_month: str
    ) -> list[str]:
        """Normalize rolling month labels across a year boundary."""
        latest_year = int(latest_month[:4])
        latest_month_number = int(latest_month[4:6])
        months: list[str] = []
        for item in latest_detail.get("yearlyAmountList", []):
            if not isinstance(item, Mapping):
                continue
            raw_month = str(item.get("month", ""))
            try:
                month = cls._normalize_billing_month(raw_month)
            except AptreeResponseError:
                digits = "".join(
                    character for character in raw_month if character.isdigit()
                )
                if len(digits) != 2 or not 1 <= int(digits) <= 12:
                    continue
                number = int(digits)
                year = (
                    latest_year if number <= latest_month_number else latest_year - 1
                )
                month = f"{year:04d}{number:02d}"
            if month not in months:
                months.append(month)
        if latest_month not in months:
            months.append(latest_month)
        return sorted(months)[-12:]

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
            "User-Agent": "HomeAssistant-HA-aptree/0.9.1",
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
