"""Subject: CrewAI's native MCP client (`crewai.mcp.MCPClient`). ONE call, no retry here.

--timeout sets `execution_timeout`; pass 0 to keep CrewAI's default (30 s).
`max_retries` is always left at CrewAI's default.
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

# This file is named crewai.py; keep its own directory off the import path so
# `import crewai` finds the installed package and not this script.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]


def describe(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "group(" + ", ".join(describe(e) for e in exc.exceptions) + ")"
    return f"{type(exc).__module__}.{type(exc).__name__}: {str(exc)[:160]}"


async def call(args: argparse.Namespace) -> dict:
    from crewai.mcp import MCPClient
    from crewai.mcp.transports.http import HTTPTransport

    options = {"execution_timeout": int(args.timeout)} if args.timeout > 0 else {}
    client = MCPClient(HTTPTransport(url=args.mcp_url), **options)
    report = {"max_retries": client.max_retries, "execution_timeout": client.execution_timeout}
    async with client:
        result = await client.call_tool_result(
            "issue_refund", {"order_id": args.order_id, "amount_cents": args.amount_cents}
        )
    report.update(outcome="error" if result.is_error else "ok", detail=str(result.content)[:200])
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
    report["client"] = f"crewai {metadata.version('crewai')} (mcp {metadata.version('mcp')})"
    print(json.dumps(report))


if __name__ == "__main__":
    main()
