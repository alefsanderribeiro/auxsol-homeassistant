"""Constants for the Auxsol Solar integration."""

DOMAIN = "auxsol"
API_BASE = "https://eu.auxsolcloud.com/auxsol-api"

CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_PLANT_ID = "plant_id"
CONF_BASELINE = "baseline_kwh"
CONF_TARIFF = "tariff"

DEFAULT_SCAN_INTERVAL = 60  # segundos (o datalogger publica a cada ~5 min)
TOKEN_REFRESH_MARGIN = 300  # renovar 5 min antes de expirar
INVENTORY_TTL = 1800  # inventário de equipamentos em cache por 30 min

MANUFACTURER = "Auxsol"

# Energia acumulada ANTES do cadastro da usina no portal.
#
# A API expõe `yt` (total do inversor) e `ym` (total do mês). Quando o inversor
# já vinha gerando antes de a usina ser criada, `yt` inclui essa geração de
# comissionamento mas a série diária não — e o total fica maior que a soma dos
# dias. O valor varia por instalação, por isso é uma opção (padrão 0 = sem
# correção). Deixe 0 se `total` já bate com a soma dos dias no seu portal.
DEFAULT_BASELINE = 0.0

# Tarifa usada só quando a API não informa `tariff.fixPrice` da usina.
DEFAULT_TARIFF = 1.0

# Tipos de equipamento que a plataforma Auxsol gerencia por planta.
DEVICE_BATTERY = "battery"
DEVICE_METER = "meter"
DEVICE_WEATHER = "weather"
DEVICE_PID = "pid"

# Status do inversor/equipamento.
STATUS_MAP = {
    "01": "Online",
    "02": "Offline",
    "03": "Alarme",
    "04": "Falha",
}
