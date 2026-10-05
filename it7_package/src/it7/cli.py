"""Command-line diagnostic client for IT7 / TQM Balluff."""

from __future__ import annotations

import argparse
import logging

from .client import IT7Client
from .models import IT7Config
from .protocol import IT7Protocol
from .simulator import SimulatedBatch, SimulatedIT7Server
from .e2e import EndToEndHarness, SimulatedRFIDTag, format_results


def _add_common(parser: argparse.ArgumentParser, *, top_level: bool) -> None:
    """Add shared connection args.

    On the top-level parser we register real defaults. On subparsers we use
    argparse.SUPPRESS so that values provided *before* the subcommand are
    preserved. This lets the user write either:

        it7-test --host H --port P open-batch ...
        it7-test open-batch --host H --port P ...
    """
    def default(value):
        return value if top_level else argparse.SUPPRESS

    parser.add_argument("--host", default=default(None),
                        help="IT7 host IP/name")
    parser.add_argument("--port", type=int, default=default(None),
                        help="IT7 command-reception UDP port "
                             "(Balluff listens on port + 1)")
    parser.add_argument("--timeout", type=float, default=default(2.0))
    parser.add_argument("--retries", type=int, default=default(0))
    parser.add_argument("--separator", default=default(";"),
                        help="Parameter separator; default: ';'")
    parser.add_argument("--encoding", default=default("utf-8"),
                        help="Wire encoding; default: utf-8")
    parser.add_argument("--verbose", action="store_true",
                        default=default(False),
                        help="Enable protocol debug logging")


def _require_common(args: argparse.Namespace,
                    parser: argparse.ArgumentParser) -> None:
    if getattr(args, "host", None) is None:
        parser.error("--host is required")
    if getattr(args, "port", None) is None:
        parser.error("--port is required")


def _config(args: argparse.Namespace) -> IT7Config:
    return IT7Config(
        host=args.host,
        it7_receive_port=args.port,
        timeout=args.timeout,
        retries=args.retries,
        encoding=args.encoding,
        parameter_separator=args.separator,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="it7-test",
        description="Diagnostic client for Accurate IT7 / TQM UDP protocol",
    )
    # Top-level: --host/--port etc. can appear *before* the subcommand.
    _add_common(parser, top_level=True)

    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("open-batch", help="Open/filter IT7 batch list")
    _add_common(p, top_level=False)
    p.add_argument("--batch-filter")
    p.add_argument("--program-filter")
    p.add_argument("--no-oper-filter")
    p.add_argument("--node")
    p.add_argument("--study")

    p = subparsers.add_parser("aux", help="Send SetAuxData")
    _add_common(p, top_level=False)
    p.add_argument("--value", action="append", required=True,
                   metavar="AuxN=VALUE")

    p = subparsers.add_parser("close", help="Close IT7 current batch")
    _add_common(p, top_level=False)
    p.add_argument("--batch-id", type=int)

    p = subparsers.add_parser(
        "measure-ack",
        help="Acknowledge an IT7 Measure OK/NOK message",
    )
    _add_common(p, top_level=False)

    p = subparsers.add_parser("listen",
                              help="Listen for and parse IT7 messages")
    _add_common(p, top_level=False)

    p = subparsers.add_parser(
        "simulate",
        help="Run the simulated TQM Balluff UDP server",
    )
    _add_common(p, top_level=False)
    p.add_argument("--batch-id", type=int, default=65)
    p.add_argument("--batch-name", default="TEST")
    p.add_argument("--auto-result", choices=["OK", "NOK"])
    p.add_argument("--node", action="append", default=None,
                   help="Additional configured node; repeatable. "
                        "'node1' is always configured.")
    p.add_argument("--aux-max-count", type=int, default=20)
    p.add_argument("--aux-max-length", type=int, default=40)
    p.add_argument("--loaded-delay", type=float, default=5.0)
    p.add_argument("--closed-delay", type=float, default=5.0)
    p.add_argument("--no-loaded", action="store_true")
    p.add_argument("--no-closed", action="store_true")
    p.add_argument("--no-close-socket", action="store_true")
    p.add_argument("--no-auto-load", action="store_true")
    p.add_argument("--duration", type=float)

    p = subparsers.add_parser(
        "e2e",
        help="Run the full localhost end-to-end harness",
    )
    p.add_argument("--batch-id", type=int, default=65)
    p.add_argument("--serial", default="SERIAL-XYZ")
    p.add_argument("--batch", default="TEST")
    p.add_argument("--line", default="LINE-3")
    p.add_argument("--timeout", type=float, default=1.0)
    p.add_argument("--retries", type=int, default=0)
    p.add_argument("--scenario",
                   choices=["ok", "nok", "cancel", "all"], default="all")
    p.add_argument("--verbose", action="store_true")

    p = subparsers.add_parser(
        "show-command",
        help="Print a generated command",
    )
    p.add_argument("kind",
                   choices=["open-batch", "aux", "close", "measure-ack"])
    p.add_argument("--separator", default=";")
    p.add_argument("--batch-filter")
    p.add_argument("--program-filter")
    p.add_argument("--no-oper-filter")
    p.add_argument("--node")
    p.add_argument("--study")
    p.add_argument("--batch-id", type=int)
    p.add_argument("--value", action="append", metavar="AuxN=VALUE")

    return parser


def _parse_aux_values(items: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --value {item!r}; expected AuxN=VALUE")
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        values[name] = value
    return values


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "e2e":
        harness = EndToEndHarness(
            batch_id=args.batch_id,
            timeout=args.timeout,
            retries=args.retries,
        )
        tag = SimulatedRFIDTag(
            serial_number=args.serial,
            batch=args.batch,
            line=args.line,
        )
        if args.scenario == "all":
            results = harness.run_all(tag=tag)
        else:
            from .e2e import Scenario
            results = [harness.run(Scenario(args.scenario), tag=tag)]
        print(format_results(results))
        return 0 if all(r.passed for r in results) else 1

    if args.command == "show-command":
        if args.kind == "open-batch":
            print(IT7Protocol.open_batch_list(
                batch_filter=args.batch_filter,
                program_filter=args.program_filter,
                no_oper_filter=args.no_oper_filter,
                node=args.node,
                study=args.study,
                separator=args.separator,
            ))
        elif args.kind == "aux":
            if not args.value:
                raise SystemExit("--value is required for aux")
            print(IT7Protocol.set_aux_data(
                _parse_aux_values(args.value), separator=args.separator,
            ))
        elif args.kind == "close":
            print(IT7Protocol.close_batch(args.batch_id))
        else:
            print(IT7Protocol.measurement_ack())
        return 0

    # All remaining subcommands need host/port.
    _require_common(args, parser)
    config = _config(args)

    if args.command == "simulate":
        import time
        nodes = {"node1"}
        if args.node:
            nodes.update(args.node)
        batch = SimulatedBatch(batch_id=args.batch_id, name=args.batch_name)
        server = SimulatedIT7Server(
            config,
            batch=batch,
            configured_nodes=nodes,
            aux_max_count=args.aux_max_count,
            aux_max_length=args.aux_max_length,
            loaded_delay=args.loaded_delay,
            closed_delay=args.closed_delay,
            send_loaded_on_open=not (args.no_loaded or args.no_auto_load),
            send_closed_on_close=not args.no_closed,
            send_close_socket_on_stop=not args.no_close_socket,
            auto_result=args.auto_result,
        )
        server.start()
        print(
            f"Simulated TQM listening on UDP {config.it7_receive_port}; "
            f"responses go to {config.host}:{config.balluff_port}"
        )
        print(f"Configured nodes: {sorted(nodes)}")
        print("Press Ctrl-C to stop.")
        try:
            if args.duration is not None:
                time.sleep(args.duration)
            else:
                while True:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
        return 0

    with IT7Client(config) as it7:
        if args.command == "open-batch":
            it7.open_batch_list(
                batch_filter=args.batch_filter,
                program_filter=args.program_filter,
                no_oper_filter=args.no_oper_filter,
                node=args.node,
                study=args.study,
            )
            print("OpenBatchList acknowledged; waiting for LOADED...")
            batch = it7.wait_for_batch_loaded()
            print(f"LOADED {batch.batch_id}")
        elif args.command == "aux":
            it7.set_aux_data(_parse_aux_values(args.value))
            print("SetAuxData acknowledged")
        elif args.command == "close":
            it7.close_batch(args.batch_id)
            print("CloseBatch acknowledged")
        elif args.command == "measure-ack":
            it7.acknowledge_measurement()
            print("Measure_OK sent")
        elif args.command == "listen":
            print(f"Listening on UDP {config.balluff_port}. Ctrl-C to stop.")
            while True:
                raw = it7.transport.receive(timeout=None)
                print(f"RX: {raw}")
                try:
                    print(f"    parsed: {IT7Protocol.parse(raw)!r}")
                except Exception as exc:
                    print(f"    parse error: {exc}")

    return 0