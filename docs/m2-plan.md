# M2 plan — does the MCP client resume after a dropped connection? (experiment 007)

Written 20 November 2026, before any trial of experiment 007 was run. **Status, 20 November 2026: done.** Run once on Windows and once on Linux from commit `9f10c84`; every prediction held on both. Results and the one harness bug found before the run: [`experiments/007-mcp-resume.md`](experiments/007-mcp-resume.md). Section 4 is the pre-registration: do not change it after a run has been seen; record deviations in the write-up, [`experiments/007-mcp-resume.md`](experiments/007-mcp-resume.md).

No paid API call is involved: the subject is the reference `mcp` Python client 2.3.0 with the hand-written retry loop of experiments 001 and 002.

## 1. The question

Experiment 002 cut the client's connection after the refund committed and found that the reference client never resent the call by itself. It noted that its stateful SSE server had no event store, so the client had nothing to resume from. This experiment adds the event store and asks: **after the connection drops, does the client resume the stream and receive the original result, or does the call fail, so that the application's retry refunds again?**

## 2. What the library and the specification say (read before designing)

From the installed source of `mcp` 2.3.0 (`.venv/Lib/site-packages/mcp/`) and its documentation site:

- **Server.** `MCPServer.run("streamable-http", ..., event_store=..., retry_interval=...)` passes an `EventStore` to the streamable HTTP transport. The documentation site lists `event_store` and `retry_interval` as the "resumability" options on its Run page, without defining an event store. `EventStore` is an abstract class (`mcp/server/streamable_http.py`, `store_event` and `replay_events_after`); the package ships no implementation. With a store, the server stores every message before sending it, and opens each SSE response with a "priming" event: an event ID with empty data (`_mint_priming_event`). It sends the priming event only when the negotiated protocol version is 2025-11-25 or later. In stateless mode the event store is not used (`streamable_http_manager.py`, `event_store=None # No event store in stateless mode`). A GET carrying `Last-Event-ID` makes the server replay the stored events of that stream after that ID (`_replay_events`).
- **Client.** In `mcp/client/streamable_http.py`, `_handle_sse_response` remembers the last event ID it has seen on a POST's SSE stream. If the stream ends without a response and an ID was seen, it waits for the server's `retry` value or 1000 ms by default and reconnects with a GET carrying `Last-Event-ID` (`_handle_reconnection`), up to 2 attempts. If no ID was seen, it ends the request with error −32000, "SSE stream ended without a response". The documentation site does not say that the client resumes automatically.
- **Protocol version.** `Client(url)` defaults to `mode="auto"`: it sends `server/discover` and adopts protocol 2026-07-28 when the server offers it (`mcp/client/_probe.py`); `mode="legacy"` uses the `initialize` handshake (2025-11-25). The server routes any request whose `MCP-Protocol-Version` header is not a handshake-era version to `handle_modern_request` (`streamable_http_manager.py`), a single-exchange path with no session and no event store. The 2026-07-28 specification removes resumable streams ("Resumable SSE streams via `Last-Event-ID` are not supported"); the 2025-11-25 specification describes the priming event and the `Last-Event-ID` reconnection.

**Checked by a probe on 20 November** (no refunds counted, not a result): against the four servers of experiment 002, `Client(url)` negotiated 2026-07-28 on all four, and every exchange on the wire was a POST answered with `application/json`, with no `mcp-session-id` header. Only `mode="legacy"` gave sessions and SSE responses. So the four transports of experiment 002 were, on the wire, one transport. This does not change any verdict of 002; it changes what 002 can be said to have compared. The write-up of 007 reports it, and 002 gets a dated note.

## 3. Factors

| Factor | Levels |
|---|---|
| Client protocol | `auto`: `Client(url)`, the default, which negotiates 2026-07-28 here · `legacy`: `Client(url, mode="legacy")`, which negotiates 2025-11-25 |
| Server | `stateful_sse`: session kept, SSE responses, no event store (as in 002) · `stateful_sse_store`: the same with an in-memory event store; `retry_interval` not set, so the client uses its default 1000 ms delay |
| Fault | `none` · `connection_drop` at drop point `no_bytes`: nothing the server sends on the connection carrying the call reaches the client (002's proxy) · `connection_drop` at drop point `after_headers` (new): the response headers and every complete SSE event that carries no JSON-RPC message (the priming event, when there is one) reach the client; from the first JSON-RPC message on, nothing does |
| Client strategy | `no_dedup`, `stable_key`, `fresh_key_per_attempt`, as in 001 and 002 |

In both drop points the proxy cuts the connection only after the sink reports the refund committed; with `after_headers` it cuts when the server's JSON-RPC response arrives, which the server sends, and stores, only after the sink has answered. The retry loop is 002's: retry on a timeout or a disconnect, up to 3 attempts, each on a new client.

2 × 2 × 3 × 3 = 36 cells, **20 trials per cell**, 720 trials, run in a fixed order as in 002. The client timeout is **5.0 s** (002 used 2.0 s) so that a reconnection after the client's default 1 s delay can finish inside it; a timeout is not the fault under test here.

## 4. Pre-registered predictions

From the source reading in section 2, before any run:

| | Cells | Prediction |
|---|---|---|
| P1 | `auto`, either server, either drop point | No resumption: the first attempt fails as a disconnect; no request with `Last-Event-ID`; `no_dedup` and `fresh_key_per_attempt` refund twice in 20 of 20; `stable_key` refunds once in 20 of 20. The event store makes no difference. |
| P2 | `legacy`, `stateful_sse` (no store), either drop point | As P1: there is no event ID to resume from. |
| P3 | `legacy`, `stateful_sse_store`, `no_bytes` | As P1: the client never received the priming event, so it has no ID. |
| P4 | `legacy`, `stateful_sse_store`, `after_headers` | The client resumes: at least one GET with `Last-Event-ID` per trial, the server replays the stored result, the first attempt ends `ok` after at least about 1 s. One request reaches the sink. All three strategies are `correct` in 20 of 20, `no_dedup` included. |
| P5 | `none`, every cell | `correct` in 20 of 20; no request with `Last-Event-ID`. |
| P6 | all | Requests at the sink beyond the application's own attempts: 0 in every trial. A resumption is a GET that replays; it does not resend the POST. |

**Controls** (contract section 7). Positive: `stable_key` in every faulted cell gives no duplicate. Negative: `no_dedup` in the faulted cells where resumption is impossible (P1 to P3) gives a duplicate in every trial. If either fails, the harness is wrong and the run is not reported as a result. A check on the fault itself: the proxy records, per drop, whether the sink held the refund (`fault_confirmed`), how many bytes it forwarded on the connection, and how many SSE event IDs those bytes contained: none with `no_bytes`; at least one in the P4 cells.

**What refutes each prediction.** P1, P2 or P3: any trial in those cells with a `Last-Event-ID` request, a first attempt that ends `ok`, or (for `no_dedup`) a single refund. P4: any `no_dedup` trial in that cell with two refunds or a first attempt that does not end `ok`; if some trials resume and some do not, P4 is refuted and the split is reported with the timings. P5: any no-fault trial that is not `correct`. P6: any trial with requests beyond the application's attempts.

## 5. Measures per trial

As in 002 (`crashtest.experiment.trial.TrialResult`), with verdicts only from `crashtest.checker.classify`, `report_matches_sink` from `crashtest.checker.report_matches_sink`, and `fault_confirmed` from the proxy's drop record. Added: the client mode and the negotiated protocol version of each attempt; the number of client requests carrying `Last-Event-ID` that the proxy saw during the trial (`resume_requests`); and, per drop, the bytes and SSE event IDs forwarded before the cut.

## 6. Where it runs

Locally on Windows (the local stack) and once on Linux through `.github/workflows/experiment.yml`, both from a committed tree. Results go to `results/mcp_resume/` and `results/mcp_resume_linux/`.

## 7. What this does not cover

- One client: the reference `mcp` Python client 2.3.0. CrewAI and agent-framework are not run here.
- One event store, written for this project (in memory, per server process), following the interface's docstrings. A store that loses events, or a server restart, is not tested.
- A cut before the request reaches the server, and a cut in the middle of a long stream of several events, stay open (the other M2 item). `after_headers` is one mid-stream point only: after the priming event, before the response.
- The client's own reconnection after a drop of its standalone GET stream is not tested.
