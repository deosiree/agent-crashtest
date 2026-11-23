"""Experiment 008: cut the connection before the request and mid-stream.

Pre-registered design: docs/m2-cut-points-plan.md. The trial is experiments
001/002's (`crashtest.experiment.trial.run_trial`: "refund this order once",
retried by the application on a timeout or a disconnect), with the factors of
experiment 007 (client connect mode, server with and without an event store) and
three cut points (`crashtest.faults.drop_proxy`): after the commit (`no_bytes`,
002's point and the negative control), before the request reaches the server
(`before_request`), and mid-stream, after a progress event and before the commit
(`mid_stream`). The servers' tool sends one progress notification and then pauses
`local_stack.PROGRESS_DELAY_S` before it calls the sink.

    python -m crashtest.experiment.cut_points --local --trials 10
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

from crashtest.experiment.crash_common import provenance
from crashtest.experiment.resume import CLIENT_MODES, ClientMode, _names
from crashtest.experiment.run import recheck_at_end
from crashtest.experiment.trial import ARMS, Fault, TrialResult, run_trial
from crashtest.faults.drop_proxy import DropPoint, DropProxy

REPO = Path(__file__).resolve().parents[3]

CUT_TRANSPORTS: tuple[str, ...] = ("stateful_sse_progress", "stateful_sse_store_progress")


@dataclass(frozen=True)
class CutCell:
    fault: Fault
    drop_point: DropPoint | None

    @property
    def name(self) -> str:
        return self.drop_point or "none"


CUT_CELLS: tuple[CutCell, ...] = (
    CutCell("none", None),
    CutCell("connection_drop", "no_bytes"),
    CutCell("connection_drop", "before_request"),
    CutCell("connection_drop", "mid_stream"),
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
    "effects_1",
    "effects_2",
    "first_attempt",
    "first_raised",
    "protocol",
    "progress_seen_first",
    "resume_requests",
    "trials_with_resume",
    "requests_received",
    "attempts",
    "request_delta",
    "client_requests",
    "fault_confirmed",
    "report_mismatch",
    "first_attempt_median_s",
    "changed_after_first_read",
    "changed_by_end_of_run",
)


def summarise(trials: list[TrialResult]) -> list[dict]:
    """One row per cell, in the order the cells were run."""

    def key(t: TrialResult) -> tuple[str, str, str, str]:
        return (t.client_mode, t.transport, t.drop_point or "none", t.arm)

    rows = []
    for cell_key in dict.fromkeys(key(t) for t in trials):
        mode, transport, point, arm = cell_key
        cell = [t for t in trials if key(t) == cell_key]
        first = Counter(t.attempts[0].outcome for t in cell if t.attempts)
        raised = Counter(t.attempts[0].raised or "-" for t in cell if t.attempts)
        protocols = Counter(a.protocol or "-" for t in cell for a in t.attempts)
        progress = Counter(str(t.attempts[0].progress_seen) for t in cell if t.attempts)
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
                "effects_1": sum(t.effects_committed == 1 for t in cell),
                "effects_2": sum(t.effects_committed == 2 for t in cell),
                "first_attempt": ", ".join(f"{k} x{v}" for k, v in sorted(first.items())),
                "first_raised": ", ".join(f"{k} x{v}" for k, v in sorted(raised.items())),
                "protocol": ", ".join(f"{k} x{v}" for k, v in sorted(protocols.items())),
                "progress_seen_first": ", ".join(f"{k} x{v}" for k, v in sorted(progress.items())),
                "resume_requests": sum(t.resume_requests or 0 for t in cell),
                "trials_with_resume": sum(bool(t.resume_requests) for t in cell),
                "requests_received": sum(t.requests_received for t in cell),
                "attempts": sum(len(t.attempts) for t in cell),
                # Sink requests minus attempts, per trial: 0 when every attempt
                # reached the sink, -1 when one never did.
                "request_delta": ", ".join(
                    f"{k} x{v}" for k, v in sorted(Counter(t.request_delta for t in cell).items())
                ),
                "client_requests": sum(t.client_requests or 0 for t in cell),
                "fault_confirmed": (
                    "-"
                    if all(t.fault_confirmed is None for t in cell)
                    else sum(bool(t.fault_confirmed) for t in cell)
                ),
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
    cut_cells: tuple[CutCell, ...] = CUT_CELLS,
    trials_per_cell: int,
    client_timeout_s: float,
    max_attempts: int,
) -> tuple[list[TrialResult], list[dict]]:
    """Run every (client mode, server, cut point, arm) cell in a fixed order."""
    results: list[TrialResult] = []
    drops: list[dict] = []
    for mode in client_modes:
        for transport, mcp_url in mcp_urls.items():
            async with DropProxy(upstream_url=mcp_url, sink_url=sink_url) as proxy:
                for cell in cut_cells:
                    for arm in ARMS:
                        for _ in range(trials_per_cell):
                            results.append(
                                await run_trial(
                                    arm=arm,
                                    fault=cell.fault,
                                    mcp_url=mcp_url,
                                    sink_url=sink_url,
                                    client_timeout_s=client_timeout_s,
                                    max_attempts=max_attempts,
                                    transport=transport,
                                    drop_proxy=proxy,
                                    client_mode=mode,
                                    drop_point=cell.drop_point or "no_bytes",
                                    progress=True,
                                )
                            )
                drops += [
                    {
                        "client_mode": mode,
                        "transport": transport,
                        "order_id": d.order_id,
                        "drop_point": d.drop_point,
                        "landed_as_intended": d.landed_as_intended(),
                        "committed_before_drop": d.committed_before_drop,
                        "request_withheld": d.request_withheld,
                        "notifications_forwarded": d.notifications_forwarded,
                        "response_forwarded": d.response_forwarded,
                        "sink_requests_at_cut": d.sink_requests_at_cut,
                        "sink_refunds_at_cut": d.sink_refunds_at_cut,
                        "bytes_forwarded": d.bytes_forwarded,
                        "event_ids_forwarded": d.event_ids_forwarded,
                        "bytes_withheld": d.bytes_withheld,
                    }
                    for d in proxy.drops
                ]
    await recheck_at_end(sink_url, results)
    return results, drops


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--local", action="store_true", required=True, help="start the stack")
    parser.add_argument("--pgdata", default=None, help="PostgreSQL data directory")
    parser.add_argument("--client-modes", default="all", help="auto, legacy, or all")
    parser.add_argument("--transports", default="all", help=", ".join(CUT_TRANSPORTS))
    parser.add_argument(
        "--cut-points",
        default="all",
        help="none, no_bytes, before_request, mid_stream, or all",
    )
    parser.add_argument("--trials", type=int, default=10, help="trials per cell")
    parser.add_argument("--client-timeout", type=float, default=10.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--out-dir", default="results/mcp_cut_points")
    args = parser.parse_args()

    try:
        modes = _names(args.client_modes, CLIENT_MODES, "client modes")
        transports = _names(args.transports, CUT_TRANSPORTS, "transports")
        points = _names(args.cut_points, tuple(c.name for c in CUT_CELLS), "cut points")
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    cells = tuple(c for c in CUT_CELLS if c.name in points)

    from crashtest.experiment.local_stack import PROGRESS_DELAY_S, local_stack

    started = datetime.now(UTC)
    # Recorded before the run: the code that ran, not whatever is checked out at the end.
    run_provenance = provenance(REPO)
    with local_stack(args.pgdata, transports=transports) as stack:
        trials, drops = asyncio.run(
            run_all(
                sink_url=stack.sink_url,
                mcp_urls={t: stack.mcp_urls[t] for t in transports},
                client_modes=modes,  # type: ignore[arg-type]
                cut_cells=cells,
                trials_per_cell=args.trials,
                client_timeout_s=args.client_timeout,
                max_attempts=args.max_attempts,
            )
        )

    rows = summarise(trials)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out_file.write_text(
        json.dumps(
            {
                "experiment": "mcp_cut_points",
                "started_at": started.isoformat(),
                "mode": "local (pgserver + subprocesses)",
                "parameters": {
                    "client_modes": list(modes),
                    "transports": list(transports),
                    "drop_points": [c.name for c in cells],
                    "trials_per_cell": args.trials,
                    "client_timeout_s": args.client_timeout,
                    "max_attempts": args.max_attempts,
                    "server_progress_delay_s": PROGRESS_DELAY_S,
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
