"""Pure unit tests: no services needed."""

from __future__ import annotations

import h11
import httpcore
import httpx
from mcp import MCPError

from crashtest.checker import classify
from crashtest.experiment.run import format_table, summarise
from crashtest.experiment.trial import (
    CONNECTION_CLOSED_CODE,
    REQUEST_TIMEOUT_CODE,
    TrialResult,
    _classify_failure,
    _key_for_attempt,
)
from crashtest.faults.drop_proxy import _Scanner, _wire_form


def _trial(arm, fault, effects) -> TrialResult:
    verdict = classify(
        effects=effects, intended=1, status="completed", payload_matches=True, confirmed=True
    )
    return TrialResult(
        arm=arm,
        fault=fault,
        order_id="o",
        intended_effects=1,
        effects_committed=effects,
        requests_received=effects,
        client_saw_success=True,
        verdict=verdict,
    )


def test_timeout_is_recognised_bare_and_inside_an_exception_group():
    timeout = MCPError(code=REQUEST_TIMEOUT_CODE, message="timed out")

    assert _classify_failure(timeout) == "timeout"
    assert _classify_failure(ExceptionGroup("g", [timeout])) == "timeout"
    assert _classify_failure(TimeoutError()) == "timeout"


def test_other_failures_are_not_treated_as_timeouts():
    assert _classify_failure(MCPError(code=-32603, message="boom")) == "error:mcp_-32603"
    assert (
        _classify_failure(ExceptionGroup("g", [ValueError(), KeyError()]))
        == "error:ValueError+KeyError"
    )


def test_key_strategy_per_arm():
    assert _key_for_attempt("no_dedup", "op") is None
    assert _key_for_attempt("stable_key", "op") == "op"
    first, second = (
        _key_for_attempt("fresh_key_per_attempt", "op"),
        _key_for_attempt("fresh_key_per_attempt", "op"),
    )
    assert first != second and "op" not in (first, second)


def test_summary_counts_verdicts_per_cell():
    trials = [
        _trial("no_dedup", "lost_response", 2),
        _trial("no_dedup", "lost_response", 2),
        _trial("stable_key", "lost_response", 1),
        _trial("stable_key", "lost_response", 0),
    ]

    rows = {(r["fault"], r["arm"]): r for r in summarise(trials)}

    assert rows[("lost_response", "no_dedup")]["duplicate"] == 2
    assert rows[("lost_response", "no_dedup")]["effects_committed"] == 4
    assert rows[("lost_response", "stable_key")]["correct"] == 1
    assert rows[("lost_response", "stable_key")]["lost"] == 1
    assert "| stateless_json | lost_response | no_dedup | 2 |" in format_table(summarise(trials))


def test_summary_keeps_transports_apart_and_counts_sdk_resends():
    quiet = _trial("no_dedup", "connection_drop", 2)
    resent = _trial("no_dedup", "connection_drop", 2)
    resent.transport = "stateful_sse"
    resent.sdk_extra_requests = 1

    rows = {r["transport"]: r for r in summarise([quiet, resent])}

    assert rows["stateless_json"]["sdk_extra_requests"] == 0
    assert rows["stateful_sse"]["sdk_extra_requests"] == 1


def test_a_cut_connection_is_ambiguous_at_every_layer_of_the_http_stack():
    for exc in (
        httpx.RemoteProtocolError("peer closed"),
        httpx.ReadError("reset"),
        httpcore.RemoteProtocolError("peer closed"),
        h11.RemoteProtocolError("peer closed"),
        ConnectionResetError(),
        MCPError(code=CONNECTION_CLOSED_CODE, message="closed"),
    ):
        assert _classify_failure(exc) == "disconnect", type(exc)
        assert _classify_failure(ExceptionGroup("g", [exc])) == "disconnect"


def test_a_group_is_ambiguous_if_any_member_is():
    group = ExceptionGroup("g", [ValueError(), httpcore.RemoteProtocolError("closed")])

    assert _classify_failure(group) == "disconnect"


def test_an_http_timeout_is_a_timeout_not_a_disconnect():
    assert _classify_failure(httpx.ReadTimeout("slow")) == "timeout"


def test_summary_reports_requests_that_never_arrived():
    short = _trial("no_dedup", "connection_drop", 1)
    short.request_delta = -1  # two attempts, one request at the sink

    row = summarise([short])[0]

    assert row["missing_requests"] == 1
    assert row["sdk_extra_requests"] == 0


def test_scanner_finds_a_pattern_split_across_two_reads():
    scanner = _Scanner()
    pattern = _wire_form("ord-1234")

    assert scanner.feed(b'{"order_id": "ord-12', [pattern]) == []
    assert scanner.feed(b'34", "amount_cents": 5}', [pattern]) == [pattern]


def test_scanner_counts_each_occurrence_once():
    scanner = _Scanner()
    pattern = _wire_form("ord-1")
    body = b'{"order_id": "ord-1"}'

    assert scanner.feed(body, [pattern]) == [pattern]
    assert scanner.feed(b" trailing bytes with no match", [pattern]) == []
    assert scanner.feed(body + body, [pattern]) == [pattern, pattern]


def test_a_key_containing_the_order_id_is_not_counted_as_a_request():
    scanner = _Scanner()
    pattern = _wire_form("ord-1")
    body = b'{"order_id": "ord-1", "idempotency_key": "refund-ord-1"}'

    assert scanner.feed(body, [pattern]) == [pattern]


def test_free_ports_are_never_handed_out_twice_in_one_process():
    from crashtest.experiment.local_stack import _free_port

    ports = [_free_port() for _ in range(50)]
    assert len(set(ports)) == 50


def test_every_runner_prints_the_report_mismatch_column():
    """Contract v0.3, RA: the printed summary of every runner shows how many
    trials told their caller something the sink contradicts (release plan B3)."""
    from crashtest.experiment import dbos_crash, langgraph_crash, resume, run, single_call

    assert "report_mismatch" in run.SUMMARY_COLUMNS
    assert "report_mismatch" in resume.SUMMARY_COLUMNS
    for module in (single_call, langgraph_crash, dbos_crash):
        assert "report_mismatch" in module.COLUMNS


def test_the_001_summary_counts_report_mismatches():
    from types import SimpleNamespace as NS

    from crashtest.experiment import run

    def trial(effects, matches):
        return NS(
            transport="t",
            fault="lost_response",
            arm="no_dedup",
            verdict="duplicate" if effects > 1 else "correct",
            effects_committed=effects,
            requests_received=effects,
            sdk_extra_requests=0,
            client_saw_success=True,
            attempts=[NS(outcome="ok")],
            client_requests=None,
            request_delta=0,
            fault_confirmed=True,
            changed_after_first_read=False,
            report_matches_sink=matches,
        )

    rows = run.summarise([trial(2, False), trial(1, True), trial(2, False)])
    assert [r["report_mismatch"] for r in rows] == [2]
