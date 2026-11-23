"""One trial through each runner's real code path: the subject process, the sink, the verdict.

The unit tests cover the runners' summaries and helpers; these cover the paths that
spawn a subject and build its trial record, where a bug otherwise shows only in a
real run (as the variable a 003 change once shadowed did, caught by mypy, not tests).

The 003 path runs everywhere: its subject is the reference `mcp` client, installed
with the project. The others need a subject environment and run only when one is
named, and only on the local stack (they need its PostgreSQL):

  CRASHTEST_LANGGRAPH_PYTHON  LangGraph 1.2.22 (004)
  CRASHTEST_DBOS_PYTHON       pydantic-ai on DBOS (005)
"""

from __future__ import annotations

import os
import sys

import pytest

from crashtest.experiment import langgraph_crash, single_call
from crashtest.faults.drop_proxy import DropProxy

LANGGRAPH = os.environ.get("CRASHTEST_LANGGRAPH_PYTHON", "")
DBOS = os.environ.get("CRASHTEST_DBOS_PYTHON", "")


def _needs(python: str, stack) -> None:
    if not python:
        pytest.skip("subject environment not named (see this module's docstring)")
    if not stack.database_url:
        pytest.skip("needs the local stack's PostgreSQL")


async def test_single_call_runner_one_trial(stack):
    async with DropProxy(upstream_url=stack.mcp_url, sink_url=stack.sink_url) as proxy:
        result = await single_call.run_single_call(
            subject="mcp_sdk", python=sys.executable, transport="stateless_json", fault="none",
            mcp_url=stack.mcp_url, sink_url=stack.sink_url, drop_proxy=proxy,
            subject_timeout_s=10.0, fault_delay_s=0.0, hang_limit_s=60.0, settle_s=0.2,
        )  # fmt: skip

    assert (result.subject_outcome, result.effects_committed) == ("ok", 1)
    assert (result.verdict, result.report_matches_sink) == ("correct", True)
    assert result.client.startswith("mcp ")


async def test_langgraph_crash_runner_one_trial(stack):
    _needs(LANGGRAPH, stack)
    await langgraph_crash.run_all(
        python=LANGGRAPH, database_url=stack.database_url, sink_url=stack.sink_url,
        kill_points=("none",), durabilities=("sync",), key_strategies=("none",),
        trials_per_cell=0,
    )  # fmt: skip  # zero trials: only creates the checkpoint tables
    trial = await langgraph_crash.run_crash_trial(
        python=LANGGRAPH, database_url=stack.database_url, sink_url=stack.sink_url,
        kill_point="none", durability="sync", key_strategy="none",
    )  # fmt: skip

    assert (trial.effects_committed, trial.verdict, trial.report_matches_sink) == (
        1,
        "correct",
        True,
    )


async def test_dbos_crash_runner_one_trial(stack):
    _needs(DBOS, stack)
    from crashtest.experiment import dbos_crash

    trial = await dbos_crash.run_trial(
        python=DBOS, system_database_url=dbos_crash._sqlalchemy_url(stack.database_url),
        sink_url=stack.sink_url, kill_point="none", tool_mode="step", key_strategy="none",
    )  # fmt: skip

    assert (trial.effects_committed, trial.verdict, trial.report_matches_sink) == (
        1,
        "correct",
        True,
    )
