# Experiment 003 — one tool call through three MCP client libraries

Run on 16 November 2026. Raw data: [`results/single_call/20261116T165233Z.json`](../../results/single_call/20261116T165233Z.json) (main run) and [`20261116T171353Z.json`](../../results/single_call/20261116T171353Z.json) (CrewAI with default settings).

**Not public.** This names a defect in another project. Under this repository's own rule, the reproduction goes to CrewAI's maintainers before anything is published.

**Note added 20 November 2026 (after experiment [007](007-mcp-resume.md)).** The transport factor was real for one client only. A probe ran each subject script once against each of the four servers through a recording proxy, on Windows, with the versions every 003 results file records. The reference client (`mcp` 2.3.0, `Client(url)`) and agent-framework 1.26.0 (which uses `mcp` 2.3.0) both sent `server/discover` and spoke protocol 2026-07-28 to every server. Their responses were JSON bodies with no `mcp-session-id`, whatever the server's settings (agent-framework's own `subscriptions/listen` call got one SSE response). So for those two clients, `stateless_json` and `stateful_sse` were the same transport. CrewAI 1.15.41 (`mcp` 1.30.0) used the `initialize` handshake at 2025-11-25 and got a session and SSE responses from the stateful servers, so its two transports did differ. No verdict changes, and the results files are unchanged. The CrewAI finding stands on both of its transports; for the other two clients, "both transports gave the same counts" means the same transport twice.

## Result

**CrewAI's MCP client turned one `call_tool` into two refunds whenever the server's response was late: 20 of 20 trials at a 2 s timeout, and 3 of 3 with CrewAI's default settings. Each time the client returned a normal success result. The reference `mcp` client and Microsoft agent-framework sent exactly one request in every trial.**

What this establishes is narrower than hypothesis H3, which says a retrying client executes the tool more than once and is close to true by definition: **CrewAI's MCP client retries `tools/call` after a timeout, by default; agent-framework's and the reference client do not.** (An earlier version of this write-up said it "supports H3 for CrewAI"; the [scientific audit](../audits/scientific-audit.md), section 4.1, corrected that.)

Each script makes a single call with no retry of its own, so every extra request comes from the library. Both transports gave the same counts; each cell below is 10 trials per transport, 20 in total.

| Client library | Fault | Trials | One request | Resent | Refunded twice | What the caller saw |
|---|---|---|---|---|---|---|
| `mcp` 2.3.0 | none | 20 | 20 | 0 | 0 | success |
| `mcp` 2.3.0 | late response | 20 | 20 | 0 | 0 | timeout error |
| `mcp` 2.3.0 | connection drop | 20 | 20 | 0 | 0 | disconnect error |
| CrewAI 1.15.41 | none | 20 | 20 | 0 | 0 | success |
| CrewAI 1.15.41 | late response | 20 | 0 | **20** | **20** | **success** |
| CrewAI 1.15.41 | connection drop | 20 | 20 | 0 | 0 | error |
| agent-framework-core 1.26.0 | none | 20 | 20 | 0 | 0 | success |
| agent-framework-core 1.26.0 | late response | 20 | 20 | 0 | 0 | timeout error |
| agent-framework-core 1.26.0 | connection drop | 20 | 20 | 0 | 0 | connection error |

CrewAI with nothing configured (30 s execution timeout, 3 attempts), against a response held for 40 s. Three trials on one transport only, so this shows the default behaviour exists, not how often it occurs. Re-run on the current harness on 17 November ([`20261117T172136Z.json`](../../results/single_call/20261117T172136Z.json)) with the same outcome: twice in 3 of 3, each reported as success, the proxy counting two tool calls per call and the sink confirming the withheld response in all three:

| Trials | Requests per call | Refunds per call | Caller saw | Time per call |
|---|---|---|---|---|
| 3 | 2 | 2 | success | about 39 s |

## Re-run on the current harness

Re-run on 17 November 2026 with the same parameters, from commit `4036ec2` with a clean tree: [`20261117T180204Z.json`](../../results/single_call/20261117T180204Z.json). This closes the [scientific audit](../audits/scientific-audit.md)'s action 1. It is also the first run judged by contract v0.2's stricter `correct` as it ran, rather than re-judged afterwards.

**All 18 cells matched the main run above.** Verdicts: 60 `correct`, 20 `duplicate` (CrewAI under a late response, on both transports), 100 `in_doubt_surfaced`. The proxy counted two tool calls per call for CrewAI under a late response and one in every other trial, and the sink confirmed the fault in all 120 faulted trials.

## On Linux, in Docker: design, written before the run

Scientific-audit action 5. The main matrix (three clients, `stateless_json` and `stateful_sse`, three faults, 10 trials per cell, 180 trials) is run once in a Linux container (`docker compose --profile experiments run --rm crash-003`). Each client runs in its own environment built from its lock file, minus `pywin32`, which is Windows only. The MCP servers and the sink are the Compose services.

**Prediction.** Every cell gives the same counts as on Windows: CrewAI refunds twice under a late response and reports success; every other faulted cell refunds once with the error surfaced; the controls refund once. A difference would point to a platform effect in a client library and would be reported as such.

## On Linux: result

Run on 17 November 2026 (UTC) on a GitHub-hosted Ubuntu machine through the experiment workflow (run 44183213773), from commit `ddf4567` with a clean tree. Raw data: [`20261117T215146Z.json`](../../results/single_call_linux/20261117T215146Z.json) and its log. **Deviation from the design above:** it ran directly on the Linux machine, not in a Docker container, because the local container attempt crashed Docker Desktop for lack of memory.

**The prediction held: all 18 cells matched both Windows runs.** Verdicts: 60 `correct`, 20 `duplicate`, 100 `in_doubt_surfaced`. CrewAI refunded twice under a late response on both transports and reported success, with the proxy counting two tool calls per call; every other faulted cell refunded once and surfaced the error; the controls refunded once. The sink confirmed the fault in all 120 faulted trials. The client libraries behave the same on Linux and Windows.

## Timeout-ratio sweep: design, written before the run

Scientific-audit action 6 (section 2.3): the main run held the response 4 times longer than the clients' timeout. This sweep asks whether each client's behaviour changes as that ratio moves through 1.

**Conditions.** The three clients, the `stateless_json` transport, the lost-response fault, 10 trials per cell, client timeout 2.0 s. The sink withholds its response for 1.0, 1.8, 2.2, 4.0 or 8.0 s: 0.5, 0.9, 1.1, 2 and 4 times the timeout. One run per delay on a GitHub Ubuntu machine through the experiment workflow, 150 trials in all.

**Predictions.**

- **Below the timeout (0.5× and 0.9×):** every client receives its response, refunds once and reports success. At 0.9× a few trials may still time out from overhead; at least 9 of 10 per client behave so.
- **Above the timeout (1.1×, 2× and 4×):** CrewAI resends and refunds twice, reporting success (10 of 10); the reference client and agent-framework refund once and raise an error (10 of 10).

**What would refute it.** CrewAI refunding twice below the timeout, or once above it; or either other client refunding twice anywhere.

## Timeout-ratio sweep: result

Run on 17 November 2026 (UTC) on GitHub-hosted Ubuntu machines through the experiment workflow, each with a clean tree. Raw data, one file and log per delay, in [`results/single_call_linux/`](../../results/single_call_linux/). The 1.0 s and 8.0 s runs are from commit `b1a6992`, the others from `20d3fdc`; the two commits differ only in the workflow file and a flag of experiment 001's runner, neither of which this runner uses.

**The prediction held in every cell, with no exceptions at 0.9×.**

| Delay | Ratio | Results file (run) | `mcp` | CrewAI | agent-framework |
|---|---|---|---|---|---|
| 1.0 s | 0.5× | `20261117T220513Z.json` (44186301260) | 10 once, success | 10 once, success | 10 once, success |
| 1.8 s | 0.9× | `20261117T220708Z.json` (44186596106) | 10 once, success | 10 once, success | 10 once, success |
| 2.2 s | 1.1× | `20261117T220700Z.json` (44186610251) | 10 once, error | **10 twice, success** | 10 once, error |
| 4.0 s | 2× | `20261117T220727Z.json` (44186622366) | 10 once, error | **10 twice, success** | 10 once, error |
| 8.0 s | 4× | `20261117T220823Z.json` (44186352867) | 10 once, error | **10 twice, success** | 10 once, error |

Verdicts: `correct` for every success with one refund, `duplicate` for CrewAI above the timeout, `in_doubt_surfaced` for every error. In all 150 trials the sink confirmed the withheld response and the proxy's count of tool calls equalled the sink's count of requests: two per call for CrewAI above the timeout, one everywhere else.

So CrewAI's resend is triggered by its own timeout expiring, at any ratio above 1 tested, and not by a long delay as such; below the timeout it behaves like the other two clients. Ratios between 0.9 and 1.1 were not tested.

## Re-check with a second counting point

After the main run, the harness gained a count of the tool calls each client puts on the wire, taken at the proxy and independent of the sink. A short re-run (2 trials per cell, default transport, [`20261116T205316Z.json`](../../results/single_call/20261116T205316Z.json)) gave the same outcome as above, and for CrewAI under a late response the proxy saw 2 requests per call. The resend is therefore the client's, not something added between the MCP server and the sink.

Under the shared checker the verdicts in that re-run were `duplicate` for CrewAI with a late response, `in_doubt_surfaced` for every cell where the caller received an error, and `correct` for the controls.

## Why CrewAI resends

In the installed package, `crewai/mcp/client.py`:

- Line 56: `MCP_MAX_RETRIES = 3`, the default for `MCPClient(max_retries=...)`.
- Lines 702-761, `_retry_operation`: wraps the operation in `asyncio.wait_for`; on a timeout, and on any exception it does not specifically exclude, it sleeps `2**attempt` seconds and runs the operation again.
- `call_tool_result` sends every tool call through `_retry_operation`.

The retry makes no distinction between read-only operations and tool calls with side effects, and it sends no idempotency key. A timeout means "no answer yet", not "did not happen", so the second request repeats an effect that already committed.

## Two smaller observations

- **A dropped connection was not retried by CrewAI.** The call raised `RuntimeError: Error during MCP client disconnect: Server disconnected without sending a response.` and the sink saw one request. The retry loop did not run for this failure in these trials; why was not investigated.
- **agent-framework's own retry did not fire.** Its `_call_tool_with_retries` retries once after what it judges to be a connection loss. The drop here surfaced as `MCPError(-32000, 'Connection closed')` and one request reached the sink. Its code path was not traced further.

## What was run

- **Operation, path and faults:** as in [experiment 002](002-mcp-client-matrix.md). The late response was held for 8 s against a 2 s client timeout.
- **Subjects:** one script per library in [`subjects/`](../../subjects/), each in its own virtual environment. Each makes one call to `issue_refund` and prints what it saw.

  | Subject | Entry point used | Timeout setting |
  |---|---|---|
  | `mcp_sdk` | `mcp.Client.call_tool` | `read_timeout_seconds=2` |
  | `crewai` | `crewai.mcp.MCPClient.call_tool_result` over `HTTPTransport` | `execution_timeout=2`; `max_retries` left at its default |
  | `agent_framework` | `MCPStreamableHTTPTool.call_tool` | `request_timeout=2` |

- **Transports:** `stateless_json` and `stateful_sse`.
- **Measure:** requests and refunds recorded by the sink for the order, read one second after the subject process exits.
- **Versions:** CrewAI 1.15.41 (bundling mcp 1.30.0), agent-framework-core 1.26.0 (mcp 2.3.0), server mcp 2.3.0, Python 3.12.2, Windows 11.

## What it does not show

- **The client object, not an agent.** `MCPClient` was called directly. No CrewAI agent, crew or model was run, so this does not show how often a real CrewAI agent hits this path, only that the path exists and behaves this way.
- **Not CrewAI's other MCP paths.** `crewai/tools/mcp_tool_wrapper.py` and `crewai/mcp/tool_resolver.py` carry their own retry constants and were not tested.
- **Two of four transports**, and no stdio.
- **Small counts.** 20 trials per cell and 3 with defaults; every trial in a cell agreed, which is a repeatability check, not a rate.
- **One version of each library, one run, one machine.**
- **Whether this is already reported upstream** was not checked.

## Reproduce

Create one virtual environment per library (`crewai` and `crewai-tools[mcp]`; `agent-framework-core`, `mcp` and `tenacity`), then:

```powershell
python -m crashtest.experiment.single_call --local --transports stateless_json,stateful_sse --trials 10 --subject mcp_sdk=.venv\Scripts\python.exe --subject crewai=<crewai env>\Scripts\python.exe --subject agent_framework=<agent-framework env>\Scripts\python.exe
```

CrewAI with its defaults:

```powershell
python -m crashtest.experiment.single_call --local --faults lost_response --subject-timeout 0 --fault-delay 40 --hang-limit 200 --trials 3 --subject crewai=<crewai env>\Scripts\python.exe
```
