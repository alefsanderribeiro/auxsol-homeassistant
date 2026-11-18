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
    """Cliente mínimo para a API da Auxsol Cloud."""

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

    # ---------------------------------------------------------------- leitura
    async def async_validate(self) -> list[dict[str, Any]]:
        """Valida credenciais e devolve a lista de plantas."""
        await self._ensure_token(force=True)
        return await self.async_get_plants()

    async def async_get_plants(self) -> list[dict[str, Any]]:
        data = await self._request("/archive/plant/list")
        return (data or {}).get("rows") or []

    async def async_get_collectors(self) -> list[dict[str, Any]]:
        data = await self._request("/archive/collector/list")
        return (data or {}).get("rows") or []

    async def async_get_inverters(self) -> list[dict[str, Any]]:
        data = await self._request("/archive/inverter/list")
        return (data or {}).get("rows") or []

    async def async_get_realtime(self, sn: str) -> dict[str, Any] | None:
        return await self._request(
            "/analysis/inverterReport/findInverterRealTimeInfoBySnV1", {"sn": sn}
        )
