"""Simulated TQM Balluff UDP server.

This is a Python re-implementation of the GUI tool provided by the IT7/TQM
software team (see TQMBalluffSimulator.docx). It implements:

* Case-insensitive command handling.
* OpenBatchList -> OpenBatchList_OK then (after delay) "LOADED <id>".
* SetAuxData -> SetAuxData_OK.
* CloseBatch -> CloseBatch_OK then (after delay) "CLOSED <id>".
* Unknown commands -> "UNKNOWN_COMMAND <original>".
* On stop -> "CLOSE_SOCKET".
* Validation errors:
    OpenBatchList_ERR1 (syntax), _ERR2 (Study not 0..4), _ERR3 (Node missing)
    SetAuxData_ERR1 (syntax), _ERR2 (Aux>N), _ERR3 (value too long), _ERR4 (no params)
    CloseBatch_ERR1 (syntax), _ERR2 (BatchID non-numeric/<0), _ERR3 (unknown batch)

Optional fault injection is layered on top so the existing E2E tests continue
to work.
"""

from __future__ import annotations

import logging
import re
import socket
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .models import IT7Config

logger = logging.getLogger(__name__)


STUDY_VALUES = {0: "CP", 1: "PP", 2: "CM", 3: "CP100", 4: "MSA"}

DEFAULT_BATCH_ID = 65
DEFAULT_AUX_MAX_COUNT = 20
DEFAULT_AUX_MAX_LENGTH = 40
DEFAULT_NOT_FOUND_NODE = "NotFound"
DEFAULT_NOT_FOUND_BATCH_ID = 99


class Fault(str, Enum):
    NONE = "none"
    DROP_OPEN_ACK = "drop-open-ack"
    DROP_BATCH_LOADED = "drop-batch-loaded"     # legacy: drops LOADED
    DROP_LOADED = "drop-loaded"                 # same as above, new name
    DROP_AUX_ACK = "drop-aux-ack"
    DROP_MEASURE_RESULT = "drop-measure-result"
    DROP_CLOSE_ACK = "drop-close-ack"
    DROP_CLOSED = "drop-closed"
    OPEN_ERROR = "open-error"
    AUX_ERROR = "aux-error"
    CLOSE_ERROR = "close-error"
    MALFORMED_RESPONSE = "malformed-response"
    UNEXPECTED_RESPONSE = "unexpected-response"
    WRONG_BATCH_ID = "wrong-batch-id"
    DUPLICATE_BATCH_LOADED = "duplicate-batch-loaded"
    DUPLICATE_OPEN_ACK = "duplicate-open-ack"


@dataclass
class SimulatedBatch:
    batch_id: int = DEFAULT_BATCH_ID
    name: str = "TEST"


@dataclass
class SimulatorState:
    received_commands: list[str] = field(default_factory=list)
    sent_messages: list[str] = field(default_factory=list)
    aux_data: dict[str, str] = field(default_factory=dict)
    active_batch_id: int | None = None
    last_open_batch_command: str | None = None
    last_measurement_result: str | None = None
    open_batch_received: bool = False
    close_batch_received: bool = False


_PARAM_RE = re.compile(r'([A-Za-z]\w*)\s*=\s*"([^"]*)"')
_AUX_NAME_RE = re.compile(r"^Aux(\d+)$", re.IGNORECASE)


class SimulatedIT7Server:
    """Python re-implementation of the TQM Balluff Simulator."""

    def __init__(
        self,
        config: IT7Config,
        *,
        batch: SimulatedBatch | None = None,
        configured_nodes: set[str] | None = None,
        known_batch_ids: set[int] | None = None,
        aux_max_count: int = DEFAULT_AUX_MAX_COUNT,
        aux_max_length: int = DEFAULT_AUX_MAX_LENGTH,
        loaded_delay: float = 5.0,
        closed_delay: float = 5.0,
        send_loaded_on_open: bool = True,
        send_closed_on_close: bool = True,
        send_close_socket_on_stop: bool = True,
        auto_result: str | None = None,
        faults: set[Fault] | None = None,
        on_command: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.batch = batch or SimulatedBatch()
        self.configured_nodes = set(configured_nodes or {"node1"})
        # Always include the configured batch id + default 65
        if known_batch_ids is None:
            known_batch_ids = {self.batch.batch_id, DEFAULT_BATCH_ID}
        self.known_batch_ids = set(known_batch_ids)
        self.aux_max_count = aux_max_count
        self.aux_max_length = aux_max_length
        self.loaded_delay = loaded_delay
        self.closed_delay = closed_delay
        self.send_loaded_on_open = send_loaded_on_open
        self.send_closed_on_close = send_closed_on_close
        self.send_close_socket_on_stop = send_close_socket_on_stop
        self.auto_result = auto_result
        self.faults = set(faults or ())
        self.on_command = on_command

        self.state = SimulatorState()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._timers: list[threading.Timer] = []
        self._auto_result_sent = False

    # ----- properties -----------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._socket is not None and not self._stop.is_set()

    @property
    def command_port(self) -> int:
        return self.config.it7_receive_port

    @property
    def balluff_port(self) -> int:
        return self.config.balluff_port

    # ----- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self.is_running:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", self.command_port))
        except OSError:
            sock.close()
            raise

        sock.settimeout(0.2)
        self._socket = sock
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="simulated-tqm", daemon=True
        )
        self._thread.start()

        logger.info(
            "Simulated TQM started: listen=0.0.0.0:%d, Balluff=%s:%d",
            self.command_port,
            self.config.host,
            self.balluff_port,
        )

    def stop(self) -> None:
        self._stop.set()

        for t in self._timers:
            t.cancel()
        self._timers.clear()

        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)

        sock, self._socket = self._socket, None
        if sock is not None:
            if self.send_close_socket_on_stop:
                try:
                    logger.info(
                        "SIM TX to %s:%d: CLOSE_SOCKET",
                        self.config.host,
                        self.balluff_port,
                    )
                    sock.sendto(
                        b"CLOSE_SOCKET",
                        (self.config.host, self.balluff_port),
                    )
                except OSError:
                    pass
            try:
                sock.close()
            except OSError:
                pass

    def __enter__(self) -> "SimulatedIT7Server":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ----- receive loop ---------------------------------------------------
    def _run(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                data, address = self._socket.recvfrom(self.config.buffer_size)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                message = data.decode(self.config.encoding).strip()
            except UnicodeDecodeError:
                logger.exception("Simulator received undecodable data")
                continue

            with self._lock:
                self.state.received_commands.append(message)

            logger.info("SIM RX from %s:%d: %s", address[0], address[1], message)

            if self.on_command is not None:
                try:
                    self.on_command(message)
                except Exception:
                    logger.exception("on_command callback failed")

            try:
                self.handle_command(message)
            except Exception:
                logger.exception("Simulator failed to handle %r", message)

    # ----- send helpers ---------------------------------------------------
    def _send(self, message: str) -> None:
        if self._socket is None:
            raise RuntimeError("Simulator is not running")

        with self._lock:
            self.state.sent_messages.append(message)

        logger.info(
            "SIM TX to %s:%d: %s",
            self.config.host,
            self.balluff_port,
            message,
        )
        self._socket.sendto(
            message.encode(self.config.encoding),
            (self.config.host, self.balluff_port),
        )

    def _schedule(self, delay: float, fn: Callable[[], None]) -> None:
        if delay <= 0:
            fn()
            return
        timer = threading.Timer(delay, fn)
        timer.daemon = True
        timer.start()
        self._timers.append(timer)

    # ----- command parsing helpers ----------------------------------------
    @classmethod
    def _parse_params(cls, remainder: str) -> dict[str, str] | None:
        """Parse `Key="value";Key2="value2"` or `Key="value" Key2="value2"`."""
        if not remainder.strip():
            return {}

        params: dict[str, str] = {}
        last_end = 0
        for m in _PARAM_RE.finditer(remainder):
            between = remainder[last_end:m.start()]
            # Between params allow only separators (semicolons / whitespace)
            if between.strip("; \t"):
                return None
            params[m.group(1)] = m.group(2)
            last_end = m.end()

        tail = remainder[last_end:]
        if tail.strip("; \t"):
            return None

        return params

    @staticmethod
    def _valid_study(value: str) -> bool:
        try:
            n = int(value)
        except ValueError:
            return False
        return 0 <= n <= 4

    # ----- command dispatch ----------------------------------------------
    def handle_command(self, message: str) -> None:
        raw = message.strip()
        if not raw:
            return

        # TQM prose uses "Command param" (space).
        # Our IT7 client sends "Command;param" (semicolon).
        # Accept both, plus any whitespace/semicolon mix.
        m = re.match(
            r"^([A-Za-z_][A-Za-z0-9_]*)[\s;]+(.*)$",
            raw,
            re.DOTALL,
        )
        if m:
            command_token = m.group(1)
            remainder = m.group(2).strip()
        else:
            # No separator: single-token command like "OpenBatchList",
            # "CloseBatch", "Measure_OK".
            command_token = raw
            remainder = ""

        cmd_upper = command_token.upper()

        if cmd_upper == "OPENBATCHLIST":
            self._handle_open_batch_list(raw, remainder)
        elif cmd_upper in {"SETAUXDATA", "SENDAUXDATA"}:
            self._handle_set_aux_data(raw, remainder)
        elif cmd_upper == "CLOSEBATCH":
            self._handle_close_batch(raw, remainder)
        elif cmd_upper == "MEASURE_OK":
            with self._lock:
                self.state.last_measurement_result = "ACK"
            logger.info("SIM: measurement result acknowledged")
        else:
            logger.info("SIM: unknown command: %s", command_token)
            self._send(f"UNKNOWN_COMMAND {command_token}")    

    # ----- OpenBatchList --------------------------------------------------
    def _handle_open_batch_list(self, raw: str, remainder: str) -> None:
        with self._lock:
            self.state.last_open_batch_command = raw
            self.state.open_batch_received = True

        # Fault injection for legacy tests
        if Fault.OPEN_ERROR in self.faults:
            self._send("OpenBatchList_ERR1")
            return
        if Fault.DROP_OPEN_ACK in self.faults:
            logger.warning("SIM FAULT: dropping OpenBatchList_OK")
            return

        params = self._parse_params(remainder)
        if params is None:
            self._send("OpenBatchList_ERR1")
            return

        study = params.get("Study")
        if study is not None and not self._valid_study(study):
            self._send("OpenBatchList_ERR2")
            return

        node = params.get("Node")
        if node is not None and node not in self.configured_nodes:
            self._send("OpenBatchList_ERR3")
            return

        if Fault.MALFORMED_RESPONSE in self.faults:
            self._send("OpenBatchList ???")
            return
        if Fault.UNEXPECTED_RESPONSE in self.faults:
            self._send("SetAuxData_OK")
            return

        self._send("OpenBatchList_OK")

        if Fault.DUPLICATE_OPEN_ACK in self.faults:
            self._send("OpenBatchList_OK")

        if self.send_loaded_on_open and Fault.DROP_LOADED not in self.faults \
                and Fault.DROP_BATCH_LOADED not in self.faults:
            self._schedule(self.loaded_delay, self.load_batch)

    # ----- SetAuxData -----------------------------------------------------
    def _handle_set_aux_data(self, raw: str, remainder: str) -> None:
        if Fault.AUX_ERROR in self.faults:
            self._send("SetAuxData_ERR1")
            return

        if not remainder.strip():
            self._send("SetAuxData_ERR4")
            return

        params = self._parse_params(remainder)
        if params is None or not params:
            self._send("SetAuxData_ERR4" if not params else "SetAuxData_ERR1")
            return

        for name, value in params.items():
            m = _AUX_NAME_RE.fullmatch(name)
            if not m:
                self._send("SetAuxData_ERR1")
                return
            idx = int(m.group(1))
            if idx < 1 or idx > self.aux_max_count:
                self._send("SetAuxData_ERR2")
                return
            if len(value) > self.aux_max_length:
                self._send("SetAuxData_ERR3")
                return

        with self._lock:
            self.state.aux_data.update(params)

        if Fault.DROP_AUX_ACK in self.faults:
            logger.warning("SIM FAULT: dropping SetAuxData_OK")
            return
        if Fault.MALFORMED_RESPONSE in self.faults:
            self._send("SetAuxData ???")
            return
        if Fault.UNEXPECTED_RESPONSE in self.faults:
            self._send("CloseBatch_OK")
            return

        self._send("SetAuxData_OK")

        # Auto-emit measurement result if requested (CLI convenience)
        if self.auto_result and not self._auto_result_sent:
            self._auto_result_sent = True
            self._schedule(
                0.05,
                lambda: self.emit_measurement_result(self.auto_result),
            )

    # ----- CloseBatch -----------------------------------------------------
    def _handle_close_batch(self, raw: str, remainder: str) -> None:
        with self._lock:
            self.state.close_batch_received = True

        if Fault.CLOSE_ERROR in self.faults:
            self._send("CloseBatch_ERR1")
            return

        stripped = remainder.strip()
        batch_id: int | None = None

        if stripped:
            try:
                batch_id = int(stripped)
            except ValueError:
                self._send("CloseBatch_ERR2")
                return
            if batch_id < 0:
                self._send("CloseBatch_ERR2")
                return

        if batch_id is not None and batch_id not in self.known_batch_ids:
            self._send("CloseBatch_ERR3")
            return

        if Fault.DROP_CLOSE_ACK in self.faults:
            logger.warning("SIM FAULT: dropping CloseBatch_OK")
            return

        with self._lock:
            self.state.active_batch_id = None

        if Fault.MALFORMED_RESPONSE in self.faults:
            self._send("CloseBatch ???")
            return
        if Fault.UNEXPECTED_RESPONSE in self.faults:
            self._send("OpenBatchList_OK")
            return

        self._send("CloseBatch_OK")

        if self.send_closed_on_close and Fault.DROP_CLOSED not in self.faults:
            closed_id = (
                batch_id if batch_id is not None else self.batch.batch_id
            )
            self._schedule(
                self.closed_delay,
                lambda cid=closed_id: self._send(f"CLOSED {cid}"),
            )

    # ----- public API -----------------------------------------------------
    def load_batch(self, batch_id: int | None = None) -> None:
        """Emit "LOADED <id>" (simulates operator selecting a batch)."""
        selected_id = self.batch.batch_id if batch_id is None else batch_id

        if Fault.WRONG_BATCH_ID in self.faults:
            selected_id += 999

        with self._lock:
            self.state.active_batch_id = selected_id

        self._send(f"LOADED {selected_id}")

        if Fault.DUPLICATE_BATCH_LOADED in self.faults:
            self._send(f"LOADED {selected_id}")

    def cancel_batch_selection(self) -> None:
        """Legacy: emit "BatchLoaded -1".

        TQM does not document this, but we keep it for backwards-compatible
        tests.
        """
        with self._lock:
            self.state.active_batch_id = None
        self._send("BatchLoaded -1")

    def emit_measurement_result(self, result: str) -> None:
        """Simulate the "Send Measure OK / NOK" buttons in the GUI."""
        normalized = result.upper()
        if normalized not in {"OK", "NOK"}:
            raise ValueError("result must be 'OK' or 'NOK'")

        with self._lock:
            self.state.last_measurement_result = normalized

        if Fault.DROP_MEASURE_RESULT in self.faults:
            logger.warning("SIM FAULT: dropping Measure %s", normalized)
            return
        if Fault.MALFORMED_RESPONSE in self.faults:
            self._send("Measure ???")
            return
        if Fault.UNEXPECTED_RESPONSE in self.faults:
            self._send("CloseBatch_OK")
            return

        self._send(f"Measure {normalized}")