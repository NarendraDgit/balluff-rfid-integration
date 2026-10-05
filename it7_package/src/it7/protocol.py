"""Construction and parsing of Accurate IT7 / TQM Balluff protocol messages.

Real TQM format:  <Command> <param1>;<param2>;<param3>
                    ^space     ^semicolon between params

Parsing accepts both LOADED and legacy BatchLoaded, plus both
UNKNOW_COMMAND (TQM typo) and UNKNOWN_COMMAND.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .exceptions import IT7ProtocolError
from .models import (
    BatchClosed,
    BatchLoaded,
    CloseSocket,
    MeasurementResult,
    UnknownCommand,
)


_ERROR_RE = re.compile(
    r"^(?P<command>[A-Za-z0-9_]+)_ERR(?P<number>\d+)$",
    re.IGNORECASE,
)
_LOADED_RE = re.compile(
    r"^(?:LOADED|BatchLoaded)\s+(?P<batch_id>-?\d+)$",
    re.IGNORECASE,
)
_CLOSED_RE = re.compile(r"^CLOSED\s+(?P<batch_id>-?\d+)$", re.IGNORECASE)
_UNKNOWN_RE = re.compile(
    # TQM simulator actually sends "UNKNOW_COMMAND" (single N).
    r"^UNKNOW[N]?_COMMAND\s+(?P<command>.+)$",
    re.IGNORECASE,
)
_CLOSE_SOCKET_RE = re.compile(r"^CLOSE_SOCKET$", re.IGNORECASE)
_MEASURE_OK_RE = re.compile(r"^Measure\s+OK$", re.IGNORECASE)
_MEASURE_NOK_RE = re.compile(r"^Measure\s+NOK$", re.IGNORECASE)
_SIMPLE_OK_RE = re.compile(
    r"^(?P<command>OpenBatchList|SetAuxData|SendAuxData|CloseBatch|Measure)_OK$",
    re.IGNORECASE,
)


_CANONICAL_COMMANDS = {
    "OPENBATCHLIST": "OpenBatchList",
    "SETAUXDATA": "SetAuxData",
    "SENDAUXDATA": "SetAuxData",
    "CLOSEBATCH": "CloseBatch",
    "MEASURE": "Measure",
}


def _normalize_command(command: str) -> str:
    return _CANONICAL_COMMANDS.get(command.upper(), command)


@dataclass(frozen=True)
class ErrorResponse:
    command: str
    error_number: int
    raw: str


@dataclass(frozen=True)
class SimpleResponse:
    command: str
    raw: str


ProtocolResponse = (
    BatchLoaded
    | BatchClosed
    | CloseSocket
    | UnknownCommand
    | ErrorResponse
    | SimpleResponse
    | MeasurementResult
)


class IT7Protocol:
    """Pure functions for IT7/TQM wire-format generation and parsing."""

    @staticmethod
    def _join(command: str, parts: list[str], separator: str) -> str:
        """TQM format: `<command> <param1>;<param2>;...`

        The separator controls the separator *between parameters*
        (default `;`). The command is always separated by a single space.
        """
        if not parts:
            return command
        return f"{command} {separator.join(parts)}"

    @staticmethod
    def _quote(value: str) -> str:
        if '"' in value:
            raise IT7ProtocolError(
                'Parameter values containing \'"\' are not supported until '
                "Accurate defines an escaping rule."
            )
        if "\r" in value or "\n" in value:
            raise IT7ProtocolError(
                "Parameter values containing CR/LF are not supported."
            )
        return f'"{value}"'

    @classmethod
    def open_batch_list(
        cls,
        *,
        batch_filter: str | None = None,
        program_filter: str | None = None,
        no_oper_filter: str | None = None,
        node: str | None = None,
        study: str | None = None,
        separator: str = ";",
    ) -> str:
        parts: list[str] = []
        fields = (
            ("BatchFilter", batch_filter),
            ("ProgramFilter", program_filter),
            ("NoOperFilter", no_oper_filter),
            ("Node", node),
            ("Study", study),
        )
        for name, value in fields:
            if value is not None:
                parts.append(f"{name}={cls._quote(value)}")
        return cls._join("OpenBatchList", parts, separator)

    @classmethod
    def set_aux_data(
        cls,
        values: dict[str, str],
        *,
        separator: str = ";",
    ) -> str:
        parts: list[str] = []
        for name, value in values.items():
            if not re.fullmatch(r"Aux\d+", name, re.IGNORECASE):
                raise IT7ProtocolError(
                    f"Invalid auxiliary field {name!r}; expected Aux1, Aux2, ..."
                )
            parts.append(f"{name}={cls._quote(value)}")
        return cls._join("SetAuxData", parts, separator)

    @staticmethod
    def close_batch(batch_id: int | None = None) -> str:
        if batch_id is None:
            return "CloseBatch"
        return f"CloseBatch {batch_id}"

    @staticmethod
    def measurement_ack() -> str:
        return "Measure_OK"

    @staticmethod
    def parse(message: str) -> ProtocolResponse:
        raw = message.strip()
        if not raw:
            raise IT7ProtocolError("Received an empty IT7 message")

        if _MEASURE_OK_RE.fullmatch(raw):
            return MeasurementResult.OK
        if _MEASURE_NOK_RE.fullmatch(raw):
            return MeasurementResult.NOK

        if _CLOSE_SOCKET_RE.fullmatch(raw):
            return CloseSocket(raw=raw)

        match = _LOADED_RE.fullmatch(raw)
        if match:
            return BatchLoaded(int(match.group("batch_id")))

        match = _CLOSED_RE.fullmatch(raw)
        if match:
            return BatchClosed(int(match.group("batch_id")))

        match = _UNKNOWN_RE.fullmatch(raw)
        if match:
            return UnknownCommand(
                command=match.group("command").strip(),
                raw=raw,
            )

        match = _ERROR_RE.fullmatch(raw)
        if match:
            return ErrorResponse(
                command=_normalize_command(match.group("command")),
                error_number=int(match.group("number")),
                raw=raw,
            )

        match = _SIMPLE_OK_RE.fullmatch(raw)
        if match:
            return SimpleResponse(
                command=_normalize_command(match.group("command")),
                raw=raw,
            )

        raise IT7ProtocolError(f"Unknown IT7 message: {raw!r}")