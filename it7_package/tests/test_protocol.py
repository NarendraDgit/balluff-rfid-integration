import pytest

from it7.exceptions import IT7ProtocolError
from it7.models import (
    BatchClosed, BatchLoaded, CloseSocket, MeasurementResult, UnknownCommand,
)
from it7.protocol import ErrorResponse, IT7Protocol, SimpleResponse


def test_open_batch_list_default_format():
    msg = IT7Protocol.open_batch_list(
        batch_filter="test", node="node1", study="0",
    )
    assert msg == 'OpenBatchList BatchFilter="test";Node="node1";Study="0"'


def test_open_batch_list_can_use_space_separator():
    msg = IT7Protocol.open_batch_list(
        batch_filter="test", node="node1", separator=" ",
    )
    assert msg == 'OpenBatchList BatchFilter="test" Node="node1"'


def test_open_batch_list_omits_missing_parameters():
    assert IT7Protocol.open_batch_list() == "OpenBatchList"


def test_set_aux_data():
    msg = IT7Protocol.set_aux_data({"Aux2": "serial xyz", "Aux4": "line 3"})
    assert msg == 'SetAuxData Aux2="serial xyz";Aux4="line 3"'


def test_close_batch():
    assert IT7Protocol.close_batch() == "CloseBatch"
    assert IT7Protocol.close_batch(12345) == "CloseBatch 12345"


def test_measurement_ack():
    assert IT7Protocol.measurement_ack() == "Measure_OK"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("OpenBatchList_OK", SimpleResponse("OpenBatchList", "OpenBatchList_OK")),
        ("SetAuxData_OK", SimpleResponse("SetAuxData", "SetAuxData_OK")),
        ("CloseBatch_OK", SimpleResponse("CloseBatch", "CloseBatch_OK")),
        ("LOADED 65", BatchLoaded(65)),
        ("LOADED -1", BatchLoaded(-1)),
        ("BatchLoaded 12345", BatchLoaded(12345)),
        ("CLOSED 65", BatchClosed(65)),
        ("CLOSE_SOCKET", CloseSocket()),
        ("Measure OK", MeasurementResult.OK),
        ("Measure NOK", MeasurementResult.NOK),
    ],
)
def test_parse(raw, expected):
    assert IT7Protocol.parse(raw) == expected


def test_parse_unknown_command_correct_spelling():
    r = IT7Protocol.parse("UNKNOWN_COMMAND TEST")
    assert isinstance(r, UnknownCommand)
    assert r.command == "TEST"


def test_parse_unknown_command_tqm_typo():
    # TQM actually sends "UNKNOW_COMMAND" (single N).
    r = IT7Protocol.parse('UNKNOW_COMMAND OpenBatchList;BatchFilter="TEST"')
    assert isinstance(r, UnknownCommand)
    assert r.command == 'OpenBatchList;BatchFilter="TEST"'


def test_parse_case_insensitive():
    assert IT7Protocol.parse("openbatchlist_ok") == \
        SimpleResponse("OpenBatchList", "openbatchlist_ok")
    assert IT7Protocol.parse("measure ok") == MeasurementResult.OK
    assert IT7Protocol.parse("loaded 65") == BatchLoaded(65)


def test_parse_error():
    assert IT7Protocol.parse("SetAuxData_ERR2") == \
        ErrorResponse("SetAuxData", 2, "SetAuxData_ERR2")
    assert IT7Protocol.parse("OpenBatchList_ERR3") == \
        ErrorResponse("OpenBatchList", 3, "OpenBatchList_ERR3")
    assert IT7Protocol.parse("CloseBatch_ERR2") == \
        ErrorResponse("CloseBatch", 2, "CloseBatch_ERR2")
    assert IT7Protocol.parse("SetAuxData_ERR4") == \
        ErrorResponse("SetAuxData", 4, "SetAuxData_ERR4")


def test_invalid_message():
    with pytest.raises(IT7ProtocolError):
        IT7Protocol.parse("something_unknown")


def test_unsupported_quote_in_value():
    with pytest.raises(IT7ProtocolError):
        IT7Protocol.set_aux_data({"Aux2": 'bad"value'})