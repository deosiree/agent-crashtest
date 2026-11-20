# Experiment 004 — kill and resume a LangGraph agent

Run twice on Windows on 16 November 2026 (UTC): [`20261116T201651Z.json`](../../results/langgraph_crash/20261116T201651Z.json) and [`20261116T204350Z.json`](../../results/langgraph_crash/20261116T204350Z.json) in `results/langgraph_crash/`. Run once on Linux on 17 November (UTC): [`20261117T214634Z.json`](../../results/langgraph_crash_linux/20261117T214634Z.json) in `results/langgraph_crash_linux/`, with its log beside it. A kill-delay sweep followed on Windows; see the sections near the end.

The second run was made after the P1 gaps in the [implementation gap report](../audits/implementation-gap-report.md) were closed. It takes its verdicts from the shared checker and records, per trial, that the sink's fault fired. Every refund count in the tables below was the same in both runs.

LangGraph's documentation was read on 17 November 2026, after the runs; see "What the documentation says" below. Every behaviour measured here is documented. Nothing in this write-up is a defect in LangGraph.

## Result

**A LangGraph agent killed while its refund tool was in flight refunded the order twice on resume, in every durability mode, unless the tool sent an idempotency key (15 of 15 without a key). Which key worked depended on the durability mode: a key taken from the model's tool-call ID held under `sync` and `async` but failed under `exit` (10 of 10 double refunds), because nothing had been saved and the model was asked again and issued a new ID. Only a key derived from the order held in every cell (30 of 30).**

> **Correction, 18 November 2026.** On Linux, a kill just after the tool returned under `async`, LangGraph's default, **refunded twice in 5 of 5 trials without a key**, where both Windows runs refunded once. In 11 of the 15 `async` trials killed there, the checkpoint holding the tool's result had not been written, so the resumed run executed the tool again. This is the risk LangGraph's documentation states for `async`. The Windows machine happened to write the checkpoint in time. So the table below shows `async` as safe after the tool returned only on Windows; in general, only `sync` or an idempotency key protects the effect at that point. Every key tested still held. See "On Linux: result".

Five trials per cell per run; the fractions below are from one Windows run and the other matched. "Twice" means two refund rows at the sink for one order.

**Killed during the tool call** — the sink had committed the refund; the response had not reached the agent.

| Durability | No key | Key = tool-call ID | Key = order |
|---|---|---|---|
| `sync` | **twice 5/5** | once 5/5 | once 5/5 |
| `async` (LangGraph's default) | **twice 5/5** | once 5/5 | once 5/5 |
| `exit` | **twice 5/5** | **twice 5/5** | once 5/5 |

**Killed after the tool returned** — as the agent entered its next model call.

| Durability | No key | Key = tool-call ID | Key = order |
|---|---|---|---|
| `sync` | once 5/5 | once 5/5 | once 5/5 |
| `async` | once 5/5 on Windows; **twice 5/5 on Linux** | once 5/5 | once 5/5 |
| `exit` | **twice 5/5** | **twice 5/5** | once 5/5 |

**No kill (control):** once in 45 of 45.

In each run all 90 kills reached their kill point and all 90 resumed runs finished with a final answer. In the second run the sink's log confirmed the withheld response in all 45 mid-tool kills, and the checker's verdicts were `duplicate` for every "twice" cell and `correct` for every "once" cell.

## What happened on resume

| Kill point | Durability | What the restarted process found | What it ran | Sink requests per trial |
|---|---|---|---|---|
| during tool | `sync`, `async` | A checkpoint holding the model's tool call, with the tool step pending | The tool again, then the model | 2 |
| during tool | `exit` | No checkpoint | Everything again: model, tool, model | 2 |
| after tool | `sync` | A checkpoint holding the tool result, with the model step pending | The model only | 1 |
| after tool | `async` (Windows) | The tool result, but **no step listed as pending** in 13 of 15 trials in each run | The model only | 1 |
| after tool | `async` (Linux) | In 11 of 15 trials, **only the checkpoint from before the tool**, with the tool step pending; in 4, the tool result with nothing pending | The tool again, then the model (11); the model only (4) | 2 (11 trials); 1 (4) |
| after tool | `exit` | No checkpoint | Everything again | 2 |

## What it shows

1. **Resuming re-runs the interrupted tool.** With a checkpoint in place, LangGraph restarts the tool step, and the tool calls the sink a second time. The effect had already committed, so without a key it is repeated.
2. **A tool-call ID is a stable key only if the tool call was saved.** Under `sync` and `async` the model's message was checkpointed before the tool ran, so the resumed tool saw the same ID. Under `exit` it was not; the model ran again and produced a new ID. The key changed between attempts, which experiment 001 showed is the same as no key.
3. **`exit` durability turns "after the tool returned" into a duplicate too.** Nothing is saved until the run ends, so a kill at any point before that repeats every effect.
4. **A key derived from the business operation held everywhere**, because it does not depend on what the framework saved.
5. **Under `async`, the saved state can look finished when it is not.** After a kill following the tool, `get_state()` returned the tool result with an empty `next` in 13 of 15 trials in each run (26 of 30), though the agent had not answered. Calling `invoke(None, config)` still continued correctly and did not repeat the tool. An application that checks `next` to decide whether to resume would stop there. In the remaining trials `next` named the model step, so this depends on timing. The cause was not traced in LangGraph's source; the kill-delay sweep below measured the window.
6. **Under `async`, a kill just after the tool returned can repeat the effect.** On Linux the checkpoint with the tool's result was missing in 11 of 15 such kills, and the tool ran again; without a key that refunded twice in 5 of 5. On Windows it was always present. How often this happens depends on how fast the checkpoint write is relative to the kill, so it is a property of the machine, not something `async` guarantees against. With a key from the tool-call ID or from the order, the effect was not repeated in any of the 10 keyed trials (the tool ran again in 6 of them, and the key turned the second call into a replay).

## What the documentation says

Read on 17 November 2026 from the source of LangChain's documentation site (`langchain-ai/docs`, `src/oss/langgraph/`, last changed in commit `ab83b7e` on 6 November): the pages on checkpointers, the functional API and interrupts. The separate "durable execution" page that older links point to now redirects to the persistence overview. The default durability was checked in the installed package (langgraph 1.2.22, `pregel/main.py`), whose docstrings say `durability` "defaults to `async`". Paraphrased below, with short quotations; check against the pages before quoting further.

| Finding above | What the documentation says | Documented? |
|---|---|---|
| 1. Resuming re-runs the interrupted tool | `invoke(None, config)` loads the latest checkpoint and "retries unfinished work" (checkpointers, "Choose how to continue a thread"). Work that started but did not finish "may run again"; the advice is to use idempotency keys or check existing results before writing (functional API, "Idempotency"). If a node fails after committing a side effect but before completing, retrying it "can repeat the effect" (interrupts) | **Yes**, including the advice to use a key |
| 2. A tool-call ID key fails under `exit` | Not addressed directly. It follows from the next row: with nothing saved, the model call is made again and issues a new ID | Follows from documented behaviour |
| 3. `exit` turns "after the tool returned" into a duplicate | Under `exit`, intermediate state is not saved, "so you cannot recover from system failures (like process crashes) mid-execution" (checkpointers, "Durability modes") | **Yes** |
| 4. A key derived from the order held everywhere | Consistent with the advice to make side-effecting work idempotent and to deduplicate writes in the external system (functional API; interrupts) | Consistent |
| 5. Under `async`, `next` was empty though the agent had not answered | `async` carries "a small risk" that a checkpoint is not written if the process crashes. The pages also say that per-node writes are kept as pending writes within a super-step, and that resuming returns the saved result without running nodes when the latest checkpoint has no unfinished work | **Not explained by the documentation.** The kill-delay sweep below shows it is a window of under 10 ms after the tool returns, under `async` only; the mechanism inside LangGraph is untraced |
| 6. Under `async`, a kill just after the tool can repeat it (Linux) | `async` carries "a small risk" that a checkpoint is not written if the process crashes during execution | **Yes**: this is that risk, observed |

Two more points from the documentation bear on how this experiment was run:

- **The resubmit after a kill under `exit`.** The checkpointer page warns that a crash before the first durable checkpoint leaves a thread indistinguishable from one that was never invoked, and suggests recording the request outside the checkpointer (an outbox row) to tell "never accepted" from "accepted but lost". The harness resubmitted in exactly that situation, as an application without such a record would have to.
- **Where side effects belong.** The documentation's advice is to put side effects in their own task (functional API) or their own node (interrupts), so that completed work is restored rather than repeated. The subject's tool is a node of its own, which is why a kill *after* the tool returned did not repeat it under `sync`, nor under `async` on Windows. On Linux, `async` had not yet saved the node's result in most such kills.

## On Linux, in Docker: design, written before the run

Scientific-audit action 5. The full design (three kill points, three durability modes, three key strategies, 5 trials per cell, 135 trials) is run once in a Linux container (`docker compose --profile experiments run --rm crash-004`), with the subject's environment built from the same lock file, against the Compose PostgreSQL (as checkpointer) and sink. Unlike the Windows runs, the cell order is randomised (seed 44).

**Prediction.** Every cell gives the same refund count as on Windows, in every trial. The `async` empty-`next` count is not predicted: the kill-delay sweep showed it is a timing window, and its width depends on the machine.

## On Linux: result

Run on 17 November 2026 (UTC) on a GitHub-hosted Ubuntu machine through the experiment workflow (`.github/workflows/experiment.yml`, run 44183198318), from commit `ddf4567` with a clean tree. Raw data: [`20261117T214634Z.json`](../../results/langgraph_crash_linux/20261117T214634Z.json) and its log.

**Deviation from the design above.** The run did not use Docker. The attempt in a local container crashed Docker Desktop when Windows ran short of memory, so the experiment ran directly on the Linux machine with the pip-installed PostgreSQL, as the CI tests do. Everything else is as designed: the full design, the subject's environment from the same lock file (minus the Windows-only `pywin32`), seed 44, 135 trials.

**The prediction failed in one cell of 27.** All 90 kills reached their kill point. Verdicts: 100 `correct`, 35 `duplicate`.

| Cell | Windows (two runs) | Linux |
|---|---|---|
| All other 26 cells | as in the tables above | **identical** |
| after tool, `async`, no key | once 5/5 in each run | **twice 5/5** |

What the restarted process found after an `async` kill following the tool, all three key strategies together:

| Saved state | Trials | What ran | Refunds |
|---|---|---|---|
| Only the checkpoint from before the tool (`next` = tools) | 11 of 15 | the tool again, then the model | twice with no key (5 of 5); once with a key (6 of 6) |
| The tool result, nothing pending (`next` empty) | 4 of 15 | the model only | once |
| The tool result, model pending | 0 of 15 | | |

Under `sync` all 15 such restarts found the tool result with the model step pending and refunded once.

**What this means.** The Windows runs said `async` protected a completed tool from a crash at the next model call. That was this machine's timing, not a property of `async`: on a slower or busier machine the checkpoint holding the tool's result can still be in flight when the process dies, and LangGraph then correctly resumes from the earlier checkpoint and runs the tool again. Its documentation says as much. For a tool with a side effect, `async` without an idempotency key is exposed after the tool returns as well as during it.

**Limits.** One Linux run, five trials per cell, on one kind of shared cloud machine whose speed is not controlled. The 11 of 15 is a property of that machine. (The commit that added the results file says 12 of 15; that was a miscount, corrected here.) Experiment 003's Linux run and the timeout-ratio sweep (audit action 6) were still to come when this was written.

## Kill-delay sweep: design, written before the run

Scientific-audit action 4 (section 1.4): the `async` observation in finding 5 is the one result here that depends on timing, and the timing was not controlled. This section fixes the follow-up's design before it is run; do not change it afterwards, and record deviations with the results.

**Question.** Does the empty `next` after a kill following the tool depend on how long after the tool returned the kill lands?

**Conditions.** Kill point `after_tool`; key strategy `none`. Durability `async`, and `sync` as the control. The harness waits a fixed delay between reading the subject's "entering the second model call" marker and the kill: 0 (as in the runs above), 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1 and 2 seconds. 10 trials per cell, 180 in all, in a random order fixed by seed 41.

**Measured per trial.** What `get_state()` showed the restarted process (`next`, whether a checkpoint existed, the message types), the path it took, and refunds at the sink.

**Predictions.**

- Under `sync`, `next` names the model step in every trial at every delay: the checkpoint is written before the next step starts.
- Under `async`, an empty `next` is most frequent at delay 0 and falls to none once the delay exceeds the time the background checkpoint write takes, which on a local PostgreSQL should be well under a second. At long delays `async` looks like `sync`.
- One refund in every trial: in all 60 earlier `after_tool` trials under these two modes the tool's result was already in the saved state.

**What would refute the timing explanation.** An empty `next` under `async` at 1 or 2 seconds as often as at 0; or any empty `next` under `sync`.

## Kill-delay sweep: result

Run on 17 November 2026 (UTC), as designed above, from commit `00ca7ff` with a clean tree. Raw data: [`20261117T183124Z.json`](../../results/langgraph_crash/20261117T183124Z.json). All 180 kills reached their kill point.

**Both predictions held. Under `async`, the saved state showed nothing pending in 9 of 10 trials killed at once, and in none of 80 trials killed 10 ms or more later. Under `sync` it never did (0 of 90). Every trial refunded once.**

| Durability | Delay before the kill | `next` empty at restart | 95% bound | Refunds |
|---|---|---|---|---|
| `async` | 0 | **9 of 10** | 55% to 99.7% | once in 10 of 10 |
| `async` | 10, 25, 50, 100, 250, 500 ms, 1 s, 2 s | **0 of 80** (0 of 10 at each) | at most 4.5% | once in 80 of 80 |
| `sync` | each of the nine delays | 0 of 90 | at most 4.0% | once in 90 of 90 |

In every one of the 180 trials the saved state already held the tool's result: the restarted process called the model once, did not run the tool again, and finished with an answer. The nine empty-`next` trials differed only in what `get_state()` reported before `invoke(None, config)` was called.

**What this shows.**

1. **Finding 5 is a timing window, and a short one.** Under `async`, just after the tool returns there is a window of under 10 ms on this machine in which the checkpoint holding the tool's result exists but does not yet list the model step as pending. A kill inside it leaves a state that looks finished. The 26 of 30 in the original runs, all killed with no delay, fit the 9 of 10 here.
2. **`sync` has no such window**, at any delay tested.
3. **It costs nothing to an application that resumes with `invoke(None, config)`**, which continued correctly every time. It misleads an application that reads `next` to decide whether a run is unfinished.

**What it does not show.** The window's size is a property of this machine, this checkpointer (PostgreSQL from `pgserver`, on the same host) and this graph; a slower database would widen it. The mechanism inside LangGraph was not traced: the documentation's "small risk" for `async` describes a checkpoint not being written, while what was seen is a checkpoint that was written but did not yet show its pending step. One run, ten trials per cell.

## What it means for hypothesis H1

H1 ([`evidence-check.md`](../evidence-check.md)) says LangGraph "re-executes a completed tool after a crash at K3, and the outcome under `durability="sync"` varies with scheduling". The nearest thing this experiment measured points the other way: under `sync`, a kill after the tool returned gave one refund in 30 of 30 across the two runs, with no variation.

That is not a refutation. The kill point is "entering the next model call", not K3 as the [contract](../contract.md) defines it, and H1's test design also calls for varying CPU limits, which was not done. But the first evidence gathered does not support H1. (Added after the [scientific audit](../audits/scientific-audit.md), section 4.3.)

## What was run

- **Subject:** [`subjects/langgraph_refund_agent.py`](../../subjects/langgraph_refund_agent.py). `langgraph.prebuilt.create_react_agent` with one tool, `issue_refund`, and `PostgresSaver` as the checkpointer. The tool posts to the sink over HTTP.
- **Model:** scripted, in process. Its first call returns a tool call with a newly generated ID, as hosted models do; its second returns a final message. No model API is involved.
- **Kill:** the harness hard-kills the subject's process, so no cleanup code runs.
  - *During the tool:* the sink commits the refund and withholds its response; the harness kills the process once the sink shows the refund.
  - *After the tool:* the process announces that it is entering its second model call and waits; the harness kills it there.
- **Resume:** the harness starts the subject again with the same thread ID. If the thread has a checkpoint, the subject calls `invoke(None, config)`; if it has none, it submits the original request again, as an application would after a restart.
- **Durability:** passed to `invoke(..., durability=...)`.
- **Versions:** langgraph 1.2.22, langgraph-checkpoint-postgres 3.2.1, Python 3.12.2, Windows 11. PostgreSQL from `pgserver`.

## What it does not show

- **Not a defect in LangGraph.** The documentation, read after the runs, describes each behaviour measured here, and advises idempotency keys for exactly this case. The finding is what that documented behaviour costs a tool with a side effect, in which configurations.
- **One agent shape.** A single tool call in the prebuilt ReAct agent. No parallel tool calls, subgraphs, interrupts or the functional API's `@task`.
- **A scripted model.** A real model might not call the tool again after a restart, or might call it with different arguments. Hypotheses H4 and H5 cover that and are untested.
- **The resubmit choice is the harness's.** With no checkpoint, the subject resubmits the request. An application that instead asked a human, or looked the refund up first, would behave differently.
- **A process kill on the host,** not a container kill, and only two kill points. The points do not yet map onto K1–K5 in the [contract](../contract.md).
- **Small counts.** Five trials per cell, two runs on one machine on one day. The refund counts were unanimous in every cell; the `async` state observation was 26 of 30.
- **The paper's result was not replicated.** No attempt was made to reproduce the crash-path result of arXiv 2608.03836.

## Reproduce

Create a virtual environment with `langgraph`, `langgraph-checkpoint-postgres`, `psycopg[binary,pool]` and `httpx`, then:

```powershell
python -m crashtest.experiment.langgraph_crash --local --python <langgraph env>\Scripts\python.exe --trials 5
```
