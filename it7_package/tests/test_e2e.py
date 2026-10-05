import pytest

from it7.e2e import EndToEndHarness, Scenario, SimulatedRFIDTag
from it7.models import MeasurementResult
from it7.state_machine import SessionState


def tag():
    return SimulatedRFIDTag(
        serial_number="SERIAL-E2E", batch="TEST", line="LINE-7",
    )


@pytest.mark.parametrize(
    ("scenario", "expected_result"),
    [
        (Scenario.OK, MeasurementResult.OK),
        (Scenario.NOK, MeasurementResult.NOK),
    ],
)
def test_e2e_measurement_outcomes(scenario, expected_result):
    result = EndToEndHarness(batch_id=65).run(scenario, tag=tag())
    assert result.passed
    assert result.state == SessionState.COMPLETE
    assert result.measurement_result == expected_result
    assert result.aux_data == {"Aux2": "SERIAL-E2E", "Aux4": "LINE-7"}


def test_e2e_cancel():
    result = EndToEndHarness(batch_id=65).run(Scenario.CANCEL, tag=tag())
    assert result.passed
    assert result.state == SessionState.CANCELLED
    assert result.batch_id is None
    assert result.aux_data == {}


@pytest.mark.parametrize(
    "scenario",
    [
        Scenario.TIMEOUT_OPEN, Scenario.TIMEOUT_BATCH, Scenario.TIMEOUT_AUX,
        Scenario.TIMEOUT_MEASURE, Scenario.TIMEOUT_CLOSE,
        Scenario.OPEN_ERROR, Scenario.AUX_ERROR, Scenario.CLOSE_ERROR,
        Scenario.MALFORMED_OPEN, Scenario.MALFORMED_MEASURE,
        Scenario.UNEXPECTED_OPEN, Scenario.UNEXPECTED_MEASURE,
        Scenario.WRONG_BATCH, Scenario.DUPLICATE_OPEN_ACK,
        Scenario.DUPLICATE_BATCH,
    ],
)
def test_e2e_fault_scenarios(scenario):
    result = EndToEndHarness(batch_id=65, timeout=0.05).run(scenario, tag=tag())
    assert result.passed, f"{scenario}: {result.error}"