"""Sensor platform for HANC Shopping List — open/checked counts per list."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import ATTR_CHECKED, ATTR_ITEMS, ATTR_TOTAL, DEVICE_NAME, DOMAIN
from .coordinator import HancShoppingListCoordinator, ListState

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the count sensors of one config entry."""
    coordinator: HancShoppingListCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HancShoppingListCountSensor(coordinator, entry, key)
        for key in coordinator.selected_list_keys
    )


class HancShoppingListCountSensor(
    CoordinatorEntity[HancShoppingListCoordinator], SensorEntity
):
    """Sensor holding the open/checked counts and full item details of a list."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:cart"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "items"

    def __init__(
        self,
        coordinator: HancShoppingListCoordinator,
        entry: ConfigEntry,
        list_key: str,
    ) -> None:
        """Initialize the count sensor."""
        super().__init__(coordinator)
        self._list_key = list_key
        self._attr_unique_id = f"{entry.unique_id}-{list_key}-counts"
        state = coordinator.get_state(list_key)
        title = state.title if state else f"List {list_key}"
        self._attr_name = f"{title} counts"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=DEVICE_NAME,
            manufacturer="Nextcloud",
            model="Shopping List",
            configuration_url=entry.data["url"],
        )
        # Suggest a clean entity id ("sensor.groceries_counts") instead of
        # one prefixed with the device name.
        self.entity_id = f"sensor.{slugify(self._attr_name)}"

    @property
    def state_value(self) -> ListState | None:
        """Return the cached state of this list."""
        return self.coordinator.get_state(self._list_key)

    @property
    def available(self) -> bool:
        """Return True when the coordinator is healthy and knows the list."""
        return super().available and self.state_value is not None

    @property
    def native_value(self) -> int | None:
        """Return the number of open (unchecked) items."""
        state = self.state_value
        if state is None:
            return None
        return sum(1 for item in state.items if not item.checked)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return counts and full item details as attributes."""
        state = self.state_value
        if state is None:
            return {}
        return {
            ATTR_TOTAL: len(state.items),
            ATTR_CHECKED: sum(1 for item in state.items if item.checked),
            ATTR_ITEMS: [
                {
                    "id": item.id,
                    "name": item.name,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "area": self.coordinator.area_name(
                        self._list_key, item.shop_area_id
                    ),
                    "checked": item.checked,
                    "checked_by": item.checked_by_name,
                    "added_by": item.added_by_name,
                }
                for item in state.items
            ],
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Keep the entity name in sync with the list title."""
        state = self.state_value
        if state is not None:
            expected = f"{state.title} counts"
            if self._attr_name != expected:
                self._attr_name = expected
        super()._handle_coordinator_update()
