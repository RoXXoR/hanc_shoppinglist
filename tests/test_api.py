"""Tests for the OCS API client."""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.hanc_shopping_list.api import (
    InvalidAuthError,
    NotFoundError,
    ReadOnlyShareError,
    SharePasswordRequiredError,
    ShoppingListApiClient,
)

from .conftest import mock_ocs, ocs
from .const_test import API, AREAS_PAYLOAD, ITEMS_PAYLOAD


@pytest.fixture
async def account_client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> ShoppingListApiClient:
    """Return a client for an account connection.

    Depends on ``aioclient_mock`` so the HA session factory is patched
    before the session is created.
    """
    return ShoppingListApiClient(
        async_get_clientsession(hass),
        "https://cloud.example.com",
        username="alice",
        password="secret",
    )


@pytest.fixture
async def share_client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> ShoppingListApiClient:
    """Return a client for a public share link."""
    return ShoppingListApiClient(
        async_get_clientsession(hass),
        "https://cloud.example.com",
        auth_method="share_link",
        token="abc123token",
    )


def _sequence(*payloads: dict[str, Any]):
    """Return a side_effect serving a different payload per call."""

    async def _side_effect(method: str, url: web.URL, data: Any):
        index = min(_side_effect.calls, len(payloads) - 1)
        _side_effect.calls += 1
        payload = payloads[index]
        return AiohttpClientMockResponse(
            method=method,
            url=url,
            status=payload.get("_status", 200),
            json={k: v for k, v in payload.items() if k != "_status"},
        )

    _side_effect.calls = 0
    return _side_effect


async def test_get_lists(
    hass: HomeAssistant,
    account_client: ShoppingListApiClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The client unwraps the OCS envelope and parses lists."""
    mock_ocs(
        aioclient_mock,
        "GET",
        "/lists",
        [{"id": 1, "title": "Groceries", "permission": 2, "isOwner": True}],
    )

    lists = await account_client.async_get_lists()

    assert [lst.id for lst in lists] == [1]
    assert lists[0].title == "Groceries"
    assert lists[0].writable is True

    call = aioclient_mock.mock_calls[0]
    headers = call[3]
    assert headers["OCS-APIRequest"] == "true"
    assert headers["Authorization"].startswith("Basic ")


async def test_get_items_and_areas(
    hass: HomeAssistant,
    account_client: ShoppingListApiClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Items and areas are parsed into models."""
    mock_ocs(aioclient_mock, "GET", "/lists/1/items", ITEMS_PAYLOAD)
    mock_ocs(aioclient_mock, "GET", "/lists/1/areas", AREAS_PAYLOAD)

    items = await account_client.async_get_items(1)
    areas = await account_client.async_get_areas(1)

    assert len(items) == 2
    assert items[0].name == "Milk"
    assert items[0].quantity == "2"
    assert items[0].unit == "l"
    assert items[0].shop_area_id == 100
    assert items[0].checked is False
    assert items[1].checked is True
    assert items[1].checked_by_name == "bob"
    assert areas[0].name == "Supermarket"


async def test_invalid_auth(
    hass: HomeAssistant,
    account_client: ShoppingListApiClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A 401 maps to InvalidAuthError."""
    aioclient_mock.get(
        f"{API}/lists",
        params={"format": "json"},
        status=401,
        json=ocs({}, 401),
    )

    with pytest.raises(InvalidAuthError):
        await account_client.async_get_lists()


async def test_not_found(
    hass: HomeAssistant,
    account_client: ShoppingListApiClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A 404 maps to NotFoundError."""
    aioclient_mock.get(
        f"{API}/lists/1/items",
        params={"format": "json"},
        status=404,
        json=ocs({}, 404),
    )

    with pytest.raises(NotFoundError):
        await account_client.async_get_items(1)


async def test_non_json_error_page(
    hass: HomeAssistant,
    account_client: ShoppingListApiClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An HTML 404 page (app missing) maps to NotFoundError."""
    aioclient_mock.get(
        f"{API}/lists",
        params={"format": "json"},
        status=404,
        text="<html>Not found</html>",
    )

    with pytest.raises(NotFoundError):
        await account_client.async_get_lists()


async def test_create_and_check_item(
    hass: HomeAssistant,
    account_client: ShoppingListApiClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Writes send the documented payloads."""
    created = {
        "id": 12,
        "listId": 1,
        "name": "Eggs",
        "quantity": "6",
        "unit": None,
        "shopAreaId": None,
        "checked": False,
        "sortOrder": 3,
    }
    checked = {**created, "checked": True, "checkedByName": "alice"}
    mock_ocs(aioclient_mock, "POST", "/lists/1/items", created, status=201)
    mock_ocs(aioclient_mock, "PUT", "/lists/1/items/12/check", checked)

    item = await account_client.async_create_item(1, name="Eggs", quantity="6")
    assert item.id == 12
    assert item.checked is False

    item = await account_client.async_set_checked(1, 12, True)
    assert item.checked is True

    assert aioclient_mock.mock_calls[0][2] == {
        "name": "Eggs",
        "checked": False,
        "quantity": "6",
    }
    assert aioclient_mock.mock_calls[1][2] == {"checked": True}


async def test_update_and_reorder(
    hass: HomeAssistant,
    account_client: ShoppingListApiClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Rename/quantity updates and reorders hit the right endpoints."""
    updated = {
        "id": 12,
        "listId": 1,
        "name": "Free-range Eggs",
        "quantity": "6",
        "unit": None,
        "shopAreaId": None,
        "checked": False,
        "sortOrder": 3,
    }
    mock_ocs(aioclient_mock, "PUT", "/lists/1/items/12", updated)
    mock_ocs(aioclient_mock, "POST", "/lists/1/items/reorder", [])

    item = await account_client.async_update_item(
        1, 12, name="Free-range Eggs", quantity="6", unit=None
    )
    assert item.name == "Free-range Eggs"
    # unit is always sent so it can be explicitly cleared
    assert aioclient_mock.mock_calls[0][2] == {
        "name": "Free-range Eggs",
        "quantity": "6",
        "unit": None,
    }

    await account_client.async_reorder_items(1, [12, 10, 11])
    assert aioclient_mock.mock_calls[1][2] == {"sortedIds": [12, 10, 11]}


async def test_share_read_only_rejected(
    hass: HomeAssistant,
    share_client: ShoppingListApiClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A read-only share reports a clear error on write attempts."""
    aioclient_mock.post(
        f"{API}/public/abc123token/items",
        params={"format": "json"},
        status=403,
        json=ocs({"message": "Read-only access"}, 403),
    )

    with pytest.raises(ReadOnlyShareError):
        await share_client.async_create_shared_item(name="Milk")


async def test_password_protected_share_unlocks(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A protected share authenticates with the stored password and retries."""
    client = ShoppingListApiClient(
        async_get_clientsession(hass),
        "https://cloud.example.com",
        auth_method="share_link",
        token="abc123token",
        share_password="hunter2",
    )

    aioclient_mock.get(
        f"{API}/public/abc123token/items",
        params={"format": "json"},
        side_effect=_sequence(
            {**ocs({"passwordRequired": True}, 403), "_status": 403},
            ocs(ITEMS_PAYLOAD),
        ),
    )
    mock_ocs(aioclient_mock, "POST", "/public/abc123token/auth", True)

    items = await client.async_get_shared_items()

    assert len(items) == 2
    # call 1: the 403, call 2: the unlock, call 3: the retry
    assert aioclient_mock.mock_calls[1][2] == {"password": "hunter2"}
    assert str(aioclient_mock.mock_calls[2][1]).endswith("/items?format=json")


async def test_password_required_without_password(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Without a stored password the client reports SharePasswordRequiredError."""
    client = ShoppingListApiClient(
        async_get_clientsession(hass),
        "https://cloud.example.com",
        auth_method="share_link",
        token="abc123token",
    )
    aioclient_mock.get(
        f"{API}/public/abc123token/items",
        params={"format": "json"},
        status=403,
        json=ocs({"passwordRequired": True}, 403),
    )

    with pytest.raises(SharePasswordRequiredError):
        await client.async_get_shared_items()


async def test_share_list_metadata(
    hass: HomeAssistant,
    share_client: ShoppingListApiClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Share links expose title and permission."""
    mock_ocs(
        aioclient_mock,
        "GET",
        "/public/abc123token",
        {"title": "Party", "permission": 1},
    )

    shared = await share_client.async_get_shared_list()

    assert shared.title == "Party"
    assert shared.writable is True
