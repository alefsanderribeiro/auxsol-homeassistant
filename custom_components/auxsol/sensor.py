"""Sensores da integração Auxsol Solar."""

from __future__ import annotations

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
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BASELINE_KWH, DOMAIN, MANUFACTURER
from .coordinator import AuxsolCoordinator, AuxsolSnapshot


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


STATUS_MAP = {
    "01": "Online",
    "02": "Offline",
    "03": "Alarme",
    "04": "Falha",
}


def _status_text(snapshot: Any) -> str | None:
    """Traduz o código de status do inversor."""
    raw = snapshot.inverter.get("status")
    if raw is None:
        return None
    return STATUS_MAP.get(str(raw), f"Desconhecido ({raw})")


def _corrected_total_energy(snapshot: Any) -> float | None:
    """Geração total, sem a energia gerada antes do cadastro da planta.

    O campo `yt` da API inclui a geração do comissionamento (BASELINE_KWH),
    que não pertence a nenhum dia da série. Subtraí-lo faz o total começar em
    zero na data de instalação e casar com a soma dos dias.
    """
    raw = _dig(snapshot.realtime, "energyData", "yt")
    if raw is None:
        return None
    try:
        return round(float(raw) - BASELINE_KWH, 3)
    except (TypeError, ValueError):
        return None


def _corrected_total_earnings(snapshot: Any) -> float | None:
    """Economia total, descontada a do comissionamento (mesma proporção)."""
    raw = _dig(snapshot.realtime, "energyData", "earnT")
    if raw is None:
        return None
    try:
        base = _dig(snapshot.realtime, "energyData", "price")
        tariff = float(base) if base is not None else 1.15
        return round(float(raw) - BASELINE_KWH * tariff, 2)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any, tz_offset: str | None) -> Any:
    """Converte 'YYYY-MM-DD HH:MM:SS' + offset (-03:00) em datetime aware."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
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


@dataclass(frozen=True, kw_only=True)
class AuxsolSensorEntityDescription(SensorEntityDescription):
    """Descrição de sensor com extrator próprio."""

    value_fn: Callable[[AuxsolSnapshot], Any]


SENSORS: tuple[AuxsolSensorEntityDescription, ...] = (
    AuxsolSensorEntityDescription(
        key="power",
        translation_key="power",
        name="Potência atual",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power-variant",
        value_fn=lambda s: _dig(s.realtime, "energyData", "power"),
    ),
    AuxsolSensorEntityDescription(
        key="energy_today",
        translation_key="energy_today",
        name="Geração hoje",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
        value_fn=lambda s: _dig(s.realtime, "energyData", "y"),
    ),
    AuxsolSensorEntityDescription(
        key="energy_month",
        translation_key="energy_month",
        name="Geração no mês",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:calendar-month",
        value_fn=lambda s: _dig(s.realtime, "energyData", "ym"),
    ),
    AuxsolSensorEntityDescription(
        key="energy_year",
        translation_key="energy_year",
        name="Geração no ano",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:calendar",
        value_fn=lambda s: _dig(s.realtime, "energyData", "yy"),
    ),
    AuxsolSensorEntityDescription(
        key="energy_total",
        translation_key="energy_total",
        name="Geração total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=_corrected_total_energy,
    ),
    AuxsolSensorEntityDescription(
        key="full_load_hours",
        translation_key="full_load_hours",
        name="Horas de plena carga",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:clock-outline",
        value_fn=lambda s: _dig(s.realtime, "energyData", "flh"),
    ),
    AuxsolSensorEntityDescription(
        key="capacity",
        translation_key="capacity",
        name="Capacidade instalada",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        icon="mdi:solar-panel",
        value_fn=lambda s: s.plant.get("capacity"),
    ),
    AuxsolSensorEntityDescription(
        key="grid_power",
        translation_key="grid_power",
        name="Potência na rede",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        value_fn=lambda s: _dig(s.realtime, "gridData", "gridPower"),
    ),
    AuxsolSensorEntityDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        name="Tensão da rede",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        value_fn=lambda s: (
            _dig(s.realtime, "gridData", "acList", 0, "u")
            if isinstance(_dig(s.realtime, "gridData", "acList"), list)
            and _dig(s.realtime, "gridData", "acList")
            else None
        ),
    ),
    AuxsolSensorEntityDescription(
        key="grid_current",
        translation_key="grid_current",
        name="Corrente da rede",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        value_fn=lambda s: (
            _dig(s.realtime, "gridData", "acList", 0, "i")
            if isinstance(_dig(s.realtime, "gridData", "acList"), list)
            and _dig(s.realtime, "gridData", "acList")
            else None
        ),
    ),
    AuxsolSensorEntityDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        name="Frequência da rede",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        value_fn=lambda s: (
            _dig(s.realtime, "gridData", "acList", 0, "f")
            if isinstance(_dig(s.realtime, "gridData", "acList"), list)
            and _dig(s.realtime, "gridData", "acList")
            else None
        ),
    ),
    AuxsolSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        name="Temperatura do inversor",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        value_fn=lambda s: _dig(s.realtime, "otherData", "temperature1"),
    ),
    AuxsolSensorEntityDescription(
        key="signal",
        translation_key="signal",
        name="Sinal do datalogger",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wifi",
        value_fn=lambda s: s.collector.get("signal"),
    ),
    AuxsolSensorEntityDescription(
        key="earnings_today",
        translation_key="earnings_today",
        name="Economia hoje",
        native_unit_of_measurement="BRL",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash",
        value_fn=lambda s: _dig(s.realtime, "energyData", "earn"),
    ),
    AuxsolSensorEntityDescription(
        key="earnings_month",
        translation_key="earnings_month",
        name="Economia no mês",
        native_unit_of_measurement="BRL",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash-multiple",
        value_fn=lambda s: _dig(s.realtime, "energyData", "earnM"),
    ),
    AuxsolSensorEntityDescription(
        key="earnings_total",
        translation_key="earnings_total",
        name="Economia total",
        native_unit_of_measurement="BRL",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:cash-100",
        value_fn=_corrected_total_earnings,
    ),
    AuxsolSensorEntityDescription(
        key="last_update",
        translation_key="last_update",
        name="Última leitura",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check",
        value_fn=lambda s: _parse_dt(
            _dig(s.realtime, "dt"), _dig(s.realtime, "timeZone")
        ),
    ),
    AuxsolSensorEntityDescription(
        key="status",
        translation_key="status",
        name="Status",
        icon="mdi:check-circle",
        value_fn=_status_text,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AuxsolCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AuxsolSensor(coordinator, entry, description) for description in SENSORS
    )


class AuxsolSensor(CoordinatorEntity[AuxsolCoordinator], SensorEntity):
    """Sensor único de um dado da planta."""

    _attr_has_entity_name = True
    entity_description: AuxsolSensorEntityDescription

    def __init__(
        self,
        coordinator: AuxsolCoordinator,
        entry: ConfigEntry,
        description: AuxsolSensorEntityDescription,
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
