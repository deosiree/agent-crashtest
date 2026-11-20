# Scientific audit of experiments 001–004

Written 17 November 2026 against commit `5320918`. This audit looks for reasons the results might be wrong, weaker than stated, or not reproducible. It is deliberately adversarial. It changes no code, results or write-ups.

It complements the [implementation gap report](implementation-gap-report.md), which compared the code with the architecture. This one asks whether the conclusions follow from the evidence.

## Status of the actions

| Action | Status |
|---|---|
| 2. Self-describing results | **Done, 17 November 2026.** Lock files for every environment; every runner records the git commit, whether the tree was clean, and the subject packages, taken when the run starts. |
| 3. Hypothesis statements | **Done.** 002, 003 and 004 reworded as section 4 asks; the README's "First result" now mentions the triple refund. |
| 7. Write the design down first | **Done for M4 and M5.** `docs/m4-plan.md` section 4; `docs/m5-plan.md` section 4. |
| 8. Trace how a CrewAI agent reaches MCP tools | **Done, from the source.** Both agent paths (`MCPNativeTool` via `MCPClient._retry_operation`, and `MCPToolWrapper`) retry a dispatched `tools/call`. Not run end to end through an `Agent`. The behaviour is already public as crewAI #8932. |
| 1. Re-run 001–003 | **Done, 17 November 2026.** 001, 002 and 003 (main matrix and the CrewAI default-settings case) re-run on the current harness: every cell matched the original runs. The one difference is that 001's unplanned triple refund did not recur. |
| 4. Control the kill timing in 004 | **Done, 17 November 2026.** A pre-registered sweep of nine delays: under `async`, an empty `next` in 9 of 10 trials killed at once and 0 of 80 killed 10 ms or more later; under `sync`, 0 of 90. The observation is a sub-10 ms window, not a general property. See the 004 write-up. |
| 5. Run on Linux | **Done, 18 November 2026.** 005: one full run in Docker matched Windows in every cell. 004: one full run on a GitHub Ubuntu machine; 26 of 27 cells matched, and the 27th changed a conclusion (under `async`, a kill just after the tool refunded twice without a key; see the 004 write-up). 003: one full run on a GitHub Ubuntu machine; all 18 cells matched. |
| 6. Vary the timeout ratio | **Done, 18 November 2026.** A pre-registered sweep at 0.5, 0.9, 1.1, 2 and 4 times the client's timeout, on GitHub Ubuntu machines: 001 (three strategies, 300 trials) and 003 (three clients, 150 trials). Every cell matched the prediction: no duplicate below the timeout; above it, no key, a new key per attempt and CrewAI refunded twice in every trial, and a stable key, `mcp` and agent-framework once. Ratios between 0.9 and 1.1 not tested. See the 001 and 003 write-ups. |

## Verdict in brief

- **The central qualitative findings are well supported:** a retry after an ambiguous outcome duplicates a non-idempotent effect; a stable key enforced by the database prevents it; CrewAI's MCP client retries tool calls on a timeout; a resumed LangGraph agent re-runs an interrupted tool.
- **The numbers carry less weight than they appear to.** Every faulted cell is a deterministic repeat, so "20 of 20" is one observation made twenty times, not twenty independent ones.
- **Everything was measured on one machine, one operating system, on one day.**
- **Three specific conclusions are stated more strongly than the evidence allows** (section 4).
- **A stranger could not reproduce the exact results today**: there is no lock file, the results files do not record the commit that produced them, and the recorded commands now run a different code path than the one that produced experiments 001–003.

Severity: **S1** could change a stated conclusion. **S2** limits how far a conclusion can be trusted or generalised. **S3** is good practice not yet followed.

## 1. Statistical validity

### 1.1 Trials within a cell are not independent (S1)

Each cell runs the same code against the same fault with the same timing. Nothing varies between trials except the order ID. Twenty identical outcomes therefore show that the harness is repeatable, which is what the write-ups say in their limits sections. But the headline sentences use the counts as evidence strength: "20 of 20", "0 extra requests in 720 trials", "30 of 30".

The honest sample size for each finding is the number of *distinct conditions* tested:

| Finding | Trials | Distinct conditions actually varied |
|---|---|---|
| 001: naive retry duplicates | 360 across three runs | 2 timeout settings, 1 transport, 1 machine |
| 002: no SDK-level resend | 720 | 4 transports × 2 fault forms, 1 client version, 1 machine |
| 003: CrewAI resends on a late response | 23, plus 2 in the re-check | 2 transports, 2 timeout settings, 1 version |
| 004: resume re-runs the tool | 270 across two runs | 3 durability modes × 2 kill points, 1 version |

**Action:** lead each write-up with the conditions varied, and give the trial count as a repeatability figure.

### 1.2 What the counts could support if the trials were independent (S2)

If they were independent draws, the exact 95% lower bounds on the true rate would be:

| Observed | 95% lower bound on the true rate |
|---|---|
| 3 of 3 | 29% |
| 5 of 5 | 48% |
| 10 of 10 | 69% |
| 20 of 20 | 83% |
| 30 of 30 | 88% |
| 80 of 80 | 96% |

And for the two results that are not all-or-nothing:

| Observed | 95% interval |
|---|---|
| 0 resends in 720 | true rate below 0.51% |
| 26 of 30 (`async` state looked finished) | 70% to 95% |

So even on the most generous reading, "5 of 5" in a 004 cell cannot exclude a true rate near one half, and the 3 trials of CrewAI with default settings cannot exclude a rate below one third. No write-up reports an interval.

### 1.3 No pre-stated design (S3)

Trial counts (20, 10, 5, 3) were chosen for run time. No hypothesis, expected outcome or stopping rule was written down before any run. Experiment 003's default-settings check was added after the main result was seen. None of this invalidates a deterministic result, but it is the pattern that produces false positives once outcomes become probabilistic, which they will in M5.

### 1.4 The one probabilistic result is uncontrolled (S1)

Experiment 004 reports that under `async` durability the saved state listed no pending step in 13 of 15 trials per run. This is a race between LangGraph's background checkpoint write and the harness's kill. The harness kills as soon as it reads a marker line from the subject's output, so the interval between "tool returned" and "process dead" is whatever the pipe and the operating system happen to take. That interval was neither controlled nor measured.

The figure therefore describes this harness on this machine. On a faster or slower kill it could be 0 of 15 or 15 of 15. The write-up says "this depends on timing" but still reports the fraction as a finding.

**Action:** sweep a controlled delay between the marker and the kill, and report the outcome as a function of that delay.

## 2. Methodology

### 2.1 One machine, one operating system, one day (S2)

All recorded results come from one Windows 11 machine on 16 November 2026. Three things are Windows-specific:

- **The kill.** `proc.kill()` is `TerminateProcess`. On Linux it is `SIGKILL`. Both are abrupt, but buffered socket and file behaviour at death differs.
- **Loopback networking.** The connection-drop proxy aborts a loopback TCP connection. Whether the client sees a reset or a clean close can differ by platform, and that decides which exception the client raises.
- **The event loop.** The sink runs on a selector loop on Windows only.

There is one mitigating fact the write-ups do not mention: the test suite runs on Ubuntu in CI on every push, and it includes single-trial versions of the 001 and 002 cells on all four transports. Those pass, so the qualitative outcome of 001 and 002 has been reproduced on Linux. Experiments 003 and 004 have no such coverage.

### 2.2 Fixed cell order, shared state (S2)

Runners iterate transport, then fault, then strategy, in a fixed order, with all trials of a cell back to back. Nothing is randomised or interleaved. Any drift over a run (a warming connection pool, a growing database, background load) is confounded with the cell. The database is never reset between runs; refund IDs in the results are in the thousands.

The uninjected triple refund in experiment 001 is direct evidence that background load changes outcomes on this machine.

### 2.3 Timing parameters were chosen to make the fault certain (S2)

The response is withheld for 5 s against a 2 s timeout, or 8 s against 2 s. These margins were widened after the first runs specifically to remove variability. The experiments therefore show what happens when a response is definitely late. They say nothing about the region where the delay is close to the timeout, which is where real systems live and where experiment 001's accident happened.

### 2.4 Experiments 001–003 were not re-run after the harness changed (S1)

The P1 fixes changed the measurement path: trials now run through the proxy, the sink consumes faults under a different rule, and each trial records `fault_confirmed`. Only experiment 004 was fully re-run afterwards. For 001, 002 and the main run of 003:

- **Whether the fault fired was inferred, not recorded.** The results predate `fault_applied`. The inference is strong (the client timed out at the configured time and the sink saw two requests) but it is an inference, and experiment 001 already showed a timeout that was not caused by the fault.
- **The 003 re-check was 2 trials per cell on one transport.**

**Action:** re-run 001–003 on the current harness and confirm the counts match. This is about 30 minutes of machine time.

### 2.5 The proxy is now always in the path (S2)

Routing every trial through a TCP proxy adds a hop and changes connection behaviour. A test shows the proxy is transparent for a normal call. Nothing shows it is transparent for a *timed-out* call, where the client abandons a connection the proxy is still holding open to the server.

### 2.6 Experiment 004's design choices drive part of its result (S1)

Three choices made by the harness, not by LangGraph, shape the outcome:

1. **The scripted model issues a new tool-call ID on every call.** The conclusion that a tool-call-ID key fails under `exit` durability depends entirely on this. It is how hosted model APIs behave, but that was assumed, not measured, and a model run with a fixed seed or a cached response would behave differently.
2. **With no checkpoint, the subject resubmits the original request.** Every `exit` duplicate follows from this policy. Any system with no saved state that resubmits will repeat its effects; that is close to a tautology and is not specific to LangGraph.
3. **The "after tool" kill is produced by making the model sleep.** That is not one of the contract's fault points. Under `sync` it lands after the checkpoint write (K4); under `async` it lands on either side of it.

### 2.7 Experiment 003 tested a client object, not an agent (S2)

`MCPClient` was constructed and called directly, with `execution_timeout` set to 2 s. The write-up says so. Two further points it does not make:

- **CrewAI bundles mcp 1.30.0; the server runs mcp 2.3.0.** The mixed-version pairing was not varied, so a version-interaction effect cannot be ruled out for the connection-drop behaviour.
- **How a CrewAI agent reaches MCP tools was not traced.** CrewAI has three MCP code paths with separate retry constants. If agents use a different one, the measured path may not be the one users hit.

## 3. Measurement

| Weakness | Severity | Detail |
|---|---|---|
| The sink is trusted without independent verification | S2 | Ground truth is one service's count. It has a 25-way concurrency test, but no experiment cross-checks it against the database directly. |
| Sink and subject share one PostgreSQL instance in 004 | S2 | The LangGraph checkpointer and the sink use the same server. Load from one can change the other's timing. |
| Requests are counted per order, not per attempt | S2 | With no key, a duplicate cannot be tied to the attempt that caused it. Open as gap G11. |
| Client belief is recorded coarsely | S2 | `client_saw_success` is a boolean. For 003 and 004 it is derived from "the script exited without raising". |
| No measurement of time | S3 | No write-up reports how long the ambiguous window was, though the data is in the files. |
| Three results formats | S3 | Open as gap G10. Older files lack the fields added later. |

## 4. Conclusions that outrun the evidence

### 4.1 "This supports hypothesis H3 for CrewAI" (003)

H3 as written in `evidence-check.md` is: "An MCP client that retries `tools/call` after a lost response executes the tool more than once." That is nearly true by definition. What 003 actually established is narrower and more useful: **CrewAI's client does retry `tools/call` on a timeout, by default.** The write-up should claim that, and not claim support for a hypothesis that could not have failed.

### 4.2 "A negative result for hypothesis H3" (002)

Experiment 002 found that the reference client does not resend. H3 is conditional on a client that retries, so a client that does not retry neither supports nor refutes it. The accurate statement is: the reference client is not a retrying client.

### 4.3 Experiment 004 does not state what it means for H1

H1 says LangGraph "re-executes a completed tool after a crash at K3, and the outcome under `durability="sync"` varies with scheduling". Experiment 004 found the opposite at its nearest kill point: under `sync`, a kill after the tool returned gave one refund in 30 of 30 across the two runs, with no variation. The write-up never mentions H1.

This is not a refutation, because the kill point is not K3 (section 2.6) and CPU limits were not varied as H1's test design requires. But a reader should be told that the first evidence gathered does not support H1.

### 4.4 Smaller overstatements

- **002, "A stateful session with SSE did not resume or replay the call."** True, but the server had no event store, so the client had nothing to resume from. The write-up says this in its limits. It should not be listed under "What it shows".
- **003, "CrewAI with nothing configured".** Three trials on one transport.
- **README, "First result".** The table gives counts for one run; the text says it ran three times. The triple-refund run is not mentioned there.

## 5. Reproducibility

| Weakness | Severity | Detail |
|---|---|---|
| No lock file | S1 | `pyproject.toml` pins only `mcp==2.3.0`. FastAPI, httpx, psycopg and uvicorn are lower bounds. The subject environments (CrewAI, agent-framework, LangGraph) were installed unpinned and exist only on one machine's D: drive. |
| Results do not record the commit | S1 | No results file contains a git hash. The three single-call files record no library versions at the top level at all, only inside each trial's `client` string. |
| Recorded commands no longer reproduce the recorded path | S1 | The "Reproduce" command in 001 and 002 now runs through the proxy and writes a different schema from the files it is documented beside. |
| Subject environments are described in prose | S2 | "Create one virtual environment per library" with a package list, no versions, no script. agent-framework needed two undeclared extras (`mcp`, `tenacity`) found by trial and error; only the write-up records this. |
| Experiments are not in CI | S2 | CI runs tests, not experiments. A regression that changed a result would pass CI if the single-trial tests still passed. |
| No seed | S3 | Nothing is seeded. Currently harmless, since nothing is random except IDs. |

**Action:** commit a lock file for the project and a pinned requirements file per subject; record the git hash and full `pip freeze` in every results file; add a script that builds the subject environments.

## 6. What is solid

So that the weaknesses are read in proportion:

- **Ground truth is external to the subject.** Counting effects in a separate service's database, not in the subject's own memory, is the right design and the one the closest prior work lacks.
- **Every experiment has a positive and a negative control**, and the controls behaved as predicted every time.
- **The harness's own failure was kept and explained.** The triple-refund run is in the results folder.
- **The limits sections are honest and specific.** Most weaknesses above are sharper versions of something a write-up already admits.
- **The mechanism is identified in source for the two findings about other projects**: CrewAI's retry loop by file and line, and LangGraph's durability modes from its own type definitions.
- **The P1 measurement gaps are closed**, with tests, and 004 was re-run and matched.
- **Deduplication correctness is tested under concurrency**, not just sequentially.

## 7. Recommended actions, in order

1. **Re-run experiments 001–003 on the current harness** and confirm the counts (section 2.4). Cheap, and it removes the largest S1 item.
2. **Make results self-describing**: git hash, `pip freeze`, and a lock file (section 5).
3. **Correct the three hypothesis statements** in the write-ups (section 4). Wording only.
4. **Control the kill timing in 004** and report the `async` observation as a function of delay (section 1.4).
5. **Run 003 and 004 on Linux**, in a container or in CI (section 2.1).
6. **Randomise cell order and vary the timeout-to-delay ratio** (sections 2.2, 2.3).
7. **Write the design down before each new experiment**: hypothesis, conditions, trial count, what would count as a failure (section 1.3). Start with M4.
8. **Trace how a CrewAI agent reaches MCP tools** before reporting upstream (section 2.7).
