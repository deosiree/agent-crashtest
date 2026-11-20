"""Unit tests for the crash runners' shared pieces: no services needed."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from crashtest.experiment import dbos_crash, langgraph_crash
from crashtest.experiment.crash_common import provenance, shuffled_cells
from crashtest.experiment.dbos_crash import DbosCrashTrial, _sqlalchemy_url, summarise

REPO = Path(__file__).resolve().parents[1]


def test_shuffled_cells_cover_every_combination_the_same_number_of_times():
    cells = shuffled_cells((("a", "b"), ("x", "y", "z")), trials_per_cell=4, seed=1)

    assert len(cells) == 2 * 3 * 4
    assert set(Counter(cells).values()) == {4}
    assert set(cells) == {(p, q) for p in "ab" for q in "xyz"}


def test_shuffled_cells_are_reproducible_from_the_seed_and_not_in_block_order():
    factors = (("a", "b", "c"), ("x", "y"))

    first = shuffled_cells(factors, trials_per_cell=5, seed=7)

    assert first == shuffled_cells(factors, trials_per_cell=5, seed=7)
    assert first != shuffled_cells(factors, trials_per_cell=5, seed=8)
    assert first != sorted(first)  # a cell's trials are spread out, not back to back


def test_provenance_records_the_commit():
    info = provenance(REPO)

    assert set(info) == {"git_commit", "git_dirty"}
    if info["git_commit"] is None:
        # Not a git checkout (a downloaded copy, or a container with nothing
        # passed in): nothing is known about the tree either.
        assert info["git_dirty"] is None
    else:
        assert len(info["git_commit"]) == 40
        assert isinstance(info["git_dirty"], bool)


def test_provenance_outside_a_git_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("CRASHTEST_GIT_COMMIT", raising=False)
    monkeypatch.delenv("CRASHTEST_GIT_DIRTY", raising=False)
    assert provenance(tmp_path) == {"git_commit": None, "git_dirty": None}

    monkeypatch.setenv("CRASHTEST_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv("CRASHTEST_GIT_DIRTY", "false")
    assert provenance(tmp_path) == {"git_commit": "a" * 40, "git_dirty": False}


def test_postgres_urls_get_an_explicit_driver_for_dbos():
    assert _sqlalchemy_url("postgresql://u:p@h:5/db") == "postgresql+psycopg://u:p@h:5/db"
    assert _sqlalchemy_url("sqlite:///x.sqlite") == "sqlite:///x.sqlite"


def _trial(kill_point, tool_mode, key_strategy, effects, keys=()) -> DbosCrashTrial:
    return DbosCrashTrial(
        kill_point=kill_point,
        tool_mode=tool_mode,
        key_strategy=key_strategy,
        sequence=0,
        order_id="o",
        workflow_id="w",
        killed=kill_point != "none",
        effects_committed=effects,
        requests_received=max(effects, len(keys)),
        verdict="duplicate" if effects > 1 else "correct",
        fault_confirmed=True if kill_point == "during_tool" else None,
        keys_at_sink=list(keys),
        resume={"tool_calls": 1} if kill_point != "none" else {},
    )


def test_dbos_summary_counts_refunds_and_unstable_keys_per_cell():
    trials = [
        _trial("after_tool", "plain", "none", 2),
        _trial("after_tool", "plain", "none", 2),
        _trial("after_tool", "step", "none", 1),
        _trial("during_tool", "plain", "tool_call_id", 2, keys=("call-a", "call-b")),
        _trial("during_tool", "plain", "tool_call_id", 1, keys=("call-a", "call-a")),
    ]

    rows = {(r["kill_point"], r["tool_mode"], r["key_strategy"]): r for r in summarise(trials)}

    assert rows[("after_tool", "plain", "none")]["refunded_twice"] == 2
    assert rows[("after_tool", "step", "none")]["refunded_once"] == 1
    assert rows[("after_tool", "step", "none")]["fault_confirmed"] == "-"
    unstable = rows[("during_tool", "plain", "tool_call_id")]
    assert unstable["distinct_keys"] == 1
    assert unstable["fault_confirmed"] == 2
    assert "duplicate x1" in unstable["verdicts"] and "correct x1" in unstable["verdicts"]


def test_both_runners_point_at_subject_scripts_that_exist():
    assert langgraph_crash.SCRIPT.is_file()
    assert dbos_crash.SCRIPT.is_file()


def test_late_response_is_selectable_but_not_part_of_all():
    assert dbos_crash._names("all", dbos_crash.KILL_POINTS, dbos_crash.EXTRA_POINTS) == (
        dbos_crash.KILL_POINTS
    )
    assert dbos_crash._names("late_response", dbos_crash.KILL_POINTS, dbos_crash.EXTRA_POINTS) == (
        "late_response",
    )


def test_dbos_summary_includes_late_response_cells():
    rows = summarise([_trial("late_response", "step", "none", 2)])

    assert [(r["kill_point"], r["refunded_twice"]) for r in rows] == [("late_response", 1)]


def _lg_trial(delay, next_seen, effects=1):
    return langgraph_crash.CrashTrial(
        kill_point="after_tool",
        durability="async",
        key_strategy="none",
        order_id="o",
        thread_id="t",
        killed=True,
        effects_committed=effects,
        requests_received=effects,
        verdict="correct",
        resume={"path": "x", "resume_saw": {"next": next_seen}},
        kill_delay_s=delay,
    )


def test_langgraph_summary_separates_kill_delays_and_reports_next():
    trials = [
        _lg_trial(0.0, []),
        _lg_trial(0.0, []),
        _lg_trial(0.0, ["agent"]),
        _lg_trial(1.0, ["agent"]),
    ]

    rows = langgraph_crash.summarise(trials)

    assert [(r["kill_delay_s"], r["trials"]) for r in rows] == [(0.0, 3), (1.0, 1)]
    assert rows[0]["next_at_resume"] == "['agent'] x1, [] x2"
    assert rows[1]["next_at_resume"] == "['agent'] x1"


def test_langgraph_summary_of_a_recorded_run_is_unchanged():
    """Results recorded before the kill delay existed still summarise to the same counts."""
    doc = json.loads((REPO / "results/langgraph_crash/20261116T204350Z.json").read_text())
    trials = [langgraph_crash.CrashTrial(**t) for t in doc["trials"]]
    fields = (
        "kill_point",
        "durability",
        "key_strategy",
        "trials",
        "refunded_once",
        "refunded_twice",
    )

    def counts(rows):
        return sorted(tuple(r[f] for f in fields) for r in rows)

    rows = langgraph_crash.summarise(trials)
    assert counts(rows) == counts(doc["summary"])
    assert {r["kill_delay_s"] for r in rows} == {0.0}


def test_single_call_needs_a_server_for_each_transport_on_an_external_stack(monkeypatch):
    from crashtest.experiment import single_call

    base = [
        "single_call",
        "--subject",
        "mcp_sdk=python",
        "--transports",
        "stateless_json,stateful_sse",
    ]
    monkeypatch.setattr(sys, "argv", base)
    with pytest.raises(SystemExit):
        single_call.main()

    monkeypatch.setattr(sys, "argv", [*base, "--mcp-url-for", "no_such_transport=http://x/mcp"])
    with pytest.raises(SystemExit):
        single_call.main()


def test_the_mcp_refund_runner_refuses_a_delay_below_the_timeout_unless_asked(monkeypatch):
    from crashtest.experiment import run

    args = ["run", "--client-timeout", "2.0", "--fault-delay", "1.0"]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit):
        run.main()
