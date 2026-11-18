"""Integração Auxsol Solar para Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuxsolAuthError, AuxsolClient, AuxsolError
from .const import (
    CONF_ACCOUNT,
    CONF_BASELINE,
    CONF_PLANT_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import AuxsolCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

AuxsolConfigEntry = ConfigEntry[AuxsolCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AuxsolConfigEntry) -> bool:
    """Configura a integração a partir de uma config entry."""
    session = async_get_clientsession(hass)
    client = AuxsolClient(
        session,
        entry.data[CONF_ACCOUNT],
        entry.data[CONF_PASSWORD],
    )

    coordinator = AuxsolCoordinator(
        hass,
        client,
        entry.data.get(CONF_PLANT_ID),
        entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL),
        # Sem valor explícito nas opções, o coordinator detecta sozinho
        # a energia de comissionamento da usina.
        entry.options.get(CONF_BASELINE),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except AuxsolAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except AuxsolError as err:
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AuxsolConfigEntry) -> bool:
    """Descarrega a integração."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_options(hass: HomeAssistant, entry: AuxsolConfigEntry) -> None:
    """Recarrega quando as opções mudam."""
    await hass.config_entries.async_reload(entry.entry_id)
