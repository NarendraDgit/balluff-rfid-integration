"""UDP transport for IT7.

One UDP socket is bound locally to IT7 receive port + 1. This lets the same
socket receive IT7's responses and send messages to IT7, with a predictable
source port. The port behavior should be confirmed against the actual IT7
configuration.
"""

from __future__ import annotations

import logging
import socket
from contextlib import suppress

from .exceptions import IT7ConnectionError, IT7TimeoutError
from .models import IT7Config

logger = logging.getLogger(__name__)


class IT7UdpTransport:
    def __init__(self, config: IT7Config):
        self.config = config
        self._socket: socket.socket | None = None

    @property
    def is_open(self) -> bool:
        return self._socket is not None

    def open(self) -> None:
        if self._socket is not None:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.config.bind_host, self.config.balluff_port))
        except OSError as exc:
            sock.close()
            raise IT7ConnectionError(
                f"Could not bind UDP port {self.config.balluff_port}: {exc}"
            ) from exc

        self._socket = sock
        logger.info(
            "IT7 UDP transport opened: local=%s:%d -> IT7=%s:%d",
            self.config.bind_host,
            self.config.balluff_port,
            self.config.host,
            self.config.it7_receive_port,
        )

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            with suppress(OSError):
                sock.close()
        logger.info("IT7 UDP transport closed")

    def __enter__(self) -> "IT7UdpTransport":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def send(self, message: str) -> None:
        if self._socket is None:
            raise IT7ConnectionError("IT7 UDP transport is not open")

        try:
            data = message.encode(self.config.encoding)
        except UnicodeEncodeError as exc:
            raise IT7ConnectionError(
                f"Could not encode IT7 message using {self.config.encoding}"
            ) from exc

        if len(data) > self.config.max_message_bytes:
            raise IT7ConnectionError(
                f"IT7 message is {len(data)} bytes, exceeding configured "
                f"maximum of {self.config.max_message_bytes}"
            )

        logger.info("IT7 TX: %s", message)

        try:
            self._socket.sendto(
                data,
                (self.config.host, self.config.it7_receive_port),
            )
        except OSError as exc:
            raise IT7ConnectionError(
                f"Could not send IT7 UDP message: {exc}"
            ) from exc

    def receive(self, timeout: float | None = None) -> str:
        if self._socket is None:
            raise IT7ConnectionError("IT7 UDP transport is not open")

        timeout_value = self.config.timeout if timeout is None else timeout
        self._socket.settimeout(timeout_value)

        try:
            data, address = self._socket.recvfrom(self.config.buffer_size)
        except socket.timeout as exc:
            raise IT7TimeoutError(
                f"No IT7 UDP message received within {timeout_value:.3f}s"
            ) from exc
        except OSError as exc:
            raise IT7ConnectionError(
                f"Could not receive IT7 UDP message: {exc}"
            ) from exc

        try:
            message = data.decode(self.config.encoding)
        except UnicodeDecodeError as exc:
            raise IT7ConnectionError(
                f"Could not decode IT7 UDP message using {self.config.encoding}"
            ) from exc

        logger.info(
            "IT7 RX from %s:%d: %s",
            address[0],
            address[1],
            message.strip(),
        )
        return message.strip()
