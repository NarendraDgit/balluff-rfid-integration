"""End-to-end test harness against the TQM-compatible simulator."""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .client import IT7Client
from .exceptions import IT7ErrorResponse, IT7ProtocolError, IT7TimeoutError
from .models import BatchLoaded, IT7Config, MeasurementResult
from .simulator import Fault, SimulatedBatch, SimulatedIT7Server
from .state_machine import MeasurementSession, SessionState

logger = logging.getLogger(__name__)


class Scenario(str, Enum):
    OK = "ok"
    NOK = "nok"
    CANCEL = "cancel"

    TIMEOUT_OPEN = "timeout-open"
    TIMEOUT_BATCH = "timeout-batch"
    TIMEOUT_AUX = "timeout-aux"
    TIMEOUT_MEASURE = "timeout-measure"
    TIMEOUT_CLOSE = "timeout-close"

    OPEN_ERROR = "open-error"
    AUX_ERROR = "aux-error"
    CLOSE_ERROR = "close-error"

    MALFORMED_OPEN = "malformed-open"
    MALFORMED_MEASURE = "malformed-measure"
    UNEXPECTED_OPEN = "unexpected-open"
    UNEXPECTED_MEASURE = "unexpected-measure"

    WRONG_BATCH = "wrong-batch"
    DUPLICATE_OPEN_ACK = "duplicate-open-ack"
    DUPLICATE_BATCH = "duplicate-batch"


@dataclass(frozen=True)
class SimulatedRFIDTag:
    serial_number: str
    batch: str
    line: str


@dataclass
class E2ETestResult:
    scenario: Scenario
    passed: bool
    expected: str
    actual: str
    state: SessionState
    batch_id: int | None = None
    measurement_result: MeasurementResult | None = None
    sent_messages: list[str] = field(default_factory=list)
    received_commands: list[str] = field(default_factory=list)
    aux_data: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def find_free_udp_port() -> int:
    for _ in range(100):
        first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            first.bind(("127.0.0.1", 0))
            port = first.getsockname()[1]
            second.bind(("127.0.0.1", port + 1))
            return port
        except OSError:
            first.close()
            second.close()
            continue
        finally:
            try:
                first.close()
            except OSError:
                pass
            try:
                second.close()
            except OSError:
                pass
    raise RuntimeError("Could not find two consecutive free UDP ports")


class EndToEndHarness:
    def __init__(
        self,
        *,
        batch_id: int = 65,
        timeout: float = 0.2,
        retries: int = 0,
        parameter_separator: str = ";",
    ):
        self.batch_id = batch_id
        self.timeout = timeout
        self.retries = retries
        self.parameter_separator = parameter_separator

    def _config(self, port: int) -> IT7Config:
        return IT7Config(
            host="127.0.0.1",
            it7_receive_port=port,
            timeout=self.timeout,
            retries=self.retries,
            parameter_separator=self.parameter_separator,
        )

    @staticmethod
    def _aux(tag: SimulatedRFIDTag) -> dict[str, str]:
        return {"Aux2": tag.serial_number, "Aux4": tag.line}

    def _client_session(self, config, tag):
        client = IT7Client(config)
        session = MeasurementSession(
            client,
            batch_filter=tag.batch,
            aux_data_factory=lambda _batch: self._aux(tag),
        )
        return client, session

    def _result(
        self,
        *,
        scenario, passed, expected, actual,
        session, server, error=None,
    ) -> E2ETestResult:
        return E2ETestResult(
            scenario=scenario,
            passed=passed,
            expected=expected,
            actual=actual,
            state=session.state,
            batch_id=session.batch_id,
            measurement_result=session.result,
            sent_messages=list(server.state.sent_messages),
            received_commands=list(server.state.received_commands),
            aux_data=dict(server.state.aux_data),
            error=error,
        )

    def run(self, scenario, *, tag=None) -> E2ETestResult:
        tag = tag or SimulatedRFIDTag(
            serial_number="SERIAL-XYZ", batch="TEST", line="LINE-3",
        )
        if scenario in {Scenario.OK, Scenario.NOK, Scenario.CANCEL}:
            return self._run_happy_or_cancel(scenario, tag)

        fault_map = {
            Scenario.TIMEOUT_OPEN: Fault.DROP_OPEN_ACK,
            Scenario.TIMEOUT_BATCH: Fault.DROP_LOADED,
            Scenario.TIMEOUT_AUX: Fault.DROP_AUX_ACK,
            Scenario.TIMEOUT_MEASURE: Fault.DROP_MEASURE_RESULT,
            Scenario.TIMEOUT_CLOSE: Fault.DROP_CLOSE_ACK,
            Scenario.OPEN_ERROR: Fault.OPEN_ERROR,
            Scenario.AUX_ERROR: Fault.AUX_ERROR,
            Scenario.CLOSE_ERROR: Fault.CLOSE_ERROR,
            Scenario.MALFORMED_OPEN: Fault.MALFORMED_RESPONSE,
            Scenario.MALFORMED_MEASURE: Fault.MALFORMED_RESPONSE,
            Scenario.UNEXPECTED_OPEN: Fault.UNEXPECTED_RESPONSE,
            Scenario.UNEXPECTED_MEASURE: Fault.UNEXPECTED_RESPONSE,
            Scenario.WRONG_BATCH: Fault.WRONG_BATCH_ID,
            Scenario.DUPLICATE_OPEN_ACK: Fault.DUPLICATE_OPEN_ACK,
            Scenario.DUPLICATE_BATCH: Fault.DUPLICATE_BATCH_LOADED,
        }
        fault = fault_map[scenario]

        if scenario in {
            Scenario.MALFORMED_MEASURE,
            Scenario.UNEXPECTED_MEASURE,
            Scenario.CLOSE_ERROR,
            Scenario.TIMEOUT_CLOSE,
        }:
            return self._run_late_fault(scenario, tag, fault)
        return self._run_start_or_aux_fault(scenario, tag, fault)

    def _start_server(self, scenario, tag, faults):
        port = find_free_udp_port()
        config = self._config(port)
        # For E2E we want LOADED/CLOSED immediately (TQM would use 5s)
        server = SimulatedIT7Server(
            config,
            batch=SimulatedBatch(batch_id=self.batch_id, name=tag.batch),
            loaded_delay=0.0,
            closed_delay=0.0,
            send_loaded_on_open=(scenario != Scenario.CANCEL),
            faults=faults,
        )
        client, session = self._client_session(config, tag)
        server.start()
        return server, client, session

    def _run_happy_or_cancel(self, scenario, tag):
        server, client, session = self._start_server(scenario, tag, set())
        try:
            if scenario == Scenario.CANCEL:
                return self._run_cancel(server, client, session)

            batch = session.start()
            if batch.batch_id != self.batch_id:
                raise AssertionError(
                    f"Expected batch {self.batch_id}, got {batch.batch_id}"
                )
            expected = (
                MeasurementResult.OK if scenario == Scenario.OK
                else MeasurementResult.NOK
            )
            server.emit_measurement_result(expected.value)
            actual = session.wait_for_result_and_close()
            if actual != expected:
                raise AssertionError(
                    f"Expected {expected.value}, got {actual.value}"
                )
            if session.state != SessionState.COMPLETE:
                raise AssertionError(
                    f"Expected COMPLETE, got {session.state.name}"
                )
            return self._result(
                scenario=scenario, passed=True, expected="COMPLETE",
                actual=session.state.name, session=session, server=server,
            )
        except Exception as exc:
            return self._result(
                scenario=scenario, passed=False, expected="COMPLETE",
                actual=session.state.name, session=session, server=server,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            client.close()
            server.stop()

    def _run_cancel(self, server, client, session):
        outcome: dict[str, object] = {}
        finished = threading.Event()

        def run_session():
            try:
                outcome["batch"] = session.start()
            except Exception as exc:
                outcome["error"] = exc
            finally:
                finished.set()

        threading.Thread(target=run_session, daemon=True).start()
        try:
            deadline = time.monotonic() + 2.0
            while session.state != SessionState.WAITING_FOR_BATCH:
                if finished.is_set():
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Session did not reach WAITING_FOR_BATCH")
                time.sleep(0.005)
            if finished.is_set() and "error" in outcome:
                raise outcome["error"]
            server.cancel_batch_selection()
            if not finished.wait(2.0):
                raise TimeoutError("Session did not finish after cancellation")
            if "error" in outcome:
                raise outcome["error"]
            batch = outcome["batch"]
            if batch.batch_id != -1:
                raise AssertionError(f"Expected -1, got {batch.batch_id}")
            if session.state != SessionState.CANCELLED:
                raise AssertionError(
                    f"Expected CANCELLED, got {session.state.name}"
                )
            return self._result(
                scenario=Scenario.CANCEL, passed=True, expected="CANCELLED",
                actual=session.state.name, session=session, server=server,
            )
        except Exception as exc:
            return self._result(
                scenario=Scenario.CANCEL, passed=False, expected="CANCELLED",
                actual=session.state.name, session=session, server=server,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            client.close()
            server.stop()

    def _run_start_or_aux_fault(self, scenario, tag, fault):
        server, client, session = self._start_server(scenario, tag, {fault})
        expected_exception: type[Exception] = Exception
        if scenario in {
            Scenario.TIMEOUT_OPEN, Scenario.TIMEOUT_BATCH,
            Scenario.TIMEOUT_AUX,
        }:
            expected_exception = IT7TimeoutError
        elif scenario in {Scenario.OPEN_ERROR, Scenario.AUX_ERROR}:
            expected_exception = IT7ErrorResponse
        elif scenario in {
            Scenario.MALFORMED_OPEN, Scenario.UNEXPECTED_OPEN,
            Scenario.WRONG_BATCH, Scenario.DUPLICATE_OPEN_ACK,
            Scenario.DUPLICATE_BATCH,
        }:
            expected_exception = (
                IT7ProtocolError if scenario == Scenario.MALFORMED_OPEN
                else IT7TimeoutError if scenario == Scenario.UNEXPECTED_OPEN
                else AssertionError
            )
        try:
            try:
                batch = session.start()
                if scenario == Scenario.WRONG_BATCH:
                    raise AssertionError("Expected wrong BatchID detection")
                if scenario in {
                    Scenario.DUPLICATE_BATCH, Scenario.DUPLICATE_OPEN_ACK,
                }:
                    return self._result(
                        scenario=scenario, passed=True,
                        expected="READY_FOR_MEASUREMENT",
                        actual=session.state.name,
                        session=session, server=server,
                    )
                raise AssertionError("Expected session.start() to fail")
            except expected_exception:
                return self._result(
                    scenario=scenario, passed=True,
                    expected=expected_exception.__name__,
                    actual=expected_exception.__name__,
                    session=session, server=server,
                )
        except Exception as exc:
            return self._result(
                scenario=scenario, passed=False,
                expected=expected_exception.__name__,
                actual=f"{type(exc).__name__}: {exc}",
                session=session, server=server,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            client.close()
            server.stop()

    def _run_late_fault(self, scenario, tag, fault):
        server, client, session = self._start_server(scenario, tag, set())
        try:
            batch = session.start()
            if batch.batch_id != self.batch_id:
                raise AssertionError("Initial batch load failed")
            server.faults.add(fault)

            if scenario in {
                Scenario.MALFORMED_MEASURE, Scenario.UNEXPECTED_MEASURE,
            }:
                server.emit_measurement_result("OK")
                try:
                    session.wait_for_result_and_close()
                except IT7ProtocolError:
                    return self._result(
                        scenario=scenario, passed=True,
                        expected="IT7ProtocolError",
                        actual="IT7ProtocolError",
                        session=session, server=server,
                    )
                raise AssertionError(
                    "Expected malformed/unexpected measurement message to fail"
                )

            if scenario == Scenario.CLOSE_ERROR:
                server.emit_measurement_result("OK")
                try:
                    session.wait_for_result_and_close()
                except IT7ErrorResponse:
                    return self._result(
                        scenario=scenario, passed=True,
                        expected="IT7ErrorResponse",
                        actual="IT7ErrorResponse",
                        session=session, server=server,
                    )
                raise AssertionError("Expected CloseBatch_ERR1")

            if scenario == Scenario.TIMEOUT_CLOSE:
                server.emit_measurement_result("OK")
                try:
                    session.wait_for_result_and_close()
                except IT7TimeoutError:
                    return self._result(
                        scenario=scenario, passed=True,
                        expected="IT7TimeoutError",
                        actual="IT7TimeoutError",
                        session=session, server=server,
                    )
                raise AssertionError("Expected CloseBatch timeout")

            raise AssertionError(f"Unhandled late scenario {scenario}")
        except Exception as exc:
            return self._result(
                scenario=scenario, passed=False, expected="fault handled",
                actual=f"{type(exc).__name__}: {exc}",
                session=session, server=server,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            client.close()
            server.stop()

    def run_all(self, *, tag=None) -> list[E2ETestResult]:
        return [self.run(s, tag=tag) for s in Scenario]


def format_results(results: list[E2ETestResult]) -> str:
    lines = ["", "IT7/TQM END-TO-END TEST REPORT", "============================="]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(
            f"{status:4}  {r.scenario.value:22} "
            f"expected={r.expected:24} actual={r.actual}"
        )
        if r.error:
            lines.append(f"      ERROR: {r.error}")
    passed = sum(r.passed for r in results)
    lines.append("")
    lines.append(f"Passed: {passed}/{len(results)}")
    return "\n".join(lines)