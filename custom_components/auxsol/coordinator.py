"""Coordinator de dados da integração Auxsol."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuxsolAuthError, AuxsolClient, AuxsolError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, INVENTORY_TTL

_LOGGER = logging.getLogger(__name__)


@dataclass
class AuxsolDevice:
    """Um equipamento físico descoberto na conta."""

    kind: str  # inverter | battery | meter | weather | pid
    sn: str
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuxsolSnapshot:
    """Fotografia da usina e de todos os seus equipamentos."""

    plant: dict[str, Any] = field(default_factory=dict)
    collector: dict[str, Any] = field(default_factory=dict)
    inverter: dict[str, Any] = field(default_factory=dict)
    realtime: dict[str, Any] = field(default_factory=dict)
    devices: dict[str, AuxsolDevice] = field(default_factory=dict)
    alarms: list[dict[str, Any]] = field(default_factory=list)


class AuxsolCoordinator(DataUpdateCoordinator[AuxsolSnapshot]):
    """Busca dados da cloud Auxsol.

    O inventário (usina, inversores, baterias, medidores, clima, PID) muda
    raramente e fica em cache por 30 min. Cada ciclo busca só a telemetria.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: AuxsolClient,
        plant_id: int | None,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.plant_id = plant_id
        self._plant: dict[str, Any] = {}
        self._collector: dict[str, Any] = {}
        self._inverter: dict[str, Any] = {}
        self._inverter_sn: str | None = None
        self._extra: dict[str, list[dict[str, Any]]] = {}
        self._inventory_at: float = 0.0

    async def _async_update_data(self) -> AuxsolSnapshot:
        try:
            if not self._inverter_sn or (time.monotonic() - self._inventory_at > INVENTORY_TTL):
                await self._refresh_inventory()

            realtime = await self.client.async_get_realtime(self._inverter_sn) or {}

            devices: dict[str, AuxsolDevice] = {}
            if self._inverter_sn:
                devices[f"inverter:{self._inverter_sn}"] = AuxsolDevice(
                    kind="inverter",
                    sn=self._inverter_sn,
                    name=self._inverter.get("inverterName") or "Inversor",
                    data=realtime,
                    raw=self._inverter,
                )

            for kind, items in self._extra.items():
                for item in items:
                    sn = item.get("sn") or item.get("collectorSn") or item.get("deviceSn")
                    if not sn:
                        continue
                    fetched = await self._fetch_device_current(kind, sn)
                    devices[f"{kind}:{sn}"] = AuxsolDevice(
                        kind=kind,
                        sn=sn,
                        name=item.get("name") or item.get("deviceName") or f"{kind} {sn}",
                        data=fetched or {},
                        raw=item,
                    )

            return AuxsolSnapshot(
                plant=self._plant,
                collector=self._collector,
                inverter=self._inverter,
                realtime=realtime,
                devices=devices,
                alarms=[],
            )
        except AuxsolAuthError as err:
            raise UpdateFailed(f"Autenticação Auxsol falhou: {err}") from err
        except AuxsolError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch_device_current(self, kind: str, sn: str) -> dict[str, Any] | None:
        """Busca a leitura corrente conforme o tipo do equipamento."""
        try:
            if kind == "battery":
                return await self.client.async_get_battery_current(sn)
            if kind == "meter":
                return await self.client.async_get_meter_current(sn)
            if kind == "weather":
                return await self.client.async_get_weather_current(sn)
            if kind == "pid":
                return await self.client.async_get_pid_current(sn)
        except AuxsolError as err:
            _LOGGER.debug("Falha ao ler %s %s: %s", kind, sn, err)
        return None

    async def _refresh_inventory(self) -> None:
        plants = await self.client.async_get_plants()
        if not plants:
            raise UpdateFailed("Nenhuma planta encontrada nesta conta Auxsol")

        plant = self._pick_plant(plants)
        plant_id = plant.get("plantId")

        inverters = [
            i for i in await self.client.async_get_inverters()
            if str(i.get("plantId")) == str(plant_id)
        ]
        collectors = [
            c for c in await self.client.async_get_collectors()
            if str(c.get("plantId")) == str(plant_id)
        ]
        if not inverters:
            raise UpdateFailed(f"Planta {plant_id} sem inversor associado")

        self._plant = plant
        self._collector = collectors[0] if collectors else {}
        self._inverter = inverters[0]
        self._inverter_sn = inverters[0].get("sn")
        self._extra = {}
        self._inventory_at = time.monotonic()
        _LOGGER.debug(
            "Inventário: planta=%s inversor=%s", plant_id, self._inverter_sn
        )

    def _pick_plant(self, plants: list[dict[str, Any]]) -> dict[str, Any]:
        if self.plant_id is not None:
            for plant in plants:
                if str(plant.get("plantId")) == str(self.plant_id):
                    return plant
        return plants[0]
