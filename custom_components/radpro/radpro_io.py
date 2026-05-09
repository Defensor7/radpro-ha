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

    def query(self, request: str, retries: int = 1) -> str | None:
        """
        Returns value (string) or None.
        - flush any stale data from previous (possibly timed-out) commands
        - send request + '\n'
        - small pause so the device can begin its reply
        - read one line
        - if startswith 'OK' return response[3:]
        Retries once on transport errors or empty/garbled responses to avoid
        a single stray reply taking down all sensors with UpdateFailed.
        """
        if self.serial is None:
            self.open()

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                assert self.serial is not None
                # Drop anything left over from a previously timed-out command,
                # otherwise readline() below would return that stale line and
                # we'd associate it with the wrong request ("response shifting").
                self.serial.reset_input_buffer()
                _LOGGER.debug("TX: %s", request)
                self.serial.write(request.encode("ascii") + b"\n")
                self.serial.flush()
                # Give the device a moment to start responding before we block on read.
                time.sleep(0.02)
                response_bytes = self.serial.readline()
            except Exception as e:
                last_err = e
                _LOGGER.debug("Serial error (attempt %d): %s", attempt + 1, e)
                # Reopen on the next attempt
                try:
                    self.close()
                except Exception:
                    pass
                self.serial = None
                if attempt < retries:
                    self.open()
                continue

            if not response_bytes:
                _LOGGER.debug("RX: (no response, attempt %d)", attempt + 1)
                continue

            response = response_bytes.decode("ascii", errors="ignore").strip()
            _LOGGER.debug("RX: %s", response)

            if response.startswith("OK"):
                return response[3:].strip()

            _LOGGER.debug("Unexpected response (attempt %d): %s", attempt + 1, response)

        if last_err is not None:
            raise RadProIOError(str(last_err)) from last_err
        return None

    def get(self, key: str) -> str | None:
        return self.query(f"GET {key}")

    def set(self, key: str, value: str | int | float) -> str | None:
        return self.query(f"SET {key} {value}")
