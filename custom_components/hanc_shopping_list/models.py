"""Data models for the HANC Shopping List integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ShoppingList:
    """A Nextcloud shopping list."""

    id: int
    title: str
    permission: int
    is_owner: bool

    @property
    def writable(self) -> bool:
        """Return True if the current user may modify the list."""
        return self.permission >= 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShoppingList:
        """Build a list from the OCS JSON payload."""
        return cls(
            id=int(data["id"]),
            title=str(data.get("title") or "Shopping list"),
            permission=int(data.get("permission", 0)),
            is_owner=bool(data.get("isOwner", False)),
        )


@dataclass(frozen=True)
class ShopArea:
    """A shop area (aisle/department) within a list."""

    id: int
    name: str
    color: str | None
    sort_order: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShopArea:
        """Build an area from the OCS JSON payload."""
        return cls(
            id=int(data["id"]),
            name=str(data.get("name") or ""),
            color=data.get("color"),
            sort_order=int(data.get("sortOrder") or 0),
        )


@dataclass(frozen=True)
class ShoppingListItem:
    """A single item on a shopping list."""

    id: int
    list_id: int
    name: str
    quantity: str | None
    unit: str | None
    shop_area_id: int | None
    checked: bool
    checked_by_name: str | None
    added_by_name: str | None
    sort_order: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShoppingListItem:
        """Build an item from the OCS JSON payload."""
        return cls(
            id=int(data["id"]),
            list_id=int(data.get("listId") or 0),
            name=str(data.get("name") or ""),
            quantity=(
                str(data["quantity"]) if data.get("quantity") is not None else None
            ),
            unit=(str(data["unit"]) if data.get("unit") is not None else None),
            shop_area_id=(
                int(data["shopAreaId"]) if data.get("shopAreaId") is not None else None
            ),
            checked=bool(data.get("checked", False)),
            checked_by_name=data.get("checkedByName"),
            added_by_name=data.get("addedByName"),
            sort_order=int(data.get("sortOrder") or 0),
        )
