"""Costanti della integrazione Diario CamAPS."""

from datetime import timedelta

DOMAIN = "camaps_diary"
DEFAULT_BASE_URL = "http://local-camaps-diary.local.hass.io:8099"
CONF_BASE_URL = "base_url"
SCAN_INTERVAL = timedelta(seconds=90)
WEEKLY_SCAN_INTERVAL = timedelta(hours=6)

ATTR_TREND = "trend"
ATTR_ULTIMO_AGGIORNAMENTO = "ultimo_aggiornamento"
ATTR_MINUTI_DA_ULTIMO_DATO = "minuti_da_ultimo_dato"
ATTR_BOLO_U = "bolo_u"
ATTR_BASALE_U = "basale_u"
ATTR_TIPO_PASTO = "tipo_pasto"
ATTR_ULTIMO_ERRORE = "ultimo_errore"
