# Experiment 007 — does the MCP client resume a dropped stream from an event store?

Run on 20 November 2026, from commit `9f10c84` (clean tree), once on Windows and once on Linux (GitHub Actions). Pre-registered design: [`docs/m2-plan.md`](../m2-plan.md). Raw data: [`results/mcp_resume/20261120T173635Z.json`](../../results/mcp_resume/20261120T173635Z.json) (Windows 11, local stack, about 20 minutes) and [`results/mcp_resume_linux/20261120T173703Z.json`](../../results/mcp_resume_linux/20261120T173703Z.json) (Ubuntu runner, about 6 minutes; log beside it).

**Every cell matched the pre-registered predictions, on both machines: 36 cells, 720 trials each, no prediction, control or harness check violated.** The two machines differed only in timing (section "What it shows", point 6).

## Result

**The reference `mcp` client 2.3.0 resumed a dropped call, and refunded once even with a naive retry loop, in exactly one configuration: the 2025-11-25 protocol (`mode="legacy"`), a stateful SSE server with an event store, and a drop that came after the client had received the stream's priming event: 20 of 20 per strategy, on both machines. With the client's default settings it never resumed: by default it negotiates protocol 2026-07-28, which has no resumable streams, so the event store made no difference and a naive retry refunded twice in every trial.**

20 trials per row per machine. "Resumed" counts trials in which the client sent a request carrying `Last-Event-ID`, as seen by the proxy.

| Client protocol | Server | Drop point | Strategy | Refunded once | Refunded twice | Resumed | First attempt |
|---|---|---|---|---|---|---|---|
| any | any | no fault | any of three | 20 | 0 | 0 | `ok` |
| `auto` (2026-07-28) | with or without store | `no_bytes` or `after_headers` | `no_dedup`, `fresh_key_per_attempt` | 0 | **20** | 0 | disconnect |
| `auto` (2026-07-28) | with or without store | `no_bytes` or `after_headers` | `stable_key` | **20** | 0 | 0 | disconnect |
| `legacy` (2025-11-25) | no store | `no_bytes` or `after_headers` | `no_dedup`, `fresh_key_per_attempt` | 0 | **20** | 0 | disconnect |
| `legacy` (2025-11-25) | no store | `no_bytes` or `after_headers` | `stable_key` | **20** | 0 | 0 | disconnect |
| `legacy` (2025-11-25) | with store | `no_bytes` | `no_dedup`, `fresh_key_per_attempt` | 0 | **20** | 0 | disconnect |
| `legacy` (2025-11-25) | with store | `no_bytes` | `stable_key` | **20** | 0 | 0 | disconnect |
| `legacy` (2025-11-25) | with store | `after_headers` | any of three | **20** | 0 | **20** | `ok`, after 1.0 s to 2.4 s |

Every row of the table holds for each of its cells separately; the per-cell rows are under `summary` in each results file.

## What was run

- **Operation and path:** as in [001](001-mcp-refund-lost-response.md) and [002](002-mcp-client-matrix.md): "refund this order once" through the MCP tool `issue_refund`, client → TCP proxy → MCP server → sink → PostgreSQL. The application's retry loop is 002's: retry on a timeout or a disconnect, up to 3 attempts, each on a new client.
- **Client protocol.** `auto`: `Client(url)`, the SDK default, which sends `server/discover` and adopts protocol 2026-07-28. `legacy`: `Client(url, mode="legacy")`, the `initialize` handshake, protocol 2025-11-25. Every attempt recorded the version it negotiated: 2026-07-28 in every `auto` attempt and 2025-11-25 in every `legacy` attempt.
- **Servers.** `stateful_sse`: `stateless_http=False`, `json_response=False`, no event store (002's server). `stateful_sse_store`: the same plus `event_store=InMemoryEventStore()` ([`src/crashtest/refund_mcp/event_store.py`](../../src/crashtest/refund_mcp/event_store.py)); `retry_interval` not set, so the client waits its default 1000 ms before reconnecting.
- **Drop points** ([`src/crashtest/faults/drop_proxy.py`](../../src/crashtest/faults/drop_proxy.py)). `no_bytes` (002's): nothing the server sends on the connection carrying the call reaches the client. `after_headers` (new): the response headers and every complete SSE event without a JSON-RPC message reach the client; the first JSON-RPC message does not. In both, the proxy cuts the connection only once the sink reports the refund committed.
- **Client timeout:** 5.0 s per attempt (002 used 2.0 s), so that a reconnection after the default 1 s delay fits inside it.
- **Versions:** `mcp` 2.3.0 (client and server), Python 3.12.2 on Windows 11 and 3.12.16 on Linux (Ubuntu runner); the other versions are in each file's `environment`.

## Checks on the fault and the harness

- **Every drop came after the commit:** `fault_confirmed` true in all 480 faulted trials on each machine.
- **The drop points did what they say.** With `no_bytes`, 0 bytes reached the client in all 240 drops per machine. With `after_headers`, the response headers reached the client in all 240; exactly one SSE event ID (the priming event) reached it in each of the 60 drops on the server with a store under `legacy`, and none in the other 180: the store-less server sends no priming event, and under 2026-07-28 the server answers with a JSON body.
- **No request below the application:** requests at the sink minus the application's attempts was 0 in all 720 trials per machine, and the tool calls the proxy saw equalled the attempts in every trial. A resumption is a GET that replays the stored answer; it did not resend the POST.
- **No count moved** after a trial's first read, or by the end of the run.
- **Re-judged afterwards.** Both files load into the result store, whose cell counts match the tables. `crashtest.results.reverdict` re-judged all 720 Windows verdicts against the local sink's rows: none changed. The Linux sink's database went with the runner, so its 720 payloads could not be re-checked; its verdicts rest on the run's own reads.
- **One harness bug was found and fixed before the run.** The first version of the event store defined `__len__`. `mcp` 2.3.0 tests the store by truthiness (`if self._event_store:`), so an empty store counted as no store, never recorded its first event, and stayed empty: the server sent no priming event and no event IDs. The end-to-end test caught it; the store no longer defines a length, and a test asserts that an empty store is truthy. This is a property of the harness, not a defect claimed in the library.
- **Pre-run checks.** Before the run of record, the test suite ran one trial in each of five cells ([`tests/test_resume.py`](../../tests/test_resume.py)), including the resume cell; those trials are not counted here.

## What the library documents

- The SDK's documentation site lists `event_store` and `retry_interval` as "resumability" options on its Run page, and nothing more: it does not define an event store, and it does not say that the client resumes a dropped stream. The package ships the `EventStore` interface but no implementation.
- The source does both. The server opens each SSE response with a priming event when it has a store and the protocol is 2025-11-25 or later (`_mint_priming_event` in `mcp/server/streamable_http.py`). The client, when a POST's SSE stream ends without a response after it has seen an event ID, waits and reconnects with `Last-Event-ID`, at most 2 attempts (`_handle_sse_response` and `_handle_reconnection` in `mcp/client/streamable_http.py`).
- The SDK's protocol-versions page documents that `Client(url)` probes `server/discover` and adopts the result when the server is modern. The 2026-07-28 specification says "Resumable SSE streams via `Last-Event-ID` are not supported". The 2025-11-25 specification describes the priming event and the reconnection.

So everything measured here is documented behaviour, or follows directly from the source: nothing in this experiment is reported as a defect.

## What it shows

1. **With its defaults, the reference client does not resume, whatever the server offers.** In all 240 `auto` trials with a drop per machine, the client negotiated 2026-07-28 and sent no `Last-Event-ID`; with the event store the outcome was the same as without, row for row. A naive retry after the drop refunded twice in 80 of 80 trials, as in 002.
2. **Resumption works when all three conditions hold, and it turns a naive retry into a single refund.** Under `legacy`, with the store, after the priming event: one `Last-Event-ID` request in each of 60 trials per machine; the server replayed the stored answer; the first attempt ended `ok` (timings in point 6); one refund; `correct` for `no_dedup` and `fresh_key_per_attempt` too, in 40 of 40 trials per machine.
3. **The client needs an event ID before the drop.** With the store but a drop before any byte reached the client (`no_bytes`), the client had no ID, did not try to resume, and the naive retry refunded twice in 40 of 40 trials per machine. The same happened with no store, where no ID is ever sent.
4. **A stable key enforced by the database protected every cell**: one refund in all 160 `stable_key` faulted trials per machine (positive control). `no_dedup` refunded twice in every faulted cell where resumption was impossible (negative control).
5. **What the caller was told always agreed with the sink, except in the duplicates** (`report_matches_sink`, contract v0.3): false in exactly the 280 duplicate trials per machine, where the client reported one successful refund and the sink held two.
6. **A resumed call is slow, and how slow depends on the machine.** The first attempt of a resumed call took 1.04 s to 1.11 s on Linux (median 1.04 s) and 1.36 s to 2.42 s on Windows (median 2.22 s); a call with no fault on the same server took a median 0.03 s on Linux and 0.67 s on Windows. The client's 1 s default reconnection delay is the floor. On Windows, 43 of the 60 resumed calls took longer than 2.0 s, the timeout 002 used; on Linux none did. So with 002's timeout, most Windows resumptions would probably have timed out first and handed the call to the retry loop. That is an inference from the timings, not a measured cell: this run used 5.0 s throughout.

## Correction to experiment 002

The probe made while designing this experiment showed that the default client negotiates 2026-07-28 with every server of 002, and that on this protocol the server answers with one JSON body, with no session and no SSE, whatever its `stateless_http` and `json_response` settings. So 002's four transports were one transport on the wire, and 002 measured the reference client on protocol 2026-07-28 four times. No verdict of 002 changes; a dated note in the [002 write-up](002-mcp-client-matrix.md) says this. The `auto` rows above match 002's connection-drop rows, and the `legacy` rows without a store are the first measurement of the stateful SSE transport that 002 meant to test.

Checked afterwards for experiment 003 (dated note in the [003 write-up](003-mcp-client-libraries.md)). Its reference-client subject and agent-framework 1.26.0 also spoke 2026-07-28 to every server, so their two transports were one. CrewAI 1.15.41 (`mcp` 1.30.0) used the 2025-11-25 handshake, so its transports did differ.

## What it does not show

- **One client, one version.** Only the reference `mcp` Python client 2.3.0. CrewAI (which pins `mcp` 1.30.0, a handshake-era client) and agent-framework were not run; whether they resume is open.
- **One event store, written for this project.** In memory, per server process, replaying only the same stream (as the 2025-11-25 specification requires). A store that loses events, a shared store behind several servers, or a server restart between the drop and the reconnection was not tested; with a lost event the client would get no answer and the naive retry would refund again.
- **One drop point within the stream.** `after_headers` cuts after the priming event and before the response, with the response already stored. A cut before the request reaches the server, a cut after some but not all of several events, and a drop that comes before the server has stored the answer were not tested. The other M2 item ("cut mid-stream and before the request") stays open.
- **The reconnection's own failure modes.** The client tries to reconnect at most 2 times. No reconnection was refused or dropped here, so what the client reports when resumption fails was not observed. The race between the reconnection and the client's timeout (point 6) was not run as a cell: every trial used a 5.0 s timeout, and the server sent no `retry` value.
- **Nothing about the 2026-07-28 protocol's own recovery.** It has no resumable streams; this experiment only shows that the default client uses it, and what that means for a drop after the commit.
- **Repeatability, not rates.** Each cell gave the same outcome in 20 of 20 trials on each of two machines. That is a repeatability check, not a sample from a distribution; the order of cells was fixed, as in 002.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m crashtest.experiment.resume --local --trials 20
```

On Linux, through the experiment workflow:

```powershell
gh workflow run experiment.yml -f experiment=007-mcp-resume
```
