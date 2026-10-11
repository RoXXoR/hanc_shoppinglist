"""The HANC Shopping List integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .coordinator import HancShoppingListCoordinator
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

type HancConfigEntry = ConfigEntry[HancShoppingListCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: HancConfigEntry) -> bool:
    """Set up one Nextcloud account (or share link) from a config entry."""
    coordinator = HancShoppingListCoordinator(hass, entry)
    # Raises ConfigEntryAuthFailed when Nextcloud rejected the credentials.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator

    async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_purge_stale_entities(hass, entry)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HancConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: HancConfigEntry) -> None:
    """Reload the entry when its options change (list selection, interval)."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_purge_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove registry entries of lists the user deselected in the options."""
    coordinator: HancShoppingListCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.is_share_link:
        return

    valid_keys = set(coordinator.selected_list_keys)
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.platform != DOMAIN:
            continue
        unique_id = reg_entry.unique_id
        # unique_ids look like "<entry-unique-id>-<list-key>[-counts]"
        parts = unique_id.split(f"{entry.unique_id}-", 1)
        if len(parts) != 2:
            continue
        suffix = parts[1]
        list_key = suffix.removesuffix("-counts")
        if list_key not in valid_keys:
            _LOGGER.debug(
                "Removing stale entity %s (list %s deselected)",
                reg_entry.entity_id,
                list_key,
            )
            registry.async_remove(reg_entry.entity_id)
