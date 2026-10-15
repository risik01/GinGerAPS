"""Diario CamAPS: integrazione Home Assistant per l'add-on locale omonimo.

Espone letture/stato come sensori (mai indicazioni di dose) e tre servizi che
richiamano l'API gia' usata dall'app sul telefono: aggiungere un pasto,
forzare la sincronizzazione da Glooko, generare il report PDF per il
diabetologo.
"""

from __future__ import annotations

import logging
from datetime import datetime

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import CONF_BASE_URL, DOMAIN
from .coordinator import CamapsDiaryCoordinator, CamapsWeeklyInsightsCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]

MEAL_TYPES = ("colazione", "spuntino", "pranzo", "merenda", "cena", "fuori_pasto")
NOTE_KINDS = ("sintomo", "attivita", "malattia", "visita", "altro")

ADD_NOTE_SCHEMA = vol.Schema({
    vol.Required("text"): cv.string,
    vol.Optional("kind", default="altro"): vol.In(NOTE_KINDS),
})

ADD_MEAL_SCHEMA = vol.Schema({
    vol.Required("meal_type"): vol.In(MEAL_TYPES),
    vol.Optional("carbs"): vol.Coerce(float),
    vol.Optional("description", default="Carboidrati (da Home Assistant)"): cv.string,
    vol.Optional("note"): cv.string,
})

GENERATE_REPORT_SCHEMA = vol.Schema({
    vol.Optional("days", default=30): vol.All(vol.Coerce(int), vol.In((14, 30, 90))),
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = CamapsDiaryCoordinator(hass, entry.data[CONF_BASE_URL])
    weekly_coordinator = CamapsWeeklyInsightsCoordinator(hass, entry.data[CONF_BASE_URL])
    await coordinator.async_config_entry_first_refresh()
    await weekly_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator, "weekly": weekly_coordinator,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _add_meal(call: ServiceCall) -> None:
        body = {
            "ora": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "tipo": call.data["meal_type"],
            "note": call.data.get("note", ""),
            "voci": [],
        }
        carbs = call.data.get("carbs")
        if carbs is not None and carbs > 0:
            body["voci"] = [{"descrizione": call.data["description"], "carbo_g": carbs}]
        try:
            await coordinator.async_call_api("POST", "/api/pasti", body)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Aggiunta pasto non riuscita: {exc}") from exc
        await coordinator.async_request_refresh()

    async def _refresh(call: ServiceCall) -> None:
        try:
            await coordinator.async_call_api("POST", "/api/sync")
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Sincronizzazione non riuscita: {exc}") from exc

    async def _generate_report(call: ServiceCall) -> None:
        try:
            await coordinator.async_call_api("POST", "/api/report", {"giorni": call.data["days"]})
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Generazione del report non riuscita: {exc}") from exc

    async def _add_note(call: ServiceCall) -> None:
        body = {
            "ora": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "testo": call.data["text"],
            "tipo": call.data["kind"],
        }
        try:
            await coordinator.async_call_api("POST", "/api/note", body)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Aggiunta nota non riuscita: {exc}") from exc

    hass.services.async_register(DOMAIN, "add_meal", _add_meal, schema=ADD_MEAL_SCHEMA)
    hass.services.async_register(DOMAIN, "refresh", _refresh)
    hass.services.async_register(DOMAIN, "generate_report", _generate_report, schema=GENERATE_REPORT_SCHEMA)
    hass.services.async_register(DOMAIN, "add_note", _add_note, schema=ADD_NOTE_SCHEMA)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "add_meal")
            hass.services.async_remove(DOMAIN, "refresh")
            hass.services.async_remove(DOMAIN, "generate_report")
            hass.services.async_remove(DOMAIN, "add_note")
    return unloaded
