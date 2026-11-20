"""The 006 validation: exact statistics, and the checks on the committed data.

No API calls and no database: everything reads committed files under results/.
"""

from __future__ import annotations

import pytest

from crashtest.experiment import model_retry_validate as v


@pytest.mark.parametrize(
    ("k", "n", "lower", "upper"),
    [
        (0, 50, 0.0, 1 - 0.025 ** (1 / 50)),  # closed form when k = 0
        (10, 10, 0.025 ** (1 / 10), 1.0),  # and when k = n
        (34, 50, 0.5330, 0.8048),
        (2, 3, 0.0943, 0.9916),
    ],
)
def test_clopper_pearson(k, n, lower, upper):
    lo, hi = v.clopper_pearson(k, n)
    assert lo == pytest.approx(lower, abs=1e-4)
    assert hi == pytest.approx(upper, abs=1e-4)


def test_clopper_pearson_rejects_impossible_counts():
    with pytest.raises(ValueError):
        v.clopper_pearson(3, 2)


def test_fisher_exact_matches_known_values():
    # Fisher's tea-tasting table: two-sided p = 34/70.
    assert v.fisher_exact(3, 1, 1, 3) == pytest.approx(34 / 70)
    # A table with no association.
    assert v.fisher_exact(5, 5, 5, 5) == pytest.approx(1.0)
    # Symmetric under swapping the rows.
    assert v.fisher_exact(34, 16, 0, 50) == pytest.approx(v.fisher_exact(0, 50, 34, 16))


@pytest.fixture(scope="module")
def result():
    return v.validate()


def test_every_run_is_the_seeded_plan_and_matches_the_sink(result):
    cohorts = result["cohorts"]
    assert cohorts["order_ids_unique_across_runs"]
    assert cohorts["export_orders_equal_run_orders"]
    assert cohorts["ledger_orders_equal_run_orders"]
    assert cohorts["ledger_request_ids_equal_trial_request_ids"]
    for run in cohorts["runs"].values():
        assert run["matches_seeded_plan_in_order"]
        assert run["errors"] == 0
    for run in result["trials_against_sink"]["runs"].values():
        assert run["mismatches"] == []
        assert run["refund_ids_contiguous"] and run["request_log_ids_contiguous"]
        assert run["trials_refunded_more_than_twice"] == 0


def test_the_main_run_counts(result):
    main = result["trials_against_sink"]["runs"][v.MAIN]
    assert (main["trials"], main["refund_rows"], main["trials_refunded_twice"]) == (930, 964, 34)
    assert result["duplicates"]["all_in_one_cell"]
    assert result["sonnet_hand_count"]["covers_exactly_the_duplicates"]


def test_verdicts_rejudged_from_the_sink_equal_the_recorded_ones(result):
    verdicts = result["verdicts"]
    assert verdicts["rejudged_from_sink_rows"] == verdicts["recorded"]
    assert verdicts["trials_whose_rejudged_verdict_or_code_differs"] == []
    assert verdicts["with_hand_codes"] == {
        "correct": 480,
        "in_doubt_surfaced": 410,
        "duplicate": 34,
        "stuck": 6,
    }


def test_report_of_keeps_a_failure_claim_apart_from_no_report():
    from crashtest.experiment.model_retry import report_of

    assert report_of("claims_success") == "done"
    assert report_of("says_uncertain") == report_of("asks_user") == "unknown"
    assert report_of("claims_failure") == "not_done"
    assert report_of("other") == report_of("none") == "none"


def test_report_check_on_the_recorded_results():
    """Contract v0.3, RA, derived for files written before it: every duplicate in
    001 to 005 was reported as done; in the 006 main run, 34 duplicates told one
    refund and, by hand, 6 told a failure while the refund had committed."""
    from pathlib import Path

    from crashtest.results import report_check as rc

    root = Path(rc.REPO)
    reports = [r for f in sorted(root.glob("results/*/*.json")) if (r := rc.review_file(f))]
    for r in reports:
        if r["experiment"] != "model_retry":
            # Only a duplicate (or an early 003 trial recorded before verdicts
            # existed) contradicts the sink, and no duplicate matches it.
            contradicting = {
                k.split(" / ")[0] for k in r["by_verdict"] if k.endswith("contradicts")
            }
            assert contradicting <= {"duplicate", "None"}
            assert "duplicate / matches" not in r["by_verdict"]
    main = next(r for r in reports if r["file"] == rc.MAIN_006)
    assert main["false_by_report_and_effects"] == {
        "reported done, sink 2": 34,
        "reported not_done, sink 1": 7,
    }
    assert main["with_hand_codes"]["false_by_report_and_effects"] == {
        "reported done, sink 2": 34,
        "reported not_done, sink 1": 6,
    }
