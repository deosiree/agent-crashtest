# Experiment 008 — a connection cut before the request, and mid-stream

Run on 21 November 2026 (20 November, 22:31 UTC), from commit `9c9cf01` (clean tree; a commit of the private research history, see [`PROVENANCE.md`](../../PROVENANCE.md)), once on Windows and once on Linux (a GitHub Actions run of the research repository), at the same time. Pre-registered design: [`docs/m2-cut-points-plan.md`](../m2-cut-points-plan.md). Raw data: [`results/mcp_cut_points/20261120T223100Z.json`](../../results/mcp_cut_points/20261120T223100Z.json) (Windows 11, local stack) and [`results/mcp_cut_points_linux/20261120T223110Z.json`](../../results/mcp_cut_points_linux/20261120T223110Z.json) (Ubuntu runner; log beside it). Both took about 35 minutes.

**Note, 23 November 2026: the fault proxy changed after these runs.** They used the proxy as it was then, which created a new HTTP client to the sink for each commit wait. The code published here, and the Reproduce command below, use the later proxy: one client for the proxy's life, a 1 s idle expiry, and one retry on a dropped connection ([`CHANGELOG.md`](../../CHANGELOG.md)). Two re-runs on the maintainer's development tree, one after each of the proxy's two changes, matched every one of the 48 cells at 5 trials per cell; they are not published ([`PROVENANCE.md`](../../PROVENANCE.md)). These two runs of record have no `sink_retries` field: it did not exist yet.

**Every cell matched the pre-registered predictions, on both machines: 48 cells, 480 trials each, with no prediction, control or fault check violated. The two machines agreed in every cell on verdicts, refund counts, first-attempt outcome and exception, progress events, resumptions and request counts; they differed only in timing.**

## Result

**A cut before the request reached the server, and a cut mid-stream while the tool was still working, both left the order with no refund from the first attempt, so even a naive retry refunded exactly once: 120 of 120 trials per cut point per machine. In the before-request case, the client raised the same exception as after a cut that came after the commit, where the same naive retry refunded twice. From the error alone, the caller could not tell the two cases apart.** Mid-stream, nothing was refunded twice because of how the server reacted. On protocol 2026-07-28 it cancelled the tool when the stream closed. On 2025-11-25 without an event store, the client's session ended and that cancelled the tool. On 2025-11-25 with an event store, the client resumed the stream and received the original result.

10 trials per cell per machine; each row holds for every cell in it, on both machines. "Request delta" is requests at the sink minus the application's attempts, per trial: −1 means one attempt never reached the sink.

| Cut point | Client protocol | Server | Strategy | Refunded once | Twice | First attempt (exception) | Request delta | Resumed |
|---|---|---|---|---|---|---|---|---|
| none | any | any | any of three | 10 | 0 | `ok` | 0 | 0 |
| `no_bytes` (after the commit) | any | any | `no_dedup`, `fresh_key_per_attempt` | 0 | **10** | disconnect (`RemoteProtocolError`) | 0 | 0 |
| `no_bytes` (after the commit) | any | any | `stable_key` | **10** | 0 | disconnect (`RemoteProtocolError`) | 0 | 0 |
| `before_request` | any | any | any of three | **10** | 0 | disconnect (`RemoteProtocolError`) | **−1** | 0 |
| `mid_stream` | `auto` (2026-07-28) | with or without store | any of three | **10** | 0 | disconnect (`MCPError` −32000) | **−1** | 0 |
| `mid_stream` | `legacy` (2025-11-25) | no store | any of three | **10** | 0 | disconnect (`MCPError` −32000) | **−1** | 0 |
| `mid_stream` | `legacy` (2025-11-25) | with store | any of three | **10** | 0 | `ok`, after 3.04 s to 3.25 s | 0 | **10** |

The exception is the innermost type in the group the client raised (`raised` in the results files: `group(RemoteProtocolError)` and `group(group(MCPError[-32000]))`). Per-cell rows are under `summary` in each file.

## What was run

- **Operation and path:** as in [001](001-mcp-refund-lost-response.md), [002](002-mcp-client-matrix.md) and [007](007-mcp-resume.md): "refund this order once" through the MCP tool `issue_refund`, client → TCP proxy → MCP server → sink → PostgreSQL. The application's retry loop is the same: retry on a timeout or a disconnect, up to 3 attempts, each on a new client.
- **Server.** 007's two stateful SSE servers (`stateless_http=False`, `json_response=False`, without and with the in-memory event store), with one change to the tool, made only for this experiment through `MCP_PROGRESS_DELAY_S`. The tool first reports progress ("refund started"), then waits **3.0 s**, then calls the sink. The tool's input schema is unchanged, and so is its behaviour in every earlier experiment, where the variable is unset. Served as `stateful_sse_progress` and `stateful_sse_store_progress`.
- **Client.** `Client(url)` (`auto`, which negotiated 2026-07-28 in every attempt) or `Client(url, mode="legacy")` (2025-11-25 in every attempt), calling with a progress callback so that the server's progress notification goes onto the stream. The client timeout was 10.0 s.
- **Cut points** ([`src/crashtest/faults/drop_proxy.py`](../../src/crashtest/faults/drop_proxy.py)):
  - `no_bytes` (002's): nothing from the server reaches the client; the proxy waits until the sink holds the refund, then aborts both connections. This is the negative control.
  - `before_request` (new): the client's bytes carrying the call are never forwarded. The proxy reads the sink and aborts both connections at once. How much of the request the server got depends on how the client's bytes arrived at the proxy: on Windows, the server logged a `ClientDisconnect` while reading the body in 60 trials (the `legacy` `before_request` trials: the headers had passed in an earlier read); the Linux log has none. Not recorded per trial.
  - `mid_stream` (new): the response headers and complete SSE events pass, up to and including the first JSON-RPC notification (the progress event). Then the proxy reads the sink and aborts both connections, without waiting for a commit.
- **Versions:** `mcp` 2.3.0 (client and server), Python 3.12.2 on Windows 11 and 3.12.16 on Linux; the other versions are in each file's `environment`.

## Checks on the fault and the harness

- **Every cut landed where intended** (`fault_confirmed`, from the proxy and the sink): true in all 360 faulted trials on each machine. `no_bytes`: the sink held the refund at the cut (120 of 120). `before_request`: the call was withheld, and the sink held no request and no refund for the order at the cut (120 of 120). `mid_stream`: at least one notification reached the client, the response did not, and the sink held no refund at the cut (120 of 120); beyond what the check requires, exactly one notification passed and the sink held no request either, in every one. The client delivered that one progress event to its callback in every `mid_stream` first attempt. Under `legacy` with the store, two SSE event IDs reached the client before the cut (the priming event and the progress event); in every other cut, none did.
- **Nothing below the application resent.** The proxy saw exactly as many calls as the application made attempts, and the sink never saw more requests than attempts, in all 480 trials per machine. A `before_request` cut, which a transport could safely resend, was not resent by the client or its HTTP stack.
- **Controls.** `stable_key` gave no duplicate in any faulted cell (positive). `no_dedup` refunded twice in all 40 `no_bytes` trials per machine (negative).
- **What the caller was told agreed with the sink except in the duplicates** (`report_matches_sink`, contract v0.3). It was false in exactly the 80 duplicate trials per machine, the `no_bytes` cells without a stable key, where the client reported one successful refund and the sink held two.
- **No count moved** after a trial's first read, or by the end of the run.
- **Re-judged afterwards.** `crashtest.results.reverdict` re-judged all 480 Windows verdicts against the local sink's rows: none changed. Both files load into the result store, whose cell counts match the table. The Linux sink's database went with the runner, so its verdicts rest on the run's own reads.
- **Pre-run checks.** A design probe (one call in each of eight cell shapes) and the test suite ([`tests/test_cut_points.py`](../../tests/test_cut_points.py), one trial in each of four cell shapes) ran before the run of record; neither is counted. The probe is described in the plan, section 2. It showed where each cut landed and which exception the first attempt raised, and the plan says what it showed.

## What the library documents

- **2026-07-28.** The source module for the single-exchange HTTP path (`mcp/server/_streamable_http_modern.py`) says that on this protocol a client cancels a request by closing the response stream. In SSE mode the handler runs beside a task that cancels it when the client disconnects. The 2026-07-28 specification has no resumable streams (007).
- **2025-11-25.** A request in a session keeps running when its SSE response is cut, "as the client might reconnect and replay" (`mcp/server/streamable_http.py`). The client sends `DELETE` to end the session when it leaves `Client(...)` (`terminate_on_close=True`). A terminated session closes its read stream, and the dispatcher then cancels in-flight handlers (`mcp/shared/jsonrpc_dispatcher.py`: "Cancel in-flight handlers"). Resumption is as described in 007.
- None of this is on the SDK's documentation site, as far as was read for 007; it is in the source and the specifications.

So every behaviour measured here follows from the source or the specification: nothing in this experiment is reported as a defect.

## What it shows

1. **A cut before the request leaves nothing to undo, and the client cannot tell it from a cut after the commit by the error.** In all 120 `before_request` trials per machine the first attempt never reached the sink (request delta −1), and the retry made the only refund: `correct` for all three strategies, `no_dedup` included. The first attempt raised `RemoteProtocolError` in every `before_request` trial and in every `no_bytes` trial, where the refund had already committed and a naive retry refunded twice (40 of 40). A rule like "retry a dropped connection" or "never retry it" is therefore wrong in one of the two cases; a stable key enforced by the sink was right in both (one refund in 40 of 40 `stable_key` trials per machine at each of the two cut points).
2. **The time to failure did differ here, but only because of this tool.** The first attempt failed after 0.02 s to 0.05 s on Linux and 0.17 s to 0.82 s on Windows with `before_request`, against 3.02 s to 3.92 s with `no_bytes`, because the `no_bytes` cut waits for the commit, which came after the tool's 3 s pause. That gap comes from the design, not from a signal the client can rely on: in 002, with no pause, a cut after the commit failed in a median 0.375 s.
3. **A cut mid-stream, before the commit, did not lead to a duplicate in any configuration tested, and the reason depended on the protocol.**
   - On 2026-07-28 (the default), the server cancelled the tool when the stream closed. The first attempt never reached the sink (request delta −1 in 60 of 60 trials per machine), and the event store made no difference.
   - On 2025-11-25 without a store, the client had no event ID, failed at once and ended its session, and the tool was cancelled during its pause: request delta −1 in 30 of 30 per machine. The cancellation is inferred from the request log, not observed directly in the server: the tool's sink call never arrived. The source says the session's termination causes it.
   - On 2025-11-25 with a store, the client resumed with one `Last-Event-ID` request per trial, the tool finished, and the result reached the client on the resumed stream. The first attempt ended `ok` in 30 of 30 trials per machine, one request reached the sink, and there was one refund.
4. **Resumption needs an event ID before the cut, as in 007.** With the store, a cut before any byte (`no_bytes`) gave no resumption, and `no_dedup` refunded twice (20 of 20 per machine, both client modes). A cut after the progress event gave a resumption every time.
5. **The resumed call waited for the work, not for the reconnection.** The resumed first attempts took 3.04 s to 3.07 s on Linux and 3.18 s to 3.25 s on Windows, about the same as a call with no fault (cell medians 3.03 s to 3.04 s on Linux, 3.22 s to 3.70 s on Windows). The client reconnected after its default 1 s and received the result live when the tool finished, about 3 s after the call began.

## What it does not show

- **A cut while the tool is talking to the sink.** The tool's 3 s pause puts the mid-stream cut before the sink call on purpose. A cut that lands while the sink request is in flight, or after the sink has committed but before the tool returns, races the cancellation against the commit. It was not run, and there a cancelled tool could still leave a refund behind. The "no duplicate" mid-stream result belongs to this tool shape (progress, pause, then the effect), not to every tool.
- **A real network failure.** The proxy aborts both sides at once, so the server learns of the cut immediately and can cancel. Where the server's side stays open (a silent drop, a proxy that keeps the upstream open), the 2026-07-28 cancellation would come late or not at all, and the effect could commit. Not tested.
- **The cancellation directly.** The server does not log tool cancellations. That the tool was cancelled is inferred from the sink: its call never arrived within the trial and the settle reads after it. The cause on 2025-11-25 (the client's `DELETE`) is read from the source, not isolated: no cell kept the session open.
- **Other cut positions.** `before_request` withheld the read that carried the order ID; whether the headers had already passed depended on how the bytes arrived (see "What was run"), and this was not controlled. A cut before any byte, or partway through the body, was not run as its own cell. `mid_stream` cut after the first of two possible events; a longer stream was not tried.
- **Other clients and servers.** Only the reference `mcp` Python client and server 2.3.0. CrewAI (`mcp` 1.30.0) and agent-framework were not run.
- **Contract wording.** Contract section 3 defined `fault_confirmed` as the fault having fired after the commit; here it means "the cut landed where its cut point says", which for the two new points is *before* the commit. The contract's wording was updated on 21 November 2026 to say this (contract version 0.4), with no change to any verdict or earlier value; section 4 lists the two cuts.
- **Repeatability, not rates.** Each cell gave the same outcome in 10 of 10 trials on each of two machines, run in a fixed order, once each.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m crashtest.experiment.cut_points --local --trials 10
```

On Linux, through the experiment workflow:

```powershell
gh workflow run experiment.yml -f experiment=008-mcp-cut-points
```
