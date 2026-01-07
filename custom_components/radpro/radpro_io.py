from __future__ import annotations

import logging
import time
import serial

_LOGGER = logging.getLogger(__name__)


class RadProIOError(Exception):
    """Protocol / transport error."""


class RadProIO:
    """
    Minimal RadPro serial I/O compatible with radpro-tool.py:
    - write: ASCII + '\n'
    - read: readline
    - parse: 'OK ' prefix -> return value
    """
    def __init__(self, port: str, baudrate: int = 115200) -> None:
        self.port = port
        self.baudrate = baudrate
        self.serial: serial.Serial | None = None

    def open(self) -> None:
        self.serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=0.5,
            write_timeout=0.5,
        )

    def close(self) -> None:
        if self.serial and self.serial.is_open:
            self.serial.close()
        self.serial = None

    def query(self, request: str) -> str | None:
        """
        Returns value (string) or None.
        Mirrors radpro-tool.py behavior:
        - send request + '\n'
        - read one line
        - if startswith 'OK' return response[3:]
        """
        if self.serial is None:
            self.open()

        try:
            assert self.serial is not None
            _LOGGER.debug("TX: %s", request)
            self.serial.write(request.encode("ascii") + b"\n")
            response_bytes = self.serial.readline()
        except Exception as e:
            _LOGGER.debug("Serial error: %s", e)
            self.serial = None
            raise RadProIOError(str(e)) from e

        time.sleep(0.05)

        if not response_bytes:
            _LOGGER.debug("RX: (no response)")
            return None

        response = response_bytes.decode("ascii", errors="ignore").strip()
        _LOGGER.debug("RX: %s", response)

        if response.startswith("OK"):
            # In radpro-tool: response[3:]
            return response[3:].strip()
        return None

    def get(self, key: str) -> str | None:
        return self.query(f"GET {key}")

    def set(self, key: str, value: str | int | float) -> str | None:
        return self.query(f"SET {key} {value}")
