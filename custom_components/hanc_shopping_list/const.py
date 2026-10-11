"""Constants for the HANC Shopping List integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "hanc_shopping_list"

# Name of the device every list of an entry is attached to.
DEVICE_NAME = "HANC Shopping List — Nextcloud"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.TODO]

# OCS endpoint of the Nextcloud Shopping List app
API_ENDPOINT = "/ocs/v2.php/apps/shopping_list/api/v1"

CONF_AUTH_METHOD = "auth_method"
AUTH_METHOD_ACCOUNT = "account"
AUTH_METHOD_SHARE_LINK = "share_link"
AUTH_METHODS = [AUTH_METHOD_ACCOUNT, AUTH_METHOD_SHARE_LINK]

CONF_VERIFY_SSL = "verify_ssl"
CONF_LIST_IDS = "list_ids"
CONF_LIST_TITLES = "list_titles"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SHARE_PASSWORD = "share_password"
CONF_GUEST_NAME = "guest_name"

DEFAULT_SCAN_INTERVAL = 30  # seconds
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 600

DEFAULT_GUEST_NAME = "Home Assistant"

# Synthetic list key used for public share links (a share always is one list)
SHARE_LIST_KEY = "share"

ATTR_TOTAL = "total"
ATTR_CHECKED = "checked"
ATTR_ITEMS = "items"

SERVICE_ADD_ITEM = "add_item"
SERVICE_CLEAR_CHECKED = "clear_checked"
SERVICE_UNCHECK_ALL = "uncheck_all"

ATTR_AREA = "area"
