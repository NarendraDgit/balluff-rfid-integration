"""Data models for the IT7 / TQM Balluff protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MeasurementResult(str, Enum):
    OK = "OK"
    NOK = "NOK"


@dataclass(frozen=True)
class BatchLoaded:
    """TQM sends "LOADED <batchID>".

    Legacy "BatchLoaded <batchID>" is also accepted for backward
    compatibility. batch_id == -1 means the operator cancelled selection.
    """

    batch_id: int


@dataclass(frozen=True)
class BatchClosed:
    """TQM notifies that a batch was closed: "CLOSED <batchID>"."""

    batch_id: int


@dataclass(frozen=True)
class CloseSocket:
    """TQM notifies that its UDP socket is closing: "CLOSE_SOCKET"."""

    raw: str = "CLOSE_SOCKET"


@dataclass(frozen=True)
class UnknownCommand:
    """TQM did not recognize the command: "UNKNOWN_COMMAND <cmd>"."""

    command: str
    raw: str


@dataclass(frozen=True)
class AuxiliaryData:
    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class IT7Config:
    """Runtime network/protocol configuration.

    `it7_receive_port` = TQM command-reception port.
    `balluff_port` = it7_receive_port + 1 (Balluff application listens here).
    """

    host: str
    it7_receive_port: int
    timeout: float = 2.0
    retries: int = 0
    encoding: str = "utf-8"
    parameter_separator: str = ";"
    bind_host: str = "0.0.0.0"
    buffer_size: int = 65535
    max_message_bytes: int = 65507

    @property
    def balluff_port(self) -> int:
        return self.it7_receive_port + 1

    def __post_init__(self) -> None:
        if not (1 <= self.it7_receive_port <= 65534):
            raise ValueError(
                "it7_receive_port must be between 1 and 65534 "
                "because the Balluff port is IT7 port + 1"
            )
        if self.timeout <= 0:
            raise ValueError("timeout must be > 0")
        if self.retries < 0:
            raise ValueError("retries must be >= 0")
        if not self.parameter_separator:
            raise ValueError("parameter_separator must not be empty")
        if not (1 <= self.buffer_size <= 65535):
            raise ValueError("buffer_size must be between 1 and 65535")
        if not (1 <= self.max_message_bytes <= 65507):
            raise ValueError("max_message_bytes must be between 1 and 65507")