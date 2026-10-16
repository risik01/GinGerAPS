"""Sensori di Diario CamAPS: solo numeri descrittivi, nessuna indicazione di dose."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CamapsDiaryCoordinator, CamapsWeeklyInsightsCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: CamapsDiaryCoordinator = data["coordinator"]
    weekly: CamapsWeeklyInsightsCoordinator = data["weekly"]
    async_add_entities([
        CamapsGlicemiaSensor(coordinator, entry),
        CamapsTirSensor(coordinator, entry, "tir_oggi", "TIR oggi", "tir_oggi_pct"),
        CamapsTirSensor(coordinator, entry, "tir_7g", "TIR 7 giorni", "tir_7g_pct"),
        CamapsCarboidratiOggiSensor(coordinator, entry),
        CamapsInsulinaOggiSensor(coordinator, entry),
        *[CamapsNutrienteOggiSensor(coordinator, entry, *spec) for spec in NUTRIENTI_OGGI],
        CamapsUltimoPastoSensor(coordinator, entry),
        CamapsUltimoBoloSensor(coordinator, entry),
        CamapsInsightSettimanaleSensor(weekly, entry),
    ])


class _Base(CoordinatorEntity[CamapsDiaryCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: CamapsDiaryCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="Diario CamAPS", manufacturer="Diario CamAPS")


class CamapsGlicemiaSensor(_Base):
    _attr_name = "Glicemia"
    _attr_native_unit_of_measurement = "mg/dL"
    _attr_device_class = SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:diabetes"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_glicemia"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("glicemia", {}).get("mgdl")

    @property
    def extra_state_attributes(self):
        g = (self.coordinator.data or {}).get("glicemia", {})
        return {
            "trend": g.get("trend"),
            "ultimo_aggiornamento": g.get("ts_local"),
            "minuti_da_ultimo_dato": g.get("minuti_da_ultimo_dato"),
        }


class CamapsTirSensor(_Base):
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:target"

    def __init__(self, coordinator, entry, key: str, name: str, data_key: str) -> None:
        super().__init__(coordinator, entry)
        self._data_key = data_key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._data_key)


class CamapsCarboidratiOggiSensor(_Base):
    _attr_name = "Carboidrati oggi"
    _attr_native_unit_of_measurement = "g"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:bread-slice"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_carboidrati_oggi"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("carboidrati_oggi_g")


class CamapsInsulinaOggiSensor(_Base):
    _attr_name = "Insulina oggi"
    _attr_native_unit_of_measurement = "U"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:needle"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_insulina_oggi"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("insulina_oggi_U")

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        return {"bolo_u": d.get("insulina_oggi_boli_U"), "basale_u": d.get("insulina_oggi_basale_U")}


# (chiave in nutrienti_oggi, nome, unita', icona)
NUTRIENTI_OGGI = (
    ("kcal", "Calorie oggi", "kcal", "mdi:fire"),
    ("proteine", "Proteine oggi", "g", "mdi:food-drumstick"),
    ("grassi", "Grassi oggi", "g", "mdi:water"),
    ("zuccheri", "Zuccheri oggi", "g", "mdi:cube-outline"),
    ("fibra", "Fibre oggi", "g", "mdi:grain"),
)


class CamapsNutrienteOggiSensor(_Base):
    """Calorie e macronutrienti dei pasti di oggi. Stato 0 se oggi non c'e' nessun pasto;
    sconosciuto se i pasti non hanno valori noti. L'attributo "parziale" avvisa quando
    manca il dato di qualche alimento (il totale e' una stima per difetto)."""

    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry, key: str, name: str, unit: str, icon: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}_oggi"

    @property
    def _oggi(self) -> dict:
        return (self.coordinator.data or {}).get("nutrienti_oggi") or {}

    @property
    def native_value(self):
        return self._oggi.get(self._key)

    @property
    def extra_state_attributes(self):
        o = self._oggi
        attrs = {"parziale": o.get("parziale"), "pasti": o.get("pasti")}
        if self._key == "kcal":
            attrs["carboidrati_g"] = o.get("carbo")
        return attrs


class CamapsUltimoPastoSensor(_Base):
    _attr_name = "Ultimo pasto"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:food"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ultimo_pasto"

    @property
    def native_value(self):
        pasto = (self.coordinator.data or {}).get("ultimo_pasto")
        if not pasto:
            return None
        from homeassistant.util import dt as dt_util
        # ts_local e' sempre ora locale "nuda" (vedi timeutil.py del diario): la
        # rendiamo consapevole del fuso con quello configurato in HA.
        naive = dt_util.parse_datetime(pasto["ts_local"].replace(" ", "T"))
        if naive is None:
            return None
        return naive.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)

    @property
    def extra_state_attributes(self):
        pasto = (self.coordinator.data or {}).get("ultimo_pasto")
        if not pasto:
            return {}
        return {"tipo_pasto": pasto.get("tipo"), "carboidrati_g": pasto.get("carboidrati_g")}


class CamapsUltimoBoloSensor(_Base):
    _attr_name = "Ultimo bolo"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:needle"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ultimo_bolo"

    @property
    def native_value(self):
        bolo = (self.coordinator.data or {}).get("ultimo_bolo")
        if not bolo:
            return None
        from homeassistant.util import dt as dt_util
        naive = dt_util.parse_datetime(bolo["ts_local"].replace(" ", "T"))
        if naive is None:
            return None
        return naive.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)

    @property
    def extra_state_attributes(self):
        bolo = (self.coordinator.data or {}).get("ultimo_bolo")
        return {"unita_u": bolo.get("unita_U")} if bolo else {}


class CamapsInsightSettimanaleSensor(_Base):
    """Fase 5: osservazioni descrittive sulla settimana, mai consigli di dose.
    Lo stato e' la prima frase (la piu' rilevante), tutte le altre sono
    nell'attributo "frasi" per automazioni/notifiche piu' complete."""

    _attr_name = "Insight settimanale"
    _attr_icon = "mdi:calendar-text"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_insight_settimanale"

    @property
    def native_value(self):
        frasi = (self.coordinator.data or {}).get("frasi") or []
        if not frasi:
            return None
        return frasi[0][:255]

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        return {
            "frasi": d.get("frasi", []),
            "settimana_da": d.get("settimana_da"),
            "settimana_a": d.get("settimana_a"),
        }
