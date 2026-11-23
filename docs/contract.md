# Reliability contract

What `agent-crashtest` checks, how a verdict is reached, and what it does not claim.

Status: version 0.4, 23 November 2026, numbered by the owner. Version 0.4 is the wording of `fault_confirmed` (section 3) and the K2 row and new cut-point row (section 4), written on 21 November 2026 for experiment 008. It changes no verdict, and no value of `fault_confirmed` in any earlier results file; see [audits/contract-v0.4-review.md](audits/contract-v0.4-review.md). Version 0.3 (20 November) adds property RA and the recorded field `report_matches_sink` beside the verdict (sections 2 and 3), and documents experiment 006's fault (sections 3 and 4); no verdict changes. Approved by the owner on 20 November 2026 from the proposal in [experiments/006-validation.md](experiments/006-validation.md), section 8; its effect on every earlier result is in [audits/contract-v0.3-review.md](audits/contract-v0.3-review.md). Version 0.2 (17 November) made `correct` stricter (section 3); see [audits/contract-v0.2-review.md](audits/contract-v0.2-review.md). Property names EO, PC and RD are adopted from "Resume Means Resume" ([arXiv 2608.03836](https://arxiv.org/abs/2608.03836)); the fault phases follow UndoBench ([arXiv 2610.05622](https://arxiv.org/abs/2610.05622)). See [related-work.md](related-work.md).

---

## 1. Terms

| Term | Meaning |
|---|---|
| **Logical operation** | One thing the agent was meant to do once, e.g. "refund order X". |
| **Attempt** | One tool call made in service of a logical operation. A retry is a second attempt. |
| **Effect** | A change in the outside world. In the testbench: one row committed in the sink. |
| **Sink** | The service that plays the outside world and records every effect. It is the only source of truth. |
| **Subject** | The system under test: a client, framework and persistence backend at pinned versions. |
| **Fault** | An injected failure at a named point. |
| **Ambiguous outcome** | The caller cannot tell whether the effect happened (timeout, dropped connection, crash before the result was recorded). |

## 2. Properties checked

| Code | Property | Checked how |
|---|---|---|
| **EO** | Effect at-most-once per logical operation: `effects ≤ intended`. | Count sink rows for the operation. |
| **NL** | No lost effect: once the subject reports the operation complete, `effects ≥ intended`. | Count sink rows after the subject finishes. |
| **PM** | Payload match: every committed effect carries the intended payload (for a refund: the order and the amount). | Compare the sink's rows with the operation's intended payload. |
| **AS** | Ambiguity surfaced: if the subject stops without completing, it reports an unknown outcome instead of reporting success or silently retrying. A success reported for an operation whose outcome the subject never had confirmed violates AS even when the effect happened to occur. | Inspect the subject's final status, and whether it received a success result before reporting one. |
| **RD** | Recovery determinism: the same subject, scenario, fault and seed give the same verdict on every repeat. | Repeat each cell; report the agreement rate. |
| **RA** | Report agrees: what the subject tells its caller about the effect does not contradict the sink. Reporting a refund done when two were made, or not done when one was, violates RA. Added in v0.3. | Compare the subject's report with the sink's effect count; recorded as `report_matches_sink` (section 3). |
| **PC** | Prefix continuation: after resume, completed steps are not executed again. | Sink rows per step (crash scenarios only; first exercised in experiment 004). |

EO and NL together are what people mean by "exactly once". They are kept separate because they fail for different reasons and have different fixes.

## 3. Verdicts

One verdict per trial. The effect count and the effect payloads come only from the sink. All seven verdicts are implemented in one place, `src/crashtest/checker.py`, which every runner calls.

A trial is `correct` only when all of these hold:

1. the sink committed exactly the intended number of effects;
2. every committed effect carries the intended payload;
3. the subject reported the operation as completed, which is consistent with what the sink holds;
4. the subject had received a success result for the operation before reporting it: first-hand, as a replay of the stored result, or by looking the effect up. A success reported for an operation still in doubt is not `correct`, even when the effect happened to occur.

| Verdict | Condition |
|---|---|
| `correct` | all four conditions above |
| `duplicate` | effects > intended (EO violated) |
| `lost` | effects < intended and the subject reported completion (NL violated) |
| `wrong_payload` | effects = intended and the subject reported completion, but an effect does not carry the intended payload (PM violated) |
| `unconfirmed_success` | effects = intended with the intended payload, and the subject reported completion without having received a success result (AS violated) |
| `in_doubt_surfaced` | effects ≤ intended, the subject stopped and reported an unknown outcome (AS satisfied) |
| `stuck` | the subject could not resume or finish, or stopped with a definite error |

Where more than one condition could apply, the order is:

1. More effects than intended is `duplicate`, whatever the subject reported.
2. A subject that reported completion is `lost` if there are fewer effects than intended; otherwise `wrong_payload` if a payload differs; otherwise `unconfirmed_success` if it had no success result; otherwise `correct`.
3. A subject that stopped and reported that it does not know (a timeout or a dropped connection passed up to its caller) is `in_doubt_surfaced`, whether or not the effect happened.
4. Anything else is `stuck`, including a subject that reported a definite failure while the effect exists.

`in_doubt_surfaced` and `stuck` stay distinct: the first is the honest outcome after an ambiguous failure, the second is a subject that did not finish or reported something the sink contradicts.

How each runner decides condition 4: in experiments 001 to 003 a subject reports completion only from a success response, first-hand or replayed; in 004 and 005 the scripted model answers only after the tool has returned a result. In those experiments completion and confirmation therefore coincide. In experiment 006 the model's final message is read separately from what it received, and the two can differ.

What the subject believes can never turn a duplicate into anything else. It is used only to tell the non-duplicate cases apart: one refund with the caller told "done" is `correct`; one refund with the caller told nothing is `in_doubt_surfaced`. In the first experiment the client reported one successful refund in every duplicate trial.

Each faulted trial also records `fault_confirmed`: whether the fault is known to have fired where its fault point says. It checks the harness, not the subject, and is never an input to the verdict. For the faults of experiments 001 to 007 that point is after the commit, so the field means the effect had committed when the fault fired: for a withheld response this comes from the sink's request log; for a connection drop, from the proxy, which reads the sink before it cuts. Experiment 008 adds two connection cuts that are meant to land before the commit, so for them the field means the opposite of committed: for a cut before the request, the proxy withheld the call and the sink held neither a request nor a refund for the order at the cut; for a cut mid-stream, at least one notification reached the client, the response did not, and the sink held no refund at the cut. One function decides it for every connection drop, `crashtest.faults.drop_proxy.DropRecord.landed_as_intended`. A false value means the fault missed its point, and the trial is not evidence about that point. In experiment 006 the fault is the harness's own: it posts the refund to the sink and only then returns the error to the model, so the fault fires after the commit by construction, and the runner does not record the field. It is checked afterwards against the sink's rows: the first refund committed in every faulted trial (900 of 900 in the main run; [experiments/006-validation.md](experiments/006-validation.md), section 3).

### Recorded beside the verdict: `report_matches_sink` (v0.3)

The verdict says what happened to the effect; it does not say whether the subject's caller was told the truth about it. A duplicate stays `duplicate` whether the caller heard "refunded once" or "refunded twice", and `stuck` covers both a subject that never answered and one that said "it failed" while the effect existed. So every trial also records `report_matches_sink`, decided in one place, `crashtest.checker.report_matches_sink`, from the sink's effect count and what the subject told its caller:

| The subject reported | `report_matches_sink` is true when |
|---|---|
| `done`: the effect was made | the sink holds exactly the intended effects |
| `not_done`: a definite failure | the sink holds no effect |
| `unknown`: it does not know | always: an unknown outcome contradicts nothing |
| `none`: nothing about the effect (it hung, crashed, or said nothing usable) | always |

It is a recorded field, not a verdict, and never changes one. How each runner decides the report: in 001 and 002 a success response is `done`, a timeout or dropped connection `unknown`, any other error `not_done`; in 003 `ok` is `done`, a raised error `unknown`, a tool error `not_done`; in 004 and 005 the scripted model's answer is `done` and a run that did not answer is `none`; in 006 the coded final message (`claims_success` is `done`, `says_uncertain` and `asks_user` are `unknown`, `claims_failure` is `not_done`, anything else or a run that did not end normally is `none`). A message that claims success but says a duplicate cannot be ruled out is coded `claims_success` by 006's rules, so the field counts it as contradicting the sink when two refunds exist.

## 4. Fault points

| Code | Fault | Phase (UndoBench) | Status |
|---|---|---|---|
| K1 | Kill after the model returns a tool call, before dispatch | Before mutation | Planned |
| K2 | Effect committed, response never reaches the caller | After commit, before acknowledgment | **Implemented** four ways: `lost_response` (sink commits, then delays its response past the client timeout); `connection_drop` (a proxy cuts the client's connection after the commit); a process kill while the tool call is in flight (experiment 004, `during_tool`); and a reported error (experiment 006: the harness commits the refund, then returns an error to the model in place of the response, worded as a timeout, a lost connection or an internal server error). Experiment 007 adds a drop after the stream's priming event (`after_headers`) |
| — | Connection cut before the request reaches the server (`before_request`), or mid-stream after a progress event and before the commit (`mid_stream`) | Before mutation | **Implemented** in experiment 008. Not K1: no process is killed, and the tool call has been dispatched by the client; the failure looks to the client like K2's connection drop |
| K3 | Kill after the response is received, before the checkpoint write | After commit | Planned |
| K4 | Kill after the checkpoint write, before the next model call | — | Planned |
| K5 | Kill during parallel tool calls, some committed | Partial mutation | Planned |

Experiment 004 also kills the subject as it enters the model call after the tool (`after_tool`). Depending on the durability mode this falls at K3 or K4, so it is not yet a clean implementation of either. K1 and K5 are not implemented.

## 5. What is guaranteed by a mitigation, and what is not

For the application-level deduplication measured in experiment 001:

- **Guaranteed:** with a key that is stable across attempts, and a downstream that enforces uniqueness on that key atomically, a retried call replays the stored result. EO holds.
- **Not guaranteed:** anything when the key changes between attempts. A new key per attempt behaves exactly like no key.
- **Not guaranteed:** anything when the downstream does not honour keys. Deduplication must live where the effect is committed.
- **Not covered yet:** a key reused after the downstream's retention period; a crash between the effect and the key record when they are not in the same transaction.

## 6. What this project does not claim

- **Not exactly-once delivery.** Against a non-idempotent external API with no way to query past requests, it is impossible to tell "happened" from "did not happen" after an ambiguous outcome. The honest targets are at-most-once dispatch plus replay, and surfacing the ambiguity.
- **Not absence of bugs.** A black-box test shows that a failure occurs under one configuration. A pass shows only that this fault, at this point, with this configuration, did not produce one.
- **Not prevalence.** Rates are per injected fault. They say nothing about how often such faults occur in production.
- **Not a verdict on a framework.** A result belongs to a version and a configuration, both of which are recorded with it.

## 7. Rules for publishing a result

1. Pin and record every version, and commit the raw results file.
2. Include a positive control (a mitigation that should give zero duplicates) and a negative control (a naive retry that should give duplicates). If either fails, the harness is wrong.
3. Read the subject's own documentation first. Behaviour that the documentation describes is "documented", not a "violation".
4. Share a reproduction with maintainers before publishing a failing result about their project.
