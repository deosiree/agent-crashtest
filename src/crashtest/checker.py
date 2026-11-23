"""The one place a verdict is decided. Every runner calls `classify`.

The verdicts and their meanings are defined in docs/contract.md, section 3
(contract version 0.4). The effect count and the effect payloads come from the
sink. The subject's own account of what happened is used only to tell the
non-duplicate cases apart; it can never turn a duplicate into anything else.

Beside the verdict, `report_matches_sink` records whether what the subject told
its caller about the effect agrees with the sink (contract v0.3, property RA).
It is a recorded field, not a verdict, and never changes one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

Verdict = Literal[
    "correct",
    "duplicate",
    "lost",
    "wrong_payload",
    "unconfirmed_success",
    "in_doubt_surfaced",
    "stuck",
]

# How the subject's run ended, as the subject itself reported it.
#   completed  it reported the operation as done
#   unknown    it stopped and reported that it does not know whether the effect
#              happened (a timeout or a dropped connection, surfaced to the caller)
#   failed     it stopped with a definite error, hung, or could not resume
SubjectStatus = Literal["completed", "unknown", "failed"]

# What the subject told its caller about the effect (contract v0.3, section 3).
#   done      the effect was made, once
#   unknown   it does not know whether the effect was made
#   not_done  the effect was not made (a definite failure)
#   none      it told its caller nothing about the effect (it hung, crashed,
#             or said something that is none of the above)
Report = Literal["done", "unknown", "not_done", "none"]

# For runners whose subject reports through its status alone (001 to 003): a
# success, an ambiguous error, or a definite one reached the caller.
REPORT_OF_STATUS: dict[SubjectStatus, Report] = {
    "completed": "done",
    "unknown": "unknown",
    "failed": "not_done",
}

VERDICTS: tuple[Verdict, ...] = (
    "correct",
    "duplicate",
    "lost",
    "wrong_payload",
    "unconfirmed_success",
    "in_doubt_surfaced",
    "stuck",
)


def classify(
    *,
    effects: int,
    intended: int,
    status: SubjectStatus,
    payload_matches: bool,
    confirmed: bool,
) -> Verdict:
    """Return the verdict for one trial.

    effects          refunds the sink committed for the operation
    intended         refunds the operation should have produced
    status           how the subject reported its run ended
    payload_matches  every committed refund carries the intended payload, checked
                     against the sink's own rows (see `payload_matches` below)
    confirmed        when the subject reported completion, it had received a
                     success result for the operation: directly, as a replay of
                     the stored result, or by looking the effect up. A success
                     reported without one is a guess, even when it happens to be right.

    Order of precedence:
      1. More effects than intended is `duplicate`, whatever the subject said.
      2. A subject that reported completion is, in this order: `lost` if there
         are fewer effects than intended; `wrong_payload` if an effect does not
         carry the intended payload; `unconfirmed_success` if it never received a
         success result; otherwise `correct`.
      3. A subject that stopped and said it does not know is `in_doubt_surfaced`.
      4. Anything else did not finish, or reported a definite failure: `stuck`.
    """
    if effects < 0 or intended < 0:
        raise ValueError("effect counts cannot be negative")
    if effects > intended:
        return "duplicate"
    if status == "completed":
        if effects < intended:
            return "lost"
        if not payload_matches:
            return "wrong_payload"
        if not confirmed:
            return "unconfirmed_success"
        return "correct"
    if status == "unknown":
        return "in_doubt_surfaced"
    return "stuck"


def payload_matches(
    refunds: Iterable[Mapping[str, Any]], *, order_id: str, amount_cents: int
) -> bool:
    """Whether every refund the sink committed carries the intended order and amount.

    `refunds` are the sink's rows for the operation (`GET /refunds`). With no rows
    there is nothing to mismatch; the count rules decide those cases.
    """
    return all(
        r.get("amount_cents") == amount_cents and r.get("order_id", order_id) == order_id
        for r in refunds
    )


def report_matches_sink(*, effects: int, intended: int, report: Report) -> bool:
    """Whether what the subject told its caller about the effect agrees with the sink.

    A recorded field beside the verdict (contract v0.3, property RA), not a
    verdict: it never changes one. False when the subject said the effect was
    done and the sink holds another number of effects (two refunds reported as
    one), or said it was not done and the sink holds one. An unknown outcome,
    or no report at all, contradicts nothing.
    """
    if effects < 0 or intended < 0:
        raise ValueError("effect counts cannot be negative")
    if report == "done":
        return effects == intended
    if report == "not_done":
        return effects == 0
    return True
