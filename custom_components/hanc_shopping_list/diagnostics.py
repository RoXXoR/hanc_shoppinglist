"""Diagnostics support for HANC Shopping List."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import CONF_SHARE_PASSWORD
from .coordinator import HancShoppingListCoordinator

REDACT_KEYS = {CONF_PASSWORD, CONF_USERNAME, CONF_SHARE_PASSWORD, "token", "url"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: HancShoppingListCoordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), REDACT_KEYS),
        "coordinator": coordinator.as_diagnostics(),
    }
