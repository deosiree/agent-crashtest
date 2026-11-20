# Contract v0.2: the stricter `correct`, and what it changes

Written 17 November 2026, before the contract and the checker were changed, as the owner asked. The full per-trial output is in [`contract-v0.2-reverdict.json`](contract-v0.2-reverdict.json).

## The decision

The owner's definition, adopted as contract v0.2 (section 3):

> the expected business effect must be verified against sink ground truth, the payload must match, the agent's final outcome must be consistent with that ground truth, and no unresolved operation may be falsely reported as successful.

`in_doubt_surfaced` and `stuck` stay distinct verdicts.

## What changed in the checker

| | v0.1 | v0.2 |
|---|---|---|
| Inputs | effects, intended, status | the same, plus `payload_matches` and `confirmed`, both **required**: no default may assume either |
| Verdicts | correct, duplicate, lost, in_doubt_surfaced, stuck | the same five, plus `wrong_payload` and `unconfirmed_success` |
| `correct` | count right, subject reported completion | count right, payload right, subject reported completion, and it had received a success result |

The two new verdicts are needed because the five v0.1 verdicts have no honest name for "right count, wrong content" or "right effect, but the success was a guess". Folding them into `lost` or `stuck` would hide what went wrong.

Precedence is unchanged where v0.1 already decided: `duplicate` first; then, for a subject that reported completion, `lost`, `wrong_payload`, `unconfirmed_success`, `correct` in that order; then `in_doubt_surfaced`; then `stuck`. v0.1 is exactly v0.2 with `payload_matches=True, confirmed=True`.

## How each experiment supplies the new inputs

| Experiment | Payload | Confirmed |
|---|---|---|
| 001, 002 (`trial.py`) | sink rows against the amount the runner sent | the client saw a success response (first-hand or a replay of the stored result). Completion is reported only then |
| 003 (`single_call.py`) | sink rows against the amount the runner now passes explicitly (2500, as before) | the subject reported `ok`, which it does only from a successful tool result |
| 004, 005 (`langgraph_crash.py`, `dbos_crash.py`) | as 003 | the scripted model answers only after the tool has returned a result |
| 006 (`model_retry.py`) | sink rows against 2500 | the model received a non-error `issue_refund` result, or a `get_refunds` result listing the refund. **Read separately from what the model tells the user**, so here the two can differ |

In 001 to 005, then, `confirmed` and "reported completion" coincide by construction. The new condition can only bite where a subject's report is not mechanically tied to what it received, which is the real-model study (006).

For 006, the subject's status comes from the coded final message: `claims_success` is completed; `says_uncertain` and `asks_user` are unknown, since the ambiguity was put to the user; `claims_failure`, anything else and a run that did not end normally are failed.

## Effect on every recorded result

Every trial in every results file was re-judged under v0.2 by `python -m crashtest.results.reverdict`. Each trial's effect count was taken as recorded; payloads were read from the sink's own rows; status and confirmation were derived from the fields each runner recorded. Nothing in `results/` was edited.

| File | Trials | Verdicts that change | Payload not verifiable | v0.2 verdicts |
|---|---|---|---|---|
| `mcp_refund/20261116T040750Z.json` (001, first run) | 120 | — | **120** | not re-judged; v0.1 verdicts stand |
| `mcp_refund/20261116T042024Z.json` (001) | 120 | 0 | 0 | correct 79, duplicate 41 |
| `mcp_refund/20261116T042252Z.json` (001) | 120 | 0 | 0 | correct 80, duplicate 40 |
| `mcp_refund/20261116T161126Z.json` (002) | 720 | 0 | 0 | correct 400, duplicate 320 |
| `mcp_refund/20261117T164731Z.json` (001 re-run) | 120 | 0 | 0 | correct 80, duplicate 40 |
| `mcp_refund/20261117T165004Z.json` (002 re-run) | 720 | 0 | 0 | correct 400, duplicate 320 |
| `single_call/20261116T165233Z.json` (003) | 180 | 0 | 0 | correct 60, duplicate 20, in_doubt_surfaced 100 |
| `single_call/20261116T171353Z.json` (003, CrewAI defaults) | 3 | 0 | 0 | duplicate 3 |
| `single_call/20261116T205316Z.json` (003 re-check) | 18 | 0 | 0 | correct 6, duplicate 2, in_doubt_surfaced 10 |
| `langgraph_crash/20261116T201651Z.json` (004) | 135 | 0 | 0 | correct 105, duplicate 30 |
| `langgraph_crash/20261116T204350Z.json` (004) | 135 | 0 | 0 | correct 105, duplicate 30 |
| `dbos_crash/20261117T013225Z.json` (005 run 1) | 360 | 0 | 0 | correct 320, duplicate 39, stuck 1 |
| `dbos_crash/20261117T143146Z.json` (005 run 2) | 360 | 0 | 0 | correct 320, duplicate 40 |
| `dbos_crash/20261117T151316Z.json` (005 step retries) | 60 | 0 | 0 | correct 10, duplicate 10, stuck 40 |
| `dbos_crash/20261117T151854Z.json` (005 SQLite) | 90 | 0 | 0 | correct 50, duplicate 40 |

**No verdict changes in any file that can be checked.** Every refund the sink holds for these trials carries the intended amount, 2500 cents, and every completion in 001 to 005 was confirmed.

### Caveats

- **The first 001 run cannot be re-judged.** Its refunds are not in the sink database used since, so its payloads cannot be checked after the fact. Its v0.1 verdicts stand, and any statement about 001 should rest on the later runs, which agree with it.
- **The payload check covers the amount.** Rows are read per order, so a refund under a different order is invisible to that read. In 001 to 005 the order is fixed by the harness; in 006 a model could send a different order ID, and that trial would then count no refund for the intended order (`lost` if the model claimed success).
- **The 003 re-run and the Linux run of 005** were still running when this was written. They used the v0.1 checker; the same tool re-judges them, and the write-ups say so.
- **005's step-retry run has 40 `stuck` trials** (plain and child-workflow tools whose `ReadTimeout` reached the caller). That is unchanged by v0.2. As the 005 write-up says, `in_doubt_surfaced` would describe them better, because the caller did receive an error; but the subject reported the run as failed, not as unknown, and the verdict follows the subject's report.

### Second pass, 18 November 2026

Every results file in `results/`, 32 in all, was re-judged again after the Linux runs and the timeout-ratio sweep, and loaded into the result store.

- **The files from Windows:** no verdict changed, as before.
- **The 13 files from Linux** (the 003, 004 and 005 Linux runs and the ten sweep runs) cannot have their payloads re-read here. Their sinks lived elsewhere: on GitHub runners that no longer exist, or, for 005, in the Docker stack's database. Re-judged under v0.2 with only the payload check left out, **none of their 1,125 trials changed verdict**.
- **Payloads:** every Linux run except 005's was written from a commit after `4738dc6`, so its runner checked payloads live under v0.2 as it ran. The 005 Linux run (`1205d85`) came before v0.2, so its payloads were never checked. Its refunds were issued by the harness with a fixed amount, as in every other 005 run, all of which pass the check.

The re-verdict tool now counts a trial it cannot re-check as `payload_not_verifiable` only, not also as a change; the earlier report above counted the first 001 run's 120 trials both ways.

## Compatibility

- `classify` now requires the two new arguments, so a runner that has not been updated fails loudly instead of silently judging under v0.1.
- The 001/002 summary gained one column, `other_verdicts`, for the two new verdicts; the existing columns are unchanged.
- The result store keeps verdicts as recorded. A file's verdicts are whatever its runner wrote; this review is how v0.1 files are read under v0.2.
