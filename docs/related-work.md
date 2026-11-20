# Related work and the extend-or-build decision

Task 1 of the roadmap. Checked on 16 November 2026.

How the sources were read: paper HTML and repository pages were fetched through a summarising tool, so wording and numbers below are as that tool extracted them. The first paper was read to roughly 93% of its text; its results table was truncated in one extract. Nothing here was run. Re-check any figure against the source before quoting it publicly.

---

## 1. Decision

**Build a new, narrower harness; do not fork either existing one. Adopt their vocabulary, replicate their findings, and cover the four things neither covers.**

The overlap is larger than the red-team review estimated. Both closest works have public code:

- The "Resume Means Resume" harness is public (the earlier review could not find it).
- UndoBench is public under Apache 2.0 and already compares seven recovery strategies, including idempotency keys.

That removes "first open crash harness for agent frameworks" as a claim. What remains uncovered, and becomes the project's scope:

| Gap | Resume Means Resume | UndoBench | ACRFence |
|---|---|---|---|
| Durable engines as a test axis (Temporal, DBOS, Restate) | Cited as related work; a head-to-head is explicitly scoped out | Not mentioned | — |
| MCP client retry and lost-response behaviour | Not mentioned | Not mentioned | Uses MCP servers as simulated services only |
| Measured rate of LLM argument drift on resume | LLM-free by design | Not reported on the pages read | Describes the effect; reports no rate |
| Effects observed at a network service, under process kill | Process-local counter plus on-disk SQLite ledger; the paper lists this as a validity threat | Wire-level oracle, but transport faults, not process kills (kill mechanism not stated) | — |
| Continuous tracking across releases with a public matrix | One-off sweep of five LangGraph versions | Submission pipeline; no live leaderboard shown | — |
| Microsoft agent-framework, OpenAI Agents SDK | Not covered (OpenAI Agents SDK listed as future work) | Two frameworks, unnamed on the pages read | — |

Why not fork:

- **`resume-contract` has no licence file**, so its code cannot legally be reused or redistributed. It is a single-author research artifact with 0 stars, 60 commits and 18 open pull requests.
- **UndoBench answers a different question** — whether an LLM agent recovers a task — and its unit is a workflow with a real or deterministic agent, not a framework's persistence layer under process kill.

Revised one-line positioning:

> Does adding a durable engine or an MCP layer actually stop an agent repeating a side effect after a crash — and how often does the model change the arguments when it retries?

This is the question a practitioner choosing a stack asks, and none of the three works answers it.

---

## 2. The three prior works

### 2.1 Resume Means Resume (arXiv 2608.03836)

- **Source:** [abstract](https://arxiv.org/abs/2608.03836), [HTML v3](https://arxiv.org/html/2608.03836v3). Single author (Sajjad Khan). Submitted 4 August 2026; v3 on 8 August 2026. Paper licence CC BY 4.0.
- **Code:** [sajjadanwar0/resume-contract](https://github.com/sajjadanwar0/resume-contract) — public, last updated 14 November 2026, no licence stated. The repository URLs printed on the PyPI page (`remit-contract`, `resume-contract-paper`) return 404.
- **Package:** [`remit-contract` 0.1.2 on PyPI](https://pypi.org/project/remit-contract/), MIT, released 31 July 2026. Rust core with Python bindings; LangGraph only; prebuilt wheels for x86_64 Linux only.

The contract (six properties):

| Code | Property | Meaning |
|---|---|---|
| PC | Prefix continuation | Recovery continues from the recorded state; replayed prefix effects are served from the record |
| EO | Effect exactly-once | Each task's effect fires at most once per branch across interrupts, crashes and resumes |
| FD | Fork determinism | Resumes with fork intent to the same checkpoint each get their own outcome |
| CV | Checkpoint validity | Schema-invalid state is rejected, not stored |
| CO | Consume-once | An interrupt is consumed by at most one resume; redelivery has no effect |
| RD | Recovery determinism | Identical durable logs give identical recovery decisions |

What was tested:

- LangGraph 1.2.9 with in-memory, SQLite and PostgreSQL savers (plus versions 1.0.5, 1.1.0, 1.1.3, 1.1.10).
- LlamaIndex Workflows 2.22.2, CrewAI 1.15.2, pydantic-graph 1.107.1, AutoGen AgentChat 0.7.5.

Method:

- Pure-Python tasks, no model calls.
- Crash injection is mostly exception-based; specific probes use a real SIGKILL synchronised by a filesystem barrier.
- Each framework runs in its own pinned environment; probes are numbered 113–174 and registered in `matrix.toml`.
- Run with `./reproduce.sh` or `uv run`. No Docker. The README gives a recipe for adding a framework.

Findings as reported:

- **LangGraph:** exactly-once holds across interrupts but fails across crashes — a completed task whose write is durable is re-run on crash-resume, on both SQLite and PostgreSQL.
- **CrewAI:** a completed method re-runs after restore; the final state looks correct, so the duplicate shows only in the external-effect ledger.
- **pydantic-graph:** cannot resume after a mid-node crash, so nothing duplicates and nothing progresses.
- **Concurrent resume:** two processes resuming one interrupt fired the effect twice in 10 of 10 runs on SQLite and PostgreSQL.
- **Remit:** an opt-in gate moved that cell to one fire in 10 of 10 runs; reported overhead within 5% of stock.

Limits the paper states:

- The effect oracle is a local counter and ledger, "not a real payment API".
- Five frameworks, three in depth; prevalence is not measured.
- Fail-stop crashes only.
- Future work: AG2, OpenAI Agents SDK, Claude Agent SDK, Agno; a production-scale engine head-to-head is scoped out.

Related upstream issues it cites: LangGraph [#8039](https://github.com/langchain-ai/langgraph/issues/8039), #6663, #6491.

### 2.2 UndoBench (arXiv 2610.05622)

- **Source:** [abstract](https://arxiv.org/abs/2610.05622). Four authors. Submitted 4 October 2026.
- **Code:** [tradertanmay/undobench](https://github.com/tradertanmay/undobench) — 3 stars, 7 commits. Software Apache 2.0; benchmark data CC BY 4.0. Only the abstract and repository page were read, not the paper body.

What it is:

- 36 multi-turn workflows across 8 enterprise domains, each run as a paired nominal and faulted trial with identical seeds.
- Faults are injected at the transport layer through a proxy: network drops, lost acknowledgments, database timeouts, partial API failures.
- Seven recovery baselines: naive retry, checkpoint/rollback, idempotency keys, sagas, LangGraph native, journaling, verify-before-retry.
- Works with real models through OpenAI-compatible endpoints, or a deterministic agent with no API key.

Headline figures from the abstract: nominal competence 83.54%; conditional recovery success 46.72%; naive retry duplicated external effects in 53.33% of trials.

Finding most relevant here: safety depends on *when* the fault lands — before mutation, during partial mutation, or after commit but before acknowledgment. Server-side idempotency and verification help most in the last phase.

### 2.3 ACRFence (arXiv 2603.20625)

- **Source:** [abstract](https://arxiv.org/abs/2603.20625), [HTML](https://arxiv.org/html/2603.20625). Four authors. Submitted 21 March 2026; workshop paper. No code link.
- **Claim:** after a checkpoint restore, an agent re-issues an irreversible action with freshly generated identifiers, so the server sees a new request ("Action Replay"); a consumed single-use token reappears in rolled-back state ("Authority Resurrection").
- **Evidence:** one setup (Claude Code CLI with Qwen3-32B, services simulated as MCP servers); 10 of 10 restore trials produced duplicate commits versus 0 of 10 without restore.
- **Proposal:** a proxy at the tool boundary that logs irreversible calls and uses an analyser LLM to decide whether a new call is equivalent to a logged one. **Not implemented or evaluated** — the paper says so.
- **What it leaves open:** no rate of argument divergence, one model, no deterministic key scheme.
- **Maintainer acknowledgment it cites:** [LangGraph #6208](https://github.com/langchain-ai/langgraph/issues/6208), opened by a maintainer in September 2025 and still open. The quoted remark that the problem is "architecturally difficult to fix" does not appear in the issue as fetched; treat that quote as unverified.

---

## 3. What to adopt from each

| From | Adopt | How |
|---|---|---|
| Resume Means Resume | Property names EO, PC and RD, and the distinction between interrupt-resume and crash-resume | Use the same terms in `docs/contract.md`; cite the paper as the source |
| Resume Means Resume | Its LangGraph and CrewAI crash findings | Replication targets on the versions it names; report agree / disagree |
| Resume Means Resume | `remit-contract` (MIT) | Include as a mitigation column for LangGraph next to the reference ledger |
| UndoBench | The three fault phases | Map onto kill points: K1 = before mutation, K5 = partial mutation, K2 = after commit before acknowledgment |
| UndoBench | Verify-before-retry and server-side idempotency as baselines | Add as sink modes and mitigation columns |
| ACRFence | The argument-drift hypothesis | The drift study measures the rate it did not report, across several models |

---

## 4. Changes to the plan

1. **Scope narrows to four contributions:** the durable-engine axis, MCP retry semantics, the argument-drift study, and continuous tracking with a network-visible sink.
2. **First subjects change.** Start with LangGraph + PostgreSQL (for replication) and one durable-engine configuration (pydantic-ai with DBOS or Temporal), not two bare frameworks. The backend comparison is the differentiator, so it must be in the MVP.
3. **The reference ledger shrinks further.** `remit-contract` and UndoBench's idempotency baseline already show the fix; the ledger is one mitigation column among several.
4. **Originality score drops.** In the main report's table, idea 1 falls from 6 to about 4 out of 10 on originality (79 to about 77 overall). It remains the top-ranked idea, and no other candidate was researched to this depth.
5. **Two courtesy actions before publishing anything:** open an issue asking the `resume-contract` author about a licence, and tell both author groups about any replication result that disagrees with theirs.

---

## 5. Still unverified

- Whether UndoBench's proxy can kill processes, and which two frameworks its paper evaluated (paper body not read).
- Whether UndoBench reports anything equivalent to an argument-drift rate.
- The last ~7% of the first paper's text and one truncated results table.
- Whether `resume-contract`'s 18 open pull requests are outside contributions or automated dependency updates.
- What pydantic-ai's durable integrations guarantee for tool side effects — the main open question for the backend axis, and the first thing task 2 should read.
