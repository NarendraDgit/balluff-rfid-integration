from collections import deque

from it7.client import IT7Client
from it7.models import IT7Config
from it7.state_machine import MeasurementSession, SessionState


class FakeTransport:
    def __init__(self, incoming):
        self.incoming = deque(incoming)
        self.sent = []
        self.is_open = False

    def open(self): self.is_open = True
    def close(self): self.is_open = False
    def send(self, message): self.sent.append(message)
    def receive(self, timeout=None): return self.incoming.popleft()


def test_complete_measurement_session():
    transport = FakeTransport([
        "OpenBatchList_OK",
        "LOADED 123",
        "SetAuxData_OK",
        "Measure NOK",
        "CloseBatch_OK",
    ])
    client = IT7Client(
        IT7Config(host="127.0.0.1", it7_receive_port=12345),
        transport=transport,
    )
    session = MeasurementSession(
        client,
        batch_filter="TEST",
        node="node1",
        study="0",
        aux_data_factory=lambda batch: {
            "Aux2": "SERIAL-123", "Aux4": "LINE-3",
        },
    )
    batch = session.start()
    assert batch.batch_id == 123
    assert session.state == SessionState.READY_FOR_MEASUREMENT

    result = session.wait_for_result_and_close()
    assert result.value == "NOK"
    assert session.state == SessionState.COMPLETE
    assert transport.sent == [
        'OpenBatchList BatchFilter="TEST";Node="node1";Study="0"',
        'SetAuxData Aux2="SERIAL-123";Aux4="LINE-3"',
        "Measure_OK",
        "CloseBatch 123",
    ]

def test_operator_can_cancel_batch_selection():
    transport = FakeTransport(["OpenBatchList_OK", "BatchLoaded -1"])
    client = IT7Client(
        IT7Config(host="127.0.0.1", it7_receive_port=12345),
        transport=transport,
    )
    session = MeasurementSession(client, batch_filter="TEST")
    batch = session.start()
    assert batch.batch_id == -1
    assert session.state == SessionState.CANCELLED