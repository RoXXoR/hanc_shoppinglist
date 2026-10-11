"""Data update coordinator for HANC Shopping List."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CannotConnectError,
    InvalidAuthError,
    NotFoundError,
    SharePasswordRequiredError,
    ShoppingListApiClient,
    ShoppingListError,
)
from .const import (
    AUTH_METHOD_ACCOUNT,
    CONF_AUTH_METHOD,
    CONF_GUEST_NAME,
    CONF_LIST_IDS,
    CONF_SCAN_INTERVAL,
    CONF_SHARE_PASSWORD,
    CONF_VERIFY_SSL,
    DEFAULT_GUEST_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SHARE_LIST_KEY,
)
from .models import ShopArea, ShoppingList, ShoppingListItem

_LOGGER = logging.getLogger(__name__)


@dataclass
class ListState:
    """Snapshot of one shopping list."""

    key: str
    title: str
    writable: bool
    list_id: int | None = None
    items: list[ShoppingListItem] = field(default_factory=list)
    areas: dict[int, ShopArea] = field(default_factory=dict)


class HancShoppingListCoordinator(DataUpdateCoordinator[dict[str, ListState]]):
    """Coordinator polling the Nextcloud Shopping List API.

    Data is a mapping of list key -> :class:`ListState`. For account mode the
    key is the numeric list id as string; for share links it is
    :data:`SHARE_LIST_KEY`.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Create the coordinator for a config entry."""
        self.entry = entry
        session = async_get_clientsession(
            hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)
        )
        self.client = ShoppingListApiClient(
            session,
            entry.data["url"],
            auth_method=entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_ACCOUNT),
            username=entry.data.get("username"),
            password=entry.data.get("password"),
            token=entry.data.get("token"),
            share_password=entry.data.get(CONF_SHARE_PASSWORD),
        )
        self.guest_name: str = entry.options.get(CONF_GUEST_NAME, DEFAULT_GUEST_NAME)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    @property
    def is_share_link(self) -> bool:
        """Return True when the entry uses a public share link."""
        return self.client.is_share_link

    @property
    def selected_list_keys(self) -> list[str]:
        """Return the list keys the user selected in the options flow."""
        if self.is_share_link:
            return [SHARE_LIST_KEY]
        list_ids = self.entry.options.get(
            CONF_LIST_IDS, self.entry.data.get(CONF_LIST_IDS, [])
        )
        return [str(list_id) for list_id in list_ids]

    async def _async_update_data(self) -> dict[str, ListState]:
        """Fetch the latest state of all selected lists."""
        try:
            return await self._async_fetch_all()
        except InvalidAuthError as err:
            raise ConfigEntryAuthFailed(
                "Nextcloud rejected the credentials; please re-authenticate"
            ) from err
        except SharePasswordRequiredError as err:
            raise ConfigEntryAuthFailed(
                "The share link password is missing or wrong"
            ) from err
        except (CannotConnectError, NotFoundError, ShoppingListError) as err:
            raise UpdateFailed(str(err)) from err

    async def _async_fetch_all(self) -> dict[str, ListState]:
        """Fetch all selected lists from the server."""
        states: dict[str, ListState] = {}
        selected = self.selected_list_keys

        if self.is_share_link:
            shared = await self.client.async_get_shared_list()
            state = ListState(
                key=SHARE_LIST_KEY,
                title=shared.title,
                writable=shared.writable,
                list_id=None,
            )
            state.items = await self.client.async_get_shared_items()
            state.areas = {
                area.id: area for area in await self.client.async_get_shared_areas()
            }
            states[SHARE_LIST_KEY] = state
            return states

        lists = {str(lst.id): lst for lst in await self.client.async_get_lists()}
        for key in selected:
            shopping_list: ShoppingList | None = lists.get(key)
            if shopping_list is None:
                _LOGGER.warning(
                    "List %s is no longer visible to the account, skipping", key
                )
                continue
            state = ListState(
                key=key,
                title=shopping_list.title,
                writable=shopping_list.writable,
                list_id=shopping_list.id,
            )
            try:
                state.items = await self.client.async_get_items(shopping_list.id)
                state.areas = {
                    area.id: area
                    for area in await self.client.async_get_areas(shopping_list.id)
                }
            except NotFoundError:
                _LOGGER.warning("List %s vanished while syncing", key)
                continue
            states[key] = state
        return states

    # ------------------------------------------------------------------
    # Write helpers used by entities and services
    # ------------------------------------------------------------------
    async def async_add_item(
        self,
        key: str,
        *,
        name: str,
        quantity: str | None = None,
        unit: str | None = None,
        area_id: int | None = None,
        checked: bool = False,
    ) -> None:
        """Add an item to a list and refresh immediately."""
        if self.is_share_link:
            await self.client.async_create_shared_item(
                name=name,
                quantity=quantity,
                unit=unit,
                shop_area_id=area_id,
                guest_name=self.guest_name,
            )
        else:
            await self.client.async_create_item(
                int(key),
                name=name,
                quantity=quantity,
                unit=unit,
                shop_area_id=area_id,
                checked=checked,
            )
        await self.async_request_refresh()

    async def async_set_checked(self, key: str, item_id: int, checked: bool) -> None:
        """Check or uncheck an item and refresh immediately."""
        if self.is_share_link:
            await self.client.async_set_shared_checked(
                item_id, checked, guest_name=self.guest_name
            )
        else:
            await self.client.async_set_checked(int(key), item_id, checked)
        await self.async_request_refresh()

    async def async_update_item(
        self,
        key: str,
        item_id: int,
        *,
        name: str | None = None,
        quantity: str | None = None,
        unit: str | None = None,
    ) -> None:
        """Update an item's name/quantity/unit and refresh immediately."""
        if self.is_share_link:
            await self.client.async_update_shared_item(
                item_id, name=name, quantity=quantity, unit=unit
            )
        else:
            await self.client.async_update_item(
                int(key), item_id, name=name, quantity=quantity, unit=unit
            )
        await self.async_request_refresh()

    async def async_reorder(self, key: str, sorted_ids: list[int]) -> None:
        """Persist a new item order and refresh immediately."""
        if self.is_share_link:
            await self.client.async_reorder_shared_items(sorted_ids)
        else:
            await self.client.async_reorder_items(int(key), sorted_ids)
        await self.async_request_refresh()

    async def async_delete_item(self, key: str, item_id: int) -> None:
        """Delete an item and refresh immediately."""
        if self.is_share_link:
            await self.client.async_delete_shared_item(item_id)
        else:
            await self.client.async_delete_item(int(key), item_id)
        await self.async_request_refresh()

    async def async_clear_checked(self, key: str) -> None:
        """Delete all checked items (account mode only)."""
        await self.client.async_clear_checked(int(key))
        await self.async_request_refresh()

    async def async_uncheck_all(self, key: str) -> None:
        """Uncheck every item (account mode only)."""
        await self.client.async_uncheck_all(int(key))
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # Helpers for entities
    # ------------------------------------------------------------------
    def get_state(self, key: str) -> ListState | None:
        """Return the cached state of one list."""
        if not self.data:
            return None
        return self.data.get(key)

    def area_name(self, key: str, area_id: int | None) -> str | None:
        """Return the shop area name for an area id."""
        state = self.get_state(key)
        if state is None or area_id is None:
            return None
        area = state.areas.get(area_id)
        return area.name if area else None

    def find_area_id_by_name(self, key: str, name: str) -> int | None:
        """Resolve a shop area name to its id (case-insensitive)."""
        state = self.get_state(key)
        if state is None:
            return None
        wanted = name.strip().casefold()
        for area in state.areas.values():
            if area.name.casefold() == wanted:
                return area.id
        return None

    def as_diagnostics(self) -> dict[str, Any]:
        """Return a redacted representation for the diagnostics download."""
        info: dict[str, Any] = {
            "auth_method": self.entry.data.get(CONF_AUTH_METHOD),
            "url": self.entry.data.get("url"),
            "scan_interval": self.entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            ),
            "selected_list_keys": self.selected_list_keys,
            "last_update_success": self.last_update_success,
            "lists": [],
        }
        for state in (self.data or {}).values():
            info["lists"].append(
                {
                    "key": state.key,
                    "title": state.title,
                    "writable": state.writable,
                    "item_count": len(state.items),
                    "checked_count": sum(1 for i in state.items if i.checked),
                    "areas": [area.name for area in state.areas.values()],
                }
            )
        return info
