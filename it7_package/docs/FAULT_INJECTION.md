# IT7 Fault-Injection Test Matrix

The E2E harness deliberately injects failures into the simulated IT7 side.
These tests exercise the actual UDP transport, `IT7Client`, and
`MeasurementSession`.

Run everything:

```bash
it7-test e2e
```

## Scenario matrix

| Scenario | Injected condition | Expected client behavior |
|---|---|---|
| `ok` | normal measurement | `COMPLETE`, result OK |
| `nok` | rejected measurement | `COMPLETE`, result NOK |
| `cancel` | operator sends `BatchLoaded -1` | `CANCELLED` |
| `timeout-open` | `OpenBatchList_OK` lost | timeout/error |
| `timeout-batch` | `BatchLoaded` lost | timeout/error |
| `timeout-aux` | `SetAuxData_OK` lost | timeout/error |
| `timeout-measure` | `Measure OK/NOK` lost | timeout/error |
| `timeout-close` | `CloseBatch_OK` lost | timeout/error |
| `open-error` | `OpenBatchList_ERR1` | protocol error |
| `aux-error` | `SetAuxData_ERR1` | protocol error |
| `close-error` | `CloseBatch_ERR1` | protocol error |
| `malformed-open` | malformed response | protocol error |
| `malformed-measure` | malformed measurement result | protocol error |
| `unexpected-open` | syntactically valid but unrelated response during open | ignored, then timeout |
| `unexpected-measure` | wrong response during measurement | protocol error |
| `wrong-batch` | unexpected BatchID | application detects mismatch |
| `duplicate-open-ack` | duplicate `OpenBatchList_OK` | tolerated |
| `duplicate-batch` | duplicate `BatchLoaded` | tolerated |

## Why these tests matter

UDP does not provide delivery guarantees. The Accurate specification says that
commands have confirmation responses, but it does not define timeout,
retry, duplicate handling, or recovery behavior. These tests therefore expose
what the current package actually does rather than pretending those policies
are defined by Accurate.

### Important current behavior

The current client:

- raises `IT7TimeoutError` when an expected response does not arrive;
- raises `IT7ErrorResponse` for an explicit `*_ERRn`;
- raises `IT7ProtocolError` for malformed protocol messages; a syntactically valid but unrelated simple response during a command exchange is currently ignored until the request times out;
- tolerates unrelated simple responses while waiting for some expected
  responses;
- does not yet implement an automatic recovery state machine after an error;
- does not yet implement a durable event queue for asynchronous messages.

These are deliberate, testable behaviors and should be reviewed before
production deployment.

## Run individual scenarios

```bash
it7-test e2e --scenario timeout-open
it7-test e2e --scenario timeout-measure
it7-test e2e --scenario aux-error
it7-test e2e --scenario malformed-measure
```

Verbose logging:

```bash
it7-test e2e --scenario timeout-open --verbose
```

## Recommended next production decisions

Before enabling automatic retries/recovery, agree with Accurate on:

1. whether commands are idempotent;
2. whether duplicate commands are safe;
3. timeout values;
4. retry count;
5. how a partially completed measurement should be recovered;
6. whether an explicit `CloseBatch` is required after a failure;
7. whether IT7 can emit delayed messages after the client has timed out.
