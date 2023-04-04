"""Config flow for Roborock."""
from __future__ import annotations

import logging
from typing import Any

from roborock.api import RoborockClient
from roborock.containers import UserData
from roborock.exceptions import RoborockException
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_BASE_URL, CONF_ENTRY_CODE, CONF_USER_DATA, DOMAIN

_LOGGER = logging.getLogger(__name__)


class RoborockFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Roborock."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._username: str | None = None
        self._errors: dict[str, str] = {}
        self._client: RoborockClient | None = None
        self._auth_method: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initialized by the user."""
        self._errors.clear()

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            self._username = username
            assert self._username is not None
            try:
                _LOGGER.debug("Requesting code for Roborock account")
                client = RoborockClient(username)
                await client.request_code()
            except RoborockException as ex:
                _LOGGER.exception(ex)
                self._errors["base"] = "invalid_email"
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.exception(ex)
                self._errors["base"] = "unknown"
            else:
                self._client = client
                return await self.async_step_code()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_USERNAME): str}),
            errors=self._errors,
        )

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initialized by the user."""
        self._errors.clear()

        if user_input is not None:
            code = user_input[CONF_ENTRY_CODE]
            user_data = await self._code_login(code)
            if user_data and self._username:
                return self._create_entry(self._username, user_data)

        return self._show_code_form()

    def _show_code_form(self) -> FlowResult:
        """Show the configuration form to provide authentication code."""
        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required(CONF_ENTRY_CODE): str}),
            errors=self._errors,
        )

    def _create_entry(self, username: str, user_data: UserData) -> FlowResult:
        """Finished config flow and create entry."""
        assert self._client is not None
        return self.async_create_entry(
            title=username,
            data={
                CONF_USERNAME: username,
                CONF_USER_DATA: user_data,
                CONF_BASE_URL: self._client.base_url,
            },
        )

    async def _code_login(self, code: str) -> UserData | None:
        """Return UserData if login code is valid."""
        try:
            _LOGGER.debug("Logging into Roborock account using email provided code")
            assert self._client is not None
            login_data = await self._client.code_login(code)
            return login_data
        except RoborockException as ex:
            _LOGGER.exception(ex)
            self._errors["base"] = "invalid_code"
            return None
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.exception(ex)
            self._errors["base"] = "unknown"
            return None
