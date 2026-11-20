# Contract v0.3: `report_matches_sink`, and what it changes

Written 20 November 2026, when the change was made. The owner approved it on 20 November ("Apply P1 and P2"), from the proposal in [006-validation.md](../experiments/006-validation.md), section 8. The per-file output is in [`contract-v0.3-report-check.json`](contract-v0.3-report-check.json).

## The change

- **P1.** A new property, RA ("report agrees"): what the subject tells its caller about the effect does not contradict the sink. It is recorded beside every verdict as `report_matches_sink`, decided in one place (`crashtest.checker.report_matches_sink`). It is **not a verdict, and no verdict changes**. The rules are in the contract, section 3.
- **P2.** Text only. Section 3 now says how experiment 006 establishes that its fault fired after the commit. Section 4 lists 006's form of K2: the harness commits the refund, then returns an error in place of the response.

Why: in 006, 32 of Sonnet's 34 duplicates told the user that one refund was made, and 6 Haiku trials said the refund failed while it had committed. The verdicts were right (`duplicate`, `stuck`), but neither verdict records what the user was told. `stuck` also merges "never answered" with "said it failed".

## How each runner supplies the report

| Experiment | `done` | `unknown` | `not_done` | `none` |
|---|---|---|---|---|
| 001, 002 (`trial.py`) | a success response, first-hand or replayed | the last attempt timed out or lost its connection | any other error | never |
| 003 (`single_call.py`) | the subject reported `ok` | it raised | it returned a tool error | never |
| 004, 005 (`langgraph_crash.py`, `dbos_crash.py`) | the scripted model answered | never | never | the run did not answer |
| 006 (`model_retry.py`) | coded `claims_success` | `says_uncertain`, `asks_user` | `claims_failure` | anything else, or a run that did not end normally |

From now on, every runner records the field in each trial. Files written before cannot be edited. For them, `python -m crashtest.results.report_check` derives the field from the fields each runner recorded, with the same rules, taking each trial's effect count as recorded. For 006, that count was checked against the sink export in the validation.

## Effect on every recorded result

All results files were read and nothing in `results/` was edited.

| Experiment | Files | Trials | `report_matches_sink` false | Of which |
|---|---|---|---|---|
| 001 (`mcp_refund_lost_response`) | 3 | 360 | 121 | every `duplicate`: reported done, two refunds |
| 002 (`mcp_refund`) | 8 | 1,860 | 800 | every `duplicate` (one trial with three refunds) |
| 003 (`mcp_single_call`) | 11 | 714 | 98 | every duplicate, including 23 in early files written before verdicts were recorded |
| 004 (`langgraph_crash`) | 4 | 585 | 95 | every `duplicate` |
| 005 (`dbos_crash`) | 6 | 1,590 | 209 | every `duplicate`. The 41 `stuck` trials never answered (`none`), so they match |
| 006, main run | 1 | 930 | 41 by the coder, **40 by hand** | 34 `duplicate` told one refund was made; 6 by hand (7 by the coder) said the refund failed while it had committed |
| 006, pilots | 2 complete | 114 | 8 | as recorded: the first pilot's codes come from the first coder rules (see the M5 plan, section 7) and were not re-coded here |

**In 001 to 005, the field adds nothing new:** it is false exactly for the duplicates. The subjects report completion only from a success result, so a duplicate is always "reported done, two refunds". That matches the contract's earlier note that the first experiment's client "reported one successful refund in every duplicate trial".

**006 is where the field and the verdict differ.** The verdict says `duplicate` or `stuck`; the field says whether the user was told something the sink contradicts. Two of the 34 duplicates (trials 336 and 430) claim success but say a duplicate cannot be ruled out. 006's rule-based coder codes them `claims_success`, so the field counts them as contradicting. A hand reading would call them closer to `unknown`, and the coder cannot tell them apart today.

## Not changed

- No verdict, in any file. `classify` is untouched.
- The result store schema. The field sits in each trial's `raw` JSON for new runs, and is derived by `report_check` for old ones.
- The 006 runner still does not record `fault_confirmed`. P2 documents how the fact is established instead.
