"""Collegamento con Glooko: on se l'ultima sincronizzazione e' andata bene."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CamapsDiaryCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator: CamapsDiaryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CamapsCollegamentoSensor(coordinator, entry)])


class CamapsCollegamentoSensor(CoordinatorEntity[CamapsDiaryCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Collegamento Glooko"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:sync"

    def __init__(self, coordinator: CamapsDiaryCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_collegamento"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="Diario CamAPS", manufacturer="Diario CamAPS")

    @property
    def is_on(self):
        return (self.coordinator.data or {}).get("collegamento", {}).get("ok")

    @property
    def extra_state_attributes(self):
        c = (self.coordinator.data or {}).get("collegamento", {})
        return {"stato": c.get("stato"), "ultimo_errore": c.get("errore")}
