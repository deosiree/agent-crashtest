"""Subject: Microsoft agent-framework's `MCPStreamableHTTPTool`. ONE call, no retry here.

--timeout sets `request_timeout`; pass 0 to keep the framework's default (30 s).
See subjects/mcp_sdk.py for the contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from importlib import metadata
from pathlib import Path

# This file is named agent_framework.py; keep its own directory off the import
# path so `import agent_framework` finds the installed package.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]


def describe(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "group(" + ", ".join(describe(e) for e in exc.exceptions) + ")"
    inner = f" <- {describe(exc.__cause__)}" if exc.__cause__ is not None else ""
    return f"{type(exc).__module__}.{type(exc).__name__}: {str(exc)[:120]}{inner}"[:400]


async def call(args: argparse.Namespace) -> dict:
    from agent_framework import MCPStreamableHTTPTool

    options = {"request_timeout": int(args.timeout)} if args.timeout > 0 else {}
    tool = MCPStreamableHTTPTool(name="refunds", url=args.mcp_url, **options)
    report = {"request_timeout": tool.request_timeout}
    async with tool:
        result = await tool.call_tool(
            "issue_refund", order_id=args.order_id, amount_cents=args.amount_cents
        )
    report.update(outcome="ok", detail=str(result)[:200])
    return report


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
    report["client"] = f"agent-framework-core {metadata.version('agent-framework-core')} (mcp {metadata.version('mcp')})"
    print(json.dumps(report))


if __name__ == "__main__":
    main()
