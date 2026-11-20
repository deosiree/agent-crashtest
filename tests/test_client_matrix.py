"""End-to-end across MCP transports, with both forms of the lost-response fault.

The connection-drop tests run through a proxy that cuts the client's connection
only after the sink has committed the refund.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from crashtest.experiment.local_stack import TRANSPORTS
from crashtest.experiment.trial import run_trial
from crashtest.faults.drop_proxy import DropProxy

FAST = {"client_timeout_s": 1.5, "fault_delay_s": 4.0, "max_attempts": 3}


@pytest.fixture(params=list(TRANSPORTS))
def transport(request, stack) -> str:
    if request.param not in stack.mcp_urls:
        pytest.skip(f"this stack does not serve the {request.param} transport")
    return request.param


@pytest_asyncio.fixture
async def proxy(stack, transport):
    async with DropProxy(upstream_url=stack.mcp_urls[transport], sink_url=stack.sink_url) as p:
        yield p


async def test_lost_response_duplicates_on_every_transport(stack, transport):
    result = await run_trial(
        arm="no_dedup",
        fault="lost_response",
        mcp_url=stack.mcp_urls[transport],
        sink_url=stack.sink_url,
        transport=transport,
        **FAST,
    )

    assert [a.outcome for a in result.attempts] == ["timeout", "ok"]
    assert result.effects_committed == 2
    assert result.sdk_extra_requests == 0


async def test_connection_drop_with_naive_retry_refunds_twice(stack, transport, proxy):
    result = await run_trial(
        arm="no_dedup",
        fault="connection_drop",
        mcp_url=stack.mcp_urls[transport],
        sink_url=stack.sink_url,
        transport=transport,
        drop_proxy=proxy,
        **FAST,
    )

    assert [d.committed_before_drop for d in proxy.drops] == [True]
    assert result.fault_confirmed is True
    assert [a.outcome for a in result.attempts] == ["disconnect", "ok"]
    assert result.client_saw_success is True
    assert result.client_requests == 2  # counted at the proxy, independently of the sink
    assert result.request_delta == 0
    assert result.effects_committed == 2
    assert result.verdict == "duplicate"


async def test_connection_drop_with_a_stable_key_refunds_once(stack, transport, proxy):
    result = await run_trial(
        arm="stable_key",
        fault="connection_drop",
        mcp_url=stack.mcp_urls[transport],
        sink_url=stack.sink_url,
        transport=transport,
        drop_proxy=proxy,
        **FAST,
    )

    assert [a.outcome for a in result.attempts] == ["disconnect", "ok"]
    assert result.attempts[1].replayed is True
    assert result.requests_received == 2
    assert result.effects_committed == 1
    assert result.verdict == "correct"


async def test_the_proxy_is_transparent_when_not_armed(stack, transport, proxy):
    result = await run_trial(
        arm="no_dedup",
        fault="none",
        mcp_url=proxy.url,
        sink_url=stack.sink_url,
        transport=transport,
        **FAST,
    )

    assert proxy.drops == []
    assert [a.outcome for a in result.attempts] == ["ok"]
    assert result.effects_committed == 1


async def test_connection_drop_without_a_proxy_is_refused(stack):
    with pytest.raises(ValueError, match="drop_proxy"):
        await run_trial(
            arm="no_dedup", fault="connection_drop", mcp_url=stack.mcp_url, sink_url=stack.sink_url
        )


async def test_a_drop_with_no_retry_is_in_doubt_not_correct(stack, transport, proxy):
    """The refund exists, but the caller was told nothing. That is not `correct`."""
    result = await run_trial(
        arm="no_dedup",
        fault="connection_drop",
        mcp_url=stack.mcp_urls[transport],
        sink_url=stack.sink_url,
        transport=transport,
        drop_proxy=proxy,
        client_timeout_s=1.5,
        fault_delay_s=4.0,
        max_attempts=1,
    )

    assert result.effects_committed == 1
    assert result.client_saw_success is False
    assert result.verdict == "in_doubt_surfaced"


async def test_a_lost_response_is_confirmed_by_the_sink_and_counted_at_the_proxy(
    stack, transport, proxy
):
    result = await run_trial(
        arm="stable_key",
        fault="lost_response",
        mcp_url=stack.mcp_urls[transport],
        sink_url=stack.sink_url,
        transport=transport,
        drop_proxy=proxy,
        **FAST,
    )

    assert result.fault_confirmed is True
    assert result.client_requests == result.requests_received == 2
    assert result.changed_after_first_read is False
    assert result.verdict == "correct"
