# Experiment 005 — kill and recover a pydantic-ai agent on DBOS

Run three times on Windows, on 17 and 18 November 2026 (local time; all on 17 November in UTC), plus once on Linux. Raw data in `results/dbos_crash/` and `results/dbos_crash_linux/`:

- Run 1: [`20261117T013225Z.json`](../../results/dbos_crash/20261117T013225Z.json), seed 5, commit `5bf80a7` (clean tree). The tables below are from this run.
- Run 3: [`20261117T210619Z.json`](../../results/dbos_crash/20261117T210619Z.json), seed 10, on another local day. See "Third run" below.
- Run 2: [`20261117T143146Z.json`](../../results/dbos_crash/20261117T143146Z.json), seed 6, after trials were isolated from each other (see "The one hung trial"). See "Second run" below. The code that ran was commit `ff89f79`; the file itself records `c25a1d5` with a dirty tree, because the runner read the commit when the run ended, after later commits and edits. The runner now records it when a run starts.

Both files record the subject environment's packages.

The design, predictions and controls were written before the run, in [`docs/m4-plan.md`](../m4-plan.md) section 4. Deviations from that design are listed under "Deviations" below.

Both behaviours measured here are documented by the libraries (m4-plan section 2a). **Nothing in this write-up is a defect in pydantic-ai or DBOS.** The experiment measures what the documented behaviour costs when a tool has a side effect.

## Result

**Hypothesis H2 held in all three of its parts. After a hard kill and automatic recovery, a pydantic-ai agent on DBOS refunded the order twice when the kill landed during the tool call and the tool sent no idempotency key, however the tool was wired (29 of 30 recorded; the thirtieth is explained below). When the kill landed after the tool had returned, a plain function tool refunded twice (10 of 10) and a tool wired as a `@DBOS.step` or a child workflow refunded once (20 of 20). Every idempotency key tested, including the model's tool-call ID, held in every cell (180 of 180 killed trials refunded once).**

Ten trials per cell, 36 cells, 360 trials, in a seeded random order. "Twice" means two refund rows at the sink for one order.

**Killed during the tool call** — the sink had committed the refund; the response had not reached the agent.

| Tool wiring | No key | Key = tool-call ID | Key = workflow ID | Key = order |
|---|---|---|---|---|
| plain function | **twice 10/10** | once 10/10 | once 10/10 | once 10/10 |
| `@DBOS.step` | **twice 9/10**, 1 hung | once 10/10 | once 10/10 | once 10/10 |
| child workflow | **twice 10/10** | once 10/10 | once 10/10 | once 10/10 |

**Killed after the tool returned** — as the agent entered its second model call.

| Tool wiring | No key | Key = tool-call ID | Key = workflow ID | Key = order |
|---|---|---|---|---|
| plain function | **twice 10/10** | once 10/10 | once 10/10 | once 10/10 |
| `@DBOS.step` | once 10/10 | once 10/10 | once 10/10 | once 10/10 |
| child workflow | once 10/10 | once 10/10 | once 10/10 | once 10/10 |

**No kill (control):** once in 120 of 120.

Exact 95% bounds (Clopper–Pearson) on the rate of a double refund:

| Condition | Double refunds | 95% bound |
|---|---|---|
| Killed during the tool, no key, any wiring | 29 of 30 recorded | 83% to 99.9% |
| Killed after the tool, plain function, no key | 10 of 10 | at least 69% |
| Killed after the tool, step or child workflow, no key | 0 of 20 | at most 17% |
| Killed at either point, any key | 0 of 180 | at most 2.0% |
| No kill | 0 of 120 | at most 3.0% |

**Controls.** Both pre-registered controls passed. Negative control (`during_tool`, plain, no key): twice in 10 of 10. Positive control (order-derived key): once in all 90 trials. The sink's log confirmed the withheld response in all 120 mid-tool kills. All 240 kills reached their kill point.

**Verdicts** from the shared checker: `duplicate` for every "twice" trial, `correct` for every "once" trial, and `stuck` for the one hung trial.

## Second run

Run 2 repeated the whole design, with a different seed for the cell order and with each trial on its own DBOS executor. The machine was held awake. **Every one of the 36 cells gave the same refund count in all ten trials as the prediction**, including the `during_tool`, step, no-key cell that lost a trial in run 1: twice in 10 of 10. All 240 kills reached their kill point; all 240 recoveries were made by the trial's own executor and ended `SUCCESS` with `recovery_attempts = 2`. The sink confirmed the withheld response in all 120 mid-tool kills. In all 60 killed trials that used the tool-call ID as the key, both attempts sent the same key. Verdicts: 320 `correct`, 40 `duplicate`.

Both runs together, with exact 95% bounds on the rate of a double refund:

| Condition | Double refunds | 95% bound |
|---|---|---|
| Killed during the tool, no key, any wiring | 59 of 60 recorded | 91% to 99.96% |
| Killed after the tool, plain function, no key | 20 of 20 | at least 83% |
| Killed after the tool, step or child workflow, no key | 0 of 40 | at most 8.8% |
| Killed at either point, any key | 0 of 360 | at most 1.0% |
| No kill | 0 of 240 | at most 1.5% |

## Third run, on another day

Run to meet the design's "two runs on different days". Raw data: [`20261117T210619Z.json`](../../results/dbos_crash/20261117T210619Z.json), seed 10, from commit `589b669` with a clean tree, on Windows. It started at 02:36 on 18 November local time (IST), about 19.5 hours after run 1. In UTC, which the file names use, that is still 17 November: a different calendar day on the machine, the same UTC date.

**Every one of the 36 cells gave the same count as runs 2 and the Linux run, in all ten trials.** All 240 kills reached their kill point. Verdicts: 320 `correct`, 40 `duplicate`. Against run 1 the only difference is the cell that lost a trial to standby (`during_tool`, step, no key): here it is twice in 10 of 10, which is what the database showed that cell's true outcome to be.

Across all four full runs (three on Windows, one on Linux; 1,440 trials) the refund counts agree in every cell.

## On Linux, in Docker

The plan listed Windows-only behaviour as a risk, to be checked by one run on Linux before relying on the result. Raw data: [`results/dbos_crash_linux/20261117T172403Z.json`](../../results/dbos_crash_linux/20261117T172403Z.json), the full design, seed 9.

The runner and the subject ran in a Linux container (Python 3.12.16, the subject's environment built from the same lock file), against the Docker Compose PostgreSQL and sink, under Docker Desktop's WSL 2 kernel. The file records commit `1205d85` with a dirty tree; the only uncommitted files were two new results files from other experiments. The image's harness and subject code were checked file by file against that commit and were identical.

**Every one of the 36 cells gave the same count as on Windows, in all ten trials.** All 240 kills reached their kill point and all 240 recoveries ended `SUCCESS` with `recovery_attempts = 2`; the sink confirmed the withheld response in all 120 mid-tool kills; the tool-call ID key was stable in 60 of 60. Verdicts: 320 `correct`, 40 `duplicate`.

One difference in timing, not outcome: when the restarted process read the workflow's status, it was `PENDING` in 164 of 240 recoveries on Linux, against `ENQUEUED` in every recovery on Windows. DBOS had already picked the workflow up for recovery by the time the status was read. Nothing downstream changed.

## Confirming pass with SQLite

The design asked for one pass over the no-key cells with DBOS's system database in SQLite instead of PostgreSQL. Raw data: [`20261117T151854Z.json`](../../results/dbos_crash/20261117T151854Z.json), seed 8, 90 trials. It ran from commit `131e5ba` with uncommitted edits to other runners and to documentation; the DBOS runner and subject were unchanged.

**Every one of the 9 cells gave the same count as on PostgreSQL, in all ten trials:** once with no kill; twice for every wiring when killed during the tool; after the tool returned, twice for the plain tool and once for step and child workflow. All 60 kills reached their kill point and all 60 recoveries ended `SUCCESS` with `recovery_attempts = 2`. The choice of system database made no difference here.

## Secondary question: step retries without a crash

The plan asked whether a step that is allowed to retry duplicates the refund when the response is late, with no kill at all. Raw data: [`20261117T151316Z.json`](../../results/dbos_crash/20261117T151316Z.json), commit `131e5ba` (clean tree), seed 7, 10 trials per cell.

**Setup.** The sink commits the refund and holds its response for 5 seconds. The tool's HTTP timeout is 2 seconds. For the step wiring the step is declared with `retries_allowed=True, max_attempts=3, interval_seconds=0.5`. The other two wirings have no retry setting to turn on and ran as they are.

| Tool wiring | No key | Key = order | What the caller saw |
|---|---|---|---|
| `@DBOS.step`, retries on | **twice 10/10** | once 10/10 | success: the workflow finished and the agent answered |
| plain function | once 10/10 | once 10/10 | the `ReadTimeout` propagated; workflow `ERROR` |
| child workflow | once 10/10 | once 10/10 | the `ReadTimeout` propagated; workflow `ERROR` |

So **turning on step retries makes a late response alone duplicate the refund, with no crash, and the caller is told the run succeeded.** This is the failure of experiments 001 and 003 (a client retry after a lost response), reproduced inside a durable engine by one decorator argument. An idempotency key held.

The plain and child-workflow trials are recorded with the verdict `stuck`. The checker gives that to a subject that failed rather than one that reported an unknown outcome. Here the caller did receive an error, so `in_doubt_surfaced` describes it better; the results file keeps the checker's verdict.

## The one hung trial

Trial 148 (`during_tool`, step, no key) is recorded with one refund and the verdict `stuck`: the restarted process did not report within the 90-second limit.

It was not caused by the subject. The laptop entered Modern Standby during the run; the Windows event log shows the session resuming at 07:20:44 local time (01:50:44 UTC), one second after this trial's first refund committed at 01:50:43, and the sink received no request from anyone for the next 10 minutes 49 seconds.

The database was inspected after the run. It shows that this order did receive a second refund, at 02:01:36 UTC (sink request 3232), in the same millisecond as the next trial's recovery request (3233). The next trial's restarted process reported two tool calls and two sink posts. So the next process recovered trial 148's pending workflow as well as its own, and that workflow then refunded a second time, as predicted for its cell.

The results file is not edited. The tables above use the recorded count, 9 of 10. Two things follow:

- **The cell's true outcome was twice in 10 of 10**, but the tenth was observed outside the trial's window and is not in the results file.
- **Trials are not isolated from each other.** Every subject process uses one DBOS application name and executor, and `DBOS.launch()` recovers every pending workflow it finds. A workflow left pending by one trial is finished by the next process. In this run that happened once and did not change the next trial's verdict (its own order had two refunds regardless). It is a harness limit to fix before the second run: give each trial its own executor ID or application name.

  *Fixed after this run:* the runner now gives each trial its own DBOS executor (`--executor-id exec-<workflow id>`), and DBOS recovers only its own executor's pending workflows. Checked by hand: a workflow left pending by a killed trial was not touched by another trial's process, and was recovered by a process with its own executor ID. The run above predates the fix.

## What happened on recovery

In all 239 recovered trials the restarted process only called `DBOS.launch()` and waited. The workflow's status at that moment was `ENQUEUED`; it finished as `SUCCESS` with `recovery_attempts = 2`.

| Kill point | Tool wiring | Model calls on recovery | Tool function entered | Sink requests per trial |
|---|---|---|---|---|
| during tool | plain | 1 (final answer only) | yes | 2 |
| during tool | step | 1 | yes | 2 |
| during tool | child workflow | 1 | yes | 2 |
| after tool | plain | 1 | yes | 2 |
| after tool | step | 1 | yes, but the step returned its recorded result | 1 |
| after tool | child workflow | 1 | no | 1 |

The model was never asked again for the tool call: its first response was replayed from its recorded step. In all 60 killed trials that used the tool-call ID as the key, both attempts sent the same key.

## What it shows

1. **A function tool that is not a step runs again on recovery, even after it has returned.** pydantic-ai routes model requests through DBOS steps but leaves function tools in workflow code, and workflow code is re-executed. The documentation says to decorate tools that do I/O with `@DBOS.step`; this is what happens to a refund when that advice is not followed.
2. **Wiring the tool as a step or child workflow closes the window after the tool returns, and not the window during it.** A step killed before it completes is run again; DBOS documents steps as "tried at least once". The effect had already committed, so without a key it was repeated in every recorded case.
3. **So a step is not a substitute for an idempotency key.** In this experiment the only configuration that refunded once at both kill points was one that sent a key.
4. **The tool-call ID was a stable key here**, unlike LangGraph under `exit` durability in experiment 004, because the model's response is recorded as a step before the tool runs and is replayed, not regenerated. The pre-registration called this the prediction most likely to be wrong; it was not wrong in 60 of 60.
5. **The workflow ID is also a usable key**, and it is available inside the tool without any plumbing (`DBOS.workflow_id`). It identifies the run, not the business operation: it protects against recovery of one run, and would not protect against the same order being submitted in a second workflow.

## What was run

- **Subject:** [`subjects/pydantic_ai_dbos_refund_agent.py`](../../subjects/pydantic_ai_dbos_refund_agent.py). A pydantic-ai `Agent` with the `DBOSDurability` capability and one tool, `issue_refund`, run inside a `@DBOS.workflow` with an assigned workflow ID. The tool posts to the sink over HTTP.
- **Tool wiring:** *plain* makes the HTTP call in the tool function; *step* calls a `@DBOS.step` (default settings, so no step retries); *child workflow* is an async tool marked with `metadata={"dbos": {"child_workflow": {}}}`.
- **Model:** pydantic-ai's `FunctionModel`, scripted, in process. Its first call returns a tool call; its second returns a final message. No model API is involved.
- **Kill:** the harness hard-kills the subject's process, so no cleanup code runs.
  - *During the tool:* the sink commits the refund and withholds its response for 20 seconds; the harness kills the process once the sink shows the refund.
  - *After the tool:* the process announces that it is entering its second model call and waits; the harness kills it there.
- **Recovery:** the harness starts the subject again with `--resume`. That process launches DBOS and waits for the workflow's result. It makes no decision about whether to run anything again; DBOS does.
- **Runner:** [`src/crashtest/experiment/dbos_crash.py`](../../src/crashtest/experiment/dbos_crash.py), seed 5.
- **Versions:** pydantic-ai-slim 2.68.0, dbos 3.5.0, Python 3.12.2, Windows 11. DBOS system database and sink both in PostgreSQL from `pgserver`.

## Deviations from the pre-registered design

- **Runs on different days:** met by the third run, on a different local calendar day, though the same UTC date (see "Third run"). All Windows runs were on one machine. The second and later runs also differed from the first in one way the design did not plan: each trial had its own DBOS executor.
- **The SQLite pass and the step-retry question** were run on the same day as the main runs, as separate results files.
- **The machine went into standby during the run.** One trial was lost to it, as described above. Standby can make a trial hang; it cannot add a refund at the sink.
- **The Linux run** was done once, as the plan's risks section asked, on the same day.

## What it does not show

- **Not a defect in pydantic-ai or DBOS.** Both behaviours are documented.
- **One agent shape.** A single tool call. No parallel tool calls, although pydantic-ai runs tool calls in parallel by default; no MCP tools, which pydantic-ai does route through steps.
- **A scripted model.** The model's response was replayed here, so a real model would not have been asked again either; but a real model's behaviour after a tool error is untested (hypotheses H4 and H5).
- **Two kill points,** both on the host, not a container kill. A kill between the sink's response arriving and the step's checkpoint being written was not targeted separately; `during_tool` kills earlier than that.
- **Step retries only in the secondary run,** with one setting (3 attempts, 0.5 s apart) and one timeout-to-delay ratio.
- **Small counts,** two runs, one machine, one day.
- **The workflow-ID key was tested within one workflow only.**

## Reproduce

Build the subject environment from [`subjects/requirements/pydantic_ai_dbos_refund_agent.txt`](../../subjects/requirements/pydantic_ai_dbos_refund_agent.txt), then:

```powershell
python -m crashtest.experiment.dbos_crash --local --python <pydantic-ai env>\Scripts\python.exe --trials 10 --seed 5
```

About 25 minutes of machine time if the machine stays awake.
