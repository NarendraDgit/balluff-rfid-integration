# IT7 Integration Bring-up Checklist

## Before connecting

- [ ] Confirm IT7 receive port with Accurate.
- [ ] Confirm that Balluff host listens on IT7 receive port + 1.
- [ ] Confirm exact command separator (`;` vs space).
- [ ] Confirm encoding.
- [ ] Confirm whether one UDP datagram equals one message.
- [ ] Confirm timeout/retry expectations.
- [ ] Confirm `ERRn` meanings.

## Basic connectivity

On the Balluff machine:

```bash
it7-test --host <IT7-IP> --port <IT7-PORT> listen
```

Verify that IT7 can send a message to the Balluff application's `<IT7-PORT + 1>`.

## Batch test

```bash
it7-test --host <IT7-IP> --port <IT7-PORT> open-batch \
    --batch-filter TEST \
    --node node1 \
    --study 0 \
    --verbose
```

Expected flow:

```text
TX OpenBatchList...
RX OpenBatchList_OK
RX BatchLoaded <id>
```

## Auxiliary-data test

```bash
it7-test --host <IT7-IP> --port <IT7-PORT> aux \
    --value Aux2="serial xyz" \
    --value Aux4="line 3" \
    --verbose
```

Expected:

```text
TX SetAuxData...
RX SetAuxData_OK
```

## Measurement test

The operator performs the IT7 measurement.

Expected from IT7:

```text
Measure OK
```

or:

```text
Measure NOK
```

Then send:

```bash
it7-test --host <IT7-IP> --port <IT7-PORT> measure-ack
```

## Close test

```bash
it7-test --host <IT7-IP> --port <IT7-PORT> close
```

Expected:

```text
RX CloseBatch_OK
```
