"""Config flow for RadPro integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_PORT,
    CONF_BAUDRATE,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_BAUDRATE,
    DEFAULT_SCAN_INTERVAL,
)
from .radpro_io import RadProIO

_LOGGER = logging.getLogger(__name__)


def _get_serial_ports() -> list[str]:
    """Get available serial ports (blocking)."""
    import glob
    import sys

    ports = []

    # Try pyserial's list_ports first (works on all platforms)
    try:
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
    except ImportError:
        pass

    # Fallback: glob for Unix-like systems
    if not ports and sys.platform != "win32":
        # Linux
        ports += sorted(glob.glob("/dev/ttyACM*"))
        ports += sorted(glob.glob("/dev/ttyUSB*"))
        # macOS
        ports += sorted(glob.glob("/dev/cu.usbmodem*"))
        ports += sorted(glob.glob("/dev/cu.usbserial*"))
        ports += sorted(glob.glob("/dev/cu.SLAB_USBtoUART*"))
        ports += sorted(glob.glob("/dev/cu.wchusbserial*"))

    # Remove duplicates while preserving order
    seen = set()
    result = []
    for p in ports:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _test_connection(port: str, baudrate: int) -> tuple[bool, str | None]:
    """Test connection to RadPro device. Returns (success, device_id or error)."""
    try:
        io = RadProIO(port, baudrate=baudrate)
        io.open()
        device_id = io.get("deviceId")
        io.close()

        if device_id:
            return True, device_id
        return False, "No response from device"
    except Exception as err:
        return False, str(err)


def _auto_detect_radpro(ports: list[str], baudrate: int) -> tuple[str | None, str | None]:
    """Try to find RadPro device on available ports. Returns (port, device_id) or (None, error)."""
    if not ports:
        return None, "No serial ports available"

    for port in ports:
        try:
            io = RadProIO(port, baudrate=baudrate)
            io.open()
            device_id = io.get("deviceId")
            io.close()

            if device_id:
                return port, device_id
        except Exception:
            continue

    return None, "RadPro device not found on any port"


class RadProConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for RadPro."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_ports: list[str] = []
        self._device_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        # Get available ports
        self._discovered_ports = await self.hass.async_add_executor_job(
            _get_serial_ports
        )

        if user_input is not None:
            port = user_input[CONF_PORT]
            baudrate = int(user_input.get(CONF_BAUDRATE, DEFAULT_BAUDRATE))

            # Handle auto-detection
            if port.lower() == "auto":
                detected_port, result = await self.hass.async_add_executor_job(
                    _auto_detect_radpro, self._discovered_ports, baudrate
                )
                if detected_port:
                    port = detected_port
                    self._device_id = result
                    success = True
                else:
                    _LOGGER.error("Auto-detection failed: %s", result)
                    errors["base"] = "cannot_connect"
                    success = False
            else:
                # Test specific port
                success, result = await self.hass.async_add_executor_job(
                    _test_connection, port, baudrate
                )
                if success:
                    self._device_id = result
                else:
                    _LOGGER.error("Failed to connect to RadPro: %s", result)
                    errors["base"] = "cannot_connect"

            if success:
                # Check if already configured
                await self.async_set_unique_id(self._device_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"RadPro ({self._device_id})",
                    data={
                        CONF_PORT: port,
                        CONF_BAUDRATE: baudrate,
                    },
                    options={
                        CONF_SCAN_INTERVAL: user_input.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    },
                )

        # Build port options
        port_options = ["auto"] + self._discovered_ports
        if not self._discovered_ports:
            port_options = ["auto"]

        data_schema = vol.Schema(
            {
                vol.Required(CONF_PORT, default=DEFAULT_PORT): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=p, label=p)
                            for p in port_options
                        ],
                        custom_value=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_BAUDRATE, default=DEFAULT_BAUDRATE
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=9600,
                        max=921600,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=60,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "discovered_ports": ", ".join(self._discovered_ports) or "none"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return RadProOptionsFlow()


class RadProOptionsFlow(OptionsFlow):
    """Handle options flow for RadPro.
    
    Note: config_entry is provided by the base class as a property.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=60,
                            step=1,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            ),
        )
