"""Sensores da integração Auxsol Solar (inversor, bateria, medidor, clima, PID)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BASELINE_KWH, DOMAIN, MANUFACTURER, STATUS_MAP
from .coordinator import AuxsolCoordinator, AuxsolDevice, AuxsolSnapshot

_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------- helpers
def _dig(data: Any, *path: Any) -> Any:
    """Navega em dicts e listas com segurança."""
    for key in path:
        if isinstance(data, dict):
            data = data.get(key)
        elif isinstance(data, list) and isinstance(key, int):
            if -len(data) <= key < len(data):
                data = data[key]
            else:
                return None
        else:
            return None
    return data


def _first(data: Any, *keys: str) -> Any:
    """Devolve o primeiro valor não nulo entre várias chaves candidatas.

    A API não é documentada e os nomes variam entre modelos de equipamento,
    por isso testamos várias chaves possíveis.
    """
    for key in keys:
        value = _dig(data, *key.split("."))
        if value is not None and value != "":
            return value
    return None


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any, tz_offset: str | None) -> Any:
    """Converte 'YYYY-MM-DD HH:MM:SS' + offset (-03:00) em datetime aware."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    parsed = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text[:19], fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    tzinfo = timezone.utc
    if isinstance(tz_offset, str) and tz_offset.startswith(("+", "-")):
        try:
            sign = 1 if tz_offset[0] == "+" else -1
            hours, minutes = tz_offset[1:].split(":")
            tzinfo = timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
        except (ValueError, IndexError):
            tzinfo = timezone.utc
    return parsed.replace(tzinfo=tzinfo)


def _status_text(value: Any) -> str | None:
    if value is None:
        return None
    return STATUS_MAP.get(str(value), f"Desconhecido ({value})")


# ------------------------------------------------------------- inversor: valores
def _corrected_total_energy(snapshot: AuxsolSnapshot) -> float | None:
    """Geração total, sem a energia gerada antes do cadastro da usina."""
    raw = _num(_dig(snapshot.realtime, "energyData", "yt"))
    return round(raw - BASELINE_KWH, 3) if raw is not None else None


def _corrected_total_earnings(snapshot: AuxsolSnapshot) -> float | None:
    """Economia total, descontado o período de comissionamento."""
    raw = _num(_dig(snapshot.realtime, "energyData", "earnT"))
    if raw is None:
        return None
    tariff = _num(_dig(snapshot.realtime, "price")) or 1.15
    return round(raw - BASELINE_KWH * tariff, 2)


@dataclass(frozen=True, kw_only=True)
class AuxsolSensorDescription(SensorEntityDescription):
    """Descrição de sensor com extrator próprio."""

    value_fn: Callable[[AuxsolSnapshot], Any]


INVERTER_SENSORS: tuple[AuxsolSensorDescription, ...] = (
    AuxsolSensorDescription(
        key="power", name="Potência atual",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power-variant",
        value_fn=lambda s: _dig(s.realtime, "energyData", "power"),
    ),
    AuxsolSensorDescription(
        key="energy_today", name="Geração hoje",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
        value_fn=lambda s: _dig(s.realtime, "energyData", "y"),
    ),
    AuxsolSensorDescription(
        key="energy_month", name="Geração no mês",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:calendar-month",
        value_fn=lambda s: _dig(s.realtime, "energyData", "ym"),
    ),
    AuxsolSensorDescription(
        key="energy_year", name="Geração no ano",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:calendar",
        value_fn=lambda s: _dig(s.realtime, "energyData", "yy"),
    ),
    AuxsolSensorDescription(
        key="energy_total", name="Geração total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=_corrected_total_energy,
    ),
    AuxsolSensorDescription(
        key="full_load_hours", name="Horas de plena carga",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:clock-outline",
        value_fn=lambda s: _dig(s.realtime, "energyData", "flh"),
    ),
    AuxsolSensorDescription(
        key="capacity", name="Capacidade instalada",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        icon="mdi:solar-panel",
        value_fn=lambda s: s.plant.get("capacity"),
    ),
    AuxsolSensorDescription(
        key="grid_power", name="Potência na rede",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        value_fn=lambda s: _dig(s.realtime, "gridData", "gridPower"),
    ),
    AuxsolSensorDescription(
        key="grid_voltage", name="Tensão da rede",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        value_fn=lambda s: _dig(s.realtime, "gridData", "acList", 0, "u"),
    ),
    AuxsolSensorDescription(
        key="grid_current", name="Corrente da rede",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        value_fn=lambda s: _dig(s.realtime, "gridData", "acList", 0, "i"),
    ),
    AuxsolSensorDescription(
        key="grid_frequency", name="Frequência da rede",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        value_fn=lambda s: _dig(s.realtime, "gridData", "acList", 0, "f"),
    ),
    AuxsolSensorDescription(
        key="temperature", name="Temperatura do inversor",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        value_fn=lambda s: _dig(s.realtime, "otherData", "temperature1"),
    ),
    AuxsolSensorDescription(
        key="signal", name="Sinal do datalogger",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wifi",
        value_fn=lambda s: s.collector.get("signal"),
    ),
    AuxsolSensorDescription(
        key="earnings_today", name="Economia hoje",
        native_unit_of_measurement="BRL",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash",
        value_fn=lambda s: _dig(s.realtime, "energyData", "earn"),
    ),
    AuxsolSensorDescription(
        key="earnings_month", name="Economia no mês",
        native_unit_of_measurement="BRL",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash-multiple",
        value_fn=lambda s: _dig(s.realtime, "energyData", "earnM"),
    ),
    AuxsolSensorDescription(
        key="earnings_total", name="Economia total",
        native_unit_of_measurement="BRL",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash-100",
        value_fn=_corrected_total_earnings,
    ),
    AuxsolSensorDescription(
        key="last_update", name="Última leitura",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check",
        value_fn=lambda s: _parse_dt(
            _dig(s.realtime, "dt"), _dig(s.realtime, "timeZone")
        ),
    ),
    AuxsolSensorDescription(
        key="status", name="Status",
        icon="mdi:check-circle",
        value_fn=lambda s: _status_text(s.inverter.get("status")),
    ),
)


# --------------------------------------------------------------- bateria
BATTERY_SENSORS: tuple[tuple[str, str, dict[str, Any], tuple[str, ...]], ...] = (
    (
        "battery_soc", "Estado de carga",
        dict(device_class=SensorDeviceClass.BATTERY, native_unit_of_measurement=PERCENTAGE,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:battery"),
        ("soc", "SOC", "batterySoc", "socValue", "capacityPercent"),
    ),
    (
        "battery_soh", "Saúde da bateria",
        dict(native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
             icon="mdi:battery-heart"),
        ("soh", "SOH", "batterySoh", "health"),
    ),
    (
        "battery_voltage", "Tensão da bateria",
        dict(device_class=SensorDeviceClass.VOLTAGE,
             native_unit_of_measurement=UnitOfElectricPotential.VOLT,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:flash"),
        ("voltage", "batVoltage", "v", "u", "totalVoltage"),
    ),
    (
        "battery_current", "Corrente da bateria",
        dict(device_class=SensorDeviceClass.CURRENT,
             native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:current-dc"),
        ("current", "batCurrent", "i"),
    ),
    (
        "battery_power", "Potência da bateria",
        dict(device_class=SensorDeviceClass.POWER,
             native_unit_of_measurement=UnitOfPower.KILO_WATT,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:battery-charging"),
        ("power", "batPower", "p"),
    ),
    (
        "battery_temperature", "Temperatura da bateria",
        dict(device_class=SensorDeviceClass.TEMPERATURE,
             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:thermometer"),
        ("temperature", "temp", "batTemp", "maxCellTemp"),
    ),
    (
        "battery_cycles", "Ciclos",
        dict(state_class=SensorStateClass.TOTAL_INCREASING, icon="mdi:counter"),
        ("cycles", "cycleCount", "cycleNum"),
    ),
    (
        "battery_capacity", "Capacidade",
        dict(device_class=SensorDeviceClass.ENERGY,
             native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
             icon="mdi:battery-high"),
        ("capacity", "remainingCapacity", "remainEnergy", "availableEnergy"),
    ),
)


# ---------------------------------------------------------------- medidor
METER_SENSORS: tuple[tuple[str, str, dict[str, Any], tuple[str, ...]], ...] = (
    (
        "meter_energy_import", "Energia importada",
        dict(device_class=SensorDeviceClass.ENERGY,
             native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
             state_class=SensorStateClass.TOTAL_INCREASING, icon="mdi:transmission-tower-import"),
        ("epa", "importEnergy", "totalEnergy", "forwardEnergy"),
    ),
    (
        "meter_energy_export", "Energia exportada",
        dict(device_class=SensorDeviceClass.ENERGY,
             native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
             state_class=SensorStateClass.TOTAL_INCREASING, icon="mdi:transmission-tower-export"),
        ("era", "exportEnergy", "reverseEnergy"),
    ),
    (
        "meter_power", "Potência ativa",
        dict(device_class=SensorDeviceClass.POWER,
             native_unit_of_measurement=UnitOfPower.KILO_WATT,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:flash"),
        ("p", "activePower", "power"),
    ),
    (
        "meter_reactive_power", "Potência reativa",
        dict(native_unit_of_measurement="kVar",
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:flash-outline"),
        ("rp", "reactivePower"),
    ),
    (
        "meter_apparent_power", "Potência aparente",
        dict(native_unit_of_measurement="kVA",
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:flash"),
        ("ap", "apparentPower"),
    ),
    (
        "meter_power_factor", "Fator de potência",
        dict(state_class=SensorStateClass.MEASUREMENT, icon="mdi:sine-wave"),
        ("pf", "powerFactor"),
    ),
    (
        "meter_frequency", "Frequência",
        dict(device_class=SensorDeviceClass.FREQUENCY,
             native_unit_of_measurement=UnitOfFrequency.HERTZ,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:sine-wave"),
        ("f", "frequency"),
    ),
    (
        "meter_voltage_a", "Tensão fase A",
        dict(device_class=SensorDeviceClass.VOLTAGE,
             native_unit_of_measurement=UnitOfElectricPotential.VOLT,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:flash"),
        ("ua", "voltageA"),
    ),
    (
        "meter_voltage_b", "Tensão fase B",
        dict(device_class=SensorDeviceClass.VOLTAGE,
             native_unit_of_measurement=UnitOfElectricPotential.VOLT,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:flash"),
        ("ub", "voltageB"),
    ),
    (
        "meter_voltage_c", "Tensão fase C",
        dict(device_class=SensorDeviceClass.VOLTAGE,
             native_unit_of_measurement=UnitOfElectricPotential.VOLT,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:flash"),
        ("uc", "voltageC"),
    ),
    (
        "meter_current_a", "Corrente fase A",
        dict(device_class=SensorDeviceClass.CURRENT,
             native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:current-ac"),
        ("ia", "currentA"),
    ),
    (
        "meter_current_b", "Corrente fase B",
        dict(device_class=SensorDeviceClass.CURRENT,
             native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:current-ac"),
        ("ib", "currentB"),
    ),
    (
        "meter_current_c", "Corrente fase C",
        dict(device_class=SensorDeviceClass.CURRENT,
             native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:current-ac"),
        ("ic", "currentC"),
    ),
)


# ------------------------------------------------------- estação meteorológica
WEATHER_SENSORS: tuple[tuple[str, str, dict[str, Any], tuple[str, ...]], ...] = (
    (
        "weather_temperature", "Temperatura ambiente",
        dict(device_class=SensorDeviceClass.TEMPERATURE,
             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:thermometer"),
        ("envTmp", "temperature", "ambientTemperature"),
    ),
    (
        "weather_temperature_max", "Temperatura máxima",
        dict(device_class=SensorDeviceClass.TEMPERATURE,
             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:thermometer-high"),
        ("envTmpMax", "maxTemperature"),
    ),
    (
        "weather_temperature_min", "Temperatura mínima",
        dict(device_class=SensorDeviceClass.TEMPERATURE,
             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:thermometer-low"),
        ("envTmpMin", "minTemperature"),
    ),
    (
        "weather_dew_point", "Ponto de orvalho",
        dict(device_class=SensorDeviceClass.TEMPERATURE,
             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:water"),
        ("dpTmp", "dewPoint"),
    ),
    (
        "weather_module_temperature", "Temperatura do painel",
        dict(device_class=SensorDeviceClass.TEMPERATURE,
             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:solar-panel"),
        ("bbTmp", "moduleTemperature", "panelTemperature"),
    ),
    (
        "weather_humidity", "Umidade",
        dict(device_class=SensorDeviceClass.HUMIDITY,
             native_unit_of_measurement=PERCENTAGE,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:water-percent"),
        ("humidity", "relativeHumidity"),
    ),
    (
        "weather_pressure", "Pressão atmosférica",
        dict(device_class=SensorDeviceClass.PRESSURE,
             native_unit_of_measurement=UnitOfPressure.HPA,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:gauge"),
        ("airPres", "pressure", "airPressure"),
    ),
    (
        "weather_radiation", "Radiação solar",
        dict(native_unit_of_measurement="W/m²",
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:white-balance-sunny"),
        ("radiation", "irradiance", "solarRadiation"),
    ),
    (
        "weather_radiation_day", "Radiação diária",
        dict(native_unit_of_measurement="MJ/m²",
             state_class=SensorStateClass.TOTAL_INCREASING, icon="mdi:weather-sunny"),
        ("radiationDay", "radiationToday"),
    ),
    (
        "weather_wind_speed", "Velocidade do vento",
        dict(device_class=SensorDeviceClass.WIND_SPEED,
             native_unit_of_measurement="m/s",
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:weather-windy"),
        ("windSpeed", "windSpeed2", "windSpeed10"),
    ),
    (
        "weather_wind_direction", "Direção do vento",
        dict(native_unit_of_measurement="°",
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:compass"),
        ("windDirection",),
    ),
    (
        "weather_sunshine", "Duração da insolação",
        dict(native_unit_of_measurement="min",
             state_class=SensorStateClass.TOTAL_INCREASING, icon="mdi:weather-sunny"),
        ("sunshineDay", "sunshineDuration"),
    ),
)


# ------------------------------------------------------------------ PID
PID_SENSORS: tuple[tuple[str, str, dict[str, Any], tuple[str, ...]], ...] = (
    (
        "pid_insulation_current", "Corrente do PID",
        dict(device_class=SensorDeviceClass.CURRENT,
             native_unit_of_measurement="mA",
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:current-dc"),
        ("pidI",),
    ),
    (
        "pid_output_voltage", "Tensão de saída do PID",
        dict(device_class=SensorDeviceClass.VOLTAGE,
             native_unit_of_measurement=UnitOfElectricPotential.VOLT,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:flash"),
        ("pidV",),
    ),
    (
        "pid_control_temperature", "Temperatura da placa de controle",
        dict(device_class=SensorDeviceClass.TEMPERATURE,
             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:thermometer"),
        ("controlT",),
    ),
    (
        "pid_boost_temperature", "Temperatura do conversor Boost",
        dict(device_class=SensorDeviceClass.TEMPERATURE,
             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:thermometer"),
        ("boostT",),
    ),
    (
        "pid_flyback_temperature", "Temperatura do conversor Flyback",
        dict(device_class=SensorDeviceClass.TEMPERATURE,
             native_unit_of_measurement=UnitOfTemperature.CELSIUS,
             state_class=SensorStateClass.MEASUREMENT, icon="mdi:thermometer"),
        ("flybackT",),
    ),
)


# ------------------------------------------------------------------- setup
async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AuxsolCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        AuxsolInverterSensor(coordinator, entry, desc) for desc in INVERTER_SENSORS
    ]

    for device in (coordinator.data.devices if coordinator.data else {}).values():
        if device.kind == "battery":
            entities.extend(
                AuxsolDeviceSensor(coordinator, entry, device, key, name, meta, keys)
                for key, name, meta, keys in BATTERY_SENSORS
            )
        elif device.kind == "meter":
            entities.extend(
                AuxsolDeviceSensor(coordinator, entry, device, key, name, meta, keys)
                for key, name, meta, keys in METER_SENSORS
            )
        elif device.kind == "weather":
            entities.extend(
                AuxsolDeviceSensor(coordinator, entry, device, key, name, meta, keys)
                for key, name, meta, keys in WEATHER_SENSORS
            )
        elif device.kind == "pid":
            entities.extend(
                AuxsolDeviceSensor(coordinator, entry, device, key, name, meta, keys)
                for key, name, meta, keys in PID_SENSORS
            )

    async_add_entities(entities)


class AuxsolInverterSensor(CoordinatorEntity[AuxsolCoordinator], SensorEntity):
    """Sensor do inversor principal da usina."""

    _attr_has_entity_name = True
    entity_description: AuxsolSensorDescription

    def __init__(
        self,
        coordinator: AuxsolCoordinator,
        entry: ConfigEntry,
        description: AuxsolSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        plant = coordinator.data.plant if coordinator.data else {}
        inverter = coordinator.data.inverter if coordinator.data else {}
        plant_id = plant.get("plantId", entry.entry_id)
        sn = inverter.get("sn", "inverter")
        self._attr_unique_id = f"{DOMAIN}_{plant_id}_{sn}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{plant_id}_{sn}")},
            manufacturer=MANUFACTURER,
            model=inverter.get("model") or "Inversor Auxsol",
            name="Placa Solar Auxsol",
            serial_number=sn,
            configuration_url="https://www.auxsolcloud.com",
        )

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (KeyError, IndexError, TypeError):
            return None


class AuxsolDeviceSensor(CoordinatorEntity[AuxsolCoordinator], SensorEntity):
    """Sensor de um equipamento auxiliar (bateria, medidor, clima, PID)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: AuxsolCoordinator,
        entry: ConfigEntry,
        device: AuxsolDevice,
        key: str,
        name: str,
        meta: dict[str, Any],
        candidates: tuple[str, ...],
    ) -> None:
        super().__init__(coordinator)
        self._device_key = f"{device.kind}:{device.sn}"
        self._candidates = candidates
        plant = coordinator.data.plant if coordinator.data else {}
        plant_id = plant.get("plantId", entry.entry_id)
        self._attr_unique_id = f"{DOMAIN}_{plant_id}_{device.kind}_{device.sn}_{key}"
        self._attr_name = name
        self.entity_description = SensorEntityDescription(key=key, **meta)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{plant_id}_{device.kind}_{device.sn}")},
            manufacturer=MANUFACTURER,
            model=device.kind.capitalize(),
            name=device.name,
            serial_number=device.sn,
            via_device=(DOMAIN, f"{plant_id}_{(coordinator.data.inverter or {}).get('sn')}"),
            configuration_url="https://www.auxsolcloud.com",
        )

    @property
    def _device(self) -> AuxsolDevice | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.devices.get(self._device_key)

    @property
    def native_value(self) -> Any:
        device = self._device
        if device is None or not device.data:
            return None
        value = _first(device.data, *self._candidates)
        if value is None:
            return None
        number = _num(value)
        return number if number is not None else value

    @property
    def available(self) -> bool:
        device = self._device
        return device is not None and bool(device.data)
