"""Constants and payloads shared by the HANC Shopping List tests."""

from __future__ import annotations

from typing import Any

BASE_URL = "https://cloud.example.com"
API = f"{BASE_URL}/ocs/v2.php/apps/shopping_list/api/v1"

ACCOUNT_ENTRY_DATA: dict[str, Any] = {
    "url": BASE_URL,
    "auth_method": "account",
    "username": "alice",
    "password": "app-password",
    "verify_ssl": True,
    "list_ids": ["1", "2"],
    "list_titles": {"1": "Groceries", "2": "Hardware"},
}

SHARE_ENTRY_DATA: dict[str, Any] = {
    "url": BASE_URL,
    "auth_method": "share_link",
    "token": "abc123token",
    "share_password": None,
    "verify_ssl": True,
}

LISTS_PAYLOAD = [
    {"id": 1, "title": "Groceries", "permission": 1, "isOwner": True},
    {"id": 2, "title": "Hardware", "permission": 0, "isOwner": False},
]

ITEMS_PAYLOAD = [
    {
        "id": 10,
        "listId": 1,
        "name": "Milk",
        "quantity": "2",
        "unit": "l",
        "shopAreaId": 100,
        "checked": False,
        "checkedByName": None,
        "addedByName": "alice",
        "sortOrder": 1,
    },
    {
        "id": 11,
        "listId": 1,
        "name": "Bread",
        "quantity": "1",
        "unit": None,
        "shopAreaId": None,
        "checked": True,
        "checkedByName": "bob",
        "addedByName": "alice",
        "sortOrder": 2,
    },
]

EMPTY_ITEMS_PAYLOAD: list[dict[str, Any]] = []

AREAS_PAYLOAD = [
    {"id": 100, "name": "Supermarket", "color": "#ff0000", "sortOrder": 1},
]

SHARED_LIST_PAYLOAD = {"title": "Party", "permission": 1}
