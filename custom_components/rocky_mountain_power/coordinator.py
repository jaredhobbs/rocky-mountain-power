"""Coordinator to handle Rocky Mountain Power connections."""
from datetime import timedelta
import logging
from types import MappingProxyType
from typing import Any, cast

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_SELENIUM_HOST, DOMAIN
from .rocky_mountain_power import (
    AggregateType,
    CostRead,
    Forecast,
    InvalidAuth,
    RockyMountainPower,
)

_LOGGER = logging.getLogger(__name__)


class RockyMountainPowerCoordinator(DataUpdateCoordinator[dict[str, Forecast]]):
    """Handle fetching Rocky Mountain Power data, updating sensors and inserting statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_data: MappingProxyType[str, Any],
    ) -> None:
        """Initialize the data handler."""
        super().__init__(
            hass,
            _LOGGER,
            name="Rocky Mountain Power",
            # Data is updated daily on Rocky Mountain Power.
            # Refresh every 12h to be at most 12h behind.
            update_interval=timedelta(hours=12),
        )
        self.api = RockyMountainPower(
            entry_data[CONF_USERNAME],
            entry_data[CONF_PASSWORD],
            entry_data[CONF_SELENIUM_HOST],
        )

        @callback
        def _dummy_listener() -> None:
            pass

        # Force the coordinator to periodically update by registering at least one listener.
        # Needed when the _async_update_data below returns {} for utilities that don't provide
        # forecast, which results to no sensors added, no registered listeners, and thus
        # _async_update_data not periodically getting called which is needed for _insert_statistics.
        self.async_add_listener(_dummy_listener)

    async def _async_update_data(
        self,
    ) -> dict[str, Forecast]:
        """Fetch data from API endpoint."""
        try:
            # Login expires after a few minutes.
            # Given the infrequent updating (every 12h)
            # assume previous session has expired and re-login.
            await self.hass.async_add_executor_job(self.api.login)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed from err
        else:
            forecasts: list[Forecast] = await self.hass.async_add_executor_job(self.api.get_forecast)
            _LOGGER.debug("Updating sensor data with: %s", forecasts)
            # Because Rocky Mountain Power provides historical usage/cost with a delay of a couple of days
            # we need to insert data into statistics.
            await self._insert_statistics()
        finally:
            await self.hass.async_add_executor_job(self.api.end_session)
        return {forecast.account.utility_account_id: forecast for forecast in forecasts}

    async def _insert_statistics(self) -> None:
        """Insert Rocky Mountain Power statistics."""
        account = await self.hass.async_add_executor_job(self.api.get_account)
        id_prefix = "_".join(
            (
                "elec",
                account.utility_account_id,
            )
        )
        cost_statistic_id = f"{DOMAIN}:{id_prefix}_energy_cost"
        consumption_statistic_id = f"{DOMAIN}:{id_prefix}_energy_consumption"
        _LOGGER.debug(
            "Updating Statistics for %s and %s",
            cost_statistic_id,
            consumption_statistic_id,
        )

        last_stat = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, consumption_statistic_id, True, set()
        )
        if not last_stat:
            _LOGGER.debug("Updating statistic for the first time")
            cost_reads = await self._async_get_all_cost_reads()
            cost_sum = 0.0
            consumption_sum = 0.0
            last_stats_time = None
        else:
            cost_reads = await self._async_get_recent_cost_reads()
            if not cost_reads:
                _LOGGER.debug("No recent usage/cost data. Skipping update")
                return
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                cost_reads[0].start_time,
                None,
                {cost_statistic_id, consumption_statistic_id},
                "hour",
                None,
                {"sum"},
            )
            cost_sum = cast(float, stats[cost_statistic_id][0]["sum"])
            consumption_sum = cast(float, stats[consumption_statistic_id][0]["sum"])
            last_stats_time = stats[cost_statistic_id][0]["start"]

        cost_statistics = []
        consumption_statistics = []

        for cost_read in cost_reads:
            start = cost_read.start_time
            if last_stats_time is not None and start.timestamp() <= last_stats_time:
                continue
            cost_sum += cost_read.provided_cost
            consumption_sum += cost_read.consumption

            cost_statistics.append(
                StatisticData(
                    start=start, state=cost_read.provided_cost, sum=cost_sum
                )
            )
            consumption_statistics.append(
                StatisticData(
                    start=start, state=cost_read.consumption, sum=consumption_sum
                )
            )

        name_prefix = " ".join(
            (
                "Rocky Mountain Power",
                "elec",
                account.utility_account_id,
            )
        )
        cost_metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"{name_prefix} cost",
            source=DOMAIN,
            statistic_id=cost_statistic_id,
            unit_of_measurement=None,
        )
        consumption_metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"{name_prefix} consumption",
            source=DOMAIN,
            statistic_id=consumption_statistic_id,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )

        async_add_external_statistics(self.hass, cost_metadata, cost_statistics)
        async_add_external_statistics(
            self.hass, consumption_metadata, consumption_statistics
        )

    async def _async_get_all_cost_reads(self) -> list[CostRead]:
        """Get all cost reads since account activation but at different resolutions depending on age.

        - month resolution for all years (since account activation)
        - day resolution for past 2 years
        - hour resolution for past month
        """
        cost_reads = []

        cost_reads.extend(await self.hass.async_add_executor_job(self.api.get_cost_reads, AggregateType.MONTH))
        cost_reads += await self.hass.async_add_executor_job(self.api.get_cost_reads, AggregateType.DAY, 24)
        cost_reads += await self.hass.async_add_executor_job(self.api.get_cost_reads, AggregateType.HOUR, 60)
        return cost_reads

    async def _async_get_recent_cost_reads(self) -> list[CostRead]:
        """Get cost reads within the past 30 days to allow corrections in data from utilities."""
        cost_reads = []
        cost_reads += await self.hass.async_add_executor_job(self.api.get_cost_reads, AggregateType.DAY)
        cost_reads += await self.hass.async_add_executor_job(self.api.get_cost_reads, AggregateType.HOUR)
        return cost_reads
