"""The proxy's reads of the sink survive a dropped keep-alive connection.

The proxy keeps one client to the sink for its life. The sink's server closes
an idle keep-alive connection after 5 s, so a read that reuses a connection just
as it is closed fails. Without a retry, that error would end the cut with no drop
record. These tests make the first read fail the way a stale
connection does and check that the read is retried once, and only for that.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from crashtest.faults import drop_proxy as dp
from crashtest.faults.drop_proxy import DropProxy


def _proxy_with(handler) -> DropProxy:
    proxy = DropProxy(upstream_url="http://127.0.0.1:1/mcp", sink_url="http://sink")
    proxy._sink = httpx.AsyncClient(base_url="http://sink", transport=httpx.MockTransport(handler))
    return proxy


def _failing_first(error: Exception, body: dict):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise error
        return httpx.Response(200, json=body)

    return handler, calls


@pytest.mark.parametrize(
    "error",
    [
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ReadError("connection reset"),
        httpx.ConnectError("connection refused"),
    ],
)
async def test_a_dropped_connection_is_retried_once(error):
    handler, calls = _failing_first(error, {"requests": [{}], "refunds": [{}]})
    proxy = _proxy_with(handler)

    assert await proxy._read_sink("o") == (1, 1)
    assert (calls["n"], proxy.sink_retries) == (2, 1)


async def test_a_timeout_is_not_retried():
    handler, calls = _failing_first(httpx.ReadTimeout("slow"), {"requests": [], "refunds": []})
    proxy = _proxy_with(handler)

    with pytest.raises(httpx.ReadTimeout):
        await proxy._read_sink("o")
    assert (calls["n"], proxy.sink_retries) == (1, 0)


async def test_a_second_failure_in_a_row_still_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("still gone")

    proxy = _proxy_with(handler)

    with pytest.raises(httpx.RemoteProtocolError):
        await proxy._read_sink("o")
    assert proxy.sink_retries == 1


async def test_the_commit_wait_survives_a_dropped_connection():
    handler, calls = _failing_first(
        httpx.RemoteProtocolError("Server disconnected"), {"requests": [{}], "refunds": [{}]}
    )
    proxy = _proxy_with(handler)

    assert await proxy._wait_for_commit("o") is True
    assert proxy.sink_retries == 1


def _pool_expiry(client: httpx.AsyncClient) -> float:
    # httpx keeps the httpcore pool on a private attribute; read it to check the
    # setting really reached the pool, not just the constant.
    return client._transport._pool._keepalive_expiry  # type: ignore[attr-defined]


def test_the_client_expires_idle_connections_before_the_sink_does():
    assert dp.SINK_KEEPALIVE_S < 5.0  # uvicorn's default keep-alive timeout
    assert _pool_expiry(dp._new_sink_client("http://sink")) == dp.SINK_KEEPALIVE_S
    fallback = DropProxy(upstream_url="http://127.0.0.1:1/mcp", sink_url="http://sink")
    assert _pool_expiry(fallback._sink_client()) == dp.SINK_KEEPALIVE_S


async def test_the_proxy_opens_its_client_with_the_expiry():
    async with DropProxy(upstream_url="http://127.0.0.1:1/mcp", sink_url="http://sink") as proxy:
        assert proxy._sink is not None
        assert _pool_expiry(proxy._sink) == dp.SINK_KEEPALIVE_S


async def test_the_commit_wait_still_gives_up_at_its_deadline():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"requests": [], "refunds": []})

    proxy = _proxy_with(handler)
    proxy.commit_wait_s = 0.1

    assert await proxy._wait_for_commit("o") is False
    assert calls["n"] >= 1 and proxy.sink_retries == 0


async def test_an_http_error_status_is_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "boom"})

    proxy = _proxy_with(handler)

    with pytest.raises(httpx.HTTPStatusError):
        await proxy._read_sink("o")
    assert (calls["n"], proxy.sink_retries) == (1, 0)


async def test_reads_across_the_keepalive_expiry_work_against_the_real_sink(stack):
    async with DropProxy(upstream_url=stack.mcp_url, sink_url=stack.sink_url) as proxy:
        assert await proxy._read_sink("no-such-order") == (0, 0)
        await asyncio.sleep(dp.SINK_KEEPALIVE_S + 0.3)  # the pooled connection has expired
        assert proxy._sink is not None
        pool = proxy._sink._transport._pool  # type: ignore[attr-defined]
        assert all(c.has_expired() or c.is_closed() for c in pool.connections)
        assert await proxy._read_sink("no-such-order") == (0, 0)
        assert proxy.sink_retries == 0
