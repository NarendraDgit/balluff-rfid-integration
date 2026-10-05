# Simulated IT7 Test Procedure

## 1. Start simulator

```bash
it7-test --host 127.0.0.1 --port 12345 simulate --verbose
```

Expected:

```text
Simulated IT7 listening on UDP 12345; responses go to 127.0.0.1:12346
```

## 2. Open batch

In another terminal:

```bash
it7-test --host 127.0.0.1 --port 12345 open-batch \
  --batch-filter TEST --node node1 --study 0 --verbose
```

Expected:

```text
OpenBatchList acknowledged; waiting for BatchLoaded...
BatchLoaded 1001
```

## 3. Auxiliary data

```bash
it7-test --host 127.0.0.1 --port 12345 aux \
  --value Aux2="SERIAL-123" \
  --value Aux4="LINE-3" \
  --verbose
```

Expected:

```text
SetAuxData acknowledged
```

## 4. Measurement

For programmatic testing, call:

```python
server.emit_measurement_result("OK")
```

or:

```python
server.emit_measurement_result("NOK")
```

The Balluff client should receive `Measure OK` or `Measure NOK`.

## 5. Acknowledge and close

```bash
it7-test --host 127.0.0.1 --port 12345 measure-ack
it7-test --host 127.0.0.1 --port 12345 close
```

Expected:

```text
Measure_OK sent
CloseBatch acknowledged
```

## 6. Cancellation test

Start with:

```bash
it7-test --host 127.0.0.1 --port 12345 simulate --no-auto-load
```

Then send `open-batch`.

The simulator can be driven from Python with:

```python
server.cancel_batch_selection()
```

The Balluff side should receive:

```text
BatchLoaded -1
```

and return to its idle/cancelled state.
