from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .radpro_io import RadProIO, RadProIOError

_LOGGER = logging.getLogger(__name__)

# Averaging window for stable radiation readings (seconds)
AVERAGING_WINDOW_S = 5


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
    - tubePulseCount -> compute CPS/CPM
    - tubeSensitivity -> compute uSv/h from CPM
    Mirrors radpro-tool stream_datalog calculations.
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

        self._prev_pulsecount: int | None = None
        self._prev_ts: float | None = None
        self._sensitivity: float | None = None  # CPM per uSv/h (RadPro: uSvH = cpm / sensitivity)

        # Sliding window for averaging: deque of (timestamp, delta_pulses, delta_time)
        self._samples: deque[tuple[float, int, float]] = deque()

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

            # Read pulse count
            pc_s = await self.hass.async_add_executor_job(self.io.get, "tubePulseCount")
            if not pc_s:
                raise UpdateFailed("No response for tubePulseCount")

            try:
                pulsecount = int(pc_s)
            except ValueError as e:
                raise UpdateFailed(f"Invalid tubePulseCount: {pc_s}") from e

            _LOGGER.debug("Raw tubePulseCount: %s", pc_s)

            now = self.hass.loop.time()

            data: dict = {
                "pulse_count": pulsecount,
            }

            # If we have previous sample -> add to sliding window
            if self._prev_pulsecount is not None and self._prev_ts is not None:
                dt = now - self._prev_ts
                dp = pulsecount - self._prev_pulsecount
                if dt > 0 and dp >= 0:
                    # Add sample to sliding window
                    self._samples.append((now, dp, dt))

                    # Remove old samples outside the averaging window
                    cutoff = now - AVERAGING_WINDOW_S
                    while self._samples and self._samples[0][0] < cutoff:
                        self._samples.popleft()

                    # Calculate averaged values over the window
                    total_pulses = sum(s[1] for s in self._samples)
                    total_time = sum(s[2] for s in self._samples)

                    if total_time > 0:
                        cps = total_pulses / total_time
                        cpm = total_pulses * 60.0 / total_time
                        data["cps"] = round(cps, 3)
                        data["cpm"] = round(cpm, 1)

                        # uSv/h = cpm / sensitivity (see radpro-tool)
                        if self._sensitivity and self._sensitivity > 0:
                            data["usvh"] = round(cpm / self._sensitivity, 3)

                        _LOGGER.debug(
                            "Calculated (avg %.0fs): dp=%d, dt=%.3fs, total_pulses=%d, "
                            "total_time=%.1fs, CPS=%.3f, CPM=%.1f, µSv/h=%s",
                            AVERAGING_WINDOW_S, dp, dt, total_pulses, total_time,
                            cps, cpm, data.get("usvh", "N/A")
                        )

            self._prev_pulsecount = pulsecount
            self._prev_ts = now

            return data

        except (RadProIOError, UpdateFailed) as e:
            raise UpdateFailed(str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise UpdateFailed(f"Unexpected error: {e}") from e

    async def async_close(self) -> None:
        await self.hass.async_add_executor_job(self.io.close)
