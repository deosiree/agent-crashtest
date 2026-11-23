"""A TCP proxy that cuts one client connection at a chosen point.

It sits between the MCP client and the MCP server and forwards bytes untouched.
When armed for an order, it watches for the request that mentions that order,
holds back everything the server sends on that connection, waits until the sink
reports the refund as committed, and then aborts the client's connection. The
client never receives a single response byte for that call.

This is the "connection drop" form of the lost-response fault: the response is
not late, it is gone.

Four drop points (experiment 007 added the second, 008 the last two):

  no_bytes        nothing the server sends on the connection reaches the client;
                  the cut waits for the commit (experiments 002 and 003)
  after_headers   the response headers, and every complete SSE event that carries
                  no JSON-RPC message (an event store's priming event), reach the
                  client; from the first JSON-RPC message on, nothing does. The
                  cut comes when that message arrives from the server, which is
                  after the commit, and is still checked against the sink.
  before_request  the client's bytes that carry the call are never forwarded:
                  the server does not receive the request, and nothing commits.
                  The cut comes at once; the sink is read to check it is empty.
  mid_stream      the response headers and complete SSE events reach the client
                  up to and including the first JSON-RPC notification (a
                  progress event); the cut comes right after it, without waiting
                  for anything. A response arriving first is withheld, and the
                  record says so. The sink is read at the cut.

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

DropPoint = Literal["no_bytes", "after_headers", "before_request", "mid_stream"]
DROP_POINTS: tuple[DropPoint, ...] = ("no_bytes", "after_headers", "before_request", "mid_stream")
# The points that cut after the commit, and wait for it.
AFTER_COMMIT_POINTS: tuple[DropPoint, ...] = ("no_bytes", "after_headers")

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


_BLOCK_ENDS = (b"\r\n\r\n", b"\n\n")


def _block_end(buffer: bytes, start: int) -> int:
    """End of the blank-line-terminated block containing `start`, or -1 if incomplete."""
    ends = [i + len(sep) for sep in _BLOCK_ENDS if (i := buffer.find(sep, start)) != -1]
    return min(ends) if ends else -1


def mid_stream_prefix(buffer: bytes) -> tuple[int, str | None]:
    """How much of the server's bytes the `mid_stream` drop point lets through.

    Returns (end, first). `first` is None while no complete JSON-RPC message has
    arrived, and `end` is then what `forwardable_prefix` lets through. It is
    "notification" when the first message carries a method and no result or
    error: that message is let through, `end` being the end of its event. It is
    "response" when the first message is a result or an error: that is held
    back, `end` being the end of the last block before it.
    """
    marker = buffer.find(_JSONRPC_MARKER)
    if marker == -1:
        return forwardable_prefix(buffer)[0], None
    message_end = _block_end(buffer, marker)
    before, _ = forwardable_prefix(buffer[:marker])
    if message_end == -1:
        return before, None
    message = buffer[marker:message_end]
    if b'"method"' in message and b'"result"' not in message and b'"error"' not in message:
        return message_end, "notification"
    return before, "response"


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
    # Experiment 008. before_request: the client's bytes carrying the call were
    # not forwarded. mid_stream: JSON-RPC notifications let through, and whether
    # the response was (it never is: a response arriving first is withheld).
    request_withheld: bool = False
    notifications_forwarded: int = 0
    response_forwarded: bool = False
    # The sink's request log and refunds for the order, read at the cut (the
    # before_request and mid_stream points; None for the after-commit points,
    # which wait for the commit instead).
    sink_requests_at_cut: int | None = None
    sink_refunds_at_cut: int | None = None

    def landed_as_intended(self) -> bool:
        """Did the cut land where its drop point says (the trial's `fault_confirmed`)?

        After-commit points: the sink held the refund when the connection was cut.
        before_request: the call was withheld and the sink had neither a request
        nor a refund for the order at the cut. mid_stream: at least one
        notification reached the client, the response did not, and the sink held
        no refund at the cut.
        """
        if self.drop_point in AFTER_COMMIT_POINTS:
            return self.committed_before_drop
        if self.drop_point == "before_request":
            return (
                self.request_withheld
                and self.sink_requests_at_cut == 0
                and self.sink_refunds_at_cut == 0
            )
        return (
            self.notifications_forwarded >= 1
            and not self.response_forwarded
            and self.sink_refunds_at_cut == 0
        )


# The proxy's client to the sink drops idle connections after this long, well before
# the sink's server does (uvicorn closes an idle keep-alive connection after 5 s). A
# read that reused a connection the server had just closed would otherwise fail.
SINK_KEEPALIVE_S = 1.0


def _new_sink_client(sink_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=sink_url,
        timeout=5.0,
        # httpx's default pool limits, kept; only the idle expiry is changed.
        limits=httpx.Limits(
            max_connections=100, max_keepalive_connections=20, keepalive_expiry=SINK_KEEPALIVE_S
        ),
    )


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
        # Sink reads retried after a dropped connection, over the proxy's life.
        self.sink_retries = 0
        self._server: asyncio.Server | None = None
        # One client to the sink for the proxy's life. Creating a client loads the
        # certificate bundle (about 150 ms, and several times that on a loaded
        # machine), which raced a client's timeout when it was done per commit wait.
        self._sink: httpx.AsyncClient | None = None
        self._tasks: set[asyncio.Task] = set()
        self.url = ""

    async def __aenter__(self) -> DropProxy:
        self._sink = _new_sink_client(self.sink_url)
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
        if self._sink is not None:
            await self._sink.aclose()
            self._sink = None

    def _sink_client(self) -> httpx.AsyncClient:
        if self._sink is None:  # used outside `async with`: open one and keep it
            self._sink = _new_sink_client(self.sink_url)
        return self._sink

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
                if target["point"] == "before_request" and target["order"] is not None:
                    # The bytes carrying the call stop here; the server never sees them.
                    order = target["order"]
                    requests, refunds = await self._read_sink(order)
                    self.drops.append(
                        DropRecord(
                            order,
                            committed_before_drop=refunds > 0,
                            bytes_withheld=len(data),
                            drop_point="before_request",
                            request_withheld=True,
                            sink_requests_at_cut=requests,
                            sink_refunds_at_cut=refunds,
                        )
                    )
                    return  # the finally block below aborts both sides
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
                if target["point"] == "mid_stream":
                    pending += data
                    end, first = mid_stream_prefix(pending)
                    if end:
                        writer.write(pending[:end])
                        await writer.drain()
                        forwarded += end
                        event_ids += count_event_ids(pending[:end])
                        pending = pending[end:]
                    if first is None:
                        continue
                    # Cut now, whatever the server is doing: no wait for a commit.
                    requests, refunds = await self._read_sink(order)
                    self.drops.append(
                        DropRecord(
                            order,
                            committed_before_drop=refunds > 0,
                            bytes_withheld=len(pending),
                            drop_point="mid_stream",
                            bytes_forwarded=forwarded,
                            event_ids_forwarded=event_ids,
                            notifications_forwarded=int(first == "notification"),
                            response_forwarded=False,
                            sink_requests_at_cut=requests,
                            sink_refunds_at_cut=refunds,
                        )
                    )
                    return
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

    async def _sink_get(self, order_id: str) -> httpx.Response:
        """GET /refunds for the order, retried once if the connection was dropped.

        Only connection-level failures are retried (a pooled connection the server
        had closed raises RemoteProtocolError or a NetworkError); a timeout or an HTTP
        error status is not, so a slow or failing sink still shows as one. If the
        retry fails too, the error propagates as before: the cut then happens with no
        DropRecord, and the trial's fault_confirmed reads false.
        """
        try:
            return await self._sink_client().get("/refunds", params={"order_id": order_id})
        except (httpx.NetworkError, httpx.RemoteProtocolError):
            self.sink_retries += 1
            return await self._sink_client().get("/refunds", params={"order_id": order_id})

    async def _read_sink(self, order_id: str) -> tuple[int, int]:
        """The sink's request-log entries and refunds for this order, right now."""
        r = await self._sink_get(order_id)
        r.raise_for_status()
        body = r.json()
        return len(body["requests"]), len(body["refunds"])

    async def _wait_for_commit(self, order_id: str) -> bool:
        """Poll the sink until the refund for this order exists."""
        deadline = asyncio.get_running_loop().time() + self.commit_wait_s
        while asyncio.get_running_loop().time() < deadline:
            r = await self._sink_get(order_id)
            if r.status_code == 200 and r.json()["refunds"]:
                return True
            await asyncio.sleep(self.poll_interval_s)
        return False
