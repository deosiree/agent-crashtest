# Remaining research gaps

As of 16 November 2026. Earlier findings: [related-work.md](related-work.md), [evidence-check.md](evidence-check.md).

## Originality: where the project stands

Not original as a category. Two public harnesses and at least 15 idempotency libraries exist. The project is defensible only on the four things none of them covers:

1. Durable engines as a test axis (does adding DBOS, Temporal or Restate stop the duplicate?).
2. MCP client retry behaviour across clients and versions.
3. Measured model behaviour after an ambiguous outcome: does the model call again, and with the same arguments?
4. Continuous tracking across releases, with effects observed at a network service.

Experiment 001 covers none of these yet. It establishes the harness and its controls.

## Open questions, in the order they block work

| # | Question | Why it matters | How to close it |
|---|---|---|---|
| 1 | What does LangGraph's own documentation promise on resume? | Without it, a LangGraph result cannot be called a violation. The page fetched earlier did not contain it. | Read the durable-execution and persistence pages; quote them in the subject page. |
| 2 | Do the other clients resend `tools/call` after a timeout? | Experiment 001 found the official Python client (mcp 2.3.0, stateless HTTP) does not. CrewAI #8932 and agent-framework #9204 report that their clients do. | Run the same fault through each client; count sink requests per attempt. |
| 3 | Does the Python client resend under other transport settings? | Only stateless JSON responses were tested. Stateful sessions with SSE have resumption logic in the SDK. | Repeat experiment 001 with `stateless_http=False` and with SSE responses. |
| 4 | Does a durable engine protect a plain function tool? | The DBOS integration page says function tools are not wrapped automatically. This is hypothesis H2 and the core of the backend axis. | pydantic-ai + DBOS subject, plain tool versus `@DBOS.step`, kills at K2 and K3. |
| 5 | How often do models re-issue a call after an unknown-outcome error, and how often do the arguments differ? | Decides whether "remove transport retries" is enough, and whether argument-hash keys work. No published rate was found. | Real-model study (H4, H5). Needs API spend; deferred. |
| 6 | Can a process be killed at an exact point on Windows and in CI the same way? | The injector depends on it. | Prototype `docker kill` driven by a sink signal, once Docker is available. |
| 7 | Is the Docker stack correct? | `Dockerfile` and `docker-compose.yml` are written but have never been built or run. | Free space on C: or move Docker's disk image, then build and run the tests against it. |

## Unverified claims carried from earlier research

- UndoBench's paper body was not read: which frameworks it evaluated, and whether it reports argument drift.
- The states of fix pull requests agent-framework #9243 and crewAI #8933.
- The claim in MCP #3394 that none of 266 non-read-only tools across 24 servers accept an idempotency key.
- Temporal, Restate and the other engine pages; only DBOS was read.
- Hidden portions of four long issue threads.

## Risks specific to this stage

- **Environment.** Drive C: on the development machine is full, and Docker's data lives there, so Docker Desktop will not start. The project itself now lives at `D:\agent-crashtest`.
- **Timing sensitivity.** A control cell failed once under a 0.5 s timeout because of machine load. Margins were widened; any future scenario must keep timeouts well clear of normal latency, and report nominal latency with the results.
- **SDK churn.** The MCP Python SDK changed major version (FastMCP became `MCPServer`; a timeout now arrives inside an exception group). The version is pinned to 2.3.0.
