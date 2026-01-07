"""RadPro Home Assistant Integration."""
from __future__ import annotations

import glob
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    CONF_PORT,
    CONF_BAUDRATE,
    DEFAULT_PORT,
    DEFAULT_BAUDRATE,
    CONF_SCAN_INTERVAL,
    CONF_SENSITIVITY_INTERVAL,
    CONF_DEVICEINFO_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SENSITIVITY_INTERVAL,
    DEFAULT_DEVICEINFO_INTERVAL,
)
from .radpro_io import RadProIO
from .coordinator import RadProCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# YAML configuration schema (deprecated, but still supported)
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.string,
                vol.Optional(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): cv.positive_int,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): cv.positive_int,
                vol.Optional(CONF_SENSITIVITY_INTERVAL, default=DEFAULT_SENSITIVITY_INTERVAL): cv.positive_int,
                vol.Optional(CONF_DEVICEINFO_INTERVAL, default=DEFAULT_DEVICEINFO_INTERVAL): cv.positive_int,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def _candidate_ports() -> list[str]:
    """Get list of candidate serial ports (blocking, run in executor)."""
    ports = []
    ports += sorted(glob.glob("/dev/ttyACM*"))
    ports += sorted(glob.glob("/dev/ttyUSB*"))
    # Also check macOS serial ports
    ports += sorted(glob.glob("/dev/cu.usbmodem*"))
    ports += sorted(glob.glob("/dev/cu.usbserial*"))
    # Remove duplicates while preserving order
    seen, out = set(), []
    for p in ports:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _probe_is_radpro(io: RadProIO) -> bool:
    """Check if device is RadPro: try GET deviceId and wait for OK response."""
    try:
        v = io.get("deviceId")
        return v is not None and len(v) > 0
    except Exception:
        return False


async def _auto_detect_port(hass: HomeAssistant, baudrate: int) -> str | None:
    """Auto-detect RadPro device on serial ports."""
    # Run glob in executor to avoid blocking the event loop
    ports = await hass.async_add_executor_job(_candidate_ports)
    if not ports:
        _LOGGER.debug("No serial ports found for auto-detection")
        return None

    _LOGGER.debug("Auto-detecting RadPro on ports: %s", ports)

    for p in ports:
        io = RadProIO(p, baudrate=baudrate)
        ok = await hass.async_add_executor_job(_probe_is_radpro, io)
        try:
            await hass.async_add_executor_job(io.close)
        except Exception:
            pass
        if ok:
            return p
    return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the RadPro integration from YAML configuration."""
    hass.data.setdefault(DOMAIN, {})

    conf = config.get(DOMAIN)
    if not conf:
        return True

    # YAML configuration - show deprecation notice
    _LOGGER.warning(
        "YAML configuration for RadPro is deprecated. "
        "Please use the UI to configure the integration."
    )

    port = conf[CONF_PORT]
    baudrate = conf[CONF_BAUDRATE]
    scan_interval = conf[CONF_SCAN_INTERVAL]
    sensitivity_interval = conf[CONF_SENSITIVITY_INTERVAL]
    deviceinfo_interval = conf[CONF_DEVICEINFO_INTERVAL]

    # Auto-detect port if configured
    if str(port).lower() in ("auto", "", "none"):
        detected = await _auto_detect_port(hass, baudrate)
        if not detected:
            _LOGGER.warning(
                "RadPro device not found. Integration will not load sensors. "
                "Connect device and restart Home Assistant, or specify port manually."
            )
            return True
        port = detected
        _LOGGER.info("Auto-detected RadPro device on port: %s", port)

    try:
        await _setup_coordinator(
            hass,
            port=port,
            baudrate=baudrate,
            scan_interval=scan_interval,
            sensitivity_interval=sensitivity_interval,
            deviceinfo_interval=deviceinfo_interval,
            entry_id="yaml",
        )

        hass.async_create_task(
            discovery.async_load_platform(hass, "sensor", DOMAIN, {}, config)
        )

        _LOGGER.info("RadPro integration loaded successfully on port: %s", port)

    except Exception as err:
        _LOGGER.error("Failed to initialize RadPro on port %s: %s", port, err)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up RadPro from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    port = entry.data[CONF_PORT]
    baudrate = entry.data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    # Auto-detect if port is "auto"
    if str(port).lower() == "auto":
        detected = await _auto_detect_port(hass, baudrate)
        if not detected:
            _LOGGER.error("RadPro device not found during auto-detection")
            return False
        port = detected
        _LOGGER.info("Auto-detected RadPro device on port: %s", port)

    try:
        await _setup_coordinator(
            hass,
            port=port,
            baudrate=baudrate,
            scan_interval=scan_interval,
            sensitivity_interval=DEFAULT_SENSITIVITY_INTERVAL,
            deviceinfo_interval=DEFAULT_DEVICEINFO_INTERVAL,
            entry_id=entry.entry_id,
        )
    except Exception as err:
        _LOGGER.error("Failed to initialize RadPro: %s", err)
        return False

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def _setup_coordinator(
    hass: HomeAssistant,
    port: str,
    baudrate: int,
    scan_interval: int,
    sensitivity_interval: int,
    deviceinfo_interval: int,
    entry_id: str,
) -> RadProCoordinator:
    """Set up the coordinator."""
    io = RadProIO(port, baudrate=baudrate)
    coordinator = RadProCoordinator(
        hass,
        io,
        interval_s=scan_interval,
        sensitivity_interval_s=sensitivity_interval,
        deviceinfo_interval_s=deviceinfo_interval,
    )

    hass.data[DOMAIN][entry_id] = coordinator

    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    async def async_stop_handler(event):
        """Close serial port when Home Assistant stops."""
        _LOGGER.debug("Closing RadPro serial connection")
        await coordinator.async_close()

    hass.bus.async_listen_once("homeassistant_stop", async_stop_handler)

    return coordinator


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: RadProCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
