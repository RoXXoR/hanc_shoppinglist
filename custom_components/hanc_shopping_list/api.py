"""Async client for the Nextcloud Shopping List OCS API."""

from __future__ import annotations

import base64
import logging
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientSession

from .const import API_ENDPOINT, AUTH_METHOD_ACCOUNT, AUTH_METHOD_SHARE_LINK
from .models import ShopArea, ShoppingList, ShoppingListItem

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


class _NotOcs:
    """Sentinel: the response body is not an OCS envelope."""

    def __repr__(self) -> str:
        """Return a readable representation."""
        return "<not an OCS envelope>"


_NOT_OCS = _NotOcs()
type _DecodedBody = dict[str, Any] | _NotOcs


class ShoppingListError(Exception):
    """Base error for the Shopping List client."""


class CannotConnectError(ShoppingListError):
    """Raised when the Nextcloud server cannot be reached."""


class InvalidAuthError(ShoppingListError):
    """Raised when the username/app password is rejected."""


class NotFoundError(ShoppingListError):
    """Raised when a list/item/share does not exist (or the app is missing)."""


class ReadOnlyShareError(ShoppingListError):
    """Raised when a public share link is read-only but a write was attempted."""


class SharePasswordRequiredError(ShoppingListError):
    """Raised when a public share link needs a password we don't have."""


class ShoppingListApiClient:
    """Client talking to the Nextcloud Shopping List OCS API.

    Supports two modes:
    - Account mode: Basic auth with a Nextcloud app password, full API.
    - Share link mode: unauthenticated, scoped to one public share token.
    """

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        *,
        auth_method: str = AUTH_METHOD_ACCOUNT,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        share_password: str | None = None,
    ) -> None:
        """Init the client.

        ``base_url`` must be the bare Nextcloud root URL, no trailing slash.
        """
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_base = f"{self._base_url}{API_ENDPOINT}"
        self._auth_method = auth_method
        self._username = username
        self._password = password
        self._token = token
        self._share_password = share_password
        self._share_unlocked = share_password is None

    @property
    def is_share_link(self) -> bool:
        """Return True when the client talks to a public share link."""
        return self._auth_method == AUTH_METHOD_SHARE_LINK

    # ------------------------------------------------------------------
    # Low-level request handling
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        """Build request headers for an OCS API call."""
        headers = {
            "OCS-APIRequest": "true",
            "Accept": "application/json",
        }
        if self._auth_method == AUTH_METHOD_ACCOUNT and self._username is not None:
            token = base64.b64encode(
                f"{self._username}:{self._password or ''}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        retry_unlocked: bool = True,
    ) -> Any:
        """Perform one OCS request and unwrap the response envelope.

        Returns the ``ocs.data`` payload. Raises a specific subclass of
        :class:`ShoppingListError` on failure.
        """
        url = f"{self._api_base}{path}"
        query = dict(params or {})
        query["format"] = "json"

        try:
            async with self._session.request(
                method,
                url,
                headers=self._headers(),
                params=query,
                json=json,
                timeout=DEFAULT_TIMEOUT,
            ) as response:
                if response.status == 401:
                    raise self._auth_error()

                body = await self._decode_body(response)

                # Nextcloud answers deletes with "204 No Content" and an
                # empty body: treat any empty 2xx as success.
                if body is _NOT_OCS and response.status < 300:
                    return None

                if body is _NOT_OCS:
                    if response.status == 404:
                        raise NotFoundError(
                            "Not found (is the Shopping List app installed?)"
                        ) from None
                    if response.status == 403 and self.is_share_link:
                        raise self._share_access_error({})
                    raise ShoppingListError(
                        f"Non-JSON response (HTTP {response.status})"
                    )

                ocs = body.get("ocs", {})
                data = ocs.get("data")
                meta = ocs.get("meta", {})
                statuscode = int(meta.get("statuscode") or response.status)

                if statuscode == 401:
                    raise self._auth_error()
                if statuscode == 403 and self.is_share_link:
                    # Password-protected and read-only shares both answer 403
                    # but say so in the payload.
                    share_error = self._share_access_error(
                        data if isinstance(data, dict) else {}
                    )
                    if (
                        isinstance(share_error, SharePasswordRequiredError)
                        and retry_unlocked
                        and self._share_password
                        and not self._share_unlocked
                    ):
                        await self._async_unlock_share()
                        return await self._request(
                            method,
                            path,
                            params=params,
                            json=json,
                            retry_unlocked=False,
                        )
                    raise share_error
                if statuscode == 404:
                    raise NotFoundError(
                        "Not found (is the Shopping List app installed?)"
                    )
                if statuscode >= 400:
                    message = meta.get("message") or "Request failed"
                    raise ShoppingListError(f"OCS error {statuscode}: {message}")

                return data
        except (TimeoutError, ClientError, OSError) as err:
            raise CannotConnectError(f"Error talking to Nextcloud: {err}") from err

    def _auth_error(self) -> ShoppingListError:
        """Build the right error for an authentication failure."""
        if self.is_share_link:
            return NotFoundError("Share link not found")
        return InvalidAuthError("Invalid username or app password")

    @staticmethod
    def _share_access_error(payload: dict[str, Any]) -> ShoppingListError:
        """Classify a 403 answer of the public share API."""
        if payload.get("passwordRequired"):
            return SharePasswordRequiredError("This share link is password protected")
        return ReadOnlyShareError("This share link does not allow changes")

    async def _decode_body(self, response: aiohttp.ClientResponse) -> _DecodedBody:
        """Decode the response body, returning :data:`_NOT_OCS` when it is
        not an OCS envelope (e.g. an HTML error page)."""
        try:
            body: Any = await response.json(content_type=None)
        except (ValueError, aiohttp.ContentTypeError):
            return _NOT_OCS

        if not isinstance(body, dict) or "ocs" not in body:
            return _NOT_OCS
        return body

    async def _async_unlock_share(self) -> None:
        """Authenticate against a password-protected share link.

        The server marks the session as unlocked, so the session cookie
        carried by our aiohttp session makes subsequent calls work.
        """
        _LOGGER.debug("Unlocking password-protected share link")
        url = f"{self._api_base}/public/{self._token}/auth"
        try:
            async with self._session.post(
                url,
                headers=self._headers(),
                params={"format": "json"},
                json={"password": self._share_password},
                timeout=DEFAULT_TIMEOUT,
            ) as response:
                if response.status == 403:
                    raise SharePasswordRequiredError("Invalid share password")
                if response.status >= 400:
                    raise ShoppingListError(
                        f"Share authentication failed ({response.status})"
                    )
                await response.read()
        except (TimeoutError, ClientError, OSError) as err:
            raise CannotConnectError(f"Error talking to Nextcloud: {err}") from err
        self._share_unlocked = True

    # ------------------------------------------------------------------
    # Account-mode endpoints
    # ------------------------------------------------------------------
    async def async_get_lists(self) -> list[ShoppingList]:
        """Return every list visible to the account."""
        data = await self._request("GET", "/lists")
        return [ShoppingList.from_dict(entry) for entry in data or []]

    async def async_get_items(self, list_id: int) -> list[ShoppingListItem]:
        """Return all items of a list (account mode)."""
        data = await self._request("GET", f"/lists/{list_id}/items")
        return [ShoppingListItem.from_dict(entry) for entry in data or []]

    async def async_get_areas(self, list_id: int) -> list[ShopArea]:
        """Return the shop areas of a list (account mode)."""
        data = await self._request("GET", f"/lists/{list_id}/areas")
        return [ShopArea.from_dict(entry) for entry in data or []]

    async def async_create_item(
        self,
        list_id: int,
        *,
        name: str,
        quantity: str | None = None,
        unit: str | None = None,
        shop_area_id: int | None = None,
        checked: bool = False,
    ) -> ShoppingListItem:
        """Create a new item on a list (account mode)."""
        body: dict[str, Any] = {"name": name, "checked": checked}
        if quantity is not None:
            body["quantity"] = quantity
        if unit is not None:
            body["unit"] = unit
        if shop_area_id is not None:
            body["shopAreaId"] = shop_area_id
        data = await self._request("POST", f"/lists/{list_id}/items", json=body)
        return ShoppingListItem.from_dict(data)

    async def async_set_checked(
        self, list_id: int, item_id: int, checked: bool
    ) -> ShoppingListItem:
        """Check or uncheck an item (account mode)."""
        data = await self._request(
            "PUT",
            f"/lists/{list_id}/items/{item_id}/check",
            json={"checked": checked},
        )
        return ShoppingListItem.from_dict(data)

    async def async_update_item(
        self,
        list_id: int,
        item_id: int,
        *,
        name: str | None = None,
        quantity: str | None = None,
        unit: str | None = None,
    ) -> ShoppingListItem:
        """Update name/quantity/unit of an item (account mode)."""
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        body["quantity"] = quantity
        body["unit"] = unit
        data = await self._request(
            "PUT", f"/lists/{list_id}/items/{item_id}", json=body
        )
        return ShoppingListItem.from_dict(data)

    async def async_reorder_items(self, list_id: int, sorted_ids: list[int]) -> None:
        """Persist a new item order (account mode)."""
        await self._request(
            "POST",
            f"/lists/{list_id}/items/reorder",
            json={"sortedIds": sorted_ids},
        )

    async def async_delete_item(self, list_id: int, item_id: int) -> None:
        """Delete an item (account mode)."""
        await self._request("DELETE", f"/lists/{list_id}/items/{item_id}")

    async def async_clear_checked(self, list_id: int) -> None:
        """Delete all checked items (account mode)."""
        await self._request("DELETE", f"/lists/{list_id}/items/checked")

    async def async_uncheck_all(self, list_id: int) -> None:
        """Uncheck every item (account mode)."""
        await self._request("POST", f"/lists/{list_id}/items/uncheck-all")

    # ------------------------------------------------------------------
    # Share-link mode endpoints
    # ------------------------------------------------------------------
    async def async_get_shared_list(self) -> ShoppingList:
        """Return list metadata for a public share link."""
        data = await self._request("GET", f"/public/{self._token}")
        title = str((data or {}).get("title") or "Shopping list")
        permission = int((data or {}).get("permission") or 0)
        return ShoppingList(id=0, title=title, permission=permission, is_owner=False)

    async def async_get_shared_items(self) -> list[ShoppingListItem]:
        """Return all items of a public share link."""
        data = await self._request("GET", f"/public/{self._token}/items")
        return [ShoppingListItem.from_dict(entry) for entry in data or []]

    async def async_get_shared_areas(self) -> list[ShopArea]:
        """Return the shop areas of a public share link."""
        data = await self._request("GET", f"/public/{self._token}/areas")
        return [ShopArea.from_dict(entry) for entry in data or []]

    async def async_create_shared_item(
        self,
        *,
        name: str,
        quantity: str | None = None,
        unit: str | None = None,
        shop_area_id: int | None = None,
        guest_name: str | None = None,
    ) -> ShoppingListItem:
        """Create an item on a public share link."""
        body: dict[str, Any] = {"name": name}
        if quantity is not None:
            body["quantity"] = quantity
        if unit is not None:
            body["unit"] = unit
        if shop_area_id is not None:
            body["shopAreaId"] = shop_area_id
        if guest_name:
            body["guestName"] = guest_name
        data = await self._request("POST", f"/public/{self._token}/items", json=body)
        return ShoppingListItem.from_dict(data)

    async def async_set_shared_checked(
        self,
        item_id: int,
        checked: bool,
        *,
        guest_name: str | None = None,
    ) -> ShoppingListItem:
        """Check or uncheck an item on a public share link."""
        body: dict[str, Any] = {"checked": checked}
        if guest_name:
            body["guestName"] = guest_name
        data = await self._request(
            "PUT",
            f"/public/{self._token}/items/{item_id}/check",
            json=body,
        )
        return ShoppingListItem.from_dict(data)

    async def async_update_shared_item(
        self,
        item_id: int,
        *,
        name: str | None = None,
        quantity: str | None = None,
        unit: str | None = None,
    ) -> ShoppingListItem:
        """Update name/quantity/unit of an item on a public share link."""
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        body["quantity"] = quantity
        body["unit"] = unit
        data = await self._request(
            "PUT", f"/public/{self._token}/items/{item_id}", json=body
        )
        return ShoppingListItem.from_dict(data)

    async def async_reorder_shared_items(self, sorted_ids: list[int]) -> None:
        """Persist a new item order on a public share link."""
        await self._request(
            "POST",
            f"/public/{self._token}/items/reorder",
            json={"sortedIds": sorted_ids},
        )

    async def async_delete_shared_item(self, item_id: int) -> None:
        """Delete an item from a public share link."""
        await self._request("DELETE", f"/public/{self._token}/items/{item_id}")
