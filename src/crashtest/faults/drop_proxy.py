"""A TCP proxy that cuts one connection after the effect has committed.

It sits between the MCP client and the MCP server and forwards bytes untouched.
When armed for an order, it watches for the request that mentions that order,
holds back everything the server sends on that connection, waits until the sink
reports the refund as committed, and then aborts the client's connection. The
client never receives a single response byte for that call.

This is the "connection drop" form of the lost-response fault: the response is
not late, it is gone.

Two drop points (experiment 007 added the second):

  no_bytes       nothing the server sends on the connection reaches the client
                 (experiments 002 and 003)
  after_headers  the response headers, and every complete SSE event that carries
                 no JSON-RPC message (an event store's priming event), reach the
                 client; from the first JSON-RPC message on, nothing does. The
                 cut comes when that message arrives from the server, which is
                 after the commit, and is still checked against the sink.

The proxy also counts client requests that carry a `Last-Event-ID` header: a
client resuming a stream, as the 2025-11-25 MCP transport describes.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

import httpx

DropPoint = Literal["no_bytes", "after_headers"]
DROP_POINTS: tuple[DropPoint, ...] = ("no_bytes", "after_headers")

# Any JSON-RPC message carries this member; headers and priming events do not.
_JSONRPC_MARKER = b'"jsonrpc"'
_LAST_EVENT_ID = b"\nlast-event-id:"
_EVENT_ID_LINE = re.compile(rb"(?m)^id:")


def forwardable_prefix(buffer: bytes) -> tuple[int, bool]:
    """How much of the server's bytes the `after_headers` drop point lets through.

    Returns (end, found): `end` is the end of the last blank-line-terminated block
    (the header block, or a whole SSE event) that lies before the first JSON-RPC
    message, or before the end of the buffer if there is none yet; `found` says
    whether a JSON-RPC message has started. A block still incomplete is held back,
    so a message split across reads is never let through in part.
    """
    marker = buffer.find(_JSONRPC_MARKER)
    limit = marker if marker != -1 else len(buffer)
    end = 0
    for sep in (b"\r\n\r\n", b"\n\n"):
        index = buffer.rfind(sep, 0, limit)
        if index != -1:
            end = max(end, index + len(sep))
    return end, marker != -1


def count_event_ids(data: bytes) -> int:
    """SSE event IDs (lines starting `id:`) in a stretch of the server's bytes."""
    return len(_EVENT_ID_LINE.findall(data))


def _wire_form(order_id: str) -> bytes:
    """The order id as a complete JSON string value.

    Matching the quotes too means a value that merely contains the id, such as
    the key "refund-<order id>", is not counted as a second request.
    """
    return b'"' + order_id.encode() + b'"'


class _Scanner:
    """Finds byte patterns in a stream, including ones split across two reads."""

    TAIL = 255

    def __init__(self) -> None:
        self._tail = b""

    def feed(self, data: bytes, patterns: list[bytes]) -> list[bytes]:
        """Return each pattern once per new occurrence in the stream so far."""
        window = self._tail + data
        found: list[bytes] = []
        for pattern in patterns:
            start = 0
            while (index := window.find(pattern, start)) != -1:
                # An occurrence lying wholly inside the old tail was reported last time.
                if index + len(pattern) > len(self._tail):
                    found.append(pattern)
                start = index + 1
        self._tail = window[-self.TAIL :]
        return found


@dataclass
class DropRecord:
    order_id: str
    committed_before_drop: bool
    bytes_withheld: int
    drop_point: DropPoint = "no_bytes"
    # What reached the client on the cut connection after the call was sent.
    bytes_forwarded: int = 0
    event_ids_forwarded: int = 0


@dataclass
class DropProxy:
    upstream_url: str  # for example http://127.0.0.1:8102/mcp
    sink_url: str
    commit_wait_s: float = 10.0
    poll_interval_s: float = 0.02
    drops: list[DropRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        parts = urlsplit(self.upstream_url)
        if parts.hostname is None or parts.port is None:
            raise ValueError(f"upstream URL needs a host and a port: {self.upstream_url}")
        self._upstream_host: str = parts.hostname
        self._upstream_port: int = parts.port
        self._path = parts.path
        self._armed: dict[bytes, DropPoint] = {}
        self._watched: dict[bytes, str] = {}  # quoted id as sent on the wire -> order id
        self.calls_seen: dict[str, int] = {}
        # Client requests carrying a Last-Event-ID header, over the proxy's life.
        self.resume_requests = 0
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        self.url = ""

    async def __aenter__(self) -> DropProxy:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}{self._path}"
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    def arm(self, order_id: str, drop_point: DropPoint = "no_bytes") -> None:
        """Drop the next connection that carries a request for this order."""
        if drop_point not in DROP_POINTS:
            raise ValueError(f"unknown drop point {drop_point!r}")
        self.watch(order_id)
        self._armed[_wire_form(order_id)] = drop_point

    def watch(self, order_id: str) -> None:
        """Start counting the requests clients send for this order.

        This is a second counting point, independent of the sink: it sees what the
        client put on the wire, whether or not the MCP server passed it on.
        """
        self.calls_seen.setdefault(order_id, 0)
        self._watched[_wire_form(order_id)] = order_id

    def unwatch(self, order_id: str) -> int:
        """Stop counting for this order and return the number of requests seen."""
        self._watched.pop(_wire_form(order_id), None)
        self._armed.pop(_wire_form(order_id), None)
        return self.calls_seen.pop(order_id, 0)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        try:
            up_reader, up_writer = await asyncio.open_connection(
                self._upstream_host, self._upstream_port
            )
        except OSError:
            writer.transport.abort()
            return

        target: dict[str, str | None] = {"order": None, "point": None}
        scanner = _Scanner()
        header_scanner = _Scanner()

        async def client_to_upstream() -> None:
            while data := await reader.read(65536):
                self.resume_requests += len(header_scanner.feed(data.lower(), [_LAST_EVENT_ID]))
                if self._watched:
                    for wire in scanner.feed(data, list(self._watched)):
                        order_id = self._watched.get(wire)
                        if order_id is None:
                            continue
                        self.calls_seen[order_id] = self.calls_seen.get(order_id, 0) + 1
                        if target["order"] is None and wire in self._armed:
                            target["point"] = self._armed.pop(wire)
                            target["order"] = order_id
                up_writer.write(data)
                await up_writer.drain()

        async def upstream_to_client() -> None:
            pending = b""  # after_headers: server bytes not yet let through
            forwarded = 0
            event_ids = 0
            while data := await up_reader.read(65536):
                order = target["order"]
                if order is None:
                    writer.write(data)
                    await writer.drain()
                    continue
                if target["point"] == "after_headers":
                    pending += data
                    end, found = forwardable_prefix(pending)
                    if end:
                        writer.write(pending[:end])
                        await writer.drain()
                        forwarded += end
                        event_ids += count_event_ids(pending[:end])
                        pending = pending[end:]
                    if not found:
                        continue
                    data = pending
                committed = await self._wait_for_commit(order)
                self.drops.append(
                    DropRecord(
                        order,
                        committed,
                        len(data),
                        drop_point=target["point"] or "no_bytes",  # type: ignore[arg-type]
                        bytes_forwarded=forwarded,
                        event_ids_forwarded=event_ids,
                    )
                )
                return  # the finally block below aborts both sides

        pumps = [
            asyncio.create_task(client_to_upstream()),
            asyncio.create_task(upstream_to_client()),
        ]
        try:
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            # abort(), not close(): nothing buffered is flushed to the client.
            writer.transport.abort()
            up_writer.transport.abort()

    async def _wait_for_commit(self, order_id: str) -> bool:
        """Poll the sink until the refund for this order exists."""
        deadline = asyncio.get_running_loop().time() + self.commit_wait_s
        async with httpx.AsyncClient(base_url=self.sink_url, timeout=5.0) as sink:
            while asyncio.get_running_loop().time() < deadline:
                r = await sink.get("/refunds", params={"order_id": order_id})
                if r.status_code == 200 and r.json()["refunds"]:
                    return True
                await asyncio.sleep(self.poll_interval_s)
        return False
