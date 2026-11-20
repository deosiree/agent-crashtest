"""The shared verdict logic, against the definitions in docs/contract.md section 3 (v0.2)."""

from __future__ import annotations

import pytest

from crashtest.checker import VERDICTS, classify, payload_matches, report_matches_sink

OK = {"payload_matches": True, "confirmed": True}


@pytest.mark.parametrize(
    ("effects", "status", "expected"),
    [
        # More effects than intended is a duplicate, whatever the subject reported.
        (2, "completed", "duplicate"),
        (2, "unknown", "duplicate"),
        (2, "failed", "duplicate"),
        (3, "completed", "duplicate"),
        # A subject that reported completion, with a matching and confirmed effect.
        (1, "completed", "correct"),
        (0, "completed", "lost"),
        # A subject that stopped and said it does not know has surfaced the ambiguity,
        # whether or not the effect happened.
        (1, "unknown", "in_doubt_surfaced"),
        (0, "unknown", "in_doubt_surfaced"),
        # A subject that failed or could not resume is stuck, even if the effect exists.
        (1, "failed", "stuck"),
        (0, "failed", "stuck"),
    ],
)
def test_verdict_for_each_combination(effects, status, expected):
    assert classify(effects=effects, intended=1, status=status, **OK) == expected


def test_correct_needs_the_count_the_payload_and_a_confirmed_report():
    def one(**flags):
        return classify(effects=1, intended=1, status="completed", **flags)

    assert one(payload_matches=True, confirmed=True) == "correct"
    assert one(payload_matches=False, confirmed=True) == "wrong_payload"
    assert one(payload_matches=True, confirmed=False) == "unconfirmed_success"


def test_precedence_between_the_new_verdicts():
    bad = {"payload_matches": False, "confirmed": False}
    # A duplicate stays a duplicate, whatever else is wrong.
    assert classify(effects=2, intended=1, status="completed", **bad) == "duplicate"
    # A missing effect is `lost` before its payload or confirmation is considered.
    assert classify(effects=0, intended=1, status="completed", **bad) == "lost"
    # A wrong payload is reported before a missing confirmation.
    assert classify(effects=1, intended=1, status="completed", **bad) == "wrong_payload"


def test_payload_and_confirmation_do_not_change_the_non_completed_verdicts():
    """in_doubt_surfaced and stuck stay distinct, and depend only on the status."""
    for payload in (True, False):
        for confirmed in (True, False):
            flags = {"payload_matches": payload, "confirmed": confirmed}
            assert classify(effects=1, intended=1, status="unknown", **flags) == "in_doubt_surfaced"
            assert classify(effects=1, intended=1, status="failed", **flags) == "stuck"


def test_lost_requires_the_subject_to_have_reported_completion():
    """The contract's `lost` is not simply "fewer effects than intended"."""
    assert classify(effects=0, intended=1, status="completed", **OK) == "lost"
    assert classify(effects=0, intended=1, status="unknown", **OK) != "lost"
    assert classify(effects=0, intended=1, status="failed", **OK) != "lost"


def test_intended_is_not_assumed_to_be_one():
    assert classify(effects=2, intended=2, status="completed", **OK) == "correct"
    assert classify(effects=1, intended=2, status="completed", **OK) == "lost"
    assert classify(effects=3, intended=2, status="completed", **OK) == "duplicate"
    assert classify(effects=0, intended=0, status="completed", **OK) == "correct"


def test_negative_counts_are_rejected():
    with pytest.raises(ValueError):
        classify(effects=-1, intended=1, status="completed", **OK)


def test_the_new_inputs_are_required():
    """No default may quietly assume a payload matched or a success was confirmed."""
    with pytest.raises(TypeError):
        classify(effects=1, intended=1, status="completed")  # type: ignore[call-arg]


def test_payload_matching_against_sink_rows():
    rows = [{"order_id": "o1", "amount_cents": 2500}]
    assert payload_matches(rows, order_id="o1", amount_cents=2500)
    assert not payload_matches(rows, order_id="o1", amount_cents=250)
    other_order = [{"order_id": "o2", "amount_cents": 2500}]
    assert not payload_matches(other_order, order_id="o1", amount_cents=2500)
    assert payload_matches([], order_id="o1", amount_cents=2500)
    # Rows read with GET /refunds?order_id= carry no order_id; the query fixed it.
    assert payload_matches([{"amount_cents": 2500}], order_id="o1", amount_cents=2500)


def test_every_contract_verdict_is_reachable():
    reached = {
        classify(effects=e, intended=1, status=s, payload_matches=p, confirmed=c)
        for e in (0, 1, 2)
        for s in ("completed", "unknown", "failed")
        for p in (True, False)
        for c in (True, False)
    }
    assert reached == set(VERDICTS)


# ---------------------------------------------------------------- contract v0.3, RA


@pytest.mark.parametrize(
    ("effects", "report", "matches"),
    [
        (1, "done", True),
        (2, "done", False),  # two refunds reported as one: the 006 duplicates
        (0, "done", False),
        (0, "not_done", True),
        (1, "not_done", False),  # said it failed, but it committed
        (2, "unknown", True),  # an unknown outcome contradicts nothing
        (1, "none", True),  # nor does no report at all
    ],
)
def test_report_matches_sink(effects, report, matches):
    assert report_matches_sink(effects=effects, intended=1, report=report) is matches


def test_report_matches_sink_never_changes_a_verdict():
    # A duplicate stays a duplicate whatever was reported; the field sits beside it.
    for report in ("done", "unknown", "not_done", "none"):
        assert (
            classify(
                effects=2, intended=1, status="completed", payload_matches=True, confirmed=True
            )
            == "duplicate"
        )
        assert report_matches_sink(effects=2, intended=1, report=report) is (
            report in ("unknown", "none")
        )


def test_report_matches_sink_rejects_negative_counts():
    with pytest.raises(ValueError):
        report_matches_sink(effects=-1, intended=1, report="done")
