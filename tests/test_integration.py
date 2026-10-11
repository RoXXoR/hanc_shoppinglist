"""End-to-end tests: entry setup, todo entity, sensor and services."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.hanc_shopping_list.const import DEFAULT_GUEST_NAME, DOMAIN
from custom_components.hanc_shopping_list.coordinator import HancShoppingListCoordinator
from custom_components.hanc_shopping_list.todo import render_summary

from .conftest import mock_ocs, ocs, register_standard_responses
from .const_test import (
    ACCOUNT_ENTRY_DATA,
    API,
    ITEMS_PAYLOAD,
    SHARE_ENTRY_DATA,
)


async def _setup_entry(hass: HomeAssistant, data: dict[str, Any]) -> MockConfigEntry:
    """Create and set up a config entry, returning it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alice @ cloud.example.com",
        data=data,
        source="user",
        unique_id=(
            "https://cloud.example.com|alice"
            if data.get("auth_method") == "account"
            else "https://cloud.example.com|share|abc123token"
        ),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    """Look up an entity id by unique id."""
    registry = er.async_get(hass)
    for entry in registry.entities.values():
        if entry.unique_id == unique_id and entry.platform == DOMAIN:
            return entry.entity_id
    return None


async def test_account_entry_creates_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An account entry creates a todo entity and a counts sensor per list."""
    register_standard_responses(aioclient_mock)
    entry = await _setup_entry(hass, ACCOUNT_ENTRY_DATA)

    assert entry.state is ConfigEntryState.LOADED

    todo_id = _entity_id(hass, "https://cloud.example.com|alice-1")
    sensor_id = _entity_id(hass, "https://cloud.example.com|alice-1-counts")
    assert todo_id == "todo.groceries"
    assert sensor_id == "sensor.groceries_counts"

    # list 2 is also selected
    assert _entity_id(hass, "https://cloud.example.com|alice-2") == "todo.hardware"


async def test_todo_entity_shows_items(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The todo entity renders items with quantity and unit folded in."""
    register_standard_responses(aioclient_mock)
    entry = await _setup_entry(hass, ACCOUNT_ENTRY_DATA)

    state = hass.states.get("todo.groceries")
    assert state is not None
    # 1 open item of 2 total
    assert state.state == "1"

    coordinator: HancShoppingListCoordinator = hass.data[DOMAIN][entry.entry_id]
    items = coordinator.get_state("1").items
    assert {render_summary(item) for item in items} == {"2 × Milk (l)", "Bread"}


async def test_sensor_counts_and_attributes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The counts sensor exposes open count plus full item details."""
    register_standard_responses(aioclient_mock)
    await _setup_entry(hass, ACCOUNT_ENTRY_DATA)

    state = hass.states.get("sensor.groceries_counts")
    assert state is not None
    assert state.state == "1"  # one open item
    attributes = state.attributes
    assert attributes["total"] == 2
    assert attributes["checked"] == 1

    by_id = {item["id"]: item for item in attributes["items"]}
    assert by_id[10]["name"] == "Milk"
    assert by_id[10]["quantity"] == "2"
    assert by_id[10]["unit"] == "l"
    assert by_id[10]["area"] == "Supermarket"
    assert by_id[10]["checked"] is False
    assert by_id[11]["checked"] is True
    assert by_id[11]["checked_by"] == "bob"


async def test_add_item_service(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The add_item service posts to the API with quantity and area."""
    register_standard_responses(aioclient_mock)
    created = {
        "id": 99,
        "listId": 1,
        "name": "Butter",
        "quantity": "2",
        "unit": "pcs",
        "shopAreaId": 100,
        "checked": False,
        "sortOrder": 3,
    }
    mock_ocs(aioclient_mock, "POST", "/lists/1/items", created, status=201)

    await _setup_entry(hass, ACCOUNT_ENTRY_DATA)

    await hass.services.async_call(
        DOMAIN,
        "add_item",
        {
            "entity_id": "todo.groceries",
            "name": "Butter",
            "quantity": "2",
            "unit": "pcs",
            "area": "Supermarket",
        },
        blocking=True,
    )

    calls = [call for call in aioclient_mock.mock_calls if call[0].upper() == "POST"]
    assert len(calls) == 1
    assert calls[0][2] == {
        "name": "Butter",
        "checked": False,
        "quantity": "2",
        "unit": "pcs",
        "shopAreaId": 100,
    }


async def test_add_item_service_unknown_area(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unknown shop area raises a clear error."""
    from homeassistant.exceptions import HomeAssistantError

    register_standard_responses(aioclient_mock)
    await _setup_entry(hass, ACCOUNT_ENTRY_DATA)

    with pytest.raises(HomeAssistantError, match="Unknown shop area"):
        await hass.services.async_call(
            DOMAIN,
            "add_item",
            {"entity_id": "todo.groceries", "name": "Butter", "area": "Nope"},
            blocking=True,
        )


async def test_clear_checked_and_uncheck_all(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The bulk services hit the documented endpoints."""
    register_standard_responses(aioclient_mock)
    aioclient_mock.delete(
        f"{API}/lists/1/items/checked",
        params={"format": "json"},
        json=ocs([]),
    )
    aioclient_mock.post(
        f"{API}/lists/1/items/uncheck-all",
        params={"format": "json"},
        json=ocs([]),
    )

    await _setup_entry(hass, ACCOUNT_ENTRY_DATA)

    await hass.services.async_call(
        DOMAIN, "clear_checked", {"entity_id": "todo.groceries"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "uncheck_all", {"entity_id": "todo.groceries"}, blocking=True
    )

    assert any("items/checked" in str(call[1]) for call in aioclient_mock.mock_calls)
    assert any("uncheck-all" in str(call[1]) for call in aioclient_mock.mock_calls)


async def test_check_item_via_todo_service(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Checking an item through the todo platform calls the check endpoint."""
    register_standard_responses(aioclient_mock)
    checked = {**ITEMS_PAYLOAD[0], "checked": True, "checkedByName": "alice"}
    mock_ocs(aioclient_mock, "PUT", "/lists/1/items/10/check", checked)

    await _setup_entry(hass, ACCOUNT_ENTRY_DATA)

    await hass.services.async_call(
        "todo",
        "update_item",
        {
            "entity_id": "todo.groceries",
            "item": "10",
            "status": "completed",
        },
        blocking=True,
    )

    check_calls = [
        call
        for call in aioclient_mock.mock_calls
        if call[0].upper() == "PUT" and "check" in str(call[1])
    ]
    assert len(check_calls) == 1
    assert check_calls[0][2] == {"checked": True}


async def test_share_entry_read_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A read-only share syncs items but the bulk services refuse."""
    from homeassistant.exceptions import HomeAssistantError

    register_standard_responses(
        aioclient_mock,
        shared_list={"title": "Party", "permission": 0},
        shared_items=ITEMS_PAYLOAD,
    )
    await _setup_entry(hass, SHARE_ENTRY_DATA)

    todo_id = _entity_id(hass, "https://cloud.example.com|share|abc123token-share")
    assert todo_id == "todo.party"

    with pytest.raises(HomeAssistantError, match="account connection"):
        await hass.services.async_call(
            DOMAIN, "clear_checked", {"entity_id": "todo.party"}, blocking=True
        )


async def test_share_entry_add_item(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A writable share accepts new items via the public API."""
    register_standard_responses(
        aioclient_mock, shared_list={"title": "Party", "permission": 1}
    )
    created = {
        "id": 55,
        "listId": 3,
        "name": "Chips",
        "quantity": "1",
        "unit": None,
        "shopAreaId": None,
        "checked": False,
        "sortOrder": 1,
    }
    mock_ocs(aioclient_mock, "POST", "/public/abc123token/items", created, status=201)

    await _setup_entry(hass, SHARE_ENTRY_DATA)

    await hass.services.async_call(
        DOMAIN,
        "add_item",
        {"entity_id": "todo.party", "name": "Chips"},
        blocking=True,
    )

    calls = [call for call in aioclient_mock.mock_calls if call[0].upper() == "POST"]
    assert len(calls) == 1
    assert calls[0][2] == {
        "name": "Chips",
        "guestName": DEFAULT_GUEST_NAME,
    }


async def test_auth_failure_triggers_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 during setup puts the entry into reauth state."""
    aioclient_mock.get(
        f"{API}/lists",
        params={"format": "json"},
        status=401,
        json=ocs({}, 401),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alice @ cloud.example.com",
        data=ACCOUNT_ENTRY_DATA,
        source="user",
        unique_id="https://cloud.example.com|alice",
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    # A reauth flow was started for this entry.
    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == "reauth"
        and flow["context"].get("entry_id") == entry.entry_id
    ]
    assert flows
