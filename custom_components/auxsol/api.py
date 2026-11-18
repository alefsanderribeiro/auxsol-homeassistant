"""Cliente HTTP da API Auxsol Cloud (auxsolcloud.com)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from .const import API_BASE, TOKEN_REFRESH_MARGIN

_LOGGER = logging.getLogger(__name__)

OK_CODE = "AWX-0000"
AUTH_EXPIRED_CODES = {"AWX-0400", "AWX-0401"}


class AuxsolError(Exception):
    """Erro genérico da API Auxsol."""


class AuxsolAuthError(AuxsolError):
    """Credenciais inválidas ou sessão expirada."""


class AuxsolClient:
    """Cliente para a API da Auxsol Cloud.

    Cobre usina, inversores, baterias, medidores, estações meteorológicas,
    sensores PID e alarmes.
    """

    def __init__(self, session: aiohttp.ClientSession, account: str, password: str) -> None:
        self._session = session
        self._account = account
        self._password = password
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ auth
    async def _login(self) -> None:
        payload = {"account": self._account, "password": self._password, "lang": "pt-BR"}
        try:
            async with self._session.post(
                f"{API_BASE}/auth/login", json=payload, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise AuxsolError(f"Falha de conexão no login: {err}") from err

        if body.get("code") != OK_CODE or not body.get("data"):
            raise AuxsolAuthError(
                f"Login recusado pela Auxsol: {body.get('code')} {body.get('msg') or ''}".strip()
            )

        data = body["data"]
        self._token = data["access_token"]
        expires_in = int(data.get("expires_in") or 43200)
        self._token_exp = time.monotonic() + expires_in
        _LOGGER.debug("Login Auxsol OK (token válido por %ss)", expires_in)

    async def _ensure_token(self, force: bool = False) -> str:
        async with self._lock:
            if force or not self._token or time.monotonic() >= self._token_exp - TOKEN_REFRESH_MARGIN:
                await self._login()
            return self._token  # type: ignore[return-value]

    # --------------------------------------------------------------- requests
    async def _request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        for attempt in (1, 2):
            token = await self._ensure_token(force=attempt == 2)
            headers = {"Authorization": f"Bearer {token}"}
            try:
                async with self._session.get(
                    f"{API_BASE}{path}",
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=45),
                ) as resp:
                    if resp.status in (401, 403):
                        if attempt == 1:
                            continue
                        raise AuxsolAuthError(f"HTTP {resp.status} em {path}")
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise AuxsolError(f"Falha de conexão em {path}: {err}") from err

            code = body.get("code")
            if code == OK_CODE:
                return body.get("data")
            if code in AUTH_EXPIRED_CODES and attempt == 1:
                _LOGGER.debug("Token expirado (%s); renovando", code)
                continue
            if code in AUTH_EXPIRED_CODES:
                raise AuxsolAuthError(f"Sessão expirada em {path}")
            raise AuxsolError(f"Erro {code} em {path}: {body.get('msg')}")

        raise AuxsolError(f"Falha inesperada em {path}")

    async def _request_rows(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Para endpoints que devolvem {rows: [...]}."""
        data = await self._request(path, params)
        if isinstance(data, dict):
            return data.get("rows") or []
        if isinstance(data, list):
            return data
        return []

    # ------------------------------------------------------------------ conta
    async def async_validate(self) -> list[dict[str, Any]]:
        """Valida credenciais e devolve a lista de plantas."""
        await self._ensure_token(force=True)
        return await self.async_get_plants()

    async def async_get_plants(self) -> list[dict[str, Any]]:
        return await self._request_rows("/archive/plant/list")

    async def async_get_plant_detail(self, plant_id: int) -> dict[str, Any]:
        data = await self._request(f"/archive/plant/findPlantById/{plant_id}")
        return data or {}

    async def async_get_collectors(self) -> list[dict[str, Any]]:
        return await self._request_rows("/archive/collector/list")

    # -------------------------------------------------------------- inversores
    async def async_get_inverters(self) -> list[dict[str, Any]]:
        return await self._request_rows("/archive/inverter/list")

    async def async_get_realtime(self, sn: str) -> dict[str, Any] | None:
        """Telemetria completa do inversor (potência, energia, rede, temperatura)."""
        return await self._request(
            "/analysis/inverterReport/findInverterRealTimeInfoBySnV1", {"sn": sn}
        )

    async def async_get_inverter_curve(
        self, sn: str, report_type: str = "0", data_time: str | None = None, data_items: str = "16399"
    ) -> list[dict[str, Any]]:
        params = {"sn": sn, "reportType": report_type, "dataItems": data_items}
        if data_time:
            params["dataTime"] = data_time
        data = await self._request("/analysis/inverterReport/queryInverterCurveBySnV1", params)
        return data or []

    async def async_get_inverter_report(
        self, sn: str, report_type: str = "1", data_time: str | None = None, data_items: str = "4032"
    ) -> dict[str, Any]:
        """Relatório tabular (dia/mês/ano) com totais por período."""
        params = {"sn": sn, "reportType": report_type, "dataItems": data_items}
        if data_time:
            params["dataTime"] = data_time
        data = await self._request("/analysis/inverterReport/queryInverterReport", params)
        return data or {}

    # ---------------------------------------------------------------- usina
    async def async_get_plant_curve(
        self, plant_id: int, report_type: str = "0", data_time: str | None = None, data_items: str = "16399"
    ) -> list[dict[str, Any]]:
        """Curva da usina: reportType 0=dia(5min), 1=mês, 2=ano."""
        params = {"plantId": plant_id, "reportType": report_type, "dataItems": data_items}
        if data_time:
            params["dataTime"] = data_time
        data = await self._request("/analysis/plantReport/queryPlantReportByPlantId", params)
        return data or []

    async def async_get_plant_current_all(self, plant_id: int) -> dict[str, Any]:
        data = await self._request(
            "/analysis/plantReport/queryPlantCurrentDataAll", {"plantId": plant_id}
        )
        return data or {}

    # -------------------------------------------------------------- baterias
    async def async_get_batteries(self) -> list[dict[str, Any]]:
        return await self._request_rows("/archive/batDevice/list")

    async def async_get_batteries_by_plant(self, plant_id: int) -> list[dict[str, Any]]:
        data = await self._request(f"/archive/batDevice/getBatByPlant/{plant_id}")
        return data or []

    async def async_get_battery_current(self, sn: str) -> dict[str, Any] | None:
        return await self._request(f"/archive/batDevice/getBatCurrentData/{sn}")

    async def async_get_battery_curve(
        self, sn: str, report_type: str = "0", data_time: str | None = None, data_items: str = "16399"
    ) -> list[dict[str, Any]]:
        params = {"sn": sn, "reportType": report_type, "dataItems": data_items}
        if data_time:
            params["dataTime"] = data_time
        data = await self._request("/analysis/batReport/queryBatCurveBySn", params)
        return data or []

    # -------------------------------------------------------------- medidores
    async def async_get_meters(self) -> list[dict[str, Any]]:
        return await self._request_rows("/archive/meterDevice/list")

    async def async_get_meters_by_plant(self, plant_id: int) -> list[dict[str, Any]]:
        data = await self._request(f"/archive/meterDevice/getMeterByPlant/{plant_id}")
        return data or []

    async def async_get_meter_current(self, sn: str) -> dict[str, Any] | None:
        return await self._request(f"/archive/meterDevice/getMeterCurrentData/{sn}")

    async def async_get_meter_curve(
        self, sn: str, report_type: str = "0", data_time: str | None = None, data_items: str = "16399"
    ) -> list[dict[str, Any]]:
        params = {"sn": sn, "reportType": report_type, "dataItems": data_items}
        if data_time:
            params["dataTime"] = data_time
        data = await self._request("/analysis/meterDeviceReport/queryMeterCurveBySn", params)
        return data or []

    # ------------------------------------------------------- estação meteorológica
    async def async_get_weather_stations(self) -> list[dict[str, Any]]:
        return await self._request_rows("/archive/weatherDevice/list")

    async def async_get_weather_by_plant(self, plant_id: int) -> list[dict[str, Any]]:
        data = await self._request(f"/archive/weatherDevice/getWeatherByPlant/{plant_id}")
        return data or []

    async def async_get_weather_current(self, sn: str) -> dict[str, Any] | None:
        return await self._request(f"/archive/weatherDevice/getWeatherCurrentData/{sn}")

    # -------------------------------------------------------------- sensores PID
    async def async_get_pids(self) -> list[dict[str, Any]]:
        return await self._request_rows("/archive/pidDevice/list")

    async def async_get_pid_current(self, sn: str) -> dict[str, Any] | None:
        return await self._request(f"/archive/pidDevice/getPidCurrentData/{sn}")

    # ---------------------------------------------------------------- alarmes
    async def async_get_alarms(self) -> list[dict[str, Any]]:
        return await self._request_rows("/analysis/alarm/list")

    async def async_get_plant_alarms(self, plant_id: int) -> Any:
        return await self._request(f"/analysis/alarm/plantCurrentAlarm/{plant_id}")

    # -------------------------------------------------- esquemas de métricas
    async def async_get_data_tree(self, kind: str, sn: str) -> list[dict[str, Any]]:
        """Árvore de métricas suportadas por um tipo de equipamento.

        kind: 'inverter' | 'meter' | 'pid' | 'weather'
        """
        paths = {
            "inverter": f"/archive/dataitem/currentDataTreeSelect/{sn}",
            "meter": f"/analysis/meterDeviceReport/currentDataTreeSelect/{sn}",
            "pid": f"/analysis/pidDeviceReport/currentDataTreeSelect/{sn}",
            "weather": f"/analysis/weatherDeviceReport/currentDataTreeSelect/{sn}",
        }
        path = paths.get(kind)
        if not path:
            return []
        data = await self._request(path)
        return data or []
