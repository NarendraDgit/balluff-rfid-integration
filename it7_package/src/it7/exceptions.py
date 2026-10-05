"""Exceptions raised by the IT7 integration package."""


class IT7Exception(Exception):
    """Base exception for the package."""


class IT7ConnectionError(IT7Exception):
    """The UDP connection could not be created or used."""


class IT7TimeoutError(IT7Exception):
    """No expected response arrived before the configured timeout."""


class IT7ProtocolError(IT7Exception):
    """A malformed or unexpected IT7 protocol message was received."""


class IT7ErrorResponse(IT7Exception):
    """IT7 explicitly rejected a command with an ERRn response."""

    def __init__(self, command: str, error_number: int, raw: str):
        self.command = command
        self.error_number = error_number
        self.raw = raw
        super().__init__(
            f"{command} failed with ERR{error_number}: {raw}"
        )
