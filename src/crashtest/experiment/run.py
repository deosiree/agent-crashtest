"""Run the MCP refund experiment and write the results to a JSON file.

Against a running stack (for example Docker Compose), default transport only:
    python -m crashtest.experiment.run --sink-url http://localhost:8101 --mcp-url http://localhost:8102/mcp

Without Docker (starts PostgreSQL, the sink and one MCP server per transport):
    python -m crashtest.experiment.run --local
    python -m crashtest.experiment.run --local --transports all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from collections import Counter
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import httpx

from crashtest.experiment.crash_common import provenance
from crashtest.experiment.local_stack import DEFAULT_TRANSPORT, TRANSPORTS
from crashtest.experiment.trial import ARMS, FAULTS, TrialResult, run_trial
from crashtest.faults.drop_proxy import DropProxy

REPO = Path(__file__).resolve().parents[3]

SUMMARY_COLUMNS = (
    "transport",
    "fault",
    "arm",
    "trials",
    "correct",
    "duplicate",
    "lost",
    "effects_committed",
    "requests_received",
    "sdk_extra_requests",
    "client_saw_success",
    "first_attempt",
    "in_doubt_surfaced",
    "stuck",
    "other_verdicts",
    "client_requests",
    "missing_requests",
    "fault_confirmed",
    "report_mismatch",
    "changed_after_first_read",
)


def summarise(trials: list[TrialResult]) -> list[dict]:
    rows = []
    transports = list(dict.fromkeys(t.transport for t in trials))
    for transport in transports:
        for fault in FAULTS:
            for arm in ARMS:
                cell = [
                    t
                    for t in trials
                    if t.transport == transport and t.arm == arm and t.fault == fault
                ]
                if not cell:
                    continue
                first = Counter(t.attempts[0].outcome for t in cell if t.attempts)
                rows.append(
                    {
                        "transport": transport,
                        "fault": fault,
                        "arm": arm,
                        "trials": len(cell),
                        "correct": sum(t.verdict == "correct" for t in cell),
                        "duplicate": sum(t.verdict == "duplicate" for t in cell),
                        "lost": sum(t.verdict == "lost" for t in cell),
                        "effects_committed": sum(t.effects_committed for t in cell),
                        "requests_received": sum(t.requests_received for t in cell),
                        "sdk_extra_requests": sum(t.sdk_extra_requests for t in cell),
                        "client_saw_success": sum(t.client_saw_success for t in cell),
                        # What the first attempt looked like to the caller, with counts.
                        "first_attempt": ", ".join(f"{k} x{v}" for k, v in sorted(first.items())),
                        "in_doubt_surfaced": sum(t.verdict == "in_doubt_surfaced" for t in cell),
                        "stuck": sum(t.verdict == "stuck" for t in cell),
                        # Verdicts added in contract v0.2, which have no column of their own.
                        "other_verdicts": ", ".join(
                            f"{v} x{n}"
                            for v in ("wrong_payload", "unconfirmed_success")
                            if (n := sum(t.verdict == v for t in cell))
                        )
                        or "-",
                        # Tool calls counted at the proxy; "-" if no proxy was used.
                        "client_requests": (
                            "-"
                            if any(t.client_requests is None for t in cell)
                            else sum(t.client_requests or 0 for t in cell)
                        ),
                        # Attempts that never reached the sink.
                        "missing_requests": sum(max(0, -t.request_delta) for t in cell),
                        # Faulted trials where the fault is known to have fired
                        # after the commit; "-" for cells with no fault.
                        "fault_confirmed": (
                            "-"
                            if all(t.fault_confirmed is None for t in cell)
                            else sum(bool(t.fault_confirmed) for t in cell)
                        ),
                        # Trials whose report contradicts the sink (contract v0.3, RA).
                        "report_mismatch": sum(t.report_matches_sink is False for t in cell),
                        "changed_after_first_read": sum(t.changed_after_first_read for t in cell),
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
    faults: tuple[str, ...] = FAULTS,
    arms: tuple[str, ...] = ARMS,
    trials_per_cell: int,
    client_timeout_s: float,
    fault_delay_s: float,
    max_attempts: int,
) -> tuple[list[TrialResult], list[dict]]:
    """Run every (transport, fault, arm) cell. Returns the trials and the proxy's drop records."""
    results: list[TrialResult] = []
    drops: list[dict] = []
    for transport, mcp_url in mcp_urls.items():
        async with DropProxy(upstream_url=mcp_url, sink_url=sink_url) as proxy:
            for fault in faults:
                for arm in arms:
                    for _ in range(trials_per_cell):
                        results.append(
                            await run_trial(
                                arm=arm,  # type: ignore[arg-type]
                                fault=fault,  # type: ignore[arg-type]
                                mcp_url=mcp_url,
                                sink_url=sink_url,
                                client_timeout_s=client_timeout_s,
                                fault_delay_s=fault_delay_s,
                                max_attempts=max_attempts,
                                transport=transport,
                                drop_proxy=proxy,
                            )
                        )
            drops += [
                {
                    "transport": transport,
                    "order_id": d.order_id,
                    "committed_before_drop": d.committed_before_drop,
                    "bytes_withheld": d.bytes_withheld,
                }
                for d in proxy.drops
            ]
    await recheck_at_end(sink_url, results)
    return results, drops


async def recheck_at_end(sink_url: str, results: list[TrialResult]) -> int:
    """Read every order again once the run is over, and flag any whose count moved.

    A refund that commits after its trial's last read (a request still in flight,
    a retry from a client that outlived the trial) would otherwise go unrecorded.
    Returns the number of trials flagged.
    """
    flagged = 0
    async with httpx.AsyncClient(base_url=sink_url, timeout=30.0) as sink:
        for result in results:
            r = await sink.get("/refunds", params={"order_id": result.order_id})
            r.raise_for_status()
            if len(r.json()["refunds"]) != result.effects_committed:
                result.changed_by_end_of_run = True
                flagged += 1
    if flagged:
        print(f"WARNING: {flagged} trial(s) changed after they ended", file=sys.stderr)
    return flagged


def _parse_names(value: str, known: tuple[str, ...], what: str) -> tuple[str, ...]:
    if value == "all":
        return known
    names = tuple(n.strip() for n in value.split(",") if n.strip())
    unknown = [n for n in names if n not in known]
    if unknown or not names:
        raise argparse.ArgumentTypeError(f"unknown {what}: {unknown}; choose from {list(known)}")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--local", action="store_true", help="start the stack on this machine without Docker"
    )
    parser.add_argument("--pgdata", default=None, help="PostgreSQL data directory for --local")
    parser.add_argument("--sink-url", default="http://localhost:8101")
    parser.add_argument("--mcp-url", default="http://localhost:8102/mcp")
    parser.add_argument(
        "--transports",
        default=DEFAULT_TRANSPORT,
        help=f"comma-separated, or 'all' (needs --local): {', '.join(TRANSPORTS)}",
    )
    parser.add_argument(
        "--faults", default="all", help=f"comma-separated, or 'all': {', '.join(FAULTS)}"
    )
    parser.add_argument(
        "--arms",
        default="all",
        help=f"client strategies, comma-separated, or 'all': {', '.join(ARMS)}",
    )
    parser.add_argument("--trials", type=int, default=20, help="trials per cell")
    parser.add_argument("--client-timeout", type=float, default=2.0)
    parser.add_argument("--fault-delay", type=float, default=5.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--out-dir", default="results/mcp_refund")
    parser.add_argument(
        "--allow-delay-below-timeout",
        action="store_true",
        help="for the timeout-ratio sweep: let the response arrive before the client times out",
    )
    args = parser.parse_args()

    if args.fault_delay <= args.client_timeout and not args.allow_delay_below_timeout:
        parser.error("--fault-delay must exceed --client-timeout, or the response is never lost")
    try:
        transports = _parse_names(args.transports, tuple(TRANSPORTS), "transports")
        faults = _parse_names(args.faults, FAULTS, "faults")
        arms = _parse_names(args.arms, ARMS, "arms")
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if not args.local and transports != (DEFAULT_TRANSPORT,):
        parser.error("an external stack serves one transport; use --local for the others")

    def go(sink_url: str, mcp_urls: dict[str, str]) -> tuple[list[TrialResult], list[dict]]:
        return asyncio.run(
            run_all(
                sink_url=sink_url,
                mcp_urls=mcp_urls,
                faults=faults,
                arms=arms,
                trials_per_cell=args.trials,
                client_timeout_s=args.client_timeout,
                fault_delay_s=args.fault_delay,
                max_attempts=args.max_attempts,
            )
        )

    started = datetime.now(UTC)
    # Recorded before the run: the code that ran, not whatever is checked out at the end.
    run_provenance = provenance(REPO)
    if args.local:
        from crashtest.experiment.local_stack import local_stack

        with local_stack(args.pgdata, transports=transports) as stack:
            trials, drops = go(stack.sink_url, {t: stack.mcp_urls[t] for t in transports})
        mode = "local (pgserver + subprocesses)"
    else:
        trials, drops = go(args.sink_url, {DEFAULT_TRANSPORT: args.mcp_url})
        mode = "external stack"

    rows = summarise(trials)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out_file.write_text(
        json.dumps(
            {
                "experiment": "mcp_refund",
                "started_at": started.isoformat(),
                "mode": mode,
                "parameters": {
                    "transports": list(transports),
                    "faults": list(faults),
                    "arms": list(arms),
                    "trials_per_cell": args.trials,
                    "client_timeout_s": args.client_timeout,
                    "fault_delay_s": args.fault_delay,
                    "max_attempts": args.max_attempts,
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
