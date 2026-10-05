"""High-level measurement-session state machine."""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Callable

from .client import IT7Client
from .models import BatchLoaded, MeasurementResult

logger = logging.getLogger(__name__)


class SessionState(Enum):
    IDLE = auto()
    OPENING_BATCH_LIST = auto()
    WAITING_FOR_BATCH = auto()
    SENDING_AUX_DATA = auto()
    READY_FOR_MEASUREMENT = auto()
    WAITING_FOR_RESULT = auto()
    ACKNOWLEDGING_RESULT = auto()
    CLOSING_BATCH = auto()
    COMPLETE = auto()
    CANCELLED = auto()
    ERROR = auto()


class MeasurementSession:
    """Orchestrates one RFID-part measurement workflow."""

    def __init__(
        self,
        it7: IT7Client,
        *,
        batch_filter: str | None = None,
        program_filter: str | None = None,
        no_oper_filter: str | None = None,
        node: str | None = None,
        study: str | None = None,
        aux_data_factory: Callable[[BatchLoaded], dict[str, str]] | None = None,
    ):
        self.it7 = it7
        self.batch_filter = batch_filter
        self.program_filter = program_filter
        self.no_oper_filter = no_oper_filter
        self.node = node
        self.study = study
        self.aux_data_factory = aux_data_factory

        self.state = SessionState.IDLE
        self.batch_id: int | None = None
        self.result: MeasurementResult | None = None

    def _set_state(self, state: SessionState) -> None:
        logger.info("IT7 session state: %s -> %s", self.state.name, state.name)
        self.state = state

    def start(self) -> BatchLoaded:
        try:
            self._set_state(SessionState.OPENING_BATCH_LIST)

            self.it7.open_batch_list(
                batch_filter=self.batch_filter,
                program_filter=self.program_filter,
                no_oper_filter=self.no_oper_filter,
                node=self.node,
                study=self.study,
            )

            self._set_state(SessionState.WAITING_FOR_BATCH)
            batch = self.it7.wait_for_batch_loaded()

            if batch.batch_id == -1:
                self._set_state(SessionState.CANCELLED)
                return batch

            self.batch_id = batch.batch_id
            self._set_state(SessionState.SENDING_AUX_DATA)

            if self.aux_data_factory is not None:
                aux_data = self.aux_data_factory(batch)
                if aux_data:
                    self.it7.set_aux_data(aux_data)

            self._set_state(SessionState.READY_FOR_MEASUREMENT)
            return batch

        except Exception:
            self._set_state(SessionState.ERROR)
            raise

    def wait_for_result_and_close(
        self,
        *,
        wait_for_closed: bool = False,
    ) -> MeasurementResult:
        if self.state != SessionState.READY_FOR_MEASUREMENT:
            raise RuntimeError(
                f"Cannot wait for result in state {self.state.name}"
            )

        try:
            self._set_state(SessionState.WAITING_FOR_RESULT)
            self.result = self.it7.wait_for_measurement_result()

            self._set_state(SessionState.ACKNOWLEDGING_RESULT)
            self.it7.acknowledge_measurement()

            self._set_state(SessionState.CLOSING_BATCH)
            self.it7.close_batch(self.batch_id)

            if wait_for_closed:
                self.it7.wait_for_batch_closed()

            self._set_state(SessionState.COMPLETE)
            return self.result

        except Exception:
            self._set_state(SessionState.ERROR)
            raise