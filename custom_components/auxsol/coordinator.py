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
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Inventário (plantas/inversores/dataloggers) muda raramente; recarrega a cada 30 min.
INVENTORY_TTL = 1800


@dataclass
class AuxsolSnapshot:
    """Fotografia completa de uma planta + inversor."""

    plant: dict[str, Any] = field(default_factory=dict)
    collector: dict[str, Any] = field(default_factory=dict)
    inverter: dict[str, Any] = field(default_factory=dict)
    realtime: dict[str, Any] = field(default_factory=dict)


class AuxsolCoordinator(DataUpdateCoordinator[AuxsolSnapshot]):
    """Busca dados da cloud Auxsol.

    O inventário é cacheado por 30 min; cada ciclo busca só a telemetria
    (1 requisição), o que permite intervalo curto sem sobrecarregar a API.
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
        self._inverter_sn: str | None = None
        self._plant: dict[str, Any] = {}
        self._collector: dict[str, Any] = {}
        self._inverter: dict[str, Any] = {}
        self._inventory_at: float = 0.0

    async def _async_update_data(self) -> AuxsolSnapshot:
        try:
            if not self._inverter_sn or (time.monotonic() - self._inventory_at > INVENTORY_TTL):
                await self._refresh_inventory()

            realtime = await self.client.async_get_realtime(self._inverter_sn) or {}

            return AuxsolSnapshot(
                plant=self._plant,
                collector=self._collector,
                inverter=self._inverter,
                realtime=realtime,
            )
        except AuxsolAuthError as err:
            raise UpdateFailed(f"Autenticação Auxsol falhou: {err}") from err
        except AuxsolError as err:
            raise UpdateFailed(str(err)) from err

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
        self._inventory_at = time.monotonic()
        _LOGGER.debug("Inventário atualizado: planta=%s inversor=%s",
                      plant_id, self._inverter_sn)

    def _pick_plant(self, plants: list[dict[str, Any]]) -> dict[str, Any]:
        if self.plant_id is not None:
            for plant in plants:
                if str(plant.get("plantId")) == str(self.plant_id):
                    return plant
        return plants[0]
