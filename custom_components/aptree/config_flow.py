"""Config and options flows for APTREE."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    AptreeApiClient,
    AptreeApiError,
    AptreeAuthenticationError,
    AptreeConnectionError,
)
from .const import DOMAIN


def _credentials_schema(
    username: str | None = None,
    *,
    password_required: bool = True,
) -> vol.Schema:
    """Build a browser-autofill-friendly credential schema."""
    username_key = vol.Required(CONF_USERNAME, default=username or "")
    password_key: vol.Marker
    if password_required:
        password_key = vol.Required(CONF_PASSWORD)
    else:
        password_key = vol.Optional(CONF_PASSWORD, default="")

    return vol.Schema(
        {
            username_key: TextSelector(
                TextSelectorConfig(
                    type=TextSelectorType.TEXT,
                    autocomplete="username",
                )
            ),
            password_key: TextSelector(
                TextSelectorConfig(
                    type=TextSelectorType.PASSWORD,
                    autocomplete="current-password",
                )
            ),
        }
    )


async def _async_validate_credentials(
    hass: HomeAssistant, username: str, password: str
) -> None:
    """Validate credentials against the sign-in endpoint."""
    api = AptreeApiClient(async_get_clientsession(hass), username, password)
    await api.async_validate_credentials()


class AptreeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an APTREE config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect and validate resident credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            try:
                await _async_validate_credentials(self.hass, username, password)
            except AptreeAuthenticationError:
                errors["base"] = "invalid_auth"
            except AptreeConnectionError:
                errors["base"] = "cannot_connect"
            except AptreeApiError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(username.casefold())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"APTREE ({username})",
                    data={CONF_USERNAME: username, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after the API rejects stored credentials."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement credentials and update the entry."""
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            try:
                await _async_validate_credentials(self.hass, username, password)
            except AptreeAuthenticationError:
                errors["base"] = "invalid_auth"
            except AptreeConnectionError:
                errors["base"] = "cannot_connect"
            except AptreeApiError:
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={CONF_USERNAME: username, CONF_PASSWORD: password},
                    title=f"APTREE ({username})",
                    unique_id=username.casefold(),
                )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(entry.data[CONF_USERNAME]),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AptreeOptionsFlow:
        """Return the options flow."""
        return AptreeOptionsFlow(config_entry)


class AptreeOptionsFlow(OptionsFlow):
    """Allow the resident ID and password to be changed."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and save changed credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input.get(CONF_PASSWORD) or self._entry.data[CONF_PASSWORD]

            duplicate = any(
                entry.entry_id != self._entry.entry_id
                and entry.unique_id == username.casefold()
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            )
            if duplicate:
                errors["base"] = "already_configured"
            else:
                try:
                    await _async_validate_credentials(self.hass, username, password)
                except AptreeAuthenticationError:
                    errors["base"] = "invalid_auth"
                except AptreeConnectionError:
                    errors["base"] = "cannot_connect"
                except AptreeApiError:
                    errors["base"] = "unknown"
                else:
                    self.hass.config_entries.async_update_entry(
                        self._entry,
                        data={CONF_USERNAME: username, CONF_PASSWORD: password},
                        title=f"APTREE ({username})",
                        unique_id=username.casefold(),
                    )
                    return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_credentials_schema(
                self._entry.data[CONF_USERNAME], password_required=False
            ),
            errors=errors,
        )
