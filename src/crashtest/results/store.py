"""Load results files into PostgreSQL and read them back by cell.

Each experiment writes its own JSON layout. What they share is loaded into
common columns; the factor levels that define a cell go into `cell`, using the
experiment's own factor names; the whole trial is kept in `raw`.

A cell is counted per condition: run settings that change the outcome, such
as the client's timeout and the fault delay, keep runs apart instead of
pooling them (`CONDITIONS`), and so does the operating system.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = Path(__file__).with_name("schema.sql")

# The factors that define a cell, per experiment, in the order they are reported.
FACTORS: dict[str, tuple[str, ...]] = {
    "mcp_refund_lost_response": ("fault", "arm"),  # 001
    "mcp_refund": ("transport", "fault", "arm"),  # 002
    "mcp_single_call": ("subject", "transport", "fault"),  # 003
    "langgraph_crash": ("kill_point", "durability", "key_strategy", "kill_delay_s"),  # 004
    "dbos_crash": ("kill_point", "tool_mode", "key_strategy"),  # 005
    "model_retry": ("model", "framing", "status_tool"),  # 006
    "mcp_resume": ("client_mode", "transport", "fault", "drop_point", "arm"),  # 007
    "mcp_cut_points": ("client_mode", "transport", "fault", "drop_point", "arm"),  # 008
}


# A factor missing from trials written before it existed, and its value then.
# (Before the kill-delay sweep, 004 killed at once.)
FACTOR_DEFAULTS: dict[str, dict[str, object]] = {
    "langgraph_crash": {"kill_delay_s": 0.0},
}

# Run parameters that define the condition a cell was measured under. Runs that
# differ in any of these are counted apart.
CONDITIONS: dict[str, tuple[str, ...]] = {
    "mcp_refund_lost_response": ("client_timeout_s", "fault_delay_s"),
    "mcp_refund": ("client_timeout_s", "fault_delay_s"),
    "mcp_single_call": ("subject_timeout_s", "fault_delay_s"),
    "mcp_resume": ("client_timeout_s",),
    "mcp_cut_points": ("client_timeout_s",),
}


class ChangedResultsFile(Exception):
    """A file already in the store has different contents now. Results are never edited."""


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA.read_text(encoding="utf-8"))


def cell_of(experiment: str, trial: dict) -> dict:
    factors = FACTORS.get(experiment)
    if factors is None:
        raise ValueError(f"unknown experiment {experiment!r}; add its factors to FACTORS")
    defaults = FACTOR_DEFAULTS.get(experiment, {})
    return {name: trial.get(name, defaults.get(name)) for name in factors}


def condition_of(experiment: str, parameters: dict, environment: dict) -> dict:
    """The run's condition: its operating system and the run parameters in CONDITIONS."""
    platform = environment.get("platform")
    return {
        "os": platform.split("-", 1)[0] if platform else None,
        **{name: parameters.get(name) for name in CONDITIONS.get(experiment, ())},
    }


def is_run_file(doc: object) -> bool:
    """Whether a parsed results JSON is one run: an experiment, its start, its trials."""
    return isinstance(doc, dict) and all(k in doc for k in ("experiment", "started_at", "trials"))


def load_file(conn: psycopg.Connection, path: Path, repo: Path) -> tuple[str, int]:
    """Load one results file.

    Returns ("loaded" | "already loaded" | "not a run file", trial count). JSON
    that is not a run (a sink export, a validation report) sits beside the runs
    in results/; it is skipped and reported, not loaded.
    """
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    doc = json.loads(data)
    if not is_run_file(doc):
        return "not a run file", 0

    existing = conn.execute("SELECT sha256 FROM results.runs WHERE file = %s", (rel,)).fetchone()
    if existing:
        if existing[0] != digest:
            raise ChangedResultsFile(f"{rel} differs from the copy loaded before")
        return "already loaded", len(doc["trials"])

    experiment = doc["experiment"]
    with conn.transaction():
        inserted = conn.execute(
            "INSERT INTO results.runs "
            "(file, sha256, experiment, started_at, parameters, environment, provenance, summary) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                rel,
                digest,
                experiment,
                doc["started_at"],
                Jsonb(doc.get("parameters", {})),
                Jsonb(doc.get("environment", {})),
                Jsonb(doc["provenance"]) if "provenance" in doc else None,
                Jsonb(doc.get("summary", [])),
            ),
        ).fetchone()
        assert inserted is not None  # RETURNING always yields the new row
        run_id = inserted[0]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO results.trials (run_id, position, cell, order_id, effects_committed, "
                "requests_received, verdict, fault_confirmed, raw) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        run_id,
                        position,
                        Jsonb(cell_of(experiment, t)),
                        t["order_id"],
                        t["effects_committed"],
                        t.get("requests_received"),
                        t.get("verdict"),
                        t.get("fault_confirmed"),
                        Jsonb(t),
                    )
                    for position, t in enumerate(doc["trials"])
                ],
            )
    return "loaded", len(doc["trials"])


def load_files(conn: psycopg.Connection, paths: Iterable[Path], repo: Path) -> list[tuple]:
    ensure_schema(conn)
    return [(p, *load_file(conn, p, repo)) for p in sorted(paths)]


def cell_counts(conn: psycopg.Connection, experiment: str) -> list[dict]:
    """Refund counts per cell and condition across every loaded run of one experiment.

    The cell is derived from each trial as recorded (`raw`), so a factor added
    later applies to files loaded before it was.
    """
    rows = conn.execute(
        "SELECT t.run_id, r.parameters, r.environment, t.raw, t.effects_committed "
        "FROM results.trials t JOIN results.runs r ON r.id = t.run_id "
        "WHERE r.experiment = %s",
        (experiment,),
    ).fetchall()
    factors = FACTORS[experiment]
    groups: dict[str, dict] = {}
    for run_id, parameters, environment, raw, effects in rows:
        key_fields = {
            **condition_of(experiment, parameters, environment),
            **cell_of(experiment, raw),
        }
        key = json.dumps(key_fields, sort_keys=True)
        g = groups.setdefault(
            key,
            {**key_fields, "trials": 0, "once": 0, "more_than_once": 0, "none": 0, "runs": set()},
        )
        g["trials"] += 1
        g["once"] += effects == 1
        g["more_than_once"] += effects > 1
        g["none"] += effects == 0
        g["runs"].add(run_id)
    out = [{**g, "runs": len(g["runs"])} for g in groups.values()]
    order = ("os", *CONDITIONS.get(experiment, ()), *factors)
    return sorted(out, key=lambda r: tuple(str(r[f]) for f in order))
