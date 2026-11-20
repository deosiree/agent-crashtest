"""The result store: load results files into PostgreSQL and count by cell."""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest

from crashtest.results.store import (
    ChangedResultsFile,
    cell_counts,
    cell_of,
    condition_of,
    load_files,
)


def _trial(kill_point, effects, verdict):
    return {
        "kill_point": kill_point,
        "tool_mode": "step",
        "key_strategy": "none",
        "order_id": f"ord-{uuid.uuid4()}",
        "effects_committed": effects,
        "requests_received": effects,
        "verdict": verdict,
        "fault_confirmed": None,
    }


def _write(tmp_path, trials, experiment="dbos_crash", parameters=None):
    path = tmp_path / "results" / experiment / f"{uuid.uuid4()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "experiment": experiment,
        "started_at": "2026-11-17T00:00:00+00:00",
        "parameters": parameters or {"seed": 1},
        "summary": [],
        "trials": trials,
    }
    path.write_text(json.dumps(doc))
    return path


@pytest.fixture
def conn(stack):
    if not stack.database_url:
        pytest.skip("the store test needs the database URL of a local stack")
    with psycopg.connect(stack.database_url, autocommit=True) as c:
        yield c


def test_cells_use_each_experiments_own_factor_names():
    assert cell_of(
        "langgraph_crash", {"kill_point": "k", "durability": "d", "key_strategy": "s"}
    ) == {
        "kill_point": "k",
        "durability": "d",
        "key_strategy": "s",
        "kill_delay_s": 0.0,  # trials from before the kill-delay sweep were killed at once
    }
    with pytest.raises(ValueError):
        cell_of("no_such_experiment", {})


def test_load_is_idempotent_and_counts_by_cell(conn, tmp_path):
    marker = f"k-{uuid.uuid4()}"  # a kill point no other test or run uses
    path = _write(
        tmp_path,
        [
            _trial(marker, 1, "correct"),
            _trial(marker, 2, "duplicate"),
            _trial(marker, 2, "duplicate"),
        ],
    )

    first = load_files(conn, [path], tmp_path)
    again = load_files(conn, [path], tmp_path)

    assert [(status, n) for _, status, n in first] == [("loaded", 3)]
    assert [(status, n) for _, status, n in again] == [("already loaded", 3)]
    row = next(r for r in cell_counts(conn, "dbos_crash") if r["kill_point"] == marker)
    assert (row["trials"], row["once"], row["more_than_once"], row["runs"]) == (3, 1, 2, 1)


def test_a_results_file_changed_after_loading_is_refused(conn, tmp_path):
    path = _write(tmp_path, [_trial("none", 1, "correct")])
    load_files(conn, [path], tmp_path)

    path.write_text(path.read_text().replace('"correct"', '"duplicate"'))

    with pytest.raises(ChangedResultsFile):
        load_files(conn, [path], tmp_path)


def test_runs_under_different_conditions_are_not_pooled(conn, tmp_path):
    arm = f"arm-{uuid.uuid4()}"  # an arm no other test or run uses

    def trial(effects):
        return {
            "transport": "t",
            "fault": "lost_response",
            "arm": arm,
            "order_id": f"ord-{uuid.uuid4()}",
            "effects_committed": effects,
        }

    below = {"client_timeout_s": 2.0, "fault_delay_s": 1.8}
    above = {"client_timeout_s": 2.0, "fault_delay_s": 2.2}
    paths = [
        _write(tmp_path, [trial(1), trial(1)], "mcp_refund", below),
        _write(tmp_path, [trial(2), trial(2)], "mcp_refund", above),
        _write(tmp_path, [trial(2)], "mcp_refund", above),
    ]
    load_files(conn, paths, tmp_path)

    rows = {r["fault_delay_s"]: r for r in cell_counts(conn, "mcp_refund") if r["arm"] == arm}
    assert set(rows) == {1.8, 2.2}
    assert (rows[1.8]["trials"], rows[1.8]["once"], rows[1.8]["runs"]) == (2, 2, 1)
    assert (rows[2.2]["trials"], rows[2.2]["more_than_once"], rows[2.2]["runs"]) == (3, 3, 2)


def test_the_condition_includes_the_operating_system():
    windows = {"platform": "Windows-11-10.0.26300-SP0"}
    linux = {"platform": "Linux-7.0.0-1019-azure-x86_64-with-glibc2.43"}
    params = {"client_timeout_s": 2.0, "fault_delay_s": 4.0, "seed": 3}
    assert condition_of("mcp_refund", params, windows) == {
        "os": "Windows",
        "client_timeout_s": 2.0,
        "fault_delay_s": 4.0,
    }
    assert condition_of("dbos_crash", params, linux) == {"os": "Linux"}
    assert condition_of("dbos_crash", params, {}) == {"os": None}


def test_json_that_is_not_a_run_is_skipped_not_loaded(conn, tmp_path):
    """A sink export or a validation report sits beside the runs in results/
    (as in results/model_retry/); loading every file must not fail on it."""
    export = tmp_path / "export.json"
    export.write_text(json.dumps({"what": "sink rows", "refunds": []}))
    run = _write(tmp_path, [_trial(f"k-{uuid.uuid4()}", 1, "correct")])

    statuses = [(p.name, status, n) for p, status, n in load_files(conn, [export, run], tmp_path)]

    assert ("export.json", "not a run file", 0) in statuses
    assert any(status == "loaded" for _, status, _ in statuses)
