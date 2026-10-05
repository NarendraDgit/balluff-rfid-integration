from collections import deque

from it7.client import IT7Client
from it7.models import IT7Config
from it7.exceptions import IT7ErrorResponse, IT7ProtocolError


class FakeTransport:
    def __init__(self, incoming):
        self.incoming = deque(incoming)
        self.sent = []
        self.is_open = False

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def send(self, message):
        self.sent.append(message)

    def receive(self, timeout=None):
        if not self.incoming:
            raise AssertionError("Fake transport has no more messages")
        return self.incoming.popleft()


def config():
    return IT7Config(host="127.0.0.1", it7_receive_port=12345)


def test_open_batch_and_wait_for_loaded():
    fake = FakeTransport(["OpenBatchList_OK", "LOADED 65"])
    client = IT7Client(config(), transport=fake)

    client.open_batch_list(batch_filter="TEST", node="node1", study="0")
    batch = client.wait_for_batch_loaded()

    assert batch.batch_id == 65
    assert fake.sent == [
        'OpenBatchList BatchFilter="TEST";Node="node1";Study="0"'
    ]

def test_set_aux_data():
    fake = FakeTransport(["SetAuxData_OK"])
    client = IT7Client(config(), transport=fake)
    client.set_aux_data({"Aux2": "serial xyz"})
    assert fake.sent == ['SetAuxData Aux2="serial xyz"']

def test_unknown_command_tqm_typo_is_buffered():
    fake = FakeTransport([
        'UNKNOW_COMMAND OpenBatchList;BatchFilter="TEST"',
        "OpenBatchList_OK",
    ])
    client = IT7Client(config(), transport=fake)
    client.open_batch_list(batch_filter="TEST")

def test_measurement_result_and_ack():
    fake = FakeTransport(["Measure OK"])
    client = IT7Client(config(), transport=fake)
    result = client.wait_for_measurement_result()
    client.acknowledge_measurement()
    assert result.value == "OK"
    assert fake.sent == ["Measure_OK"]


def test_close_batch_then_wait_closed():
    fake = FakeTransport(["CloseBatch_OK", "CLOSED 65"])
    client = IT7Client(config(), transport=fake)
    client.close_batch(65)
    closed = client.wait_for_batch_closed()
    assert closed.batch_id == 65


def test_error_response_raises():
    fake = FakeTransport(["SetAuxData_ERR3"])
    client = IT7Client(config(), transport=fake)
    try:
        client.set_aux_data({"Aux2": "x"})
    except IT7ErrorResponse as exc:
        assert exc.command == "SetAuxData"
        assert exc.error_number == 3
    else:
        raise AssertionError("Expected IT7ErrorResponse")


def test_async_loaded_buffered_before_open_ack():
    fake = FakeTransport(["LOADED 42", "OpenBatchList_OK"])
    client = IT7Client(config(), transport=fake)
    client.open_batch_list(batch_filter="TEST")
    batch = client.wait_for_batch_loaded()
    assert batch.batch_id == 42


def test_unexpected_message_during_measurement_is_protocol_error():
    fake = FakeTransport(["CloseBatch_OK"])
    client = IT7Client(config(), transport=fake)
    try:
        client.wait_for_measurement_result()
    except IT7ProtocolError:
        pass
    else:
        raise AssertionError("Expected IT7ProtocolError")


def test_case_insensitive_ack_matching():
    fake = FakeTransport(["openbatchlist_ok"])
    client = IT7Client(config(), transport=fake)
    client.open_batch_list(batch_filter="TEST")
    # No exception means the ACK was accepted