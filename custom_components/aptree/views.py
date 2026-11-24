"""Authenticated bill PDF view, dashboard card and websocket API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.http import KEY_HASS

from .const import CARD_URL, DOMAIN, INTEGRATION_VERSION
from .sensor import monthly_records


async def async_register(hass: HomeAssistant) -> None:
    """Register everything the dashboard needs, once per Home Assistant run."""
    hass.http.register_view(AptreeBillView())
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL,
                str(Path(__file__).parent / "frontend" / "aptree-fees-card.js"),
                False,
            )
        ]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={INTEGRATION_VERSION}")
    websocket_api.async_register_command(hass, websocket_bills)


def _runtime(hass: HomeAssistant, entry_id: str | None) -> tuple[str, Any] | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry_id is not None and entry.entry_id != entry_id:
            continue
        if entry.state is ConfigEntryState.LOADED:
            return entry.entry_id, entry.runtime_data
    return None


class AptreeBillView(HomeAssistantView):
    """Serve a stored bill PDF to signed-in users."""

    url = "/api/aptree/bill/{entry_id}/{month}"
    name = "api:aptree:bill"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, month: str
    ) -> web.StreamResponse:
        """Return the original PDF."""
        hass = request.app[KEY_HASS]
        found = _runtime(hass, entry_id)
        path = found[1].archive.path(month) if found else None
        if path is None or not await hass.async_add_executor_job(path.is_file):
            raise web.HTTPNotFound
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": f'inline; filename="aptree-{month}.pdf"',
                "Cache-Control": "private, no-store",
            },
        )


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "aptree/bills",
        vol.Optional("entry_id"): str,
    }
)
def websocket_bills(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the complete bill archive for the dashboard card."""
    found = _runtime(hass, msg.get("entry_id"))
    if found is None:
        connection.send_error(msg["id"], "not_loaded", "APTREE is not loaded")
        return
    entry_id, runtime = found
    data = runtime.coordinator.archive_data
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry_id,
            "due_date": data.get("dueDate"),
            "unpaid": data.get("unpaidAmount"),
            "overdue": data.get("overdueAmount"),
            "bills": monthly_records(data, runtime.archive.parsed),
            "pdf_months": runtime.archive.months,
        },
    )
