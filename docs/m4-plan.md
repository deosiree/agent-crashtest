# M4 plan — pydantic-ai with DBOS (hypothesis H2)

Written 17 November 2026. **Status:** tasks 1–6 are done; experiment 005 was run once and is written up in [`experiments/005-pydantic-ai-dbos.md`](experiments/005-pydantic-ai-dbos.md). Tasks 7 and 8, the second run and the SQLite pass are open. Section 4 has not been changed since before the run.

This is the investigation and the implementation plan for milestone M4. Nothing in it is a recorded result: the probe figures in section 3 come from a hand-driven script, a few trials per cell, and are here to show the plan is feasible.

## 1. The hypothesis

H2, from [`evidence-check.md`](evidence-check.md):

> pydantic-ai with DBOS duplicates an effect when the tool is a plain function, and does not when it is a `@DBOS.step` — except in the window between effect and step checkpoint.

## 2. What the source says

Read from the installed packages: `pydantic-ai-slim` 2.68.0 and `dbos` 3.5.0. The documentation pages were read afterwards; see section 2a.

| Finding | Where | Consequence for M4 |
|---|---|---|
| `DBOSAgent` is deprecated in favour of a `DBOSDurability` capability | `pydantic_ai/durable_exec/dbos/_agent.py`, the `@deprecated` block above `class DBOSAgent` | Test the capability. H2 was written against the wrapper. |
| With the capability, the caller wraps `agent.run()` in their own `@DBOS.workflow` | Same block: "call `agent.run()` inside a `@DBOS.workflow`; the wrapper did this automatically" | The subject owns the workflow, and so owns its ID. |
| Model requests and MCP calls are routed through DBOS steps. Function tools are not | `_durability.py`, class docstring; `engine_spec.wrapped_toolset_kinds = {'mcp', 'dynamic'}` | A plain function tool runs in workflow code and is re-executed on recovery. This is the premise of H2. |
| A function tool can instead run as a child workflow, chosen by tool metadata `{'dbos': {'child_workflow': ...}}` | `durable_exec/_base.py`, `selects_child_workflow`; `ChildWorkflowConfig` in `dbos/_utils.py` | A third tool configuration that H2 does not mention. |
| A DBOS step does not retry by default: `retries_allowed=False`, `max_attempts=3`, `interval_seconds=1.0`, `backoff_rate=2.0` | `dbos/_dbos.py`, `DBOS.step` signature | Turning retries on is a separate way to duplicate an effect, with no crash at all. |
| A workflow is recovered at most 100 times | `dbos/_registrations.py`, `DEFAULT_MAX_RECOVERY_ATTEMPTS = 100` | A workflow that keeps crashing ends as an error; that maps to the `stuck` verdict. |
| The system database defaults to a SQLite file; PostgreSQL is supported | `dbos/_dbos_config.py`, `system_database_url` | Record which one each run used. |
| Tool calls run in parallel by default (`parallel_ordered_events`) | `_durability.py`, `parallel_execution_mode` | Relevant to fault point K5. |

## 2a. What the documentation says

Read on 17 November 2026. The quotations were extracted from the live pages by a fetch tool and were not copied by hand, so check them against the pages before quoting them anywhere else.

pydantic-ai, DBOS durable-execution page (`pydantic.dev/docs/ai/integrations/durable_execution/dbos/`):

- "When the agent runs inside a DBOS workflow, the capability routes model requests and MCP communication through DBOS steps."
- On function tools: "If they involve non-deterministic behavior or perform I/O, you should explicitly decorate them with `@DBOS.step`."
- "Skip the decorator if durability isn't needed, so you avoid the extra DB checkpoint write."
- "Steps may perform I/O (network, disk, API calls). If a step fails, it restarts from the beginning."
- "`DBOSAgent` is the original wrapper-agent path for DBOS integration and will be removed in v3."
- "Child workflows are only supported for async tools in a `FunctionToolset` with a unique `id`."

DBOS, workflow tutorial (`docs.dbos.dev/python/tutorials/workflow-tutorial`):

- "If a workflow is interrupted for any reason, DBOS automatically recovers its execution from the last completed step."
- "Steps are tried at least once but are never re-executed after they complete."
- "An assigned workflow ID acts as an idempotency key"

**What this means for experiment 005.** Both behaviours H2 predicts are documented:

- A plain function tool that does I/O is being used against the documentation's advice. If it runs again on recovery, that is documented behaviour, not a defect.
- "Tried at least once" covers a step killed before it completed. If it runs again, that is also documented.

So experiment 005 cannot find a "violation" in either library. What it can do is measure the documented behaviour, show what it costs when the tool has a side effect, and show which idempotency keys survive it.

## 3. Feasibility probe

A subject script exists: [`subjects/pydantic_ai_dbos_refund_agent.py`](../subjects/pydantic_ai_dbos_refund_agent.py). It uses pydantic-ai's `FunctionModel` as the scripted model, so no new mock was needed. It was driven by an uncommitted script against the local stack, with DBOS's system database in SQLite. Refunds per order after a hard kill and restart:

| Kill point | Tool mode | No key | Key = workflow ID | Key = order | Trials per cell |
|---|---|---|---|---|---|
| none | plain, step | 1 | — | — | 1 |
| During the tool call | plain | **2** | 1 | 1 | 1 |
| During the tool call | step | **2** | 1 | 1 | 1 |
| After the tool returned | plain | **2** | 1 | — | 3 |
| After the tool returned | step | 1 | 1 | — | 3 |

What the probe suggests, to be confirmed by the real experiment:

- **H2 looks right in all three of its parts.** After the tool returned, the plain tool was called again on recovery and the step tool was not (its sink call count on recovery was 0). During the tool call, both duplicated: that is the window between effect and step checkpoint.
- **DBOS recovered the workflow by itself.** The restarted process only called `DBOS.launch()` and waited; the workflow's status at that moment was `ENQUEUED`, and it finished as `SUCCESS` with `recovery_attempts = 2`.
- **The model was not asked again for the tool call.** On recovery the scripted model ran once, for the final answer only. The first model response was replayed from its recorded step. So, unlike LangGraph under `exit` durability in experiment 004, the tool-call ID should stay stable; the probe did not test that key.
- **The workflow ID is a usable idempotency key**, and one that LangGraph has no direct equivalent of.

## 4. Experiment 005 — design, written before the run

This section is the pre-registration that the [scientific audit](audits/scientific-audit.md) (section 1.3) asks for. Do not change it after seeing results; record deviations in the write-up.

**Question.** After a hard kill and automatic recovery, how many refunds does a pydantic-ai + DBOS agent make, as a function of how the tool is wired, where the kill lands, and what idempotency key the tool sends?

**Factors.**

| Factor | Levels |
|---|---|
| Tool mode | `plain` (HTTP call in the tool function); `step` (tool calls a `@DBOS.step`); `child_workflow` (tool metadata selects a child workflow) |
| Kill point | `none`; `during_tool` (sink has committed, response withheld); `after_tool` (entering the second model call) |
| Key strategy | `none`; `tool_call_id`; `workflow_id`; `business` |
| System database | PostgreSQL (primary); SQLite (one confirming pass over the `none`-key cells) |

3 × 3 × 4 = 36 cells on PostgreSQL.

**Predictions.**

| Kill point | Tool mode | No key | Any stable key |
|---|---|---|---|
| none | all | 1 | 1 |
| during_tool | all | 2 | 1 |
| after_tool | plain | 2 | 1 |
| after_tool | step, child_workflow | 1 | 1 |

`tool_call_id` is predicted to behave as a stable key in every cell, because the model response is replayed. This is the prediction most likely to be wrong.

**Controls.** Negative control: `during_tool`, `plain`, no key, must give 2. Positive control: any cell with the `business` key must give 1. If either fails, the harness is wrong and the run is void.

**Trials.** 10 per cell, cell order randomised, the whole design run twice on different days. Report conditions varied first, counts second, with exact 95% bounds.

**Measures per trial.** Refunds and requests at the sink; `fault_confirmed`; the verdict from `crashtest.checker`; DBOS workflow status before and after recovery; `recovery_attempts`; model calls, tool calls and sink posts in the recovered process.

**What would refute H2.** A `step` or `child_workflow` cell at `after_tool` with no key giving 2; or a `plain` cell at `after_tool` with no key giving 1.

**Secondary question, separate run.** With `retries_allowed=True` on the step and a withheld response but **no kill**, does the step retry and duplicate? This needs an HTTP timeout shorter than the withheld response. It tests the same failure as experiment 003 inside a durable engine.

## 5. Implementation tasks

In order. Each ends with something testable.

1. **Read and quote the documentation.** DBOS: workflows, steps, recovery, idempotency. pydantic-ai: the DBOS durable-execution page. Record what each promises about tools with side effects. A result that matches the documentation is "documented", not a defect (contract §7, rule 3).
2. **Generalise the crash runner.** `langgraph_crash.py` hard-codes its script, its factor names and its argument names. Extract a `crash_runner` that takes a subject description: script, factors, how to pass them, and which kill points it supports. Keep `langgraph_crash` working and its results unchanged; add a test that the summary is identical for an existing results file.
3. **Finish the subject.** Add `tool_call_id` as a key strategy (take it from the tool's `RunContext`); add `child_workflow` mode; accept a PostgreSQL system database; report the DBOS status fields. Add a subject smoke test that is skipped when the environment is absent.
4. **Pin the environment.** Add `subjects/requirements/pydantic_ai_dbos.txt` with exact versions, and a script that builds each subject environment. This also closes the audit's reproducibility item for the existing subjects.
5. **Make results self-describing.** Git hash and `pip freeze` of the subject environment in every results file.
6. **Run experiment 005** as designed in section 4. Write it up as `docs/experiments/005-pydantic-ai-dbos.md` using the structure of 004.
7. **Run the secondary question** on step retries.
8. **Result store** (roadmap M4): only after 005, so the schema is designed from five experiments' worth of real results, not guessed.

Estimated effort: tasks 1–5 are roughly two working sessions; the runs are about an hour of machine time in total.

## 6. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| The `after_tool` kill races the step's checkpoint write, as it did for LangGraph's `async` mode | Medium | Add a controlled delay between the marker and the kill and sweep it. Audit section 1.4. |
| DBOS recovery depends on the executor ID or application version matching across restarts | Medium | The probe worked with defaults on one machine. Set both explicitly and record them. |
| Windows-only behaviour | Medium | Run 005 once in the Docker stack on Linux before writing it up. |
| pydantic-ai or DBOS releases change behaviour | High over months | Pin versions; record them; state the versions in every claim. |
| `child_workflow` mode needs more setup than the metadata flag | Unknown | Timebox to two hours; if it does not work, drop the level and say so. |
| The kill-and-resubmit choice shapes the result, as in experiment 004 | Low here | DBOS recovers by itself, so the harness makes no resubmit decision. State this. |

## 7. Out of scope for M4

Real models (M5); Temporal, Prefect and Restate, although pydantic-ai ships integrations for the first two in the same `durable_exec` package; fault points K1 and K5; the scoreboard.
