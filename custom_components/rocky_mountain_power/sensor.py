"""Support for Rocky Mountain Power sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rocky_mountain_power import Forecast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RockyMountainPowerCoordinator


@dataclass
class RockyMountainPowerEntityDescriptionMixin:
    """Mixin values for required keys."""

    value_fn: Callable[[Forecast], str | float]


@dataclass
class RockyMountainPowerEntityDescription(SensorEntityDescription, RockyMountainPowerEntityDescriptionMixin):
    """Class describing Rocky Mountain Power sensors entities."""


# suggested_display_precision=0 for all sensors since
# Rocky Mountain Power provides 0 decimal points for all these.
# (for the statistics in the energy dashboard Rocky Mountain Power does provide decimal points)
ELEC_SENSORS: tuple[RockyMountainPowerEntityDescription, ...] = (
    RockyMountainPowerEntityDescription(
        key="elec_forecasted_cost",
        name="Current bill forecasted cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        suggested_unit_of_measurement="USD",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        value_fn=lambda data: data.forecasted_cost,
    ),
    RockyMountainPowerEntityDescription(
        key="elec_forecasted_cost_low",
        name="Current bill forecasted cost low",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        suggested_unit_of_measurement="USD",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        value_fn=lambda data: data.forecasted_cost_low,
    ),
    RockyMountainPowerEntityDescription(
        key="elec_forecasted_cost_high",
        name="Current bill forecasted cost high",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        suggested_unit_of_measurement="USD",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=0,
        value_fn=lambda data: data.forecasted_cost_high,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Rocky Mountain Power sensor."""

    coordinator: RockyMountainPowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[RockyMountainPowerSensor] = []
    forecasts = coordinator.data.values()
    for forecast in forecasts:
        device_id = f"{coordinator.api.utility.subdomain()}_{forecast.account.utility_account_id}"
        device = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=f"{forecast.account.meter_type.name} account {forecast.account.utility_account_id}",
            manufacturer="Rocky Mountain Power",
            model=coordinator.api.utility.name(),
            entry_type=DeviceEntryType.SERVICE,
        )
        sensors: tuple[RockyMountainPowerEntityDescription, ...] = ELEC_SENSORS
        for sensor in sensors:
            entities.append(
                RockyMountainPowerSensor(
                    coordinator,
                    sensor,
                    forecast.account.utility_account_id,
                    device,
                    device_id,
                )
            )

    async_add_entities(entities)


class RockyMountainPowerSensor(CoordinatorEntity[RockyMountainPowerCoordinator], SensorEntity):
    """Representation of an Rocky Mountain Power sensor."""

    entity_description: RockyMountainPowerEntityDescription

    def __init__(
        self,
        coordinator: RockyMountainPowerCoordinator,
        description: RockyMountainPowerEntityDescription,
        utility_account_id: str,
        device: DeviceInfo,
        device_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"
        self._attr_device_info = device
        self.utility_account_id = utility_account_id

    @property
    def native_value(self) -> StateType:
        """Return the state."""
        if self.coordinator.data is not None:
            return self.entity_description.value_fn(
                self.coordinator.data[self.utility_account_id]
            )
        return None
