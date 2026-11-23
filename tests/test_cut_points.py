"""Experiment 008: the before_request and mid_stream cut points, and the runner.

The end-to-end tests run one trial in each new cut point's main cell shapes
(docs/m2-cut-points-plan.md). They check that each cut lands where it says, and
pin the behaviour of the installed `mcp` 2.3.0 that the predictions rest on, so
that a library upgrade that changes it fails here. They are pre-run checks, not
counted in the experiment.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from crashtest.experiment.cut_points import CUT_CELLS, format_table, summarise
from crashtest.experiment.resume import FAULT_CELLS
from crashtest.experiment.trial import Attempt, TrialResult, run_trial
from crashtest.faults.drop_proxy import DropRecord, mid_stream_prefix

FAST = {"client_timeout_s": 10.0, "max_attempts": 3, "progress": True}

# --- the mid_stream cut point --------------------------------------------------

HEADERS = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n\r\n"
PRIMING = b"id: 7\r\ndata: \r\n\r\n"
PROGRESS = (
    b"event: message\r\n"
    b'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":0}}\r\n'
    b"id: 8\r\n\r\n"
)
RESPONSE = b'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\r\nid: 9\r\n\r\n'


def test_the_first_notification_passes_and_the_cut_follows_it():
    buffer = HEADERS + PRIMING + PROGRESS + RESPONSE
    assert mid_stream_prefix(buffer) == (len(HEADERS + PRIMING + PROGRESS), "notification")


def test_a_response_arriving_first_is_held_back():
    assert mid_stream_prefix(HEADERS + RESPONSE) == (len(HEADERS), "response")


def test_a_json_body_is_a_response():
    head = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n"
    body = b'{"jsonrpc":"2.0","id":1,"result":{}}\r\n\r\n'
    assert mid_stream_prefix(head + body) == (len(head), "response")


def test_an_incomplete_notification_is_held_back():
    partial = HEADERS + PRIMING + PROGRESS[:40]
    assert mid_stream_prefix(partial) == (len(HEADERS + PRIMING), None)


def test_nothing_is_decided_before_a_message_arrives():
    assert mid_stream_prefix(HEADERS + PRIMING) == (len(HEADERS + PRIMING), None)


# --- did the cut land where intended -------------------------------------------


def test_landed_after_commit_needs_the_commit():
    assert DropRecord("o", True, 0, drop_point="no_bytes").landed_as_intended()
    assert not DropRecord("o", False, 0, drop_point="after_headers").landed_as_intended()


def test_landed_before_request_needs_an_empty_sink():
    ok = DropRecord(
        "o",
        False,
        10,
        drop_point="before_request",
        request_withheld=True,
        sink_requests_at_cut=0,
        sink_refunds_at_cut=0,
    )
    assert ok.landed_as_intended()
    ok.sink_requests_at_cut = 1
    assert not ok.landed_as_intended()


def test_landed_mid_stream_needs_a_notification_and_no_refund():
    record = DropRecord(
        "o",
        False,
        0,
        drop_point="mid_stream",
        notifications_forwarded=1,
        sink_requests_at_cut=0,
        sink_refunds_at_cut=0,
    )
    assert record.landed_as_intended()
    record.sink_refunds_at_cut = 1
    assert not record.landed_as_intended()
    record.sink_refunds_at_cut = 0
    record.notifications_forwarded = 0  # the response came first
    assert not record.landed_as_intended()


def test_007_keeps_its_own_two_drop_points():
    assert [c.name for c in FAULT_CELLS] == ["none", "no_bytes", "after_headers"]


def test_008_cut_cells():
    assert [c.name for c in CUT_CELLS] == ["none", "no_bytes", "before_request", "mid_stream"]


# --- the runner's summary ------------------------------------------------------


def _trial(**overrides) -> TrialResult:
    base = dict(
        arm="no_dedup",
        fault="connection_drop",
        order_id="ord-1",
        intended_effects=1,
        effects_committed=1,
        requests_received=1,
        client_saw_success=True,
        verdict="correct",
        attempts=[
            Attempt(1, None, "disconnect", 0.2, raised="X", protocol="2026-07-28", progress_seen=0),
            Attempt(2, None, "ok", 3.1, protocol="2026-07-28", progress_seen=1),
        ],
        transport="stateful_sse_progress",
        request_delta=-1,
        client_requests=2,
        fault_confirmed=True,
        report_matches_sink=True,
        client_mode="auto",
        drop_point="before_request",
        resume_requests=0,
    )
    base.update(overrides)
    return TrialResult(**base)  # type: ignore[arg-type]


def test_summary_counts_request_deltas_and_first_errors():
    trials = [
        _trial(order_id="a"),
        _trial(
            order_id="b",
            request_delta=0,
            effects_committed=2,
            verdict="duplicate",
            report_matches_sink=False,
        ),
        _trial(
            order_id="c",
            fault="none",
            drop_point=None,
            fault_confirmed=None,
            attempts=[Attempt(1, None, "ok", 3.1, progress_seen=1)],
            request_delta=0,
        ),
    ]
    rows = summarise(trials)
    assert [r["drop_point"] for r in rows] == ["before_request", "none"]
    cut = rows[0]
    assert (cut["trials"], cut["correct"], cut["duplicate"]) == (2, 1, 1)
    assert (cut["effects_1"], cut["effects_2"]) == (1, 1)
    assert cut["request_delta"] == "-1 x1, 0 x1"
    assert cut["first_raised"] == "X x2"
    assert cut["progress_seen_first"] == "0 x2"
    assert cut["attempts"] == 4
    assert cut["report_mismatch"] == 1
    assert cut["fault_confirmed"] == 2
    assert rows[1]["fault_confirmed"] == "-"
    assert "before_request" in format_table(rows)


# --- end to end, one trial per cell shape --------------------------------------


def _url(stack, name: str) -> str:
    if name not in stack.mcp_urls:
        pytest.skip(f"this stack does not serve the {name} transport")
    return stack.mcp_urls[name]


@pytest_asyncio.fixture
async def proxy_for(stack):
    from contextlib import AsyncExitStack

    from crashtest.faults.drop_proxy import DropProxy

    async with AsyncExitStack() as exits:

        async def make(name: str) -> tuple[str, DropProxy]:
            url = _url(stack, name)
            proxy = await exits.enter_async_context(
                DropProxy(upstream_url=url, sink_url=stack.sink_url)
            )
            return url, proxy

        yield make


async def _cut(stack, proxy_for, transport, mode, point, arm="no_dedup", fault="connection_drop"):
    url, proxy = await proxy_for(transport)
    result = await run_trial(
        arm=arm,
        fault=fault,
        mcp_url=url,
        sink_url=stack.sink_url,
        transport=transport,
        drop_proxy=proxy,
        client_mode=mode,
        drop_point=point,
        **FAST,
    )
    return result, proxy


async def test_progress_reaches_the_client_with_no_fault(stack, proxy_for):
    result, _ = await _cut(
        stack, proxy_for, "stateful_sse_progress", "auto", "no_bytes", fault="none"
    )
    assert result.verdict == "correct"
    assert [a.progress_seen for a in result.attempts] == [1]


async def test_before_request_nothing_reaches_the_sink(stack, proxy_for):
    result, proxy = await _cut(stack, proxy_for, "stateful_sse_progress", "auto", "before_request")
    [drop] = proxy.drops
    assert drop.request_withheld and drop.landed_as_intended()
    assert result.fault_confirmed is True
    assert [a.outcome for a in result.attempts] == ["disconnect", "ok"]
    assert result.request_delta == -1
    assert (result.effects_committed, result.verdict) == (1, "correct")


async def test_mid_stream_cut_cancels_the_tool_on_2026_07_28(stack, proxy_for):
    result, proxy = await _cut(
        stack, proxy_for, "stateful_sse_store_progress", "auto", "mid_stream"
    )
    [drop] = proxy.drops
    assert drop.notifications_forwarded == 1 and drop.sink_refunds_at_cut == 0
    assert result.fault_confirmed is True
    assert result.attempts[0].progress_seen == 1
    assert result.resume_requests == 0
    assert result.request_delta == -1
    assert (result.effects_committed, result.verdict) == (1, "correct")


async def test_mid_stream_cut_is_resumed_with_an_event_store(stack, proxy_for):
    result, proxy = await _cut(
        stack, proxy_for, "stateful_sse_store_progress", "legacy", "mid_stream"
    )
    assert result.fault_confirmed is True
    assert [a.outcome for a in result.attempts] == ["ok"]
    assert result.resume_requests and result.resume_requests >= 1
    assert (result.effects_committed, result.verdict) == (1, "correct")
