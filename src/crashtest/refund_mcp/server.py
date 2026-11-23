"""MCP server exposing one side-effecting tool, `issue_refund`, backed by the sink.

Run with:  python -m crashtest.refund_mcp.server
Env:       SINK_URL (default http://127.0.0.1:8101), MCP_HOST, MCP_PORT,
           MCP_STATELESS and MCP_JSON_RESPONSE (both default true),
           MCP_EVENT_STORE (default false: an in-memory event store, for
           resumable SSE streams; used only by a stateful server),
           MCP_PROGRESS_DELAY_S (default unset: when set, the tool first sends
           one progress notification, then waits this long before it calls
           the sink; experiment 008's mid-stream cut needs bytes on the
           stream before the commit)

The server adds no deduplication of its own. It forwards an optional
idempotency key to the sink as a header, the way a payments client would.
"""

from __future__ import annotations

import asyncio
import os
from typing import Literal, cast, get_args

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from crashtest.refund_mcp.event_store import InMemoryEventStore

SINK_URL = os.environ.get("SINK_URL", "http://127.0.0.1:8101")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _log_level(value: str) -> LogLevel:
    level = value.upper()
    if level not in get_args(LogLevel):
        raise ValueError(f"MCP_LOG_LEVEL must be one of {get_args(LogLevel)}, got {value!r}")
    return cast(LogLevel, level)


def _progress_delay(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    delay = float(value)
    if delay < 0:
        raise ValueError(f"MCP_PROGRESS_DELAY_S must not be negative, got {value!r}")
    return delay


# Experiment 008. None (the default, and experiments 001 to 007): no progress
# notification and no pause, so the tool behaves exactly as before.
PROGRESS_DELAY_S = _progress_delay(os.environ.get("MCP_PROGRESS_DELAY_S"))

mcp = MCPServer("refund-service", log_level=_log_level(os.environ.get("MCP_LOG_LEVEL", "WARNING")))


class RefundResult(BaseModel):
    """Typed result, so the SDK returns structured content and not only text."""

    refund_id: int
    order_id: str
    amount_cents: int
    replayed: bool


@mcp.tool()
async def issue_refund(
    order_id: str,
    amount_cents: int,
    idempotency_key: str | None = None,
    ctx: Context | None = None,
) -> RefundResult:
    """Refund an order. This moves money and must not be repeated for the same order.

    Args:
        order_id: The order to refund.
        amount_cents: Amount to refund, in cents.
        idempotency_key: Optional. A caller-chosen key identifying this one refund
            operation. Calls repeating the same key return the original refund.
    """
    if PROGRESS_DELAY_S is not None and ctx is not None:
        # Sent only if the client asked for progress (a progress token); either
        # way the pause comes before the sink call, so before the commit.
        await ctx.report_progress(0, 1, "refund started")
        await asyncio.sleep(PROGRESS_DELAY_S)
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{SINK_URL}/refunds",
            json={"order_id": order_id, "amount_cents": amount_cents},
            headers=headers,
        )
    response.raise_for_status()
    return RefundResult.model_validate(response.json())


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def main() -> None:
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8102"))
    mcp.run(
        "streamable-http",
        host=host,
        port=port,
        # Default: one request, one JSON response, no server-side session. That is
        # the simplest transport behaviour, so the baseline measures retries and
        # nothing else. Experiment 002 turns each of these off in turn.
        stateless_http=_env_flag("MCP_STATELESS", True),
        json_response=_env_flag("MCP_JSON_RESPONSE", True),
        # Experiment 007: with a store, an SSE response opens with a priming event
        # (an event ID, no data) and a client that loses the stream can resume it
        # with Last-Event-ID. `retry_interval` is left unset, so the client waits
        # its own default before reconnecting.
        event_store=InMemoryEventStore() if _env_flag("MCP_EVENT_STORE", False) else None,
        # Host-header checking is off because this is a local test rig reached
        # by container name inside Docker. Do not copy this to a real server.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


if __name__ == "__main__":
    main()
