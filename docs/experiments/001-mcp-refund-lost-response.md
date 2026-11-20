# Experiment 001 — MCP refund, lost response, retry

Run three times on 16 November 2026 (UTC timestamps in the file names). The table below is the first run; all three are listed under "Runs". Raw data is in [`results/mcp_refund/`](../../results/mcp_refund/).

## Result

**With a naive retry, a lost response produced two refunds in 20 of 20 trials. A stable idempotency key enforced by the database brought that to one refund in 20 of 20. A key regenerated on each attempt was no better than no key: two refunds in 20 of 20.**

| Fault | Client strategy | Trials | Correct | Duplicate | Lost | Refunds committed | Requests received by sink | Client saw success |
|---|---|---|---|---|---|---|---|---|
| none | `no_dedup` | 20 | 20 | 0 | 0 | 20 | 20 | 20 |
| none | `stable_key` | 20 | 20 | 0 | 0 | 20 | 20 | 20 |
| none | `fresh_key_per_attempt` | 20 | 20 | 0 | 0 | 20 | 20 | 20 |
| lost response | `no_dedup` | 20 | 0 | **20** | 0 | 40 | 40 | 20 |
| lost response | `stable_key` | 20 | **20** | 0 | 0 | 20 | 40 | 20 |
| lost response | `fresh_key_per_attempt` | 20 | 0 | **20** | 0 | 40 | 40 | 20 |

Every lost-response trial followed the same pattern: attempt 1 timed out, attempt 2 succeeded.

## Runs

| Results file | Client timeout / fault delay | Lost-response cells | No-fault cells |
|---|---|---|---|
| `20261116T040750Z.json` | 0.5 s / 2.0 s | As in the table above | 60 of 60 correct |
| `20261116T042024Z.json` | 0.5 s / 2.0 s | Identical to the table above | 59 of 60 correct; **one unplanned triple refund** |
| `20261116T042252Z.json` | 2.0 s / 5.0 s | Identical to the table above | 60 of 60 correct |
| `20261117T164731Z.json` (re-run) | 0.5 s / 2.0 s | Identical to the table above | 60 of 60 correct |

### Re-run on the current harness, 17 November 2026

The [scientific audit](../audits/scientific-audit.md) (section 2.4, action 1) asked for 001 to be run again after the harness changed: the shared checker, the fault record, the proxy in the path, and provenance in the results file. The re-run used the 0.5 s / 2.0 s configuration of the first two runs, from commit `1b50c5d` with a clean tree.

**Every cell matched.** No key and a new key per attempt refunded twice in 20 of 20 lost-response trials each; a stable key refunded once in 20 of 20; all 60 no-fault trials were `correct`. The sink's log confirmed the withheld response in all 60 lost-response trials, the proxy counted two tool calls per faulted trial, and no order's count moved by the end of the run. The unplanned triple refund of the second run did not recur. The 2.0 s / 5.0 s configuration was re-run as part of experiment 002's re-run, which covers it.

Under contract v0.2 none of these verdicts changes ([review](../audits/contract-v0.2-review.md)). The first run's refunds are no longer in the sink database, so its payloads cannot be re-checked; the later runs, which can be, agree with it.

### Timeout-ratio sweep: design, written before the run

Scientific-audit action 6 (section 2.3): the runs above held the response 2.5 to 4 times longer than the client's timeout, which makes the fault certain. This sweep asks where the outcome changes as that ratio moves through 1.

**Conditions.** The `stateless_json` transport, the lost-response fault, all three client strategies, 20 trials per cell, client timeout 2.0 s, up to 3 attempts. The sink withholds its response for 1.0, 1.8, 2.2, 4.0 or 8.0 s: 0.5, 0.9, 1.1, 2 and 4 times the timeout. One run per delay on a GitHub Ubuntu machine through the experiment workflow, 300 trials in all.

**Predictions.**

- **Below the timeout (0.5× and 0.9×):** the first attempt receives its response, nothing is retried, and every strategy refunds once. At 0.9× a few trials may still time out from processing overhead; at least 18 of 20 per strategy refund once.
- **Above the timeout (1.1×, 2× and 4×):** the first attempt times out and is retried. No key and a new key per attempt refund twice in 20 of 20; a stable key refunds once in 20 of 20, as in the runs above.

**What would refute it.** Any duplicate below the timeout, or a strategy without a stable key refunding once above it.

### Timeout-ratio sweep: result

Run on 17 November 2026 (UTC) on GitHub-hosted Ubuntu machines through the experiment workflow, all five from commit `20d3fdc` with a clean tree (runs 44186528768, 44186542234, 44186555505, 44186568065 and 44186584464). Raw data, one file and log per delay, in [`results/mcp_refund_linux/`](../../results/mcp_refund_linux/).

**The prediction held in every cell, and the boundary was sharper than predicted: not one trial at 0.9× timed out.**

| Delay | Ratio to timeout | Results file | `no_dedup` | `stable_key` | `fresh_key_per_attempt` | Attempts per trial |
|---|---|---|---|---|---|---|
| 1.0 s | 0.5× | `20261117T220554Z.json` | 20 once | 20 once | 20 once | 1 |
| 1.8 s | 0.9× | `20261117T220552Z.json` | 20 once | 20 once | 20 once | 1 |
| 2.2 s | 1.1× | `20261117T220559Z.json` | **20 twice** | 20 once | **20 twice** | 2 |
| 4.0 s | 2× | `20261117T220605Z.json` | **20 twice** | 20 once | **20 twice** | 2 |
| 8.0 s | 4× | `20261117T220609Z.json` | **20 twice** | 20 once | **20 twice** | 2 |

"Once" is verdict `correct`, "twice" is `duplicate`. In all 300 trials the sink's log confirmed the withheld response, the proxy's count of tool calls equalled the sink's count of requests, the client reported success, and no order's count moved by the end of the run. Above the timeout every trial took exactly two attempts: the first timed out and the second was answered in time.

What this adds to the runs above: the outcome depends on whether the response arrives before the client gives up, not on how late it is. Holding the response 1.1 times the timeout gives the same duplicate as holding it 4 times; holding it 0.9 times gives none. Within this range nothing in between was seen, so the earlier runs' large ratios did not create the effect. Not tested: ratios between 0.9 and 1.1, where timing jitter would decide individual trials, and a loaded machine, where the 0.5 s run above produced an unplanned duplicate without any fault.

### The unplanned triple refund

In the second run, one `fresh_key_per_attempt` trial with **no fault injected** refunded the order three times. Two ordinary calls exceeded the 0.5 s client timeout on a busy machine (the median successful call took about 0.3 s), each was retried with a new key, and all three committed.

Two consequences:

- **It is a real instance of the failure, without injection.** A tight timeout plus a retry is enough; no fault is needed.
- **It was a flaw in the harness.** A control cell must not depend on machine load. The default timeout is now 2.0 s and the fault delay 5.0 s. The run is kept in the results folder, not discarded.

The lost-response cells were identical in all three runs.

## What was run

The description below is for the first run; the third differs only in the timeout and delay.

- **Operation:** "refund this order once", via the MCP tool `issue_refund`.
- **Path:** MCP client → MCP server (streamable HTTP, stateless) → sink (FastAPI) → PostgreSQL 16.2.
- **Fault:** the sink commits the refund, then holds its response for 2.0 s. The client's timeout is 0.5 s, so the client never sees the first response.
- **Retry policy:** on a timeout, call again, up to 3 attempts. Each attempt uses a new connection.
- **Client strategies:**
  - `no_dedup` — no key.
  - `stable_key` — one key chosen per operation and reused on every attempt; the sink enforces it with a unique index and replays the stored refund.
  - `fresh_key_per_attempt` — a new key on each attempt.
- **Verdict:** the number of refund rows in the sink for the order. One is correct.
- **Versions:** Python 3.12.2, mcp 2.3.0, FastAPI 0.163.1, psycopg 3.3.9, Windows 11.
- **How it ran:** locally, with PostgreSQL from the `pgserver` package and the services as subprocesses. **Not run in Docker** — Docker Desktop would not start on the test machine because drive C: was full.

## What it shows

1. **The client's view is wrong in the duplicate cases.** In all 40 duplicate trials the client recorded one successful refund. Only the sink shows two.
2. **Deduplication worked because the database enforced it.** With `stable_key` the sink received 40 requests and committed 20 refunds; the other 20 were replays of the stored result.
3. **A key only helps if it is the same key.** `fresh_key_per_attempt` is the mechanical version of a caller, or a model, that regenerates the request instead of repeating it. How often real models do that is unmeasured (hypotheses H4 and H5 in [evidence-check.md](../evidence-check.md)).
4. **The MCP Python client did not resend on its own.** In this configuration each timed-out attempt produced exactly one request at the sink, so every duplicate came from the application's retry loop, not from the SDK.

## What it does not show

- **Nothing about any agent framework.** There is no LangGraph, CrewAI or pydantic-ai here, and no model. The retry loop is hand-written.
- **Nothing about prevalence.** The fault was injected in every faulted trial. The faulted cells gave the same outcome in all 60 trials per cell across three runs, so "20 of 20" is a repeatability check, not a sample from a distribution.
- **Only one fault shape.** The response is delayed, not dropped at the socket, and no process is killed.
- **Only one transport setting.** Stateless streamable HTTP with JSON responses. Stateful sessions, SSE streaming and stdio are untested.
- **The outcome was expected.** This experiment validates the harness with a positive and a negative control. It is not a new finding about MCP.

## Reproduce

```powershell
python -m crashtest.experiment.run --local --trials 20
```

See the [README](../../README.md) for setup.
