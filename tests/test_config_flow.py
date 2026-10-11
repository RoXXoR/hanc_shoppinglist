"""Tests for the config and options flow."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.hanc_shopping_list.const import (
    AUTH_METHOD_SHARE_LINK,
    DOMAIN,
)

from .conftest import mock_ocs, ocs
from .const_test import API, LISTS_PAYLOAD, SHARE_ENTRY_DATA


def _register_lists(aioclient_mock: AiohttpClientMocker, **kwargs: Any) -> None:
    """Register GET /lists with the given payload (defaults to standard)."""
    mock_ocs(aioclient_mock, "GET", "/lists", LISTS_PAYLOAD, **kwargs)


async def test_user_flow_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Full account setup flow: URL, credentials, list selection."""
    _register_lists(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "url": "cloud.example.com",
            "auth_method": "account",
            "verify_ssl": True,
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "account"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "alice", "password": "app-password"},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "lists"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"list_ids": ["1", "2"]}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "alice @ cloud.example.com"
    assert result["data"]["url"] == "https://cloud.example.com"
    assert result["data"]["username"] == "alice"
    assert result["data"]["list_ids"] == ["1", "2"]
    assert result["data"]["list_titles"] == {"1": "Groceries", "2": "Hardware"}


async def test_user_flow_invalid_auth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 shows the invalid_auth error on the account step."""
    aioclient_mock.get(
        f"{API}/lists",
        params={"format": "json"},
        status=401,
        json=ocs({}, 401),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "url": "https://cloud.example.com",
            "auth_method": "account",
            "verify_ssl": True,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "alice", "password": "wrong"},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_app_not_installed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A missing app shows a dedicated error."""
    aioclient_mock.get(
        f"{API}/lists",
        params={"format": "json"},
        status=404,
        text="<html>404</html>",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "url": "https://cloud.example.com",
            "auth_method": "account",
            "verify_ssl": True,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "alice", "password": "app-password"},
    )
    assert result["errors"] == {"base": "app_not_installed"}


async def test_user_flow_share_link(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Share link flow accepts a full URL and creates a share entry."""
    mock_ocs(
        aioclient_mock,
        "GET",
        "/public/abc123token",
        {"title": "Party", "permission": 1},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "url": "https://cloud.example.com",
            "auth_method": AUTH_METHOD_SHARE_LINK,
            "verify_ssl": True,
        },
    )
    assert result["step_id"] == "share"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"token": "https://cloud.example.com/s/abc123token"},
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Party"
    assert result["data"]["auth_method"] == AUTH_METHOD_SHARE_LINK
    assert result["data"]["token"] == "abc123token"


async def test_user_flow_share_password_required(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A protected share asks for the password."""
    aioclient_mock.get(
        f"{API}/public/abc123token",
        params={"format": "json"},
        status=403,
        json=ocs({"passwordRequired": True}, 403),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "url": "https://cloud.example.com",
            "auth_method": AUTH_METHOD_SHARE_LINK,
            "verify_ssl": True,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": "abc123token"}
    )
    assert result["errors"] == {"base": "share_password_required"}

    # Now retry with the password, server accepts it
    aioclient_mock.clear_requests()
    mock_ocs(
        aioclient_mock,
        "GET",
        "/public/abc123token",
        {"title": "Party", "permission": 1},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": "abc123token", "share_password": "hunter2"}
    )
    assert result["type"] == "create_entry"
    assert result["data"]["share_password"] == "hunter2"


async def test_duplicate_account_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Configuring the same account twice aborts."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alice @ cloud.example.com",
        data={
            "url": "https://cloud.example.com",
            "auth_method": "account",
            "username": "alice",
            "password": "x",
            "verify_ssl": True,
        },
        source=config_entries.SOURCE_USER,
        unique_id="https://cloud.example.com|alice",
    )
    entry.add_to_hass(hass)

    _register_lists(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "url": "https://cloud.example.com",
            "auth_method": "account",
            "verify_ssl": True,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "Alice", "password": "app-password"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_options_flow_share(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The options flow of a share entry stores the polling interval."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Party",
        data=SHARE_ENTRY_DATA,
        source=config_entries.SOURCE_USER,
        unique_id="https://cloud.example.com|share|abc123token",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 60}
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {"scan_interval": 60}


async def test_options_flow_account_lists(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The options flow of an account entry lets the user pick the lists."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .const_test import ACCOUNT_ENTRY_DATA

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alice @ cloud.example.com",
        data=ACCOUNT_ENTRY_DATA,
        source=config_entries.SOURCE_USER,
        unique_id="https://cloud.example.com|alice",
    )
    entry.add_to_hass(hass)
    _register_lists(aioclient_mock)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"list_ids": ["1"], "scan_interval": 45}
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {"list_ids": ["1"], "scan_interval": 45}
