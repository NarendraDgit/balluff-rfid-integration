import socket
import time

from it7.models import IT7Config
from it7.simulator import Fault, SimulatedBatch, SimulatedIT7Server


def free_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def receive_one(sock, timeout=1.0):
    sock.settimeout(timeout)
    data, _ = sock.recvfrom(65535)
    return data.decode("utf-8")


def make_server(port, **kwargs):
    config = IT7Config(host="127.0.0.1", it7_receive_port=port, timeout=1.0)
    return SimulatedIT7Server(
        config,
        loaded_delay=0.0,
        closed_delay=0.0,
        send_close_socket_on_stop=False,
        **kwargs,
    )


def test_open_aux_measure_close_happy_path():
    port = free_udp_port()
    balluff = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    balluff.bind(("127.0.0.1", port + 1))
    server = make_server(port, batch=SimulatedBatch(batch_id=65))

    try:
        server.start()
        balluff.sendto(b'OpenBatchList;BatchFilter="TEST"',
                       ("127.0.0.1", port))
        assert receive_one(balluff) == "OpenBatchList_OK"
        assert receive_one(balluff) == "LOADED 65"

        balluff.sendto(b'SetAuxData;Aux2="SERIAL";Aux4="LINE-3"',
                       ("127.0.0.1", port))
        assert receive_one(balluff) == "SetAuxData_OK"
        assert server.state.aux_data == {"Aux2": "SERIAL", "Aux4": "LINE-3"}

        server.emit_measurement_result("OK")
        assert receive_one(balluff) == "Measure OK"

        balluff.sendto(b"Measure_OK", ("127.0.0.1", port))
        time.sleep(0.05)
        assert server.state.last_measurement_result == "ACK"

        balluff.sendto(b"CloseBatch 65", ("127.0.0.1", port))
        assert receive_one(balluff) == "CloseBatch_OK"
        assert receive_one(balluff) == "CLOSED 65"
    finally:
        server.stop()
        balluff.close()


def test_open_batch_list_study_error():
    port = free_udp_port()
    balluff = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    balluff.bind(("127.0.0.1", port + 1))
    server = make_server(port)
    try:
        server.start()
        balluff.sendto(b'OpenBatchList Study="not-a-number"',
                       ("127.0.0.1", port))
        assert receive_one(balluff) == "OpenBatchList_ERR2"

        balluff.sendto(b'OpenBatchList Study="99"', ("127.0.0.1", port))
        assert receive_one(balluff) == "OpenBatchList_ERR2"
    finally:
        server.stop()
        balluff.close()


def test_open_batch_list_node_not_found():
    port = free_udp_port()
    balluff = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    balluff.bind(("127.0.0.1", port + 1))
    server = make_server(port)
    try:
        server.start()
        balluff.sendto(b'OpenBatchList Node="NotFound"',
                       ("127.0.0.1", port))
        assert receive_one(balluff) == "OpenBatchList_ERR3"
    finally:
        server.stop()
        balluff.close()


def test_set_aux_data_errors():
    port = free_udp_port()
    balluff = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    balluff.bind(("127.0.0.1", port + 1))
    server = make_server(port)
    try:
        server.start()
        balluff.sendto(b"SetAuxData", ("127.0.0.1", port))
        assert receive_one(balluff) == "SetAuxData_ERR4"

        balluff.sendto(b'SetAuxData Aux21="Test"', ("127.0.0.1", port))
        assert receive_one(balluff) == "SetAuxData_ERR2"

        long_value = "x" * 41
        balluff.sendto(
            f'SetAuxData Aux1="{long_value}"'.encode(),
            ("127.0.0.1", port),
        )
        assert receive_one(balluff) == "SetAuxData_ERR3"
    finally:
        server.stop()
        balluff.close()


def test_close_batch_errors():
    port = free_udp_port()
    balluff = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    balluff.bind(("127.0.0.1", port + 1))
    server = make_server(port)
    try:
        server.start()
        balluff.sendto(b"CloseBatch 99", ("127.0.0.1", port))
        assert receive_one(balluff) == "CloseBatch_ERR3"

        balluff.sendto(b"CloseBatch abc", ("127.0.0.1", port))
        assert receive_one(balluff) == "CloseBatch_ERR2"

        balluff.sendto(b"CloseBatch -5", ("127.0.0.1", port))
        assert receive_one(balluff) == "CloseBatch_ERR2"
    finally:
        server.stop()
        balluff.close()


def test_unknown_command():
    port = free_udp_port()
    balluff = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    balluff.bind(("127.0.0.1", port + 1))
    server = make_server(port)
    try:
        server.start()
        balluff.sendto(b"TEST", ("127.0.0.1", port))
        assert receive_one(balluff) == "UNKNOWN_COMMAND TEST"
    finally:
        server.stop()
        balluff.close()


def test_case_insensitive_command():
    port = free_udp_port()
    balluff = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    balluff.bind(("127.0.0.1", port + 1))
    server = make_server(port)
    try:
        server.start()
        balluff.sendto(b"closebaTCH", ("127.0.0.1", port))
        assert receive_one(balluff) == "CloseBatch_OK"
        assert receive_one(balluff) == "CLOSED 65"
    finally:
        server.stop()
        balluff.close()


def test_close_socket_sent_on_stop():
    port = free_udp_port()
    balluff = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    balluff.bind(("127.0.0.1", port + 1))
    config = IT7Config(host="127.0.0.1", it7_receive_port=port, timeout=1.0)
    server = SimulatedIT7Server(
        config, loaded_delay=0.0, closed_delay=0.0,
        send_close_socket_on_stop=True,
    )
    server.start()
    server.stop()
    assert receive_one(balluff) == "CLOSE_SOCKET"
    balluff.close()


def test_cancel_batch_selection_legacy():
    port = free_udp_port()
    balluff = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    balluff.bind(("127.0.0.1", port + 1))
    server = make_server(port, send_loaded_on_open=False)
    try:
        server.start()
        balluff.sendto(b"OpenBatchList", ("127.0.0.1", port))
        assert receive_one(balluff) == "OpenBatchList_OK"
        server.cancel_batch_selection()
        assert receive_one(balluff) == "BatchLoaded -1"
    finally:
        server.stop()
        balluff.close()