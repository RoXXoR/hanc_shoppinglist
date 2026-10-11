"""Shared fixtures and helpers for the HANC Shopping List tests."""

from __future__ import annotations

from typing import Any

import pytest

from .const_test import (
    ACCOUNT_ENTRY_DATA,
    API,
    AREAS_PAYLOAD,
    EMPTY_ITEMS_PAYLOAD,
    ITEMS_PAYLOAD,
    LISTS_PAYLOAD,
    SHARE_ENTRY_DATA,
    SHARED_LIST_PAYLOAD,
)

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Allow loading the integration from ./custom_components in tests."""


def ocs(data: Any, statuscode: int = 200) -> dict[str, Any]:
    """Wrap a payload in the OCS response envelope."""
    status = "ok" if statuscode < 400 else "failure"
    return {
        "ocs": {
            "meta": {
                "status": status,
                "statuscode": statuscode,
                "message": "OK" if statuscode < 400 else "Error",
            },
            "data": data,
        }
    }


def mock_ocs(
    aioclient_mock: Any,
    method: str,
    path: str,
    data: Any,
    *,
    status: int = 200,
    raw: bool = False,
) -> None:
    """Register a mocked OCS endpoint on the HA aiohttp test mocker.

    ``raw`` registers the payload as-is, otherwise it is wrapped in the OCS
    envelope. Registering the same (method, path, params) twice makes the
    second answer take precedence for subsequent calls in some situations,
    so tests that need sequences should register one response per call by
    using distinct query parameters or ``side_effect``-style helpers here.
    """
    getattr(aioclient_mock, method.lower())(
        f"{API}{path}",
        params={"format": "json"},
        status=status,
        json=data if raw else ocs(data, status),
    )


def register_standard_responses(
    aioclient_mock: Any,
    *,
    lists: list | None = None,
    items: dict[int, list] | None = None,
    areas: dict[int, list] | None = None,
    shared_list: dict | None = None,
    shared_items: list | None = None,
) -> None:
    """Register the standard Nextcloud responses on the HA aiohttp mocker."""
    lists = LISTS_PAYLOAD if lists is None else lists
    items = {1: ITEMS_PAYLOAD, 2: EMPTY_ITEMS_PAYLOAD} if items is None else items
    areas = {1: AREAS_PAYLOAD, 2: []} if areas is None else areas

    mock_ocs(aioclient_mock, "GET", "/lists", lists)
    for list_id, payload in items.items():
        mock_ocs(aioclient_mock, "GET", f"/lists/{list_id}/items", payload)
    for list_id, payload in areas.items():
        mock_ocs(aioclient_mock, "GET", f"/lists/{list_id}/areas", payload)
    mock_ocs(
        aioclient_mock,
        "GET",
        "/public/abc123token",
        SHARED_LIST_PAYLOAD if shared_list is None else shared_list,
    )
    mock_ocs(
        aioclient_mock,
        "GET",
        "/public/abc123token/items",
        [] if shared_items is None else shared_items,
    )
    mock_ocs(aioclient_mock, "GET", "/public/abc123token/areas", AREAS_PAYLOAD)


@pytest.fixture
def mocked_api(aioclient_mock: Any) -> Any:
    """Provide the HA aiohttp mocker pre-loaded with standard responses."""
    register_standard_responses(aioclient_mock)
    return aioclient_mock


@pytest.fixture
def bare_api(aioclient_mock: Any) -> Any:
    """Provide a bare HA aiohttp mocker (tests register their own routes)."""
    return aioclient_mock


__all__ = [
    "ACCOUNT_ENTRY_DATA",
    "API",
    "AREAS_PAYLOAD",
    "EMPTY_ITEMS_PAYLOAD",
    "ITEMS_PAYLOAD",
    "LISTS_PAYLOAD",
    "SHARED_LIST_PAYLOAD",
    "SHARE_ENTRY_DATA",
    "mock_ocs",
    "ocs",
    "register_standard_responses",
]
