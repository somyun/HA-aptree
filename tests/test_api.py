"""Tests for the standalone APTREE API client."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import IsolatedAsyncioTestCase

ROOT = Path(__file__).parents[1]
INTEGRATION_DIR = ROOT / "custom_components" / "aptree"

# Load the standalone client without importing Home Assistant's integration package.
custom_components_package = ModuleType("custom_components")
custom_components_package.__path__ = [str(ROOT / "custom_components")]
aptree_package = ModuleType("custom_components.aptree")
aptree_package.__path__ = [str(INTEGRATION_DIR)]
sys.modules.setdefault("custom_components", custom_components_package)
sys.modules.setdefault("custom_components.aptree", aptree_package)

aiohttp_stub = ModuleType("aiohttp")


class ClientError(Exception):
    """Stand-in for aiohttp.ClientError during isolated client tests."""


class ClientSession:
    """Stand-in used only to satisfy the client's type import."""


aiohttp_stub.ClientError = ClientError
aiohttp_stub.ClientSession = ClientSession
sys.modules.setdefault("aiohttp", aiohttp_stub)


def _load_module(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, INTEGRATION_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_module("custom_components.aptree.const", "const.py")
api_module = _load_module("custom_components.aptree.api", "api.py")
AptreeApiClient = api_module.AptreeApiClient
AptreeAuthenticationError = api_module.AptreeAuthenticationError


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    async def json(self, *, content_type: str | None = None) -> dict[str, Any]:
        return self._payload


class FakeSession:
    """Return queued responses and retain request metadata."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def token_response(access: str = "access", refresh: str = "refresh") -> FakeResponse:
    """Build a successful token response."""
    now = int(time.time())
    return FakeResponse(
        200,
        {
            "isSuccess": True,
            "message": "ok",
            "result": {
                "accessToken": access,
                "accessTokenExpiresAt": now + 3600,
                "refreshToken": refresh,
                "refreshTokenExpiresAt": now + 86400,
                "tokenType": "Bearer",
            },
        },
    )


class AptreeApiClientTests(IsolatedAsyncioTestCase):
    """Exercise login, billing, and token refresh behavior."""

    async def test_login_and_fetch_latest_bill(self) -> None:
        session = FakeSession(
            [
                token_response(),
                FakeResponse(
                    200,
                    {"isSuccess": True, "message": "ok", "result": "202608"},
                ),
                FakeResponse(
                    200,
                    {
                        "isSuccess": True,
                        "message": "ok",
                        "result": {"totalAmount": {"amount": 123456}},
                    },
                ),
            ]
        )
        client = AptreeApiClient(session, "resident", "secret")  # type: ignore[arg-type]

        bill = await client.async_get_bill()

        self.assertEqual(123456, bill["totalAmount"]["amount"])
        self.assertEqual("202608", bill["billingMonth"])
        self.assertEqual(1, len(bill["monthlyBillDetails"]))
        self.assertEqual("POST", session.requests[0]["method"])
        self.assertNotIn("Authorization", session.requests[0]["headers"])
        self.assertEqual({"billingMonth": "202608"}, session.requests[2]["params"])
        self.assertTrue(
            session.requests[2]["headers"]["Authorization"].startswith("Bearer ")
        )

    async def test_fetches_details_for_each_history_month(self) -> None:
        session = FakeSession(
            [
                token_response(),
                FakeResponse(200, {"isSuccess": True, "result": "202608"}),
                FakeResponse(
                    200,
                    {
                        "isSuccess": True,
                        "result": {
                            "targetMonth": "2026-08-01",
                            "yearlyAmountList": [
                                {"month": "2026-07-01", "amount": 100000},
                                {"month": "2026-08-01", "amount": 110000},
                            ],
                        },
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "isSuccess": True,
                        "result": {"targetMonth": "2026-07-01"},
                    },
                ),
            ]
        )
        client = AptreeApiClient(session, "resident", "secret")  # type: ignore[arg-type]

        bill = await client.async_get_bill()

        self.assertEqual(2, len(bill["monthlyBillDetails"]))
        self.assertEqual({"billingMonth": "202607"}, session.requests[3]["params"])

    async def test_expired_access_token_uses_refresh_token(self) -> None:
        session = FakeSession(
            [
                token_response("old-access", "refresh"),
                token_response("new-access", "new-refresh"),
                FakeResponse(
                    200,
                    {"isSuccess": True, "message": "ok", "result": "202608"},
                ),
                FakeResponse(
                    200,
                    {"isSuccess": True, "message": "ok", "result": {}},
                ),
            ]
        )
        client = AptreeApiClient(session, "resident", "secret")  # type: ignore[arg-type]
        await client.async_validate_credentials()
        client._access_token_expires_at = 0

        await client.async_get_bill()

        self.assertEqual("/auth/refresh", session.requests[1]["url"].split(".kr")[-1])
        self.assertEqual({"refreshToken": "refresh"}, session.requests[1]["json"])

    async def test_invalid_credentials_raise_authentication_error(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    400,
                    {
                        "isSuccess": False,
                        "message": "invalid",
                        "result": None,
                    },
                )
            ]
        )
        client = AptreeApiClient(session, "resident", "wrong")  # type: ignore[arg-type]

        with self.assertRaises(AptreeAuthenticationError):
            await client.async_validate_credentials()
