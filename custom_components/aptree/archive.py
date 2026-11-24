"""Private on-disk archive of the original bill PDFs."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession
from homeassistant.core import HomeAssistant

from .bill_pdf import parse_bill
from .const import (
    PDF_DIRECTORY,
    PDF_LOOKBACK_MONTHS,
    PDF_MAX_BYTES,
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
    ) -> None:
        """Initialize the archive."""
        self._hass = hass
        self._session = session
        self._community_id = community_id
        self._building = building
        self._unit = unit
        self._directory = Path(hass.config.path(PDF_DIRECTORY, entry_id))
        self._attempted: dict[str, datetime] = {}
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

    async def async_update(self, months: list[str]) -> bool:
        """Download missing PDFs for the newest months; return True if any was added."""
        if not self._building or not self._unit:
            return False
        added = False
        now = datetime.now(UTC)
        for month in sorted(months)[-PDF_LOOKBACK_MONTHS:]:
            if month in self.parsed or not MONTH_PATTERN.match(month):
                continue
            last = self._attempted.get(month)
            if last is not None and now - last < timedelta(seconds=PDF_RETRY_INTERVAL):
                continue
            self._attempted[month] = now
            data = await self._async_download(month)
            if data is None:
                continue
            try:
                self.parsed[month] = await self._hass.async_add_executor_job(
                    self._save, month, data
                )
            except OSError as err:
                _LOGGER.warning(
                    "Could not store APTREE bill PDF (%s)", type(err).__name__
                )
                continue
            added = True
        return added

    async def _async_download(self, month: str) -> bytes | None:
        params = {
            "txtAptCode": self._community_id,
            "txtDong": self._building,
            "txtHo": self._unit,
            "comYY": month[:4],
            "comMM": month[5:7],
            "tempId": "HPGoji",
        }
        try:
            async with asyncio.timeout(PDF_TIMEOUT):
                async with self._session.get(PDF_URL, params=params) as response:
                    if response.status != 200:
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
        # A missing bill is served as an HTML error page with HTTP 200.
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
