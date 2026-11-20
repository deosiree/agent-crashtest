"""Experiment 007: the resumable server, the second drop point, and the runner.

The end-to-end tests run one trial in each of the cells that matter most to the
design in docs/m2-plan.md. They check the harness (the drop happened after the
commit, the counts are read from the sink), and they pin down the behaviour of
the installed `mcp` 2.3.0 so that a library upgrade that changes it fails here.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from mcp_types import JSONRPCNotification, JSONRPCResponse

from crashtest.experiment.resume import FAULT_CELLS, format_table, summarise
from crashtest.experiment.trial import Attempt, TrialResult, run_trial
from crashtest.faults.drop_proxy import DropProxy, count_event_ids, forwardable_prefix
from crashtest.refund_mcp.event_store import InMemoryEventStore

FAST = {"client_timeout_s": 5.0, "max_attempts": 3}

# --- the after_headers drop point ------------------------------------------------

HEADERS = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nmcp-session-id: abc\r\n\r\n"
PRIMING = b"id: 7\r\ndata: \r\n\r\n"
RESPONSE = b'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\r\nid: 8\r\n\r\n'


def test_headers_and_priming_event_pass_and_the_message_does_not():
    end, found = forwardable_prefix(HEADERS + PRIMING + RESPONSE)
    assert found
    assert end == len(HEADERS + PRIMING)


def test_nothing_is_held_back_before_a_message_arrives():
    assert forwardable_prefix(HEADERS + PRIMING) == (len(HEADERS + PRIMING), False)


def test_an_incomplete_block_is_held_back():
    assert forwardable_prefix(HEADERS + b"id: 7\r\nda") == (len(HEADERS), False)


def test_a_json_body_gets_its_headers_only():
    head = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n"
    assert forwardable_prefix(head + b'{"jsonrpc":"2.0","id":1}') == (len(head), True)


def test_a_message_split_across_reads_is_found_once_complete():
    buffer = HEADERS + PRIMING + b'event: message\r\ndata: {"json'
    assert forwardable_prefix(buffer) == (len(HEADERS + PRIMING), False)
    assert forwardable_prefix(buffer + b'rpc":"2.0"}') == (len(HEADERS + PRIMING), True)


def test_event_ids_are_counted_but_not_the_session_header():
    assert count_event_ids(HEADERS) == 0
    assert count_event_ids(HEADERS + PRIMING) == 1
    assert count_event_ids(PRIMING + RESPONSE) == 2


def test_an_unknown_drop_point_is_refused():
    proxy = DropProxy(upstream_url="http://127.0.0.1:1/mcp", sink_url="http://127.0.0.1:2")
    with pytest.raises(ValueError):
        proxy.arm("ord-1", "mid_air")  # type: ignore[arg-type]


# --- the event store -------------------------------------------------------------


async def test_replay_sends_only_later_events_of_the_same_stream():
    store = InMemoryEventStore()
    priming = await store.store_event("req-1", None)
    await store.store_event("req-2", JSONRPCNotification(jsonrpc="2.0", method="other/stream"))
    answer = JSONRPCResponse(jsonrpc="2.0", id=1, result={})
    answer_id = await store.store_event("req-1", answer)

    sent = []

    async def send(event):
        sent.append(event)

    assert await store.replay_events_after(priming, send) == "req-1"
    assert [(e.message, e.event_id) for e in sent] == [(answer, answer_id)]


async def test_replay_after_an_unknown_id_sends_nothing():
    store = InMemoryEventStore()
    await store.store_event("req-1", None)

    async def send(event):  # pragma: no cover - must not be called
        raise AssertionError(event)

    assert await store.replay_events_after("999", send) is None


async def test_event_ids_are_unique_across_streams():
    store = InMemoryEventStore()
    ids = [await store.store_event(s, None) for s in ("a", "b", "a", "c")]
    assert len(set(ids)) == 4
    assert store.event_count == 4


def test_an_empty_store_is_truthy():
    # mcp 2.3.0 checks `if self._event_store:`; a falsy store is silently ignored.
    assert InMemoryEventStore()


# --- the runner's summary --------------------------------------------------------


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
        attempts=[Attempt(1, None, "ok", 1.2, protocol="2025-11-25")],
        transport="stateful_sse_store",
        client_requests=1,
        fault_confirmed=True,
        report_matches_sink=True,
        client_mode="legacy",
        drop_point="after_headers",
        resume_requests=1,
    )
    base.update(overrides)
    return TrialResult(**base)  # type: ignore[arg-type]


def test_summary_counts_resumes_and_forwarded_event_ids():
    trials = [
        _trial(order_id="a"),
        _trial(
            order_id="b",
            effects_committed=2,
            requests_received=2,
            verdict="duplicate",
            resume_requests=0,
            report_matches_sink=False,
            attempts=[
                Attempt(1, None, "disconnect", 0.4, protocol="2025-11-25"),
                Attempt(2, None, "ok", 0.1, protocol="2025-11-25"),
            ],
        ),
        _trial(order_id="c", fault="none", drop_point=None, fault_confirmed=None),
    ]
    drops = [
        {"order_id": "a", "event_ids_forwarded": 1},
        {"order_id": "b", "event_ids_forwarded": 0},
    ]
    rows = summarise(trials, drops)
    assert [r["drop_point"] for r in rows] == ["after_headers", "none"]
    dropped = rows[0]
    assert dropped["trials"] == 2
    assert (dropped["correct"], dropped["duplicate"]) == (1, 1)
    assert dropped["first_attempt"] == "disconnect x1, ok x1"
    assert dropped["protocol"] == "2025-11-25 x3"
    assert (dropped["resume_requests"], dropped["trials_with_resume"]) == (1, 1)
    assert dropped["event_ids_forwarded"] == 1
    assert dropped["report_mismatch"] == 1
    assert dropped["fault_confirmed"] == 2
    assert rows[1]["event_ids_forwarded"] == "-"
    assert rows[1]["fault_confirmed"] == "-"
    assert "after_headers" in format_table(rows)


def test_fault_cells_are_no_fault_and_both_drop_points():
    assert [c.name for c in FAULT_CELLS] == ["none", "no_bytes", "after_headers"]


# --- end to end, one trial per cell ----------------------------------------------


@pytest.fixture
def store_url(stack) -> str:
    if "stateful_sse_store" not in stack.mcp_urls:
        pytest.skip("this stack does not serve the stateful_sse_store transport")
    return stack.mcp_urls["stateful_sse_store"]


@pytest_asyncio.fixture
async def store_proxy(stack, store_url):
    async with DropProxy(upstream_url=store_url, sink_url=stack.sink_url) as p:
        yield p


@pytest_asyncio.fixture
async def plain_proxy(stack):
    if "stateful_sse" not in stack.mcp_urls:
        pytest.skip("this stack does not serve the stateful_sse transport")
    async with DropProxy(upstream_url=stack.mcp_urls["stateful_sse"], sink_url=stack.sink_url) as p:
        yield p


async def test_no_fault_through_the_resumable_server(stack, store_url, store_proxy):
    result = await run_trial(
        arm="no_dedup",
        fault="none",
        mcp_url=store_url,
        sink_url=stack.sink_url,
        transport="stateful_sse_store",
        drop_proxy=store_proxy,
        client_mode="legacy",
        **FAST,
    )
    assert result.verdict == "correct"
    assert result.attempts[0].protocol == "2025-11-25"
    assert result.drop_point is None
    assert result.resume_requests == 0


async def test_legacy_client_resumes_from_the_event_store(stack, store_url, store_proxy):
    result = await run_trial(
        arm="no_dedup",
        fault="connection_drop",
        mcp_url=store_url,
        sink_url=stack.sink_url,
        transport="stateful_sse_store",
        drop_proxy=store_proxy,
        client_mode="legacy",
        drop_point="after_headers",
        **FAST,
    )
    [drop] = store_proxy.drops
    assert drop.committed_before_drop and drop.event_ids_forwarded >= 1
    assert result.fault_confirmed is True
    assert [a.outcome for a in result.attempts] == ["ok"]
    assert result.resume_requests and result.resume_requests >= 1
    assert (result.effects_committed, result.verdict) == (1, "correct")
    assert result.report_matches_sink is True


async def test_no_resume_without_the_priming_event(stack, store_url, store_proxy):
    result = await run_trial(
        arm="no_dedup",
        fault="connection_drop",
        mcp_url=store_url,
        sink_url=stack.sink_url,
        transport="stateful_sse_store",
        drop_proxy=store_proxy,
        client_mode="legacy",
        drop_point="no_bytes",
        **FAST,
    )
    assert [d.event_ids_forwarded for d in store_proxy.drops] == [0]
    assert [a.outcome for a in result.attempts] == ["disconnect", "ok"]
    assert result.resume_requests == 0
    assert (result.effects_committed, result.verdict) == (2, "duplicate")


async def test_no_resume_without_an_event_store(stack, plain_proxy):
    result = await run_trial(
        arm="no_dedup",
        fault="connection_drop",
        mcp_url=stack.mcp_urls["stateful_sse"],
        sink_url=stack.sink_url,
        transport="stateful_sse",
        drop_proxy=plain_proxy,
        client_mode="legacy",
        drop_point="after_headers",
        **FAST,
    )
    assert [d.event_ids_forwarded for d in plain_proxy.drops] == [0]
    assert [a.outcome for a in result.attempts] == ["disconnect", "ok"]
    assert (result.effects_committed, result.verdict) == (2, "duplicate")


async def test_default_client_negotiates_a_protocol_with_no_resumption(
    stack, store_url, store_proxy
):
    result = await run_trial(
        arm="stable_key",
        fault="connection_drop",
        mcp_url=store_url,
        sink_url=stack.sink_url,
        transport="stateful_sse_store",
        drop_proxy=store_proxy,
        drop_point="after_headers",
        **FAST,
    )
    assert [a.protocol for a in result.attempts] == ["2026-07-28", "2026-07-28"]
    assert [a.outcome for a in result.attempts] == ["disconnect", "ok"]
    assert result.resume_requests == 0
    assert (result.effects_committed, result.verdict) == (1, "correct")
