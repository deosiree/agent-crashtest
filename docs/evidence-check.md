# Evidence check: the cited issues, re-read in full

Task 2 of the roadmap. Checked on 16–17 November 2026.

How this was read: each issue page was fetched through a summarising tool. GitHub collapses long threads, so on four of the eight issues part of the thread was hidden (noted per issue). Author roles are not always shown, so "no maintainer comment" means none was visible. Nothing was reproduced.

---

## 1. Verdict

**The problem is real and unresolved, but the evidence is weaker and noisier than the main report said.** Three corrections matter:

- **"Five ecosystems independently" is overstated.** Solid evidence exists in three places (LangGraph #8039, Microsoft agent-framework, the MCP spec issue) plus one well-specified CrewAI report. One cited issue is not about this problem at all, one concerns a hosted service, and one was filed by someone promoting their own fix.
- **Maintainers are mostly silent.** Only Microsoft's agent-framework and pydantic-ai maintainers have engaged. LangGraph, CrewAI and the MCP spec threads show no visible maintainer reply.
- **The threads are saturated with people promoting idempotency libraries.** I counted at least 15 named products across the eight threads. Another ledger would be one more; a neutral measurement is what nobody there has.

The project premise survives. The narrowed scope from [related-work.md](related-work.md) is reinforced, and the threads supply two concrete, testable hypotheses (section 4).

---

## 2. Status of each issue

| Issue | State | Maintainer position | Quality of evidence | Verdict |
|---|---|---|---|---|
| [LangGraph #8039](https://github.com/langchain-ai/langgraph/issues/8039) — persistence order unenforced under `durability="sync"` | Open, unassigned, labelled `bug` | None visible. A maintainer marked #8234 as a duplicate of it | **Strong.** Probe script; outcome differs by host (1 vCPU replays, 16 threads duplicates); a commenter reports 15 of 20 runs duplicating on 1.2.16 | Keep as primary evidence |
| [agent-framework #9204](https://github.com/microsoft/agent-framework/issues/9204) — MCP retry duplicates side effects | Open, assigned, labelled `reproduced` | Engaged. Work directed to PR #9243; agreed direction is no automatic replay and an unknown-outcome error | **Strong**, though the reproduction is mock-based, not a live server | Keep; may be fixed soon |
| [agent-framework #3938](https://github.com/microsoft/agent-framework/issues/3938) — tool replay on checkpoint retry | Open, labelled `question`, backlog | "Idempotency should be implemented at the executor layer"; mid-superstep checkpoints only "considered" | **Good.** Community repros in Python and .NET | Keep; note it is by design for now |
| [MCP spec #3394](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/3394) — lost-response retry re-executes effects | Open, no labels | None visible | **Good.** Two independent reproductions reported; 27 comments hidden | Keep |
| [crewAI #8932](https://github.com/crewAIInc/crewAI/issues/8932) — MCP calls re-sent up to 3 times after timeout | Open, no labels | None visible | **Good.** Exact versions, runnable repro, fix in open PR #8933. Reporter flags the issue as LLM-generated | Keep |
| [crewAI #5802](https://github.com/crewAIInc/crewAI/issues/5802) — no idempotency guard on tool retry | Open; `bug` label added by the author | None visible | **Weak.** Versions given as "latest"; conceptual repro; the author promotes their own idempotency package; ~208 entries hidden | Demote to supporting |
| [LangGraph #7417](https://github.com/langchain-ai/langgraph/issues/7417) — long tool calls re-executed | Open | None visible; the reporter says none in five months and has left the platform | **Off-target.** It concerns the hosted LangGraph Cloud scheduler's stale-run sweep, not the open-source library. One commenter reports a local process-kill duplicate on 1.2.11 | Remove as primary evidence |
| [pydantic-ai #7955](https://github.com/pydantic/pydantic-ai/issues/7955) — tool-level `idempotent` / `side_effect_free` declarations | Open, assigned | Engaged. A maintainer proposes one typed `ToolAnnotations` shape with an MCP mapping | **Miscited.** It is a feature proposal about tool annotations for speculative execution, not a report of duplicated effects | Remove from the evidence list; relevant only as design context |

Also checked:

- **MCP `idempotencyKey` proposal (PR #3182).** The #3394 author states it was closed in August 2026 on policy grounds. Not verified from the PR itself.
- **LangGraph #6208**, cited by the ACRFence paper as maintainer acknowledgment. It is a maintainer-opened issue about nodes with multiple interrupts re-running, still open. The paper's quoted remark was not found in it.

---

## 3. What the threads show beyond status

### 3.1 The fix is being pitched from all sides

Named tools promoted in these threads include SafeAgent, Once, idemkit, PicoMUTX, AgentShield, onceonly, AER-1, agentsafe, Inferrail, tooltrace-audit, SeenRelay, reality-layer, HumanMirror, stateguard and agentsignal. Several are posted repeatedly across repositories by the same accounts.

Consequences for this project:

- **Do not lead with a ledger.** It would read as the sixteenth pitch.
- **Do not post in these threads yet.** A comment without results looks like the others. When there are results, post one comment per thread with a reproduction, state plainly that it is your own project, and stop.
- **Never post unreviewed AI-written comments.** Some existing comments are tagged as AI-generated and other participants ask for their claims to be re-run before being trusted.

### 3.2 Fix pull requests are not a realistic contribution path on LangGraph

Two external fix PRs for #8039 (#8055, #8050) were closed by a bot within about 20 seconds because their authors were not assigned to the issue. The realistic contribution there is reproduction evidence, not code.

Better contribution targets, on this evidence: Microsoft agent-framework (maintainers triage and respond) and pydantic-ai (maintainers engage on design).

### 3.3 Durable engines do not cover custom tools by default

From the [pydantic-ai DBOS page](https://pydantic.dev/docs/ai/integrations/durable_execution/dbos/):

- Model requests and MCP calls run as DBOS steps.
- Function tools are **not** wrapped automatically; the user must add `@DBOS.step` to any tool that does I/O.
- A run is durable only when `agent.run()` is called inside the user's own `@DBOS.workflow`; without it the run works but is not durable.
- A failed step "restarts from the beginning"; the page recommends keeping side effects idempotent. It never states exactly-once or at-least-once.

The pydantic-ai overview lists nine engines (Temporal, DBOS, Prefect, Restate, AWS Lambda, Absurd, pgtask, Azure Durable Task, Apache Airflow) and says which tool calls get wrapped differs by engine.

This is the strongest argument for the backend axis: "add a durable engine" does not by itself protect a tool, and the gap depends on configuration a user can easily get wrong.

---

## 4. Hypotheses to test (replacing the vaguer claims)

| # | Hypothesis | Source | How the harness tests it |
|---|---|---|---|
| H1 | LangGraph with a PostgreSQL saver re-executes a completed tool after a crash at K3, and the outcome under `durability="sync"` varies with scheduling | #8039; "Resume Means Resume" | Kill at K3 across N seeds and two CPU limits; count effects at the sink |
| H2 | pydantic-ai with DBOS duplicates an effect when the tool is a plain function, and does not when it is a `@DBOS.step` — except in the window between effect and step checkpoint | DBOS integration page | Same scenario, two tool configurations, kills at K2 and K3 |
| H3 | An MCP client that retries `tools/call` after a lost response executes the tool more than once | crewAI #8932; agent-framework #9204; MCP #3394 | Sink commits then drops the response; count effects per client and version |
| H4 | After an "unknown outcome" error, a model re-issues the call on its own, so removing transport retries is not sufficient | Commenters on agent-framework #9204 and MCP #3394 | Real-model study: return an unknown-outcome error, record whether and how the model calls again |
| H5 | When a model re-issues a call, its arguments differ often enough to defeat argument-hash keys | ACRFence | Same study: compare original and re-issued arguments |

H4 was not in the original plan. It is cheap to add to the drift study and is the one question raised repeatedly in the threads that nobody has answered with data.

---

## 5. Corrections to the main report

1. Section 1 and gap G1: replace "five ecosystems independently" with "LangGraph, Microsoft agent-framework, CrewAI's MCP client and the MCP specification".
2. Remove pydantic-ai #7955 and LangGraph #7417 from the evidence for G1.
3. Section 14: drop the expectation of merged upstream fixes on LangGraph; target reproductions accepted by agent-framework or pydantic-ai maintainers instead.
4. Roadmap: the first durable-engine subject is pydantic-ai with DBOS, in both tool configurations (H2).

---

## 6. Not verified

- ~~**LangGraph's own durable-execution documentation.**~~ Read on 17 November 2026 from the documentation's source; see "What the documentation says" in [experiment 004](experiments/004-langgraph-crash-resume.md). It documents re-running unfinished work on resume and advises idempotency keys for side effects.
- The hidden portions of four threads (LangGraph #7417 and #8039, crewAI #5802, MCP #3394), where a maintainer reply could exist.
- The state of the fix PRs (agent-framework #9243, crewAI #8933 and #6048).
- The claim in MCP #3394 that none of 266 non-read-only tools across 24 servers accept an idempotency key.
- Temporal, Restate and the other engine pages; only the DBOS page was read.
