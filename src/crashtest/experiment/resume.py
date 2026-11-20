"""Experiment 007: does the MCP client resume a dropped stream from an event store?

Pre-registered design: docs/m2-plan.md. The trial is experiments 001/002's
(`crashtest.experiment.trial.run_trial`: "refund this order once", retried by the
application on a timeout or a disconnect), with two factors added: the client's
connect mode (`auto`, the SDK default, which negotiates protocol 2026-07-28 with
this server; `legacy`, which uses the 2025-11-25 handshake) and the server (the
stateful SSE server with and without an event store). The connection is cut after
the commit at one of two drop points (`crashtest.faults.drop_proxy`).

    python -m crashtest.experiment.resume --local --trials 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Literal

from crashtest.experiment.crash_common import provenance
from crashtest.experiment.run import recheck_at_end
from crashtest.experiment.trial import ARMS, Fault, TrialResult, run_trial
from crashtest.faults.drop_proxy import DROP_POINTS, DropPoint, DropProxy

REPO = Path(__file__).resolve().parents[3]

ClientMode = Literal["auto", "legacy"]
CLIENT_MODES: tuple[ClientMode, ...] = ("auto", "legacy")
RESUME_TRANSPORTS: tuple[str, ...] = ("stateful_sse", "stateful_sse_store")


@dataclass(frozen=True)
class FaultCell:
    fault: Fault
    drop_point: DropPoint | None

    @property
    def name(self) -> str:
        return self.drop_point or "none"


FAULT_CELLS: tuple[FaultCell, ...] = (
    FaultCell("none", None),
    *(FaultCell("connection_drop", point) for point in DROP_POINTS),
)

SUMMARY_COLUMNS = (
    "client_mode",
    "transport",
    "drop_point",
    "arm",
    "trials",
    "correct",
    "duplicate",
    "in_doubt_surfaced",
    "stuck",
    "other_verdicts",
    "first_attempt",
    "protocol",
    "resume_requests",
    "trials_with_resume",
    "event_ids_forwarded",
    "effects_committed",
    "requests_received",
    "sdk_extra_requests",
    "client_requests",
    "fault_confirmed",
    "report_mismatch",
    "first_attempt_median_s",
    "changed_after_first_read",
    "changed_by_end_of_run",
)


def summarise(trials: list[TrialResult], drops: list[dict]) -> list[dict]:
    """One row per cell, in the order the cells were run."""
    ids_by_order = {d["order_id"]: d["event_ids_forwarded"] for d in drops}
    keys = list(
        dict.fromkeys((t.client_mode, t.transport, t.drop_point or "none", t.arm) for t in trials)
    )
    rows = []
    for mode, transport, point, arm in keys:
        cell = [
            t
            for t in trials
            if (t.client_mode, t.transport, t.drop_point or "none", t.arm)
            == (mode, transport, point, arm)
        ]
        first = Counter(t.attempts[0].outcome for t in cell if t.attempts)
        protocols = Counter(a.protocol or "-" for t in cell for a in t.attempts)
        firsts = [t.attempts[0].elapsed_s for t in cell if t.attempts]
        rows.append(
            {
                "client_mode": mode,
                "transport": transport,
                "drop_point": point,
                "arm": arm,
                "trials": len(cell),
                "correct": sum(t.verdict == "correct" for t in cell),
                "duplicate": sum(t.verdict == "duplicate" for t in cell),
                "in_doubt_surfaced": sum(t.verdict == "in_doubt_surfaced" for t in cell),
                "stuck": sum(t.verdict == "stuck" for t in cell),
                "other_verdicts": ", ".join(
                    f"{v} x{n}"
                    for v in ("lost", "wrong_payload", "unconfirmed_success")
                    if (n := sum(t.verdict == v for t in cell))
                )
                or "-",
                "first_attempt": ", ".join(f"{k} x{v}" for k, v in sorted(first.items())),
                "protocol": ", ".join(f"{k} x{v}" for k, v in sorted(protocols.items())),
                "resume_requests": sum(t.resume_requests or 0 for t in cell),
                "trials_with_resume": sum(bool(t.resume_requests) for t in cell),
                "event_ids_forwarded": (
                    "-" if point == "none" else sum(ids_by_order.get(t.order_id, 0) for t in cell)
                ),
                "effects_committed": sum(t.effects_committed for t in cell),
                "requests_received": sum(t.requests_received for t in cell),
                "sdk_extra_requests": sum(t.sdk_extra_requests for t in cell),
                "client_requests": sum(t.client_requests or 0 for t in cell),
                "fault_confirmed": (
                    "-"
                    if all(t.fault_confirmed is None for t in cell)
                    else sum(bool(t.fault_confirmed) for t in cell)
                ),
                # Trials where what the client told its caller contradicts the sink.
                "report_mismatch": sum(t.report_matches_sink is False for t in cell),
                "first_attempt_median_s": round(statistics.median(firsts), 3) if firsts else "-",
                "changed_after_first_read": sum(t.changed_after_first_read for t in cell),
                "changed_by_end_of_run": sum(t.changed_by_end_of_run for t in cell),
            }
        )
    return rows


def format_table(rows: list[dict]) -> str:
    lines = [
        "| " + " | ".join(SUMMARY_COLUMNS) + " |",
        "|" + "---|" * len(SUMMARY_COLUMNS),
    ]
    lines += ["| " + " | ".join(str(r[h]) for h in SUMMARY_COLUMNS) + " |" for r in rows]
    return "\n".join(lines)


async def run_all(
    *,
    sink_url: str,
    mcp_urls: dict[str, str],
    client_modes: tuple[ClientMode, ...] = CLIENT_MODES,
    fault_cells: tuple[FaultCell, ...] = FAULT_CELLS,
    trials_per_cell: int,
    client_timeout_s: float,
    max_attempts: int,
) -> tuple[list[TrialResult], list[dict]]:
    """Run every (client mode, server, fault, arm) cell in a fixed order."""
    results: list[TrialResult] = []
    drops: list[dict] = []
    for mode in client_modes:
        for transport, mcp_url in mcp_urls.items():
            async with DropProxy(upstream_url=mcp_url, sink_url=sink_url) as proxy:
                for fault_cell in fault_cells:
                    for arm in ARMS:
                        for _ in range(trials_per_cell):
                            results.append(
                                await run_trial(
                                    arm=arm,
                                    fault=fault_cell.fault,
                                    mcp_url=mcp_url,
                                    sink_url=sink_url,
                                    client_timeout_s=client_timeout_s,
                                    max_attempts=max_attempts,
                                    transport=transport,
                                    drop_proxy=proxy,
                                    client_mode=mode,
                                    drop_point=fault_cell.drop_point or "no_bytes",
                                )
                            )
                drops += [
                    {
                        "client_mode": mode,
                        "transport": transport,
                        "order_id": d.order_id,
                        "drop_point": d.drop_point,
                        "committed_before_drop": d.committed_before_drop,
                        "bytes_forwarded": d.bytes_forwarded,
                        "event_ids_forwarded": d.event_ids_forwarded,
                        "bytes_withheld": d.bytes_withheld,
                    }
                    for d in proxy.drops
                ]
    await recheck_at_end(sink_url, results)
    return results, drops


def _names(value: str, known: tuple[str, ...], what: str) -> tuple[str, ...]:
    if value == "all":
        return known
    names = tuple(n.strip() for n in value.split(",") if n.strip())
    unknown = [n for n in names if n not in known]
    if not names or unknown:
        raise argparse.ArgumentTypeError(f"unknown {what}: {unknown}; choose from {list(known)}")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--local", action="store_true", required=True, help="start the stack")
    parser.add_argument("--pgdata", default=None, help="PostgreSQL data directory")
    parser.add_argument("--client-modes", default="all", help="auto, legacy, or all")
    parser.add_argument("--transports", default="all", help=", ".join(RESUME_TRANSPORTS))
    parser.add_argument(
        "--drop-points", default="all", help="none, no_bytes, after_headers, or all"
    )
    parser.add_argument("--trials", type=int, default=20, help="trials per cell")
    parser.add_argument("--client-timeout", type=float, default=5.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--out-dir", default="results/mcp_resume")
    args = parser.parse_args()

    try:
        modes = _names(args.client_modes, CLIENT_MODES, "client modes")
        transports = _names(args.transports, RESUME_TRANSPORTS, "transports")
        points = _names(args.drop_points, tuple(c.name for c in FAULT_CELLS), "drop points")
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    cells = tuple(c for c in FAULT_CELLS if c.name in points)

    from crashtest.experiment.local_stack import local_stack

    started = datetime.now(UTC)
    # Recorded before the run: the code that ran, not whatever is checked out at the end.
    run_provenance = provenance(REPO)
    with local_stack(args.pgdata, transports=transports) as stack:
        trials, drops = asyncio.run(
            run_all(
                sink_url=stack.sink_url,
                mcp_urls={t: stack.mcp_urls[t] for t in transports},
                client_modes=modes,  # type: ignore[arg-type]
                fault_cells=cells,
                trials_per_cell=args.trials,
                client_timeout_s=args.client_timeout,
                max_attempts=args.max_attempts,
            )
        )

    rows = summarise(trials, drops)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out_file.write_text(
        json.dumps(
            {
                "experiment": "mcp_resume",
                "started_at": started.isoformat(),
                "mode": "local (pgserver + subprocesses)",
                "parameters": {
                    "client_modes": list(modes),
                    "transports": list(transports),
                    "drop_points": [c.name for c in cells],
                    "trials_per_cell": args.trials,
                    "client_timeout_s": args.client_timeout,
                    "max_attempts": args.max_attempts,
                    "server_retry_interval_ms": None,
                },
                "environment": {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "mcp": metadata.version("mcp"),
                    "httpx": metadata.version("httpx"),
                    "fastapi": metadata.version("fastapi"),
                    "psycopg": metadata.version("psycopg"),
                },
                "provenance": run_provenance,
                "summary": rows,
                "connection_drops": drops,
                "trials": [t.to_dict() for t in trials],
            },
            indent=2,
        )
    )
    print(format_table(rows))
    print(f"\nwrote {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
