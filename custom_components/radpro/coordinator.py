from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .radpro_io import RadProIO, RadProIOError

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """RadPro device information parsed from GET deviceId response.
    
    Response format: OK [hardware-id];[software-id];[device-id]
    Example: OK FS2011 (STM32F051C8);Rad Pro 2.0/en;b5706d937087f975b5812810
    """
    hardware_id: str | None = None      # e.g., "FS2011 (STM32F051C8)"
    software_id: str | None = None      # e.g., "Rad Pro 2.0/en"
    device_id: str | None = None        # e.g., "b5706d937087f975b5812810"
    battery_voltage: float | None = None

    @property
    def model(self) -> str | None:
        """Extract model name from hardware_id."""
        if self.hardware_id:
            # "FS2011 (STM32F051C8)" -> "FS2011"
            return self.hardware_id.split("(")[0].strip()
        return None

    @property
    def sw_version(self) -> str | None:
        """Extract version from software_id."""
        if self.software_id:
            # "Rad Pro 2.0/en" -> "2.0"
            parts = self.software_id.split()
            for part in parts:
                if "/" in part:
                    return part.split("/")[0]
                if part[0].isdigit():
                    return part
        return self.software_id


class RadProCoordinator(DataUpdateCoordinator[dict]):
    """
    Polls device using RadPro protocol:
    - tubeRate -> CPM (averaged by device)
    - tubePulseCount -> lifetime pulse counter
    - tubeSensitivity -> compute µSv/h from CPM
    Uses device's built-in averaging for stable readings.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        io: RadProIO,
        interval_s: int = 2,
        sensitivity_interval_s: int = 3600,
        deviceinfo_interval_s: int = 600,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="RadPro Serial",
            update_interval=timedelta(seconds=max(1, interval_s)),
        )
        self.io = io
        self.device_info: DeviceInfo = DeviceInfo()

        self._sensitivity: float | None = None  # CPM per µSv/h

        # Intervals for periodic refresh (in update cycles)
        self._sensitivity_interval = max(1, sensitivity_interval_s // max(1, interval_s))
        self._deviceinfo_interval = max(1, deviceinfo_interval_s // max(1, interval_s))
        self._update_counter: int = 0

    async def async_setup(self) -> None:
        # Open port once
        await self.hass.async_add_executor_job(self.io.open)

        # Read static-ish info
        await self._read_device_info()

        # Read sensitivity once on startup (we can refresh later too)
        await self._read_sensitivity()

    async def _read_device_info(self) -> None:
        """Read device identification and battery voltage.
        
        deviceId response format: [hardware-id];[software-id];[device-id]
        Example: FS2011 (STM32F051C8);Rad Pro 2.0/en;b5706d937087f975b5812810
        """
        raw_device_id = await self.hass.async_add_executor_job(self.io.get, "deviceId")
        if raw_device_id:
            parts = raw_device_id.split(";")
            if len(parts) >= 3:
                self.device_info.hardware_id = parts[0].strip()
                self.device_info.software_id = parts[1].strip()
                self.device_info.device_id = parts[2].strip()
            elif len(parts) == 1:
                # Fallback: old format or just device_id
                self.device_info.device_id = raw_device_id.strip()
            _LOGGER.debug(
                "Device info: hardware=%s, software=%s, id=%s",
                self.device_info.hardware_id,
                self.device_info.software_id,
                self.device_info.device_id,
            )

        # deviceBatteryVoltage
        bv = await self.hass.async_add_executor_job(self.io.get, "deviceBatteryVoltage")
        if bv:
            try:
                self.device_info.battery_voltage = float(bv)
                _LOGGER.debug("Battery voltage: %s V/cell", bv)
            except ValueError:
                _LOGGER.warning("Invalid deviceBatteryVoltage: %s", bv)

    async def _read_sensitivity(self) -> None:
        s = await self.hass.async_add_executor_job(self.io.get, "tubeSensitivity")
        if s:
            try:
                self._sensitivity = float(s)
                _LOGGER.debug("Tube sensitivity: %s CPM/(µSv/h)", s)
            except ValueError:
                _LOGGER.warning("Invalid tubeSensitivity: %s", s)

    async def _async_update_data(self) -> dict:
        try:
            self._update_counter += 1

            # Periodically refresh sensitivity
            if self._update_counter % self._sensitivity_interval == 0:
                await self._read_sensitivity()

            # Periodically refresh device info
            if self._update_counter % self._deviceinfo_interval == 0:
                await self._read_device_info()

            data: dict = {}

            # Read tubeRate (CPM, already averaged by device)
            rate_s = await self.hass.async_add_executor_job(self.io.get, "tubeRate")
            if rate_s:
                try:
                    cpm = float(rate_s)
                    cps = cpm / 60.0
                    data["cps"] = round(cps, 3)
                    data["cpm"] = round(cpm, 1)

                    # µSv/h = CPM / sensitivity
                    if self._sensitivity and self._sensitivity > 0:
                        usvh = cpm / self._sensitivity
                        data["usvh"] = round(usvh, 3)

                    _LOGGER.debug(
                        "tubeRate: CPM=%.1f, CPS=%.3f, µSv/h=%s (sensitivity=%.1f)",
                        cpm, cps, data.get("usvh", "N/A"),
                        self._sensitivity or 0
                    )
                except ValueError:
                    _LOGGER.warning("Invalid tubeRate: %s", rate_s)

            # Read pulse count (lifetime counter)
            pc_s = await self.hass.async_add_executor_job(self.io.get, "tubePulseCount")
            if pc_s:
                try:
                    data["pulse_count"] = int(pc_s)
                    _LOGGER.debug("tubePulseCount: %s", pc_s)
                except ValueError:
                    _LOGGER.warning("Invalid tubePulseCount: %s", pc_s)

            if not data:
                raise UpdateFailed("No valid data received from device")

            return data

        except (RadProIOError, UpdateFailed) as e:
            raise UpdateFailed(str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"Unexpected error: {e}") from e

    async def async_close(self) -> None:
        await self.hass.async_add_executor_job(self.io.close)
