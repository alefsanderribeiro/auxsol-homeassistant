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
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TARIFF,
    DOMAIN,
    INVENTORY_TTL,
)

_LOGGER = logging.getLogger(__name__)

# O baseline de comissionamento é recalculado no máximo a cada 6 horas:
# ele só muda se a usina for recriada no portal.
BASELINE_TTL = 6 * 3600


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
    baseline_kwh: float = 0.0
    tariff: float = DEFAULT_TARIFF


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
        baseline_kwh: float | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.plant_id = plant_id
        # None = detectar automaticamente; valor = fornecido pelo usuário.
        self._baseline_manual = baseline_kwh
        self._baseline_auto: float = 0.0
        self._baseline_at: float = 0.0
        self._plant: dict[str, Any] = {}
        self._collector: dict[str, Any] = {}
        self._inverter: dict[str, Any] = {}
        self._inverter_sn: str | None = None
        self._tariff: float = DEFAULT_TARIFF
        self._extra: dict[str, list[dict[str, Any]]] = {}
        self._inventory_at: float = 0.0

    # ------------------------------------------------------------- baseline
    @property
    def baseline_kwh(self) -> float:
        """Energia de comissionamento a descontar dos totais."""
        if self._baseline_manual is not None:
            return max(float(self._baseline_manual), 0.0)
        return self._baseline_auto

    async def _compute_baseline(self, realtime: dict[str, Any]) -> None:
        """Calcula a energia gerada antes do cadastro da usina.

        O total (`yt`) inclui a geração de comissionamento; a série diária não.
        Isolamos o excedente somando os meses já fechados da série anual e
        comparando com o total menos o mês corrente:

            baseline = yt − ym − Σ(meses anteriores da série anual)

        Assim funciona para qualquer usina, nova ou antiga.
        """
        yt = _as_float((realtime.get("energyData") or {}).get("yt"))
        ym = _as_float((realtime.get("energyData") or {}).get("ym"))
        if yt is None or ym is None or self.plant_id is None:
            return

        plant = self._plant or {}
        inicio = _first_year(plant.get("createTime") or plant.get("buildTime"))
        if inicio is None:
            self._baseline_auto = 0.0
            self._baseline_at = time.monotonic()
            return

        ano_atual = time.gmtime().tm_year
        mes_atual = time.gmtime().tm_mon
        anteriores = 0.0
        try:
            for ano in range(inicio, ano_atual + 1):
                blocos = await self.client.async_get_plant_curve(
                    self.plant_id, "2", f"{ano}-01-01", "4032"
                )
                for bloco in blocos:
                    if bloco.get("dataItem") != 6:
                        continue
                    for ponto in bloco.get("data") or []:
                        valor = _as_float(ponto.get("value"))
                        if valor is None:
                            continue
                        try:
                            mes = int(str(ponto.get("dt")))
                        except (TypeError, ValueError):
                            continue
                        # Ignora o mês corrente: ele ainda está em curso e o
                        # valor definitivo vem de `ym`.
                        if ano == ano_atual and mes >= mes_atual:
                            continue
                        anteriores += valor
        except AuxsolError as err:
            _LOGGER.debug("Falha ao calcular baseline: %s", err)
            return

        self._baseline_auto = max(round(yt - ym - anteriores, 3), 0.0)
        self._baseline_at = time.monotonic()
        _LOGGER.debug(
            "Baseline calculado: %.3f kWh (yt=%.2f ym=%.2f meses_anteriores=%.2f)",
            self._baseline_auto, yt, ym, anteriores,
        )

    # ----------------------------------------------------------------- ciclo
    async def _async_update_data(self) -> AuxsolSnapshot:
        try:
            if not self._inverter_sn or (time.monotonic() - self._inventory_at > INVENTORY_TTL):
                await self._refresh_inventory()

            realtime = await self.client.async_get_realtime(self._inverter_sn) or {}

            if (
                self._baseline_manual is None
                and time.monotonic() - self._baseline_at > BASELINE_TTL
            ):
                await self._compute_baseline(realtime)

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
                baseline_kwh=self.baseline_kwh,
                tariff=self._tariff,
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

        # O detalhe traz createTime e a tarifa da usina.
        try:
            detalhe = await self.client.async_get_plant_detail(plant_id)
            if detalhe:
                plant = {**plant, **detalhe}
        except AuxsolError as err:
            _LOGGER.debug("Sem detalhe da usina %s: %s", plant_id, err)

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
        self._tariff = _plant_tariff(plant)
        self._extra = {}
        self._inventory_at = time.monotonic()
        _LOGGER.debug(
            "Inventário: planta=%s inversor=%s tarifa=%.4f",
            plant_id, self._inverter_sn, self._tariff,
        )

    def _pick_plant(self, plants: list[dict[str, Any]]) -> dict[str, Any]:
        if self.plant_id is not None:
            for plant in plants:
                if str(plant.get("plantId")) == str(self.plant_id):
                    return plant
        return plants[0]


# ------------------------------------------------------------------ helpers
def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_year(texto: Any) -> int | None:
    """Extrai o ano de createTime/buildTime ('2026-11-17T03:36:17...')."""
    if not isinstance(texto, str) or len(texto) < 4:
        return None
    try:
        ano = int(texto[:4])
    except ValueError:
        return None
    return ano if 2000 <= ano <= 2100 else None


def _plant_tariff(plant: dict[str, Any]) -> float:
    """Tarifa da usina, definida no próprio portal."""
    valor = _as_float((plant.get("tariff") or {}).get("fixPrice"))
    return valor if valor and valor > 0 else DEFAULT_TARIFF
