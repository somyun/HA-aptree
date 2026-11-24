"""Private on-disk archive of the original bill PDFs."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession
from homeassistant.core import HomeAssistant

from .bill_pdf import parse_bill
from .const import (
    PDF_ATTEMPTS,
    PDF_DIRECTORY,
    PDF_HOST_SUFFIX,
    PDF_MAX_BYTES,
    PDF_MAX_FAILURES,
    PDF_REQUEST_GAP,
    PDF_RETRY_DELAY,
    PDF_RETRY_INTERVAL,
    PDF_TIMEOUT,
    PDF_URL,
)

_LOGGER = logging.getLogger(__name__)
MONTH_PATTERN = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


class BillArchive:
    """Download, keep and parse bill PDFs for one household.

    Files live outside ``www`` because a bill contains personal data; they are
    only reachable through the authenticated view in ``views.py``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: ClientSession,
        entry_id: str,
        community_id: str,
        building: str,
        unit: str,
        get_customer_code: Callable[[], Awaitable[str | None]],
    ) -> None:
        """Initialize the archive."""
        self._hass = hass
        self._session = session
        self._community_id = community_id
        self._building = building
        self._unit = unit
        self._get_customer_code = get_customer_code
        self._customer_code: str | None = None
        self._directory = Path(hass.config.path(PDF_DIRECTORY, entry_id))
        self._attempted: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self.parsed: dict[str, dict[str, Any]] = {}

    @property
    def months(self) -> list[str]:
        """Return months whose PDF is stored."""
        return sorted(self.parsed)

    def path(self, month: str) -> Path | None:
        """Return the stored file for a month, if there is one."""
        if not MONTH_PATTERN.match(month) or month not in self.parsed:
            return None
        return self._directory / f"{month}.pdf"

    async def async_load(self) -> None:
        """Index PDFs that are already stored."""
        self.parsed = await self._hass.async_add_executor_job(self._load)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._directory.is_dir():
            return {}
        result: dict[str, dict[str, Any]] = {}
        for file in sorted(self._directory.glob("*.pdf")):
            if MONTH_PATTERN.match(file.stem):
                result[file.stem] = _safe_parse(file.read_bytes(), file.stem)
        return result

    async def async_update(
        self, months: list[str], on_added: Callable[[], None]
    ) -> None:
        """Download every missing PDF, newest first, one at a time."""
        if not self._building or not self._unit or self._lock.locked():
            return
        async with self._lock:
            failures = 0
            for month in sorted(set(months), reverse=True):
                if month in self.parsed or not MONTH_PATTERN.match(month):
                    continue
                now = datetime.now(UTC)
                last = self._attempted.get(month)
                if last is not None and now - last < timedelta(
                    seconds=PDF_RETRY_INTERVAL
                ):
                    continue
                if self._customer_code is None:
                    self._customer_code = await self._async_load_customer_code()
                    if self._customer_code is None:
                        return
                self._attempted[month] = now
                data = await self._async_download_with_retry(month)
                if data is None:
                    failures += 1
                    if failures >= PDF_MAX_FAILURES:
                        return
                    continue
                failures = 0
                try:
                    self.parsed[month] = await self._hass.async_add_executor_job(
                        self._save, month, data
                    )
                except OSError as err:
                    _LOGGER.warning(
                        "Could not store APTREE bill PDF (%s)", type(err).__name__
                    )
                    return
                on_added()
                await asyncio.sleep(PDF_REQUEST_GAP)

    async def _async_load_customer_code(self) -> str | None:
        code = await self._get_customer_code()
        if code is None:
            return None
        # Never fetch another household's bill if the settings do not match.
        household = (
            f"{self._community_id}{self._building.zfill(4)}{self._unit.zfill(4)}"
        )
        if household not in code:
            _LOGGER.warning("APTREE building or unit does not match the account")
            return None
        return code

    async def _async_download_with_retry(self, month: str) -> bytes | None:
        """Retry once when the server does not return a PDF."""
        for attempt in range(PDF_ATTEMPTS):
            if attempt:
                await asyncio.sleep(PDF_RETRY_DELAY)
            if (data := await self._async_download(month)) is not None:
                return data
        return None

    async def _async_download(self, month: str) -> bytes | None:
        params = {
            "pParam": "Goji",
            "pYyyymm": month.replace("-", ""),
            "pCustCode": self._customer_code or "",
            "pRecgubun": "0",
            "pSerGbn": "1",
            "pRentGbn": "1",
        }
        try:
            async with asyncio.timeout(PDF_TIMEOUT):
                async with self._session.get(
                    PDF_URL, params=params, max_redirects=3
                ) as response:
                    if (
                        response.status != 200
                        or response.url.scheme != "https"
                        or not str(response.url.host).endswith(PDF_HOST_SUFFIX)
                    ):
                        return None
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        chunks.extend(chunk)
                        if len(chunks) > PDF_MAX_BYTES:
                            return None
        except (TimeoutError, ClientError) as err:
            # Never log the URL: it identifies the household.
            _LOGGER.debug("APTREE bill PDF unavailable (%s)", type(err).__name__)
            return None
        data = bytes(chunks)
        # Errors are served as an HTML page with HTTP 200.
        return data if data.startswith(b"%PDF-") else None

    def _save(self, month: str, data: bytes) -> dict[str, Any]:
        self._directory.mkdir(parents=True, exist_ok=True)
        target = self._directory / f"{month}.pdf"
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
        return _safe_parse(data, month)


def _safe_parse(data: bytes, month: str) -> dict[str, Any]:
    """Parse a bill, returning no values when its layout is not recognized."""
    try:
        parsed = parse_bill(data)
    except Exception as err:  # noqa: BLE001 - the original stays viewable
        _LOGGER.warning("Could not read APTREE bill PDF (%s)", type(err).__name__)
        return {}
    if parsed.get("month") != month:
        _LOGGER.warning("APTREE bill PDF month did not match the request")
        return {}
    return parsed
