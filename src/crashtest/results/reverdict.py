"""Recompute the verdict of every recorded trial under the current contract.

Results files are never edited. This reads them, takes each trial's effect count
as recorded, reads the committed refunds' payloads from the sink database, and
derives the subject's status and whether its success was confirmed from the
fields the runner recorded. It reports, per file, how the verdicts compare.

    python -m crashtest.results.reverdict --database-url URL [--out FILE]

Contract v0.1 is v0.2 with `payload_matches=True, confirmed=True`, so the v0.1
verdict of a file written before the checker existed is computed that way.

A trial whose refunds are no longer in the given sink database (an earlier
database, or a run on another machine, such as the GitHub runs) cannot be
re-judged. It is counted as `payload_not_verifiable`, not as a change.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg

from crashtest.checker import SubjectStatus, Verdict, classify
from crashtest.experiment.trial import AMBIGUOUS_OUTCOMES

REPO = Path(__file__).resolve().parents[3]


def _status_and_confirmed(experiment: str, t: dict) -> tuple[SubjectStatus, bool]:
    """The subject's status and whether a reported success was confirmed, per runner."""
    if experiment in (
        "mcp_refund",
        "mcp_refund_lost_response",
        "mcp_resume",
        "mcp_cut_points",
    ):
        if t["client_saw_success"]:
            return "completed", True  # a success response, first-hand or replayed
        attempts = t.get("attempts") or []
        last = attempts[-1]["outcome"] if attempts else ""
        return ("unknown" if last in AMBIGUOUS_OUTCOMES else "failed"), False
    if experiment == "mcp_single_call":
        outcome = t.get("subject_outcome")
        if outcome == "ok":
            return "completed", True  # reported only from a successful tool result
        return ("unknown" if outcome == "raised" else "failed"), False
    if experiment in ("langgraph_crash", "dbos_crash"):
        last = t["resume"] if t.get("killed") else t["first_run"]
        done = last.get("outcome") == "ok" and bool(last.get("answered"))
        return ("completed" if done else "failed"), done  # answers only after a tool result
    raise ValueError(f"no status rule for experiment {experiment!r}")


def _payloads(conn: psycopg.Connection, order_ids: list[str]) -> dict[str, list[int]]:
    rows = conn.execute(
        "SELECT order_id, amount_cents FROM refunds WHERE order_id = ANY(%s)", (order_ids,)
    ).fetchall()
    out: dict[str, list[int]] = {}
    for order_id, amount in rows:
        out.setdefault(order_id, []).append(amount)
    return out


def review_file(conn: psycopg.Connection, path: Path, amount_cents: int = 2500) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    experiment = doc["experiment"]
    trials = doc["trials"]
    payloads = _payloads(conn, [t["order_id"] for t in trials])

    old_counts: Counter[str] = Counter()
    new_counts: Counter[str] = Counter()
    changed: list[dict] = []
    unverifiable = 0
    for position, t in enumerate(trials):
        effects = t["effects_committed"]
        intended = t.get("intended_effects", 1)
        status, confirmed = _status_and_confirmed(experiment, t)
        old: Verdict = t.get("verdict") or classify(
            effects=effects, intended=intended, status=status, payload_matches=True, confirmed=True
        )
        amounts = payloads.get(t["order_id"])
        if effects and amounts is None:
            unverifiable += 1
            new = "payload_not_verifiable"
        else:
            new = classify(
                effects=effects,
                intended=intended,
                status=status,
                payload_matches=all(a == amount_cents for a in amounts or []),
                confirmed=confirmed,
            )
        old_counts[old] += 1
        new_counts[new] += 1
        if new != old and new != "payload_not_verifiable":
            changed.append(
                {"position": position, "order_id": t["order_id"], "v0.1": old, "v0.2": new}
            )
    return {
        "file": "/".join(path.parts[-3:]),  # results/<experiment>/<stamp>.json
        "experiment": experiment,
        "trials": len(trials),
        "v0.1": dict(sorted(old_counts.items())),
        "v0.2": dict(sorted(new_counts.items())),
        "changed": changed,
        "payload_not_verifiable": unverifiable,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database-url", required=True, help="the sink's database")
    parser.add_argument("--out", type=Path, help="write the full report as JSON here")
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args()
    files = args.files or sorted(REPO.glob("results/*/*.json"))

    with psycopg.connect(args.database_url) as conn:
        reports = [review_file(conn, f.resolve()) for f in files]
    for r in reports:
        print(
            f"{r['file']:50} {r['trials']:4}  changed={len(r['changed']):3}  "
            f"unverifiable={r['payload_not_verifiable']:3}  v0.2={r['v0.2']}"
        )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
