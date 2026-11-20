# LangGraph and pydantic-ai + DBOS, side by side

Experiments 004 and 005 put the same refund agent through the same two kills. This page sets their results next to each other. It adds no new data. Read the "What it does not show" sections of [004](experiments/004-langgraph-crash-resume.md) and [005](experiments/005-pydantic-ai-dbos.md) before quoting anything here.

Both subjects: one scripted model (first call returns a tool call, second returns a final message), one tool that posts a refund to the sink, a hard process kill, then a restart. Neither result is a defect; both libraries behave as designed.

## How each one comes back after a kill

| | LangGraph 1.2.22 (`PostgresSaver`) | pydantic-ai 2.68.0 + DBOS 3.5.0 |
|---|---|---|
| What is saved | A checkpoint of graph state after each super-step (or only at the end, under `exit`) | Each step's output: every model request, and the tool if it is a step or child workflow |
| Who decides to resume | The application: it reads the checkpoint and calls `invoke(None, config)`, or resubmits | DBOS: `DBOS.launch()` finds the pending workflow and re-runs it, replaying recorded steps |
| What happens to the model's first response | Replayed from the checkpoint under `sync`/`async`; **asked again** under `exit` | Always replayed from its recorded step |
| Unit of re-execution | The interrupted node (the whole tool node) | All workflow code; recorded steps return their saved result |
| In 004/005 the harness made a resume decision | Yes, with no checkpoint it resubmitted the request | No |

## Refunds per order

Without an idempotency key. "Twice" means two refunds for one order.

| Kill point | LangGraph `sync` | LangGraph `async` (default) | LangGraph `exit` | DBOS, plain function tool | DBOS, step or child-workflow tool |
|---|---|---|---|---|---|
| During the tool call | twice | twice | twice | twice | twice |
| After the tool returned | once | once on Windows; **twice on Linux** (5 of 5) | twice | **twice** | once |

The LangGraph `async` cell differs by machine: on Linux the checkpoint holding the tool's result was still unwritten in most kills, so the tool ran again (see the [004 write-up](experiments/004-langgraph-crash-resume.md), "On Linux: result"). The DBOS cells were the same on Windows and Linux.

With a key:

| Key | LangGraph | DBOS |
|---|---|---|
| Tool-call ID from the model | Held under `sync` and `async`; **failed under `exit`** (the model was asked again and issued a new ID) | Held in every cell (60 of 60 killed trials) |
| Run identity (DBOS workflow ID) | No direct equivalent tested | Held in every cell |
| Derived from the order | Held in every cell | Held in every cell |

## What the comparison shows

1. **Neither engine closes the window during the tool call.** In both, a kill after the sink committed and before the result was saved repeated the refund on every recorded trial without a key. Durable execution moves the problem; it does not remove it. Only a key the sink enforces closed it.
2. **After the tool returns, each engine is safe only in one configuration, and neither default is it.** LangGraph was safe there under `sync`; under its default, `async`, it depended on whether the checkpoint write had finished (once on Windows, twice on Linux), and `exit` always repeated. DBOS was safe there only if the tool was a step or child workflow; a plain function tool, which is what pydantic-ai gives you unless you decorate it, was not. *(Corrected 18 November: this item first said LangGraph's `async` was safe after the tool returned, from the Windows runs alone.)*
3. **The tool-call ID is a stable key in DBOS and conditionally stable in LangGraph**, because DBOS always records the model response before the tool runs, and LangGraph under `exit` does not.
4. **DBOS removes the application's resume decision.** In LangGraph the application chose to call `invoke(None)` or resubmit; in DBOS the engine recovered the workflow without the application deciding anything. That is less code to get wrong, and also less control: DBOS will re-run a plain tool whether or not the application would have.
5. **A key derived from the business operation was the only choice that held everywhere**, in both engines, in every mode and at both kill points.

## Caveats on the comparison itself

- Different libraries, different versions, run on different days with different trial counts (004: 5 per cell, two runs; 005: 10 per cell, see its write-up for the run count).
- One agent shape, one tool, a scripted model. No parallel tool calls, MCP tools or real models.
- Both on Windows, host process kills, PostgreSQL from `pgserver`.
- Both libraries' documentation has been read: for 005 before the runs, for 004 afterwards (17 November). Each describes the behaviour measured; neither result is a defect.
