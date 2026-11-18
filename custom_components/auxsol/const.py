"""Constants for the Auxsol Solar integration."""

DOMAIN = "auxsol"
API_BASE = "https://eu.auxsolcloud.com/auxsol-api"

CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_PLANT_ID = "plant_id"

DEFAULT_SCAN_INTERVAL = 60  # segundos (o datalogger publica a cada ~5 min)
TOKEN_REFRESH_MARGIN = 300  # renovar 5 min antes de expirar
INVENTORY_TTL = 1800  # inventário de equipamentos em cache por 30 min

MANUFACTURER = "Auxsol"

# Energia gerada ANTES da planta ser cadastrada no portal (comissionamento).
# O campo `yt` (total) da API inclui esse valor, mas a série diária não —
# por isso `yt - ym` sobra 36,8 kWh "fantasma" que não pertence a nenhum dia.
# Confirmado em 2026-11-17: 36,80 kWh × R$1,15/kWh = R$42,32 = (earnT - earnM).
BASELINE_KWH = 36.80

# Tipos de equipamento que a plataforma Auxsol gerencia por planta.
DEVICE_BATTERY = "battery"
DEVICE_METER = "meter"
DEVICE_WEATHER = "weather"
DEVICE_PID = "pid"
DEVICE_TYPES = (DEVICE_BATTERY, DEVICE_METER, DEVICE_WEATHER, DEVICE_PID)

# Status do inversor/equipamento.
STATUS_MAP = {
    "01": "Online",
    "02": "Offline",
    "03": "Alarme",
    "04": "Falha",
}
