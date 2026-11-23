# M2 plan — cut the connection before the request and mid-stream (experiment 008)

Written 21 November 2026, before any trial of experiment 008 was run. **Status, 21 November 2026: done.** Run once on Windows and once on Linux from commit `9c9cf01` (private research history; see [`PROVENANCE.md`](../PROVENANCE.md)); every prediction held on both: [`experiments/008-mcp-cut-points.md`](experiments/008-mcp-cut-points.md). Section 4 is the pre-registration: do not change it after a run has been seen; record deviations in the write-up, `docs/experiments/008-mcp-cut-points.md`.

No paid API call is involved: the subject is the reference `mcp` Python client 2.3.0 with the hand-written retry loop of experiments 001, 002 and 007.

## 1. The question

Experiments 002 and 007 cut the client's connection only after the refund had committed. The roadmap's last M2 item asks for the other two places a connection can break: **before the request reaches the server** (nothing committed) and **mid-stream** (some response bytes have arrived, the result has not). The questions:

1. Before the request: can the client tell this failure from a cut after the commit? If not, the same retry rule must be safe in both.
2. Mid-stream, before the commit: when the connection breaks while the tool is still working, does the effect still happen (so a naive retry refunds twice), is the tool cancelled (so a retry refunds once), or does the client resume and receive the result?

## 2. What the library says (read before designing)

From the installed source of `mcp` 2.3.0 (`.venv/Lib/site-packages/mcp/`), and experiment 007's reading, which still holds (`Client(url)` negotiates 2026-07-28 by default; `mode="legacy"` gives 2025-11-25; only the latter has sessions, event stores and resumption):

- **2026-07-28 (`_streamable_http_modern.py`).** Each request is a single POST. With `json_response=False` the handler runs beside a `watch_disconnect` task; the response switches to SSE when the handler emits a notification. When the client's connection closes, `watch_disconnect` cancels the handler's task group. The module docstring says so: "cancellation is closing the response stream".
- **2025-11-25, session (`streamable_http.py`, `streamable_http_manager.py`, `shared/jsonrpc_dispatcher.py`).** The request is handed to the session's server task, which does not stop when the POST's SSE response is cut ("Still processing message as the client might reconnect and replay"). The client sends `DELETE` to end its session when it leaves `Client(...)` (`terminate_on_close=True`, `client/streamable_http.py`). The session then terminates, its read stream closes, and the dispatcher's loop ends with `tg.cancel_scope.cancel()`: "Cancel in-flight handlers".
- **Client resumption (as in 007).** Only on 2025-11-25, only after the client has seen an SSE event ID, and only if the server has an event store. On reconnection the server replays stored events after `Last-Event-ID`, then registers the stream so later messages go to it live (`_replay_events`).
- **Progress.** A tool's `ctx.report_progress` sends `notifications/progress` on the request's stream only if the client's request carries a progress token, which `call_tool(..., progress_callback=...)` provides. Request-scoped log messages are opt-in per request on 2026-07-28, so progress is used, not logging.

**Design probe, 21 November** (one call per new cell shape, eight calls; refund counts and verdicts not read; not a result): both new cut points landed as intended on both servers and in both modes: `before_request` withheld the call with nothing at the sink; `mid_stream` forwarded exactly one progress notification, which the client delivered to its callback, with no refund at the sink at the cut. The first attempt raised `RemoteProtocolError` after `before_request` and `MCPError` −32000 after `mid_stream`, except under `legacy` with the event store, where the first attempt ended `ok`. The server logged `ClientDisconnect` while reading the body after `before_request` under `legacy`: it had the headers, never the body.

## 3. Design

**The server.** Experiment 007's two stateful SSE servers again (`stateless_http=False`, `json_response=False`, without and with the in-memory event store), with one change to the tool, made only when `MCP_PROGRESS_DELAY_S` is set (unset for every earlier experiment, whose tool is unchanged; its input schema is identical): the tool first reports progress ("refund started") and then waits **3.0 s** before it calls the sink. The pause puts the mid-stream cut clearly before the commit, and makes the predictions below independent of how fast the client's `DELETE` or reconnection is: 007 measured reconnections of up to 2.4 s on Windows. Served as `stateful_sse_progress` and `stateful_sse_store_progress`.

**The client.** Every call passes a progress callback, which counts the progress notifications delivered per attempt (`progress_seen`).

| Factor | Levels |
|---|---|
| Client protocol | `auto` (default; 2026-07-28) · `legacy` (2025-11-25) |
| Server | `stateful_sse_progress` (no store) · `stateful_sse_store_progress` (store) |
| Cut point | `none` · `no_bytes` (002's: after the commit, nothing reaches the client; the negative control) · `before_request` (new) · `mid_stream` (new) |
| Client strategy | `no_dedup`, `stable_key`, `fresh_key_per_attempt` |

The new cut points (`src/crashtest/faults/drop_proxy.py`):

- **`before_request`.** When the client's bytes carrying the call (the order ID as a JSON string) reach the proxy, they are not forwarded; the proxy reads the sink and aborts both connections. Earlier bytes of the same request (its headers) may have been forwarded; the server never gets a complete request.
- **`mid_stream`.** The response headers and complete SSE events pass, up to and including the first JSON-RPC notification; the proxy then reads the sink and aborts both connections, with no wait for a commit. If a response (result or error) were to arrive first, it would be held back and the cut would not count as landed.

2 × 2 × 4 × 3 = 48 cells, **10 trials per cell**, 480 trials per machine, run in a fixed order (mode, server, cut point, strategy). Ten, not 007's twenty, because the pause makes every call take more than 3 s; each machine runs it once, so each cell has 20 trials in all. Retry loop as in 002 and 007: retry on a timeout or a disconnect, up to 3 attempts, each on a new client. Client timeout **10.0 s**, so that a 3 s tool plus a reconnection fits inside it; a timeout is not the fault under test.

**`fault_confirmed`** (did the cut land where intended), from the proxy and the sink, per trial (`DropRecord.landed_as_intended`):

- `no_bytes`: the sink held the refund when the connection was cut (as in 002 and 007).
- `before_request`: the call's bytes were withheld, and the sink held no request and no refund for the order at the cut.
- `mid_stream`: at least one notification was forwarded, no response was, and the sink held no refund at the cut.

## 4. Pre-registered predictions

| | Cells | Prediction |
|---|---|---|
| P1 | `none`, every cell | `correct` in 10 of 10; one progress notification delivered; no `Last-Event-ID` request. |
| P2 | `no_bytes`, every mode and server | As 007's P1–P3: first attempt `disconnect`; no `Last-Event-ID`; `no_dedup` and `fresh_key_per_attempt` refund twice in 10 of 10; `stable_key` refunds once in 10 of 10. |
| P3 | `before_request`, every mode and server | First attempt `disconnect`, with the same exception description (`raised`) as the `no_bytes` cells of the same mode and server, so the client cannot tell the two apart. The first attempt never reaches the sink: requests at the sink minus attempts is −1 in every trial. The retry refunds once: `correct` in 10 of 10 for all three strategies. The proxy sees as many calls as there were attempts (nothing below the application resends). |
| P4 | `mid_stream`, `auto`, either server | The server cancels the tool when the stream closes, during its pause: the first attempt never reaches the sink (−1 in every trial), the first attempt is `disconnect` after one progress notification, no `Last-Event-ID`, the event store makes no difference; the retry refunds once: `correct` in 10 of 10 for all three strategies. |
| P5 | `mid_stream`, `legacy`, no store | No event ID, so no resumption: the first attempt fails at once (`disconnect`, −32000), the client ends its session, and ending the session cancels the tool before its sink call: −1 in every trial; `correct` in 10 of 10 for all three strategies. |
| P6 | `mid_stream`, `legacy`, store | The client resumes: one or more `Last-Event-ID` requests per trial; the session and the tool carry on, and the result reaches the client on the resumed stream; the first attempt ends `ok`, after at least 3 s; one request at the sink (0 in every trial); `correct` in 10 of 10 for all three strategies. |
| P7 | all | Requests at the sink never exceed the application's attempts, and the calls the proxy sees equal the attempts, in every trial. |
| P8 | all | `report_matches_sink` is false exactly in the duplicate trials. |

**Controls** (contract section 7). Positive: `stable_key` gives no duplicate in any faulted cell. Negative: `no_dedup` refunds twice in every `no_bytes` trial. A check on the fault: `fault_confirmed` true in every faulted trial. If a control or the fault check fails, the harness is wrong and the run is not reported as a result.

**What refutes each prediction.** P1: a no-fault trial that is not `correct`, or with no progress delivered. P2: any `no_bytes` trial with a resumption or a first attempt that ends `ok`, or a `no_dedup`/`fresh_key_per_attempt` trial with one refund. P3: a `before_request` trial with a request delta other than −1, any duplicate, or a first-attempt `raised` that differs from the `no_bytes` cells' of the same mode and server. P4 and P5: any trial with a request delta of 0 (the cancelled tool committed anyway) or a duplicate; a split is reported with the timings. P6: any trial that did not resume, whose first attempt did not end `ok`, or with two refunds. P7 and P8: any exception.

## 5. Measures per trial

As in 007 (`crashtest.experiment.trial.TrialResult`), with verdicts only from `crashtest.checker.classify` and `report_matches_sink` from `crashtest.checker.report_matches_sink`. Added: `progress_seen` per attempt; per drop, `request_withheld`, `notifications_forwarded`, `response_forwarded`, and the sink's request and refund counts at the cut. The summary reports `request_delta` (sink requests minus attempts) per trial.

## 6. Where it runs

Locally on Windows and once on Linux through `.github/workflows/experiment.yml` (`008-mcp-cut-points`), both from a committed tree. Results go to `results/mcp_cut_points/` and `results/mcp_cut_points_linux/`.

## 7. What this does not cover

- One client (`mcp` 2.3.0), one server implementation, one tool shape (progress, then a 3 s pause, then the effect). A cut **during** the sink call, while the request to the sink is in flight, is a race this design avoids on purpose and does not measure.
- The proxy aborts both sides (a TCP reset), so the server learns of the cut at once. A silent network failure, where the server's side stays open until a timeout, is not tested.
- `before_request` withholds the request's body; a cut in the middle of the client's body, or one before any byte of the request, is not separately tested.
- No mid-stream cut after the commit with several events; 007's `after_headers` remains the only after-commit mid-stream point.
