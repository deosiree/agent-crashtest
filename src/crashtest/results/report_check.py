"""Derive contract v0.3's `report_matches_sink` for every recorded trial.

Results files are never edited. Runners record the field from contract v0.3 on;
this derives it for the files written before, from the fields each runner
recorded, with the same rules the runners now apply, and counts it per file
beside the recorded verdict. No database is needed: the effect count is taken as
recorded (for 006 it was checked against the sink export in the validation).

    python -m crashtest.results.report_check [--out FILE] [files ...]

For the 006 main run it also gives the counts with the hand codes in place of
the coder, where a trial was read by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from crashtest.checker import REPORT_OF_STATUS, Report, report_matches_sink
from crashtest.experiment.model_retry import report_of
from crashtest.results.reverdict import _status_and_confirmed

REPO = Path(__file__).resolve().parents[3]
MAIN_006 = "results/model_retry/20261118T035136Z.json"
HAND_CODES_006 = "tests/data/m5_main_hand_codes.json"


def report_of_trial(experiment: str, t: dict, told_user: str | None = None) -> Report:
    """What the subject told its caller, from the fields its runner recorded."""
    if experiment == "model_retry":
        if t.get("stop_reason") != "end_turn":
            return "none"
        return report_of(told_user if told_user is not None else t["told_user"])
    status, _ = _status_and_confirmed(experiment, t)
    if experiment in ("langgraph_crash", "dbos_crash"):
        # The scripted model either answers after the tool result or says nothing.
        return "done" if status == "completed" else "none"
    # 001 to 003: success, an ambiguous error, or a definite one reached the caller.
    return REPORT_OF_STATUS[status]


def _count(experiment: str, trials: list[dict], hand: dict[str, Any] | None = None) -> dict:
    by_verdict: Counter[str] = Counter()
    false_by: Counter[str] = Counter()
    for position, t in enumerate(trials):
        code = hand[str(position)]["code"] if hand and str(position) in hand else None
        report = report_of_trial(experiment, t, code)
        ok = report_matches_sink(
            effects=t["effects_committed"], intended=t.get("intended_effects", 1), report=report
        )
        by_verdict[f"{t.get('verdict')} / {'matches' if ok else 'contradicts'}"] += 1
        if not ok:
            false_by[f"reported {report}, sink {t['effects_committed']}"] += 1
    return {
        "report_matches_sink_false": sum(false_by.values()),
        "false_by_report_and_effects": dict(sorted(false_by.items())),
        "by_verdict": dict(sorted(by_verdict.items())),
    }


def review_file(path: Path) -> dict[str, Any] | None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "trials" not in doc:
        return None
    experiment = doc["experiment"]
    # A trial that stopped at an API error before any model call has no record.
    trials = [t for t in doc["trials"] if not t.get("error")]
    rel = path.resolve().relative_to(REPO).as_posix()
    out: dict[str, Any] = {
        "file": rel,
        "experiment": experiment,
        "trials": len(trials),
        "skipped_error_trials": len(doc["trials"]) - len(trials),
        **_count(experiment, trials),
    }
    if rel == MAIN_006:
        hand = json.loads((REPO / HAND_CODES_006).read_text("utf-8"))["labels"]
        out["with_hand_codes"] = _count(experiment, trials, hand)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, help="write the full report as JSON here")
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args()
    files = args.files or sorted(REPO.glob("results/*/*.json"))
    reports = [r for f in files if (r := review_file(f.resolve())) is not None]
    for r in reports:
        print(
            f"{r['file']:52} {r['trials']:5}  contradicts={r['report_matches_sink_false']:4}  "
            f"{r['false_by_report_and_effects']}"
        )
    if args.out:
        if args.out.exists():
            raise SystemExit(f"{args.out} exists; write a new file")
        args.out.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
