# Full End-to-End Testing

The E2E harness uses the actual `IT7Client` and real localhost UDP sockets.
Only the IT7 Measure side and RFID reader are simulated.

## Architecture

```text
Simulated RFID tag
       |
       v
MeasurementSession
       |
       v
   IT7Client
       |
      UDP
       |
       v
SimulatedIT7Server
```

## Run all scenarios

After installing the package:

```bash
it7-test e2e
```

Expected output:

```text
IT7 END-TO-END TEST REPORT
==========================
PASS  OK       state=COMPLETE  batch=1001  result=OK
PASS  NOK      state=COMPLETE  batch=1001  result=NOK
PASS  CANCEL   state=CANCELLED  batch=-1

Passed: 3/3
```

## Run one scenario

```bash
it7-test e2e --scenario ok
it7-test e2e --scenario nok
it7-test e2e --scenario cancel
```

## Supply simulated RFID values

```bash
it7-test e2e \
    --serial SERIAL-ABC-123 \
    --batch PART-42 \
    --line LINE-7
```

The current test mapping is:

```text
RFID serial -> Aux2
RFID line   -> Aux4
```

This mapping is only a test assumption. It should be replaced with the
mapping agreed with Accurate.

## What the E2E test verifies

### OK path

1. RFID tag is supplied to the application.
2. Application sends `OpenBatchList`.
3. Simulated IT7 replies `OpenBatchList_OK`.
4. Simulated operator selects batch 1001.
5. IT7 sends `BatchLoaded 1001`.
6. Application sends `SetAuxData`.
7. Simulated IT7 replies `SetAuxData_OK`.
8. Simulated IT7 completes a measurement with `Measure OK`.
9. Application sends `Measure_OK`.
10. Application sends `CloseBatch 1001`.
11. Simulated IT7 replies `CloseBatch_OK`.
12. Session reaches `COMPLETE`.

### NOK path

Same as OK, except IT7 emits:

```text
Measure NOK
```

The workflow must still acknowledge the result and close the batch.

### Cancel path

1. Application sends `OpenBatchList`.
2. Simulated IT7 acknowledges it.
3. Simulated operator closes the Open Batch window.
4. IT7 sends `BatchLoaded -1`.
5. Application reaches `CANCELLED`.
6. No auxiliary data is sent.
7. No measurement/close operation is attempted.

## Run tests

```bash
pytest
```

The E2E tests are intentionally separate from protocol unit tests because they
exercise real UDP sockets and therefore test actual network behavior.
