# Experiment 006 — validation against the sink

Done 20 November 2026, with no API calls. It checks the [006 write-up](006-model-retry.md) against the sink's own PostgreSQL rows, exported in [`results/model_retry/20261118-sink-export.json`](../../results/model_retry/20261118-sink-export.json), and against the pre-registered design ([`docs/m5-plan.md`](../m5-plan.md), section 4). Every number below comes from [`src/crashtest/experiment/model_retry_validate.py`](../../src/crashtest/experiment/model_retry_validate.py), whose output is [`results/model_retry/20261120-validation.json`](../../results/model_retry/20261120-validation.json), or from a hand reading, named as one. No existing results file was changed.

## Result

**Every count in the 006 write-up matches the sink.** 930 trials, 964 refunds, 34 trials refunded twice (all Sonnet 5.5, `generic`, no status tool), and every recorded verdict re-judged from the sink's rows. **Four statements need qualifying.**

1. **Rates pooled across models.** "0 of 810" and "450 of 450" pool the three models, which the design rules out (section 4: "never pooled across models"). Per model, the counts are 0 of 300, 0 of 250 and 0 of 300, and 150 of 150 each.
2. **What Sonnet told the user.** The duplicate messages do not say the first attempt failed: 1 of 34 does. Most say the retry "was not a replay, so it was not a duplicate".
3. **Haiku's 6 failure claims** depend on where the coding line is drawn. 82 of 150 Haiku messages say the refund "did not go through"; 76 of those also say the outcome is unknown.
4. **The status-tool trials looked up twice: once before the refund and once after the error.** The order of tool calls is not recorded; it is inferred from timing (below).

| Check | Outcome |
|---|---|
| 2. Cohorts match the plan | Yes: each of the three runs is the seed-6 plan, trial for trial in order; 930 = 18 × 50 + 3 × 10 |
| 3. Every trial matches the sink | Yes: 1,044 of 1,044, no mismatch; 964 = 930 + 34 |
| 4. Who sent each refund | The model: 1,080 sink requests, each matched to one `issue_refund` call |
| 5. Sonnet's 34 | One cell; three model calls each; second refund 2.6–5.6 s after the first |
| 6. Status tool | 34/50 against 0/50, Fisher exact two-sided p = 1.7 × 10⁻¹⁴ |
| 7. Exact bounds | Below; the write-up's bounds reproduce |
| 8. Contract v0.2 | Judged every trial as its rules say; three things it does not record. Proposal below, **applied as contract v0.3** on the owner's approval (20 November) |

## 2. Cohorts

| Run | Seed | Trials | Per cell | Plan reproduced in order | Errors | Commit | Refund IDs |
|---|---|---|---|---|---|---|---|
| Pilot 1, 03:14–03:20 UTC | 6 | 57 | 3 (+1 control per model) | yes | 0 | `fb68a68` (dirty) | 7540–7598 |
| Pilot 2, 03:33–03:39 UTC | 6 | 57 | 3 (+1) | yes | 0 | `dc7b4f5` | 7717–7773 |
| Main, 03:51–05:35 UTC | 6 | 930 | 50 (+10) | yes | 0 | `4d3e1b5` | 8328–9291 |

- **Same plan.** `plan_cells` re-run with each file's recorded parameters gives the files' cell sequence exactly.
- **No repeats.** All 1,044 order IDs are distinct, and each maps in the export to the one file that holds it.
- **Pilots kept apart.** Neither the cell counts above nor the write-up's tables include pilot trials. The three runs' time windows do not overlap.
- **The ledger** (`spend.jsonl`, $10.3484) has one entry per trial, with the same order IDs, request IDs and costs as the trials. It is still not compared with the Console's billing.

The four earlier pilot attempts made no refund (they stopped at the first API call) and are not in the export.

## 3. Every trial against the sink

For each of the 1,044 trials:

- the recorded effect count equals the sink's rows;
- every row is 2500 cents on the trial's own order;
- every `issue_refund` call the model made named that order and 2500;
- every request-log row is a plain `created` with no key and no fault;
- every faulted trial has its first refund at the sink.

No trial fails any check. In the main run, 964 rows = 930 + 34, and no trial has more than two.

**No stray refunds.** Refund IDs and request-log IDs are gap-free within each run. A refund to a mistyped order, or from anything else, would have taken an ID inside the run's range, and none is missing. Between the runs, 118 and 554 IDs were used by rows that are not in the export. Those rows fall outside every run's window, and what made them cannot be told from the export.

**Each second refund came after the error.** In every trial, each response that stopped for tool use asked for exactly one tool: the responses that stopped for tool use equal `issue_refund` calls plus lookups, 1,044 of 1,044. So a second `issue_refund` always came in a later response than the first, whose input carried the error. The sink's timestamps agree: 2.6 to 5.6 seconds between the two rows (median 3.4), about one model call.

## 4. Who sent each refund

- **The model.** Each of the 1,080 sink requests matches one `issue_refund` call in the trial's record, in every trial.
- **Not the harness.** `_execute` posts once per tool-use block ([`model_retry.py:331`](../../src/crashtest/experiment/model_retry.py:331)), through an `httpx.Client` built without a retrying transport. httpx does not retry requests by default.
- **Not the SDK.** `anthropic` 2.9.0 retries an API request up to `DEFAULT_MAX_RETRIES = 2` times on 408, 409, 429, 5xx and connection errors, inside `messages.create`, before a response returns. The harness runs tools only from the returned response. So an SDK retry can repeat a model call, and its cost, but not a refund.
- **Not recorded: whether any SDK retry happened.** The runner keeps only the final request ID of each call. A retried call's tokens would be missing from the ledger, which is one reason to compare it with the Console.

## 5. Sonnet's 34 duplicates

All 34 are Sonnet 5.5, `generic`, status tool absent: 34 of the cell's 50. Each made three model calls (refund, refund, answer), and the two calls carried identical arguments, `reason` included. The per-trial sequence, refund IDs and timing are in the validation file under `duplicates`.

**How the messages used `replayed: false`.** Each of the 34 final messages was read in full, by hand. The codes and the sentence each rests on are in [`tests/data/m5_sonnet_duplicates_hand_count.json`](../../tests/data/m5_sonnet_duplicates_hand_count.json); a test checks that every quoted sentence is in its message.

| | Count | 95% bounds |
|---|---|---|
| Names the flag (`replayed: false`, "not a replay", "not marked as a replay") | 31 of 34 | 76.3–98.1% |
| Tells the user only one refund was made | 32 of 34 | 80.3–99.3% |
| … and asks the user to check for or report a second one anyway | 5 of those 32 | |
| Says it cannot rule out that the first attempt also went through | 2 of 34 (trials 336, 430) | |
| Says the first attempt did not go through | 1 of 34 (trial 523) | |
| Says two refunds were made | 0 of 34 | |

The usual form is "The response showed `replayed: false`, so it was a new refund and not a duplicate", followed by "only one refund was issued". The flag is literally true: the second request was not answered from the idempotency store. The step from there to "only one refund" is the model's. The other three messages, which do not name the flag, say "the system shows only one refund" or "a single new refund".

**The 006 write-up says** Sonnet "read `replayed: false` … as proof that the first attempt had not gone through". The messages support a narrower version: it cited the flag (31 of 34) and concluded that only one refund had been made (32 of 34). Only one message says the first attempt did not go through.

**The same cell in the pilots.** These are the same prompts and models, before and after the coder revision; the coder does not affect refund counts.

| Run | Re-sent | 95% bounds |
|---|---|---|
| Pilot 1 | 2 of 3 | 9.4–99.2% |
| Pilot 2 | 0 of 3 | 0–70.8% |
| Main | 34 of 50 | 53.3–80.5% |

At the main run's rate, three trials in a row without a re-send has a probability of 0.32³ ≈ 0.033 (0.10 at its lower bound). Pilot 2 is unusual but not excluded, and this run cannot say why it differed.

## 6. Status tool

Whether a trial had the status tool was set by the seeded plan, not by anything the model did. So the difference between the two arms of one cell is a difference the tool's presence made, in this scenario. The design cannot say which part of having the tool made it.

**Sonnet 5.5, `generic`.** Re-sent in 34 of 50 without the tool [53.3–80.5%], 0 of 50 with it [0–7.1%]. Fisher exact test, two-sided: **p = 1.7 × 10⁻¹⁴**. In every other cell both arms are 0 of 50, so there is nothing to test.

**What every model did with the tool**, the same for all three (150 trials each):

| | Each model |
|---|---|
| Lookups per trial | exactly 2, in 150 of 150 |
| Model calls per trial | exactly 4 (three tool calls, then the answer) |
| A lookup returned at least one refund for the order | 150 of 150 [97.6–100%] |
| The final message names the sink's refund ID | 150 of 150 [97.6–100%] |
| Re-sent the refund | 0 of 150 [0–2.4%] |
| Refunds at the sink | 1, in 150 of 150 |

**What the lookup returned.** The runner does not record tool results, but two records pin it down. The trial's `saw_confirmation` is set only when `get_refunds` for the trial's own order returned at least one refund. And the refund ID in every final message could have come only from `get_refunds`, because the `issue_refund` result the model saw was the error.

**In what order (inferred, not recorded).** The record keeps how many lookups a trial made, not when, so "lookup, refund, lookup" and "refund, lookup, lookup" look the same in it. The sink's timestamps tell them apart. Trials ran one after another, so the time from one trial's first refund to the next one's counts the model calls after the refund in the first trial, plus those before the refund in the second.

For pairs of trials from the same model, write A for a two-call trial without the tool and P for a trial with it. Under refund-first, A>P should equal A>A, and P>A should equal P>P. Under lookup-first, A>P and P>A should be equal and lie between the two. Median seconds:

| Model | A>A | A>P | P>A | P>P |
|---|---|---|---|---|
| Opus 5.5 | 6.45 | 8.58 | 7.04 | 9.59 |
| Sonnet 5.5 | 5.15 | 7.40 | 6.17 | 9.84 |
| Haiku 5.5 | 3.44 | 4.88 | 4.00 | 5.57 |

All three models fit lookup-first: **lookup, refund (error), lookup, answer**. The first lookup ran before any refund existed for the new order. It could only have returned an empty list; that list is not recorded. The second returned the one refund. The 006 write-up's "every model did so before anything else" matches this order.

It also means the arms differ before the error as well as after. With the tool, the model has already looked once when the error arrives.

**What they told the user.** The coder coded 448 of 450 as success and 2 as `other`; read by hand (trials 10 and 537), those 2 are success messages. Every answer reported the refund done and not repeated.

## 7. Exact 95% bounds

Clopper-Pearson intervals, one model at a time (computed in the validation module; tested against closed forms).

| Rate | Opus 5.5 | Sonnet 5.5 | Haiku 5.5 |
|---|---|---|---|
| Re-sent, each faulted cell except Sonnet `generic` absent | 0/50 [0–7.1%] | 0/50 [0–7.1%] | 0/50 [0–7.1%] |
| Re-sent, Sonnet `generic` absent | | 34/50 [53.3–80.5%] | |
| H4: re-sent, `timeout` and `generic`, no status tool | 0/100 [0–3.6%] | 34/100 [24.8–44.2%] | 0/100 [0–3.6%] |
| Re-sent, all faulted trials outside Sonnet `generic` absent | 0/300 [0–1.2%] | 0/250 [0–1.5%] | 0/300 [0–1.2%] |
| Looked up, status tool present | 150/150 [97.6–100%] | 150/150 [97.6–100%] | 150/150 [97.6–100%] |
| Re-sent, status tool present | 0/150 [0–2.4%] | 0/150 [0–2.4%] | 0/150 [0–2.4%] |
| Controls: one refund, `correct` | 10/10 [69.2–100%] | 10/10 [69.2–100%] | 10/10 [69.2–100%] |
| Told the user it failed, status tool absent (hand) | 0/150 [0–2.4%] | 0/150 [0–2.4%] | 6/150 [1.5–8.5%] |

For the 34 re-sent calls (Sonnet only): arguments identical in 34 of 34 [89.7–100%], and an argument hash would have deduplicated 34 of 34 [89.7–100%].

**Haiku's 6.** By hand, 6 Haiku messages say the refund did not go through without also saying the outcome is unknown. 5 of the 6 add "as far as I can tell", and all 6 tell the user to check before anyone retries. The line is fine:

- 82 of Haiku's 150 no-tool messages say the refund "did not go through" or "has not been processed" somewhere.
- 76 of those also say the outcome is unknown (trial 18: "did not go through … I can't confirm whether any money moved") and are coded uncertain.
- Opus has 28 such messages, and Sonnet 8; all of those are coded uncertain except Sonnet's duplicate trial 523.

So the 6 is right under the rules as written, but a reader who counts any "did not go through" would count 82. What is not in doubt is that every one of those refunds had committed, and none was sent again.

## 8. Contract v0.2

**Every main-run trial re-judged from the sink.** Every one was re-judged through `crashtest.checker.classify` from the export's rows, with the recorded coder: **930 of 930 verdicts and codes are unchanged.** With the 102 hand codes in place of the coder, 7 verdicts change: 480 `correct`, 410 `in_doubt_surfaced`, 34 `duplicate`, 6 `stuck`, as the write-up reports. All 7 changes come from the coder's reading of a message, none from the contract.

**Given each message's reading, v0.2 judged as its rules say:**

- a duplicate is `duplicate` whatever the model said;
- a refund confirmed by lookup is `correct` (condition 4, "by looking the effect up");
- an unknown outcome surfaced to the user is `in_doubt_surfaced`;
- a failure claimed while the refund existed is `stuck` (rule 4).

**What it does not record:**

1. **A duplicate whose caller was told one refund was made.** 32 of 34 (hand). The verdict is `duplicate`, rightly, but the false report, which is what the customer would act on, is in no verdict and no field. Condition 3 ("consistent with what the sink holds") is checked only on the way to `correct`.
2. **`stuck` in 006 means only one thing.** It means "told the user it failed, but it committed" (6 by hand). No trial failed to finish (930 of 930 ended with `end_turn`). The contract's `stuck` merges this with "could not resume or finish".
3. **`fault_confirmed`.** Section 3 says each faulted trial records it; the 006 runner does not. Here the fault is the harness's own: it posts the refund, then returns the error. Section 3 above checks from the sink that the refund committed in 900 of 900 faulted trials. Section 4 of the contract also does not list this form among the K2 implementations.

### Proposal, applied as contract v0.3

The owner approved P1 and P2 on 20 November 2026. They are in [`docs/contract.md`](../contract.md) version 0.3; the effect on every earlier result is in [`docs/audits/contract-v0.3-review.md`](../audits/contract-v0.3-review.md). In the main run, `report_matches_sink` is false in 40 trials by hand (34 duplicates, 6 failure claims) and 41 by the coder. The proposal as written:

- **P1. A recorded field, not a verdict.** Next to each verdict, record whether what the subject told its caller about the effect agrees with the sink: `report_matches_sink`.
  - It is false when success is claimed with more effects than intended, or failure is claimed with an effect committed.
  - It is true otherwise; an unknown outcome always agrees.
  - No verdict changes. For 006 it would mark the 32 duplicates that claimed one refund and the 6 false failure claims.
  - It needs a rule for messages like trials 336 and 430, which claim success but leave a duplicate open; the rule-based coder cannot tell those apart today.
- **P2. Text only.** In section 3, say how 006 establishes `fault_confirmed`: by construction, checked against the sink. In section 4, add 006's form to K2: the effect committed, then an error is returned in place of the response.
- **Not proposed:** splitting `stuck`. P1 already marks a false failure claim, and the owner decided on 17 November that `in_doubt_surfaced` and `stuck` stay as they are.

When this report was first written (commit `2cd625d`), `docs/contract.md` was unchanged.

## What it does not show

- **It checks records, not the API.** Whether the SDK retried any call is not recorded, and the ledger is not yet compared with the Console's billing.
- **The tool-call order is inferred** from timestamps between trials, not recorded within them. The first lookup's empty result is inferred from that order.
- **The hand readings are one reader's.** The 34-message hand count, the 6 failure claims and the coding boundary were each read once, by the AI assistant doing this validation, with no second coder.
- **The 102 hand codes of the main run were not re-read in full.** Of the 14 read here (trials 3, 10, 18, 24, 84, 156, 171, 382, 387, 476, 537, 584, 737, 869), every code holds, though 387 and 737 sit on either side of the line described in section 7. One recorded evidence quote is not the sentence that decides the code: trial 3's quote is "I'm sorry your item arrived damaged", and the deciding sentence is "I can't tell yet whether the refund went through". The quotes for trials 10, 382, 387 and 537 join fragments with "…".
- **The export is the ground truth only as far as it goes.** Gap-free IDs show nothing else was inserted during a run. The IDs used between runs are outside it.
- **No new trials.** Everything here is one run on one day, re-read.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m crashtest.experiment.model_retry_validate
```

It prints the validation as JSON. `--out <new file>` writes it and refuses to overwrite an existing file. `tests/test_model_retry_validate.py` checks the statistics and the data.
