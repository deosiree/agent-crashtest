"""`crashtest`: crash-test your own MCP client against a fake refund tool.

    crashtest serve                     start the sink and the MCP server locally
    crashtest arm ORDER_ID [--delay 5]  make the next refund for ORDER_ID lose its response
    crashtest verdict ORDER_ID          count the refunds and judge them

The MCP server has one tool, `issue_refund(order_id, amount_cents, idempotency_key?)`.
It is a test double: it writes a row to a local PostgreSQL database and moves no
real money. With a fault armed, the sink commits the refund and then holds its
response past your client's timeout, so your client never hears back. What your
client does next (retry, give up, report) decides how many refunds the sink holds.
The verdict comes from the project's checker (`crashtest.checker`).

`serve` writes the URLs it prints to `.crashtest-stack.json` in the current
folder; `arm` and `verdict` read them from there, unless `--sink-url` or the
`CRASHTEST_SINK_URL` environment variable says otherwise. Without either, they
use `http://localhost:8101`, the Docker Compose stack's sink.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from crashtest.checker import (
    Report,
    SubjectStatus,
    classify,
    payload_matches,
    report_matches_sink,
)

STATE_FILE = Path(".crashtest-stack.json")
DOCKER_SINK_URL = "http://localhost:8101"
DEFAULT_AMOUNT_CENTS = 2500

# What a client told its caller, and the run status the checker reads from it.
STATUS_OF_REPORT: dict[str, SubjectStatus] = {
    "done": "completed",
    "unknown": "unknown",
    "not_done": "failed",
    "none": "failed",
}


def _sink_url(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    if env := os.environ.get("CRASHTEST_SINK_URL"):
        return env.rstrip("/")
    if STATE_FILE.is_file():
        return str(json.loads(STATE_FILE.read_text(encoding="utf-8"))["sink_url"]).rstrip("/")
    return DOCKER_SINK_URL


def _idle() -> None:
    """One second of waiting while the stack serves (replaced in the tests)."""
    time.sleep(1)


def cmd_serve(args: argparse.Namespace) -> int:
    from crashtest.experiment.local_stack import local_stack

    data_dir = Path(args.data_dir).resolve()
    print(f"Starting PostgreSQL ({data_dir}), the sink and the MCP server...", flush=True)
    with local_stack(data_dir) as stack:
        STATE_FILE.write_text(
            json.dumps({"sink_url": stack.sink_url, "mcp_url": stack.mcp_url}, indent=2),
            encoding="utf-8",
        )
        print(
            f"\nMCP server (streamable HTTP): {stack.mcp_url}\n"
            f"Sink:                         {stack.sink_url}\n\n"
            "Point your MCP client at the MCP server; its tool is issue_refund.\n"
            "In another terminal, in this folder:\n"
            "  crashtest arm ORDER_ID      the next refund for ORDER_ID loses its response\n"
            "  crashtest verdict ORDER_ID  how many refunds the sink holds, and the verdict\n\n"
            "Press Ctrl+C to stop.",
            flush=True,
        )
        try:
            while True:
                _idle()
        except KeyboardInterrupt:
            print("\nStopping.", flush=True)
        finally:
            STATE_FILE.unlink(missing_ok=True)
    return 0


def cmd_arm(args: argparse.Namespace) -> int:
    sink = _sink_url(args.sink_url)
    r = httpx.put(
        f"{sink}/control/faults",
        json={"order_id": args.order_id, "delay_s": args.delay, "times": args.times},
        timeout=10.0,
    )
    r.raise_for_status()
    print(
        f"Armed: the next {args.times} refund(s) for {args.order_id} commit, then their "
        f"response is held for {args.delay:g} s. Set your client's timeout below that."
    )
    return 0


def judge(truth: dict, *, order_id: str, amount_cents: int, report: Report) -> dict:
    """The checker's verdict for one order, from the sink's rows and what the client said."""
    effects = len(truth["refunds"])
    status = STATUS_OF_REPORT[report]
    verdict = classify(
        effects=effects,
        intended=1,
        status=status,
        payload_matches=payload_matches(
            truth["refunds"], order_id=order_id, amount_cents=amount_cents
        ),
        confirmed=status == "completed",
    )
    return {
        "order_id": order_id,
        "refunds": effects,
        "requests": len(truth["requests"]),
        "client_report": report,
        "verdict": verdict,
        "report_matches_sink": report_matches_sink(effects=effects, intended=1, report=report),
    }


def cmd_verdict(args: argparse.Namespace) -> int:
    sink = _sink_url(args.sink_url)
    r = httpx.get(f"{sink}/refunds", params={"order_id": args.order_id}, timeout=10.0)
    r.raise_for_status()
    result = judge(
        r.json(), order_id=args.order_id, amount_cents=args.amount_cents, report=args.report
    )
    if args.json:
        print(json.dumps(result))
    else:
        print(
            f"order {result['order_id']}: {result['refunds']} refund(s) committed, "
            f"{result['requests']} refund request(s) received\n"
            f"verdict: {result['verdict']}\n"
            f"what the client said ({result['client_report']}) agrees with the sink: "
            f"{result['report_matches_sink']}"
        )
    return 0 if result["verdict"] == "correct" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crashtest", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the sink and the MCP server on this machine")
    serve.add_argument(
        "--data-dir", default=".crashtest-pgdata", help="where PostgreSQL keeps its data"
    )
    serve.set_defaults(func=cmd_serve)

    arm = sub.add_parser("arm", help="make the next refund for an order lose its response")
    arm.add_argument("order_id")
    arm.add_argument("--delay", type=float, default=5.0, help="seconds the response is held")
    arm.add_argument("--times", type=int, default=1, help="how many refunds it applies to")
    arm.add_argument("--sink-url")
    arm.set_defaults(func=cmd_arm)

    verdict = sub.add_parser("verdict", help="count an order's refunds and judge them")
    verdict.add_argument("order_id")
    verdict.add_argument(
        "--report",
        choices=tuple(STATUS_OF_REPORT),
        default="done",
        help="what your client told its caller: done (the default), unknown, not_done, none",
    )
    verdict.add_argument("--amount-cents", type=int, default=DEFAULT_AMOUNT_CENTS)
    verdict.add_argument("--sink-url")
    verdict.add_argument("--json", action="store_true", help="print one JSON object")
    verdict.set_defaults(func=cmd_verdict)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except httpx.HTTPError as exc:
        print(f"crashtest: cannot reach the sink: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
