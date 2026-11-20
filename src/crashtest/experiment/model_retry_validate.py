"""Validate experiment 006 (M5) against the sink's own rows. No API calls.

Checks cohorts, every trial against the sink, who sent each refund, the
duplicates, the status tool, exact bounds and the verdicts. Reads the three complete 006
results files, the ledger and the sink export (every PostgreSQL row for the
006 orders, `results/model_retry/20261118-sink-export.json`), and checks the
runner's records against the export. Nothing here decides a verdict except
`crashtest.checker.classify`.

    python -m crashtest.experiment.model_retry_validate
    python -m crashtest.experiment.model_retry_validate --out results/model_retry/<new>.json

The write-up is docs/experiments/006-validation.md.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from crashtest.checker import classify, payload_matches
from crashtest.experiment.model_retry import (
    AMOUNT_CENTS,
    FRAMINGS,
    MODELS,
    STATUS_TOOL,
    code_final_text,
    plan_cells,
    subject_status,
)

REPO = Path(__file__).resolve().parents[3]
RESULTS = "results/model_retry"
EXPORT = f"{RESULTS}/20261118-sink-export.json"
MAIN = f"{RESULTS}/20261118T035136Z.json"
LEDGER = f"{RESULTS}/spend.jsonl"
HAND_CODES = "tests/data/m5_main_hand_codes.json"
SONNET_HAND_COUNT = "tests/data/m5_sonnet_duplicates_hand_count.json"


# ---------------------------------------------------------------- exact statistics


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return math.fsum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def _bisect(f: Any, lo: float, hi: float) -> float:
    """Root of a function that is decreasing on [lo, hi]."""
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Exact (Clopper-Pearson) two-sided confidence interval for k successes in n."""
    if not 0 <= k <= n or n == 0:
        raise ValueError(f"need 0 <= k <= n and n > 0, got k={k}, n={n}")
    a = (1 - level) / 2
    lower = 0.0 if k == 0 else _bisect(lambda p: a - (1 - _binom_cdf(k - 1, n, p)), 0.0, 1.0)
    upper = 1.0 if k == n else _bisect(lambda p: _binom_cdf(k, n, p) - a, 0.0, 1.0)
    return lower, upper


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact test p-value for the table [[a, b], [c, d]].

    Sums, exactly, the probabilities of every table with the same margins that is
    no more likely than the observed one (the convention R's fisher.test uses).
    """
    row1, col1, n = a + b, a + c, a + b + c + d
    total = math.comb(n, row1)

    def prob(x: int) -> Fraction:
        return Fraction(math.comb(col1, x) * math.comb(n - col1, row1 - x), total)

    observed = prob(a)
    lo, hi = max(0, row1 + col1 - n), min(row1, col1)
    return float(sum(p for x in range(lo, hi + 1) if (p := prob(x)) <= observed))


def rate(k: int, n: int) -> dict:
    lo, hi = clopper_pearson(k, n)
    return {"k": k, "n": n, "ci95": [round(lo, 4), round(hi, 4)]}


# ---------------------------------------------------------------- loading


def _load(path: str) -> Any:
    return json.loads((REPO / path).read_text(encoding="utf-8"))


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _by_order(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["id"]):
        out[r["order_id"]].append(r)
    return out


def _contiguous(ids: list[int]) -> bool:
    return ids == list(range(ids[0], ids[0] + len(ids))) if ids else True


# ---------------------------------------------------------------- the checks


def check_cohorts(export: dict, runs: dict[str, dict], ledger: list[dict]) -> dict:
    """Step 2: each run is the seeded plan, trial for trial; order IDs are unique."""
    out: dict[str, Any] = {"runs": {}}
    all_orders: list[str] = []
    for path, doc in runs.items():
        p, trials = doc["parameters"], doc["trials"]
        cells = plan_cells(
            MODELS,
            tuple(FRAMINGS),
            STATUS_TOOL,
            p["trials_per_cell"],
            p["controls_per_model"],
            p["seed"],
        )
        got = [(t["model"], t["framing"], t["status_tool"]) for t in trials]
        per_cell = Counter(got)
        orders = [t["order_id"] for t in trials]
        all_orders += orders
        out["runs"][path] = {
            "pilot": p["pilot"],
            "seed": p["seed"],
            "trials": len(trials),
            "planned": len(cells),
            "cells": len(per_cell),
            "trials_per_cell": sorted(set(per_cell.values())),
            "matches_seeded_plan_in_order": got == [tuple(c) for c in cells],
            "sequence_is_0_to_n": [t["sequence"] for t in trials] == list(range(len(trials))),
            "errors": sum(bool(t["error"]) for t in trials),
            "stop_reasons": dict(Counter(t["stop_reason"] for t in trials)),
            "commit": doc["provenance"]["git_commit"][:7],
            "dirty": doc["provenance"]["git_dirty"],
            "orders_mapped_to_this_file_in_export": sum(
                export["order_to_file"].get(o) == path for o in orders
            ),
        }
    ledger_orders = Counter(e["order_id"] for e in ledger)
    request_ids = [
        r["request_id"] for doc in runs.values() for t in doc["trials"] for r in t["requests"]
    ]
    trial_request_ids = Counter(request_ids)
    ledger_request_ids = Counter(i for e in ledger for i in e["request_ids"])
    out["order_ids_unique_across_runs"] = len(all_orders) == len(set(all_orders))
    out["orders_in_export"] = len(export["order_to_file"])
    out["export_orders_equal_run_orders"] = set(export["order_to_file"]) == set(all_orders)
    out["ledger_entries"] = len(ledger)
    out["ledger_orders_equal_run_orders"] = set(ledger_orders) == set(all_orders) and all(
        v == 1 for v in ledger_orders.values()
    )
    out["ledger_request_ids_equal_trial_request_ids"] = ledger_request_ids == trial_request_ids
    out["request_ids_unique"] = all(v == 1 for v in trial_request_ids.values())
    costs = {e["order_id"]: e["cost_usd"] for e in ledger}
    out["ledger_cost_equals_trial_cost"] = all(
        math.isclose(costs[t["order_id"]], t["cost_usd"])
        for doc in runs.values()
        for t in doc["trials"]
    )
    out["ledger_total_usd"] = round(math.fsum(e["cost_usd"] for e in ledger), 4)
    return out


def check_trials(export: dict, runs: dict[str, dict]) -> dict:
    """Steps 3 and 4: every trial's record against the sink's rows."""
    refunds = _by_order(export["refunds"])
    log = _by_order(export["request_log"])
    out: dict[str, Any] = {"runs": {}}
    for path, doc in runs.items():
        trials = doc["trials"]
        mismatches: list[dict] = []
        for t in trials:
            rs, ls = refunds[t["order_id"]], log[t["order_id"]]
            problems = []
            if len(rs) != t["effects_committed"]:
                problems.append("recorded effect count differs from the sink's rows")
            if len(ls) != len(t["refund_calls"]):
                problems.append("sink requests differ from the model's issue_refund calls")
            if any(r["amount_cents"] != AMOUNT_CENTS or r["order_id"] != t["order_id"] for r in rs):
                problems.append("a refund row has the wrong amount or order")
            if any(
                c.get("order_id") != t["order_id"] or c.get("amount_cents") != AMOUNT_CENTS
                for c in t["refund_calls"]
            ):
                problems.append("a model call named another order or amount")
            if any(r["outcome"] != "created" or r["fault_applied"] for r in ls):
                problems.append("a request was not a plain create")
            if t["framing"] != "control" and not rs:
                problems.append("faulted trial without the first refund committed")
            # Each response that stopped for tool use asked for exactly one tool, so
            # a second issue_refund came in a later response, after the model had
            # been given the first call's (error) result.
            tool_responses = sum(r["stop_reason"] == "tool_use" for r in t["requests"])
            if tool_responses != len(t["refund_calls"]) + t["lookups"]:
                problems.append("a response asked for more than one tool")
            if problems:
                mismatches.append({"sequence": t["sequence"], "problems": problems})
        rows = [r for t in trials for r in refunds[t["order_id"]]]
        ids = sorted(r["id"] for r in rows)
        log_ids = sorted(r["id"] for t in trials for r in log[t["order_id"]])
        out["runs"][path] = {
            "trials": len(trials),
            "refund_rows": len(rows),
            "trials_refunded_twice": sum(len(refunds[t["order_id"]]) == 2 for t in trials),
            "trials_refunded_more_than_twice": sum(len(refunds[t["order_id"]]) > 2 for t in trials),
            "refund_calls": sum(len(t["refund_calls"]) for t in trials),
            "mismatches": mismatches,
            # A gap-free id range means no other refund (to a mistyped order, or
            # from anything else) was inserted while the run was in progress.
            "refund_ids": [ids[0], ids[-1]],
            "refund_ids_contiguous": _contiguous(ids),
            "request_log_ids_contiguous": _contiguous(log_ids),
            "first_and_last_refund_at": [
                min(r["created_at"] for r in rows),
                max(r["created_at"] for r in rows),
            ],
        }
    return out


def _first_refund_at(export: dict) -> dict[str, datetime]:
    return {o: _ts(rs[0]["created_at"]) for o, rs in _by_order(export["refunds"]).items()}


def check_duplicates(export: dict, main: dict, pilots: dict[str, dict]) -> dict:
    """Step 5: Sonnet's re-sends, from the sink's timestamps."""
    refunds = _by_order(export["refunds"])
    trials = []
    for t in main["trials"]:
        rs = refunds[t["order_id"]]
        if len(rs) < 2:
            continue
        trials.append(
            {
                "sequence": t["sequence"],
                "cell": [t["model"], t["framing"], t["status_tool"]],
                "stop_reasons": [r["stop_reason"] for r in t["requests"]],
                "refund_ids": [r["id"] for r in rs],
                "seconds_between_refunds": round(
                    (_ts(rs[1]["created_at"]) - _ts(rs[0]["created_at"])).total_seconds(), 3
                ),
                "arguments": t["arg_comparisons"],
                "arg_hash_would_dedupe": t["arg_hash_would_dedupe"],
                "told_user_coded": t["told_user"],
            }
        )
    gaps = [t["seconds_between_refunds"] for t in trials]
    cell = ("claude-sonnet-5-5", "generic", "absent")
    pilot_cells = {}
    for path, doc in pilots.items():
        c = [t for t in doc["trials"] if (t["model"], t["framing"], t["status_tool"]) == cell]
        k = sum(len(refunds[t["order_id"]]) > 1 for t in c)
        pilot_cells[path] = {**rate(k, len(c)), "sequences": [t["sequence"] for t in c]}
    return {
        "trials": trials,
        "count": len(trials),
        "all_in_one_cell": sorted({tuple(t["cell"]) for t in trials}) == [cell],
        "all_three_calls_tool_tool_end": all(
            t["stop_reasons"] == ["tool_use", "tool_use", "end_turn"] for t in trials
        ),
        "seconds_between_refunds": {
            "min": min(gaps),
            "median": statistics.median(gaps),
            "max": max(gaps),
        },
        "same_cell_in_pilots": pilot_cells,
    }


def _kind(t: dict) -> str:
    return "P" if t["status_tool"] == "present" else "A"


def check_status_tool(export: dict, main: dict) -> dict:
    """Step 6: what the lookup returned and what the models did next.

    The runner does not record the order of tool calls within a trial. The order
    is inferred from the sink's timestamps: trials ran one after another, so the
    time from one trial's first refund to the next trial's first refund counts the
    model calls after the first refund in the earlier trial plus those before it
    in the later one. Only same-model pairs are compared.
    """
    refunds = _by_order(export["refunds"])
    trials = main["trials"]
    present = [t for t in trials if t["status_tool"] == "present"]
    per_model = {}
    for m in MODELS:
        c = [t for t in present if t["model"] == m]
        per_model[m] = {
            "trials": len(c),
            "lookups_per_trial": dict(Counter(t["lookups"] for t in c)),
            "model_calls_per_trial": dict(Counter(t["model_calls"] for t in c)),
            "reissued": sum(t["reissued"] > 0 for t in c),
            "refunds_at_sink": dict(Counter(len(refunds[t["order_id"]]) for t in c)),
            "saw_a_refund_in_a_lookup": rate(sum(t["saw_confirmation"] for t in c), len(c)),
            # The refund id reached the model only through get_refunds: the
            # issue_refund result it saw was the error.
            "final_message_names_the_sink_refund_id": rate(
                sum(str(refunds[t["order_id"]][0]["id"]) in t["final_text"] for t in c), len(c)
            ),
        }
    first = _first_refund_at(export)
    gaps: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for a, b in zip(trials, trials[1:], strict=False):
        # Only two-call no-tool trials, so that every "A" has one call each side.
        if a["model"] != b["model"] or any(
            t["status_tool"] == "absent" and t["model_calls"] != 2 for t in (a, b)
        ):
            continue
        pair = f"{_kind(a)}>{_kind(b)}"
        gaps[a["model"]][pair].append((first[b["order_id"]] - first[a["order_id"]]).total_seconds())
    timing = {
        m: {
            k: {"pairs": len(v), "median_s": round(statistics.median(v), 2)}
            for k, v in sorted(g.items())
        }
        for m, g in gaps.items()
    }
    # Calls between consecutive first refunds, by hypothesis (A has 1 before, 1 after):
    #   refund first  (refund, lookup, lookup, answer): P has 1 before, 3 after
    #   lookup first  (lookup, refund, lookup, answer): P has 2 before, 2 after
    predicted = {
        "refund_first": {"A>A": 2, "A>P": 2, "P>A": 4, "P>P": 4},
        "lookup_first": {"A>A": 2, "A>P": 3, "P>A": 3, "P>P": 4},
    }
    fits_lookup_first = {
        m: t["A>P"]["median_s"] > t["A>A"]["median_s"]
        and t["P>A"]["median_s"] < t["P>P"]["median_s"]
        for m, t in timing.items()
    }
    return {
        "per_model": per_model,
        "timing_between_consecutive_first_refunds": timing,
        "calls_between_first_refunds_predicted": predicted,
        "timing_fits_lookup_first": fits_lookup_first,
    }


def check_rates(main: dict, pilots: dict[str, dict]) -> dict:
    """Step 7: exact 95% bounds for the rates the 006 write-up reports."""
    trials = main["trials"]
    cells = {}
    for m in MODELS:
        for f in (*FRAMINGS, "control"):
            for s in STATUS_TOOL:
                c = [t for t in trials if (t["model"], t["framing"], t["status_tool"]) == (m, f, s)]
                if c:
                    cells[f"{m} {f} {s}"] = rate(sum(t["reissued"] > 0 for t in c), len(c))
    out: dict[str, Any] = {"reissue_per_cell": cells}
    by_model: dict[str, Any] = {}
    for m in MODELS:
        faulted = [t for t in trials if t["model"] == m and t["framing"] != "control"]
        h4 = [
            t
            for t in faulted
            if t["status_tool"] == "absent" and t["framing"] in ("timeout", "generic")
        ]
        other = [
            t
            for t in faulted
            if not (
                m == "claude-sonnet-5-5"
                and t["framing"] == "generic"
                and t["status_tool"] == "absent"
            )
        ]
        absent = [t for t in faulted if t["status_tool"] == "absent"]
        present = [t for t in faulted if t["status_tool"] == "present"]
        by_model[m] = {
            "h4_reissue_timeout_and_generic_absent": rate(
                sum(t["reissued"] > 0 for t in h4), len(h4)
            ),
            "reissue_in_faulted_trials_outside_sonnet_generic_absent": rate(
                sum(t["reissued"] > 0 for t in other), len(other)
            ),
            "looked_up_with_status_tool": rate(
                sum(t["lookups"] > 0 for t in present), len(present)
            ),
            "reissue_with_status_tool": rate(sum(t["reissued"] > 0 for t in present), len(present)),
            "told_user_coded_absent": dict(Counter(t["told_user"] for t in absent)),
        }
        controls = [t for t in trials if t["model"] == m and t["framing"] == "control"]
        by_model[m]["controls_refunded_once_correct"] = rate(
            sum(t["effects_committed"] == 1 and t["verdict"] == "correct" for t in controls),
            len(controls),
        )
    out["per_model"] = by_model
    dup = [t for t in trials if t["reissued"]]
    out["reissued_args_identical"] = rate(
        sum(c == "identical" for t in dup for c in t["arg_comparisons"]),
        sum(len(t["arg_comparisons"]) for t in dup),
    )
    out["arg_hash_would_dedupe"] = rate(
        sum(h for t in dup for h in t["arg_hash_would_dedupe"]),
        sum(len(t["arg_hash_would_dedupe"]) for t in dup),
    )
    sonnet = [t for t in trials if t["model"] == "claude-sonnet-5-5" and t["framing"] == "generic"]
    k_abs = sum(t["reissued"] > 0 for t in sonnet if t["status_tool"] == "absent")
    n_abs = sum(t["status_tool"] == "absent" for t in sonnet)
    k_pre = sum(t["reissued"] > 0 for t in sonnet if t["status_tool"] == "present")
    n_pre = sum(t["status_tool"] == "present" for t in sonnet)
    out["sonnet_generic_status_tool_test"] = {
        "absent": rate(k_abs, n_abs),
        "present": rate(k_pre, n_pre),
        "fisher_exact_two_sided_p": fisher_exact(k_abs, n_abs - k_abs, k_pre, n_pre - k_pre),
    }
    return out


def check_verdicts(export: dict, main: dict) -> dict:
    """Step 8: re-judge every main-run trial from the sink's rows, with the
    recorded coder and with the hand codes where a trial was read by hand."""
    refunds = _by_order(export["refunds"])
    hand = _load(HAND_CODES)["labels"]
    recorded: Counter[str] = Counter()
    rejudged: Counter[str] = Counter()
    by_hand: Counter[str] = Counter()
    differs: list[int] = []
    changed_by_hand: list[dict] = []
    for i, t in enumerate(main["trials"]):
        rs = refunds[t["order_id"]]
        finished = t["stop_reason"] == "end_turn"

        def judge(code: str, t: dict = t, rs: list = rs, finished: bool = finished) -> str:
            return classify(
                effects=len(rs),
                intended=1,
                status=subject_status(code) if finished else "failed",
                payload_matches=payload_matches(
                    rs, order_id=t["order_id"], amount_cents=AMOUNT_CENTS
                ),
                confirmed=t["saw_confirmation"],
            )

        coded = code_final_text(t["final_text"]) if t["final_text"] else "none"
        v = judge(coded)
        recorded[t["verdict"]] += 1
        rejudged[v] += 1
        if v != t["verdict"] or coded != t["told_user"]:
            differs.append(i)
        h = hand.get(str(i))
        hv = judge(h["code"]) if h else v
        by_hand[hv] += 1
        if hv != v:
            changed_by_hand.append(
                {
                    "index": i,
                    "cell": [t["model"], t["framing"], t["status_tool"]],
                    "from": v,
                    "to": hv,
                }
            )
    duplicates = [t for t in main["trials"] if len(refunds[t["order_id"]]) > 1]
    return {
        "recorded": dict(recorded),
        "rejudged_from_sink_rows": dict(rejudged),
        "trials_whose_rejudged_verdict_or_code_differs": differs,
        "with_hand_codes": dict(by_hand),
        "changed_by_hand_codes": changed_by_hand,
        "hand_labels": len(hand),
        # What the contract does not record: a duplicate whose caller was told
        # the refund was done (coded claims_success), so its report contradicts
        # the sink. The verdict stays `duplicate` either way.
        "duplicates_coded_claims_success": sum(
            t["told_user"] == "claims_success" for t in duplicates
        ),
        "faulted_trials_record_fault_confirmed": any(
            "fault_confirmed" in t for t in main["trials"] if t["framing"] != "control"
        ),
    }


def check_sonnet_hand_count(main: dict) -> dict:
    """The hand count of how each of the 34 messages used `replayed: false`."""
    hand = _load(SONNET_HAND_COUNT)
    duplicates = {t["sequence"] for t in main["trials"] if t["reissued"]}
    labelled = {int(k) for k in hand["labels"]}
    for k, v in hand["labels"].items():
        if v["quote"] not in main["trials"][int(k)]["final_text"]:
            raise ValueError(f"trial {k}: the quoted sentence is not in its final message")
    return {
        "covers_exactly_the_duplicates": labelled == duplicates,
        "names_the_flag": sum(v["names_the_flag"] for v in hand["labels"].values()),
        "tells_user": dict(Counter(v["tells_user"] for v in hand["labels"].values())),
        "adds_a_caveat": sum(v["adds_a_caveat"] for v in hand["labels"].values()),
        "infers_first_attempt_did_not_go_through": sum(
            v["infers_first_attempt_did_not_go_through"] for v in hand["labels"].values()
        ),
    }


def validate() -> dict:
    export = _load(EXPORT)
    runs = {p: _load(p) for p in export["results_files"]}
    ledger = [
        json.loads(line) for line in (REPO / LEDGER).read_text("utf-8").splitlines() if line.strip()
    ]
    main = runs[MAIN]
    pilots = {p: d for p, d in runs.items() if p != MAIN}
    return {
        "what": "Validation of experiment 006 (M5) against the sink export; no API calls.",
        "inputs": {"export": EXPORT, "results_files": list(runs), "ledger": LEDGER},
        "cohorts": check_cohorts(export, runs, ledger),
        "trials_against_sink": check_trials(export, runs),
        "duplicates": check_duplicates(export, main, pilots),
        "sonnet_hand_count": check_sonnet_hand_count(main),
        "status_tool": check_status_tool(export, main),
        "rates": check_rates(main, pilots),
        "verdicts": check_verdicts(export, main),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", help="write the result here (a new file; never overwrites)")
    args = parser.parse_args()
    result = validate()
    text = json.dumps(result, indent=2)
    if args.out:
        out = Path(args.out)
        if out.exists():
            raise SystemExit(f"{out} exists; results files are never overwritten")
        out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
