"""Subject: the reference `mcp` Python client. Makes ONE tool call and reports what it saw.

Every subject script has the same contract: arguments --mcp-url, --order-id,
--amount-cents, --timeout; exactly one JSON object on the last line of stdout.
The script must not retry. Whatever the client library does on its own is the
thing being measured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from importlib import metadata


def describe(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "group(" + ", ".join(describe(e) for e in exc.exceptions) + ")"
    return f"{type(exc).__module__}.{type(exc).__name__}: {str(exc)[:160]}"


async def call(args: argparse.Namespace) -> dict:
    from mcp import Client

    async with Client(args.mcp_url) as client:
        result = await client.call_tool(
            "issue_refund",
            {"order_id": args.order_id, "amount_cents": args.amount_cents},
            read_timeout_seconds=args.timeout,
        )
    return {"outcome": "error" if result.is_error else "ok", "detail": str(result.structured_content)[:200]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--amount-cents", type=int, default=2500)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    started = time.perf_counter()
    try:
        report = asyncio.run(call(args))
    except BaseException as exc:  # noqa: BLE001 - the report must say what was raised
        report = {"outcome": "raised", "detail": describe(exc)}
    report["elapsed_s"] = round(time.perf_counter() - started, 3)
    report["client"] = "mcp " + metadata.version("mcp")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
