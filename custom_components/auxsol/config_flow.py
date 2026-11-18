"""Config flow da integração Auxsol Solar."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuxsolAuthError, AuxsolClient, AuxsolError
from .const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_PLANT_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class AuxsolConfigFlow(ConfigFlow, domain=DOMAIN):
    """Fluxo de configuração da Auxsol."""

    VERSION = 1

    def __init__(self) -> None:
        self._plants: list[dict[str, Any]] = []
        self._account: str | None = None
        self._password: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = AuxsolClient(
                session, user_input[CONF_ACCOUNT], user_input[CONF_PASSWORD]
            )
            try:
                self._plants = await client.async_validate()
            except AuxsolAuthError:
                errors["base"] = "invalid_auth"
            except AuxsolError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Erro inesperado validando credenciais Auxsol")
                errors["base"] = "unknown"
            else:
                if not self._plants:
                    errors["base"] = "no_plants"
                else:
                    self._account = user_input[CONF_ACCOUNT]
                    self._password = user_input[CONF_PASSWORD]
                    return await self.async_step_plant()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_plant(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            plant_id = user_input[CONF_PLANT_ID]
            plant = next(
                (p for p in self._plants if str(p.get("plantId")) == str(plant_id)), {}
            )
            await self.async_set_unique_id(f"auxsol_{plant_id}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=plant.get("plantName") or f"Planta {plant_id}",
                data={
                    CONF_ACCOUNT: self._account,
                    CONF_PASSWORD: self._password,
                    CONF_PLANT_ID: int(plant_id),
                },
            )

        options = {
            str(p.get("plantId")): p.get("plantName") or str(p.get("plantId"))
            for p in self._plants
        }
        return self.async_show_form(
            step_id="plant",
            data_schema=vol.Schema({vol.Required(CONF_PLANT_ID): vol.In(options)}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return AuxsolOptionsFlow()


class AuxsolOptionsFlow(OptionsFlow):
    """Opções: intervalo de atualização."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            "scan_interval", DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional("scan_interval", default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=60, max=3600)
                    )
                }
            ),
        )
