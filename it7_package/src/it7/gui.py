"""
PySide6 GUI for the IT7 / TQM Balluff client with RFID integration.

RFID integration (via rfid_service.py):
  * Latest RFID tag read is fetched over HTTP.
  * SetAuxData can use tag_uid (Aux2) and tag_user_data (Aux4).
  * When Measure OK / NOK arrives, a single byte ("byte number 8",
    i.e. 0-based index 7) is written to the tag's USER_DATA bank:
        01 = OK
        00 = NOK
"""

from __future__ import annotations

import logging
import sys

try:
    from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot
    from PySide6.QtGui import QFont, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QDoubleSpinBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
    _PYSIDE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYSIDE_AVAILABLE = False

from .client import IT7Client
from .exceptions import IT7TimeoutError
from .models import IT7Config
from .rfid_client import RfidServiceClient, DEFAULT_RFID_URL


# ---------------------------------------------------------------------------
# Result-byte configuration
#
# "byte number 8" in the tag's USER_DATA field stores the measurement result.
# We treat "byte number 8" as 1-based -> 0-based index 7.
# ---------------------------------------------------------------------------
RESULT_BYTE_INDEX = 7
RESULT_VALUE_OK = "01"
RESULT_VALUE_NOK = "00"


if _PYSIDE_AVAILABLE:

    # -----------------------------------------------------------------------
    # Logging bridge
    # -----------------------------------------------------------------------

    class _LogEmitter(QObject):
        message = Signal(str)

    class _QtLogHandler(logging.Handler):
        def __init__(self, emitter: "_LogEmitter"):
            super().__init__()
            self.emitter = emitter

        def emit(self, record: logging.LogRecord) -> None:
            try:
                self.emitter.message.emit(self.format(record))
            except Exception:
                self.handleError(record)

    # -----------------------------------------------------------------------
    # Worker
    # -----------------------------------------------------------------------

    class IT7Worker(QObject):
        sig_connected = Signal()
        sig_disconnected = Signal()
        sig_batch_loaded = Signal(int)
        sig_aux_sent = Signal()
        sig_measure_result = Signal(str)
        sig_measure_acked = Signal()
        sig_batch_closed = Signal()
        sig_error = Signal(str)
        sig_busy = Signal(bool)
        sig_state = Signal(str)

        # RFID
        sig_rfid_health = Signal(str, str)          # status, message
        sig_rfid_data = Signal(dict)                # latest tag data
        sig_rfid_write_start = Signal(str, int, str)
        sig_rfid_write_done = Signal(str, str)      # result_code, message

        def __init__(self, rfid_client: RfidServiceClient | None = None):
            super().__init__()
            self._client: IT7Client | None = None
            self._rfid = rfid_client
            self._rfid_url = DEFAULT_RFID_URL
            self._auto_rfid_write = True

        # ------------------------------------------------------------------
        # Slot helpers
        # ------------------------------------------------------------------

        @Slot(object)
        def set_rfid_client(self, rfid: object) -> None:
            self._rfid = rfid  # type: ignore[assignment]
        
        @Slot(str)
        def set_rfid_url(self, url: str) -> None:
            """Remember which RFID service URL to talk to."""
            url = (url or "").strip() or DEFAULT_RFID_URL
            if url != self._rfid_url:
                logging.getLogger("it7.rfid").info(
                    "RFID service URL set to %s", url
                )
            self._rfid_url = url
            # Force client recreation on next use
            if self._rfid is not None:
                try:
                    self._rfid.close()
                except Exception:
                    pass
                self._rfid = None

        def _get_rfid_client(self) -> RfidServiceClient:
            """Return a client for the current URL, creating it if needed."""
            if self._rfid is None:
                self._rfid = RfidServiceClient(base_url=self._rfid_url)
            return self._rfid

        @Slot(bool)
        def set_auto_rfid_write(self, enabled: bool) -> None:
            self._auto_rfid_write = bool(enabled)
            logging.getLogger("it7.rfid").info(
                "Auto RFID write %s",
                "ENABLED" if enabled else "DISABLED",
            )

        # ------------------------------------------------------------------
        # Connection
        # ------------------------------------------------------------------

        @Slot(object)
        def connect_to_it7(self, config: IT7Config) -> None:
            try:
                self._client = IT7Client(config)
                self._client.open()
                self.sig_connected.emit()
            except Exception as exc:
                self.sig_error.emit(f"Connect failed: {exc}")

        @Slot()
        def disconnect(self) -> None:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None
            self.sig_disconnected.emit()

        # ------------------------------------------------------------------
        # RFID helpers
        # ------------------------------------------------------------------

        @Slot(str)
        def rfid_check_health(self, url: str) -> None:
            rfid = RfidServiceClient(base_url=url)
            try:
                result = rfid.health()
                status = result.get("status", "ERROR")
                if status == "OK":
                    self.sig_rfid_health.emit("OK", "RFID service reachable")
                else:
                    self.sig_rfid_health.emit(
                        "ERROR", result.get("message", "Unknown")
                    )
            finally:
                rfid.close()

        @Slot(str)
        def rfid_fetch_latest(self, url: str) -> None:
            rfid = RfidServiceClient(base_url=url)
            try:
                data = rfid.get_latest()
                if data is None:
                    self.sig_error.emit("No RFID tag data available")
                    return
                self.sig_rfid_data.emit(data)
            finally:
                rfid.close()

        def _write_result_to_rfid(self, result_code: str) -> None:
            """Write 0x01 (OK) or 0x00 (NOK) to byte 8 of USER_DATA."""
            try:
                rfid = self._get_rfid_client()
            except Exception as exc:
                self.sig_rfid_write_done.emit(
                    "ERROR", f"RFID client init failed: {exc}"
                )
                return

            result_value = (
                RESULT_VALUE_OK if result_code == "OK" else RESULT_VALUE_NOK
            )
            address = RESULT_BYTE_INDEX

            self.sig_rfid_write_start.emit(result_code, address, result_value)

            write_result = rfid.write(address, result_value)

            if write_result.get("status") == "OK":
                self.sig_rfid_write_done.emit(
                    result_code,
                    f"Wrote 0x{result_value} at byte {address + 1}",
                )
            else:
                self.sig_rfid_write_done.emit(
                    "ERROR",
                    write_result.get("message", "Unknown write error"),
                )

        # ------------------------------------------------------------------
        # IT7 commands
        # ------------------------------------------------------------------

        @Slot(str, str, str)
        def open_batch(self, batch_filter: str, node: str, study: str) -> None:
            if self._client is None:
                self.sig_error.emit("Not connected to IT7")
                return
            self.sig_busy.emit(True)
            self.sig_state.emit("OPENING_BATCH_LIST")
            try:
                self._client.open_batch_list(
                    batch_filter=batch_filter or None,
                    node=node or None,
                    study=study or None,
                )
                self.sig_state.emit("WAITING_FOR_BATCH")
                batch = self._client.wait_for_batch_loaded()
                self.sig_batch_loaded.emit(batch.batch_id)
                self.sig_state.emit(
                    "CANCELLED"
                    if batch.batch_id == -1
                    else "READY_FOR_MEASUREMENT"
                )
            except IT7TimeoutError as exc:
                self.sig_error.emit(f"Timeout: {exc}")
                self.sig_state.emit("ERROR")
            except Exception as exc:
                self.sig_error.emit(f"OpenBatchList failed: {exc}")
                self.sig_state.emit("ERROR")
            finally:
                self.sig_busy.emit(False)

        @Slot(dict)
        def send_aux(self, values: dict) -> None:
            if self._client is None:
                self.sig_error.emit("Not connected to IT7")
                return
            if not values:
                self.sig_error.emit("No aux data to send")
                return
            self.sig_busy.emit(True)
            self.sig_state.emit("SENDING_AUX_DATA")
            try:
                self._client.set_aux_data(values)
                self.sig_aux_sent.emit()
                self.sig_state.emit("READY_FOR_MEASUREMENT")
            except Exception as exc:
                self.sig_error.emit(f"SetAuxData failed: {exc}")
                self.sig_state.emit("ERROR")
            finally:
                self.sig_busy.emit(False)

        @Slot()
        def wait_result(self) -> None:
            if self._client is None:
                self.sig_error.emit("Not connected to IT7")
                return
            self.sig_busy.emit(True)
            self.sig_state.emit("WAITING_FOR_RESULT")
            try:
                result = self._client.wait_for_measurement_result()
                self.sig_measure_result.emit(result.value)

                # Auto-write result byte to RFID tag
                if self._auto_rfid_write:
                    try:
                        self._write_result_to_rfid(result.value)
                    except Exception as exc:
                        logging.getLogger("it7.rfid").exception(
                            "Auto RFID write failed"
                        )
                        self.sig_error.emit(f"RFID write failed: {exc}")

                self.sig_state.emit("READY_FOR_MEASUREMENT")
            except Exception as exc:
                self.sig_error.emit(f"Wait for result failed: {exc}")
                self.sig_state.emit("ERROR")
            finally:
                self.sig_busy.emit(False)

        @Slot()
        def ack_measure(self) -> None:
            if self._client is None:
                self.sig_error.emit("Not connected to IT7")
                return
            try:
                self._client.acknowledge_measurement()
                self.sig_measure_acked.emit()
            except Exception as exc:
                self.sig_error.emit(f"Measure_OK failed: {exc}")

        @Slot(object)
        def close_batch(self, batch_id) -> None:
            if self._client is None:
                self.sig_error.emit("Not connected to IT7")
                return
            self.sig_busy.emit(True)
            self.sig_state.emit("CLOSING_BATCH")
            try:
                bid = (
                    batch_id
                    if (batch_id is not None and batch_id >= 0)
                    else None
                )
                self._client.close_batch(bid)
                self.sig_batch_closed.emit()
                self.sig_state.emit("COMPLETE")
            except Exception as exc:
                self.sig_error.emit(f"CloseBatch failed: {exc}")
                self.sig_state.emit("ERROR")
            finally:
                self.sig_busy.emit(False)

        @Slot(str, str, str, dict)
        def run_full_cycle(
            self,
            batch_filter: str,
            node: str,
            study: str,
            aux_values: dict,
        ) -> None:
            if self._client is None:
                self.sig_error.emit("Not connected to IT7")
                return
            self.sig_busy.emit(True)
            try:
                self.sig_state.emit("OPENING_BATCH_LIST")
                self._client.open_batch_list(
                    batch_filter=batch_filter or None,
                    node=node or None,
                    study=study or None,
                )
                self.sig_state.emit("WAITING_FOR_BATCH")
                batch = self._client.wait_for_batch_loaded()
                self.sig_batch_loaded.emit(batch.batch_id)

                if batch.batch_id == -1:
                    self.sig_state.emit("CANCELLED")
                    return

                if aux_values:
                    self.sig_state.emit("SENDING_AUX_DATA")
                    self._client.set_aux_data(aux_values)
                    self.sig_aux_sent.emit()

                self.sig_state.emit("WAITING_FOR_RESULT")
                result = self._client.wait_for_measurement_result()
                self.sig_measure_result.emit(result.value)

                if self._auto_rfid_write:
                    try:
                        self._write_result_to_rfid(result.value)
                    except Exception as exc:
                        logging.getLogger("it7.rfid").exception(
                            "Auto RFID write failed"
                        )
                        self.sig_error.emit(f"RFID write failed: {exc}")

                self.sig_state.emit("ACKNOWLEDGING_RESULT")
                self._client.acknowledge_measurement()
                self.sig_measure_acked.emit()

                self.sig_state.emit("CLOSING_BATCH")
                self._client.close_batch(batch.batch_id)
                self.sig_batch_closed.emit()
                self.sig_state.emit("COMPLETE")
            except Exception as exc:
                self.sig_error.emit(f"Full cycle failed: {exc}")
                self.sig_state.emit("ERROR")
            finally:
                self.sig_busy.emit(False)


    # -----------------------------------------------------------------------
    # Main window
    # -----------------------------------------------------------------------

    class MainWindow(QMainWindow):

        request_connect = Signal(object)
        request_disconnect = Signal()
        request_open_batch = Signal(str, str, str)
        request_send_aux = Signal(dict)
        request_wait_result = Signal()
        request_ack = Signal()
        request_close = Signal(object)
        request_full_cycle = Signal(str, str, str, dict)

        request_rfid_health = Signal(str)
        request_rfid_fetch = Signal(str)
        request_rfid_set_auto = Signal(bool)
        request_rfid_set_url = Signal(str)  # <-- ADDED

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("IT7 / TQM Balluff Client + RFID")
            self.resize(1080, 860)

            self._connected = False
            self._batch_id: int | None = None
            self._latest_tag_data: dict | None = None

            self._build_ui()
            self._setup_worker()
            self._setup_logging()

            # Push the initial RFID URL down to the worker.
            self.request_rfid_set_url.emit(
                self.rfid_url_edit.text().strip()
            )

        # ------------------------------------------------------------------
        # UI
        # ------------------------------------------------------------------

        def _build_ui(self) -> None:
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)

            # ============ Connection ============
            conn_group = QGroupBox("IT7 Connection")
            conn_layout = QHBoxLayout(conn_group)

            conn_layout.addWidget(QLabel("Host:"))
            self.host_edit = QLineEdit("127.0.0.1")
            self.host_edit.setMaximumWidth(140)
            conn_layout.addWidget(self.host_edit)

            conn_layout.addWidget(QLabel("Port:"))
            self.port_spin = QSpinBox()
            self.port_spin.setRange(1, 65534)
            self.port_spin.setValue(30000)
            conn_layout.addWidget(self.port_spin)

            conn_layout.addWidget(QLabel("Timeout (s):"))
            self.timeout_spin = QDoubleSpinBox()
            self.timeout_spin.setRange(0.5, 60.0)
            self.timeout_spin.setValue(10.0)
            self.timeout_spin.setSingleStep(0.5)
            conn_layout.addWidget(self.timeout_spin)

            self.connect_btn = QPushButton("Connect")
            self.connect_btn.clicked.connect(self._on_connect_clicked)
            conn_layout.addWidget(self.connect_btn)

            self.disconnect_btn = QPushButton("Disconnect")
            self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
            self.disconnect_btn.setEnabled(False)
            conn_layout.addWidget(self.disconnect_btn)

            conn_layout.addStretch()
            self.conn_status = QLabel("● Disconnected")
            self.conn_status.setStyleSheet("color: red; font-weight: bold;")
            conn_layout.addWidget(self.conn_status)

            root.addWidget(conn_group)

            # ============ RFID Integration ============
            rfid_group = QGroupBox("RFID Service (rfid_service.py)")
            rfid_v = QVBoxLayout(rfid_group)

            rfid_row1 = QHBoxLayout()
            rfid_row1.addWidget(QLabel("Service URL:"))
            self.rfid_url_edit = QLineEdit(DEFAULT_RFID_URL)
            self.rfid_url_edit.textChanged.connect(
                self._on_rfid_url_changed
            )
            rfid_row1.addWidget(self.rfid_url_edit)

            self.rfid_test_btn = QPushButton("Test")
            self.rfid_test_btn.clicked.connect(self._on_rfid_test_clicked)
            rfid_row1.addWidget(self.rfid_test_btn)

            self.rfid_fetch_btn = QPushButton("Fetch Latest Tag")
            self.rfid_fetch_btn.clicked.connect(self._on_rfid_fetch_clicked)
            rfid_row1.addWidget(self.rfid_fetch_btn)

            rfid_row1.addStretch()
            self.rfid_status_label = QLabel("● Unknown")
            self.rfid_status_label.setStyleSheet(
                "color: gray; font-weight: bold;"
            )
            rfid_row1.addWidget(self.rfid_status_label)
            rfid_v.addLayout(rfid_row1)

            rfid_row2 = QHBoxLayout()
            rfid_row2.addWidget(QLabel("Tag UID:"))
            self.rfid_uid_label = QLabel("—")
            self.rfid_uid_label.setStyleSheet(
                "font-family: Consolas; color: #333;"
            )
            self.rfid_uid_label.setMinimumWidth(220)
            rfid_row2.addWidget(self.rfid_uid_label)

            rfid_row2.addWidget(QLabel("User Data:"))
            self.rfid_user_label = QLabel("—")
            self.rfid_user_label.setStyleSheet(
                "font-family: Consolas; color: #333;"
            )
            self.rfid_user_label.setMinimumWidth(220)
            rfid_row2.addWidget(self.rfid_user_label)

            rfid_row2.addWidget(QLabel("Event:"))
            self.rfid_event_label = QLabel("—")
            rfid_row2.addWidget(self.rfid_event_label)

            rfid_row2.addStretch()
            rfid_v.addLayout(rfid_row2)

            rfid_row3 = QHBoxLayout()
            self.use_rfid_cb = QCheckBox(
                "Use RFID data for Aux  (Aux2 = tag_uid, Aux4 = tag_user_data)"
            )
            self.use_rfid_cb.setChecked(True)
            self.use_rfid_cb.toggled.connect(self._on_use_rfid_toggled)
            rfid_row3.addWidget(self.use_rfid_cb)

            self.auto_write_cb = QCheckBox(
                "Auto-write Measure OK/NOK to RFID byte 8  (01=OK, 00=NOK)"
            )
            self.auto_write_cb.setChecked(True)
            self.auto_write_cb.toggled.connect(self._on_auto_write_toggled)
            rfid_row3.addWidget(self.auto_write_cb)
            rfid_row3.addStretch()
            rfid_v.addLayout(rfid_row3)

            root.addWidget(rfid_group)

            # ============ Batch ============
            batch_group = QGroupBox("Batch")
            batch_layout = QHBoxLayout(batch_group)

            batch_layout.addWidget(QLabel("BatchFilter:"))
            self.batch_filter_edit = QLineEdit("TEST")
            batch_layout.addWidget(self.batch_filter_edit)

            batch_layout.addWidget(QLabel("Node:"))
            self.node_edit = QLineEdit("node1")
            self.node_edit.setMaximumWidth(110)
            batch_layout.addWidget(self.node_edit)

            batch_layout.addWidget(QLabel("Study:"))
            self.study_edit = QLineEdit("0")
            self.study_edit.setMaximumWidth(50)
            batch_layout.addWidget(self.study_edit)

            self.open_batch_btn = QPushButton("Open Batch")
            self.open_batch_btn.clicked.connect(self._on_open_batch_clicked)
            self.open_batch_btn.setEnabled(False)
            batch_layout.addWidget(self.open_batch_btn)

            batch_layout.addStretch()
            batch_layout.addWidget(QLabel("Batch ID:"))
            self.batch_id_label = QLabel("—")
            self.batch_id_label.setStyleSheet("font-weight: bold;")
            batch_layout.addWidget(self.batch_id_label)

            root.addWidget(batch_group)

            # ============ Aux ============
            aux_group = QGroupBox("Auxiliary Data")
            aux_layout = QHBoxLayout(aux_group)

            aux_layout.addWidget(QLabel("Aux2 (serial):"))
            self.aux2_edit = QLineEdit("SERIAL-123")
            aux_layout.addWidget(self.aux2_edit)

            aux_layout.addWidget(QLabel("Aux4 (line):"))
            self.aux4_edit = QLineEdit("LINE-3")
            aux_layout.addWidget(self.aux4_edit)

            self.send_aux_btn = QPushButton("Send Aux Data")
            self.send_aux_btn.clicked.connect(self._on_send_aux_clicked)
            self.send_aux_btn.setEnabled(False)
            aux_layout.addWidget(self.send_aux_btn)

            root.addWidget(aux_group)

            self._update_aux_field_state()

            # ============ Measurement ============
            meas_group = QGroupBox("Measurement")
            meas_layout = QHBoxLayout(meas_group)

            self.wait_result_btn = QPushButton("Wait for Measure OK/NOK")
            self.wait_result_btn.clicked.connect(
                self._on_wait_result_clicked
            )
            self.wait_result_btn.setEnabled(False)
            meas_layout.addWidget(self.wait_result_btn)

            self.ack_btn = QPushButton("Acknowledge (Measure_OK)")
            self.ack_btn.clicked.connect(self._on_ack_clicked)
            self.ack_btn.setEnabled(False)
            meas_layout.addWidget(self.ack_btn)

            self.close_btn = QPushButton("Close Batch")
            self.close_btn.clicked.connect(self._on_close_clicked)
            self.close_btn.setEnabled(False)
            meas_layout.addWidget(self.close_btn)

            meas_layout.addStretch()

            self.full_cycle_btn = QPushButton("▶  Run Full Cycle")
            self.full_cycle_btn.clicked.connect(
                self._on_full_cycle_clicked
            )
            self.full_cycle_btn.setEnabled(False)
            self.full_cycle_btn.setStyleSheet(
                "QPushButton { font-weight: bold; padding: 6px 14px; }"
            )
            meas_layout.addWidget(self.full_cycle_btn)

            root.addWidget(meas_group)

            # ============ State / Result ============
            status_layout = QHBoxLayout()
            status_layout.addWidget(QLabel("IT7 State:"))
            self.state_label = QLabel("IDLE")
            self.state_label.setStyleSheet(
                "font-weight: bold; color: #666;"
            )
            status_layout.addWidget(self.state_label)

            status_layout.addSpacing(30)
            status_layout.addWidget(QLabel("Result:"))
            self.result_label = QLabel("—")
            self.result_label.setStyleSheet(
                "font-weight: bold; font-size: 14pt; color: #333;"
            )
            status_layout.addWidget(self.result_label)

            status_layout.addSpacing(30)
            status_layout.addWidget(QLabel("RFID Write:"))
            self.rfid_write_label = QLabel("—")
            self.rfid_write_label.setStyleSheet(
                "font-weight: bold; color: #333;"
            )
            status_layout.addWidget(self.rfid_write_label)

            status_layout.addStretch()
            root.addLayout(status_layout)

            # ============ Log ============
            log_group = QGroupBox("Log")
            log_layout = QVBoxLayout(log_group)

            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            font = QFont("Consolas")
            font.setStyleHint(QFont.Monospace)
            font.setPointSize(9)
            self.log_view.setFont(font)
            self.log_view.setMaximumBlockCount(5000)
            log_layout.addWidget(self.log_view)

            log_buttons = QHBoxLayout()
            clear_btn = QPushButton("Clear Log")
            clear_btn.clicked.connect(self.log_view.clear)
            log_buttons.addWidget(clear_btn)

            self.autoscroll_cb = QCheckBox("Auto-scroll")
            self.autoscroll_cb.setChecked(True)
            log_buttons.addWidget(self.autoscroll_cb)
            log_buttons.addStretch()
            log_layout.addLayout(log_buttons)

            root.addWidget(log_group, stretch=1)

        # ------------------------------------------------------------------
        # Worker wiring
        # ------------------------------------------------------------------

        def _setup_worker(self) -> None:
            self._thread = QThread()
            self._worker = IT7Worker(rfid_client=None)
            self._worker.moveToThread(self._thread)

            # commands
            self.request_connect.connect(self._worker.connect_to_it7)
            self.request_disconnect.connect(self._worker.disconnect)
            self.request_open_batch.connect(self._worker.open_batch)
            self.request_send_aux.connect(self._worker.send_aux)
            self.request_wait_result.connect(self._worker.wait_result)
            self.request_ack.connect(self._worker.ack_measure)
            self.request_close.connect(self._worker.close_batch)
            self.request_full_cycle.connect(self._worker.run_full_cycle)

            self.request_rfid_health.connect(self._worker.rfid_check_health)
            self.request_rfid_fetch.connect(self._worker.rfid_fetch_latest)
            self.request_rfid_set_auto.connect(
                self._worker.set_auto_rfid_write
            )
            self.request_rfid_set_url.connect(self._worker.set_rfid_url)  # <-- ADDED

            # IT7 signals
            self._worker.sig_connected.connect(self._on_connected)
            self._worker.sig_disconnected.connect(self._on_disconnected)
            self._worker.sig_batch_loaded.connect(self._on_batch_loaded)
            self._worker.sig_aux_sent.connect(self._on_aux_sent)
            self._worker.sig_measure_result.connect(self._on_measure_result)
            self._worker.sig_measure_acked.connect(self._on_measure_acked)
            self._worker.sig_batch_closed.connect(self._on_batch_closed)
            self._worker.sig_error.connect(self._on_error)
            self._worker.sig_busy.connect(self._on_busy)
            self._worker.sig_state.connect(self._on_state)

            # RFID signals
            self._worker.sig_rfid_health.connect(self._on_rfid_health)
            self._worker.sig_rfid_data.connect(self._on_rfid_data)
            self._worker.sig_rfid_write_start.connect(
                self._on_rfid_write_start
            )
            self._worker.sig_rfid_write_done.connect(
                self._on_rfid_write_done
            )

            self._thread.start()

        def _setup_logging(self) -> None:
            self._log_emitter = _LogEmitter()
            self._log_emitter.message.connect(
                self._append_log, Qt.QueuedConnection
            )
            handler = _QtLogHandler(self._log_emitter)
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S")
            )
            for name in ("it7", "it7.rfid"):
                logger = logging.getLogger(name)
                logger.addHandler(handler)
                logger.setLevel(logging.INFO)

        # ------------------------------------------------------------------
        # Logging
        # ------------------------------------------------------------------

        def _append_log(self, msg: str) -> None:
            if "IT7 TX" in msg:
                color = "#006600"
            elif "IT7 RX" in msg:
                color = "#0000cc"
            elif "RFID" in msg and "Wrote" in msg:
                color = "#006600"
            elif "RFID" in msg:
                color = "#9933cc"
            elif "ERROR" in msg or "Traceback" in msg:
                color = "#cc0000"
            else:
                color = None

            if color:
                safe = (
                    msg.replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace(">", "&gt;")
                )
                self.log_view.appendHtml(
                    f'<span style="color:{color}">{safe}</span>'
                )
            else:
                self.log_view.appendPlainText(msg)

            if self.autoscroll_cb.isChecked():
                self.log_view.moveCursor(QTextCursor.End)

        # ------------------------------------------------------------------
        # Button handlers
        # ------------------------------------------------------------------

        def _on_connect_clicked(self) -> None:
            try:
                config = IT7Config(
                    host=self.host_edit.text().strip(),
                    it7_receive_port=self.port_spin.value(),
                    timeout=self.timeout_spin.value(),
                )
            except Exception as exc:
                QMessageBox.critical(self, "Invalid config", str(exc))
                return
            self.request_connect.emit(config)

        def _on_disconnect_clicked(self) -> None:
            self.request_disconnect.emit()

        def _on_rfid_url_changed(self, _text: str) -> None:  # <-- ADDED
            self.request_rfid_set_url.emit(
                self.rfid_url_edit.text().strip()
            )

        def _on_rfid_test_clicked(self) -> None:
            url = self.rfid_url_edit.text().strip()
            if not url:
                QMessageBox.warning(self, "RFID URL", "Enter service URL.")
                return
            self.request_rfid_health.emit(url)

        def _on_rfid_fetch_clicked(self) -> None:
            url = self.rfid_url_edit.text().strip()
            if not url:
                QMessageBox.warning(self, "RFID URL", "Enter service URL.")
                return
            self.request_rfid_fetch.emit(url)

        def _on_use_rfid_toggled(self, checked: bool) -> None:
            self._update_aux_field_state()

        def _update_aux_field_state(self) -> None:
            manual = not self.use_rfid_cb.isChecked()
            self.aux2_edit.setEnabled(manual)
            self.aux4_edit.setEnabled(manual)

        def _on_auto_write_toggled(self, checked: bool) -> None:
            self.request_rfid_set_auto.emit(checked)

        def _on_open_batch_clicked(self) -> None:
            self.request_open_batch.emit(
                self.batch_filter_edit.text().strip(),
                self.node_edit.text().strip(),
                self.study_edit.text().strip(),
            )

        def _collect_aux_values(self) -> dict | None:
            """Build aux dict from RFID (if enabled) or manual fields."""
            if self.use_rfid_cb.isChecked():
                tag = self._latest_tag_data
                if tag is None:
                    QMessageBox.warning(
                        self,
                        "No RFID tag data",
                        "Click 'Fetch Latest Tag' first.",
                    )
                    return None
                uid = (tag.get("tag_uid") or "").strip()
                user = (tag.get("tag_user_data") or "").strip()
                values = {}
                if uid:
                    values["Aux2"] = uid
                if user:
                    values["Aux4"] = user
                if not values:
                    QMessageBox.warning(
                        self,
                        "Empty RFID data",
                        "Tag has no UID or user data.",
                    )
                    return None
                return values

            a2 = self.aux2_edit.text().strip()
            a4 = self.aux4_edit.text().strip()
            values = {}
            if a2:
                values["Aux2"] = a2
            if a4:
                values["Aux4"] = a4
            if not values:
                QMessageBox.warning(
                    self,
                    "No aux data",
                    "Enter at least Aux2 or Aux4.",
                )
                return None
            return values

        def _on_send_aux_clicked(self) -> None:
            values = self._collect_aux_values()
            if values is None:
                return
            self.request_send_aux.emit(values)

        def _on_wait_result_clicked(self) -> None:
            self.request_wait_result.emit()

        def _on_ack_clicked(self) -> None:
            self.request_ack.emit()

        def _on_close_clicked(self) -> None:
            self.request_close.emit(self._batch_id)

        def _on_full_cycle_clicked(self) -> None:
            values = self._collect_aux_values()
            if values is None:
                return
            self.request_full_cycle.emit(
                self.batch_filter_edit.text().strip(),
                self.node_edit.text().strip(),
                self.study_edit.text().strip(),
                values,
            )

        # ------------------------------------------------------------------
        # IT7 signals
        # ------------------------------------------------------------------

        def _on_connected(self) -> None:
            self._connected = True
            self.conn_status.setText("● Connected")
            self.conn_status.setStyleSheet(
                "color: green; font-weight: bold;"
            )
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.open_batch_btn.setEnabled(True)
            self.full_cycle_btn.setEnabled(True)
            self._append_log(
                f"--- IT7 connected "
                f"{self.host_edit.text()}:{self.port_spin.value()} ---"
            )

        def _on_disconnected(self) -> None:
            self._connected = False
            self.conn_status.setText("● Disconnected")
            self.conn_status.setStyleSheet(
                "color: red; font-weight: bold;"
            )
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.open_batch_btn.setEnabled(False)
            self.send_aux_btn.setEnabled(False)
            self.wait_result_btn.setEnabled(False)
            self.ack_btn.setEnabled(False)
            self.close_btn.setEnabled(False)
            self.full_cycle_btn.setEnabled(False)
            self._append_log("--- IT7 disconnected ---")

        def _on_batch_loaded(self, batch_id: int) -> None:
            self._batch_id = batch_id if batch_id > 0 else None
            self.batch_id_label.setText(str(batch_id))
            if batch_id == -1:
                self.batch_id_label.setStyleSheet(
                    "font-weight: bold; color: #cc6600;"
                )
                self.send_aux_btn.setEnabled(False)
                self.wait_result_btn.setEnabled(False)
            else:
                self.batch_id_label.setStyleSheet(
                    "font-weight: bold; color: green;"
                )
                self.send_aux_btn.setEnabled(True)

        def _on_aux_sent(self) -> None:
            self.wait_result_btn.setEnabled(True)
            self.ack_btn.setEnabled(True)
            self.close_btn.setEnabled(True)

        def _on_measure_result(self, result: str) -> None:
            color = "green" if result == "OK" else "red"
            self.result_label.setText(result)
            self.result_label.setStyleSheet(
                f"font-weight: bold; font-size: 14pt; color: {color};"
            )

        def _on_measure_acked(self) -> None:
            self._append_log("--- Measure_OK sent ---")

        def _on_batch_closed(self) -> None:
            self._batch_id = None
            self.batch_id_label.setText("—")
            self.send_aux_btn.setEnabled(False)
            self.wait_result_btn.setEnabled(False)
            self.ack_btn.setEnabled(False)
            self.close_btn.setEnabled(False)
            self.result_label.setText("—")
            self.result_label.setStyleSheet(
                "font-weight: bold; font-size: 14pt; color: #333;"
            )

        def _on_error(self, msg: str) -> None:
            self._append_log(f"ERROR: {msg}")
            QMessageBox.warning(self, "IT7 / RFID Error", msg)

        def _on_busy(self, busy: bool) -> None:
            if busy:
                self.open_batch_btn.setEnabled(False)
                self.send_aux_btn.setEnabled(False)
                self.wait_result_btn.setEnabled(False)
                self.ack_btn.setEnabled(False)
                self.close_btn.setEnabled(False)
                self.full_cycle_btn.setEnabled(False)
            else:
                if self._connected:
                    self.open_batch_btn.setEnabled(True)
                    self.full_cycle_btn.setEnabled(True)
                    if self._batch_id is not None:
                        self.send_aux_btn.setEnabled(True)
                        self.close_btn.setEnabled(True)

        def _on_state(self, state: str) -> None:
            self.state_label.setText(state)
            colors = {
                "IDLE": "#666666",
                "OPENING_BATCH_LIST": "#0066cc",
                "WAITING_FOR_BATCH": "#cc6600",
                "SENDING_AUX_DATA": "#0066cc",
                "READY_FOR_MEASUREMENT": "#009933",
                "WAITING_FOR_RESULT": "#cc6600",
                "ACKNOWLEDGING_RESULT": "#0066cc",
                "CLOSING_BATCH": "#0066cc",
                "COMPLETE": "#009933",
                "CANCELLED": "#cc6600",
                "ERROR": "#cc0000",
            }
            color = colors.get(state, "#333333")
            self.state_label.setStyleSheet(
                f"font-weight: bold; color: {color};"
            )

        # ------------------------------------------------------------------
        # RFID signals
        # ------------------------------------------------------------------

        def _on_rfid_health(self, status: str, message: str) -> None:
            if status == "OK":
                self.rfid_status_label.setText("● Reachable")
                self.rfid_status_label.setStyleSheet(
                    "color: green; font-weight: bold;"
                )
            else:
                self.rfid_status_label.setText("● Unreachable")
                self.rfid_status_label.setStyleSheet(
                    "color: red; font-weight: bold;"
                )
                self._append_log(f"RFID health: {message}")

        def _on_rfid_data(self, tag: dict) -> None:
            self._latest_tag_data = tag
            uid = tag.get("tag_uid") or "—"
            user = tag.get("tag_user_data") or "—"
            event = tag.get("event") or "—"
            self.rfid_uid_label.setText(uid)
            self.rfid_user_label.setText(user)
            self.rfid_event_label.setText(event)
            self.rfid_status_label.setText("● Got tag data")
            self.rfid_status_label.setStyleSheet(
                "color: green; font-weight: bold;"
            )
            self._append_log(
                f"--- RFID tag fetched: UID={uid} UserData={user} ---"
            )

        def _on_rfid_write_start(
            self, result_code: str, address: int, value: str
        ) -> None:
            self.rfid_write_label.setText(
                f"Writing {result_code} (0x{value} @ byte {address + 1})..."
            )
            self.rfid_write_label.setStyleSheet(
                "font-weight: bold; color: #0066cc;"
            )

        def _on_rfid_write_done(
            self, result_code: str, message: str
        ) -> None:
            if result_code == "ERROR":
                self.rfid_write_label.setText("RFID write FAILED")
                self.rfid_write_label.setStyleSheet(
                    "font-weight: bold; color: #cc0000;"
                )
                self._append_log(f"RFID write failed: {message}")
            else:
                self.rfid_write_label.setText(
                    f"RFID updated: {result_code} ({message})"
                )
                self.rfid_write_label.setStyleSheet(
                    "font-weight: bold; color: green;"
                )
                self._append_log(f"RFID write OK: {message}")

                # Refresh cache so byte-8 reflects the new value
                self.request_rfid_fetch.emit(
                    self.rfid_url_edit.text().strip()
                )

        # ------------------------------------------------------------------
        # Shutdown
        # ------------------------------------------------------------------

        def closeEvent(self, event) -> None:
            if self._connected:
                self.request_disconnect.emit()
            self._thread.quit()
            self._thread.wait(2000)
            event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    if not _PYSIDE_AVAILABLE:
        print(
            "PySide6 is not installed.\n"
            "Install it with:  pip install PySide6"
        )
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("IT7 TQM Client + RFID")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())