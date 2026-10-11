"""Domain services for HANC Shopping List (add_item, clear_checked, uncheck_all)."""

from __future__ import annotations

import functools
import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_AREA,
    DOMAIN,
    SERVICE_ADD_ITEM,
    SERVICE_CLEAR_CHECKED,
    SERVICE_UNCHECK_ALL,
)
from .todo import ENTITIES, HancShoppingListTodoEntity

_LOGGER = logging.getLogger(__name__)

ADD_ITEM_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("quantity"): vol.Any(
            cv.string, vol.Coerce(int), vol.Coerce(float)
        ),
        vol.Optional("unit"): cv.string,
        vol.Optional(ATTR_AREA): cv.string,
        vol.Optional("checked", default=False): cv.boolean,
    }
)

TARGETED_SCHEMA = cv.make_entity_service_schema({})


def _resolve_targets(call: ServiceCall) -> list[HancShoppingListTodoEntity]:
    """Map the target entity_ids onto live todo entities."""
    entity_ids = call.data.get("entity_id") or []
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]

    targets: list[HancShoppingListTodoEntity] = []
    for entity_id in entity_ids:
        entity = ENTITIES.get(entity_id)
        if entity is None:
            raise ServiceValidationError(
                f"{entity_id} is not a HANC Shopping List todo entity"
            )
        targets.append(entity)
    if not targets:
        raise ServiceValidationError("No target todo entity was given")
    return targets


def _normalize_quantity(value: Any) -> str | None:
    """Turn the service quantity argument into the string the API expects."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


async def _async_handle_add_item(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle hanc_shopping_list.add_item."""
    name = str(call.data["name"]).strip()
    if not name:
        raise ServiceValidationError("name must not be empty")
    quantity = _normalize_quantity(call.data.get("quantity"))

    for entity in _resolve_targets(call):
        try:
            await entity.async_service_add_item(
                name=name,
                quantity=quantity,
                unit=call.data.get("unit"),
                area=call.data.get(ATTR_AREA),
                checked=bool(call.data.get("checked", False)),
            )
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(f"Could not add item: {err}") from err


async def _async_handle_clear_checked(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle hanc_shopping_list.clear_checked."""
    for entity in _resolve_targets(call):
        if entity.coordinator.is_share_link:
            raise ServiceValidationError(
                "clear_checked requires an account connection, "
                "share links only allow deleting single items"
            )
        try:
            await entity.coordinator.async_clear_checked(entity.list_key)
        except Exception as err:
            raise HomeAssistantError(f"Could not clear checked items: {err}") from err


async def _async_handle_uncheck_all(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle hanc_shopping_list.uncheck_all."""
    for entity in _resolve_targets(call):
        if entity.coordinator.is_share_link:
            raise ServiceValidationError(
                "uncheck_all requires an account connection, "
                "share links only allow changing single items"
            )
        try:
            await entity.coordinator.async_uncheck_all(entity.list_key)
        except Exception as err:
            raise HomeAssistantError(f"Could not uncheck items: {err}") from err


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_ADD_ITEM):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_ITEM,
        functools.partial(_async_handle_add_item, hass),
        schema=ADD_ITEM_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_CHECKED,
        functools.partial(_async_handle_clear_checked, hass),
        schema=TARGETED_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNCHECK_ALL,
        functools.partial(_async_handle_uncheck_all, hass),
        schema=TARGETED_SCHEMA,
    )
