# Implementation gap report

Audited 17 November 2026. The authority is [`docs/architecture.md`](../architecture.md) (the MVP design) with [`docs/contract.md`](../contract.md) for properties and verdicts, compared against the code at commit `fddc5b3` plus the uncommitted work listed in section 6.

No code was changed for this audit. All 40 tests and all results files are as they were.

## Status after the fixes of 17 November 2026

The audit below is kept as written. Since then:

| Gap | Status | Where |
|---|---|---|
| G1 shared checker | **Fixed.** One `classify` function, all five verdicts, used by all three runners and the test helper | `src/crashtest/checker.py`; `tests/test_checker.py` |
| G2 fault consumption | **Fixed.** Only a request that creates an effect consumes a fault; a replay or a conflict leaves it armed | `sink/app.py`; three new tests in `tests/test_sink.py` |
| G3 fault record | **Fixed.** `request_log.fault_applied`, returned by `GET /refunds`; every runner records `fault_confirmed` per faulted trial | `sink/schema.sql`; `trial.py`, `single_call.py`, `langgraph_crash.py` |
| G4 request accounting | **Fixed.** Signed `request_delta`; a second count of client requests at the proxy; ground truth read twice with a pause; and, since 17 November, every order re-read when the run ends, with `changed_by_end_of_run` set on any trial whose count moved | `trial.py`; `run.py` (`recheck_at_end`); `faults/drop_proxy.py`; test in `tests/test_mcp_refund.py` |
| G6 empty key | **Fixed.** Rejected with 422 | `sink/app.py`; test in `tests/test_sink.py` |
| G8 proxy matching | **Partly.** The byte scanner has unit tests for split reads and for a key that contains the order ID. The commit-wait timeout path and HTTP/2 are still untested | `tests/test_units.py` |
| G5 request log | **Closed by documentation.** `architecture.md` §4 now says "one row per request that reached a decision" and that a request rejected by validation is not logged | `docs/architecture.md` |
| G7 second fault mechanism | **Closed by documentation.** Recorded as a decision in `architecture.md` §3: the connection drop is armed in the runner's memory because the sink cannot cut a connection between two other parties | `docs/architecture.md` |
| G9 transport coverage | **Fixed.** Docker Compose serves all four transports (ports 8102–8105); the tests read the extra servers from `CRASHTEST_MCP_URL_<TRANSPORT>`, and the Docker CI job sets them | `docker-compose.yml`; `tests/conftest.py`; CI workflow |
| G10 results formats | **Fixed.** A result store loads every results file into `results.runs` and `results.trials`, with each experiment's factors in a common `cell` column. The files stay the record; a changed file is refused | `src/crashtest/results/`; `tests/test_results_store.py` |
| G11 smaller points | **Fixed.** `_free_port()` never hands out the same port twice in one process; `request_log.refund_id` links each request to its refund; a test pins down that a key reused for another order is a 409 logged under the new order, with the refund staying with the first | `local_stack.py`; `sink/schema.sql`, `sink/app.py`; `tests/test_sink.py`, `tests/test_units.py` |
| Section 7, stale documents | Updated: `architecture.md`, `contract.md`, `README.md`, `ROADMAP.md` | |

One decision made while fixing G1 needs confirming. The contract's table said `correct` is "effects = intended". With five verdicts that overlaps `in_doubt_surfaced` and `stuck`, so the contract now gives an order of precedence, and `correct` additionally requires the subject to have reported completion. No recorded trial in experiments 001 or 002 changes verdict under this rule, because in every one the client ended with a success.

Tests went from 40 to 70. All earlier results files are unchanged.

## Summary

- **The core design is implemented as specified.** The effect, the request log and the fault consumption commit in one transaction; deduplication is enforced by a partial unique index; a reused key with a different body returns 409; every documented endpoint exists.
- **The largest gap is the shared checker.** Verdict logic now exists in three places, disagrees with the contract's definition of `lost`, and two of the five verdicts are not implemented although crash scenarios now exist.
- **Fault consumption is looser than the design states.** Any request for an armed order consumes the fault, not only the one that commits an effect, and a consumed fault can be dropped without delaying anything.
- **Request accounting has three weaknesses** that could hide or misattribute a resend. None affects the published counts in experiments 001–003, for the reasons given under each.
- **The work has run ahead of the documents.** Experiment 003 and the LangGraph subject are not reflected in the architecture, contract or roadmap, and the roadmap's order was not followed.

Priorities: **P1** can produce a wrong verdict or a wrong published number. **P2** is a design inconsistency or untested assumption that does not affect current results. **P3** is documentation or housekeeping.

## 1. Conformance to architecture decisions

| Decision (architecture §3) | Status | Evidence |
|---|---|---|
| Truth lives in a separate service with its own database | Conforms | `src/crashtest/sink/app.py`; every runner reads `GET /refunds` |
| PostgreSQL; atomic claim with `ON CONFLICT DO NOTHING` on a partial unique index | Conforms | `app.py:105-111`; `schema.sql:16-18` |
| psycopg 3, raw SQL, no ORM | Conforms | `app.py` throughout |
| Effect and request log in one transaction | Conforms for requests that reach the handler; see G4 | `app.py:91-129` |
| Fault armed per order in the database, consumed by the request that commits | **Deviates**; see G2, G3 | `app.py:131-137` |
| K2 simulated by delaying the response | Conforms; a second mechanism now exists outside the sink; see G3 | `app.py:144-145`; `faults/drop_proxy.py` |
| Transport: streamable HTTP, stateless, JSON | Conforms as the default; three more settings added; see G11 | `refund_mcp/server.py` |
| One connection per attempt | Conforms | `trial.py`, `async with Client(mcp_url)` inside the loop |
| Key is an ordinary optional tool argument | Conforms | `server.py:issue_refund`; `tests/test_mcp_refund.py:16-22` |
| Same key with a different body returns 409 | Conforms | `app.py:119-123, 141-142`; `tests/test_sink.py:48-62` |
| Runs without Docker via `pgserver` | Conforms | `experiment/local_stack.py` |
| Sink starts uvicorn itself with a selector loop on Windows | Conforms | `sink/__main__.py:28-32` |

Interfaces (architecture §5): all six exist with the documented behaviour. `POST /control/reset` has no test and no caller (`app.py:186-191`).

Data model (architecture §4): tables and columns match `schema.sql`. One description is inaccurate; see G12.

## 2. P1 — can produce a wrong verdict or number

### G1. No shared checker; verdict logic is triplicated and does not match the contract

- **Design:** architecture §2 lists a "Checker: shared verdict logic for all subjects, including `in_doubt_surfaced` and `stuck`", extracted from `trial.py`. Contract §3 defines five verdicts.
- **Found:**
  - The same three-way expression is written out in `trial.py:227-231`, `langgraph_crash.py:179` and the test helper `tests/test_units.py:21`. `single_call.py` computes no verdict at all.
  - Contract §3 defines `lost` as "effects < intended **and the subject reported completion**". All three copies return `lost` whenever effects are below intended, whatever the subject reported.
  - `in_doubt_surfaced` and `stuck` are not implemented. Contract §3 says they "arrive with the crash scenarios"; `langgraph_crash.py` is a crash scenario and classifies a hung or failed resume with zero refunds as `lost`.
  - `intended = 1` is hard-coded in each copy.
- **Why it matters:** in experiments 002 and 003 a client that stops with an error after the effect committed is recorded as `correct`. That is right for property EO but says nothing about AS (ambiguity surfaced), which the contract says is checked. The crash experiment will misreport `stuck` runs as `lost`.
- **Next action:** add `src/crashtest/checker.py` with one function taking effects, intended, and the subject's final status, returning one of the five verdicts. Call it from all three runners. Do this before running the LangGraph experiment for record.

### G2. A fault is consumed by any request, and can be consumed without firing

- **Design:** "Armed per order in the database; consumed by the request that commits … The fault fires after the commit." `schema.sql:34-35` says `remaining` counts requests whose response is delayed "after the effect is committed".
- **Found:** `app.py:131-137` decrements `remaining` for every request that reaches that point, including a replay (no new effect) and a conflict. For a conflict the code raises 409 at `app.py:141-142`, before the delay at `144-145`, so the fault is consumed and nothing is delayed.
- **Why it has not affected results:** every experiment arms one fault and the first request for the order creates the effect.
- **Why it matters:** with `times` above 1, or a replay arriving first, the fault lands on a request that committed nothing, and the trial silently tests something else. There is no test for either case; `tests/test_sink.py:107-115` covers only two keyless requests in sequence.
- **Next action:** decide and document the rule ("consumed by any request" or "only by a request that creates an effect"), make the 409 path consistent with it, and add tests for fault-with-replay and fault-with-conflict.

### G3. Nothing records that a fault actually fired

- **Found:** `request_log` (`schema.sql:24-30`) has no column saying a request was delayed, and `GET /refunds` does not return it. Runners infer that the fault fired from the client's timeout.
- **Why it matters:** experiment 001 already met the failure this hides: an uninjected timeout under load produced a triple refund. A natural timeout and an injected one are indistinguishable in the results file. The connection-drop proxy does record each drop (`drop_proxy.py:103-104`), so the two fault mechanisms are held to different standards.
- **Related:** `run.py` writes `committed_before_drop` to the results file, but no runner excludes or flags a trial where it is false. It was true in all 240 drops of experiment 002.
- **Next action:** add a `fault_applied` flag to `request_log`, return it from `GET /refunds`, and have the runners assert it per faulted trial, as `tests/test_client_matrix.py:58` does for drops.

### G4. Request accounting can hide or misattribute a resend

Three separate weaknesses:

1. **Clamped subtraction.** `trial.py:242` computes `sdk_extra_requests = max(0, requests − attempts)`. An attempt that never reaches the sink and an extra request from the SDK cancel out to zero. In experiment 002 every faulted cell showed exactly 40 requests for 40 attempts, so no cancellation occurred there.
2. **Counting happens only at the sink.** There are two hops, client → MCP server → sink, and only the second is counted. A resend by the client that the MCP server did not forward, or a repeat by the MCP server, would be attributed wrongly. The MCP server's `httpx` call has no retry (`server.py:issue_refund`), which is why the attribution holds today; nothing tests that.
3. **Ground truth is read once, immediately.** `trial.py:220-224` reads the sink right after the retry loop. Its comment is correct for an injected fault, where the commit precedes the delay. For a timeout with no fault, the request may still be in flight and commit after the read. `single_call.py` waits one second first; `trial.py` and `langgraph_crash.py` do not.

- **Next action:** record signed `requests − attempts`; count `tools/call` requests at the drop proxy or the MCP server as a second measurement point; read the ground truth after a short settle and again at the end of the run, and flag any trial whose count changed.

### G5. "One row per request received" is not true

- **Design:** architecture §4, `request_log` — "one row per request received".
- **Found:** a request that fails validation is rejected before the handler runs (`app.py:82-86`) and is never logged; `tests/test_sink.py:118-122` checks that no effect exists but not the log. A request whose transaction fails is rolled back along with its log row (`app.py:91`).
- **Why it matters:** the log counts requests that committed, not requests received. For the current experiments these are the same, since every request is valid.
- **Next action:** either reword the design to "one row per request that reached a decision", or log rejected requests in a separate statement.

## 3. P2 — inconsistencies and untested assumptions

### G6. An empty `Idempotency-Key` is treated as a key

`app.py:85` accepts any header up to 200 characters with no minimum, and `app.py:93` tests `is None`. Every request sent with an empty key would deduplicate against every other. The MCP server guards against this (`if idempotency_key`), but subjects that call the sink directly, such as `subjects/langgraph_refund_agent.py`, are not protected. Add `min_length=1` and a test.

### G7. A second fault mechanism lives outside the sink and the database

The connection drop is armed in the memory of the runner process (`drop_proxy.py:45, 68`), not in the `faults` table, whose `mode` column still allows only `commit_then_delay` (`schema.sql:38`) and is never read (`app.py:132-135`). This is a reasonable design, since the sink cannot cut a connection between two other parties, but it contradicts "armed per order in the database". Record it as a decision in the architecture.

### G8. The drop proxy's target matching is untested at the unit level

`drop_proxy.py:88-95` finds its target by searching for the order ID in the client's bytes, keeping a 256-byte tail between reads. It assumes the ID appears only in the `tools/call` request and that the connection carries one request at a time. The proxy is covered only end to end. There is no test for an ID split across reads, for the commit-wait timeout path (`drop_proxy.py:125-131`), or for a client using HTTP/2.

### G9. Transport coverage differs between the two stacks

Docker Compose serves only the default transport, so the Docker CI job skips 12 of 40 tests. The other three transports are exercised only on the pip-installed PostgreSQL stack.

### G10. Three results formats and no result store

Architecture §2 lists a "Result store: PostgreSQL tables for runs and trials". Results are still JSON files, now with three unrelated schemas in `results/mcp_refund/`, `results/single_call/` and `results/langgraph_crash/`. Until the store exists, a shared envelope (experiment, versions, parameters, summary, trials) would keep them comparable.

### G11. Smaller points

- **`_free_port()` race:** `local_stack.py` picks a free port, closes it, then starts a server on it. It now does this five times per stack.
- **No link from a request to its effect:** `request_log` has no `refund_id`, so with no key a duplicate cannot be traced to the request that made it.
- **Key scope:** the unique index is on the key alone. The same key used for a different order gives a 409 logged under the new order, with the refund under the old one. Untested.

## 4. Missing components (architecture §2)

**Status note, 23 November 2026:** this section records the state at the audit. Since then the checker (`src/crashtest/checker.py`), the result store (`src/crashtest/results/`) and the pydantic-ai + DBOS subject (experiment 005) have been built, and the LangGraph subject was run (experiment 004). The scripted LLM is still in-process, and the injector still kills a host process.

| Piece | Status | Notes |
|---|---|---|
| Scripted LLM | **Different from design** | Specified as an "OpenAI-compatible server". Implemented as an in-process scripted model inside `subjects/langgraph_refund_agent.py`. It cannot be reused by a subject in another framework. |
| Injector | **Partly, and different** | Specified to run a subject "in a container" and kill at K1, K3, K4, K5. `langgraph_crash.py` kills a host process at two points named `during_tool` and `after_tool`. K1 and K5 are absent. The names do not map onto K1–K5. |
| Subjects | **One of two, unverified** | The LangGraph subject is written and has not been run. pydantic-ai + DBOS does not exist. |
| Checker | **Missing** | See G1. |
| Result store | **Missing** | See G10. |

## 5. Roadmap and scope conflicts

1. **Order.** Architecture §2 says the pieces are added "in order": scripted LLM, injector, subjects, checker, result store. A subject and a kill harness were written before the checker, which is how the verdict logic came to be copied a third time.
2. **M2 is recorded as open but is done.** `ROADMAP.md` still lists "Run the same faults through other MCP clients" as unchecked. Experiment 003 did it.
3. **M3 items skipped.** The roadmap's M3 begins with "Read and quote LangGraph's durability documentation" and "evaluate an existing mock before writing one". Neither is recorded. The durability modes were read from the installed package's docstrings, not the documentation. The replication of arXiv 2608.03836 has not been attempted.
4. **A result about another project.** Experiment 003 names a defect in CrewAI. Contract §7 rule 4 and the roadmap both require sharing the reproduction with maintainers before publishing. The repository is private, so nothing has been published; the write-up says so. (Note, 23 November 2026: the repository has since been made public; the CrewAI behaviour was already public as crewAI issue #8932.)

## 6. Uncommitted work at the time of the audit

`docs/experiments/003-mcp-client-libraries.md`, `results/single_call/`, `src/crashtest/experiment/single_call.py`, `src/crashtest/experiment/langgraph_crash.py`, `subjects/`. The last two have no tests. `single_call.py` and `langgraph_crash.py` pass Ruff's formatter and mypy; `langgraph_crash.py` has one over-length line.

## 7. Documents out of date (P3)

| Document | What is stale |
|---|---|
| `architecture.md` §1 | Diagram and component table omit the drop proxy, the single-call runner, the crash runner and `subjects/`. The runner is described as running "(fault, strategy)" cells; it now also iterates transports. |
| `architecture.md` §3 | "A true connection drop is a later variant" — it exists. |
| `architecture.md` §4 | `created_at` is described as "Commit time". `schema.sql:10` uses `DEFAULT now()`, which is the transaction's start time. |
| `contract.md` §4 | K2 is described as "not a process kill or a dropped socket". Both now exist in code. |
| `contract.md` §2, §3 | PC is "not exercised yet" and two verdicts "arrive with the crash scenarios". |
| `README.md` | "No agent framework is tested yet" needs qualifying once experiment 003 is committed. |

## 8. Recommended order of work

1. **G1** — extract the checker and use it in all three runners. Everything after depends on verdicts being right.
2. **G3** then **G2** — record fault application, then fix and test the consumption rule. Both are small changes to the sink and its schema.
3. **G4** — signed request difference, a settle before the ground-truth read, and a second counting point.
4. **G6** — one-line validation fix with a test.
5. **Commit experiment 003** with the roadmap and document updates from sections 5 and 7.
6. **Then** run the LangGraph experiment, so its first recorded results use the shared checker.
7. **G5, G7–G11** as they come up.

Items 1–4 change working code. They were not made as part of this audit.
