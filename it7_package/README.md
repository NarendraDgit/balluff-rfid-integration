# Accurate IT7 UDP Package

A small Python 3.11+ package implementing the UDP protocol described in
Accurate Engineering's **"Communication with Balluff system"** document.

## Scope

Implemented protocol messages:

### Balluff application -> IT7 Measure

- `OpenBatchList`
- `SetAuxData`
- `CloseBatch`
- `Measure_OK` (measurement-result acknowledgement)

### IT7 Measure -> Balluff application

- `OpenBatchList_OK`
- `OpenBatchList_ERRn`
- `BatchLoaded <BatchID>`
- `SetAuxData_OK`
- `SetAuxData_ERRn`
- `Measure OK`
- `Measure NOK`
- `CloseBatch_OK`
- `CloseBatch_ERRn`

## Important protocol assumptions

The source document has a few ambiguities that should be confirmed with Accurate
before production deployment:

1. It says IT7's receive port is configurable and IT7's send port is that port + 1.
2. The example `123456` cannot be a valid UDP port (UDP ports max out at 65535);
   therefore the package does not use that example as a default.
3. The formal syntax suggests semicolon-separated parameters, while the prose
   examples visually show a space after the command. The package defaults to
   semicolon separation and makes this configurable.
4. The document does not define character encoding, escaping rules, maximum
   message size, retry policy, or the meanings of `ERRn`.
5. This implementation treats one UDP datagram as one protocol message.

The implementation is deliberately conservative: protocol parsing is strict,
timeouts/retries are configurable, and raw protocol traffic can be logged.

## Package layout

```text
src/it7/
    __init__.py
    cli.py
    client.py
    exceptions.py
    models.py
    protocol.py
    state_machine.py
    transport.py
tests/
    test_protocol.py
    test_client.py
    test_state_machine.py
```

## Install

With `uv`:

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
uv pip install pytest
```

Or with standard Python:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install pytest
```

## Run tests

```bash
pytest
```

## CLI test client

Show help:

```bash
it7-test --help
```

Example:

```bash
it7-test --host 192.168.72.223 --port 12345 open-batch \
    --batch-filter TEST \
    --node node1 \
    --study 0
```

Listen for unsolicited IT7 messages:

```bash
it7-test --host 192.168.72.223 --port 12345 listen
```

Send auxiliary data:

```bash
it7-test --host 192.168.72.223 --port 12345 aux \
    --value Aux2="serial xyz" \
    --value Aux4="line 3"
```

Close the current batch:

```bash
it7-test --host 192.168.72.223 --port 12345 close
```

Send a measurement acknowledgement:

```bash
it7-test --host 192.168.72.223 --port 12345 measure-ack
```

## Python example

```python
from it7 import IT7Client, IT7Config

config = IT7Config(
    host="192.168.72.223",
    it7_receive_port=12345,
)

with IT7Client(config) as it7:
    it7.open_batch_list(
        batch_filter="TEST",
        node="node1",
        study="0",
    )

    batch = it7.wait_for_batch_loaded()

    if batch.batch_id == -1:
        print("Operator cancelled batch selection")
        return

    it7.set_aux_data({
        "Aux2": "serial xyz",
        "Aux4": "line 3",
    })

    result = it7.wait_for_measurement_result()

    print("Measurement:", result.value)

    it7.acknowledge_measurement()

    it7.close_batch(batch.batch_id)
```

## Measurement-session helper

For the higher-level workflow, see `MeasurementSession` in
`it7.state_machine`.

It intentionally does **not** try to automate the operator's batch selection:
the source document says the operator manually selects and opens the batch.

## Security / network notes

This is intended for a trusted industrial LAN. The source protocol specifies
UDP but does not specify authentication, encryption, message signing, or
authorization. Do not expose the IT7 UDP port to an untrusted network.

A firewall rule should normally permit UDP only between the Balluff application
host and the IT7 Measure host.

## Next step before production

Confirm the exact wire format and network behavior with Accurate, especially:

- actual port number;
- destination/source port behavior;
- command separator;
- encoding;
- field escaping;
- timeout/retry expectations;
- duplicate-command behavior;
- `ERRn` definitions;
- maximum auxiliary-field length.


## Simulated IT7 server

The package includes `SimulatedIT7Server`, a small UDP test double. It is useful
for testing the Balluff-side client before connecting to a real IT7 installation.

Start it in one terminal:

```bash
it7-test --host 127.0.0.1 --port 12345 simulate
```

The simulator listens on UDP port `12345` and sends IT7->Balluff messages to
`127.0.0.1:12346`.

In a second terminal:

```bash
it7-test --host 127.0.0.1 --port 12345 open-batch \
    --batch-filter TEST
```

The simulator automatically behaves like an operator who selects batch 1001,
so the client should see:

```text
OpenBatchList_OK
BatchLoaded 1001
```

Then:

```bash
it7-test --host 127.0.0.1 --port 12345 aux \
    --value Aux2="SERIAL-123" \
    --value Aux4="LINE-3"
```

For a more realistic measurement test, use the Python API:

```python
from it7 import IT7Client, IT7Config, SimulatedBatch, SimulatedIT7Server

config = IT7Config(
    host="127.0.0.1",
    it7_receive_port=12345,
)

server = SimulatedIT7Server(
    config,
    batch=SimulatedBatch(batch_id=42),
)

server.start()

try:
    with IT7Client(config) as it7:
        it7.open_batch_list(batch_filter="TEST")
        batch = it7.wait_for_batch_loaded()

        it7.set_aux_data({
            "Aux2": "SERIAL-42",
            "Aux4": "LINE-3",
        })

        # Simulate IT7 finishing the measurement.
        server.emit_measurement_result("OK")

        result = it7.wait_for_measurement_result()
        it7.acknowledge_measurement()
        it7.close_batch(batch.batch_id)

        print(result)
finally:
    server.stop()
```

The simulator deliberately does not pretend to be the real IT7 measurement
engine. It only exercises the UDP protocol and the Balluff-side state machine.


## Full end-to-end harness

Run the complete localhost integration test:

```bash
it7-test e2e
```

This exercises the real `IT7Client` over real UDP sockets against the simulated
IT7 server, with a simulated RFID tag. It tests:

- successful measurement (`OK`);
- rejected measurement (`NOK`);
- operator cancellation (`BatchLoaded -1`);
- auxiliary-data mapping;
- `Measure_OK` acknowledgement;
- batch closure;
- final state transitions.

You can run one scenario:

```bash
it7-test e2e --scenario ok
it7-test e2e --scenario nok
it7-test e2e --scenario cancel
```

And provide simulated RFID values:

```bash
it7-test e2e \
    --serial SERIAL-ABC-123 \
    --batch PART-42 \
    --line LINE-7
```

See `docs/E2E_TESTING.md` for details.


## Fault-injection E2E tests

The simulator can deliberately inject protocol/network failures. Run all
scenarios with:

```bash
it7-test e2e
```

Fault scenarios include:

```text
timeout-open
timeout-batch
timeout-aux
timeout-measure
timeout-close
open-error
aux-error
close-error
malformed-open
malformed-measure
unexpected-open
unexpected-measure
wrong-batch
duplicate-open-ack
duplicate-batch
```

See `docs/FAULT_INJECTION.md` for the test matrix and interpretation.
