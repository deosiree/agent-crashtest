# Experiment 002 — MCP transports and a true connection drop

Run on 16 November 2026, and re-run on the current harness on 17 November. Raw data: [`results/mcp_refund/20261116T161126Z.json`](../../results/mcp_refund/20261116T161126Z.json) and [`20261117T165004Z.json`](../../results/mcp_refund/20261117T165004Z.json).

**The re-run matched the first run in all 36 cells.** It was made from commit `1b50c5d` (the tree held only uncommitted documentation edits) after the harness gained the shared checker, the fault record and provenance, as the [scientific audit](../audits/scientific-audit.md) asked (action 1). In the re-run the sink confirmed the fault in all 480 faulted trials, the proxy recorded all 240 connection drops as falling after the commit, requests beyond the application's own attempts were 0 in all 720 trials, and no order's count moved by the end of the run. Under contract v0.2 no verdict changes ([review](../audits/contract-v0.2-review.md)).

**Note added 20 November 2026 (experiment 007).** The four "transports" below were one transport on the wire. The client was `Client(url)` with its default `mode="auto"`, which sends `server/discover` and adopts protocol 2026-07-28 when the server offers it, as every server here does. On that protocol the server answers each request with a single JSON body, with no session and no SSE, whatever its `stateless_http` and `json_response` settings: a probe against the four servers saw only POSTs answered with `application/json` and no `mcp-session-id` header. Only `mode="legacy"` (the 2025-11-25 handshake) gives the stateful and SSE behaviour the table names. No verdict changes, and the results files are unchanged. What 002 shows is the reference client on protocol 2026-07-28, four times; it does not compare transports. See [007](007-mcp-resume.md), which runs both modes.

## Result

**The MCP Python client never resent a tool call by itself: 0 extra requests in 720 trials, across four transport settings and two kinds of lost response. Every duplicate refund came from the application's retry loop, and the outcome depended only on the key strategy, not on the transport or on how the response was lost.**

The accurate statement is that the reference client is not a retrying client: the transport layer of `mcp` 2.3.0 did not create duplicates in any configuration tested. Hypothesis H3 is about clients that retry, so this result neither supports nor refutes it. (An earlier version of this write-up called it a negative result for H3; the [scientific audit](../audits/scientific-audit.md), section 4.2, corrected that.)

The same pattern held in all four transports, so one table covers them. Each cell is 20 trials per transport, 80 in total.

| Fault | Client strategy | Trials | Refunded once | Refunded twice | Requests at sink | Resent by the SDK | Client saw success |
|---|---|---|---|---|---|---|---|
| none | any of the three | 240 | 240 | 0 | 240 | 0 | 240 |
| lost response (late) | `no_dedup` | 80 | 0 | **80** | 160 | 0 | 80 |
| lost response (late) | `stable_key` | 80 | **80** | 0 | 160 | 0 | 80 |
| lost response (late) | `fresh_key_per_attempt` | 80 | 0 | **80** | 160 | 0 | 80 |
| connection drop | `no_dedup` | 80 | 0 | **80** | 160 | 0 | 80 |
| connection drop | `stable_key` | 80 | **80** | 0 | 160 | 0 | 80 |
| connection drop | `fresh_key_per_attempt` | 80 | 0 | **80** | 160 | 0 | 80 |

The per-transport rows, which are identical to each other, are in the results file under `summary`.

## What was run

- **Operation and path:** as in [experiment 001](001-mcp-refund-lost-response.md): "refund this order once" through the MCP tool `issue_refund`, client → MCP server → sink → PostgreSQL.
- **Transports:** one MCP server per setting of streamable HTTP.

  | Name | Server session | Response format |
  |---|---|---|
  | `stateless_json` (the 001 baseline) | none | one JSON body |
  | `stateless_sse` | none | SSE stream |
  | `stateful_json` | session kept by the server | one JSON body |
  | `stateful_sse` | session kept by the server | SSE stream |

- **Faults:**
  - **Lost response (late):** as in 001. The sink commits, then holds its response for 5.0 s; the client's timeout is 2.0 s.
  - **Connection drop (new):** a TCP proxy sits between the client and the MCP server. For the targeted call it withholds everything the server sends, waits until the sink reports the refund as committed, then aborts the client's connection. The client receives no response bytes for that call.
- **Retry policy:** retry on a timeout or a disconnect, up to 3 attempts, each on a new connection.
- **Client strategies:** `no_dedup`, `stable_key`, `fresh_key_per_attempt`, as in 001.
- **Versions:** Python 3.12.2, mcp 2.3.0, FastAPI 0.163.1, psycopg 3.3.9, Windows 11. Run locally with PostgreSQL from `pgserver`.

## Checks on the fault itself

- **Every drop happened after the commit.** The proxy recorded 240 drops, and in all 240 the sink already held the refund when the connection was cut.
- **The proxy is transparent when not armed.** A test runs a normal call through it on each transport and gets one refund and no drop.
- **The two faults look different to the client.**

  | Fault | What the first attempt raised | Median time to failure |
  |---|---|---|
  | Lost response (late) | `MCPError` code −32001, request timed out | the 2.0 s timeout |
  | Connection drop | `RemoteProtocolError`: "Server disconnected without sending a response." | 0.375 s |

## What it shows

1. **No SDK-level resend, in any transport tested.** `sdk_extra_requests` — requests the sink received beyond the application's own attempts — was 0 in every one of 720 trials.
2. **A dropped connection is as ambiguous as a timeout, and arrives faster.** The caller learns nothing about whether the refund happened, in about 0.4 s instead of 2 s. A retry loop that treats "connection error" as "safe to retry" duplicates the refund exactly as a timeout does.
3. **The fix is the same in every cell.** A stable key enforced by the database gave one refund in 160 of 160 faulted trials; a key regenerated per attempt gave two in 160 of 160.

A stateful session with SSE also did not resume or replay the call after a drop. That is not listed above as a finding, because the server had no event store, so the client had nothing to resume from.

## A finding about the harness

The disconnect was first misclassified. `mcp` 2.3.0 talks HTTP through `httpx2` and `httpcore2`, not `httpx`, so the error raised was `httpx2.RemoteProtocolError`, which is not a subclass of anything in `httpx`. Code that catches `httpx.TransportError` around an MCP call will not catch it. The harness now recognises both families.

## What it does not show

- **One client, one version.** Only the `mcp` Python client 2.3.0. CrewAI, Microsoft agent-framework and other MCP clients were not run; that part of milestone M2 is still open.
- **No resumable server.** With an event store configured, a stateful SSE client may behave differently after a drop. That configuration was not tested.
- **One drop point.** The connection was cut after the commit and before any response byte. A cut in the middle of an SSE stream, or before the request reached the server, was not tested.
- **No stdio transport.**
- **Nothing about agent frameworks or models.** The retry loop is hand-written, as in 001.
- **One run.** Each cell gave the same outcome in all 20 trials per transport, which is a repeatability check, not a sample from a distribution. The run was not repeated on another day or machine.

## Reproduce

```powershell
python -m crashtest.experiment.run --local --transports all --trials 20
```

See the [README](../../README.md) for setup.
