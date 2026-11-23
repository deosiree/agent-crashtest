# Roadmap

Updated 23 November 2026. Each milestone ends with something measured and written up.

## Done

- [x] Prior work read; extend-or-build decision — `docs/related-work.md`
- [x] Cited issues re-verified; hypotheses H1–H5 — `docs/evidence-check.md`
- [x] Reliability contract (now v0.3) — `docs/contract.md`
- [x] MVP architecture — `docs/architecture.md`
- [x] Sink service with database-enforced idempotency and a lost-response fault
- [x] Refund MCP server and client trial logic
- [x] Experiment 001: lost response and retry, three client strategies — `docs/experiments/001-mcp-refund-lost-response.md`
- [x] Harness fix: control cells no longer depend on machine load (wider timeout margins)
- [x] Docker Compose stack built and run (PostgreSQL 16.16)
- [x] M1: GitHub repository, licence, contributor and security docs, Ruff, mypy, coverage, CI green
- [x] Harness fix: the fault proxy keeps one sink client and retries a read once after a dropped keep-alive connection (`sink_retries` per trial) — `CHANGELOG.md`

## Order of the open work

1. M2: cut the connection mid-stream and before the request arrives, not only after the commit.
2. M3: attempt to replicate the crash-path result reported in arXiv 2608.03836.

Done so far, in order: the GitHub Actions experiment workflow (`.github/workflows/experiment.yml`); 003 and 004 on Linux (003: all 18 cells matched; 004: 26 of 27, where under `async` a kill just after the tool refunded twice without a key, and once on Windows); the timeout-to-delay sweep for 001 and 003 (duplicates only above the timeout); a consistency pass that re-judged every results file with no verdict change; M5 (experiment 006, validated against the sink export); and experiment 007 (MCP stream resumption). Experiment 007 also showed that 002's four server settings were one transport on the wire, and that in 003 only CrewAI's two transports differed; both write-ups carry dated notes.

## Audit actions

- [x] All eight actions in `docs/audits/scientific-audit.md`, section 7; its status table gives each result

## M1 — make it a repository

- [x] Licence (MIT), `CONTRIBUTING.md`, `SECURITY.md`
- [x] Ruff lint and format, mypy, coverage; all clean
- [x] GitHub Actions workflow: lint, tests on the local stack, tests on the Docker stack

## M2 — MCP client matrix (hypothesis H3)

- [x] Repeat experiment 001 with stateful sessions and SSE responses
- [x] Add a true connection-drop variant of the K2 fault (TCP proxy that cuts the connection after the commit)
- [x] Write up as experiment 002 — `docs/experiments/002-mcp-client-matrix.md`. Result: 0 SDK-level resends in 720 trials
- [x] Run the same faults through other MCP clients (CrewAI, Microsoft agent-framework) — `docs/experiments/003-mcp-client-libraries.md`. Result: CrewAI's client resent on a late response and refunded twice, 20 of 20
- [x] Check whether the CrewAI behaviour is already reported: yes, publicly, as crewAI #8932 (fix PR #8933 open)
- [x] Stateful SSE server with an event store: does the client resume or replay after a drop? — `docs/experiments/007-mcp-resume.md` (plan: `docs/m2-plan.md`). Result: the reference client resumed and refunded once, even with a naive retry, only under `mode="legacy"` (protocol 2025-11-25) with an event store and a drop after the priming event; by default it negotiates 2026-07-28, which has no resumable streams, and refunded twice
- [ ] Cut the connection mid-stream and before the request arrives, not only after the commit

## M3 — first framework subject (hypothesis H1)

- [x] Read and quote LangGraph's durability documentation — "What the documentation says" in `docs/experiments/004-langgraph-crash-resume.md`. Every 004 behaviour is documented, except the `async` empty-`next` observation, which is unexplained
- [x] Scripted model: in-process, inside the LangGraph subject. Not yet the OpenAI-compatible server the architecture specifies, and no existing mock was evaluated first
- [x] Injector, first form: hard-kills a subject process on the host at two points. Not yet a container, and not yet K1 or K5
- [x] LangGraph + PostgreSQL saver subject; kills during and after the tool call — `docs/experiments/004-langgraph-crash-resume.md`
- [x] Close the P1 gaps in `docs/audits/implementation-gap-report.md`; 004 re-run with the shared checker, same counts
- [x] Remaining gaps in that report: G5, G7, G9–G11, and the end-of-run re-read from G4
- [ ] Attempt to replicate the crash-path result reported in arXiv 2608.03836

## M4 — durable-engine axis (hypothesis H2)

- [x] Investigation, feasibility probe and plan — `docs/m4-plan.md`. The probe supports H2; it is not a recorded result
- [x] pydantic-ai + DBOS subject and runner: plain function, `@DBOS.step` and child-workflow tools; four key strategies — `docs/experiments/005-pydantic-ai-dbos.md`. H2 held in two runs
- [x] Isolate trials from each other: one DBOS executor per trial
- [x] Second run of 005 (same day, not another day as designed; recorded as a deviation)
- [x] SQLite confirming pass and the step-retry question
- [x] One run of 005 on Linux in Docker (`docker compose --profile experiments run dbos-crash`): all 36 cells matched the Windows runs
- [x] Contract v0.2: the stricter `correct`, reviewed against every earlier result first — `docs/audits/contract-v0.2-review.md`
- [x] Contract v0.3: property RA, `report_matches_sink` beside every verdict; no verdict changed — `docs/audits/contract-v0.3-review.md`
- [x] Shared checker with `in_doubt_surfaced` and `stuck` verdicts — `src/crashtest/checker.py`
- [x] Result store in PostgreSQL — `src/crashtest/results/`; `python -m crashtest.results --local load`

## M5 — model behaviour (hypotheses H4, H5)

- [x] Pre-registered design and cost estimate — `docs/m5-plan.md`
- [x] Runner with a fake-client test suite — `src/crashtest/experiment/model_retry.py`
- [x] Pilot, then the real-model study: does the model call again after an unknown-outcome error, and do the arguments change? Two pilots and the 930-trial main run — `docs/experiments/006-model-retry.md`
- [x] Report per-model rates with exact confidence bounds — `docs/experiments/006-model-retry.md` and `docs/experiments/006-validation.md`
- [x] Validate against the sink's own rows — `docs/experiments/006-validation.md`
- [x] Compare the run's cost ledger with the provider's billing: $10.35, matched to the cent

## Later

- Scoreboard API and static site
- Nightly matrix across pinned and latest versions
- Live demo agent
- More subjects: OpenAI Agents SDK; Temporal and Restate
- Gateway fault scenarios on the same injector

## Before any public result about another project

- Share the reproduction with its maintainers first.
- Do not post in the upstream issue threads without results; when posting, state that this is your own project.
