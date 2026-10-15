"""Config flow di Diario CamAPS: un solo campo (indirizzo dell'add-on), gia' con il
valore predefinito corretto per una installazione standard. Una sola istanza."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from aiohttp import ClientError

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN


class CamapsDiaryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        self._async_abort_entries_match({})
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            session = async_get_clientsession(self.hass)
            try:
                async with asyncio.timeout(10):
                    resp = await session.get(f"{base_url}/api/integrazione")
                    resp.raise_for_status()
            except (ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="Diario CamAPS", data={CONF_BASE_URL: base_url})

        schema = vol.Schema({vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
