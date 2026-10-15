"""Coordinator: interroga /api/integrazione dell'add-on Diario CamAPS."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL, WEEKLY_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class CamapsDiaryCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, base_url: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.base_url = base_url.rstrip("/")

    async def _async_update_data(self) -> dict:
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(15):
                resp = await session.get(f"{self.base_url}/api/integrazione")
                resp.raise_for_status()
                return await resp.json()
        except (ClientError, TimeoutError) as exc:
            raise UpdateFailed(f"Diario CamAPS non risponde: {exc}") from exc

    async def async_call_api(self, method: str, path: str, json: dict | None = None) -> dict:
        """Usata dai servizi per chiamare l'API del diario (pasti, sync, report)."""
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(30):
            resp = await session.request(method, f"{self.base_url}{path}", json=json)
            resp.raise_for_status()
            if resp.content_type == "application/json":
                return await resp.json()
            return {}


class CamapsWeeklyInsightsCoordinator(DataUpdateCoordinator[dict]):
    """Insight settimanali (Fase 5): piu' lenti e piu' pesanti da calcolare,
    non hanno bisogno del polling frequente usato per glicemia/insulina."""

    def __init__(self, hass: HomeAssistant, base_url: str) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_settimana", update_interval=WEEKLY_SCAN_INTERVAL)
        self.base_url = base_url.rstrip("/")

    async def _async_update_data(self) -> dict:
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(30):
                resp = await session.get(f"{self.base_url}/api/insights/settimana")
                resp.raise_for_status()
                return await resp.json()
        except (ClientError, TimeoutError) as exc:
            raise UpdateFailed(f"Diario CamAPS non risponde: {exc}") from exc
