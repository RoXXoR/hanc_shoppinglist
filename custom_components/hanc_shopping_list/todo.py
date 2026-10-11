"""Todo platform for HANC Shopping List — one todo entity per list."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DEVICE_NAME, DOMAIN
from .coordinator import HancShoppingListCoordinator, ListState
from .models import ShoppingListItem

_LOGGER = logging.getLogger(__name__)

# Registry of live todo entities by entity_id so the domain services
# (add_item / clear_checked / uncheck_all) can resolve their targets.
ENTITIES: dict[str, HancShoppingListTodoEntity] = {}

SUPPORTED_FEATURES = (
    TodoListEntityFeature.CREATE_TODO_ITEM
    | TodoListEntityFeature.UPDATE_TODO_ITEM
    | TodoListEntityFeature.DELETE_TODO_ITEM
    | TodoListEntityFeature.MOVE_TODO_ITEM
)

# "2 × Milk" / "2 x Milk" — a leading quantity the user typed
_QTY_RE = re.compile(r"^(?P<qty>\d+(?:[.,]\d+)?)\s*[×x]\s+(?P<rest>.+)$")
# " (kg)" — a trailing unit in parentheses at the end of a summary
_UNIT_RE = re.compile(r"^(?P<name>.+?)\s+\((?P<unit>[^()\s]{1,12})\)$")
# Tokens we accept as an auto-detected unit when the item had none
_UNIT_LIKE_RE = re.compile(r"^[A-Za-zÄÖÜäöüß./µ%]{1,6}$")


def render_summary(item: ShoppingListItem) -> str:
    """Render an item as the todo summary shown in Home Assistant."""
    name = item.name
    quantity = (item.quantity or "").strip()
    if quantity and quantity != "1":
        name = f"{quantity} × {name}"
    if item.unit:
        name = f"{name} ({item.unit})"
    return name


def parse_summary(
    summary: str, *, current_quantity: str | None, current_unit: str | None
) -> tuple[str, str | None, str | None]:
    """Parse a user-edited summary back into (name, quantity, unit).

    A leading "N × " is always treated as the quantity. A trailing "(x)" is
    only treated as a unit when it matches the item's current unit or looks
    like a short unit token — so item names keep their parentheses.
    """
    summary = summary.strip()
    quantity = current_quantity
    match = _QTY_RE.match(summary)
    if match:
        quantity = match.group("qty")
        summary = match.group("rest").strip()

    unit = current_unit
    match = _UNIT_RE.match(summary)
    if match:
        candidate = match.group("unit")
        if candidate == current_unit or (
            current_unit is None and _UNIT_LIKE_RE.match(candidate)
        ):
            summary = match.group("name").strip()
            unit = candidate

    return summary, quantity, unit


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the todo entities of one config entry."""
    coordinator: HancShoppingListCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HancShoppingListTodoEntity(coordinator, entry, key)
        for key in coordinator.selected_list_keys
    )


class HancShoppingListTodoEntity(
    CoordinatorEntity[HancShoppingListCoordinator], TodoListEntity
):
    """Representation of one Nextcloud shopping list as a todo list."""

    _attr_has_entity_name = False
    _attr_supported_features = SUPPORTED_FEATURES
    _attr_icon = "mdi:cart-outline"

    def __init__(
        self,
        coordinator: HancShoppingListCoordinator,
        entry: ConfigEntry,
        list_key: str,
    ) -> None:
        """Initialize the todo list entity."""
        super().__init__(coordinator)
        self._list_key = list_key
        self._attr_unique_id = f"{entry.unique_id}-{list_key}"
        state = coordinator.get_state(list_key)
        self._attr_name = state.title if state else f"List {list_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=DEVICE_NAME,
            manufacturer="Nextcloud",
            model="Shopping List",
            configuration_url=entry.data["url"],
        )
        # Suggest a clean entity id ("todo.groceries") instead of one
        # prefixed with the device name.
        self.entity_id = f"todo.{slugify(self._attr_name)}"

    # ------------------------------------------------------------------
    # Coordinator plumbing
    # ------------------------------------------------------------------
    async def async_added_to_hass(self) -> None:
        """Register the entity for the domain services."""
        await super().async_added_to_hass()
        ENTITIES[self.entity_id] = self
        self.async_on_remove(lambda: ENTITIES.pop(self.entity_id, None))

    @property
    def list_key(self) -> str:
        """Return the coordinator key of this list."""
        return self._list_key

    @property
    def state_value(self) -> ListState | None:
        """Return the cached state of this list."""
        return self.coordinator.get_state(self._list_key)

    @property
    def available(self) -> bool:
        """Return True when the coordinator is healthy and knows the list."""
        return super().available and self.state_value is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        state = self.state_value
        if state is not None and state.title != self._attr_name:
            self._attr_name = state.title
        super()._handle_coordinator_update()

    # ------------------------------------------------------------------
    # TodoListEntity interface
    # ------------------------------------------------------------------
    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return the current items of the list."""
        state = self.state_value
        if state is None:
            return None
        return [
            TodoItem(
                uid=str(item.id),
                summary=render_summary(item),
                status=(
                    TodoItemStatus.COMPLETED
                    if item.checked
                    else TodoItemStatus.NEEDS_ACTION
                ),
                description=self.coordinator.area_name(
                    self._list_key, item.shop_area_id
                ),
            )
            for item in state.items
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add an item to the list."""
        summary = (item.summary or "").strip()
        if not summary:
            raise HomeAssistantError("Item name must not be empty")

        quantity: str | None = None
        name = summary
        match = _QTY_RE.match(summary)
        if match:
            quantity = match.group("qty")
            name = match.group("rest").strip()

        try:
            await self.coordinator.async_add_item(
                self._list_key, name=name, quantity=quantity
            )
        except Exception as err:
            raise HomeAssistantError(f"Could not add item: {err}") from err

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Check/uncheck or rename an item."""
        local = self._find_local(item.uid)
        if local is None:
            raise HomeAssistantError("Item no longer exists on the list")

        checked = item.status == TodoItemStatus.COMPLETED
        summary = (item.summary or "").strip()
        updates: dict[str, Any] = {}

        if checked != local.checked:
            updates["checked"] = checked

        current_summary = render_summary(local)
        if summary and summary != current_summary:
            name, quantity, unit = parse_summary(
                summary,
                current_quantity=local.quantity,
                current_unit=local.unit,
            )
            if not name:
                raise HomeAssistantError("Item name must not be empty")
            updates["name"] = name
            if quantity != local.quantity:
                updates["quantity"] = quantity
            if unit != local.unit:
                updates["unit"] = unit

        if not updates:
            return

        try:
            if "checked" in updates:
                await self.coordinator.async_set_checked(
                    self._list_key, local.id, updates["checked"]
                )
            if "name" in updates or "quantity" in updates or "unit" in updates:
                await self.coordinator.async_update_item(
                    self._list_key,
                    local.id,
                    name=updates.get("name"),
                    quantity=updates.get("quantity", local.quantity),
                    unit=updates.get("unit", local.unit),
                )
        except Exception as err:
            raise HomeAssistantError(f"Could not update item: {err}") from err

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete items from the list."""
        try:
            for uid in uids:
                local = self._find_local(uid)
                if local is not None:
                    await self.coordinator.async_delete_item(self._list_key, local.id)
        except Exception as err:
            raise HomeAssistantError(f"Could not delete item: {err}") from err

    async def async_move_todo_item(
        self, uid: str, previous_uid: str | None = None
    ) -> None:
        """Move an item to a new position in the list."""
        state = self.state_value
        if state is None:
            raise HomeAssistantError("List is not available")

        ids = [item.id for item in state.items]
        try:
            moving = int(uid)
        except ValueError as err:
            raise HomeAssistantError("Invalid item") from err
        if moving not in ids:
            raise HomeAssistantError("Item no longer exists on the list")

        ids.remove(moving)
        if previous_uid is None:
            ids.insert(0, moving)
        else:
            try:
                index = ids.index(int(previous_uid))
            except (ValueError, TypeError):
                index = len(ids) - 1
            ids.insert(index + 1, moving)

        try:
            await self.coordinator.async_reorder(self._list_key, ids)
        except Exception as err:
            raise HomeAssistantError(f"Could not reorder items: {err}") from err

    # ------------------------------------------------------------------
    # Helpers used by the domain services
    # ------------------------------------------------------------------
    def _find_local(self, uid: str | None) -> ShoppingListItem | None:
        """Find an item of this list by its uid."""
        state = self.state_value
        if state is None or uid is None:
            return None
        try:
            item_id = int(uid)
        except ValueError:
            return None
        for item in state.items:
            if item.id == item_id:
                return item
        return None

    async def async_service_add_item(
        self,
        *,
        name: str,
        quantity: str | None = None,
        unit: str | None = None,
        area: str | None = None,
        checked: bool = False,
    ) -> None:
        """Handle the hanc_shopping_list.add_item service for this list."""
        area_id: int | None = None
        if area:
            area_id = self.coordinator.find_area_id_by_name(self._list_key, area)
            if area_id is None:
                raise HomeAssistantError(
                    f"Unknown shop area '{area}' on list '{self._attr_name}'"
                )
        try:
            await self.coordinator.async_add_item(
                self._list_key,
                name=name,
                quantity=quantity,
                unit=unit,
                area_id=area_id,
                checked=checked,
            )
        except Exception as err:
            raise HomeAssistantError(f"Could not add item: {err}") from err
