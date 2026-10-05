"""High-level synchronous IT7/TQM client with asynchronous event buffering."""

from __future__ import annotations

import logging
from collections import deque

from .exceptions import IT7ErrorResponse, IT7ProtocolError, IT7TimeoutError
from .models import (
    BatchClosed,
    BatchLoaded,
    CloseSocket,
    IT7Config,
    MeasurementResult,
    UnknownCommand,
)
from .protocol import ErrorResponse, IT7Protocol, SimpleResponse
from .transport import IT7UdpTransport

logger = logging.getLogger(__name__)


class IT7Client:
    """Client implementing the Balluff <-> IT7/TQM command protocol.

    LOADED, CLOSED, Measure OK/NOK, CLOSE_SOCKET and UNKNOWN_COMMAND are
    asynchronous events. They are buffered when they arrive while the client
    is waiting for a command ACK, so an early event cannot be lost or
    accidentally treated as the ACK.
    """

    def __init__(self, config: IT7Config, *, transport=None):
        self.config = config
        self.transport = transport or IT7UdpTransport(config)
        self._events = deque()

    def open(self):
        self.transport.open()

    def close(self):
        self.transport.close()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _ensure_open(self):
        if not self.transport.is_open:
            self.open()

    def _receive_live(self):
        raw = self.transport.receive()
        return IT7Protocol.parse(raw)

    def _receive_event_or_live(self):
        if self._events:
            return self._events.popleft()
        return self._receive_live()

    @staticmethod
    def _is_async(response) -> bool:
        return isinstance(
            response,
            (
                BatchLoaded,
                BatchClosed,
                MeasurementResult,
                CloseSocket,
                UnknownCommand,
            ),
        )

    def _buffer_async(self, response) -> None:
        logger.debug("Buffering asynchronous IT7 event: %r", response)
        self._events.append(response)

    def _send_and_wait_simple(self, message: str, expected_command: str):
        self._ensure_open()

        attempts = self.config.retries + 1
        last_timeout = None

        for attempt in range(1, attempts + 1):
            if attempt > 1:
                logger.warning(
                    "Retrying IT7 command (%d/%d): %s",
                    attempt, attempts, message,
                )

            self.transport.send(message)

            while True:
                try:
                    response = self._receive_live()
                except IT7TimeoutError as exc:
                    last_timeout = exc
                    break

                if isinstance(response, ErrorResponse):
                    if response.command.lower() == expected_command.lower():
                        raise IT7ErrorResponse(
                            response.command,
                            response.error_number,
                            response.raw,
                        )
                    logger.warning(
                        "Ignoring unrelated IT7 error response: %s",
                        response.raw,
                    )
                    continue

                if isinstance(response, SimpleResponse):
                    if response.command.lower() == expected_command.lower():
                        return response
                    logger.warning(
                        "Ignoring unrelated IT7 response while waiting for "
                        "%s: %s",
                        expected_command,
                        response.raw,
                    )
                    continue

                if self._is_async(response):
                    self._buffer_async(response)
                    continue

                raise IT7ProtocolError(
                    f"Unexpected IT7 response while waiting for "
                    f"{expected_command}_OK: {response!r}"
                )

        assert last_timeout is not None
        raise last_timeout

    def open_batch_list(
        self,
        *,
        batch_filter: str | None = None,
        program_filter: str | None = None,
        no_oper_filter: str | None = None,
        node: str | None = None,
        study: str | None = None,
    ) -> None:
        message = IT7Protocol.open_batch_list(
            batch_filter=batch_filter,
            program_filter=program_filter,
            no_oper_filter=no_oper_filter,
            node=node,
            study=study,
            separator=self.config.parameter_separator,
        )
        self._send_and_wait_simple(message, "OpenBatchList")

    def wait_for_batch_loaded(self) -> BatchLoaded:
        self._ensure_open()

        while True:
            response = self._receive_event_or_live()

            if isinstance(response, BatchLoaded):
                return response

            if isinstance(response, ErrorResponse):
                raise IT7ErrorResponse(
                    response.command,
                    response.error_number,
                    response.raw,
                )

            logger.debug(
                "Ignoring IT7 message while waiting for BatchLoaded: %r",
                response,
            )

    def wait_for_batch_closed(self) -> BatchClosed:
        """Wait for the asynchronous CLOSED <id> notification."""
        self._ensure_open()

        while True:
            response = self._receive_event_or_live()

            if isinstance(response, BatchClosed):
                return response

            if isinstance(response, ErrorResponse):
                raise IT7ErrorResponse(
                    response.command,
                    response.error_number,
                    response.raw,
                )

            logger.debug(
                "Ignoring IT7 message while waiting for CLOSED: %r",
                response,
            )

    def set_aux_data(self, values: dict[str, str]) -> None:
        message = IT7Protocol.set_aux_data(
            values,
            separator=self.config.parameter_separator,
        )
        self._send_and_wait_simple(message, "SetAuxData")

    def wait_for_measurement_result(self) -> MeasurementResult:
        self._ensure_open()

        while True:
            response = self._receive_event_or_live()

            if isinstance(response, MeasurementResult):
                return response

            if isinstance(response, ErrorResponse):
                raise IT7ErrorResponse(
                    response.command,
                    response.error_number,
                    response.raw,
                )

            raise IT7ProtocolError(
                "Unexpected IT7 message while waiting for measurement "
                f"result: {response!r}"
            )

    def acknowledge_measurement(self) -> None:
        self._ensure_open()
        self.transport.send(IT7Protocol.measurement_ack())

    def close_batch(self, batch_id: int | None = None) -> None:
        message = IT7Protocol.close_batch(batch_id)
        self._send_and_wait_simple(message, "CloseBatch")