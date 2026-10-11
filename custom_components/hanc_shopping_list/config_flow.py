"""Config flow for HANC Shopping List."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    CannotConnectError,
    InvalidAuthError,
    NotFoundError,
    ReadOnlyShareError,
    SharePasswordRequiredError,
    ShoppingListApiClient,
    ShoppingListError,
)
from .const import (
    AUTH_METHOD_ACCOUNT,
    AUTH_METHOD_SHARE_LINK,
    AUTH_METHODS,
    CONF_AUTH_METHOD,
    CONF_LIST_IDS,
    CONF_LIST_TITLES,
    CONF_SCAN_INTERVAL,
    CONF_SHARE_PASSWORD,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SHARE_LIST_KEY,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_AUTH_METHOD, default=AUTH_METHOD_ACCOUNT): SelectSelector(
            SelectSelectorConfig(
                options=AUTH_METHODS,
                mode=SelectSelectorMode.DROPDOWN,
                translation_key=CONF_AUTH_METHOD,
            )
        ),
        vol.Required(CONF_VERIFY_SSL, default=True): BooleanSelector(),
    }
)

ACCOUNT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

SHARE_SCHEMA = vol.Schema(
    {
        vol.Required("token"): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_SHARE_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

# Shown when a share link was already parsed from a pasted URL: only the
# (optional) link password is still needed.
SHARE_PASSWORD_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SHARE_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _normalize_url(url: str) -> str:
    """Strip trailing slashes and add a scheme if missing."""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = f"https://{url}"
    return url


# A Nextcloud Shopping List share link: <base>/apps/shopping_list/s/<token>
_SHARE_LINK_RE = re.compile(
    r"^(?P<base>https?://.+?)/apps/shopping_list/s/(?P<token>[A-Za-z0-9]+)"
)


def _share_token_from_url(value: str) -> str:
    """Accept either a raw token or a full share URL."""
    value = value.strip()
    if "/s/" in value:
        return value.split("/s/", 1)[1].split("/", 1)[0].split("?", 1)[0]
    return value


def _parse_share_url(value: str) -> tuple[str, str] | None:
    """Split a share link into (nextcloud_base_url, token).

    Handles Nextcloud installs in a subdirectory ("https://host/cloud/apps/
    shopping_list/s/<token>" -> ("https://host/cloud", "<token>")). Returns
    None when the value is not a share link.
    """
    match = _SHARE_LINK_RE.match(value.strip())
    if match is None:
        return None
    return match.group("base").rstrip("/"), match.group("token")


class HancShoppingListConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration of HANC Shopping List."""

    VERSION = 1

    _url: str = ""
    _verify_ssl: bool = True
    _auth_method: str = AUTH_METHOD_ACCOUNT
    _username: str = ""
    _password: str = ""
    _token: str = ""
    _share_password: str | None = None
    _lists: list[tuple[int, str]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for server URL, auth method and TLS verification."""
        errors: dict[str, str] = {}
        if user_input is not None:
            raw_url = user_input[CONF_URL].strip()
            self._verify_ssl = user_input[CONF_VERIFY_SSL]

            # Convenience: a full share link pasted here is treated as a
            # share connection, its base URL and token extracted for the user.
            share = _parse_share_url(raw_url)
            if share is not None:
                self._url, self._token = share
                self._auth_method = AUTH_METHOD_SHARE_LINK
                _LOGGER.debug(
                    "Detected a share link in the URL field (base %s)", self._url
                )
                return await self.async_step_share()

            self._url = _normalize_url(raw_url)
            self._auth_method = user_input[CONF_AUTH_METHOD]
            if self._auth_method == AUTH_METHOD_SHARE_LINK:
                return await self.async_step_share()
            return await self.async_step_account()

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for Nextcloud username and app password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            try:
                lists = await self._async_fetch_lists(self._username, self._password)
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except NotFoundError:
                errors["base"] = "app_not_installed"
            except ShoppingListError as err:
                _LOGGER.exception("Unexpected error validating credentials")
                errors["base"] = "unknown"
                _LOGGER.debug("Details: %s", err)
            else:
                self._lists = [(lst.id, lst.title) for lst in lists]
                unique_id = f"{self._url}|{self._username.casefold()}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return await self.async_step_lists()

        return self.async_show_form(
            step_id="account", data_schema=ACCOUNT_SCHEMA, errors=errors
        )

    async def async_step_share(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a public share link (or raw token) and optional password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if "token" in user_input:
                self._token = _share_token_from_url(user_input["token"])
            self._share_password = user_input.get(CONF_SHARE_PASSWORD) or None

        if self._token:
            try:
                shared = await self._async_fetch_shared_list(
                    self._token, self._share_password
                )
            except SharePasswordRequiredError:
                errors["base"] = "share_password_required"
            except ReadOnlyShareError:
                errors["base"] = "share_read_only"
            except NotFoundError:
                errors["base"] = "share_not_found"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except ShoppingListError:
                _LOGGER.exception("Unexpected error validating the share link")
                errors["base"] = "unknown"
            else:
                if shared.permission < 1:
                    # Still allow read-only sync, just inform via title later.
                    _LOGGER.info("Share link %s is read-only", self._url)
                unique_id = f"{self._url}|share|{self._token.casefold()}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=shared.title,
                    data={
                        CONF_URL: self._url,
                        CONF_AUTH_METHOD: AUTH_METHOD_SHARE_LINK,
                        "token": self._token,
                        CONF_SHARE_PASSWORD: self._share_password,
                        CONF_VERIFY_SSL: self._verify_ssl,
                    },
                    options={},
                )

        # When the token is already known (pasted as a full link) only the
        # optional password is still of interest to the user.
        schema = SHARE_PASSWORD_SCHEMA if self._token else SHARE_SCHEMA
        return self.async_show_form(step_id="share", data_schema=schema, errors=errors)

    async def async_step_lists(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which lists to import (all by default)."""
        if user_input is not None:
            selected = user_input.get(CONF_LIST_IDS) or [
                str(list_id) for list_id, _ in self._lists
            ]
            titles = {str(list_id): title for list_id, title in self._lists if title}
            return self.async_create_entry(
                title=f"{self._username} @ {self._url.replace('https://', '')}",
                data={
                    CONF_URL: self._url,
                    CONF_AUTH_METHOD: AUTH_METHOD_ACCOUNT,
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_VERIFY_SSL: self._verify_ssl,
                    CONF_LIST_IDS: selected,
                    CONF_LIST_TITLES: titles,
                },
                options={},
            )

        options = {str(list_id): title for list_id, title in self._lists}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LIST_IDS,
                    default=list(options),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": value, "label": label}
                            for value, label in options.items()
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="lists", data_schema=schema)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start the reauth flow when credentials expired."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-enter the credentials for an existing entry."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            if reauth_entry.data.get(CONF_AUTH_METHOD) == AUTH_METHOD_SHARE_LINK:
                token = _share_token_from_url(user_input["token"])
                share_password = user_input.get(CONF_SHARE_PASSWORD) or None
                try:
                    await self._async_fetch_shared_list(token, share_password)
                except SharePasswordRequiredError:
                    errors["base"] = "share_password_required"
                except NotFoundError:
                    errors["base"] = "share_not_found"
                except CannotConnectError:
                    errors["base"] = "cannot_connect"
                except ShoppingListError:
                    errors["base"] = "unknown"
                else:
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data_updates={
                            "token": token,
                            CONF_SHARE_PASSWORD: share_password,
                        },
                    )
                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=SHARE_SCHEMA,
                    errors=errors,
                )

            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            try:
                await self._async_fetch_lists(username, password)
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except NotFoundError:
                errors["base"] = "app_not_installed"
            except ShoppingListError:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_USERNAME: username, CONF_PASSWORD: password},
                )

        schema = (
            SHARE_SCHEMA
            if reauth_entry.data.get(CONF_AUTH_METHOD) == AUTH_METHOD_SHARE_LINK
            else ACCOUNT_SCHEMA
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------
    def _make_client(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        share_password: str | None = None,
    ) -> ShoppingListApiClient:
        """Build a throwaway client for validation."""
        session = async_get_clientsession(self.hass, verify_ssl=self._verify_ssl)
        auth_method = AUTH_METHOD_SHARE_LINK if token else AUTH_METHOD_ACCOUNT
        return ShoppingListApiClient(
            session,
            self._url,
            auth_method=auth_method,
            username=username,
            password=password,
            token=token,
            share_password=share_password,
        )

    async def _async_fetch_lists(self, username: str, password: str):
        """Fetch all lists of an account (validates the credentials)."""
        client = self._make_client(username=username, password=password)
        return await client.async_get_lists()

    async def _async_fetch_shared_list(self, token: str, share_password: str | None):
        """Fetch the metadata of a share link (validates the token)."""
        client = self._make_client(token=token, share_password=share_password)
        return await client.async_get_shared_list()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Create the options flow."""
        return HancShoppingListOptionsFlow()


class HancShoppingListOptionsFlow(OptionsFlow):
    """Options: choose lists and the polling interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        entry = self.config_entry
        if entry.data.get(CONF_AUTH_METHOD) == AUTH_METHOD_SHARE_LINK:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                }
            )
            return self.async_show_form(
                step_id="init", data_schema=schema, errors=errors
            )

        titles: dict[str, str] = entry.data.get(CONF_LIST_TITLES, {})
        if not titles:
            titles = await self._async_refresh_titles(entry)
        if not titles:
            # Server unreachable — keep the previously chosen ids working.
            stored = entry.options.get(CONF_LIST_IDS, entry.data.get(CONF_LIST_IDS, []))
            titles = {str(list_id): str(list_id) for list_id in stored}

        default_selected = entry.options.get(
            CONF_LIST_IDS, entry.data.get(CONF_LIST_IDS, list(titles))
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_LIST_IDS, default=default_selected): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": value, "label": label}
                            for value, label in sorted(
                                titles.items(), key=lambda kv: kv[1]
                            )
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def _async_refresh_titles(self, entry: ConfigEntry) -> dict[str, str]:
        """Try to refresh the list titles from the server."""
        session = async_get_clientsession(
            self.hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)
        )
        client = ShoppingListApiClient(
            session,
            entry.data[CONF_URL],
            auth_method=entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_ACCOUNT),
            username=entry.data.get(CONF_USERNAME),
            password=entry.data.get(CONF_PASSWORD),
            token=entry.data.get("token"),
            share_password=entry.data.get(CONF_SHARE_PASSWORD),
        )
        try:
            if client.is_share_link:
                shared = await client.async_get_shared_list()
                return {SHARE_LIST_KEY: shared.title}
            lists = await client.async_get_lists()
        except (CannotConnectError, InvalidAuthError, NotFoundError, ShoppingListError):
            _LOGGER.debug("Could not refresh list titles", exc_info=True)
            return {}
        return {str(lst.id): lst.title for lst in lists}
