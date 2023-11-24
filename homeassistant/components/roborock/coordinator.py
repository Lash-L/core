"""Roborock Coordinator."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from roborock.cloud_api import RoborockMqttClient
from roborock.command_cache import CacheableAttribute
from roborock.containers import DeviceData, HomeDataDevice, HomeDataProduct, NetworkInfo
from roborock.exceptions import RoborockException
from roborock.local_api import RoborockLocalClient
from roborock.roborock_typing import DeviceProp

from homeassistant.const import ATTR_CONNECTIONS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .models import RoborockHassDeviceInfo

SCAN_INTERVAL = timedelta(seconds=30)

_LOGGER = logging.getLogger(__name__)


class RoborockDataUpdateCoordinator(DataUpdateCoordinator[DeviceProp]):
    """Class to manage fetching data from the API."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: HomeDataDevice,
        device_networking: NetworkInfo,
        product_info: HomeDataProduct,
        cloud_api: RoborockMqttClient,
    ) -> None:
        """Initialize."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.roborock_device_info = RoborockHassDeviceInfo(
            device,
            device_networking,
            product_info,
            DeviceProp(),
        )
        device_data = DeviceData(device, product_info.model, device_networking.ip)
        self.api: RoborockLocalClient | RoborockMqttClient = RoborockLocalClient(
            device_data
        )
        self.cloud_api = cloud_api
        self.device_info = DeviceInfo(
            name=self.roborock_device_info.device.name,
            identifiers={(DOMAIN, self.roborock_device_info.device.duid)},
            manufacturer="Roborock",
            model=self.roborock_device_info.product.model,
            sw_version=self.roborock_device_info.device.fv,
        )
        self.supported_entities: set[str] = set()
        self.needed_cache_keys: list[CacheableAttribute] = []
        self.current_map: int | None = None

        if mac := self.roborock_device_info.network_info.mac:
            self.device_info[ATTR_CONNECTIONS] = {(dr.CONNECTION_NETWORK_MAC, mac)}
        self.maps: dict[str, int] = {}

    async def verify_api(self) -> None:
        """Verify that the api is reachable. If it is not, switch clients."""
        if isinstance(self.api, RoborockLocalClient):
            try:
                await self.api.ping()
            except RoborockException:
                _LOGGER.warning(
                    "Using the cloud API for device %s. This is not recommended as it can lead to rate limiting. We recommend making your vacuum accessible by your Home Assistant instance",
                    self.roborock_device_info.device.duid,
                )
                # We use the cloud api if the local api fails to connect.
                self.api = self.cloud_api
                # Right now this should never be called if the cloud api is the primary api,
                # but in the future if it is, a new else should be added.

    async def release(self) -> None:
        """Disconnect from API."""
        await self.api.async_disconnect()

    async def _recover(self) -> None:
        """When the api has been unavailable - we need to update the cache for all the values that we need."""
        await asyncio.gather(
            *(self.api.cache[key].async_value() for key in self.needed_cache_keys)
        )

    async def _update_device_prop(self) -> None:
        """Update device properties."""
        device_prop = await self.api.get_prop()
        if device_prop:
            if self.roborock_device_info.props:
                self.roborock_device_info.props.update(device_prop)
            else:
                self.roborock_device_info.props = device_prop

    async def _async_update_data(self) -> DeviceProp:
        """Update data via library."""
        try:
            await self._update_device_prop()
            self._set_current_map()
        except RoborockException as ex:
            raise UpdateFailed(ex) from ex
        if (
            self.roborock_device_info.props.consumable is None
            or self.roborock_device_info.props.clean_summary is None
            or self.roborock_device_info.props.status is None
        ):
            raise UpdateFailed("One of the needed props was None.")
        if not self.api.is_available:
            _LOGGER.debug(
                "Coordinator for device %s recovered",
                self.roborock_device_info.device.duid,
            )
            await self._recover()
            self.api.is_available = True
        return self.roborock_device_info.props

    def _set_current_map(self) -> None:
        if (
            self.roborock_device_info.props.status is not None
            and self.roborock_device_info.props.status.map_status is not None
        ):
            # The map status represents the map flag as flag * 4 + 3 -
            # so we have to invert that in order to get the map flag that we can use to set the current map.
            self.current_map = (
                self.roborock_device_info.props.status.map_status - 3
            ) // 4

    async def get_maps(self) -> None:
        """Add a map to the coordinators mapping."""
        maps = await self.api.get_multi_maps_list()
        if maps is not None and maps.map_info is not None:
            for roborock_map in maps.map_info:
                self.maps[roborock_map.name] = roborock_map.mapFlag
